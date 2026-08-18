from __future__ import annotations

import threading
import unittest

from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

from app.agents.supervisor.graph import Supervisor
from app.main import app
from app.schemas import ChatRequest


class TesteApi(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.client.post("/reset")

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
        self.assertIn("turno 2", response.json()["resposta"])

    def test_sessoes_nao_compartilham_historico(self) -> None:
        self.client.post("/chat", json={"session_id": "a", "mensagem": "Primeira"})
        response = self.client.post(
            "/chat", json={"session_id": "b", "mensagem": "Outra conversa"}
        )

        self.assertIn("turno 1", response.json()["resposta"])

    def test_reset_descarta_todas_as_sessoes(self) -> None:
        self.client.post("/chat", json={"session_id": "a", "mensagem": "Primeira"})
        reset = self.client.post("/reset")
        response = self.client.post(
            "/chat", json={"session_id": "a", "mensagem": "Nova conversa"}
        )

        self.assertEqual(reset.json(), {"status": "ok"})
        self.assertIn("turno 1", response.json()["resposta"])

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
        supervisor = Supervisor()
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


if __name__ == "__main__":
    unittest.main()
