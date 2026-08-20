from __future__ import annotations

import unittest

from app.agents.triagem import (
    DadosCadastraisModel,
    EstadoElegibilidadeEnum,
    EvidenciasNormativasModel,
    ExtracaoTriagemModel,
    FatosDocumentaisModel,
    avaliar_elegibilidade,
    extrair_triagem_estruturada,
    gerar_resposta_triagem,
    normalizar_carteirinha,
)


class LlmEstruturadoFalso:
    def __init__(self, resposta: dict) -> None:
        self.resposta = resposta
        self.schema = None
        self.prompt = ""

    def with_structured_output(self, schema):
        self.schema = schema
        return self

    def invoke(self, prompt: str) -> dict:
        self.prompt = prompt
        return self.resposta


class TesteTriagem(unittest.TestCase):
    def test_normaliza_carteirinha_sem_impor_tamanho(self) -> None:
        self.assertEqual(normalizar_carteirinha("70.42 8813-5561"), "704288135561")

    def test_extracao_estruturada_valida_resposta_do_llm(self) -> None:
        llm = LlmEstruturadoFalso(
            {
                "intencao": "SOLICITAR_REEMBOLSO",
                "confianca_intencao": 0.91,
                "carteirinha": "12-34",
            }
        )

        resultado = extrair_triagem_estruturada("Quero ajuda", llm)

        self.assertEqual(resultado.carteirinha, "12-34")
        self.assertIn("Quero ajuda", llm.prompt)

    def test_resposta_e_gerada_por_saida_estruturada(self) -> None:
        llm = LlmEstruturadoFalso({"resposta": "Informe o documento do atendimento."})

        resposta = gerar_resposta_triagem({"pendencias": ["comprovante"]}, llm)

        self.assertEqual(resposta, "Informe o documento do atendimento.")

    def test_prompt_orienta_recusa_de_dados_de_terceiro(self) -> None:
        llm = LlmEstruturadoFalso({"resposta": "Vou continuar com o titular."})

        gerar_resposta_triagem({"tentativa_terceiro": True}, llm)

        self.assertIn("dados e beneficios de terceiro", llm.prompt)
        self.assertIn("'tentativa_terceiro': True", llm.prompt)

    def test_prompt_orienta_resposta_para_conflito_normativo(self) -> None:
        class LlmFalso:
            def with_structured_output(self, schema):
                return self

            def invoke(self, prompt: str) -> dict:
                self.prompt = prompt
                return {"resposta": "Nao foi possivel estabelecer a elegibilidade."}

        llm = LlmFalso()
        gerar_resposta_triagem({"conflito_normativo": True}, llm)

        self.assertIn("nao foi possivel estabelecer a elegibilidade", llm.prompt)

    def test_mantem_elegibilidade_pendente_sem_fontes_completas(self) -> None:
        resultado = avaliar_elegibilidade(
            DadosCadastraisModel(validado=True),
            FatosDocumentaisModel(validado=True, data_fato="2026-01-10"),
            EvidenciasNormativasModel(),
        )

        self.assertEqual(resultado.estado, EstadoElegibilidadeEnum.PENDENTE)
        self.assertIn("fundamentacao normativa", resultado.pendencias)

    def test_define_elegibilidade_apenas_com_documento_e_norma_validados(self) -> None:
        resultado = avaliar_elegibilidade(
            DadosCadastraisModel(validado=True),
            FatosDocumentaisModel(validado=True, data_fato="2026-01-10"),
            EvidenciasNormativasModel(
                fonte_material=True,
                vigente=True,
                resultado_elegibilidade=True,
            ),
        )

        self.assertEqual(resultado.estado, EstadoElegibilidadeEnum.ELEGIVEL)


if __name__ == "__main__":
    unittest.main()
