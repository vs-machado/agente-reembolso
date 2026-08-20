from __future__ import annotations

import base64
import unittest
from pathlib import Path

from app.agents.documento import (
    ClassificacaoDocumentoModel,
    analisar_documento,
    classificar_documento,
    extrair_texto,
)
from app.schemas import Anexo, Categoria


RAIZ = Path(__file__).resolve().parents[1]


def criar_anexo(nome: str) -> Anexo:
    caminho = RAIZ / "anexos" / "treino" / nome
    return Anexo(
        filename=nome,
        mime_type="application/pdf",
        base64=base64.b64encode(caminho.read_bytes()).decode(),
    )


def classificador_falso(texto: str) -> ClassificacaoDocumentoModel:
    if "energia" in texto.casefold():
        return ClassificacaoDocumentoModel(
            categoria=Categoria.INVALIDO,
            natureza_medica=False,
            justificativa="Arquivo sem natureza assistencial.",
        )
    if "Prótese" in texto:
        categoria = Categoria.MATERIAL_OPME
    elif "RELATÓRIO CLÍNICO" in texto:
        categoria = Categoria.RELATORIO_CLINICO
    elif "PSICOTERAPIA" in texto:
        categoria = Categoria.SESSAO_TERAPIA
    else:
        categoria = Categoria.CONSULTA_MEDICA
    return ClassificacaoDocumentoModel(
        categoria=categoria,
        natureza_medica=True,
        justificativa="Classificacao simulada.",
    )


class TesteDocumento(unittest.TestCase):
    def test_extrai_pdf_de_treino(self) -> None:
        texto = extrair_texto(criar_anexo("recibo_consulta_dermatologia.pdf"))

        self.assertIsNone(texto.erro)
        self.assertIn("Consulta m", texto.texto)

    def test_bloqueia_conta_de_energia(self) -> None:
        resultado = analisar_documento(
            criar_anexo("conta_energia.pdf"), classificador=classificador_falso
        )

        self.assertEqual(resultado.categoria, Categoria.INVALIDO)
        self.assertFalse(resultado.aproveitavel)
        self.assertEqual(resultado.pendencias, [])

    def test_extrai_consulta_com_valor_e_data(self) -> None:
        resultado = analisar_documento(
            criar_anexo("recibo_consulta_dermatologia.pdf"), classificador=classificador_falso
        )

        self.assertEqual(resultado.categoria, Categoria.CONSULTA_MEDICA)
        self.assertEqual(str(resultado.valor_solicitado_brl), "240.00")
        self.assertEqual(str(resultado.data_atendimento), "2026-04-30")
        self.assertEqual(resultado.codigo_tuss, "10101012")
        self.assertEqual(resultado.pendencias, [])

    def test_sessao_sem_numero_gera_pendencia_do_historico(self) -> None:
        resultado = analisar_documento(
            criar_anexo("recibo_psicoterapia.pdf"), classificador=classificador_falso
        )

        self.assertEqual(resultado.categoria, Categoria.SESSAO_TERAPIA)
        self.assertIn("P19", resultado.pendencias)

    def test_relatorio_posterior_e_complementar(self) -> None:
        resultado = analisar_documento(
            criar_anexo("relatorio_clinico_psicoterapia.pdf"),
            ha_pedido_pendente=True,
            classificador=classificador_falso,
        )

        self.assertEqual(resultado.categoria, Categoria.RELATORIO_CLINICO)
        self.assertTrue(resultado.relatorio_complementar)
        self.assertTrue(resultado.aproveitavel)
        self.assertIsNone(resultado.valor_solicitado_brl)

    def test_base64_invalido_nao_interrompe_analise(self) -> None:
        resultado = extrair_texto(
            Anexo(filename="arquivo.pdf", mime_type="application/pdf", base64="invalido!")
        )

        self.assertEqual(resultado.erro, "arquivo em base64 invalido")

    def test_classificacao_valida_a_saida_estruturada_do_modelo(self) -> None:
        class LlmFalso:
            def with_structured_output(self, schema):
                self.schema = schema
                return self

            def invoke(self, prompt: str) -> dict:
                self.prompt = prompt
                return {
                    "categoria": "INVALIDO",
                    "natureza_medica": False,
                    "justificativa": "Nao e documento assistencial.",
                }

        resultado = classificar_documento("Qualquer documento", LlmFalso())

        self.assertEqual(resultado.categoria, Categoria.INVALIDO)


if __name__ == "__main__":
    unittest.main()
