"""Operacoes monetarias deterministicas do reembolso."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.agents.normas.models import ParametrosCalculoNormativoModel


CENTAVOS = Decimal("0.01")
CEM = Decimal("100")


def arredondar_valor(valor: Decimal) -> Decimal:
    """Arredonda uma unica vez, no total final, pelo criterio meio para cima."""
    return valor.quantize(CENTAVOS, rounding=ROUND_HALF_UP)


def calcular_reembolso_item(
    valor_pago: Decimal,
    teto_brl: Decimal,
    coparticipacao_percentual: Decimal,
    saldo_anual: Decimal | None = None,
) -> Decimal:
    """Aplica a ordem normativa sem arredondamentos intermediarios."""
    base = min(valor_pago, teto_brl)
    apos_coparticipacao = base * (Decimal("1") - coparticipacao_percentual / CEM)
    return min(apos_coparticipacao, saldo_anual) if saldo_anual is not None else apos_coparticipacao


def calcular_total_reembolso(valores_itens: list[Decimal]) -> Decimal:
    """Soma os itens integralmente antes de arredondar o total informado."""
    return arredondar_valor(sum(valores_itens, Decimal("0")))


def converter_moeda(valor: Decimal, cotacao_venda: Decimal) -> Decimal:
    """Converte valor estrangeiro para reais usando a PTAX de venda."""
    return valor * cotacao_venda


def somar_reembolsos_ano(historico: list[dict[str, Any]], ano: int) -> Decimal:
    """Soma somente os pagamentos do ano civil da data-fato."""
    total = Decimal("0")
    for pedido in historico:
        try:
            data_atendimento = date.fromisoformat(str(pedido["data_atendimento"]))
            valor = Decimal(str(pedido["valor_reembolsado_brl"]))
        except (KeyError, TypeError, ValueError):
            continue
        if data_atendimento.year == ano:
            total += valor
    return total


def executar_calculo_normativo(
    valor_pago_brl: Decimal,
    parametros: ParametrosCalculoNormativoModel,
    *,
    total_reembolsado_ano: Decimal | None = None,
) -> Decimal | None:
    """Executa apenas a formula fixa quando todos os parametros existem."""
    if (
        parametros.teto_urs is None
        or parametros.valor_urs_brl is None
        or parametros.coparticipacao_percentual is None
    ):
        return None
    saldo_anual: Decimal | None = None
    if parametros.exige_limite_anual:
        if parametros.limite_anual_brl is None or total_reembolsado_ano is None:
            return None
        saldo_anual = max(Decimal("0"), parametros.limite_anual_brl - total_reembolsado_ano)
    teto_brl = parametros.teto_urs * parametros.valor_urs_brl
    return calcular_reembolso_item(
        valor_pago_brl,
        teto_brl,
        parametros.coparticipacao_percentual,
        saldo_anual,
    )
