from __future__ import annotations

from datetime import date
from decimal import Decimal
import logging
import unittest

from app.agents.documento.models import ItemDocumentalModel
from app.agents.normas import (
    AvaliacaoNormativaModel,
    FonteAfastadaModel,
    FonteNormativaModel,
    avaliar_normas_item,
    calcular_reembolsos_normativos,
    formular_consulta_normativa,
    formular_consultas_normativas,
)
from app.agents.normas.models import ParametrosCalculoNormativoModel
from app.rag import FonteModel
from app.schemas import Categoria
from app.agents.triagem import construir_evidencias_normativas


class TesteNormas(unittest.TestCase):
    def test_parametros_aceitam_decimal_no_formato_brasileiro(self) -> None:
        parametros = ParametrosCalculoNormativoModel(limite_anual_brl="1.141,20")

        self.assertEqual(parametros.limite_anual_brl, Decimal("1141.20"))

    def test_preserva_fonte_recuperada_na_avaliacao(self) -> None:
        fonte = FonteNormativaModel(
            texto="O teto da consulta e de 40 URS.",
            citacao="Regulamento | Art. 12 | p. 4",
            metadados={"status": "vigente", "vigencia_inicio": "2026-01-01"},
            score=0.91,
            origens=("bm25", "vetorial"),
            score_llm=97,
        )

        avaliacao = AvaliacaoNormativaModel(
            consulta="teto de consulta medica",
            data_fato=date(2026, 4, 30),
            fontes_recuperadas=[fonte],
            fontes_aplicaveis=[fonte],
            ha_fonte_suficiente=True,
            vigencia_confirmada=True,
            regras_aplicaveis=["Art. 12"],
            resultado_elegibilidade=True,
            justificativa="A fonte vigente contem o teto aplicavel.",
        )

        self.assertEqual(avaliacao.fontes_aplicaveis[0].citacao, fonte.citacao)
        self.assertEqual(avaliacao.fontes_aplicaveis[0].metadados["status"], "vigente")
        self.assertTrue(avaliacao.ha_fonte_suficiente)

    def test_registra_fonte_afastada_sem_criar_regra(self) -> None:
        fonte = FonteAfastadaModel(
            texto="Regra revogada.",
            citacao="Regulamento antigo | Art. 7 | p. 2",
            metadados={"status": "revogado", "vigencia_fim": "2025-12-31"},
            score=0.65,
            origens=("vetorial",),
            motivo_afastamento="Fora da vigencia da data-fato.",
        )

        avaliacao = AvaliacaoNormativaModel(
            consulta="teto de consulta medica",
            fontes_recuperadas=[fonte],
            fontes_afastadas=[fonte],
            pendencias=["data do atendimento"],
            justificativa="A data-fato e necessaria para confirmar a vigencia.",
        )

        self.assertFalse(avaliacao.ha_fonte_suficiente)
        self.assertFalse(avaliacao.vigencia_confirmada)
        self.assertIsNone(avaliacao.resultado_elegibilidade)
        self.assertEqual(avaliacao.regras_aplicaveis, [])
        self.assertEqual(avaliacao.fontes_afastadas[0].motivo_afastamento, "Fora da vigencia da data-fato.")

    def test_anexo_invalido_nao_formula_consulta(self) -> None:
        fatos = ItemDocumentalModel(
            categoria=Categoria.INVALIDO,
        )

        self.assertIsNone(formular_consulta_normativa(fatos, "Quero reembolso"))

    def test_consulta_inclui_todos_os_parametros_do_calculo(self) -> None:
        consulta = formular_consulta_normativa(
            ItemDocumentalModel(categoria=Categoria.CONSULTA_MEDICA),
            "plano Pleno e data de adesao 2025-09-01",
        )

        self.assertIsNotNone(consulta)
        self.assertIn("coparticipacao por plano e faixa de adesao", consulta)
        self.assertIn("considerar circulares", consulta)

    def test_consultas_focadas_separam_assuntos_normativos(self) -> None:
        consultas = formular_consultas_normativas(
            ItemDocumentalModel(categoria=Categoria.CONSULTA_MEDICA, codigo_tuss="10101012"),
            "plano Pleno e data de adesao 2025-09-01",
)

        self.assertIsNotNone(consultas)
        self.assertEqual(len(consultas), 4)
        self.assertIn("cobertura do procedimento", consultas[0])
        self.assertIn("circular vigente", consultas[1])
        self.assertIn("limite anual", consultas[2])
        self.assertIn("competencia da analise", consultas[3])

    def test_funde_fontes_das_consultas_e_le_uma_vez(self) -> None:
        class RecuperadorFalso:
            def __init__(self) -> None:
                self.consultas: list[str] = []

            def recuperar(self, consulta, **_kwargs):
                self.consultas.append(consulta)
                return [
                    FonteModel("Regra comum.", "Fonte comum", {}, 0.8, ("bm25",)),
                    FonteModel(f"Regra {len(self.consultas)}.", f"Fonte {len(self.consultas)}", {}, 0.7, ("vetorial",)),
                ]

            def recuperar_chunks_circulares_vigentes(self, _data_atendimento, **_kwargs):
                return [
                    FonteModel(
                        "Ementa da circular.",
                        "Circular 02/2026 | pagina 1 | p. 1",
                        {"tipo": "circular", "status": "vigente", "vigencia_inicio": "2026-04-20"},
                        0.0,
                        ("vigencia",),
                    )
                ]

        class LlmFalso:
            def __init__(self) -> None:
                self.chamadas = 0

            def with_structured_output(self, _schema):
                return self

            def invoke(self, _prompt):
                self.chamadas += 1
                return {"indices_aplicaveis": [1], "justificativa": "Leitura concluida."}

        recuperador = RecuperadorFalso()
        llm = LlmFalso()
        avaliacao = avaliar_normas_item(
            ItemDocumentalModel(categoria=Categoria.CONSULTA_MEDICA, data_atendimento=date(2026, 4, 30)),
            "Tenho direito?",
            recuperador=recuperador,
            llm=llm,
        )

        self.assertEqual(len(recuperador.consultas), 4)
        self.assertEqual(len(avaliacao.fontes_recuperadas), 6)
        self.assertIn("Circular 02/2026", "\n".join(f.citacao for f in avaliacao.fontes_recuperadas))
        self.assertEqual(llm.chamadas, 1)

    def test_fusao_limita_fontes_intercaladas(self) -> None:
        class RecuperadorFalso:
            def recuperar(self, consulta, **_kwargs):
                return [
                    FonteModel(f"{consulta} {indice}", f"{consulta} {indice}", {}, 0.9, ("bm25",))
                    for indice in range(10)
                ]

            def recuperar_chunks_circulares_vigentes(self, _data_atendimento, **_kwargs):
                return []

        class LlmFalso:
            def with_structured_output(self, _schema):
                return self

            def invoke(self, _prompt):
                return {"indices_aplicaveis": [1], "justificativa": "Leitura concluida."}

        avaliacao = avaliar_normas_item(
            ItemDocumentalModel(categoria=Categoria.CONSULTA_MEDICA, data_atendimento=date(2026, 4, 30)),
            "Tenho direito?",
            recuperador=RecuperadorFalso(),
            llm=LlmFalso(),
        )

        self.assertEqual(len(avaliacao.fontes_recuperadas), 10)

    def test_prompt_trata_tempo_de_adesao_como_fato_quando_informado(self) -> None:
        class LlmFalso:
            def with_structured_output(self, _schema):
                return self

            def invoke(self, prompt: str):
                self.prompt = prompt
                return {"indices_aplicaveis": [1], "justificativa": "Leitura concluida."}

        llm = LlmFalso()
        from app.agents.normas.services import _ler_fontes

        _ler_fontes(
            "tempo_adesao_meses_completos: 7",
            date(2026, 4, 30),
            [
                FonteNormativaModel(
                    texto="Faixas de coparticipacao.",
                    citacao="Fonte A",
                    metadados={},
                    score=0.9,
                    origens=("bm25",),
                )
            ],
            llm,
        )

        self.assertIn("como fato de entrada", llm.prompt)

    def test_prompt_exige_rastreabilidade_de_componentes_do_calculo(self) -> None:
        class LlmFalso:
            def with_structured_output(self, _schema):
                return self

            def invoke(self, prompt: str):
                self.prompt = prompt
                return {"indices_aplicaveis": [1], "justificativa": "Leitura concluida."}

        llm = LlmFalso()
        from app.agents.normas.services import _ler_fontes

        _ler_fontes(
            "consulta normativa",
            date(2026, 4, 30),
            [
                FonteNormativaModel(
                    texto="Formula de reembolso.",
                    citacao="Fonte A",
                    metadados={},
                    score=0.9,
                    origens=("bm25",),
                )
            ],
            llm,
        )

        self.assertIn("componentes usados da formula", llm.prompt)

    def test_conflito_preserva_fontes_e_registra_motivo(self) -> None:
        class RecuperadorFalso:
            def recuperar(self, *args, **kwargs):
                return [
                    FonteModel(
                        texto="A cobertura e prevista.",
                        citacao="Regulamento | Art. 10 | p. 2",
                        metadados={"status": "vigente"},
                        score=0.9,
                        origens=("vetorial",),
                    ),
                    FonteModel(
                        texto="A cobertura e vedada.",
                        citacao="Circular | Art. 3 | p. 1",
                        metadados={"status": "vigente"},
                        score=0.8,
                        origens=("bm25",),
                    ),
                ]

            def recuperar_chunks_circulares_vigentes(self, _data_atendimento, **_kwargs):
                return []

        class LlmFalso:
            def with_structured_output(self, schema):
                return self

            def invoke(self, prompt: str):
                return {
                    "indices_aplicaveis": [1, 2],
                    "ha_conflito_material": True,
                    "resultado_elegibilidade": None,
                    "justificativa": "As fontes vigentes chegam a conclusoes incompativeis.",
                }

        fatos = ItemDocumentalModel(
            categoria=Categoria.CONSULTA_MEDICA,
            valor_solicitado_brl=Decimal("240"),
            data_atendimento=date(2026, 4, 30),
            descricao_procedimento="Consulta medica",
        )

        with self.assertLogs("app.agents.normas.services", logging.WARNING) as logs:
            avaliacao = avaliar_normas_item(
                fatos,
                "Tenho direito ao reembolso?",
                recuperador=RecuperadorFalso(),
                llm=LlmFalso(),
            )

        self.assertTrue(avaliacao.ha_conflito_material)
        self.assertIsNone(avaliacao.resultado_elegibilidade)
        self.assertEqual(len(avaliacao.fontes_aplicaveis), 2)
        self.assertIn("conflito_normativo", logs.output[0])

    def test_conflito_nao_define_elegibilidade_nem_protocolo(self) -> None:
        avaliacao = AvaliacaoNormativaModel(
            consulta="consulta medica",
            ha_conflito_material=True,
            justificativa="Fontes materiais conflitantes.",
        )

        evidencias = construir_evidencias_normativas(avaliacao)

        self.assertTrue(evidencias.conflito)
        self.assertIsNone(evidencias.resultado_elegibilidade)
        self.assertIn("elegibilidade nao estabelecida por conflito normativo", evidencias.pendencias)

    def test_calcula_itens_respeitando_saldo_anual_comum(self) -> None:
        parametros = {
            "teto_urs": "20",
            "valor_urs_brl": "10",
            "coparticipacao_percentual": "0",
            "limite_anual_brl": "250",
            "exige_limite_anual": True,
        }
        fatos = [
            ItemDocumentalModel(
                categoria=Categoria.CONSULTA_MEDICA,
                valor_solicitado_brl=Decimal("200"), data_atendimento=date(2026, 4, 30),
            ),
            ItemDocumentalModel(
                categoria=Categoria.CONSULTA_MEDICA,
                valor_solicitado_brl=Decimal("200"), data_atendimento=date(2026, 5, 1),
            ),
        ]
        avaliacoes = [
            AvaliacaoNormativaModel(consulta="c", justificativa="ok", parametros_calculo=parametros),
            AvaliacaoNormativaModel(consulta="c", justificativa="ok", parametros_calculo=parametros),
        ]

        resultado = calcular_reembolsos_normativos(fatos, avaliacoes, totais_reembolsados_ano={2026: "100"})

        self.assertEqual(resultado.valores_itens_brl, [Decimal("150"), Decimal("0")])
        self.assertEqual(resultado.valor_reembolso_brl, Decimal("150.00"))

    def test_descarta_alcada_citada_por_fonte_nao_aplicavel(self) -> None:
        class RecuperadorFalso:
            def recuperar(self, *args, **kwargs):
                return [
                    FonteModel("Regra aplicavel.", "Fonte A", {}, 0.9, ("bm25",)),
                    FonteModel("Regra afastada.", "Fonte B", {}, 0.8, ("vetorial",)),
                ]

            def recuperar_chunks_circulares_vigentes(self, _data_atendimento, **_kwargs):
                return []

        class LlmFalso:
            def with_structured_output(self, schema):
                return self

            def invoke(self, prompt: str):
                return {
                    "indices_aplicaveis": [1],
                    "resultado_elegibilidade": True,
                    "avaliacao_alcada": {
                        "exige_analista": False,
                        "permite_calculo": True,
                        "justificativa": "Conclusao apoiada apenas na fonte afastada.",
                        "indices_fontes": [2],
                    },
                    "justificativa": "Leitura concluida.",
                }

        avaliacao = avaliar_normas_item(
            ItemDocumentalModel(
                categoria=Categoria.CONSULTA_MEDICA,
                valor_solicitado_brl=Decimal("100"),
                data_atendimento=date(2026, 4, 30),
            ),
            "Tenho direito?",
            recuperador=RecuperadorFalso(),
            llm=LlmFalso(),
        )

        self.assertIsNotNone(avaliacao.avaliacao_alcada)
        self.assertIsNone(avaliacao.avaliacao_alcada.exige_analista)
        self.assertIsNone(avaliacao.avaliacao_alcada.permite_calculo)
        self.assertIn(
            "fundamentacao normativa da alcada",
            avaliacao.avaliacao_alcada.pendencias,
        )

    def test_descarta_classificacao_apoiada_em_fonte_nao_aplicavel(self) -> None:
        class RecuperadorFalso:
            def recuperar(self, *args, **kwargs):
                return [
                    FonteModel("Regra aplicavel.", "Fonte A", {}, 0.9, ("bm25",)),
                    FonteModel("Regra afastada.", "Fonte B", {}, 0.8, ("vetorial",)),
                ]

            def recuperar_chunks_circulares_vigentes(self, _data_atendimento, **_kwargs):
                return []

        class LlmFalso:
            def with_structured_output(self, schema):
                return self

            def invoke(self, prompt: str):
                return {
                    "indices_aplicaveis": [1],
                    "resultado_elegibilidade": True,
                    "classificacao_decisao": "APROVADO",
                    "indices_fonte_classificacao": [2],
                    "justificativa_classificacao": "Apoiada em fonte afastada.",
                    "justificativa": "Leitura concluida.",
                }

        avaliacao = avaliar_normas_item(
            ItemDocumentalModel(
                categoria=Categoria.CONSULTA_MEDICA,
                valor_solicitado_brl=Decimal("100"),
                data_atendimento=date(2026, 4, 30),
            ),
            "Tenho direito?",
            recuperador=RecuperadorFalso(),
            llm=LlmFalso(),
        )

        self.assertIsNone(avaliacao.classificacao_decisao)
        self.assertEqual(avaliacao.indices_fonte_classificacao, [])

    def test_valida_classificacao_apoiada_em_fonte_aplicavel(self) -> None:
        fonte = FonteNormativaModel(
            texto="Regra vigente.",
            citacao="Regulamento | Art. 12",
            metadados={"status": "vigente", "vigencia_inicio": "2026-01-01"},
            score=0.9,
            origens=("bm25",),
        )

        avaliacao = AvaliacaoNormativaModel(
            consulta="consulta",
            ha_fonte_suficiente=True,
            vigencia_confirmada=True,
            fontes_recuperadas=[fonte],
            fontes_aplicaveis=[fonte],
            classificacao_decisao="APROVADO",
            indices_fonte_classificacao=[1],
            justificativa="Aprovado pela fonte 1.",
            justificativa_classificacao="Sem reducao de limite anual.",
        )

        self.assertEqual(avaliacao.classificacao_decisao, "APROVADO")
        self.assertEqual(avaliacao.indices_fonte_classificacao, [1])


if __name__ == "__main__":
    unittest.main()
