"""Constrói os artefatos imutáveis de recuperação a partir de ``kb/``.

    python -m ingest.build
"""

from __future__ import annotations

import json
import re
import shutil
import unicodedata
from hashlib import sha256
from pathlib import Path
from typing import Iterator

import fitz
from docx import Document as DocumentoDocx
from llama_index.core import StorageContext, VectorStoreIndex
from llama_index.core.schema import TextNode
from llama_index.core.vector_stores import SimpleVectorStore
from llama_index.retrievers.bm25 import BM25Retriever

from app.llm import criar_embeddings_llamaindex
from ingest.catalogo import DocumentoNormativoModel, extrair_catalogo

RAIZ = Path(__file__).resolve().parents[1]
DIR_KB = RAIZ / "kb"
DIR_STORAGE = RAIZ / "storage"



def _normalizar(texto: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", texto)).strip()


def _tokens(texto: str) -> list[str]:
    return re.findall(r"\S+", texto)


def _dividir(texto: str, limite: int, sobreposicao: int = 0) -> Iterator[str]:
    palavras = _tokens(texto)
    inicio = 0
    while inicio < len(palavras):
        fim = min(inicio + limite, len(palavras))
        yield " ".join(palavras[inicio:fim])
        if fim == len(palavras):
            return
        inicio = fim - sobreposicao


def _registro_tabela(cabecalhos: list[str], valores: list[str | None], vigencia: str) -> str:
    """Repete o contexto necessário para tornar uma linha de tabela indexável."""
    campos = [
        f"{_normalizar(cabecalho)}: {_normalizar(valor)}"
        for cabecalho, valor in zip(cabecalhos, valores)
        if cabecalho and valor
    ]
    return " | ".join([*campos, f"Vigência: {vigencia}"])


def _blocos_tabela(pagina: fitz.Page, numero: int, vigencia: str) -> Iterator[tuple[int, str, str]]:
    tabelas = pagina.find_tables().tables
    if tabelas:
        # Preserva a introdução da página sem duplicar o conteúdo da tabela.
        limite_tabela = min(tabela.bbox[1] for tabela in tabelas)
        introducao = _normalizar(pagina.get_text(clip=fitz.Rect(0, 0, pagina.rect.width, limite_tabela)))
        if introducao:
            yield numero, f"pagina {numero}, introdução", introducao
    for indice_tabela, tabela in enumerate(tabelas, start=1):
        dados = tabela.extract()
        if not dados:
            continue
        cabecalhos, *linhas = dados
        for indice_linha, linha in enumerate(linhas, start=1):
            registro = _registro_tabela(cabecalhos, linha, vigencia)
            if registro:
                yield numero, f"pagina {numero}, tabela {indice_tabela}, linha {indice_linha}", registro


def _blocos_pdf(caminho: Path, vigencia_tabela: str | None = None) -> Iterator[tuple[int, str, str]]:
    for numero, pagina in enumerate(fitz.open(caminho), start=1):
        if vigencia_tabela:
            yield from _blocos_tabela(pagina, numero, vigencia_tabela)
            continue
        texto = _normalizar(pagina.get_text())
        if not texto:
            continue
        partes = re.split(r"(?=(?:Art\.\s*\d+|§\s*\d+|(?<![Aa]rt\. )(?<!\d)\d+(?:\.\d+)*\.\s|T[IÍ]TULO|CAP[IÍ]TULO)\b)", texto, flags=re.IGNORECASE)
        for indice, parte in enumerate(partes):
            parte = parte.strip()
            if parte:
                titulo = parte.split(" ", 12)[:12]
                yield numero, f"pagina {numero}, bloco {indice + 1}: {' '.join(titulo)}", parte


def _blocos_docx(caminho: Path) -> Iterator[tuple[int, str, str]]:
    paragrafos = [_normalizar(p.text) for p in DocumentoDocx(caminho).paragraphs]
    paragrafos = [p for p in paragrafos if p]
    for indice in range(0, len(paragrafos), 2):
        bloco = " ".join(paragrafos[indice:indice + 2])
        yield 1, f"FAQ {indice // 2 + 1}", bloco


def _extrair(caminho: Path, documento: DocumentoNormativoModel) -> Iterator[tuple[int, str, str]]:
    if caminho.suffix.lower() == ".pdf":
        vigencia_tabela = documento.vigencia_inicio.isoformat() if documento.tipo == "tabela" and documento.vigencia_inicio else None
        yield from _blocos_pdf(caminho, vigencia_tabela)
    elif caminho.suffix.lower() == ".docx":
        yield from _blocos_docx(caminho)


def _metadados(documento: DocumentoNormativoModel, pagina: int, estrutural: str) -> dict:
    dados = documento.model_dump(mode="json")
    dados.update({"arquivo_origem": documento.arquivo, "pagina": pagina, "caminho_estrutural": estrutural})
    return dados


def _criar_nos() -> tuple[list[TextNode], list[dict], dict]:
    subchunks: list[TextNode] = []
    pais: list[dict] = []
    catalogo = {documento.arquivo: documento for documento in extrair_catalogo(DIR_KB)}
    inventario: dict = {"documentos": [], "pendentes_curadoria": [], "excluidos": []}
    for caminho in sorted(DIR_KB.iterdir()):
        if caminho.name not in catalogo:
            inventario["excluidos"].append({"arquivo": caminho.name, "motivo": "sem catalogo"})
            continue
        documento = catalogo[caminho.name]
        if documento.status == "pendente_curadoria":
            inventario["pendentes_curadoria"].append(documento.model_dump(mode="json"))
            continue
        quantidade_pais = quantidade_subchunks = 0
        blocos = list(_extrair(caminho, documento))
        grupos: list[tuple[int, str, str]] = []
        atual: list[str] = []
        pagina_atual = 0
        caminho_atual = ""
        for pagina, estrutural, bloco in blocos:
            # Itens curtos (sobretudo linhas de tabela) só têm sentido agrupados.
            if atual and len(_tokens(" ".join(atual))) + len(_tokens(bloco)) > 700:
                grupos.append((pagina_atual, caminho_atual, " ".join(atual)))
                atual = []
            if not atual:
                pagina_atual, caminho_atual = pagina, estrutural
            atual.append(bloco)
            if len(_tokens(" ".join(atual))) >= 450:
                grupos.append((pagina_atual, caminho_atual, " ".join(atual)))
                atual = []
        if atual:
            grupos.append((pagina_atual, caminho_atual, " ".join(atual)))
        for pagina, estrutural, bloco in grupos:
            for indice_pai, conteudo in enumerate(_dividir(bloco, 650, 80), start=1):
                meta = _metadados(documento, pagina, estrutural)
                pai_id = sha256(f"{caminho.name}|{pagina}|{estrutural}|{indice_pai}".encode()).hexdigest()[:20]
                meta["referencias_normativas"] = sorted(set(re.findall(r"(?:art\.\s*(\d+)|\b(TUSS-\d+)\b)", conteudo, flags=re.IGNORECASE)))
                meta["referencias_normativas"] = [f"ART-{artigo}" if artigo else codigo.upper() for artigo, codigo in meta["referencias_normativas"]]
                pai = {"chunk_pai_id": pai_id, "texto": conteudo, "metadados": meta}
                pais.append(pai)
                quantidade_pais += 1
                for indice_sub, trecho in enumerate(_dividir(conteudo, 220, 80), start=1):
                    sub_id = f"{pai_id}-{indice_sub}"
                    texto_indexado = f"{meta['titulo']} | {estrutural} | {trecho}"
                    subchunks.append(TextNode(id_=sub_id, text=texto_indexado, metadata={**meta, "chunk_pai_id": pai_id, "subchunk_id": sub_id}))
                    quantidade_subchunks += 1
        inventario["documentos"].append({**documento.model_dump(mode="json"), "chunks_pai": quantidade_pais, "subchunks": quantidade_subchunks})
    return subchunks, pais, inventario


def main() -> int:
    if not DIR_KB.exists():
        raise SystemExit(f"Base de conhecimento ausente: {DIR_KB}")
    
    temporario = DIR_STORAGE.with_name(f"{DIR_STORAGE.name}.tmp")
    shutil.rmtree(temporario, ignore_errors=True)
    temporario.mkdir(parents=True)
    reranker = DIR_STORAGE / "reranker"
    if reranker.exists():
        # Pesos ONNX são provisionados fora do Git e não devem ser recriados aqui.
        shutil.copytree(reranker, temporario / "reranker")

    subchunks, pais, inventario = _criar_nos()
    if not subchunks:
        raise SystemExit("Nenhum subchunk foi extraido da base de conhecimento.")
    
    contexto = StorageContext.from_defaults(vector_store=SimpleVectorStore())
    VectorStoreIndex(subchunks, storage_context=contexto, embed_model=criar_embeddings_llamaindex())
    contexto.persist(persist_dir=str(temporario / "vetorial"))

    BM25Retriever.from_defaults(nodes=subchunks, similarity_top_k=30, language="portuguese").persist(str(temporario / "bm25"))
    (temporario / "chunks_pai.json").write_text(json.dumps(pais, ensure_ascii=False, indent=2), encoding="utf-8")
    (temporario / "inventario.json").write_text(json.dumps(inventario, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.rmtree(DIR_STORAGE, ignore_errors=True)

    temporario.replace(DIR_STORAGE)
    print(json.dumps({"documentos": len(inventario["documentos"]), "pendentes_curadoria": len(inventario["pendentes_curadoria"]), "chunks_pai": len(pais), "subchunks": len(subchunks), "excluidos": len(inventario["excluidos"])}, ensure_ascii=False))
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
