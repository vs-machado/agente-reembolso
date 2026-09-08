"""Consolidacao dos resultados e resposta do turno."""

from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.agents.normas import AvaliacaoNormativaModel, ResultadoCalculoNormativoModel
from app.agents.supervisor.documentos import _documento_base
from app.agents.supervisor.models import EstadoSupervisor, GeradorResposta, RevisorResposta
from app.agents.triagem import EstadoElegibilidadeEnum
from app.schemas import ChatResponse, Decisao
from app.tools import ClienteMcp


def _normalizar_identificador_normativo(identificador: str) -> str | None:
    """Padroniza identificadores normativos para o formato canonico."""
    limpo = identificador.strip()
    if not limpo:
        return None
    match_art = re.match(r"^(?:art(?:\.|igo)?\s*|art-)(\d+)$", limpo, re.IGNORECASE)
    if match_art:
        return f"ART-{match_art.group(1)}"
    match_circ = re.match(r"^(?:circ(?:ular)?(?:\s+normativa)?[\s\-_]*|circ-)(\d+)[\/\-_](\d+)$", limpo, re.IGNORECASE)
    if match_circ:
        return f"CIRC-{int(match_circ.group(1)):02d}-{match_circ.group(2)}"
    match_tuss = re.match(r"^(?:tuss[\s\-_]*)?(\d{8})$", limpo, re.IGNORECASE)
    if match_tuss:
        return f"TUSS-{match_tuss.group(1)}"
    match_nt = re.match(r"^(?:nota\s+tecnica[\s\-_]*|nt-)(\d+)$", limpo, re.IGNORECASE)
    if match_nt:
        return f"NT-{int(match_nt.group(1)):02d}"
    match_anexo = re.match(r"^(?:anexo[\s\-_]*)([a-zivxlcdm]+)$", limpo, re.IGNORECASE)
    if match_anexo:
        return f"ANEXO-{match_anexo.group(1).upper()}"
    if re.match(r"^(?:ART-\d+|CIRC-\d+-\d+|TUSS-\d+|NT-\d+|ANEXO-[A-Z0-9]+)$", limpo, re.IGNORECASE):
        return limpo.upper()
    return None


def _consolidar(estado: EstadoSupervisor) -> dict[str, Any]:
    triagem = estado.get("triagem", {})
    documento = _documento_base(estado)
    avaliacoes = [
        AvaliacaoNormativaModel.model_validate(item)
        for item in estado.get("avaliacoes_normativas", [])
    ]
    calculo = (
        ResultadoCalculoNormativoModel.model_validate(estado["calculo_normativo"])
        if estado.get("calculo_normativo")
        else None
    )
    pendencias = list(triagem.get("pendencias", []))
    if documento:
        pendencias.extend(documento.pendencias)
    pendencias.extend(
        pendencia for avaliacao in avaliacoes for pendencia in avaliacao.pendencias
    )
    pendencias.extend(
        pendencia
        for avaliacao in avaliacoes
        if avaliacao.avaliacao_alcada
        for pendencia in avaliacao.avaliacao_alcada.pendencias
    )
    if estado.get("historico_status") == "falha":
        pendencias.append("consulta ao historico de reembolsos")
    if calculo:
        pendencias.extend(calculo.pendencias)
    conflito = any(avaliacao.ha_conflito_material for avaliacao in avaliacoes)
    alcadas = [
        avaliacao.avaliacao_alcada
        for avaliacao in avaliacoes
        if avaliacao.avaliacao_alcada is not None
    ]
    exige_analista = any(alcada.exige_analista is True for alcada in alcadas)
    impede_calculo = any(alcada.permite_calculo is False for alcada in alcadas)
    competencia_indeterminada = bool(avaliacoes) and (
        len(alcadas) != len(avaliacoes)
        or any(
            alcada.exige_analista is None or alcada.permite_calculo is None
            for alcada in alcadas
        )
    )
    elegibilidade = triagem.get("elegibilidade", {}).get("estado")
    decisao: Decisao | None = None
    invalido_no_turno = bool(
        estado.get("anexo_turno_id")
        and any(
            item["anexo_id"] == estado["anexo_turno_id"]
            for item in estado.get("documentos_invalidos", [])
        )
    )
    if estado.get("pedido_terceiro_atual"):
        decisao = Decisao.FORA_DE_ESCOPO
    elif conflito or competencia_indeterminada:
        decisao = None
    elif invalido_no_turno or (documento and documento.pendencias):
        decisao = Decisao.PENDENTE_DOCUMENTO
    elif exige_analista:
        decisao = Decisao.ESCALADO_ANALISTA
    elif elegibilidade == EstadoElegibilidadeEnum.INELEGIVEL.value:
        decisao = Decisao.NEGADO
    elif (
        elegibilidade == EstadoElegibilidadeEnum.ELEGIVEL.value
        and calculo
        and not calculo.pendencias
    ):
        classificacoes = [
            avaliacao.classificacao_decisao
            for avaliacao in avaliacoes
        ]
        if calculo.limitado_por_saldo_anual or any(c in ("APROVADO_PARCIAL", Decisao.APROVADO_PARCIAL.value) for c in classificacoes):
            decisao = Decisao.APROVADO_PARCIAL
        elif all(c in ("APROVADO", Decisao.APROVADO.value) for c in classificacoes):
            decisao = Decisao.APROVADO
        elif not avaliacoes or any(c is None for c in classificacoes):
            decisao = None
            pendencias.append("classificacao da reducao")
        else:
            decisao = None
            pendencias.append("classificacao da reducao")

    regras_brutas = [
        regra
        for avaliacao in avaliacoes
        for regra in (
            avaliacao.regras_aplicaveis
            + (
                avaliacao.avaliacao_alcada.regras_aplicaveis
                if avaliacao.avaliacao_alcada
                else []
            )
            + (
                avaliacao.parametros_calculo.dispositivos_calculo
                if avaliacao.parametros_calculo
                else []
            )
        )
    ]
    regras = list(
        dict.fromkeys(
            normalizada
            for regra in regras_brutas
            if (normalizada := _normalizar_identificador_normativo(regra)) is not None
        )
    )
    valor_solicitado = documento.valor_solicitado_total_brl if documento else None
    valor_reembolso = None if impede_calculo or decisao == Decisao.ESCALADO_ANALISTA else (
        calculo.valor_reembolso_brl if calculo else None
    )
    return {
        "categoria_documento": documento.categoria_dominante if documento else None,
        "decisao": decisao,
        "valor_solicitado_brl": valor_solicitado,
        "valor_reembolso_brl": valor_reembolso,
        "regras_aplicadas": regras,
        "pendencias": list(dict.fromkeys(pendencias)),
    }


