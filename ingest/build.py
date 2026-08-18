"""Constrói os artefatos imutáveis de recuperação a partir de ``kb/``.

    python -m ingest.build
"""

from __future__ import annotations

import json
import re
import shutil
import unicodedata
from datetime import date
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

RAIZ = Path(__file__).resolve().parents[1]
DIR_KB = RAIZ / "kb"
DIR_STORAGE = RAIZ / "storage"

# Funciona para a base fixa do desafio, mas um catálogo hardcoded não é ideal se
# novos documentos precisarem entrar na pipeline.
CATALOGO = {
    "regulamento_geral.pdf": {"documento_id": "REG-2026", "tipo": "regulamento", "titulo": "Regulamento Geral de Reembolso", "data_publicacao": "2025-12-31", "vigencia_inicio": "2026-01-01", "status": "vigente", "autoridade": 3},
    "tabela_urs_2026.pdf": {"documento_id": "TURS-2026", "tipo": "tabela", "titulo": "Tabela URS 2026", "data_publicacao": "2026-01-01", "vigencia_inicio": "2026-01-01", "vigencia_fim": "2026-12-31", "status": "vigente", "autoridade": 4},
    "nota_tecnica_02_documentos.pdf": {"documento_id": "NT-02", "tipo": "nota_tecnica", "titulo": "Nota Tecnica 02", "data_publicacao": None, "vigencia_inicio": None, "status": "vigente", "autoridade": 4},
    "anexo_iv_exclusoes.pdf": {"documento_id": "ANEXO-IV", "tipo": "anexo", "titulo": "Anexo IV - Exclusoes de Cobertura", "data_publicacao": None, "vigencia_inicio": None, "status": "vigente", "autoridade": 4},
    "circular_04_2025.pdf": {"documento_id": "CIRC-04-2025", "tipo": "circular", "titulo": "Circular Normativa 04/2025", "data_publicacao": "2025-06-12", "vigencia_inicio": "2025-07-01", "vigencia_fim": "2025-10-31", "status": "revogado", "autoridade": 2, "invalida_documento_id": "CIRC-09-2025", "alvos_normativos": ["ART-12"]},
    "circular_09_2025.pdf": {"documento_id": "CIRC-09-2025", "tipo": "circular", "titulo": "Circular Normativa 09/2025", "data_publicacao": "2025-10-20", "vigencia_inicio": "2025-11-01", "status": "vigente", "autoridade": 2, "substitui_documento_id": "CIRC-04-2025", "alvos_normativos": ["ART-12"]},
    "circular_11_2026.pdf": {"documento_id": "CIRC-11-2026", "tipo": "circular", "titulo": "Circular Normativa 11/2026", "data_publicacao": "2026-01-15", "vigencia_inicio": "2026-02-01", "vigencia_fim": "2026-04-19", "status": "substituido", "autoridade": 2, "invalida_documento_id": "CIRC-02-2026", "alvos_normativos": ["ART-41", "ART-73", "TUSS-50000462"]},
    "circular_02_2026.pdf": {"documento_id": "CIRC-02-2026", "tipo": "circular", "titulo": "Circular Normativa 02/2026", "data_publicacao": "2026-04-20", "vigencia_inicio": "2026-04-20", "status": "vigente", "autoridade": 2, "substitui_documento_id": "CIRC-11-2026", "alvos_normativos": ["ART-41", "ART-44", "ART-73", "TUSS-50000462"]},
    "manual_rede_credenciada.pdf": {"documento_id": "MANUAL-REDE", "tipo": "manual", "titulo": "Manual da Rede Credenciada e do Reembolso", "data_publicacao": None, "vigencia_inicio": None, "status": "apoio", "autoridade": 6},
    "faq_interno.docx": {"documento_id": "FAQ-INTERNO", "tipo": "faq", "titulo": "FAQ Interno de Atendimento", "data_publicacao": "2025-08-01", "vigencia_inicio": None, "status": "apoio_desatualizado", "autoridade": 6},
}


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


def _blocos_pdf(caminho: Path) -> Iterator[tuple[int, str, str]]:
    for numero, pagina in enumerate(fitz.open(caminho), start=1):
        texto = _normalizar(pagina.get_text())
        if not texto:
            continue
        partes = re.split(r"(?=(?:Art\.\s*\d+|§\s*\d+|\d+(?:\.\d+)*\.\s|T[IÍ]TULO|CAP[IÍ]TULO)\b)", texto, flags=re.IGNORECASE)
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


def _extrair(caminho: Path) -> Iterator[tuple[int, str, str]]:
    if caminho.suffix.lower() == ".pdf":
        yield from _blocos_pdf(caminho)
    elif caminho.suffix.lower() == ".docx":
        yield from _blocos_docx(caminho)


def _metadados(caminho: Path, pagina: int, estrutural: str) -> dict:
    dados = dict(CATALOGO[caminho.name])
    for campo in ("data_publicacao", "vigencia_inicio", "vigencia_fim", "substitui_documento_id", "invalida_documento_id"):
        dados.setdefault(campo, None)
    dados.update({"arquivo_origem": caminho.name, "pagina": pagina, "caminho_estrutural": estrutural})
    return dados


def _criar_nos() -> tuple[list[TextNode], list[dict], dict]:
    subchunks: list[TextNode] = []
    pais: list[dict] = []
    inventario: dict = {"gerado_em": date.today().isoformat(), "documentos": [], "excluidos": []}
    for caminho in sorted(DIR_KB.iterdir()):
        if caminho.name not in CATALOGO:
            inventario["excluidos"].append({"arquivo": caminho.name, "motivo": "sem catalogo"})
            continue
        quantidade_pais = quantidade_subchunks = 0
        blocos = list(_extrair(caminho))
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
                meta = _metadados(caminho, pagina, estrutural)
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
        inventario["documentos"].append({**CATALOGO[caminho.name], "arquivo": caminho.name, "chunks_pai": quantidade_pais, "subchunks": quantidade_subchunks})
    return subchunks, pais, inventario


def main() -> int:
    if not DIR_KB.exists():
        raise SystemExit(f"Base de conhecimento ausente: {DIR_KB}")
    
    temporario = DIR_STORAGE.with_name(f"{DIR_STORAGE.name}.tmp")
    shutil.rmtree(temporario, ignore_errors=True)
    temporario.mkdir(parents=True)

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
    print(json.dumps({"documentos": len(inventario["documentos"]), "chunks_pai": len(pais), "subchunks": len(subchunks), "excluidos": len(inventario["excluidos"])}, ensure_ascii=False))
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
