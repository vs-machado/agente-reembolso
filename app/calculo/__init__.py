"""Cálculo — a apuração do valor a reembolsar.

Tudo o que este módulo precisa fazer está escrito, em português, na `kb/`.
Nenhuma fórmula é entregue aqui de propósito: reconstruí-la lendo o regulamento
é a prova.

O que você vai precisar responder:
  * qual é o teto do procedimento e de onde ele vem;
  * o que se compara com o quê, e em que ordem;
  * o que diferencia um plano do outro;
  * como se arredonda.
"""

from app.calculo.services import (
    arredondar_valor,
    calcular_reembolso_item,
    calcular_total_reembolso,
    converter_moeda,
    executar_calculo_normativo,
    somar_reembolsos_ano,
)

__all__ = [
    "arredondar_valor",
    "calcular_reembolso_item",
    "calcular_total_reembolso",
    "converter_moeda",
    "executar_calculo_normativo",
    "somar_reembolsos_ano",
]
