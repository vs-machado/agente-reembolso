"""Extracao, classificacao e validacao de anexos documentais."""

from __future__ import annotations

import base64
import binascii
import io
import unicodedata
from collections.abc import Callable

import fitz
import pytesseract
from docx import Document
from PIL import Image

from app.agents.documento.models import (
    AnaliseConteudoDocumentalModel,
    ClassificacaoDocumentoModel,
    DadosDocumentoModel,
    EvidenciaRelatorioClinicoModel,
    FatosDocumentaisModel,
    ItemDocumentalModel,
    TextoExtraidoModel,
)
from app.schemas import Anexo, Categoria

LIMITE_BYTES = 10 * 1024 * 1024
LIMITE_PAGINAS = 20


def extrair_texto(anexo: Anexo) -> TextoExtraidoModel:
    """Extrai texto com limites locais, sem enviar o arquivo inteiro ao modelo."""
    try:
        conteudo = base64.b64decode(anexo.base64, validate=True)
    except (binascii.Error, ValueError):
        return TextoExtraidoModel(erro="arquivo em base64 invalido")
    if not conteudo:
        return TextoExtraidoModel(erro="arquivo vazio")
    if len(conteudo) > LIMITE_BYTES:
        return TextoExtraidoModel(erro="arquivo excede o limite permitido")

    mime = anexo.mime_type.lower()
    try:
        if mime == "application/pdf":
            with fitz.open(stream=conteudo, filetype="pdf") as documento:
                if len(documento) > LIMITE_PAGINAS:
                    return TextoExtraidoModel(erro="arquivo excede o limite de paginas")
                texto = "\n".join(pagina.get_text() for pagina in documento).strip()
                return TextoExtraidoModel(texto=texto, paginas=len(documento))
        if mime in {"image/jpeg", "image/png"}:
            imagem = Image.open(io.BytesIO(conteudo))
            texto = pytesseract.image_to_string(imagem, lang="por").strip()
            return TextoExtraidoModel(texto=texto, paginas=1)
        if mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            documento = Document(io.BytesIO(conteudo))
            texto = "\n".join(paragrafo.text for paragrafo in documento.paragraphs).strip()
            return TextoExtraidoModel(texto=texto, paginas=1)
    except Exception:
        return TextoExtraidoModel(erro="arquivo corrompido ou ilegivel")
    return TextoExtraidoModel(erro="tipo de arquivo nao suportado")


ClassificadorDocumento = Callable[[str], ClassificacaoDocumentoModel]
AnalisadorConteudoDocumental = Callable[[str], AnaliseConteudoDocumentalModel]


def classificar_documento(texto: str, llm: object | None = None) -> ClassificacaoDocumentoModel:
    """Classifica o conteudo integral com saida fechada no enum do contrato."""
    if llm is None:
        from app.llm import criar_llm

        llm = criar_llm(temperature=0)
    modelo = llm.with_structured_output(ClassificacaoDocumentoModel)
    resultado = modelo.invoke(
        """Classifique este documento para um pedido de reembolso medico.
        Escolha exclusivamente uma categoria disponivel no schema. Marque
        `INVALIDO` e `natureza_medica=false` somente se o arquivo nao for um
        documento de despesa assistencial ou uma evidencia clinica. Para uma
        despesa assistencial cuja cobertura ainda nao esteja definida, use
        `DESPESA_NAO_COBERTA` apenas quando essa natureza estiver clara: a
        cobertura sera avaliada por outro agente. Avalie o documento integral,
        nao palavras ou valores isolados, e nao invente fatos.
        Retorne somente a estrutura solicitada.

        Documento:
        """
        + texto
    )
    return ClassificacaoDocumentoModel.model_validate(resultado)


def _normalizar_nome(nome: str) -> str:
    normalizado = unicodedata.normalize("NFD", nome.casefold())
    sem_acentos = "".join(
        caractere for caractere in normalizado if not unicodedata.combining(caractere)
    )
    return " ".join(sem_acentos.split())


def _pendencias_fiscais(
    dados: DadosDocumentoModel,
    categoria: Categoria,
    titular: str | None,
) -> list[str]:
    pendencias: list[str] = []
    if not dados.nome_beneficiario:
        pendencias.append("P01")
    elif titular and _normalizar_nome(dados.nome_beneficiario) != _normalizar_nome(titular):
        pendencias.append("P03")
    if not dados.cpf_beneficiario_presente:
        pendencias.append("P02")
    if not dados.nome_prestador:
        pendencias.append("P04")
    if not dados.cpf_cnpj_prestador_presente:
        pendencias.append("P05")
    if not dados.registro_conselho:
        pendencias.append("P06")
    if not dados.data_atendimento:
        pendencias.append("P09")
    if not dados.descricao_procedimento:
        pendencias.append("P13")
    if dados.valor_total_original is None:
        pendencias.append("P15")
    if not dados.assinatura_ou_carimbo_presente:
        pendencias.append("P18")
    if categoria == Categoria.SESSAO_TERAPIA and dados.numero_sessao_ano is None:
        pendencias.append("P19")
    return pendencias