def responder_turno(
    estado: EstadoSupervisor,
    gerador: GeradorResposta,
    revisor_resposta: RevisorResposta,
    cliente_mcp: ClienteMcp,
) -> dict[str, Any]:
    """Consolida fontes tipadas e executa protocolo somente apos alcada normativa."""
    dados = _consolidar(estado)
    triagem = dict(estado.get("triagem", {}))
    protocolo = triagem.get("protocolo")
    if dados["decisao"] == Decisao.ESCALADO_ANALISTA and not protocolo:
        titular = triagem.get("carteirinha_titular")
        if titular:
            payload = {
                "chave_idempotencia": f"{estado['pedido_id']}:alcada",
                "categoria": dados["categoria_documento"].value
                if dados["categoria_documento"]
                else None,
                "valor_solicitado_brl": str(dados["valor_solicitado_brl"])
                if dados["valor_solicitado_brl"] is not None
                else None,
                "regras_aplicadas": dados["regras_aplicadas"],
            }
            resultado = cliente_mcp.abrir_protocolo(titular, payload)
            if resultado.sucesso and resultado.dados:
                protocolo = resultado.dados.get("protocolo")
                triagem["protocolo"] = protocolo
            else:
                dados["pendencias"].append("abertura de protocolo")
    pendencias_conversa = list(dados["pendencias"])
    if not triagem.get("intencao_confirmada"):
        pendencias_conversa.append("confirmacao da solicitacao de reembolso")
    if not triagem.get("carteirinha_titular"):
        pendencias_conversa.append("carteirinha valida do titular")
    if not estado.get("anexos"):
        pendencias_conversa.append("comprovante do atendimento")
    mensagem_atual = next(
        item for item in reversed(estado["messages"]) if isinstance(item, HumanMessage)
    )
    respostas_anteriores = [
        str(item.content) for item in estado["messages"] if isinstance(item, AIMessage)
    ]
    avaliacoes = [
        AvaliacaoNormativaModel.model_validate(item)
        for item in estado.get("avaliacoes_normativas", [])
    ]
    calculo = (
        ResultadoCalculoNormativoModel.model_validate(estado["calculo_normativo"])
        if estado.get("calculo_normativo")
        else None
    )
    documento = _documento_base(estado)
    procedimentos = [
        item.descricao_procedimento
        for item in (documento.itens if documento else [])
        if item.descricao_procedimento
    ]
    contexto = {
        **dados,
        "protocolo": protocolo,
        "protocolo_aberto": bool(protocolo),
        "pendencias": list(dict.fromkeys(pendencias_conversa)),
        "decisao": dados["decisao"].value if dados["decisao"] else None,
        "tentativa_terceiro": bool(estado.get("pedido_terceiro_atual")),
        "conflito_normativo": bool(triagem.get("conflito_normativo")),
        "mensagem_atual": str(mensagem_atual.content),
        "resposta_anterior": respostas_anteriores[-1] if respostas_anteriores else None,
        "procedimentos": procedimentos,
        "total_reembolsado_ano_brl": (
            float(calculo.total_reembolsado_ano_brl)
            if calculo and calculo.total_reembolsado_ano_brl is not None
            else None
        ),
        "limitado_por_saldo_anual": calculo.limitado_por_saldo_anual if calculo else False,
        "parametros_calculo": [
            item.parametros_calculo.model_dump(mode="json")
            for item in avaliacoes
            if item.parametros_calculo is not None
        ],
    }
    texto = revisor_resposta(gerador(contexto))
    resposta = ChatResponse(resposta=texto, protocolo=protocolo, **dados)
    return {
        "messages": [AIMessage(content=texto)],
        "triagem": triagem,
        "resposta_chat": resposta.model_dump(mode="json"),
    }
