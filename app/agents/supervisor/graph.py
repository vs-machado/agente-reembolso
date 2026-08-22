"""Supervisor LangGraph que coordena agentes pela disponibilidade do estado."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from datetime import date
from enum import Enum
import json
import logging
import os
import re
from threading import RLock
from time import perf_counter
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from pydantic import BaseModel, Field

from app.agents.documento import FatosDocumentaisModel, ItemDocumentalModel, analisar_documento
from app.agents.normas import (
    AvaliacaoNormativaModel,
    ResultadoCalculoNormativoModel,
    avaliar_normas_itens,
    calcular_reembolsos_normativos,
)
from app.agents.triagem import (
    DadosCadastraisModel,
    EstadoElegibilidadeEnum,
    EvidenciasNormativasModel,
    ExtracaoTriagemModel,
    FatosDocumentaisModel as FatosTriagemModel,
    ResultadoTriagemModel,
    avaliar_elegibilidade,
    extrair_triagem_estruturada,
    gerar_resposta_triagem,
    normalizar_carteirinha,
)
from app.calculo import converter_moeda, somar_reembolsos_ano
from app.guardrails import revisar_resposta_beneficiario, validar_pedido_terceiro
from app.rag import RetrieverHibrido
from app.schemas import Anexo, Categoria, ChatRequest, ChatResponse, Decisao
from app.tools import ClienteMcp, CotacaoPtaxModel, consultar_cotacao_ptax

LIMITE_PASSOS_TURNO = 12
LOG = logging.getLogger("uvicorn.error")


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


def preparar_turno(estado: EstadoSupervisor) -> dict[str, Any]:
    """Registra o anexo integral e reinicia apenas os controles efemeros do turno."""
    anexos = list(estado.get("anexos", []))
    anexo_turno_id = estado.get("anexo_turno_id")
    anexo_recebido = estado.get("anexo_recebido")
    if anexo_turno_id and anexo_recebido and not any(
        item["id"] == anexo_turno_id for item in anexos
    ):
        anexos.append({"id": anexo_turno_id, "anexo": anexo_recebido})
    return {
        "anexos": anexos,
        "pedido_terceiro_atual": False,
        "passos_turno": 0,
        "proxima_acao": "",
        "pedido_id": estado.get("pedido_id") or estado["pedido_id_candidato"],
    }


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


def _ids_anexos_pendentes(estado: EstadoSupervisor) -> list[str]:
    encerrados = set(estado.get("anexos_processados", [])) | set(
        estado.get("anexos_bloqueados", [])
    )
    return [item["id"] for item in estado.get("anexos", []) if item["id"] not in encerrados]


def _ha_documento_pendente(estado: EstadoSupervisor) -> bool:
    return (
        estado.get("triagem_turno_id") == estado.get("turno_id")
        and not estado.get("pedido_terceiro_atual", False)
        and bool(_ids_anexos_pendentes(estado))
    )


def _documento_base(estado: EstadoSupervisor) -> FatosDocumentaisModel | None:
    documentos = [
        FatosDocumentaisModel.model_validate(item["fatos"])
        for item in estado.get("documentos_validos", [])
    ]
    fiscais = [documento for documento in documentos if documento.itens]
    if not fiscais:
        return documentos[-1] if documentos else None
    base = fiscais[-1]
    itens: list[ItemDocumentalModel] = []
    assinaturas: set[str] = set()
    for documento in fiscais:
        for item in documento.itens:
            assinatura = item.model_dump_json()
            if assinatura not in assinaturas:
                itens.append(item)
                assinaturas.add(assinatura)
    relatorios = [
        documento.evidencia_relatorio
        for documento in documentos
        if documento.evidencia_relatorio is not None
    ]
    tem_relatorio_valido = any(
        doc.categoria == Categoria.RELATORIO_CLINICO and not doc.pendencias
        for doc in documentos
    )
    tem_pedido_medico_valido = any(
        doc.evidencia_pedido_medico is not None and not doc.pendencias
        for doc in documentos
    )
    historico_sucesso = estado.get("historico_status") == "sucesso"
    pendencias: list[str] = []
    for documento in documentos:
        for pendencia in documento.pendencias:
            if historico_sucesso and pendencia == "P19":
                continue
            if tem_relatorio_valido and pendencia in ("P21", "P22", "relatorio clinico", "relatorio_clinico"):
                continue
            if tem_pedido_medico_valido and pendencia in ("P21", "pedido medico", "pedido_medico"):
                continue
            if pendencia not in pendencias:
                pendencias.append(pendencia)

    return base.model_copy(
        update={
            "itens": itens,
            "evidencia_relatorio": relatorios[-1] if relatorios else base.evidencia_relatorio,
            "relatorio_complementar": bool(relatorios),
            "pendencias": pendencias,
            "aproveitavel": not pendencias,
        }
    )


def _assinatura_itens_normativos(documento: FatosDocumentaisModel | None) -> str:
    """Representa somente fatos de despesa que podem alterar a leitura normativa."""
    if documento is None:
        return ""
    return json.dumps(
        [item.model_dump(mode="json") for item in documento.itens],
        ensure_ascii=False,
        sort_keys=True,
    )


def _ha_normas_pendentes(estado: EstadoSupervisor) -> bool:
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


def _converter_documentos_para_brl(
    documentos: list[dict[str, Any]],
    consultar_cotacao: ConsultorCotacao,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Converte somente despesas estrangeiras com a PTAX da data-fato."""
    atualizados: list[dict[str, Any]] = []
    pendencias: list[str] = []
    for registro in documentos:
        fatos = FatosDocumentaisModel.model_validate(registro["fatos"])
        itens: list[ItemDocumentalModel] = []
        alterado = False
        for item in fatos.itens:
            moeda = item.codigo_moeda_iso
            if (
                item.valor_solicitado_brl is not None
                or item.valor_original is None
                or moeda in {None, "BRL"}
                or item.data_atendimento is None
            ):
                itens.append(item)
                continue
            try:
                cotacao = consultar_cotacao(moeda, item.data_atendimento)
            except Exception:
                cotacao = None
            if cotacao is None:
                pendencias.append(f"cotacao PTAX para {moeda}")
                itens.append(item)
                continue
            itens.append(
                item.model_copy(
                    update={
                        "valor_solicitado_brl": converter_moeda(
                            item.valor_original, cotacao.cotacao_venda
                        )
                    }
                )
            )
            alterado = True
        if alterado:
            atualizados.append(
                {**registro, "fatos": fatos.model_copy(update={"itens": itens}).model_dump(mode="json")}
            )
        else:
            atualizados.append(registro)
    return atualizados, list(dict.fromkeys(pendencias))


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


