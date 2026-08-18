"""Busca híbrida sobre os artefatos imutáveis gerados por ``ingest.build``."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

from llama_index.core import StorageContext, load_index_from_storage
from llama_index.retrievers.bm25 import BM25Retriever

from app.llm import criar_embeddings_llamaindex

LOG = logging.getLogger(__name__)
RAIZ = Path(__file__).resolve().parents[2]
DIR_STORAGE = RAIZ / "storage"
LIMITE_RETRIEVER = 30


@dataclass(frozen=True)
class FonteModel:
    texto: str
    citacao: str
    metadados: dict
    score: float
    origens: tuple[str, ...]


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
    if metadados.get("status") in {"apoio", "apoio_desatualizado"}:
        return False
    if referencia is None:
        return metadados.get("status") == "vigente"
    inicio = metadados.get("vigencia_inicio")
    fim = metadados.get("vigencia_fim")
    return (not inicio or date.fromisoformat(inicio) <= referencia) and (not fim or referencia <= date.fromisoformat(fim))


def _aplicar_precedencia(candidatos: list[tuple[object, float, tuple[str, ...]]]) -> list[tuple[object, float, tuple[str, ...]]]:
    """Remove a redação anterior somente nos dispositivos alcançados por circular."""
    alvos = {
        alvo
        for no, _, _ in candidatos
        if no.node.metadata.get("tipo") == "circular"
        for alvo in no.node.metadata.get("alvos_normativos", [])
    }
    return [
        candidato
        for candidato in candidatos
        if candidato[0].node.metadata.get("tipo") == "circular"
        or not (set(candidato[0].node.metadata.get("referencias_normativas", [])) & alvos)
    ]


class RerankerOnnx:
    """Cross-encoder local; os pesos devem existir em ``storage/reranker``."""

    def __init__(self, diretorio: Path) -> None:
        self._sessao = None
        self._tokenizer = None
        modelo = diretorio / "model.onnx"
        tokenizer = diretorio / "tokenizer.json"
        if modelo.exists() and tokenizer.exists():
            import onnxruntime
            from tokenizers import Tokenizer

            self._sessao = onnxruntime.InferenceSession(str(modelo), providers=["CPUExecutionProvider"])
            self._tokenizer = Tokenizer.from_file(str(tokenizer))

    @property
    def disponivel(self) -> bool:
        return self._sessao is not None

    def ordenar(self, consulta: str, candidatos: list[tuple[object, float, tuple[str, ...]]]) -> list[tuple[object, float, tuple[str, ...]]]:
        if not self._sessao or not self._tokenizer:
            return candidatos
        import numpy as np

        pontuados = []
        for no, score, origens in candidatos:
            codificado = self._tokenizer.encode(consulta, no.node.get_content())
            codificado.pad(256)
            entradas = {"input_ids": np.array([codificado.ids], dtype=np.int64), "attention_mask": np.array([codificado.attention_mask], dtype=np.int64)}
            if any(item.name == "token_type_ids" for item in self._sessao.get_inputs()):
                entradas["token_type_ids"] = np.array([codificado.type_ids], dtype=np.int64)
            saida = self._sessao.run(None, entradas)[0]
            pontuados.append((no, float(np.squeeze(saida)), origens))
        return sorted(pontuados, key=lambda item: item[1], reverse=True)


class RetrieverHibrido:
    def __init__(self, diretorio: Path = DIR_STORAGE) -> None:
        contexto = StorageContext.from_defaults(persist_dir=str(diretorio / "vetorial"))
        self._indice = load_index_from_storage(contexto, embed_model=criar_embeddings_llamaindex())
        self._bm25 = BM25Retriever.from_persist_dir(str(diretorio / "bm25"))
        self._bm25.similarity_top_k = LIMITE_RETRIEVER
        self._pais = {item["chunk_pai_id"]: item for item in json.loads((diretorio / "chunks_pai.json").read_text(encoding="utf-8"))}
        self._reranker = RerankerOnnx(diretorio / "reranker")

    def recuperar(self, consulta: str, data_atendimento: date | None = None, limite: int = 8) -> list[FonteModel]:
        """Retorna chunks-pai normativos, deduplicados e rastreáveis."""
        vetorial = self._indice.as_retriever(similarity_top_k=LIMITE_RETRIEVER).retrieve(consulta)
        lexical = self._bm25.retrieve(consulta)
        fundidos = fundir_rrf((("vetorial", vetorial), ("bm25", lexical)))
        aplicaveis = [(no, score, origens) for no, score, origens in fundidos if _vigente(no.node.metadata, data_atendimento)]
        aplicaveis = _aplicar_precedencia(aplicaveis)
        ordenados = self._reranker.ordenar(consulta, aplicaveis)
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
        LOG.info("rag consulta=%r candidatos=%d contexto=%d reranker=%s", consulta, len(fundidos), len(selecionados), self._reranker.disponivel)
        return selecionados
