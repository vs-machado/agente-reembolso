from __future__ import annotations

from datetime import date
from unittest import TestCase

from app.tools import consultar_cotacao_ptax


class RespostaFalsa:
    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {
            "value": [
                {"dataHoraCotacao": "2026-05-01T13:00:00Z", "cotacaoVenda": 6.0},
                {"dataHoraCotacao": "2026-04-29T13:00:00Z", "cotacaoVenda": 5.2},
                {"dataHoraCotacao": "2026-04-30T13:00:00Z", "cotacaoVenda": 5.3},
            ]
        }


class ClienteFalso:
    def __init__(self) -> None:
        self.url = ""

    def get(self, url: str) -> RespostaFalsa:
        self.url = url
        return RespostaFalsa()


class TesteCotacao(TestCase):
    def test_seleciona_ultima_cotacao_sem_usar_data_futura(self) -> None:
        cliente = ClienteFalso()

        cotacao = consultar_cotacao_ptax("usd", date(2026, 4, 30), cliente_http=cliente)

        self.assertIsNotNone(cotacao)
        assert cotacao is not None
        self.assertEqual(str(cotacao.cotacao_venda), "5.3")
        self.assertEqual(cotacao.data_cotacao, date(2026, 4, 30))
        self.assertIn("@moeda=%27USD%27", cliente.url)

    def test_retorna_nada_quando_nao_ha_cotacao_anterior(self) -> None:
        class ClienteSemCotacao:
            def get(self, url: str) -> RespostaFalsa:
                return RespostaFalsa()

        class RespostaSemCotacao(RespostaFalsa):
            def json(self) -> dict:
                return {"value": []}

        class ClienteFalsoSemCotacao:
            def get(self, url: str) -> RespostaSemCotacao:
                return RespostaSemCotacao()

        self.assertIsNone(
            consultar_cotacao_ptax("USD", date(2026, 4, 30), cliente_http=ClienteFalsoSemCotacao())
        )