def executar_triagem(
    estado: EstadoSupervisor,
    extrator: ExtratorTriagem,
    cliente_mcp: ClienteMcp,
    validador_pedido_terceiro: ValidadorPedidoTerceiro,
) -> dict[str, Any]:
    """Atualiza conversa, cadastro e elegibilidade sem assumir ordem dos demais agentes."""
    triagem = dict(estado.get("triagem", {}))
    pedido_terceiro = bool(estado.get("pedido_terceiro_atual"))
    atualizacoes: dict[str, Any] = {}
    if estado.get("triagem_turno_id") != estado.get("turno_id"):
        mensagem = next(
            item for item in reversed(estado["messages"]) if isinstance(item, HumanMessage)
        )
        extracao = extrator(str(mensagem.content))
        candidata = normalizar_carteirinha(extracao.carteirinha or "") or None
        titular = triagem.get("carteirinha_titular")
        pedido_terceiro = bool(extracao.tentativa_terceiro) or validador_pedido_terceiro(
            str(mensagem.content), candidata, titular
        )
        resultado = ResultadoTriagemModel(
            intencao=extracao.intencao,
            confianca_intencao=extracao.confianca_intencao,
            carteirinha_candidata=candidata,
            intencao_presente=bool(triagem.get("intencao_confirmada"))
            or extracao.intencao.value == "SOLICITAR_REEMBOLSO",
            carteirinha_presente=bool(titular or candidata),
            anexo_presente=bool(estado.get("anexos")),
            tentativa_terceiro=pedido_terceiro,
            pendencias=list(triagem.get("pendencias", [])),
        )
        triagem.update(resultado.model_dump(mode="json"))
        triagem["intencao_confirmada"] = resultado.intencao_presente
        if candidata and not titular and not pedido_terceiro:
            consulta = cliente_mcp.consultar_beneficiario(candidata)
            pendencias = list(triagem.get("pendencias", []))
            if consulta.sucesso and consulta.dados:
                triagem["carteirinha_titular"] = normalizar_carteirinha(
                    str(consulta.dados.get("carteirinha", candidata))
                )
                triagem["cadastro_validado"] = True
                triagem["nome_titular"] = consulta.dados.get("nome")
                triagem["dados_cadastrais"] = {
                    chave: valor
                    for chave, valor in consulta.dados.items()
                    if chave not in {"cpf", "cpf_mascarado", "carteirinha", "nome"}
                }
                pendencias = [item for item in pendencias if item != "carteirinha nao localizada"]
            elif "carteirinha nao localizada" not in pendencias:
                pendencias.append("carteirinha nao localizada")
            triagem["pendencias"] = pendencias
        if pedido_terceiro and estado.get("anexo_turno_id"):
            bloqueados = list(estado.get("anexos_bloqueados", []))
            bloqueados.append(estado["anexo_turno_id"])
            atualizacoes["anexos_bloqueados"] = list(dict.fromkeys(bloqueados))
        atualizacoes["triagem_turno_id"] = estado["turno_id"]

    documento = _documento_base(estado)
    avaliacoes = [
        AvaliacaoNormativaModel.model_validate(item)
        for item in estado.get("avaliacoes_normativas", [])
    ]
    pendencias_documentais = list(documento.pendencias) if documento else []
    if estado.get("historico_status") == "sucesso":
        pendencias_documentais = [
            pendencia for pendencia in pendencias_documentais if pendencia != "P19"
        ]
    resultados = [avaliacao.resultado_elegibilidade for avaliacao in avaliacoes]
    resultado_normativo = (
        False if False in resultados else True if resultados and all(resultados) else None
    )
    evidencias = EvidenciasNormativasModel(
        fonte_material=bool(avaliacoes) and all(item.ha_fonte_suficiente for item in avaliacoes),
        vigente=bool(avaliacoes) and all(item.vigencia_confirmada for item in avaliacoes),
        conflito=any(item.ha_conflito_material for item in avaliacoes),
        resultado_elegibilidade=resultado_normativo,
        pendencias=list(
            dict.fromkeys(pendencia for item in avaliacoes for pendencia in item.pendencias)
        ),
    )
    elegibilidade = avaliar_elegibilidade(
        DadosCadastraisModel(
            validado=bool(triagem.get("cadastro_validado")),
            carteirinha_titular=triagem.get("carteirinha_titular"),
            pendencias=list(triagem.get("pendencias", [])),
        ),
        FatosTriagemModel(
            validado=bool(documento and not pendencias_documentais),
            data_fato=(
                documento.itens[0].data_atendimento.isoformat()
                if documento and documento.itens and documento.itens[0].data_atendimento
                else None
            ),
            pendencias=pendencias_documentais,
        ),
        evidencias,
    )
    triagem["elegibilidade"] = elegibilidade.model_dump(mode="json")
    triagem["conflito_normativo"] = evidencias.conflito
    atualizacoes.update(
        {
            "triagem": triagem,
            "pedido_terceiro_atual": pedido_terceiro,
            "elegibilidade_documentos_revisao": estado.get("documentos_revisao", 0),
            "elegibilidade_normas_revisao": estado.get("normas_revisao", 0),
        }
    )
    return atualizacoes


