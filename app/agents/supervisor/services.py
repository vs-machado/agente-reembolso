"""Entrada publica do supervisor e gerenciamento das sessoes."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
import json
import logging
import os
from threading import RLock
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.documento import ItemDocumentalModel, analisar_documento
from app.agents.normas import (
    AvaliacaoNormativaModel,
    avaliar_normas_itens,
    calcular_reembolsos_normativos,
)
from app.agents.supervisor.graph import _compilar
from app.agents.supervisor.models import (
    AnalisadorDocumento,
    AvaliadorNormas,
    CalculadorNormas,
    ConsultorCotacao,
    ExtratorTriagem,
    GeradorResposta,
    RevisorResposta,
    RoteadorSupervisor,
    ValidadorPedidoTerceiro,
)
from app.agents.supervisor.roteamento import escolher_proxima_acao
from app.agents.triagem import extrair_triagem_estruturada, gerar_resposta_triagem
from app.guardrails import revisar_resposta_beneficiario, validar_pedido_terceiro
from app.rag import RetrieverHibrido
from app.schemas import ChatRequest, ChatResponse, Decisao
from app.tools import ClienteMcp, consultar_cotacao_ptax

LOG = logging.getLogger("uvicorn.error")


class Supervisor:
    """Entrada unica do coordenador, com checkpoint e exclusao mutua por sessao."""

    def __init__(
        self,
        cliente_mcp: ClienteMcp | None = None,
        extrator_triagem: ExtratorTriagem = extrair_triagem_estruturada,
        gerador_resposta: GeradorResposta = gerar_resposta_triagem,
        validador_pedido_terceiro: ValidadorPedidoTerceiro = validar_pedido_terceiro,
        revisor_resposta: RevisorResposta = revisar_resposta_beneficiario,
        analisador_documento: AnalisadorDocumento = analisar_documento,
        avaliador_normas: AvaliadorNormas | None = None,
        calculador_normas: CalculadorNormas = calcular_reembolsos_normativos,
        consultor_cotacao: ConsultorCotacao = consultar_cotacao_ptax,
        roteador: RoteadorSupervisor = escolher_proxima_acao,
        recuperador_factory: Callable[[], RetrieverHibrido] = RetrieverHibrido,
    ) -> None:
        self._lock_catalogo = RLock()
        self._locks_sessoes: dict[str, RLock] = {}
        self._cliente_mcp = cliente_mcp or ClienteMcp()
        self._extrator_triagem = extrator_triagem
        self._gerador_resposta = gerador_resposta
        self._validador_pedido_terceiro = validador_pedido_terceiro
        self._revisor_resposta = revisor_resposta
        self._analisador_documento = analisador_documento
        self._calculador_normas = calculador_normas
        self._consultor_cotacao = consultor_cotacao
        self._roteador = roteador
        self._recuperador_factory = recuperador_factory
        self._recuperador: RetrieverHibrido | None = None

        def avaliar(itens: list[ItemDocumentalModel], pergunta: str) -> list[AvaliacaoNormativaModel]:
            if self._recuperador is None:
                self._recuperador = self._recuperador_factory()
            return avaliar_normas_itens(itens, pergunta, recuperador=self._recuperador)

        self._avaliador_normas = avaliador_normas or avaliar
        self._novo_grafo()

    def _novo_grafo(self) -> None:
        self._checkpointer = InMemorySaver()
        self._grafo = _compilar(
            self._checkpointer,
            self._cliente_mcp,
            self._extrator_triagem,
            self._gerador_resposta,
            self._validador_pedido_terceiro,
            self._revisor_resposta,
            self._analisador_documento,
            self._avaliador_normas,
            self._calculador_normas,
            self._consultor_cotacao,
            self._roteador,
        )

    @contextmanager
    def _sessao_bloqueada(self, session_id: str):
        """Adquire o lock ainda sob o catalogo, fechando a corrida com o reset."""
        with self._lock_catalogo:
            lock = self._locks_sessoes.setdefault(session_id, RLock())
            lock.acquire()
        try:
            yield
        finally:
            lock.release()

    def _responder_indisponibilidade(
        self,
        config: dict[str, Any],
        req: ChatRequest,
    ) -> ChatResponse:
        """Mantem o atendimento utilizavel quando um no falha antes da resposta."""
        opcoes = (
            "Estou com uma indisponibilidade temporaria para concluir esta etapa. "
            "Seu pedido permanece em acompanhamento; tente novamente em instantes.",
            "Nao consegui concluir a analise agora por uma instabilidade temporaria. "
            "O pedido continua em acompanhamento e voce pode retomar em instantes.",
        )
        try:
            estado = self._grafo.get_state(config)
            anterior = next(
                (
                    str(item.content)
                    for item in reversed(estado.values.get("messages", []))
                    if isinstance(item, AIMessage)
                ),
                "",
            )
            resposta = opcoes[1] if anterior == opcoes[0] else opcoes[0]
            self._grafo.update_state(config, {"messages": [AIMessage(content=resposta)]})
        except Exception:
            resposta = opcoes[0]
        return ChatResponse(
            resposta=resposta,
            pendencias=["indisponibilidade temporaria na analise"],
        )

    def responder(self, req: ChatRequest) -> ChatResponse:
        turno_id = uuid4().hex
        metadados = {"anexo_nome": req.anexo.filename} if req.anexo else {}
        estado_inicial: dict[str, Any] = {
            "messages": [HumanMessage(content=req.mensagem, additional_kwargs=metadados)],
            "turno_id": turno_id,
            "anexo_turno_id": turno_id if req.anexo else None,
            "anexo_recebido": req.anexo.model_dump(mode="python") if req.anexo else None,
            "pedido_id_candidato": turno_id,
        }
        config = {"configurable": {"thread_id": req.session_id}}
        with self._sessao_bloqueada(req.session_id):
            try:
                estado = self._grafo.invoke(estado_inicial, config=config)
            except Exception:
                LOG.exception("atendimento_indisponivel session_id=%s", req.session_id)
                return self._responder_indisponibilidade(config, req)
        resposta = ChatResponse.model_validate(estado["resposta_chat"])
        if os.getenv("AGENTE_LOG_CHAT_COMPLETO", "").lower() in {"1", "true", "sim"}:
            LOG.info(
                "chat_completo=%s",
                json.dumps(
                    {
                        "session_id": req.session_id,
                        "turno_id": turno_id,
                        "beneficiario": req.mensagem,
                        "anexo": req.anexo.filename if req.anexo else None,
                        "agente": resposta.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                ),
            )
        return resposta

    def aplicar_decisao(
        self,
        session_id: str,
        decisao: Decisao,
        payload: dict[str, Any] | None = None,
    ) -> ChatResponse:
        """Mantem a entrada explicita legada com efeito de protocolo idempotente."""
        decisao = Decisao(decisao)
        config = {"configurable": {"thread_id": session_id}}
        with self._sessao_bloqueada(session_id):
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
                    dados["chave_idempotencia"] = (
                        f"{estado.values.get('pedido_id', session_id)}:alcada"
                    )
                    resultado = self._cliente_mcp.abrir_protocolo(titular, dados)
                    if resultado.sucesso and resultado.dados:
                        protocolo = resultado.dados.get("protocolo")
            triagem["decisao"] = decisao.value
            if protocolo:
                triagem["protocolo"] = protocolo
            self._grafo.update_state(config, {"triagem": triagem})
            resposta = self._revisor_resposta(
                self._gerador_resposta(
                    {
                        "decisao": decisao.value,
                        "protocolo_aberto": bool(protocolo),
                        "pendencias": triagem.get("pendencias", []),
                    }
                )
            )
        return ChatResponse(
            resposta=resposta,
            decisao=decisao,
            protocolo=protocolo,
            pendencias=triagem.get("pendencias", []),
        )

    def limpar_sessoes(self) -> None:
        """Espera sessoes em curso e substitui integralmente o checkpoint em memoria."""
        with self._lock_catalogo:
            locks = list(self._locks_sessoes.values())
            for lock in locks:
                lock.acquire()
            try:
                self._novo_grafo()
                self._locks_sessoes.clear()
            finally:
                for lock in reversed(locks):
                    lock.release()
