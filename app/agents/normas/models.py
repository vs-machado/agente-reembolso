"""Modelos internos da avaliacao normativa."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class FonteNormativaModel(BaseModel):
    """Fonte retornada pelo RAG, preservada para auditoria da decisao."""

    texto: str
    citacao: str
    metadados: dict[str, Any]
    score: float
    origens: tuple[str, ...]
    score_llm: int | None = None


class FonteAfastadaModel(FonteNormativaModel):
    """Fonte recuperada que nao pode fundamentar a avaliacao."""

    motivo_afastamento: str = Field(min_length=1)


class ParametrosCalculoNormativoModel(BaseModel):
    """Parametros extraidos das fontes para o executor deterministico."""

    teto_urs: Decimal | None = Field(default=None, ge=0)
    valor_urs_brl: Decimal | None = Field(default=None, ge=0)
    coparticipacao_percentual: Decimal | None = Field(default=None, ge=0, le=100)
    limite_anual_brl: Decimal | None = Field(default=None, ge=0)
    exige_limite_anual: bool = False
    dispositivos_calculo: list[str] = Field(default_factory=list)


class LeituraNormativaModel(BaseModel):
    """Leitura do LLM limitada aos indices das fontes recuperadas."""

    indices_aplicaveis: list[int] = Field(default_factory=list)
    motivos_afastamento: dict[int, str] = Field(default_factory=dict)
    ha_conflito_material: bool = False
    resultado_elegibilidade: bool | None = None
    regras_aplicaveis: list[str] = Field(default_factory=list)
    parametros_calculo: ParametrosCalculoNormativoModel | None = None
    justificativa: str = Field(min_length=1)


class AvaliacaoNormativaModel(BaseModel):
    """Resultado interno rastreavel da recuperacao e leitura normativa."""

    consulta: str = Field(min_length=1)
    data_fato: date | None = None
    fontes_recuperadas: list[FonteNormativaModel] = Field(default_factory=list)
    fontes_aplicaveis: list[FonteNormativaModel] = Field(default_factory=list)
    fontes_afastadas: list[FonteAfastadaModel] = Field(default_factory=list)
    ha_fonte_suficiente: bool = False
    vigencia_confirmada: bool = False
    ha_conflito_material: bool = False
    regras_aplicaveis: list[str] = Field(default_factory=list)
    parametros_calculo: ParametrosCalculoNormativoModel | None = None
    resultado_elegibilidade: bool | None = None
    pendencias: list[str] = Field(default_factory=list)
    justificativa: str = Field(min_length=1)


class ResultadoCalculoNormativoModel(BaseModel):
    """Resultado deterministico agregado, pronto para o supervisor apresentar."""

    valores_itens_brl: list[Decimal] = Field(default_factory=list)
    valor_reembolso_brl: Decimal | None = None
    total_reembolsado_ano_brl: Decimal | None = None
    pendencias: list[str] = Field(default_factory=list)
