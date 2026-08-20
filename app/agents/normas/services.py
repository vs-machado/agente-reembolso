"""Recuperacao e avaliacao normativa limitada a fatos e fontes rastreaveis."""

from __future__ import annotations

import logging
from datetime import date

from app.agents.documento.models import FatosDocumentaisModel
from app.agents.normas.models import (
    AvaliacaoNormativaModel,
    FonteAfastadaModel,
    FonteNormativaModel,
    LeituraNormativaModel,
    ResultadoCalculoNormativoModel,
)
from app.rag import RetrieverHibrido
from app.schemas import Categoria

LOG = logging.getLogger(__name__)


def formular_consulta_normativa(fatos: FatosDocumentaisModel, pergunta: str) -> str | None:
    """Formula consulta somente com fatos aproveitaveis do documento."""
    if fatos.categoria == Categoria.INVALIDO or not fatos.aproveitavel or not fatos.natureza_medica:
        return None
    partes = [f"categoria {fatos.categoria.value}"]
    if fatos.descricao_procedimento:
        partes.append(f"procedimento {fatos.descricao_procedimento}")
    if fatos.codigo_tuss:
        partes.append(f"codigo TUSS {fatos.codigo_tuss}")
    if fatos.indicacao_clinica:
        partes.append("indicacao clinica expressa")
    if pergunta.strip():
        partes.append(f"pergunta do beneficiario {pergunta.strip()}")
    return "; ".join(partes)


def avaliar_normas_item(
    fatos: FatosDocumentaisModel,
    pergunta: str,
    *,
    recuperador: RetrieverHibrido,
    llm: object | None = None,
) -> AvaliacaoNormativaModel:
    """Recupera e interpreta fontes sem permitir conclusoes fora da base."""
    consulta = formular_consulta_normativa(fatos, pergunta)
    if consulta is None:
        return AvaliacaoNormativaModel(
            consulta="fatos documentais insuficientes para consulta normativa",
            pendencias=list(fatos.pendencias) or ["validacao documental"],
            justificativa="O anexo nao fornece fatos aproveitaveis para o RAG.",
        )
    if fatos.data_atendimento is None:
        return AvaliacaoNormativaModel(
            consulta=consulta,
            pendencias=list(dict.fromkeys([*fatos.pendencias, "data do atendimento"])),
            justificativa="A data-fato e obrigatoria para verificar a vigencia normativa.",
        )

    recuperadas = [
        FonteNormativaModel(
            texto=fonte.texto,
            citacao=fonte.citacao,
            metadados=fonte.metadados,
            score=fonte.score,
            origens=fonte.origens,
            score_llm=fonte.score_llm,
        )
        for fonte in recuperador.recuperar(
            consulta,
            data_atendimento=fatos.data_atendimento,
            validar_relevancia=True,
        )
    ]
    if not recuperadas:
        return AvaliacaoNormativaModel(
            consulta=consulta,
            data_fato=fatos.data_atendimento,
            pendencias=["fundamentacao normativa"],
            justificativa="Nao foi encontrada fonte material vigente para os fatos documentais.",
        )
    leitura = _ler_fontes(consulta, fatos.data_atendimento, recuperadas, llm)
    aplicaveis, afastadas = _separar_fontes(recuperadas, leitura)
    avaliacao = AvaliacaoNormativaModel(
        consulta=consulta,
        data_fato=fatos.data_atendimento,
        fontes_recuperadas=recuperadas,
        fontes_aplicaveis=aplicaveis,
        fontes_afastadas=afastadas,
        ha_fonte_suficiente=bool(aplicaveis),
        vigencia_confirmada=bool(aplicaveis),
        ha_conflito_material=leitura.ha_conflito_material,
        regras_aplicaveis=leitura.regras_aplicaveis,
        parametros_calculo=leitura.parametros_calculo,
        resultado_elegibilidade=None if leitura.ha_conflito_material else leitura.resultado_elegibilidade,
        justificativa=leitura.justificativa,
    )
    if avaliacao.ha_conflito_material:
        # Registra somente identificadores rastreaveis, sem dados do beneficiario.
        LOG.warning(
            "conflito_normativo consulta=%r data_fato=%s citacoes=%s motivo=%r",
            consulta,
            fatos.data_atendimento.isoformat(),
            [fonte.citacao for fonte in aplicaveis],
            leitura.justificativa,
        )
    return avaliacao


