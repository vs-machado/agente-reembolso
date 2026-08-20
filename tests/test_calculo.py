from __future__ import annotations

from decimal import Decimal
import unittest

from app.calculo import calcular_reembolso_item, calcular_total_reembolso, converter_moeda, somar_reembolsos_ano
from app.agents.normas import ParametrosCalculoNormativoModel


class TesteCalculo(unittest.TestCase):
    def test_aplica_ordem_normativa_sem_arredondar_item(self) -> None:
        valor = calcular_reembolso_item(
            Decimal("100.019"),
            Decimal("90.019"),
            Decimal("10"),
            Decimal("80"),
        )

        self.assertEqual(valor, Decimal("80"))

    def test_arredonda_somente_total_pelo_meio_para_cima(self) -> None:
        total = calcular_total_reembolso([Decimal("10.0025"), Decimal("10.0025")])

        self.assertEqual(total, Decimal("20.01"))

    def test_converte_moeda_com_decimal(self) -> None:
        self.assertEqual(converter_moeda(Decimal("10.25"), Decimal("5.1234")), Decimal("52.514850"))

    def test_soma_historico_apenas_do_ano_civil(self) -> None:
        total = somar_reembolsos_ano(
            [
                {"data_atendimento": "2026-01-15", "valor_reembolsado_brl": "12.10"},
                {"data_atendimento": "2026-12-20", "valor_reembolsado_brl": 3.2},
                {"data_atendimento": "2025-12-30", "valor_reembolsado_brl": "99.99"},
            ],
            2026,
        )

        self.assertEqual(total, Decimal("15.30"))

    def test_executa_calculo_com_parametros_extraidos(self) -> None:
        from app.calculo import executar_calculo_normativo

        resultado = executar_calculo_normativo(
            Decimal("500"),
            ParametrosCalculoNormativoModel(
                teto_urs=Decimal("40"),
                valor_urs_brl=Decimal("10"),
                coparticipacao_percentual=Decimal("20"),
                limite_anual_brl=Decimal("350"),
                exige_limite_anual=True,
                dispositivos_calculo=["Art. 12"],
            ),
            total_reembolsado_ano=Decimal("100"),
        )

        self.assertEqual(resultado, Decimal("250"))

    def test_nao_calcula_sem_parametro_obrigatorio(self) -> None:
        from app.calculo import executar_calculo_normativo

        self.assertIsNone(
            executar_calculo_normativo(
                Decimal("100"),
                ParametrosCalculoNormativoModel(coparticipacao_percentual=Decimal("20")),
            )
        )
