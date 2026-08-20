"""Modelos do agente de triagem."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class IntencaoEnum(str, Enum):
    SOLICITAR_REEMBOLSO = "SOLICITAR_REEMBOLSO"
    OUTRA = "OUTRA"
    AUSENTE = "AUSENTE"


class EstadoElegibilidadeEnum(str, Enum):
    PENDENTE = "PENDENTE"
    ELEGIVEL = "ELEGIVEL"
    INELEGIVEL = "INELEGIVEL"


class ElegibilidadeModel(BaseModel):
    estado: EstadoElegibilidadeEnum
    justificativa: str
    pendencias: list[str] = Field(default_factory=list)


class ResultadoTriagemModel(BaseModel):
    intencao: IntencaoEnum = IntencaoEnum.AUSENTE
    confianca_intencao: float | None = Field(default=None, ge=0, le=1)
    carteirinha_candidata: str | None = None
    intencao_presente: bool = False
    carteirinha_presente: bool = False
    anexo_presente: bool = False
    tentativa_terceiro: bool = False
    elegibilidade: ElegibilidadeModel | None = None
    pendencias: list[str] = Field(default_factory=list)
    resposta_coleta: str | None = None


class ExtracaoTriagemModel(BaseModel):
    """Saida estruturada solicitada ao LLM, antes das regras locais."""

    intencao: IntencaoEnum = IntencaoEnum.AUSENTE
    confianca_intencao: float | None = Field(default=None, ge=0, le=1)
    carteirinha: str | None = None
    tentativa_terceiro: bool = False


class RespostaTriagemModel(BaseModel):
    """Resposta conversacional produzida pelo LLM a partir do estado minimizado."""

    resposta: str = Field(min_length=1)


class DadosCadastraisModel(BaseModel):
    validado: bool = False
    carteirinha_titular: str | None = None
    pendencias: list[str] = Field(default_factory=list)


class FatosDocumentaisModel(BaseModel):
    validado: bool = False
    data_fato: str | None = None
    pendencias: list[str] = Field(default_factory=list)


class EvidenciasNormativasModel(BaseModel):
    fonte_material: bool = False
    vigente: bool = False
    conflito: bool = False
    resultado_elegibilidade: bool | None = None
    pendencias: list[str] = Field(default_factory=list)