def executar_documento(
    estado: EstadoSupervisor,
    analisador: AnalisadorDocumento,
) -> dict[str, Any]:
    """Processa um anexo pendente e isola documentos sem natureza medica."""
    anexo_id = _ids_anexos_pendentes(estado)[0]
    registro = next(item for item in estado["anexos"] if item["id"] == anexo_id)
    fatos = analisador(
        Anexo.model_validate(registro["anexo"]),
        nome_titular=estado.get("triagem", {}).get("nome_titular"),
        ha_pedido_pendente=bool(estado.get("documentos_validos")),
    )
    processados = list(estado.get("anexos_processados", [])) + [anexo_id]
    if not fatos.natureza_medica or fatos.categoria == Categoria.INVALIDO:
        invalidos = list(estado.get("documentos_invalidos", []))
        invalidos.append({"anexo_id": anexo_id, "fatos": fatos.model_dump(mode="json")})
        return {"anexos_processados": processados, "documentos_invalidos": invalidos}
    validos = list(estado.get("documentos_validos", []))
    validos.append({"anexo_id": anexo_id, "fatos": fatos.model_dump(mode="json")})
    return {
        "anexos_processados": processados,
        "documentos_validos": validos,
        "documentos_revisao": estado.get("documentos_revisao", 0) + 1,
        "calculo_normativo": None,
    }


