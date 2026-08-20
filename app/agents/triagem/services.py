"""Extracao, resposta e avaliacao de elegibilidade da triagem."""

from __future__ import annotations

from app.agents.triagem.models import (
    DadosCadastraisModel,
    ElegibilidadeModel,
    EstadoElegibilidadeEnum,
    EvidenciasNormativasModel,
    ExtracaoTriagemModel,
    FatosDocumentaisModel,
    RespostaTriagemModel,
)
from app.agents.normas.models import AvaliacaoNormativaModel


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
        dado da pessoa mencionada. Quando `conflito_normativo` for verdadeiro,
        informe somente que nao foi possivel estabelecer a elegibilidade do
        reembolso com as normas aplicaveis. Nao escolha uma regra, nao prometa
        protocolo e nao atribua culpa ao beneficiario.
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


def construir_evidencias_normativas(avaliacao: AvaliacaoNormativaModel) -> EvidenciasNormativasModel:
    """Converte a avaliacao rastreavel em fatos para a decisao da triagem."""
    pendencias = list(avaliacao.pendencias)
    if avaliacao.ha_conflito_material:
        pendencias.append("elegibilidade nao estabelecida por conflito normativo")
    return EvidenciasNormativasModel(
        fonte_material=avaliacao.ha_fonte_suficiente,
        vigente=avaliacao.vigencia_confirmada,
        conflito=avaliacao.ha_conflito_material,
        resultado_elegibilidade=avaliacao.resultado_elegibilidade,
        pendencias=list(dict.fromkeys(pendencias)),
    )
