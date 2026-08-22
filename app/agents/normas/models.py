"""Modelos internos da avaliacao normativa."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


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

    @field_validator(
        "teto_urs",
        "valor_urs_brl",
        "coparticipacao_percentual",
        "limite_anual_brl",
        mode="before",
    )
    @classmethod
    def aceitar_decimal_em_formato_local(cls, valor: object) -> object:
        """Aceita a notacao decimal brasileira produzida pela leitura em portugues."""
        if not isinstance(valor, str) or "," not in valor:
            return valor
        return valor.replace(".", "").replace(",", ".")

    @model_validator(mode="after")
    def preencher_limite_anual_se_ausente(self) -> Self:
        if self.exige_limite_anual and self.limite_anual_brl is None and self.valor_urs_brl is not None:
            self.limite_anual_brl = Decimal("48") * self.valor_urs_brl
        return self


class AvaliacaoAlcadaModel(BaseModel):
    """Competencia decisoria extraida das fontes, sem regras fixadas no codigo."""

    exige_analista: bool | None = None
    permite_calculo: bool | None = None
    item_sob_analise: bool | None = None
    justificativa: str = Field(min_length=1)
    regras_aplicaveis: list[str] = Field(default_factory=list)
    indices_fontes: list[int] = Field(default_factory=list)
    pendencias: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validar_conclusao(self) -> "AvaliacaoAlcadaModel":
        """Impede conclusoes contraditorias ou sem referencia normativa."""
        if (self.exige_analista is None) != (self.permite_calculo is None):
            raise ValueError("decisao de alcada deve preencher ambos os campos")
        conclusao = self.exige_analista is not None
        if conclusao and not self.indices_fontes:
            raise ValueError("conclusao de alcada exige ao menos uma fonte")
        if self.exige_analista is True and self.permite_calculo is not False:
            raise ValueError("analise humana deve bloquear o calculo automatizado")
        if self.exige_analista is False and self.permite_calculo is not True:
            raise ValueError("competencia automatizada deve autorizar explicitamente o calculo")
        return self


class LeituraNormativaModel(BaseModel):
    """Leitura do LLM limitada aos indices das fontes recuperadas."""

    indices_aplicaveis: list[int] = Field(default_factory=list)
    motivos_afastamento: dict[int, str] = Field(default_factory=dict)
    ha_conflito_material: bool = False
    resultado_elegibilidade: bool | None = None
    regras_aplicaveis: list[str] = Field(default_factory=list)
    parametros_calculo: ParametrosCalculoNormativoModel | None = None
    avaliacao_alcada: AvaliacaoAlcadaModel | None = None
    classificacao_decisao: str | None = None
    indices_fonte_classificacao: list[int] = Field(default_factory=list)
    justificativa_classificacao: str = ""
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
    avaliacao_alcada: AvaliacaoAlcadaModel | None = None
    classificacao_decisao: str | None = None
    indices_fonte_classificacao: list[int] = Field(default_factory=list)
    justificativa_classificacao: str = ""
    resultado_elegibilidade: bool | None = None
    pendencias: list[str] = Field(default_factory=list)
    justificativa: str = Field(min_length=1)

    @model_validator(mode="after")
    def validar_fundamentacao_alcada(self) -> "AvaliacaoNormativaModel":
        alcada = self.avaliacao_alcada
        if alcada is None or alcada.exige_analista is None:
            return self
        if not self.ha_fonte_suficiente or not self.vigencia_confirmada:
            raise ValueError("conclusao de alcada exige fonte suficiente e vigente")
        for indice in alcada.indices_fontes:
            if not 1 <= indice <= len(self.fontes_recuperadas):
                raise ValueError("indice de fonte da alcada e invalido")
            if self.fontes_recuperadas[indice - 1] not in self.fontes_aplicaveis:
                raise ValueError("fonte da alcada nao esta entre as fontes aplicaveis")
        return self

    @model_validator(mode="after")
    def validar_fundamentacao_classificacao(self) -> "AvaliacaoNormativaModel":
        if self.classificacao_decisao is None:
            return self
        if not self.ha_fonte_suficiente or not self.vigencia_confirmada:
            raise ValueError("conclusao de classificacao da decisao exige fonte suficiente e vigente")
        if not self.indices_fonte_classificacao:
            raise ValueError("conclusao de classificacao da decisao exige ao menos uma fonte")
        for indice in self.indices_fonte_classificacao:
            if not 1 <= indice <= len(self.fontes_recuperadas):
                raise ValueError("indice de fonte da classificacao de decisao e invalido")
            if self.fontes_recuperadas[indice - 1] not in self.fontes_aplicaveis:
                raise ValueError("fonte da classificacao de decisao nao esta entre as fontes aplicaveis")
        return self


class ResultadoCalculoNormativoModel(BaseModel):
    """Resultado deterministico agregado, pronto para o supervisor apresentar."""

    valores_itens_brl: list[Decimal] = Field(default_factory=list)
    valor_reembolso_brl: Decimal | None = None
    total_reembolsado_ano_brl: Decimal | None = None
    limitado_por_saldo_anual: bool = False
    pendencias: list[str] = Field(default_factory=list)
