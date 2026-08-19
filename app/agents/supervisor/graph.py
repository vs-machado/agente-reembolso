"""Supervisor LangGraph para triagem e ciclo de vida das sessoes."""

from __future__ import annotations

from threading import RLock
from typing import Any, Callable, Literal

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph

from app.agents.triagem import (
    ExtracaoTriagemModel,
    ResultadoTriagemModel,
    extrair_triagem_estruturada,
    gerar_resposta_triagem,
    normalizar_carteirinha,
)
from app.schemas import ChatRequest, ChatResponse, Decisao
from app.tools import ClienteMcp


class EstadoSupervisor(MessagesState, total=False):
    """Estado minimizado: mensagens e fatos de triagem do atendimento corrente."""

    triagem: dict[str, Any]


ExtratorTriagem = Callable[[str], ExtracaoTriagemModel]
GeradorResposta = Callable[[dict[str, object]], str]


def extrair_turno(
    estado: EstadoSupervisor,
    extrator_triagem: ExtratorTriagem,
) -> dict:
    """Extrai fatos conversacionais sem consultar fontes externas."""
    mensagem = estado["messages"][-1]
    anterior = dict(estado.get("triagem", {}))
    extracao = extrator_triagem(mensagem.content)
    carteirinha = normalizar_carteirinha(extracao.carteirinha or "") or None
    resultado = ResultadoTriagemModel(
        intencao=extracao.intencao,
        confianca_intencao=extracao.confianca_intencao,
        carteirinha_candidata=carteirinha,
        intencao_presente=bool(anterior.get("intencao_confirmada"))
        or extracao.intencao.value == "SOLICITAR_REEMBOLSO",
        carteirinha_presente=carteirinha is not None,
        anexo_presente=bool(anterior.get("anexo_presente"))
        or bool(mensagem.additional_kwargs.get("anexo_nome")),
        tentativa_terceiro=bool(anterior.get("tentativa_terceiro"))
        or extracao.tentativa_terceiro,
        pendencias=list(anterior.get("pendencias", [])),
    )
    anterior.update(resultado.model_dump(mode="json"))
    anterior["intencao_confirmada"] = resultado.intencao_presente
    return {"triagem": anterior}


def rotear_apos_extracao(
    estado: EstadoSupervisor,
) -> Literal["validar_cadastro", "registrar_terceiro", "gerar_resposta"]:
    triagem = estado["triagem"]
    candidata = triagem.get("carteirinha_candidata")
    titular = triagem.get("carteirinha_titular")
    if candidata and titular and candidata != titular:
        return "registrar_terceiro"
    if candidata and not titular:
        return "validar_cadastro"
    return "gerar_resposta"


def validar_cadastro(estado: EstadoSupervisor, cliente_mcp: ClienteMcp) -> dict:
    """Valida a carteirinha no MCP, unica fonte de verdade cadastral."""
    triagem = dict(estado["triagem"])
    candidata = triagem["carteirinha_candidata"]
    consulta = cliente_mcp.consultar_beneficiario(candidata)
    pendencias = list(triagem.get("pendencias", []))
    if consulta.sucesso and consulta.dados:
        triagem["carteirinha_titular"] = normalizar_carteirinha(
            str(consulta.dados.get("carteirinha", candidata))
        )
        triagem["cadastro_validado"] = True
        triagem["dados_cadastrais"] = {
            chave: valor
            for chave, valor in consulta.dados.items()
            if chave not in {"cpf", "cpf_mascarado", "carteirinha", "nome"}
        }
        pendencias = [item for item in pendencias if item != "carteirinha nao localizada"]
    else:
        if "carteirinha nao localizada" not in pendencias:
            pendencias.append("carteirinha nao localizada")
    triagem["pendencias"] = pendencias
    return {"triagem": triagem}


def registrar_terceiro(estado: EstadoSupervisor) -> dict:
    """Marca o guardrail sem substituir a carteirinha previamente validada."""
    triagem = dict(estado["triagem"])
    triagem["tentativa_terceiro"] = True
    return {"triagem": triagem}


def _contexto_resposta(triagem: dict[str, Any]) -> dict[str, object]:
    pendencias = list(triagem.get("pendencias", []))
    if not triagem.get("intencao_confirmada"):
        pendencias.append("confirmacao da solicitacao de reembolso")
    if not triagem.get("carteirinha_titular"):
        pendencias.append("carteirinha valida do titular")
    if not triagem.get("anexo_presente"):
        pendencias.append("comprovante do atendimento")
    return {
        "pendencias": list(dict.fromkeys(pendencias)),
        "tentativa_terceiro": bool(triagem.get("tentativa_terceiro")),
        "cadastro_validado": bool(triagem.get("cadastro_validado")),
        "anexo_presente": bool(triagem.get("anexo_presente")),
        "intencao_confirmada": bool(triagem.get("intencao_confirmada")),
    }


