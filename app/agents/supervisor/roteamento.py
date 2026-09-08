"""Selecao de acoes disponiveis conforme o estado do supervisor."""

from __future__ import annotations

from typing import Any

from app.agents.normas import AvaliacaoNormativaModel
from app.agents.supervisor.documentos import (
    _assinatura_itens_normativos,
    _documento_base,
    _ids_anexos_pendentes,
)
from app.agents.supervisor.models import (
    AcaoSupervisorEnum,
    EscolhaSupervisorModel,
    EstadoSupervisor,
    RoteadorSupervisor,
)
from app.agents.triagem import EstadoElegibilidadeEnum

LIMITE_PASSOS_TURNO = 12


def escolher_proxima_acao(
    resumo_estado: dict[str, Any],
    acoes_disponiveis: list[AcaoSupervisorEnum],
    llm: object | None = None,
) -> AcaoSupervisorEnum:
    """Delega ao modelo somente a escolha entre transicoes atualmente validas."""
    if llm is None:
        from app.llm import criar_llm

        llm = criar_llm(temperature=0)
    resultado = llm.with_structured_output(EscolhaSupervisorModel).invoke(
        "Voce coordena Triagem, Documento e Normas sem sequencia fixa. Escolha "
        "exclusivamente uma das acoes disponiveis conforme o estado. Priorize o "
        "agente capaz de produzir um fato ainda ausente ou atualizar uma analise "
        "afetada por fato novo. Nunca escolha responder enquanto houver trabalho "
        "disponivel. Retorne somente a estrutura solicitada.\n\n"
        f"ACOES DISPONIVEIS: {[acao.value for acao in acoes_disponiveis]}\n"
        f"ESTADO MINIMIZADO: {resumo_estado}"
    )
    escolha = EscolhaSupervisorModel.model_validate(resultado).acao
    if escolha not in acoes_disponiveis:
        raise ValueError("o supervisor escolheu uma transicao indisponivel")
    return escolha


def _ha_triagem_pendente(estado: EstadoSupervisor) -> bool:
    if estado.get("triagem_turno_id") != estado.get("turno_id"):
        return True
    if not estado.get("documentos_validos"):
        return False
    return (
        estado.get("elegibilidade_documentos_revisao", -1)
        != estado.get("documentos_revisao", 0)
        or estado.get("elegibilidade_normas_revisao", -1)
        != estado.get("normas_revisao", 0)
    )


def _ha_documento_pendente(estado: EstadoSupervisor) -> bool:
    return (
        estado.get("triagem_turno_id") == estado.get("turno_id")
        and not estado.get("pedido_terceiro_atual", False)
        and bool(_ids_anexos_pendentes(estado))
    )


def _ha_normas_pendentes(estado: EstadoSupervisor) -> bool:
    # Evita repetir a mesma chamada indisponivel ate atingir o limite de passos.
    if estado.get("normas_falha_turno_id") == estado.get("turno_id"):
        return False
    documento = _documento_base(estado)
    if documento is None or not documento.natureza_medica or not documento.itens:
        return False
    if not estado.get("triagem", {}).get("cadastro_validado"):
        return False
    if any(item.data_atendimento is None for item in documento.itens):
        return False
    avaliacao_desatualizada = estado.get("assinatura_itens_normativos", "") != _assinatura_itens_normativos(
        documento
    )
    elegibilidade = estado.get("triagem", {}).get("elegibilidade", {})
    avaliacoes = [
        AvaliacaoNormativaModel.model_validate(item)
        for item in estado.get("avaliacoes_normativas", [])
    ]
    historico_pendente = (
        _exige_historico(avaliacoes)
        and estado.get("historico_status") != "sucesso"
        and not (
            estado.get("historico_status") == "falha"
            and estado.get("historico_tentativa_turno_id") == estado.get("turno_id")
        )
    )
    calculo_desatualizado = (
        elegibilidade.get("estado") == EstadoElegibilidadeEnum.ELEGIVEL.value
        and (
            estado.get("calculo_normativo") is None
            or estado.get("calculo_normas_revisao", -1) != estado.get("normas_revisao", 0)
        )
        and not (
            estado.get("historico_status") == "falha"
            and estado.get("historico_tentativa_turno_id") == estado.get("turno_id")
        )
    )
    return avaliacao_desatualizada or historico_pendente or calculo_desatualizado


def _exige_historico(avaliacoes: list[AvaliacaoNormativaModel]) -> bool:
    return any(
        avaliacao.parametros_calculo and avaliacao.parametros_calculo.exige_limite_anual
        for avaliacao in avaliacoes
    )


def _acoes_disponiveis(estado: EstadoSupervisor) -> list[AcaoSupervisorEnum]:
    verificacoes = (
        (AcaoSupervisorEnum.TRIAGEM, _ha_triagem_pendente),
        (AcaoSupervisorEnum.DOCUMENTO, _ha_documento_pendente),
        (AcaoSupervisorEnum.NORMAS, _ha_normas_pendentes),
    )
    acoes = [acao for acao, disponivel in verificacoes if disponivel(estado)]
    return acoes or [AcaoSupervisorEnum.RESPONDER]


def coordenar(
    estado: EstadoSupervisor,
    roteador: RoteadorSupervisor,
) -> dict[str, Any]:
    """Monta transicoes validas e permite ao modelo escolher o proximo agente."""
    passos = estado.get("passos_turno", 0) + 1
    acoes = (
        [AcaoSupervisorEnum.RESPONDER]
        if passos > LIMITE_PASSOS_TURNO
        else _acoes_disponiveis(estado)
    )
    resumo = {
        "triagem_do_turno_concluida": estado.get("triagem_turno_id") == estado.get("turno_id"),
        "anexos_pendentes": len(_ids_anexos_pendentes(estado)),
        "documentos_revisao": estado.get("documentos_revisao", 0),
        "normas_revisao": estado.get("normas_revisao", 0),
        "elegibilidade": estado.get("triagem", {}).get("elegibilidade"),
        "pedido_terceiro_atual": estado.get("pedido_terceiro_atual", False),
    }
    acao = acoes[0] if len(acoes) == 1 else roteador(resumo, acoes)
    return {"passos_turno": passos, "proxima_acao": acao.value}