def avaliar_normas_itens(
    fatos_itens: list[FatosDocumentaisModel],
    pergunta: str,
    *,
    recuperador: RetrieverHibrido,
    llm: object | None = None,
) -> list[AvaliacaoNormativaModel]:
    """Avalia cada item preservando categoria, data-fato e evidencia proprias."""
    return [
        avaliar_normas_item(fatos, pergunta, recuperador=recuperador, llm=llm)
        for fatos in fatos_itens
        if fatos.categoria != Categoria.INVALIDO
    ]


def calcular_reembolsos_normativos(
    fatos_itens: list[FatosDocumentaisModel],
    avaliacoes: list[AvaliacaoNormativaModel],
    *,
    totais_reembolsados_ano: dict[int, object] | None = None,
) -> ResultadoCalculoNormativoModel:
    """Calcula itens elegiveis na ordem recebida, sem exceder saldo anual comum."""
    from decimal import Decimal

    from app.calculo import calcular_total_reembolso, executar_calculo_normativo

    if len(fatos_itens) != len(avaliacoes):
        raise ValueError("fatos e avaliacoes devem conter a mesma quantidade de itens")
    acumulados = {
        ano: Decimal(str(valor))
        for ano, valor in (totais_reembolsados_ano or {}).items()
    }
    valores: list[Decimal] = []
    pendencias: list[str] = []
    for fatos, avaliacao in zip(fatos_itens, avaliacoes, strict=True):
        parametros = avaliacao.parametros_calculo
        if fatos.valor_solicitado_brl is None or fatos.data_atendimento is None or parametros is None:
            pendencias.append("parametros insuficientes para calculo normativo")
            continue
        ano = fatos.data_atendimento.year
        total_anterior = acumulados.get(ano)
        valor = executar_calculo_normativo(
            fatos.valor_solicitado_brl,
            parametros,
            total_reembolsado_ano=total_anterior,
        )
        if valor is None:
            pendencias.append("parametros insuficientes para calculo normativo")
            continue
        valores.append(valor)
        if parametros.exige_limite_anual:
            acumulados[ano] = (total_anterior or Decimal("0")) + valor
    return ResultadoCalculoNormativoModel(
        valores_itens_brl=valores,
        valor_reembolso_brl=calcular_total_reembolso(valores) if len(valores) == len(fatos_itens) else None,
        total_reembolsado_ano_brl=sum(acumulados.values(), Decimal("0")) if acumulados else None,
        pendencias=list(dict.fromkeys(pendencias)),
    )


def _ler_fontes(
    consulta: str,
    data_fato: date,
    fontes: list[FonteNormativaModel],
    llm: object | None,
) -> LeituraNormativaModel:
    if llm is None:
        from app.llm import criar_llm

        llm = criar_llm(temperature=0)
    candidatos = "\n\n".join(
        f"FONTE {indice}\nCitacao: {fonte.citacao}\nTrecho: {fonte.texto}"
        for indice, fonte in enumerate(fontes, start=1)
    )
    resultado = llm.with_structured_output(LeituraNormativaModel).invoke(
        "Avalie a elegibilidade somente com as fontes fornecidas. Nao invente "
        "regras, valores, dispositivos ou criterios de desempate. Indique apenas "
        "indices existentes. Extraia parametros_calculo apenas quando o valor e "
        "o dispositivo estiverem explicitamente nas fontes. Quando fontes materiais conflitarem, marque "
        "ha_conflito_material=true e resultado_elegibilidade=null.\n\n"
        f"DATA-FATO: {data_fato.isoformat()}\nCONSULTA: {consulta}\n\n{candidatos}"
    )
    return LeituraNormativaModel.model_validate(resultado)


def _separar_fontes(
    fontes: list[FonteNormativaModel],
    leitura: LeituraNormativaModel,
) -> tuple[list[FonteNormativaModel], list[FonteAfastadaModel]]:
    indices_aplicaveis = {indice for indice in leitura.indices_aplicaveis if 1 <= indice <= len(fontes)}
    aplicaveis = [fonte for indice, fonte in enumerate(fontes, start=1) if indice in indices_aplicaveis]
    afastadas = [
        FonteAfastadaModel(
            **fonte.model_dump(),
            motivo_afastamento=leitura.motivos_afastamento.get(indice, "Sem relacao material com a consulta."),
        )
        for indice, fonte in enumerate(fontes, start=1)
        if indice not in indices_aplicaveis
    ]
    return aplicaveis, afastadas
