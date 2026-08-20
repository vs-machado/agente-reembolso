from __future__ import annotations

import base64
import unittest
from datetime import date
from pathlib import Path

from app.agents.documento import (
    AnaliseConteudoDocumentalModel,
    ClassificacaoDocumentoModel,
    DadosDocumentoModel,
    EvidenciaRelatorioClinicoModel,
    FatosDocumentaisModel,
    ItemDocumentalModel,
    analisar_conteudo_documental,
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


def extrator_unico(texto: str, classificacao: ClassificacaoDocumentoModel) -> list[ItemDocumentalModel]:
    return [
        ItemDocumentalModel(
            categoria=classificacao.categoria,
            valor_solicitado_brl="240.00" if "dermatologia" in texto else None,
            data_atendimento=date(2026, 4, 30) if "dermatologia" in texto else None,
            codigo_tuss="10101012" if "dermatologia" in texto else None,
        )
    ]


def dados_documento_falsos(categoria: Categoria) -> DadosDocumentoModel:
    if categoria == Categoria.RELATORIO_CLINICO:
        return DadosDocumentoModel(
            nome_beneficiario="Beneficiario de Teste",
            registro_conselho="CRP 00/00000",
        )
    if categoria == Categoria.INVALIDO:
        return DadosDocumentoModel()
    return DadosDocumentoModel(
        nome_beneficiario="Beneficiario de Teste",
        cpf_beneficiario_presente=True,
        nome_prestador="Profissional de Teste",
        cpf_cnpj_prestador_presente=True,
        registro_conselho="CRM 00/00000",
        data_atendimento=date(2026, 4, 30),
        descricao_procedimento="Procedimento documentado",
        valor_total_original="240.00",
        codigo_moeda_iso="BRL",
        assinatura_ou_carimbo_presente=True,
        numero_sessao_ano=None if categoria == Categoria.SESSAO_TERAPIA else 1,
    )


def analisador_falso(texto: str) -> AnaliseConteudoDocumentalModel:
    classificacao = classificador_falso(texto)
    itens = []
    evidencia_relatorio = None
    if classificacao.categoria not in {Categoria.INVALIDO, Categoria.RELATORIO_CLINICO}:
        itens = extrator_unico(texto, classificacao)
    elif classificacao.categoria == Categoria.RELATORIO_CLINICO:
        evidencia_relatorio = EvidenciaRelatorioClinicoModel(
            identificacao_beneficiario="Beneficiario de Teste",
            identificacao_profissional="Profissional de Teste",
            registro_conselho="CRP 00/00000",
            data_emissao=date(2026, 5, 10),
            periodo_acompanhamento="2026",
            numero_sessoes_ano=12,
            manutencao_tratamento=True,
            assinatura_presente=True,
        )
    else:
        # Simula uma saida contraditoria para testar o bloqueio local de INVALIDO.
        itens = [
            ItemDocumentalModel(
                categoria=Categoria.INVALIDO,
                valor_solicitado_brl="999.00",
            )
        ]
        evidencia_relatorio = EvidenciaRelatorioClinicoModel(
            identificacao_beneficiario="Conteudo que deve ser descartado"
        )
    return AnaliseConteudoDocumentalModel(
        classificacao=classificacao,
        dados_documento=dados_documento_falsos(classificacao.categoria),
        itens=itens,
        evidencia_relatorio=evidencia_relatorio,
    )


class TesteDocumento(unittest.TestCase):
    def test_extrai_pdf_de_treino(self) -> None:
        texto = extrair_texto(criar_anexo("recibo_consulta_dermatologia.pdf"))

        self.assertIsNone(texto.erro)
        self.assertIn("Consulta m", texto.texto)

    def test_bloqueia_conta_de_energia(self) -> None:
        resultado = analisar_documento(
            criar_anexo("conta_energia.pdf"),
            analisador=analisador_falso,
        )

        self.assertEqual(resultado.categoria_dominante, Categoria.INVALIDO)
        self.assertFalse(resultado.aproveitavel)
        self.assertEqual(resultado.itens, [])
        self.assertIsNone(resultado.evidencia_relatorio)
        self.assertEqual(resultado.pendencias, [])

    def test_extrai_consulta_com_valor_e_data(self) -> None:
        resultado = analisar_documento(
            criar_anexo("recibo_consulta_dermatologia.pdf"),
            analisador=analisador_falso,
        )

        self.assertEqual(resultado.categoria_dominante, Categoria.CONSULTA_MEDICA)
        self.assertEqual(str(resultado.valor_solicitado_total_brl), "240.00")
        self.assertEqual(str(resultado.itens[0].data_atendimento), "2026-04-30")
        self.assertEqual(resultado.itens[0].codigo_tuss, "10101012")
        self.assertEqual(resultado.pendencias, [])

    def test_sessao_sem_numero_gera_pendencia_do_historico(self) -> None:
        resultado = analisar_documento(
            criar_anexo("recibo_psicoterapia.pdf"),
            analisador=analisador_falso,
        )

        self.assertEqual(resultado.categoria_dominante, Categoria.SESSAO_TERAPIA)
        self.assertIn("P19", resultado.pendencias)

    def test_pendencias_usam_fatos_estruturados_e_nao_rotulos_do_documento(self) -> None:
        def analisador(_texto: str) -> AnaliseConteudoDocumentalModel:
            return AnaliseConteudoDocumentalModel(
                classificacao=ClassificacaoDocumentoModel(
                    categoria=Categoria.CONSULTA_MEDICA,
                    natureza_medica=True,
                    justificativa="Documento fiscal medico.",
                ),
                dados_documento=DadosDocumentoModel(),
            )

        resultado = analisar_documento(
            criar_anexo("recibo_consulta_dermatologia.pdf"),
            analisador=analisador,
        )

        self.assertEqual(
            resultado.pendencias,
            ["P01", "P02", "P04", "P05", "P06", "P09", "P13", "P15", "P18"],
        )

    def test_relatorio_posterior_e_complementar(self) -> None:
        resultado = analisar_documento(
            criar_anexo("relatorio_clinico_psicoterapia.pdf"),
            ha_pedido_pendente=True,
            analisador=analisador_falso,
        )

        self.assertEqual(resultado.categoria_dominante, Categoria.RELATORIO_CLINICO)
        self.assertTrue(resultado.relatorio_complementar)
        self.assertTrue(resultado.aproveitavel)
        self.assertEqual(resultado.evidencia_relatorio.numero_sessoes_ano, 12)
        self.assertTrue(resultado.evidencia_relatorio.manutencao_tratamento)
        self.assertIsNone(resultado.valor_solicitado_total_brl)

    def test_preserva_categorias_dos_itens_e_calcula_dominante_por_soma(self) -> None:
        resultado = FatosDocumentaisModel(
            categoria=Categoria.EXAME_DIAGNOSTICO,
            natureza_medica=True,
            aproveitavel=True,
            justificativa="Itens discriminados.",
            itens=[
                ItemDocumentalModel(
                    categoria=Categoria.CONSULTA_MEDICA,
                    valor_solicitado_brl="100.00",
                ),
                ItemDocumentalModel(
                    categoria=Categoria.EXAME_DIAGNOSTICO,
                    valor_solicitado_brl="80.00",
                ),
                ItemDocumentalModel(
                    categoria=Categoria.EXAME_DIAGNOSTICO,
                    valor_solicitado_brl="80.00",
                ),
            ],
        )

        self.assertEqual(resultado.categoria_dominante, Categoria.EXAME_DIAGNOSTICO)
        self.assertEqual(str(resultado.valor_solicitado_total_brl), "260.00")
        self.assertEqual(
            [item.categoria for item in resultado.itens],
            [
                Categoria.CONSULTA_MEDICA,
                Categoria.EXAME_DIAGNOSTICO,
                Categoria.EXAME_DIAGNOSTICO,
            ],
        )

    def test_analise_preserva_itens_fornecidos_pelo_analisador(self) -> None:
        def analisador(_texto: str) -> AnaliseConteudoDocumentalModel:
            return AnaliseConteudoDocumentalModel(
                classificacao=classificador_falso(_texto),
                dados_documento=dados_documento_falsos(Categoria.CONSULTA_MEDICA),
                itens=[
                    ItemDocumentalModel(
                        categoria=Categoria.CONSULTA_MEDICA,
                        valor_solicitado_brl="100.00",
                    ),
                    ItemDocumentalModel(
                        categoria=Categoria.EXAME_DIAGNOSTICO,
                        valor_solicitado_brl="120.00",
                    ),
                ],
            )

        resultado = analisar_documento(
            criar_anexo("recibo_consulta_dermatologia.pdf"),
            analisador=analisador,
        )

        self.assertEqual(len(resultado.itens), 2)
        self.assertEqual(resultado.categoria_dominante, Categoria.EXAME_DIAGNOSTICO)

    def test_relatorio_nao_cria_item_de_despesa(self) -> None:
        def analisador(texto: str) -> AnaliseConteudoDocumentalModel:
            return AnaliseConteudoDocumentalModel(
                classificacao=classificador_falso(texto),
                dados_documento=dados_documento_falsos(Categoria.RELATORIO_CLINICO),
                itens=[
                    ItemDocumentalModel(
                        categoria=Categoria.RELATORIO_CLINICO,
                        valor_solicitado_brl="100.00",
                    )
                ],
                evidencia_relatorio=EvidenciaRelatorioClinicoModel(
                    identificacao_beneficiario="Beneficiario de Teste",
                    identificacao_profissional="Profissional de Teste",
                    registro_conselho="CRP 00/00000",
                    data_emissao=date(2026, 5, 10),
                    periodo_acompanhamento="2026",
                    numero_sessoes_ano=12,
                    manutencao_tratamento=True,
                    assinatura_presente=True,
                ),
            )

        resultado = analisar_documento(
            criar_anexo("relatorio_clinico_psicoterapia.pdf"),
            analisador=analisador,
        )

        self.assertEqual(resultado.itens, [])
        self.assertEqual(resultado.evidencia_relatorio.registro_conselho, "CRP 00/00000")
        self.assertTrue(resultado.evidencia_relatorio.assinatura_presente)
        self.assertEqual(resultado.categoria_dominante, Categoria.RELATORIO_CLINICO)
        self.assertIsNone(resultado.valor_solicitado_total_brl)

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

    def test_analise_estruturada_faz_uma_unica_invocacao(self) -> None:
        class LlmFalso:
            def __init__(self) -> None:
                self.invocacoes = 0

            def with_structured_output(self, schema):
                self.schema = schema
                return self

            def invoke(self, prompt: str) -> dict:
                self.invocacoes += 1
                self.prompt = prompt
                return {
                    "classificacao": {
                        "categoria": "CONSULTA_MEDICA",
                        "natureza_medica": True,
                        "justificativa": "Recibo de consulta.",
                    },
                    "dados_documento": {
                        "nome_beneficiario": "Beneficiario de Teste",
                        "cpf_beneficiario_presente": True,
                        "nome_prestador": "Profissional de Teste",
                        "cpf_cnpj_prestador_presente": True,
                        "registro_conselho": "CRM 00/00000",
                        "data_atendimento": "2026-04-30",
                        "descricao_procedimento": "Consulta medica",
                        "valor_total_original": "240.00",
                        "codigo_moeda_iso": "BRL",
                        "assinatura_ou_carimbo_presente": True,
                    },
                    "itens": [
                        {
                            "categoria": "CONSULTA_MEDICA",
                            "valor_solicitado_brl": "240.00",
                        }
                    ],
                }

        llm = LlmFalso()
        resultado = analisar_conteudo_documental("Recibo de consulta", llm)

        self.assertEqual(llm.invocacoes, 1)
        self.assertIs(llm.schema, AnaliseConteudoDocumentalModel)
        self.assertEqual(resultado.classificacao.categoria, Categoria.CONSULTA_MEDICA)
        self.assertEqual(str(resultado.itens[0].valor_solicitado_brl), "240.00")

    def test_analise_estruturada_preserva_moeda_estrangeira_sem_converter(self) -> None:
        class LlmFalso:
            def with_structured_output(self, schema):
                self.schema = schema
                return self

            def invoke(self, prompt: str) -> dict:
                self.prompt = prompt
                return {
                    "classificacao": {
                        "categoria": "EXAME_DIAGNOSTICO",
                        "natureza_medica": True,
                        "justificativa": "Documento de exame no exterior.",
                    },
                    "dados_documento": {
                        "nome_beneficiario": "Beneficiario de Teste",
                        "cpf_beneficiario_presente": True,
                        "nome_prestador": "Profissional de Teste",
                        "cpf_cnpj_prestador_presente": True,
                        "registro_conselho": "CRM 00/00000",
                        "data_atendimento": "2026-04-30",
                        "descricao_procedimento": "Exame de imagem",
                        "valor_total_original": "150.00",
                        "codigo_moeda_iso": "EUR",
                        "assinatura_ou_carimbo_presente": True,
                    },
                    "itens": [
                        {
                            "categoria": "EXAME_DIAGNOSTICO",
                            "valor_original": "150.00",
                            "codigo_moeda_iso": "EUR",
                            "valor_solicitado_brl": None,
                            "descricao_procedimento": "Exame de imagem",
                        }
                    ],
                }

        llm = LlmFalso()
        resultado = analisar_conteudo_documental("Exame no valor de EUR 150,00", llm)
        item = resultado.itens[0]

        self.assertIn("codigo_moeda_iso", llm.prompt)
        self.assertEqual(str(item.valor_original), "150.00")
        self.assertEqual(item.codigo_moeda_iso, "EUR")
        self.assertIsNone(item.valor_solicitado_brl)


if __name__ == "__main__":
    unittest.main()
