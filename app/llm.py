"""Onde a chave entra e como falar com o modelo Gemini.

As chamadas usam a API oficial do Gemini com uma chave pessoal:

    BOOTCAMP_API_KEY=...                     # chave do Google AI Studio

Este arquivo concentra a configuração dos clientes usados pelo agente.

    from app.llm import criar_llm, criar_embeddings

    llm = criar_llm()                        # LangChain / LangGraph
    emb = criar_embeddings()

    from app.llm import criar_llm_llamaindex, criar_embeddings_llamaindex

    Settings.llm = criar_llm_llamaindex()    # LlamaIndex
    Settings.embed_model = criar_embeddings_llamaindex()

"""

from __future__ import annotations

import os
from pathlib import Path

MODELO = "gemini-2.5-flash-lite"
MODELO_EMBEDDING = "gemini-embedding-2"
DIMENSOES = 1536
TIMEOUT_LLM_SEGUNDOS = 60.0
TENTATIVAS_LLM = 3

RAIZ = Path(__file__).resolve().parents[1]


class FaltaConfiguracao(RuntimeError):
    pass


def carregar_env() -> None:
    """Lê o `.env` da raiz do repositório, sem depender de biblioteca.

    O que já está no ambiente vence o arquivo — é assim que a banca injeta as
    credenciais dela no `docker run`, sem que o seu `.env` atrapalhe.
    """
    env = RAIZ / ".env"
    if not env.exists():
        return
    for linha in env.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


def _chave_api() -> str:
    carregar_env()
    chave = os.getenv("BOOTCAMP_API_KEY", "").strip()
    if not chave:
        raise FaltaConfiguracao(
            "preencha BOOTCAMP_API_KEY no .env (copie de .env.example)")
    return chave


# ------------------------------------------------------- LangChain / LangGraph
def criar_llm(**extra):
    """O modelo de chat, pronto para `bind_tools` e `create_react_agent`."""
    from langchain_google_genai import ChatGoogleGenerativeAI

    chave = _chave_api()
    extra.setdefault("request_timeout", TIMEOUT_LLM_SEGUNDOS)
    extra.setdefault("retries", TENTATIVAS_LLM)
    return ChatGoogleGenerativeAI(
        model=MODELO, google_api_key=chave,
        temperature=extra.pop("temperature", 0), **extra)


def criar_embeddings(**extra):
    """Embeddings do Gemini, 1536 dimensões."""
    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    chave = _chave_api()
    extra.setdefault("output_dimensionality", DIMENSOES)
    return GoogleGenerativeAIEmbeddings(
        model=f"models/{MODELO_EMBEDDING}", google_api_key=chave, **extra)


# ------------------------------------------------------------------ LlamaIndex
def criar_llm_llamaindex(**extra):
    from llama_index.llms.google_genai import GoogleGenAI

    chave = _chave_api()
    return GoogleGenAI(model=MODELO, api_key=chave, **extra)


def criar_embeddings_llamaindex(**extra):
    from llama_index.embeddings.google_genai import GoogleGenAIEmbedding

    chave = _chave_api()
    extra.setdefault("embedding_config", {"output_dimensionality": DIMENSOES})
    return GoogleGenAIEmbedding(
        model_name=MODELO_EMBEDDING, api_key=chave, **extra)


if __name__ == "__main__":
    # python -m app.llm  -> confere a configuração antes de você começar
    try:
        print("chat      :", criar_llm().invoke("Responda apenas: pronto.").content.strip())
        print("embeddings:", len(criar_embeddings().embed_query("teste")), "dimensões")
    except FaltaConfiguracao as erro:
        raise SystemExit(f"{erro}")