def _pendencias_relatorio(
    dados: DadosDocumentoModel,
    evidencia: EvidenciaRelatorioClinicoModel | None,
    titular: str | None,
) -> list[str]:
    pendencias: list[str] = []
    if not dados.nome_beneficiario:
        pendencias.append("P01")
    elif titular and _normalizar_nome(dados.nome_beneficiario) != _normalizar_nome(titular):
        pendencias.append("P03")
    if not dados.registro_conselho:
        pendencias.append("P06")
    if not evidencia or not evidencia.data_emissao:
        pendencias.append("P09")
    if not evidencia or evidencia.manutencao_tratamento is not True:
        pendencias.append("P21")
    if not evidencia or evidencia.assinatura_presente is not True:
        pendencias.append("P22")
    return pendencias


def analisar_conteudo_documental(
    texto: str,
    llm: object | None = None,
) -> AnaliseConteudoDocumentalModel:
    """Classifica e extrai itens com uma unica invocacao do modelo."""
    if llm is None:
        from app.llm import criar_llm

        llm = criar_llm(temperature=0)
    modelo = llm.with_structured_output(AnaliseConteudoDocumentalModel)
    resultado = modelo.invoke(
        """Analise este documento para um pedido de reembolso medico.
        Classifique-o exclusivamente em uma categoria disponivel no schema.
        Marque `INVALIDO` e `natureza_medica=false` somente se o arquivo nao for
        documento de despesa assistencial nem evidencia clinica. Nao confunda
        campo obrigatorio ausente com documento invalido.

        Para documento fiscal valido, extraia somente procedimentos ou sessoes
        com valor individualizado. Preserve a categoria propria de cada item,
        inclusive quando houver categorias diferentes no mesmo documento. Nao
        divida valor global, nao estime valores e nao invente datas, codigos ou
        indicacoes clinicas. Para cada valor, preserve `valor_original` e o
        `codigo_moeda_iso`. Preencha `valor_solicitado_brl` somente quando a
        moeda indicada no documento for BRL; nunca converta moeda por estimativa.
        Retorne `itens` vazio para documento invalido, relatorio clinico ou valor
        global sem discriminacao.

        Preencha `dados_documento` com os fatos do documento, independentemente
        dos rotulos ou layout usados: nome do beneficiario e do prestador,
        presenca de CPF do beneficiario e CPF/CNPJ do prestador sem reproduzir
        esses numeros, registro profissional, data do atendimento, descricao,
        valor total, moeda, assinatura ou carimbo e numero da sessao no ano.
        Use null ou false quando o fato nao estiver efetivamente presente.

        Para relatorio clinico, preencha `evidencia_relatorio` somente com os
        campos efetivamente presentes: identificacoes, registro profissional,
        data de emissao, periodo de acompanhamento, numero de sessoes no ano,
        indicacao de manutencao e assinatura. Esses fatos sao evidencia
        complementar e nao item de despesa. Para as demais categorias, retorne
        `evidencia_relatorio=null`. A cobertura sera avaliada por outro agente.
        Retorne somente a estrutura solicitada.

        Documento:
        """
        + texto
    )
    return AnaliseConteudoDocumentalModel.model_validate(resultado)


def analisar_documento(
    anexo: Anexo,
    *,
    nome_titular: str | None = None,
    ha_pedido_pendente: bool = False,
    analisador: AnalisadorConteudoDocumental | None = None,
) -> FatosDocumentaisModel:
    """Produz fatos tipados; anexos invalidos nao seguem para decisao normativa."""
    extraido = extrair_texto(anexo)
    if extraido.erro or not extraido.texto:
        # Falhas tecnicas do anexo nao devem interromper a sessao nem gerar inferencias.
        return FatosDocumentaisModel(
            categoria=Categoria.INVALIDO,
            natureza_medica=False,
            aproveitavel=False,
            justificativa=extraido.erro or "nao foi possivel ler o arquivo",
        )
    analise = (analisador or analisar_conteudo_documental)(extraido.texto)
    classificacao = analise.classificacao
    categoria = classificacao.categoria
    if categoria == Categoria.INVALIDO:
        # Anexos sem natureza assistencial ficam fora das etapas normativa e decisoria.
        return FatosDocumentaisModel(
            categoria=Categoria.INVALIDO,
            natureza_medica=False,
            aproveitavel=False,
            justificativa=classificacao.justificativa,
        )
    if categoria == Categoria.RELATORIO_CLINICO:
        # Relatorios complementares seguem os requisitos proprios da NT-02.
        pendencias = _pendencias_relatorio(
            analise.dados_documento,
            analise.evidencia_relatorio,
            nome_titular,
        )
    else:
        # Documentos fiscais usam os campos obrigatorios e condicionais da NT-02.
        pendencias = _pendencias_fiscais(analise.dados_documento, categoria, nome_titular)
    # Mesmo que o LLM misture os campos, relatorio permanece evidencia e nunca despesa.
    return FatosDocumentaisModel(
        categoria=categoria,
        natureza_medica=classificacao.natureza_medica,
        # Uma pendencia bloqueia a conclusao documental, mas preserva o pedido para complemento.
        aproveitavel=not pendencias,
        justificativa=classificacao.justificativa,
        dados_documento=analise.dados_documento,
        itens=[] if categoria == Categoria.RELATORIO_CLINICO else analise.itens,
        evidencia_relatorio=analise.evidencia_relatorio
        if categoria == Categoria.RELATORIO_CLINICO
        else None,
        pendencias=pendencias,
        relatorio_complementar=categoria == Categoria.RELATORIO_CLINICO and ha_pedido_pendente,
    )
