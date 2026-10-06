"""API do agente. As 3 rotas são obrigatórias; o miolo é seu.

    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.agents.supervisor import Supervisor
from app.llm import carregar_env
from app.schemas import ChatRequest, ChatResponse
from app.treino_interface import executar_treino

# Lê o `.env` no start. O que já vem do ambiente vence — é assim que a banca
# injeta as credenciais dela no `docker run`.
carregar_env()

app = FastAPI(title="Agente de Reembolso")
supervisor = Supervisor()
app.mount("/ui", StaticFiles(directory=Path(__file__).parent / "interface", html=True), name="interface")


@app.get("/", include_in_schema=False)
def inicio() -> RedirectResponse:
    return RedirectResponse(url="/ui/")


@app.get("/treino", include_in_schema=False)
def treino(caso: str = "todos") -> StreamingResponse:
    if os.getenv("INTERFACE_TREINO") != "1":
        raise HTTPException(status_code=404)
    if caso not in {"todos", "01", "02", "03"}:
        raise HTTPException(status_code=400, detail="Caso inválido")
    return StreamingResponse(executar_treino(caso), media_type="application/x-ndjson")


@app.get("/health")
def health() -> dict:
    """Precisa responder 200 em até 60 segundos do start do container.

    Não construa índice aqui: o `storage/` já vem pronto na imagem.
    """
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    """Um turno de conversa.

    O avaliador NÃO reenvia o histórico: recupere o estado da conversa pelo
    `session_id`, com o checkpointer do seu grafo.
    """
    return supervisor.responder(req)


@app.post("/reset")
def reset() -> dict:
    """Limpa estado e sessões. Chamado entre conversas."""
    supervisor.limpar_sessoes()
    return {"status": "ok"}
