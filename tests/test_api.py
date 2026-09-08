from __future__ import annotations

import threading
import unittest
import re
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

from app.agents.supervisor import Supervisor
from app.agents.triagem import ExtracaoTriagemModel
from app.guardrails import identificar_pedido_terceiro
from app.main import app
from app.schemas import ChatRequest, Decisao
from app.tools import ResultadoMcp


class ClienteMcpFalso:
    def __init__(self) -> None:
        self.consultas: list[str] = []
        self.protocolos: list[tuple[str, dict]] = []

    def consultar_beneficiario(self, carteirinha: str) -> ResultadoMcp:
        self.consultas.append(carteirinha)
        return ResultadoMcp(True, dados={"carteirinha": carteirinha, "plano": "Essencial"})

    def abrir_protocolo(self, carteirinha: str, payload: dict) -> ResultadoMcp:
        self.protocolos.append((carteirinha, payload))
        return ResultadoMcp(True, dados={"protocolo": "20260000001"})


def extrator_falso(mensagem: str) -> ExtracaoTriagemModel:
    ocorrencia = re.search(r"carteirinha\s+(\d+)", mensagem, flags=re.IGNORECASE)
    return ExtracaoTriagemModel(
        intencao="SOLICITAR_REEMBOLSO",
        confianca_intencao=1,
        carteirinha=ocorrencia.group(1) if ocorrencia else None,
    )


def gerador_resposta_falso(contexto: dict[str, object]) -> str:
    return f"Resposta gerada para: {', '.join(contexto.get('pendencias', [])) or 'triagem'}"


def validar_pedido_terceiro_falso(mensagem: str, candidata: str | None, sessao: str | None) -> bool:
    return identificar_pedido_terceiro(mensagem, candidata, sessao)


def revisar_resposta_falsa(resposta: str) -> str:
    return resposta