def executar_normas(
    estado: EstadoSupervisor,
    avaliador: AvaliadorNormas,
    calculador: CalculadorNormas,
    cliente_mcp: ClienteMcp,
    consultar_cotacao: ConsultorCotacao,
) -> dict[str, Any]:
    """Atualiza fontes vigentes e revisita calculo somente apos elegibilidade."""
    documento = _documento_base(estado)
    if documento is None:
        return {}
    atualizacoes: dict[str, Any] = {}
    avaliacoes = [
        AvaliacaoNormativaModel.model_validate(item)
        for item in estado.get("avaliacoes_normativas", [])
    ]
    assinatura_itens = _assinatura_itens_normativos(documento)
    if estado.get("assinatura_itens_normativos", "") != assinatura_itens:
        pergunta = str(
            next(item for item in reversed(estado["messages"]) if isinstance(item, HumanMessage)).content
        )
        dados_cadastrais = estado.get("triagem", {}).get("dados_cadastrais", {})
        contexto_cadastral = {
            chave: dados_cadastrais.get(chave)
            for chave in ("plano", "data_adesao", "status")
            if dados_cadastrais.get(chave) is not None
        }
        if "data_adesao" in contexto_cadastral and documento and documento.itens:
            try:
                data_adesao = date.fromisoformat(str(contexto_cadastral["data_adesao"]))
                data_atendimento = documento.itens[0].data_atendimento
                if data_atendimento:
                    meses = (data_atendimento.year - data_adesao.year) * 12 + (data_atendimento.month - data_adesao.month)
                    if data_atendimento.day < data_adesao.day:
                        meses -= 1
                    contexto_cadastral["tempo_adesao_meses_completos"] = max(0, meses)
            except Exception:
                pass
        pergunta_normativa = (
            f"{pergunta}\nContexto cadastral na data do atendimento: "
            f"{contexto_cadastral}"
        )
        avaliacoes = avaliador(documento.itens, pergunta_normativa)
        LOG.info(
            "normas_resultado=%s",
            json.dumps(
                [
                    {
                        "consulta": item.consulta,
                        "fontes": [fonte.citacao for fonte in item.fontes_aplicaveis],
                        "parametros_calculo": (
                            item.parametros_calculo.model_dump(mode="json")
                            if item.parametros_calculo
                            else None
                        ),
                        "alcada": (
                            item.avaliacao_alcada.model_dump(mode="json")
                            if item.avaliacao_alcada
                            else None
                        ),
                    }
                    for item in avaliacoes
                ],
                ensure_ascii=False,
            ),
        )
        return {
            "avaliacoes_normativas": [item.model_dump(mode="json") for item in avaliacoes],
            "normas_documentos_revisao": estado.get("documentos_revisao", 0),
            "assinatura_itens_normativos": assinatura_itens,
            "normas_revisao": estado.get("normas_revisao", 0) + 1,
            "calculo_normativo": None,
        }

    exige_historico = _exige_historico(avaliacoes)
    historico = list(estado.get("historico_mcp", []))
    if exige_historico and estado.get("historico_status") != "sucesso":
        titular = estado.get("triagem", {}).get("carteirinha_titular")
        if not titular:
            return atualizacoes
        consulta = cliente_mcp.consultar_historico(titular)
        if not consulta.sucesso or not consulta.dados:
            return {
                "historico_status": "falha",
                "historico_consultado": False,
                "historico_tentativa_turno_id": estado.get("turno_id", ""),
            }
        historico = list(consulta.dados.get("pedidos", []))
        return {
            "historico_mcp": historico,
            "historico_consultado": True,
            "historico_status": "sucesso",
            "historico_tentativa_turno_id": estado.get("turno_id", ""),
            "normas_revisao": estado.get("normas_revisao", 0) + 1,
        }

    elegibilidade = estado.get("triagem", {}).get("elegibilidade", {})
    if elegibilidade.get("estado") != EstadoElegibilidadeEnum.ELEGIVEL.value:
        return atualizacoes
    competencias = [avaliacao.avaliacao_alcada for avaliacao in avaliacoes]
    if not competencias or any(
        competencia is None
        or competencia.exige_analista is not False
        or competencia.permite_calculo is not True
        for competencia in competencias
    ):
        return {"calculo_normas_revisao": estado.get("normas_revisao", 0)}
    documentos, pendencias_cotacao = _converter_documentos_para_brl(
        list(estado.get("documentos_validos", [])), consultar_cotacao
    )
    if pendencias_cotacao:
        return {
            "calculo_normativo": ResultadoCalculoNormativoModel(
                pendencias=pendencias_cotacao
            ).model_dump(mode="json"),
            "calculo_normas_revisao": estado.get("normas_revisao", 0),
        }
    if documentos != estado.get("documentos_validos", []):
        atualizacoes["documentos_validos"] = documentos
        documento = _documento_base({**estado, "documentos_validos": documentos})
        if documento is None:
            return atualizacoes
    totais = {
        item.data_atendimento.year: somar_reembolsos_ano(historico, item.data_atendimento.year)
        for item in documento.itens
        if item.data_atendimento
    }
    calculo = calculador(
        documento.itens,
        avaliacoes,
        totais_reembolsados_ano=totais,
    )
    atualizacoes.update(
        {
            "calculo_normativo": calculo.model_dump(mode="json"),
            "calculo_normas_revisao": estado.get("normas_revisao", 0),
        }
    )
    return atualizacoes


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


