"""Nos de preparacao e execucao dos agentes especializados."""

from __future__ import annotations

from datetime import date
import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage

from app.agents.normas import AvaliacaoNormativaModel, ResultadoCalculoNormativoModel
from app.agents.supervisor.documentos import (
    _assinatura_itens_normativos,
    _converter_documentos_para_brl,
    _documento_base,
    _ids_anexos_pendentes,
)
from app.agents.supervisor.models import (
    AnalisadorDocumento,
    AvaliadorNormas,
    CalculadorNormas,
    ConsultorCotacao,
    EstadoSupervisor,
    ExtratorTriagem,
    ValidadorPedidoTerceiro,
)
from app.agents.supervisor.roteamento import _exige_historico
from app.agents.triagem import (
    DadosCadastraisModel,
    EstadoElegibilidadeEnum,
    EvidenciasNormativasModel,
    FatosDocumentaisModel as FatosTriagemModel,
    ResultadoTriagemModel,
    avaliar_elegibilidade,
    normalizar_carteirinha,
)
from app.calculo import somar_reembolsos_ano
from app.schemas import Anexo, Categoria
from app.tools import ClienteMcp

LOG = logging.getLogger("uvicorn.error")


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
        try:
            avaliacoes = avaliador(documento.itens, pergunta_normativa)
        except Exception:
            LOG.exception("normas_indisponivel turno_id=%s", estado.get("turno_id", ""))
            return {
                "calculo_normativo": ResultadoCalculoNormativoModel(
                    pendencias=["analise normativa temporariamente indisponivel"]
                ).model_dump(mode="json"),
                "calculo_normas_revisao": estado.get("normas_revisao", 0),
                "normas_falha_turno_id": estado.get("turno_id", ""),
            }
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
            "normas_falha_turno_id": "",
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
