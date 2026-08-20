"""Normas — recuperação, vigência e cálculo.

Encontra a regra que vale para a categoria e o plano, resolve qual redação está
em vigor e apura o valor.

Cuidado com três coisas que a base cobra:
  * a regra e a condição que a limita nem sempre estão no mesmo artigo;
  * o texto impresso num documento pode ter sido alterado por outro;
  * a ordem das operações do cálculo é parte da própria norma.
"""

from app.agents.normas.models import (
    AvaliacaoNormativaModel,
    FonteAfastadaModel,
    FonteNormativaModel,
    LeituraNormativaModel,
    ParametrosCalculoNormativoModel,
    ResultadoCalculoNormativoModel,
)
from app.agents.normas.services import (
    avaliar_normas_item,
    avaliar_normas_itens,
    calcular_reembolsos_normativos,
    formular_consulta_normativa,
)

__all__ = [
    "AvaliacaoNormativaModel",
    "FonteAfastadaModel",
    "FonteNormativaModel",
    "LeituraNormativaModel",
    "ParametrosCalculoNormativoModel",
    "ResultadoCalculoNormativoModel",
    "avaliar_normas_item",
    "avaliar_normas_itens",
    "calcular_reembolsos_normativos",
    "formular_consulta_normativa",
]