def _executar_com_telemetria(
    nome: str,
    operacao: Callable[[EstadoSupervisor], dict[str, Any]],
    estado: EstadoSupervisor,
) -> dict[str, Any]:
    """Registra a duracao de cada no mesmo quando a execucao falha."""
    inicio = perf_counter()
    sucesso = False
    try:
        resultado = operacao(estado)
        sucesso = True
        return resultado
    finally:
        LOG.info(
            "agente_no nome=%s duracao_ms=%.1f sucesso=%s turno_id=%s",
            nome,
            (perf_counter() - inicio) * 1000,
            sucesso,
            estado.get("turno_id", ""),
        )


def _compilar(
    checkpointer: InMemorySaver,
    cliente_mcp: ClienteMcp,
    extrator: ExtratorTriagem,
    gerador: GeradorResposta,
    validador_pedido_terceiro: ValidadorPedidoTerceiro,
    revisor_resposta: RevisorResposta,
    analisador: AnalisadorDocumento,
    avaliador: AvaliadorNormas,
    calculador: CalculadorNormas,
    consultar_cotacao: ConsultorCotacao,
    roteador: RoteadorSupervisor,
):
    grafo = StateGraph(EstadoSupervisor)
    grafo.add_node(
        "preparar_turno",
        lambda estado: _executar_com_telemetria("preparar_turno", preparar_turno, estado),
    )
    grafo.add_node(
        "coordenar",
        lambda estado: _executar_com_telemetria(
            "coordenar", lambda atual: coordenar(atual, roteador), estado
        ),
    )
    grafo.add_node(
        "triagem",
        lambda estado: _executar_com_telemetria(
            "triagem",
            lambda atual: executar_triagem(
                atual, extrator, cliente_mcp, validador_pedido_terceiro
            ),
            estado,
        ),
    )
    grafo.add_node(
        "documento",
        lambda estado: _executar_com_telemetria(
            "documento", lambda atual: executar_documento(atual, analisador), estado
        ),
    )
    grafo.add_node(
        "normas",
        lambda estado: _executar_com_telemetria(
            "normas",
            lambda atual: executar_normas(
                atual, avaliador, calculador, cliente_mcp, consultar_cotacao
            ),
            estado,
        ),
    )
    grafo.add_node(
        "responder",
        lambda estado: _executar_com_telemetria(
            "responder",
            lambda atual: responder_turno(
                atual, gerador, revisor_resposta, cliente_mcp
            ),
            estado,
        ),
    )
    grafo.add_edge(START, "preparar_turno")
    grafo.add_edge("preparar_turno", "coordenar")
    grafo.add_conditional_edges(
        "coordenar",
        lambda estado: estado["proxima_acao"],
        {acao.value: acao.value for acao in AcaoSupervisorEnum},
    )
    grafo.add_edge("triagem", "coordenar")
    grafo.add_edge("documento", "coordenar")
    grafo.add_edge("normas", "coordenar")
    grafo.add_edge("responder", END)
    return grafo.compile(checkpointer=checkpointer)


