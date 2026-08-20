"""Recuperacao e avaliacao normativa limitada a fatos e fontes rastreaveis."""

from __future__ import annotations

import logging
from datetime import date
from itertools import zip_longest

from app.agents.documento.models import ItemDocumentalModel
from app.agents.normas.models import (
    AvaliacaoAlcadaModel,
    AvaliacaoNormativaModel,
    FonteAfastadaModel,
    FonteNormativaModel,
    LeituraNormativaModel,
    ResultadoCalculoNormativoModel,
)
from app.rag import RetrieverHibrido
from app.schemas import Categoria

LOG = logging.getLogger(__name__)


def formular_consulta_normativa(item: ItemDocumentalModel, pergunta: str) -> str | None:
    """Formula consulta somente com fatos aproveitaveis do documento."""
    if item.categoria == Categoria.INVALIDO:
        return None
    partes = [f"categoria {item.categoria.value}"]
    if item.descricao_procedimento:
        partes.append(f"procedimento {item.descricao_procedimento}")
    if item.codigo_tuss:
        partes.append(f"codigo TUSS {item.codigo_tuss}")
    if item.indicacao_clinica:
        partes.append("indicacao clinica expressa")
    if pergunta.strip():
        partes.append(f"pergunta do beneficiario {pergunta.strip()}")
    partes.append(
        "regras vigentes de cobertura, carencia, teto em URS, valor da URS, "
        "coparticipacao por plano e faixa de adesao, limite anual, ordem do calculo, "
        "competencia da analise automatizada e alcada; considerar circulares que "
        "alterem os artigos aplicaveis"
    )
    return "; ".join(partes)


def formular_consultas_normativas(item: ItemDocumentalModel, pergunta: str) -> list[str] | None:
    """Divide a recuperacao por assunto para preservar fontes complementares."""
    if item.categoria == Categoria.INVALIDO:
        return None
    fatos_procedimento = [f"categoria {item.categoria.value}"]
    if item.descricao_procedimento:
        fatos_procedimento.append(f"procedimento {item.descricao_procedimento}")
    if item.codigo_tuss:
        fatos_procedimento.append(f"codigo TUSS {item.codigo_tuss}")
    if item.indicacao_clinica:
        fatos_procedimento.append("indicacao clinica expressa")
    contexto_procedimento = "; ".join(fatos_procedimento)
    contexto_cadastral = pergunta.strip() or "dados cadastrais do beneficiario"
    return [
        f"{contexto_procedimento}; cobertura do procedimento, codigo TUSS na Tabela URS, carencia, teto em URS, exigencias documentais e relatorio clinico",
        f"{contexto_cadastral}; conversao do teto em URS para reais, apuracao do valor a reembolsar, ordem de apuracao, coparticipacao por plano e faixa de adesao, circular vigente e arredondamento",
        f"{contexto_cadastral}; limite anual de reembolso em URS e reais, saldo anual, deducao de reembolsos anteriores e necessidade de historico de pedidos",
        f"{contexto_procedimento}; competencia da analise automatizada, alcada decisoria, limites em reais, encaminhamento obrigatorio a analista humano e vedacao de calculo para OPME",
    ]


def _fundir_fontes_recuperadas(
    fontes_por_consulta: list[list[FonteNormativaModel]],
    limite: int = 10,
) -> list[FonteNormativaModel]:
    """Intercala rankings focados e conserva uma unica fonte por citacao."""
    fontes: dict[str, FonteNormativaModel] = {}
    for grupo in zip_longest(*fontes_por_consulta):
        for fonte in grupo:
            if fonte is None:
                continue
            existente = fontes.get(fonte.citacao)
            if existente is None:
                fontes[fonte.citacao] = fonte
                continue
            fontes[fonte.citacao] = existente.model_copy(
                update={
                    "score": max(existente.score, fonte.score),
                    "origens": tuple(sorted(set(existente.origens) | set(fonte.origens))),
                    "score_llm": max(valor for valor in (existente.score_llm, fonte.score_llm) if valor is not None)
                    if existente.score_llm is not None or fonte.score_llm is not None
                    else None,
                }
            )
    return list(fontes.values())[:limite]


