"""API publica do agente de triagem."""

from app.agents.triagem.models import (
    DadosCadastraisModel,
    ElegibilidadeModel,
    EstadoElegibilidadeEnum,
    EvidenciasNormativasModel,
    ExtracaoTriagemModel,
    FatosDocumentaisModel,
    IntencaoEnum,
    RespostaTriagemModel,
    ResultadoTriagemModel,
)
from app.agents.triagem.services import (
    avaliar_elegibilidade,
    construir_evidencias_normativas,
    extrair_triagem_estruturada,
    gerar_resposta_triagem,
    normalizar_carteirinha,
)


__all__ = [
    "DadosCadastraisModel",
    "ElegibilidadeModel",
    "EstadoElegibilidadeEnum",
    "EvidenciasNormativasModel",
    "ExtracaoTriagemModel",
    "FatosDocumentaisModel",
    "IntencaoEnum",
    "ResultadoTriagemModel",
    "RespostaTriagemModel",
    "avaliar_elegibilidade",
    "construir_evidencias_normativas",
    "extrair_triagem_estruturada",
    "gerar_resposta_triagem",
    "normalizar_carteirinha",
]