class Supervisor:
    """Entrada unica do coordenador, com checkpoint e exclusao mutua por sessao."""

    def __init__(
        self,
        cliente_mcp: ClienteMcp | None = None,
        extrator_triagem: ExtratorTriagem = extrair_triagem_estruturada,
        gerador_resposta: GeradorResposta = gerar_resposta_triagem,
        validador_pedido_terceiro: ValidadorPedidoTerceiro = validar_pedido_terceiro,
        revisor_resposta: RevisorResposta = revisar_resposta_beneficiario,
        analisador_documento: AnalisadorDocumento = analisar_documento,
        avaliador_normas: AvaliadorNormas | None = None,
        calculador_normas: CalculadorNormas = calcular_reembolsos_normativos,
        consultor_cotacao: ConsultorCotacao = consultar_cotacao_ptax,
        roteador: RoteadorSupervisor = escolher_proxima_acao,
        recuperador_factory: Callable[[], RetrieverHibrido] = RetrieverHibrido,
    ) -> None:
        self._lock_catalogo = RLock()
        self._locks_sessoes: dict[str, RLock] = {}
        self._cliente_mcp = cliente_mcp or ClienteMcp()
        self._extrator_triagem = extrator_triagem
        self._gerador_resposta = gerador_resposta
        self._validador_pedido_terceiro = validador_pedido_terceiro
        self._revisor_resposta = revisor_resposta
        self._analisador_documento = analisador_documento
        self._calculador_normas = calculador_normas
        self._consultor_cotacao = consultor_cotacao
        self._roteador = roteador
        self._recuperador_factory = recuperador_factory
        self._recuperador: RetrieverHibrido | None = None

        def avaliar(itens: list[ItemDocumentalModel], pergunta: str) -> list[AvaliacaoNormativaModel]:
            if self._recuperador is None:
                self._recuperador = self._recuperador_factory()
            return avaliar_normas_itens(itens, pergunta, recuperador=self._recuperador)

        self._avaliador_normas = avaliador_normas or avaliar
        self._novo_grafo()

    def _novo_grafo(self) -> None:
        self._checkpointer = InMemorySaver()
        self._grafo = _compilar(
            self._checkpointer,
            self._cliente_mcp,
            self._extrator_triagem,
            self._gerador_resposta,
            self._validador_pedido_terceiro,
            self._revisor_resposta,
            self._analisador_documento,
            self._avaliador_normas,
            self._calculador_normas,
            self._consultor_cotacao,
            self._roteador,
        )

    @contextmanager
    def _sessao_bloqueada(self, session_id: str):
        """Adquire o lock ainda sob o catalogo, fechando a corrida com o reset."""
        with self._lock_catalogo:
            lock = self._locks_sessoes.setdefault(session_id, RLock())
            lock.acquire()
        try:
            yield
        finally:
            lock.release()

    def responder(self, req: ChatRequest) -> ChatResponse:
        turno_id = uuid4().hex
        metadados = {"anexo_nome": req.anexo.filename} if req.anexo else {}
        estado_inicial: dict[str, Any] = {
            "messages": [HumanMessage(content=req.mensagem, additional_kwargs=metadados)],
            "turno_id": turno_id,
            "anexo_turno_id": turno_id if req.anexo else None,
            "anexo_recebido": req.anexo.model_dump(mode="python") if req.anexo else None,
            "pedido_id_candidato": turno_id,
        }
        config = {"configurable": {"thread_id": req.session_id}}
        with self._sessao_bloqueada(req.session_id):
            estado = self._grafo.invoke(estado_inicial, config=config)
        resposta = ChatResponse.model_validate(estado["resposta_chat"])
        if os.getenv("AGENTE_LOG_CHAT_COMPLETO", "").lower() in {"1", "true", "sim"}:
            LOG.info(
                "chat_completo=%s",
                json.dumps(
                    {
                        "session_id": req.session_id,
                        "turno_id": turno_id,
                        "beneficiario": req.mensagem,
                        "anexo": req.anexo.filename if req.anexo else None,
                        "agente": resposta.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                ),
            )
        return resposta

    def aplicar_decisao(
        self,
        session_id: str,
        decisao: Decisao,
        payload: dict[str, Any] | None = None,
    ) -> ChatResponse:
        """Mantem a entrada explicita legada com efeito de protocolo idempotente."""
        decisao = Decisao(decisao)
        config = {"configurable": {"thread_id": session_id}}
        with self._sessao_bloqueada(session_id):
            estado = self._grafo.get_state(config)
            triagem = dict(estado.values.get("triagem", {}))
            protocolo = triagem.get("protocolo")
            if decisao == Decisao.ESCALADO_ANALISTA and not protocolo:
                titular = triagem.get("carteirinha_titular")
                if titular:
                    dados = {
                        chave: valor
                        for chave, valor in (payload or {}).items()
                        if chave.lower() not in {"cpf", "cid"}
                    }
                    dados["chave_idempotencia"] = (
                        f"{estado.values.get('pedido_id', session_id)}:alcada"
                    )
                    resultado = self._cliente_mcp.abrir_protocolo(titular, dados)
                    if resultado.sucesso and resultado.dados:
                        protocolo = resultado.dados.get("protocolo")
            triagem["decisao"] = decisao.value
            if protocolo:
                triagem["protocolo"] = protocolo
            self._grafo.update_state(config, {"triagem": triagem})
            resposta = self._revisor_resposta(
                self._gerador_resposta(
                    {
                        "decisao": decisao.value,
                        "protocolo_aberto": bool(protocolo),
                        "pendencias": triagem.get("pendencias", []),
                    }
                )
            )
        return ChatResponse(
            resposta=resposta,
            decisao=decisao,
            protocolo=protocolo,
            pendencias=triagem.get("pendencias", []),
        )

    def limpar_sessoes(self) -> None:
        """Espera sessoes em curso e substitui integralmente o checkpoint em memoria."""
        with self._lock_catalogo:
            locks = list(self._locks_sessoes.values())
            for lock in locks:
                lock.acquire()
            try:
                self._novo_grafo()
                self._locks_sessoes.clear()
            finally:
                for lock in reversed(locks):
                    lock.release()
