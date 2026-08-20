"""API publica do agente de documentos."""

from app.agents.documento.models import (
    AnaliseConteudoDocumentalModel,
    ClassificacaoDocumentoModel,
    DadosDocumentoModel,
    EvidenciaRelatorioClinicoModel,
    FatosDocumentaisModel,
    ItemDocumentalModel,
    TextoExtraidoModel,
)
from app.agents.documento.services import (
    analisar_conteudo_documental,
    analisar_documento,
    classificar_documento,
    extrair_texto,
)

__all__ = [
    "AnaliseConteudoDocumentalModel",
    "ClassificacaoDocumentoModel",
    "DadosDocumentoModel",
    "EvidenciaRelatorioClinicoModel",
    "FatosDocumentaisModel",
    "ItemDocumentalModel",
    "TextoExtraidoModel",
    "analisar_conteudo_documental",
    "analisar_documento",
    "classificar_documento",
    "extrair_texto",
]
