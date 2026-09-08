"""Supervisor LangGraph que coordena agentes pela disponibilidade do estado."""

from __future__ import annotations

from collections.abc import Callable
import logging
from time import perf_counter
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.agents.supervisor.models import (
    AcaoSupervisorEnum,
    AnalisadorDocumento,
    AvaliadorNormas,
    CalculadorNormas,
    ConsultorCotacao,
    EstadoSupervisor,
    ExtratorTriagem,
    GeradorResposta,
    RevisorResposta,
    RoteadorSupervisor,
    ValidadorPedidoTerceiro,
)
from app.agents.supervisor.nodes import (
    executar_documento,
    executar_normas,
    executar_triagem,
    preparar_turno,
)
from app.agents.supervisor.resposta import responder_turno
from app.agents.supervisor.roteamento import coordenar
from app.tools import ClienteMcp

LOG = logging.getLogger("uvicorn.error")


def _executar_com_telemetria(
    nome: str,
    operacao: Callable[[EstadoSupervisor], dict[str, Any]],
    estado: EstadoSupervisor,
) -> dict[str, Any]:
    """Registra a duracao de cada no mesmo quando a execucao falha."""
    inicio = perf_counter()
    sucesso = False
    try:
        resultado = operacao(estado)
        sucesso = True
        return resultado
    finally:
        LOG.info(
            "agente_no nome=%s duracao_ms=%.1f sucesso=%s turno_id=%s",
            nome,
            (perf_counter() - inicio) * 1000,
            sucesso,
            estado.get("turno_id", ""),
        )


def _compilar(
    checkpointer: InMemorySaver,
    cliente_mcp: ClienteMcp,
    extrator: ExtratorTriagem,
    gerador: GeradorResposta,
    validador_pedido_terceiro: ValidadorPedidoTerceiro,
    revisor_resposta: RevisorResposta,
    analisador: AnalisadorDocumento,
    avaliador: AvaliadorNormas,
    calculador: CalculadorNormas,
    consultar_cotacao: ConsultorCotacao,
    roteador: RoteadorSupervisor,
):
    grafo = StateGraph(EstadoSupervisor)
    grafo.add_node(
        "preparar_turno",
        lambda estado: _executar_com_telemetria("preparar_turno", preparar_turno, estado),
    )
    grafo.add_node(
        "coordenar",
        lambda estado: _executar_com_telemetria(
            "coordenar", lambda atual: coordenar(atual, roteador), estado
        ),
    )
    grafo.add_node(
        "triagem",
        lambda estado: _executar_com_telemetria(
            "triagem",
            lambda atual: executar_triagem(
                atual, extrator, cliente_mcp, validador_pedido_terceiro
            ),
            estado,
        ),
    )
    grafo.add_node(
        "documento",
        lambda estado: _executar_com_telemetria(
            "documento", lambda atual: executar_documento(atual, analisador), estado
        ),
    )
    grafo.add_node(
        "normas",
        lambda estado: _executar_com_telemetria(
            "normas",
            lambda atual: executar_normas(
                atual, avaliador, calculador, cliente_mcp, consultar_cotacao
            ),
            estado,
        ),
    )
    grafo.add_node(
        "responder",
        lambda estado: _executar_com_telemetria(
            "responder",
            lambda atual: responder_turno(
                atual, gerador, revisor_resposta, cliente_mcp
            ),
            estado,
        ),
    )
    grafo.add_edge(START, "preparar_turno")
    grafo.add_edge("preparar_turno", "coordenar")
    grafo.add_conditional_edges(
        "coordenar",
        lambda estado: estado["proxima_acao"],
        {acao.value: acao.value for acao in AcaoSupervisorEnum},
    )
    grafo.add_edge("triagem", "coordenar")
    grafo.add_edge("documento", "coordenar")
    grafo.add_edge("normas", "coordenar")
    grafo.add_edge("responder", END)
    return grafo.compile(checkpointer=checkpointer)
