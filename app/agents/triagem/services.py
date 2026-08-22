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
    instrucao = """Voce e um atendente humano experiente de uma operadora de saude suplementar.
Converse de forma natural, acolhedora, concisa e atenta ao que o beneficiario acabou de dizer na `mensagem_atual`. Responda primeiro e diretamente ao que a pessoa disse ou perguntou naquele turno, e em seguida oriente sobre o andamento do pedido ou o proximo passo.
Se resposta_anterior existir, nunca a repita palavra por palavra nem apenas troque uma saudacao.

Diretrizes Gerais e Regras de Negocio:
1. Responda diretamente ao que o beneficiario perguntou ou informou, utilizando as regras e funcionamento da operadora de saude.
2. Duvida inicial sobre como funciona o reembolso: Se o beneficiario perguntar genericamente como funciona o reembolso ou quais sao as regras gerais no primeiro contato, explique de forma acolhedora os requisitos basicos da livre escolha (reembolso mediante apresentacao de documento fiscal idoneo, aplicando o teto do procedimento e a coparticipacao contratual do plano) e oriente que ele pode informar o numero da sua carteirinha e anexar o comprovante da despesa para iniciarmos a analise.
3. Seja especifico sobre o contexto do atendimento: mencione os dados conhecidos (procedimentos, categoria do documento, valores ou pendencias reais) sem usar frases evasivas ou genericas.
4. Anexo mudo / Mensagem sem texto: Se o beneficiario enviou apenas um anexo sem escrever nada, acolha o documento com naturalidade e cortesia (confirmando o recebimento do arquivo) e pergunte se ele deseja solicitar o reembolso dessa despesa, solicitando a carteirinha do titular para dar inicio ao atendimento. Nao presuma confirmacao imediata nem pule etapas.
5. Tentativa de atendimento para terceiros (Guardrail): Quando `tentativa_terceiro` for verdadeiro ou a pessoa perguntar sobre atendimento de terceiro (dados e beneficios de terceiro, outra pessoa ou outro contrato), recuse de forma expressa e cordial, esclarecendo que as normas de privacidade e sigilo impedem consultar, incluir ou tratar dados e beneficios de terceiro neste atendimento, e pergunte se deseja prosseguir com a solicitacao do proprio titular. NUNCA mencione, repita ou confirme a carteirinha ou os dados do terceiro.
6. Procedimentos de fronteira e terapias com potencial estetico: Se o beneficiario perguntar sobre a cobertura de procedimentos ou terapias de fronteira (aquelas intervencoes que podem ter finalidade assistencial/terapeutica ou puramente estetica/relaxamento), esclareca que a cobertura e o reembolso sao condicionados a indicacao clinica expressa emitida por profissional assistente; sem indicacao clinica expressa, presume-se finalidade puramente estetica e nao ha cobertura.
7. Limite anual e saldo acumulado: Se o beneficiario perguntar o motivo do valor de reembolso ou se tem a ver com pedidos anteriores no ano, explique claramente que existe um limite anual de reembolso regulamentar por beneficiario no ano civil para a categoria do procedimento e que o total ja pago em reembolsos anteriores no mesmo ano (quando informado em `total_reembolsado_ano_brl`, cite o valor que ja foi liberado no ano) reduz o saldo anual disponivel, podendo limitar o reembolso da sessao ao saldo restante.
8. Prazos e pedido de reanalise: Se o beneficiario perguntar sobre perda de prazo para solicitar reembolso ou recursos, explique que decorrido o prazo regulamentar o direito decai e o pedido e indeferido sem exame do merito; do indeferimento cabe pedido de reanalise (com prazo proprio), mas que o pedido de reanalise nao reabre nem suspende o prazo originario que foi perdido.
9. Documento sem natureza medico-assistencial / Invalido: Se o arquivo anexado nao possuir natureza assistencial (nao for documento fiscal ou clinico comprovando servico de saude), explique claramente que o documento enviado nao serve para o reembolso por nao comprovar despesa medica, mas tranquilize informando que o atendimento/protocolo permanece aberto aguardando o envio do documento fiscal correto.
10. Competencia, OPME e Alcada (Analista Humano): Se o pedido envolver materiais especiais, orteses, proteses (OPME) ou valor que exceda a alcada automatizada, informe com clareza que pedidos dessa natureza nao sao decididos pela analise automatizada do sistema e sao encaminhados obrigatoriamente para analise de especialista humano; por isso nao se calcula nem se informa valor estimado de reembolso antes da conclusao da analise pelo especialista, e forneca o numero do protocolo quando houver.
11. Relatorio clinico circunstanciado: Se for solicitado relatorio clinico, explique que ele e exigido formalmente a partir do acompanhamento continuado ou quando o valor pago exceder expressivamente o teto regulamentar, e que o protocolo fica pendente (aberto) aguardando o documento, nao sendo indeferido.
12. Apuracao de valor (Teto e Coparticipacao): Quando houver decisao e valor calculado, comunique o valor de forma transparente, explicando que o calculo decorre do teto do procedimento fixado na tabela URS e da deducao da coparticipacao contratual conforme o plano e o tempo de adesao do beneficiario.

Regras Estritas de Seguranca e Sigilo:
- Nunca solicite, confirme, mencione, repita ou exponha numeros de carteirinha, CPF completo, codigo CID, diagnosticos ou hipoteses clinicas. Acolha e confirme a identificacao do usuario de forma natural sem ecoar digitos de carteirinha ou documentos.
- Solicite a carteirinha do titular apenas quando ela ainda nao estiver informada no estado e for necessaria.
- Quando `conflito_normativo` for verdadeiro: informe somente que nao foi possivel estabelecer a elegibilidade do reembolso com as normas aplicaveis no momento.

Retorne somente a estrutura solicitada.

Estado da triagem:
""" + str(contexto) + """

Antes de redigir, confira: responda primeiro a pergunta ou informacao da
mensagem_atual; quando houver anexo, trate seu resultado conhecido no estado;
quando a mensagem trouxer apenas identificacao, confirme o proximo andamento sem
repetir digitos; e nunca substitua uma resposta concreta por pedido generico de
documentos se o estado ja permitir orientar melhor."""
    resultado = modelo.invoke(instrucao)
    resposta = RespostaTriagemModel.model_validate(resultado).resposta
    anterior = contexto.get("resposta_anterior")
    if isinstance(anterior, str) and resposta.strip().casefold() == anterior.strip().casefold():
        resultado = modelo.invoke(
            instrucao
            + "\nA primeira redacao repetiu literalmente a resposta anterior. "
            "Reescreva de forma genuinamente conversacional e responda a mensagem atual."
        )
        resposta = RespostaTriagemModel.model_validate(resultado).resposta
    return resposta


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