def gerar_resposta(
    estado: EstadoSupervisor,
    gerador_resposta: GeradorResposta,
) -> dict:
    """Converte estado minimizado em resposta natural, sem texto fixo no agente."""
    contexto = _contexto_resposta(estado["triagem"])
    resposta = gerador_resposta(contexto)
    return {"messages": [AIMessage(content=resposta)]}


def _compilar(
    checkpointer: InMemorySaver,
    cliente_mcp: ClienteMcp,
    extrator_triagem: ExtratorTriagem,
    gerador_resposta: GeradorResposta,
):
    grafo = StateGraph(EstadoSupervisor)
    grafo.add_node("extrair_turno", lambda estado: extrair_turno(estado, extrator_triagem))
    grafo.add_node("validar_cadastro", lambda estado: validar_cadastro(estado, cliente_mcp))
    grafo.add_node("registrar_terceiro", registrar_terceiro)
    grafo.add_node("gerar_resposta", lambda estado: gerar_resposta(estado, gerador_resposta))
    grafo.add_edge(START, "extrair_turno")
    grafo.add_conditional_edges("extrair_turno", rotear_apos_extracao)
    grafo.add_edge("validar_cadastro", "gerar_resposta")
    grafo.add_edge("registrar_terceiro", "gerar_resposta")
    grafo.add_edge("gerar_resposta", END)
    return grafo.compile(checkpointer=checkpointer)


class Supervisor:
    """Entrada unica do grafo, com sessoes isoladas pelo ``session_id``."""

    def __init__(
        self,
        cliente_mcp: ClienteMcp | None = None,
        extrator_triagem: ExtratorTriagem = extrair_triagem_estruturada,
        gerador_resposta: GeradorResposta = gerar_resposta_triagem,
    ) -> None:
        self._lock = RLock()
        self._cliente_mcp = cliente_mcp or ClienteMcp()
        self._extrator_triagem = extrator_triagem
        self._gerador_resposta = gerador_resposta
        self._novo_grafo()

    def _novo_grafo(self) -> None:
        self._checkpointer = InMemorySaver()
        self._grafo = _compilar(
            self._checkpointer,
            self._cliente_mcp,
            self._extrator_triagem,
            self._gerador_resposta,
        )

    def responder(self, req: ChatRequest) -> ChatResponse:
        metadados = {"anexo_nome": req.anexo.filename} if req.anexo else {}
        estado_inicial = {
            "messages": [HumanMessage(content=req.mensagem, additional_kwargs=metadados)]
        }
        config = {"configurable": {"thread_id": req.session_id}}
        with self._lock:
            estado = self._grafo.invoke(estado_inicial, config=config)
        return ChatResponse(resposta=estado["messages"][-1].content)

    def aplicar_decisao(
        self,
        session_id: str,
        decisao: Decisao,
        payload: dict[str, Any] | None = None,
    ) -> ChatResponse:
        """Executa o efeito idempotente aprovado para a decisao de alçada."""
        decisao = Decisao(decisao)
        config = {"configurable": {"thread_id": session_id}}
        with self._lock:
            estado = self._grafo.get_state(config)
            triagem = dict(estado.values.get("triagem", {}))
            protocolo = triagem.get("protocolo")
            if decisao == Decisao.ESCALADO_ANALISTA and not protocolo:
                titular = triagem.get("carteirinha_titular")
                if titular:
                    dados = {
                        chave: valor
                        for chave, valor in (payload or {}).items()
                        if chave.lower() not in {"cpf", "cid"}
                    }
                    resultado = self._cliente_mcp.abrir_protocolo(titular, dados)
                    if resultado.sucesso and resultado.dados:
                        protocolo = resultado.dados.get("protocolo")
                    else:
                        triagem.setdefault("pendencias", []).append("abertura de protocolo")
            triagem["decisao"] = decisao.value
            if protocolo:
                triagem["protocolo"] = protocolo
            self._grafo.update_state(config, {"triagem": triagem})
            resposta = self._gerador_resposta(
                {
                    "decisao": decisao.value,
                    "protocolo_aberto": bool(protocolo),
                    "pendencias": triagem.get("pendencias", []),
                }
            )
        return ChatResponse(
            resposta=resposta,
            decisao=decisao,
            protocolo=protocolo,
            pendencias=triagem.get("pendencias", []),
        )

    def limpar_sessoes(self) -> None:
        """Descarta o checkpointer inteiro, como exige o contrato de /reset."""
        with self._lock:
            self._novo_grafo()
