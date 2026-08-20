"""Consulta rastreavel da cotacao PTAX divulgada pelo Banco Central."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel


URL_PTAX = "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/CotacaoMoedaPeriodo"


class CotacaoPtaxModel(BaseModel):
    moeda: str
    cotacao_venda: Decimal
    data_cotacao: date
    url_consultada: str


def consultar_cotacao_ptax(
    moeda_iso: str,
    data_atendimento: date,
    *,
    cliente_http: httpx.Client | None = None,
) -> CotacaoPtaxModel | None:
    """Retorna a ultima cotacao de venda publicada ate a data-fato.

    A API recebe datas no formato americano. A janela inclui os sete dias
    anteriores para cobrir fins de semana e feriados, sem aceitar cotacao futura.
    """
    moeda = moeda_iso.upper().strip()
    if len(moeda) != 3 or not moeda.isalpha():
        raise ValueError("moeda ISO invalida")
    inicio = data_atendimento - timedelta(days=7)
    parametros = {
        "@moeda": f"'{moeda}'",
        "@dataInicial": f"'{inicio:%m-%d-%Y}'",
        "@dataFinalCotacao": f"'{data_atendimento:%m-%d-%Y}'",
        "$format": "json",
    }
    caminho = "(moeda=@moeda,dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)"
    url = f"{URL_PTAX}{caminho}?{urlencode(parametros, safe='@')}"
    proprio = cliente_http is None
    cliente = cliente_http or httpx.Client(timeout=15)
    try:
        resposta = cliente.get(url)
        resposta.raise_for_status()
        registros = resposta.json().get("value", [])
    finally:
        if proprio:
            cliente.close()

    candidatas: list[tuple[datetime, Decimal]] = []
    for registro in registros:
        try:
            momento = datetime.fromisoformat(str(registro["dataHoraCotacao"]).replace("Z", "+00:00"))
            valor = Decimal(str(registro["cotacaoVenda"]))
        except (KeyError, TypeError, ValueError, InvalidOperation):
            continue
        if momento.date() <= data_atendimento:
            candidatas.append((momento, valor))
    if not candidatas:
        return None
    momento, valor = max(candidatas, key=lambda item: item[0])
    return CotacaoPtaxModel(
        moeda=moeda,
        cotacao_venda=valor,
        data_cotacao=momento.date(),
        url_consultada=url,
    )
