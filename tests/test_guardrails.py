from __future__ import annotations

import unittest

from app.guardrails import (
    identificar_pedido_terceiro,
    revisar_resposta_beneficiario,
    validar_pedido_terceiro,
)


class TesteGuardrails(unittest.TestCase):
    def test_bloqueia_dependente_mesmo_quando_o_solicitante_e_titular(self) -> None:
        self.assertTrue(
            identificar_pedido_terceiro(
                "Sou o titular e quero pedir para meu dependente.", None, "1234"
            )
        )

    def test_bloqueia_carteirinha_diferente_da_sessao(self) -> None:
        self.assertTrue(
            identificar_pedido_terceiro("Carteirinha 9999", "9999", "1234")
        )

    def test_validador_semantico_recebe_indicio_local(self) -> None:
        class LlmFalso:
            def with_structured_output(self, modelo):
                return self

            def invoke(self, prompt):
                self.prompt = prompt
                return {"pedido_terceiro": False}

        llm = LlmFalso()
        self.assertTrue(validar_pedido_terceiro("Meu conjuge precisa de ajuda.", None, "1234", llm))
        self.assertIn("Indicio deterministico adicional: True", llm.prompt)

    def test_revisor_reformula_resposta_pelo_llm(self) -> None:
        class LlmFalso:
            def with_structured_output(self, modelo):
                return self

            def invoke(self, prompt):
                self.prompt = prompt
                return {"resposta": "Posso orientar sobre o andamento do pedido."}

        llm = LlmFalso()
        resposta = revisar_resposta_beneficiario("Seu CID e A00.", llm)

        self.assertEqual(resposta, "Posso orientar sobre o andamento do pedido.")
        self.assertIn("Indicio local de CPF ou CID: True", llm.prompt)


if __name__ == "__main__":
    unittest.main()