def avaliar_normas_item(
    item: ItemDocumentalModel,
    pergunta: str,
    *,
    recuperador: RetrieverHibrido,
    llm: object | None = None,
) -> AvaliacaoNormativaModel:
    """Recupera e interpreta fontes sem permitir conclusoes fora da base."""
    consultas = formular_consultas_normativas(item, pergunta)
    if consultas is None:
        return AvaliacaoNormativaModel(
            consulta="fatos documentais insuficientes para consulta normativa",
            pendencias=["validacao documental"],
            justificativa="O anexo nao fornece fatos aproveitaveis para o RAG.",
        )
    if item.data_atendimento is None:
        return AvaliacaoNormativaModel(
            consulta="\n".join(consultas),
            pendencias=["data do atendimento"],
            justificativa="A data-fato e obrigatoria para verificar a vigencia normativa.",
        )

    grupos = [
        [
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
                data_atendimento=item.data_atendimento,
                limite=10,
            )
        ]
        for consulta in consultas
    ]
    grupos.append(
        [
            FonteNormativaModel(
                texto=fonte.texto,
                citacao=fonte.citacao,
                metadados=fonte.metadados,
                score=fonte.score,
                origens=fonte.origens,
                score_llm=fonte.score_llm,
            )
            for fonte in recuperador.recuperar_chunks_circulares_vigentes(
                item.data_atendimento,
                limite=6,
            )
        ]
    )
    recuperadas = _fundir_fontes_recuperadas(grupos)
    consulta = "\n".join(consultas)
    if not recuperadas:
        return AvaliacaoNormativaModel(
            consulta=consulta,
            data_fato=item.data_atendimento,
            pendencias=["fundamentacao normativa"],
            justificativa="Nao foi encontrada fonte material vigente para os fatos documentais.",
        )
    leitura = _ler_fontes(consulta, item.data_atendimento, recuperadas, llm)
    aplicaveis, afastadas = _separar_fontes(recuperadas, leitura)
    avaliacao_alcada = _validar_avaliacao_alcada(leitura, recuperadas)
    classificacao, indices_classificacao, just_classificacao = _validar_classificacao_decisao(leitura, recuperadas)
    avaliacao = AvaliacaoNormativaModel(
        consulta=consulta,
        data_fato=item.data_atendimento,
        fontes_recuperadas=recuperadas,
        fontes_aplicaveis=aplicaveis,
        fontes_afastadas=afastadas,
        ha_fonte_suficiente=bool(aplicaveis),
        vigencia_confirmada=bool(aplicaveis),
        ha_conflito_material=leitura.ha_conflito_material,
        regras_aplicaveis=leitura.regras_aplicaveis,
        parametros_calculo=leitura.parametros_calculo,
        avaliacao_alcada=avaliacao_alcada,
        classificacao_decisao=classificacao,
        indices_fonte_classificacao=indices_classificacao,
        justificativa_classificacao=just_classificacao,
        resultado_elegibilidade=None if leitura.ha_conflito_material else leitura.resultado_elegibilidade,
        justificativa=leitura.justificativa,
    )
    if avaliacao.ha_conflito_material:
        # Registra somente identificadores rastreaveis, sem dados do beneficiario.
        LOG.warning(
            "conflito_normativo consulta=%r data_fato=%s citacoes=%s motivo=%r",
            consulta,
            item.data_atendimento.isoformat(),
            [fonte.citacao for fonte in aplicaveis],
            leitura.justificativa,
        )
    return avaliacao


def avaliar_normas_itens(
    itens: list[ItemDocumentalModel],
    pergunta: str,
    *,
    recuperador: RetrieverHibrido,
    llm: object | None = None,
) -> list[AvaliacaoNormativaModel]:
    """Avalia cada item preservando categoria, data-fato e evidencia proprias."""
    return [
        avaliar_normas_item(item, pergunta, recuperador=recuperador, llm=llm)
        for item in itens
        if item.categoria != Categoria.INVALIDO
    ]


