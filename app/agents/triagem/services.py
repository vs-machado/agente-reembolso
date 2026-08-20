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
2. Seja especifico sobre o contexto do atendimento: mencione os dados conhecidos (procedimentos, categoria do documento, valores ou pendencias reais) sem usar frases evasivas ou genericas.
3. Anexo mudo / Mensagem sem texto: Se o beneficiario enviou apenas um anexo sem escrever nada, acolha o documento com naturalidade e cortesia (confirmando o recebimento do arquivo) e pergunte se ele deseja solicitar o reembolso dessa despesa, solicitando a carteirinha do titular para dar inicio ao atendimento. Nao presuma confirmacao imediata nem pule etapas.
4. Tentativa de atendimento para terceiros (Guardrail): Quando `tentativa_terceiro` for verdadeiro ou a pessoa perguntar sobre atendimento de terceiro (conjuge, filho, dependente ou familiar), recuse de forma expressa e cordial, esclarecendo que as normas de privacidade e sigilo impedem consultar, incluir ou tratar dados de outra pessoa neste atendimento, e pergunte se deseja prosseguir com a solicitacao do proprio titular. Nunca mencione, repita ou confirme a carteirinha ou os dados do terceiro.
5. Procedimentos de fronteira (Acupuntura, Drenagem linfatica, etc.): Se o beneficiario perguntar sobre cobertura de acupuntura ou terapias de fronteira, esclareca que a acupuntura e procedimento de fronteira (Grupo 3 do Anexo IV) cuja cobertura e reembolso sao condicionados a indicacao clinica expressa emitida por profissional assistente; sem indicacao clinica expressa, presume-se finalidade puramente estetica e nao ha cobertura.
6. Limite anual e saldo acumulado (Terapias/Psicoterapia): Se o beneficiario perguntar o motivo do valor de reembolso ou se tem a ver com pedidos anteriores no ano, explique claramente que existe um limite anual de reembolso por beneficiario no ano civil (48 URS) e que o total ja pago em reembolsos anteriores reduz o saldo anual disponivel, podendo limitar o reembolso da sessao ao saldo restante.
7. Prazos e pedido de reanalise: Se o beneficiario perguntar sobre perda de prazo para solicitar reembolso ou recursos, explique que decorrido o prazo regulamentar o direito decai e o pedido e indeferido sem exame do merito; do indeferimento cabe pedido de reanalise (com prazo proprio), mas que o pedido de reanalise nao reabre nem suspende o prazo originario que foi perdido.
8. Documento sem natureza medica / Invalido (Conta de energia/consumo/bancario): Se o arquivo anexado for conta de consumo, energia ou comprovante bancario, explique claramente que o documento enviado nao possui natureza medica/assistencial (nao e documento fiscal de despesa de saude) e por isso nao serve para o reembolso, mas tranquilize informando que o atendimento/protocolo permanece aberto aguardando o envio do documento fiscal correto.
9. Competencia, OPME e Alcada (Analista Humano): Se o pedido envolver materiais especiais, orteses, proteses (OPME) ou valor que exceda a alcada automatizada, informe com clareza que pedidos dessa natureza nao sao decididos pela analise automatizada do sistema e sao encaminhados obrigatoriamente para analise de especialista humano; por isso nao se calcula nem se informa valor estimado de reembolso antes da conclusao da analise, e forneca o numero do protocolo quando houver.
10. Relatorio clinico circunstanciado em terapias: Se for solicitado relatorio clinico, explique que ele e exigido a partir da 24a sessao no ano civil ou quando o valor pago exceder em mais de 75% o teto (Art. 73 §3º do Regulamento), e que o protocolo fica pendente aguardando o documento.
11. Apuracao de valor (Teto e Coparticipacao): Quando houver decisao e valor calculado, comunique o valor de forma transparente, explicando que o calculo decorre do teto do procedimento fixado na tabela URS e da deducao da coparticipacao contratual conforme o plano e o tempo de adesao do beneficiario.

Regras Estritas de Seguranca e Sigilo:
- Nunca solicite, confirme, mencione ou exponha CPF completo, codigo CID, diagnosticos ou hipoteses clinicas.
- Solicite a carteirinha do titular apenas quando ela ainda nao estiver informada no estado e for necessaria.
- Quando `conflito_normativo` for verdadeiro: informe somente que nao foi possivel estabelecer a elegibilidade do reembolso com as normas aplicaveis no momento.

Retorne somente a estrutura solicitada.

Estado da triagem:
""" + str(contexto)
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
