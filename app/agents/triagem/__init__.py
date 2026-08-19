"""Triagem — intenção, carteirinha e elegibilidade.

Extrai o que o beneficiário quer e o número da carteirinha de uma mensagem em
linguagem corrente, consulta o MCP e aplica o guardrail de terceiros.

Este módulo não interpreta anexos nem fontes normativas. Ele consolida apenas
resultados tipados produzidos pelos agentes responsáveis e pelo MCP.
"""

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


def normalizar_carteirinha(valor: str) -> str:
    """Remove formatacao sem impor quantidade de digitos."""
    return "".join(caractere for caractere in valor if caractere.isdigit())


def extrair_triagem_estruturada(mensagem: str, llm: object | None = None) -> ExtracaoTriagemModel:
    """Pede ao LLM uma extracao validada, sem delegar validacoes ao modelo."""
    if llm is None:
        from app.llm import criar_llm

        llm = criar_llm(temperature=0)
    modelo = llm.with_structured_output(ExtracaoTriagemModel)
    resultado = modelo.invoke(
        """Extraia os dados da triagem desta mensagem em portugues.
        Identifique a intencao de solicitar reembolso, se houver, e qualquer
        carteirinha explicitamente informada. Nao invente digitos. Marque
        tentativa_terceiro somente se a pessoa indicar que fala por outra pessoa.
        Retorne somente a estrutura solicitada.

        Mensagem:
        """
        + mensagem
    )
    return ExtracaoTriagemModel.model_validate(resultado)


def gerar_resposta_triagem(contexto: dict[str, object], llm: object | None = None) -> str:
    """Gera a resposta do beneficiario sem expor identificadores ou dados sensiveis."""
    if llm is None:
        from app.llm import criar_llm

        llm = criar_llm(temperature=0)
    modelo = llm.with_structured_output(RespostaTriagemModel)
    resultado = modelo.invoke(
        """Voce atende pedidos de reembolso em portugues. Gere uma resposta curta,
        cordial e especifica para o estado fornecido. Oriente somente o proximo
        passo necessario. Nunca exponha, repita ou solicite CPF, CID, carteirinha
        ou dados de outra pessoa. Nao invente analises documentais ou normativas.
        Quando `tentativa_terceiro` for verdadeiro, informe que o atendimento nao
        pode consultar, incluir ou informar dados e beneficios de terceiro, e que
        seguira somente com o pedido ja aberto para o titular. Nao revele nenhum
        dado da pessoa mencionada.
        Retorne somente a estrutura solicitada.

        Estado da triagem:
        """
        + str(contexto)
    )
    return RespostaTriagemModel.model_validate(resultado).resposta


def avaliar_elegibilidade(
    cadastro: DadosCadastraisModel,
    fatos: FatosDocumentaisModel,
    normas: EvidenciasNormativasModel,
) -> ElegibilidadeModel:
    """Conclui somente quando todas as fontes obrigatorias estao prontas."""
    pendencias = list(cadastro.pendencias) + list(fatos.pendencias) + list(normas.pendencias)
    if not cadastro.validado:
        pendencias.append("validacao cadastral")
    if not fatos.validado:
        pendencias.append("validacao documental")
    if not fatos.data_fato:
        pendencias.append("data do atendimento")
    if not normas.fonte_material:
        pendencias.append("fundamentacao normativa")
    if not normas.vigente:
        pendencias.append("vigencia normativa")
    if normas.conflito:
        pendencias.append("resolucao de conflito normativo")
    if normas.resultado_elegibilidade is None:
        pendencias.append("resultado normativo")

    pendencias = list(dict.fromkeys(pendencias))
    if pendencias:
        return ElegibilidadeModel(
            estado=EstadoElegibilidadeEnum.PENDENTE,
            justificativa="A elegibilidade depende das fontes e validacoes pendentes.",
            pendencias=pendencias,
        )
    if normas.resultado_elegibilidade:
        return ElegibilidadeModel(
            estado=EstadoElegibilidadeEnum.ELEGIVEL,
            justificativa="Dados cadastrais, fatos documentais e evidencia normativa validados.",
        )
    return ElegibilidadeModel(
        estado=EstadoElegibilidadeEnum.INELEGIVEL,
        justificativa="A evidencia normativa valida indica que o pedido nao e elegivel.",
    )


__all__ = [
    "DadosCadastraisModel",
    "ElegibilidadeModel",
    "EstadoElegibilidadeEnum",
    "EvidenciasNormativasModel",
    "ExtracaoTriagemModel",
    "RespostaTriagemModel",
    "FatosDocumentaisModel",
    "IntencaoEnum",
    "ResultadoTriagemModel",
    "RespostaTriagemModel",
    "avaliar_elegibilidade",
    "extrair_triagem_estruturada",
    "gerar_resposta_triagem",
    "gerar_resposta_triagem",
    "normalizar_carteirinha",
]
