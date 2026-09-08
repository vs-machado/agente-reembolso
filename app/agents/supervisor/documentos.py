"""Operacoes sobre os documentos acumulados no estado do supervisor."""

from __future__ import annotations

import json
from typing import Any

from app.agents.documento import FatosDocumentaisModel, ItemDocumentalModel
from app.agents.supervisor.models import ConsultorCotacao, EstadoSupervisor
from app.calculo import converter_moeda
from app.schemas import Categoria


def _ids_anexos_pendentes(estado: EstadoSupervisor) -> list[str]:
    encerrados = set(estado.get("anexos_processados", [])) | set(
        estado.get("anexos_bloqueados", [])
    )
    return [item["id"] for item in estado.get("anexos", []) if item["id"] not in encerrados]


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
