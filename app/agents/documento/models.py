"""Modelos internos do agente de documentos."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas import Categoria


class TextoExtraidoModel(BaseModel):
    texto: str = ""
    paginas: int = 0
    erro: str | None = None


class ClassificacaoDocumentoModel(BaseModel):
    categoria: Categoria
    natureza_medica: bool
    justificativa: str = Field(min_length=1)


class FatosDocumentaisModel(BaseModel):
    """Fatos seguros para persistir e encaminhar aos demais agentes."""

    categoria: Categoria
    natureza_medica: bool
    aproveitavel: bool
    justificativa: str
    valor_solicitado_brl: Decimal | None = None
    data_atendimento: date | None = None
    codigo_tuss: str | None = None
    descricao_procedimento: str | None = None
    indicacao_clinica: bool = False
    pendencias: list[str] = Field(default_factory=list)
    relatorio_complementar: bool = False