def calcular_reembolsos_normativos(
    itens: list[ItemDocumentalModel],
    avaliacoes: list[AvaliacaoNormativaModel],
    *,
    totais_reembolsados_ano: dict[int, object] | None = None,
) -> ResultadoCalculoNormativoModel:
    """Calcula itens elegiveis na ordem recebida, sem exceder saldo anual comum."""
    from decimal import Decimal

    from app.calculo import calcular_total_reembolso, executar_calculo_normativo

    if len(itens) != len(avaliacoes):
        raise ValueError("fatos e avaliacoes devem conter a mesma quantidade de itens")
    acumulados = {
        ano: Decimal(str(valor))
        for ano, valor in (totais_reembolsados_ano or {}).items()
    }
    valores: list[Decimal] = []
    pendencias: list[str] = []
    limitado_por_saldo = False
    for item, avaliacao in zip(itens, avaliacoes, strict=True):
        parametros = avaliacao.parametros_calculo
        if item.valor_solicitado_brl is None or item.data_atendimento is None or parametros is None:
            pendencias.append("parametros insuficientes para calculo normativo")
            continue
        ano = item.data_atendimento.year
        total_anterior = acumulados.get(ano)
        valor = executar_calculo_normativo(
            item.valor_solicitado_brl,
            parametros,
            total_reembolsado_ano=total_anterior,
        )
        if valor is None:
            pendencias.append("parametros insuficientes para calculo normativo")
            continue
        valores.append(valor)
        if parametros.exige_limite_anual and parametros.limite_anual_brl is not None and total_anterior is not None:
            saldo = max(Decimal("0"), parametros.limite_anual_brl - total_anterior)
            base = min(item.valor_solicitado_brl, (parametros.teto_urs or Decimal("0")) * (parametros.valor_urs_brl or Decimal("0")))
            apos_copart = base * (Decimal("1") - (parametros.coparticipacao_percentual or Decimal("0")) / Decimal("100"))
            if saldo < apos_copart:
                limitado_por_saldo = True
        if parametros.exige_limite_anual:
            acumulados[ano] = (total_anterior or Decimal("0")) + valor
    return ResultadoCalculoNormativoModel(
        valores_itens_brl=valores,
        valor_reembolso_brl=calcular_total_reembolso(valores) if len(valores) == len(itens) else None,
        total_reembolsado_ano_brl=sum(acumulados.values(), Decimal("0")) if acumulados else None,
        limitado_por_saldo_anual=limitado_por_saldo,
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
        f"FONTE {indice}\nCitação: {fonte.citacao}\nVigência: {fonte.metadados.get('vigencia_inicio', 'indeterminada')}\nTrecho: {fonte.texto}"
        for indice, fonte in enumerate(fontes, start=1)
    )
    resultado = llm.with_structured_output(LeituraNormativaModel).invoke(
        "Avalie elegibilidade, calculo e competencia decisoria estritamente com base nas fontes fornecidas em CANDIDATOS. Nao invente "
        "regras, valores, dispositivos ou criterios de desempate. "
        "Em `indices_aplicaveis`, liste os numeros inteiros (ex: [1, 2, 4]) de todas as FONTEs fornecidas que sao materialmente aplicaveis e vigentes para a analise. "
        "Quando uma fonte posterior em vigencia alterar ou der nova redacao a dispositivo de fonte mais antiga, "
        "prevalece a fonte posterior para fatos ocorridos apos o seu inicio de vigencia (isso nao configura conflito material). "
        "Para extrair coparticipacao_percentual: calcule o tempo de adesao em meses completos decorridos entre data_adesao e a data do atendimento informadas na CONSULTA e obtenha o percentual na tabela de coparticipacao da fonte vigente mais recente correspondente ao plano e a essa faixa de adesao. "
        "Extraia teto_urs e valor_urs_brl conforme a Tabela URS e fontes vigentes. Quando houver limite anual em URS, multiplique pelo valor da URS para preencher limite_anual_brl e marque exige_limite_anual=true. "
        "Classifique a decisao em `classificacao_decisao` ('APROVADO' ou 'APROVADO_PARCIAL') e cite em "
        "`indices_fonte_classificacao` os numeros inteiros das fontes vigentes que sustentam essa classificacao: "
        "`APROVADO` quando o pedido atender aos requisitos e nenhuma norma determinar deferimento parcial; "
        "`APROVADO_PARCIAL` somente quando norma expressa previr deferimento parcial (como quando o valor for limitado pelo saldo anual). Reducoes previstas em norma "
        "(como coparticipacao contratual ou teto do procedimento) nao tornam o deferimento parcial. "
        "Em `regras_aplicaveis`, liste exclusivamente os identificadores normativos normalizados (no formato "
        "`ART-{numero}`, `TUSS-{codigo}`, `CIRC-{numero}-{ano}`, `ANEXO-{numero/letra}`, `NT-{numero}`) de todos os "
        "dispositivos e regras das fontes vigentes que compõem a fundamentação normativa integral da decisão. "
        "Cadeia normativa obrigatória a incluir em `regras_aplicaveis` a partir das fontes fornecidas: "
        "1) O código TUSS do procedimento presente na Tabela URS e o artigo do Regulamento ou Circular que fixa sua cobertura e teto (ex: ART-35 para consultas, ART-41 e CIRC-02-2026 para terapias); "
        "2) Todos os artigos do Regulamento Geral que fundamentam o cálculo do reembolso: fórmula de conversão de teto URS em reais (ART-33), regra e ordem de apuração (ART-43), coparticipação por plano e tempo de adesão (ART-44), limite anual quando aplicável (ART-45) e regra de arredondamento (ART-47); "
        "3) Normas documentais e exigências de relatório clínico para terapias (ART-73), quando aplicável; "
        "4) Normas de competência decisória e alçada (ART-78), quando aplicável. "
        "ATENÇÃO CRÍTICA: Em `regras_aplicaveis`, inclua SOMENTE dispositivos, artigos, circulares e códigos TUSS que constem explicitamente no texto das fontes vigentes fornecidas em CANDIDATOS. NUNCA inclua códigos TUSS do documento do usuário que não existam nas fontes normativas. "
        "Não inclua 'FONTE-X', nomes por extenso nem texto livre. "
        "Em `avaliacao_alcada`, avalie se a analise automatizada pode decidir com base estrita nas regras de competencia "
        "das fontes fornecidas, citando em `indices_fontes` os numeros inteiros das fontes correspondentes. Identifique `item_sob_analise` somente quando "
        "previsto expressamente nas fontes. Quando o pedido for de material, órtese, prótese ou OPME, ou exceder o limite de alçada, marque exige_analista=true e permite_calculo=false com base no ART-78. Quando nao houver fonte suficiente, deixe `exige_analista` e `permite_calculo` nulos. "
        "Quando fontes materiais vigentes conflitarem, marque ha_conflito_material=true e resultado_elegibilidade=null.\n\n"
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


def _validar_avaliacao_alcada(
    leitura: LeituraNormativaModel,
    fontes_recuperadas: list[FonteNormativaModel],
) -> AvaliacaoAlcadaModel | None:
    """Aceita conclusao de alcada apenas quando ela referencia fonte aplicavel."""
    alcada = leitura.avaliacao_alcada
    if alcada is None:
        return None
    indices_aplicaveis = {
        indice
        for indice in leitura.indices_aplicaveis
        if 1 <= indice <= len(fontes_recuperadas)
    }
    indices = sorted(
        {
            indice
            for indice in alcada.indices_fontes
            if indice in indices_aplicaveis
        }
    )
    if leitura.ha_conflito_material or not indices:
        pendencias = list(alcada.pendencias)
        pendencias.append("fundamentacao normativa da alcada")
        return AvaliacaoAlcadaModel(
            exige_analista=None,
            permite_calculo=None,
            item_sob_analise=alcada.item_sob_analise,
            justificativa=alcada.justificativa,
            regras_aplicaveis=alcada.regras_aplicaveis,
            indices_fontes=indices,
            pendencias=list(dict.fromkeys(pendencias)),
        )
    return alcada.model_copy(update={"indices_fontes": indices})


def _validar_classificacao_decisao(
    leitura: LeituraNormativaModel,
    fontes_recuperadas: list[FonteNormativaModel],
) -> tuple[str | None, list[int], str]:
    """Aceita classificacao de decisao apenas quando ela referencia fonte aplicavel."""
    classificacao = leitura.classificacao_decisao
    if classificacao is None:
        return None, [], leitura.justificativa_classificacao
    indices_aplicaveis = {
        indice
        for indice in leitura.indices_aplicaveis
        if 1 <= indice <= len(fontes_recuperadas)
    }
    indices = sorted(
        {
            indice
            for indice in leitura.indices_fonte_classificacao
            if indice in indices_aplicaveis
        }
    )
    if leitura.ha_conflito_material or not indices:
        return None, indices, leitura.justificativa_classificacao
    return classificacao, indices, leitura.justificativa_classificacao
