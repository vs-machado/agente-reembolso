"""Estado, escolhas e contratos de dependencias do supervisor."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from enum import Enum
from typing import Any

from langgraph.graph import MessagesState
from pydantic import BaseModel, Field

from app.agents.documento import FatosDocumentaisModel, ItemDocumentalModel
from app.agents.normas import AvaliacaoNormativaModel, ResultadoCalculoNormativoModel
from app.agents.triagem import ExtracaoTriagemModel
from app.tools import CotacaoPtaxModel


class AcaoSupervisorEnum(str, Enum):
    TRIAGEM = "triagem"
    DOCUMENTO = "documento"
    NORMAS = "normas"
    RESPONDER = "responder"


class EscolhaSupervisorModel(BaseModel):
    acao: AcaoSupervisorEnum
    motivo: str = Field(min_length=1)


class EstadoSupervisor(MessagesState, total=False):
    """Estado serializavel com fontes de verdade mantidas em campos separados."""

    turno_id: str
    anexo_turno_id: str | None
    anexo_recebido: dict[str, Any] | None
    anexos: list[dict[str, Any]]
    anexos_processados: list[str]
    anexos_bloqueados: list[str]
    documentos_validos: list[dict[str, Any]]
    documentos_invalidos: list[dict[str, Any]]
    triagem: dict[str, Any]
    avaliacoes_normativas: list[dict[str, Any]]
    calculo_normativo: dict[str, Any] | None
    historico_mcp: list[dict[str, Any]]
    historico_consultado: bool
    historico_status: str
    historico_tentativa_turno_id: str
    documentos_revisao: int
    normas_revisao: int
    normas_documentos_revisao: int
    assinatura_itens_normativos: str
    elegibilidade_documentos_revisao: int
    elegibilidade_normas_revisao: int
    calculo_normas_revisao: int
    normas_falha_turno_id: str
    triagem_turno_id: str
    pedido_terceiro_atual: bool
    passos_turno: int
    proxima_acao: str
    resposta_chat: dict[str, Any]
    pedido_id: str
    pedido_id_candidato: str


ExtratorTriagem = Callable[[str], ExtracaoTriagemModel]
GeradorResposta = Callable[[dict[str, object]], str]
ValidadorPedidoTerceiro = Callable[[str, str | None, str | None], bool]
RevisorResposta = Callable[[str], str]
AnalisadorDocumento = Callable[..., FatosDocumentaisModel]
AvaliadorNormas = Callable[[list[ItemDocumentalModel], str], list[AvaliacaoNormativaModel]]
CalculadorNormas = Callable[..., ResultadoCalculoNormativoModel]
ConsultorCotacao = Callable[[str, date], CotacaoPtaxModel | None]
RoteadorSupervisor = Callable[
    [dict[str, Any], list[AcaoSupervisorEnum]], AcaoSupervisorEnum
]
