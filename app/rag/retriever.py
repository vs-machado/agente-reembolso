"""Busca híbrida sobre os artefatos imutáveis gerados por ``ingest.build``."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Iterable

from llama_index.core import StorageContext, load_index_from_storage
from llama_index.core.indices.vector_store.retrievers import VectorIndexRetriever
from llama_index.retrievers.bm25 import BM25Retriever
from pydantic import BaseModel, Field

from app.llm import criar_embeddings_llamaindex, criar_llm

LOG = logging.getLogger(__name__)
RAIZ = Path(__file__).resolve().parents[2]
DIR_STORAGE = RAIZ / "storage"
LIMITE_RETRIEVER = 15
LIMITE_TOKENS_RERANKER = 256


@dataclass(frozen=True)
class FonteModel:
    texto: str
    citacao: str
    metadados: dict
    score: float
    origens: tuple[str, ...]
    score_llm: int | None = None


class PontuacaoRelevanciaModel(BaseModel):
    indice_fonte: int = Field(ge=1)
    relevante: bool
    score: int = Field(ge=0, le=100)


class AvaliacaoRelevanciaModel(BaseModel):
    ha_fonte_suficiente: bool
    pontuacoes: list[PontuacaoRelevanciaModel]


def fundir_rrf(resultados: Iterable[tuple[str, list]], constante: int = 60) -> list[tuple[object, float, tuple[str, ...]]]:
    """Une rankings por Reciprocal Rank Fusion (RRF).

    Cada posição contribui com ``1 / (constante + posição)``. Isso evita
    comparar diretamente scores vetoriais e BM25, que usam escalas distintas,
    preserva candidatos relevantes de qualquer busca e favorece os encontrados
    por ambas.
    """
    acumulado: dict[str, tuple[object, float, set[str]]] = {}
    for origem, nos in resultados:
        for posicao, no in enumerate(nos, start=1):
            chave = no.node.node_id
            atual = acumulado.get(chave)
            valor = 1 / (constante + posicao)
            if atual:
                acumulado[chave] = (atual[0], atual[1] + valor, atual[2] | {origem})
            else:
                acumulado[chave] = (no, valor, {origem})
    return [(no, score, tuple(sorted(origens))) for no, score, origens in sorted(acumulado.values(), key=lambda item: item[1], reverse=True)]


def _vigente(metadados: dict, referencia: date | None) -> bool:
    if metadados.get("status") not in {"vigente", "revogado", "substituido"}:
        return False
    if referencia is None:
        return metadados.get("status") == "vigente"
    inicio = metadados.get("vigencia_inicio")
    fim = metadados.get("vigencia_fim")
    return (not inicio or date.fromisoformat(inicio) <= referencia) and (not fim or referencia <= date.fromisoformat(fim))


def _ids_aplicaveis(metadados_por_id: dict[str, dict], referencia: date | None) -> list[str]:
    """Seleciona subchunks antes da busca vetorial."""
    return [subchunk_id for subchunk_id, metadados in metadados_por_id.items() if _vigente(metadados, referencia)]


def _mascara_aplicaveis(corpus: list[dict], referencia: date | None) -> list[int]:
    """Impede que BM25 atribua score a fontes não aplicáveis."""
    return [int(_vigente(metadados, referencia)) for metadados in corpus]


class RerankerOnnx:
    """Cross-encoder local INT8; os artefatos residem em ``storage/reranker``."""

    def __init__(self, diretorio: Path) -> None:
        self._sessao = None
        self._tokenizer = None
        modelo = diretorio / "model.int8.onnx"
        tokenizer = diretorio / "tokenizer.json"
        if not modelo.exists() or not tokenizer.exists():
            # O RRF continua utilizável quando a imagem não contém o modelo.
            LOG.warning("reranker indisponivel: faltam artefatos em %s", diretorio)
            return

        import onnxruntime
        from tokenizers import Tokenizer

        self._sessao = onnxruntime.InferenceSession(str(modelo), providers=["CPUExecutionProvider"])
        self._tokenizer = Tokenizer.from_file(str(tokenizer))
        # O modelo foi quantizado para a janela fixa usada na inferência em lote.
        self._tokenizer.enable_truncation(max_length=LIMITE_TOKENS_RERANKER)
        self._tokenizer.enable_padding(length=LIMITE_TOKENS_RERANKER)

    @property
    def disponivel(self) -> bool:
        return self._sessao is not None

    def ordenar(self, consulta: str, candidatos: list[tuple[object, float, tuple[str, ...]]]) -> list[tuple[object, float, tuple[str, ...]]]:
        if not candidatos or not self._sessao or not self._tokenizer:
            return candidatos
        import numpy as np

        codificados = [self._tokenizer.encode(consulta, no.node.get_content()) for no, _, _ in candidatos]
        entradas = {
            "input_ids": np.array([item.ids for item in codificados], dtype=np.int64),
            "attention_mask": np.array([item.attention_mask for item in codificados], dtype=np.int64),
        }
        # XLM-R não usa segmentos, mas o reranker aceita modelos que os declarem.
        if any(item.name == "token_type_ids" for item in self._sessao.get_inputs()):
            entradas["token_type_ids"] = np.array([item.type_ids for item in codificados], dtype=np.int64)
        scores = self._sessao.run(None, entradas)[0].reshape(-1)
        # Após a fusão RRF, o cross-encoder decide a ordem sem alterar as origens.
        pontuados = [(no, float(score), origens) for (no, _, origens), score in zip(candidatos, scores, strict=True)]
        return sorted(pontuados, key=lambda item: item[1], reverse=True)


class RelevanciaReranker:
    """Valida a relevância dos chunks finais sem introduzir novas fontes."""

    def __init__(self, llm=None) -> None:
        self._llm = llm or criar_llm()

    def ordenar(self, consulta: str, fontes: list[FonteModel]) -> list[FonteModel]:
        if not fontes:
            return []
        candidatos = "\n\n".join(
            f"FONTE {indice}\nCitação: {fonte.citacao}\nTrecho: {fonte.texto}"
            for indice, fonte in enumerate(fontes, start=1)
        )
        instrucao = (
            "Avalie se cada fonte responde materialmente à consulta. Use apenas os "
            "trechos fornecidos: não crie regras, fatos ou citações. Considere uma "
            "fonte relevante somente se ela puder fundamentar ao menos parte da "
            "resposta. Marque ha_fonte_suficiente como falso somente se nenhuma "
            "fonte for materialmente relevante, mesmo que uma fonte relevante nao "
            "resolva todos os aspectos da consulta. "
            f"\n\nCONSULTA\n{consulta}\n\nCANDIDATOS\n{candidatos}"
        )
        try:
            avaliacao = self._llm.with_structured_output(AvaliacaoRelevanciaModel).invoke(instrucao)
        except Exception:
            LOG.exception("reranker LLM indisponível")
            return []
        if not avaliacao.ha_fonte_suficiente:
            return []
        pontuacoes = {
            item.indice_fonte: item
            for item in avaliacao.pontuacoes
            if item.relevante and item.indice_fonte <= len(fontes)
        }
        pontuadas = [
            replace(fonte, score_llm=pontuacoes[indice].score)
            for indice, fonte in enumerate(fontes, start=1)
            if indice in pontuacoes
        ]
        return sorted(pontuadas, key=lambda fonte: fonte.score_llm or 0, reverse=True)


class RetrieverHibrido:
    def __init__(self, diretorio: Path = DIR_STORAGE, reranker_relevancia: RelevanciaReranker | None = None) -> None:
        contexto = StorageContext.from_defaults(persist_dir=str(diretorio / "vetorial"))
        self._indice = load_index_from_storage(contexto, embed_model=criar_embeddings_llamaindex())
        self._bm25 = BM25Retriever.from_persist_dir(str(diretorio / "bm25"))
        self._bm25.similarity_top_k = LIMITE_RETRIEVER
        self._pais = {item["chunk_pai_id"]: item for item in json.loads((diretorio / "chunks_pai.json").read_text(encoding="utf-8"))}
        self._reranker = RerankerOnnx(diretorio / "reranker")
        self._reranker_llm = reranker_relevancia

    def _recuperar_bm25(self, consulta: str, mascara: list[int]) -> list:
        """Cria uma visão filtrada do índice BM25 sem reindexar o corpus."""
        retriever = BM25Retriever(
            existing_bm25=self._bm25.bm25,
            stemmer=self._bm25.stemmer,
            similarity_top_k=LIMITE_RETRIEVER,
            skip_stemming=self._bm25.skip_stemming,
            token_pattern=self._bm25.token_pattern,
            corpus_weight_mask=mascara,
        )
        return retriever.retrieve(consulta)

    def recuperar_chunks_circulares_vigentes(self, data_atendimento: date | None = None, limite: int = 3) -> list[FonteModel]:
        """Seleciona chunks de circulares vigentes por metadados, sem reconstruir documentos.

        Sujeita apenas a vigencia por periodo; a precedencia material entre normas e
        deixada para a leitura da LLM. Retorna o chunk inicial de cada circular, que
        contem a ementa e os artigos alterados.
        """
        vigentes = [
            pai
            for pai in self._pais.values()
            if pai["metadados"].get("tipo") == "circular"
            and _vigente(pai["metadados"], data_atendimento)
        ]
        por_documento: dict[str, dict] = {}
        for pai in vigentes:
            documento_id = pai["metadados"].get("documento_id")
            atual = por_documento.get(documento_id)
            pagina = int(pai["metadados"].get("pagina") or 0)
            if atual is None or pagina < int(atual["metadados"].get("pagina") or 0):
                por_documento[documento_id] = pai
        ordenadas = sorted(
            por_documento.values(),
            key=lambda pai: pai["metadados"].get("vigencia_inicio") or "0000-00-00",
            reverse=True,
        )
        selecionadas: list[FonteModel] = []
        for pai in ordenadas[:limite]:
            meta = {**pai["metadados"], "vigencia_indeterminada": not pai["metadados"].get("vigencia_inicio")}
            citacao = f"{meta['titulo']} | {meta['caminho_estrutural']} | p. {meta['pagina']}"
            selecionadas.append(FonteModel(pai["texto"], citacao, meta, 0.0, ("vigencia",), None))
        if selecionadas and data_atendimento is not None:
            LOG.info("rag chunks_circulares_vigentes=%s data=%s", [f.citacao for f in selecionadas], data_atendimento.isoformat())
        return selecionadas

    def recuperar(self, consulta: str, data_atendimento: date | None = None, limite: int = 8, validar_relevancia: bool = False) -> list[FonteModel]:
        """Retorna chunks-pai normativos, deduplicados e rastreaveis.

        Args:
            consulta: Pergunta normativa usada nas buscas vetorial e lexical.
            data_atendimento: Data-fato para filtrar fontes por vigencia.
            limite: Quantidade de chunks-pai retornados, entre 6 e 10.
            validar_relevancia: Quando verdadeiro, usa a LLM para remover fontes
                sem relacao material com a consulta apos o reranker ONNX. Use
                apenas ao preparar uma decisao normativa; o caminho padrao evita
                essa chamada adicional e retorna o ranking local.
        """
        metadados_vetoriais = self._indice.vector_store.data.metadata_dict
        ids_aplicaveis = _ids_aplicaveis(metadados_vetoriais, data_atendimento)
        if not ids_aplicaveis:
            LOG.info("rag consulta=%r candidatos=0 contexto=0: nenhuma fonte aplicavel", consulta)
            return []
        vetorial = VectorIndexRetriever(
            self._indice,
            similarity_top_k=LIMITE_RETRIEVER,
            node_ids=ids_aplicaveis,
        ).retrieve(consulta)
        lexical = self._recuperar_bm25(consulta, _mascara_aplicaveis(self._bm25.corpus, data_atendimento))
        fundidos = fundir_rrf((("vetorial", vetorial), ("bm25", lexical)))
        # Validação defensiva para artefatos gerados em momentos distintos.
        aplicaveis = [(no, score, origens) for no, score, origens in fundidos if _vigente(no.node.metadata, data_atendimento)]
        ordenados = self._reranker.ordenar(consulta, aplicaveis[:15])
        selecionados: list[FonteModel] = []
        pais_usados: set[str] = set()
        for no, score, origens in ordenados:
            pai_id = no.node.metadata["chunk_pai_id"]
            if pai_id in pais_usados:
                continue
            pai = self._pais[pai_id]
            meta = {**pai["metadados"], "vigencia_indeterminada": not pai["metadados"].get("vigencia_inicio")}
            citacao = f"{meta['titulo']} | {meta['caminho_estrutural']} | p. {meta['pagina']}"
            selecionados.append(FonteModel(pai["texto"], citacao, meta, score, origens))
            pais_usados.add(pai_id)
            if len(selecionados) == min(max(limite, 6), 10):
                break
        if validar_relevancia:
            self._reranker_llm = self._reranker_llm or RelevanciaReranker()
            selecionados = self._reranker_llm.ordenar(consulta, selecionados)
        LOG.info("rag consulta=%r candidatos=%d contexto=%d reranker=%s reranker_llm=%s", consulta, len(fundidos), len(selecionados), self._reranker.disponivel, validar_relevancia)
        return selecionados