class TesteApi(unittest.TestCase):
    def setUp(self) -> None:
        self.patcher = patch(
            "app.main.supervisor",
            Supervisor(
                cliente_mcp=ClienteMcpFalso(),
                extrator_triagem=extrator_falso,
                gerador_resposta=gerador_resposta_falso,
                validador_pedido_terceiro=validar_pedido_terceiro_falso,
                revisor_resposta=revisar_resposta_falsa,
            ),
        )
        self.patcher.start()
        self.client = TestClient(app)
        self.client.post("/reset")

    def tearDown(self) -> None:
        self.patcher.stop()

    def test_saude(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_chat_retorna_formato_da_resposta(self) -> None:
        response = self.client.post(
            "/chat", json={"session_id": "sessao", "mensagem": "Olá"}
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            set(body),
            {
                "resposta",
                "categoria_documento",
                "decisao",
                "valor_solicitado_brl",
                "valor_reembolso_brl",
                "regras_aplicadas",
                "protocolo",
                "pendencias",
            },
        )
        self.assertIsInstance(body["resposta"], str)
        self.assertEqual(body["regras_aplicadas"], [])
        self.assertEqual(body["pendencias"], [])

    def test_mesma_sessao_acumula_mensagens(self) -> None:
        self.client.post("/chat", json={"session_id": "a", "mensagem": "Primeira"})
        response = self.client.post(
            "/chat", json={"session_id": "a", "mensagem": "Segunda"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Resposta gerada", response.json()["resposta"])

    def test_sessoes_nao_compartilham_historico(self) -> None:
        self.client.post("/chat", json={"session_id": "a", "mensagem": "Primeira"})
        response = self.client.post(
            "/chat", json={"session_id": "b", "mensagem": "Outra conversa"}
        )

        self.assertIn("Resposta gerada", response.json()["resposta"])

    def test_reset_descarta_todas_as_sessoes(self) -> None:
        self.client.post("/chat", json={"session_id": "a", "mensagem": "Primeira"})
        reset = self.client.post("/reset")
        response = self.client.post(
            "/chat", json={"session_id": "a", "mensagem": "Nova conversa"}
        )

        self.assertEqual(reset.json(), {"status": "ok"})
        self.assertIn("Resposta gerada", response.json()["resposta"])

    def test_anexo_e_guardado_na_mensagem_humana(self) -> None:
        self.client.post(
            "/chat",
            json={
                "session_id": "anexo",
                "mensagem": "Segue o recibo.",
                "anexo": {
                    "filename": "recibo.pdf",
                    "mime_type": "application/pdf",
                    "base64": "cGRm",
                },
            },
        )

        from app.main import supervisor

        state = supervisor._grafo.get_state(
            {"configurable": {"thread_id": "anexo"}}
        )
        message = state.values["messages"][0]
        self.assertIsInstance(message, HumanMessage)
        self.assertEqual(message.additional_kwargs["anexo_nome"], "recibo.pdf")


class TesteLockSupervisor(unittest.TestCase):
    def test_reset_aguarda_chat_em_execucao(self) -> None:
        supervisor = Supervisor(
            extrator_triagem=extrator_falso,
            gerador_resposta=gerador_resposta_falso,
            validador_pedido_terceiro=validar_pedido_terceiro_falso,
            revisor_resposta=revisar_resposta_falsa,
        )
        invoke = supervisor._grafo.invoke
        chat_started = threading.Event()
        allow_chat_to_finish = threading.Event()
        reset_finished = threading.Event()
        errors: list[BaseException] = []

        def slow_invoke(*args, **kwargs):
            chat_started.set()
            self.assertTrue(allow_chat_to_finish.wait(timeout=1))
            return invoke(*args, **kwargs)

        def run_chat() -> None:
            try:
                supervisor.responder(ChatRequest(session_id="a", mensagem="Olá"))
            except BaseException as error:  # pragma: no cover - assertion below reports it
                errors.append(error)

        def run_reset() -> None:
            try:
                supervisor.limpar_sessoes()
                reset_finished.set()
            except BaseException as error:  # pragma: no cover - assertion below reports it
                errors.append(error)

        supervisor._grafo.invoke = slow_invoke
        chat_thread = threading.Thread(target=run_chat)
        reset_thread = threading.Thread(target=run_reset)
        chat_thread.start()
        self.assertTrue(chat_started.wait(timeout=1))
        reset_thread.start()

        self.assertFalse(reset_finished.wait(timeout=0.1))
        allow_chat_to_finish.set()
        chat_thread.join(timeout=1)
        reset_thread.join(timeout=1)

        self.assertFalse(chat_thread.is_alive())
        self.assertFalse(reset_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(reset_finished.is_set())


class TesteTriagemSupervisor(unittest.TestCase):
    def test_usa_extracao_estruturada_em_mensagem_ambigua(self) -> None:
        chamadas: list[str] = []

        def extrator(mensagem: str) -> ExtracaoTriagemModel:
            chamadas.append(mensagem)
            return ExtracaoTriagemModel(
                intencao="SOLICITAR_REEMBOLSO",
                confianca_intencao=0.82,
                carteirinha="1234",
            )

        supervisor = Supervisor(
            cliente_mcp=ClienteMcpFalso(),
            extrator_triagem=extrator,
            gerador_resposta=gerador_resposta_falso,
            validador_pedido_terceiro=validar_pedido_terceiro_falso,
            revisor_resposta=revisar_resposta_falsa,
        )
        resposta = supervisor.responder(
            ChatRequest(
                session_id="llm",
                mensagem="Tenho uma despesa recente e gostaria de saber como proceder.",
            )
        )

        self.assertEqual(len(chamadas), 1)
        self.assertIn("comprovante do atendimento", resposta.resposta)

    def test_consulta_carteirinha_e_preserva_titular(self) -> None:
        cliente = ClienteMcpFalso()
        supervisor = Supervisor(
            cliente_mcp=cliente,
            extrator_triagem=extrator_falso,
            gerador_resposta=gerador_resposta_falso,
            validador_pedido_terceiro=validar_pedido_terceiro_falso,
            revisor_resposta=revisar_resposta_falsa,
        )

        supervisor.responder(
            ChatRequest(session_id="triagem", mensagem="Quero reembolso, carteirinha 1234")
        )
        resposta = supervisor.responder(
            ChatRequest(session_id="triagem", mensagem="Carteirinha 9999")
        )

        self.assertEqual(cliente.consultas, ["1234"])
        self.assertIn("Resposta gerada", resposta.resposta)
        estado = supervisor._grafo.get_state({"configurable": {"thread_id": "triagem"}})
        self.assertEqual(estado.values["triagem"]["carteirinha_titular"], "1234")
        self.assertTrue(estado.values["triagem"]["tentativa_terceiro"])

    def test_abre_protocolo_uma_unica_vez_para_alcada_humana(self) -> None:
        cliente = ClienteMcpFalso()
        supervisor = Supervisor(
            cliente_mcp=cliente,
            extrator_triagem=extrator_falso,
            gerador_resposta=gerador_resposta_falso,
            validador_pedido_terceiro=validar_pedido_terceiro_falso,
            revisor_resposta=revisar_resposta_falsa,
        )
        supervisor.responder(
            ChatRequest(session_id="protocolo", mensagem="Carteirinha 1234")
        )

        primeira = supervisor.aplicar_decisao(
            "protocolo",
            Decisao.ESCALADO_ANALISTA,
            {"categoria": "consulta", "cpf": "nao deve sair"},
        )
        segunda = supervisor.aplicar_decisao(
            "protocolo",
            Decisao.ESCALADO_ANALISTA,
            {"categoria": "consulta"},
        )

        self.assertEqual(len(cliente.protocolos), 1)
        self.assertEqual(cliente.protocolos[0][0], "1234")
        self.assertNotIn("cpf", cliente.protocolos[0][1])
        self.assertEqual(primeira.protocolo, "20260000001")
        self.assertEqual(segunda.protocolo, "20260000001")


if __name__ == "__main__":
    unittest.main()
