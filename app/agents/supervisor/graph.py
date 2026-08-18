"""Grafo inicial do supervisor e ciclo de vida das sessoes.

Os especialistas passam a substituir o no ``registrar_turno`` sem mudar a camada HTTP.
"""

from __future__ import annotations

from threading import RLock

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph

from app.schemas import ChatRequest, ChatResponse


def registrar_turno(estado: MessagesState) -> dict:
    """Nó temporario que confirma o turno ate os especialistas serem ligados."""
    ultima_mensagem = estado["messages"][-1]
    turno = sum(isinstance(mensagem, HumanMessage) for mensagem in estado["messages"])
    anexo_nome = ultima_mensagem.additional_kwargs.get("anexo_nome")
    if anexo_nome:
        resposta = (
            f"Recebi o anexo deste atendimento no turno {turno}. "
            "Vou analisá-lo junto das demais informações."
        )
    else:
        resposta = (
            f"Registrei sua mensagem no turno {turno}. "
            "Vou continuar a análise do seu pedido de reembolso."
        )
    return {"messages": [AIMessage(content=resposta)]}


def _compilar(checkpointer: InMemorySaver):
    grafo = StateGraph(MessagesState)
    grafo.add_node("registrar_turno", registrar_turno)
    grafo.add_edge(START, "registrar_turno")
    grafo.add_edge("registrar_turno", END)
    return grafo.compile(checkpointer=checkpointer)


class Supervisor:
    """Entrada unica do grafo, com sessoes isoladas pelo ``session_id``."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._novo_grafo()

    def _novo_grafo(self) -> None:
        self._checkpointer = InMemorySaver()
        self._grafo = _compilar(self._checkpointer)

    def responder(self, req: ChatRequest) -> ChatResponse:
        metadados = {"anexo_nome": req.anexo.filename} if req.anexo else {}
        estado_inicial = {
            "messages": [HumanMessage(content=req.mensagem, additional_kwargs=metadados)]
        }
        config = {"configurable": {"thread_id": req.session_id}}
        with self._lock:
            estado = self._grafo.invoke(estado_inicial, config=config)
        return ChatResponse(resposta=estado["messages"][-1].content)

    def limpar_sessoes(self) -> None:
        """Descarta o checkpointer inteiro, como exige o contrato de /reset."""
        with self._lock:
            self._novo_grafo()
