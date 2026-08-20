"""Extracao, classificacao e validacao de anexos documentais."""

from __future__ import annotations

import base64
import binascii
import io
import re
import unicodedata
from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation

import fitz
import pytesseract
from docx import Document
from PIL import Image

from app.agents.documento.models import (
    ClassificacaoDocumentoModel,
    FatosDocumentaisModel,
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


def _texto_normalizado(texto: str) -> str:
    sem_acentos = unicodedata.normalize("NFD", texto.casefold())
    sem_acentos = "".join(caractere for caractere in sem_acentos if not unicodedata.combining(caractere))
    return " ".join(sem_acentos.split())


ClassificadorDocumento = Callable[[str], ClassificacaoDocumentoModel]


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


def _valor(texto: str) -> Decimal | None:
    ocorrencias = re.findall(r"R\$\s*([\d.]+,\d{2})", texto)
    if not ocorrencias:
        return None
    try:
        return Decimal(ocorrencias[-1].replace(".", "").replace(",", "."))
    except InvalidOperation:
        return None


def _data_atendimento(texto: str) -> date | None:
    ocorrencia = re.search(r"data do atendimento\s*:\s*(\d{2}/\d{2}/\d{4})", texto, re.I)
    if not ocorrencia:
        return None
    try:
        return date.fromisoformat("-".join(reversed(ocorrencia.group(1).split("/"))))
    except ValueError:
        return None


def _pendencias_fiscais(texto: str, categoria: Categoria, titular: str | None) -> list[str]:
    normalizado = _texto_normalizado(texto)
    pendencias: list[str] = []
    nome = re.search(r"(?:recebi de|paciente)\s*:\s*([^\n]+)", texto, re.I)
    if not nome:
        pendencias.append("P01")
    elif titular and _texto_normalizado(nome.group(1)) != _texto_normalizado(titular):
        pendencias.append("P03")
    if not re.search(r"cpf do paciente\s*:\s*\d", texto, re.I):
        pendencias.append("P02")
    if not re.search(r"(?:profissional|prestador)\s*:\s*[^\n]+", texto, re.I):
        pendencias.append("P04")
    if not re.search(r"cpf/cnpj\s*:\s*\d", texto, re.I):
        pendencias.append("P05")
    if not re.search(r"(?:crm|crp|cro|crefito)[-\s]?[a-z]*\s*\d", normalizado, re.I):
        pendencias.append("P06")
    if not _data_atendimento(texto):
        pendencias.append("P09")
    if not re.search(r"(?:referente a|descricao|descrição)\s*:\s*[^\n]+", texto, re.I):
        pendencias.append("P13")
    if _valor(texto) is None:
        pendencias.append("P15")
    if not re.search(r"_{5,}|assinatura|carimbo", texto, re.I):
        pendencias.append("P18")
    if categoria == Categoria.SESSAO_TERAPIA and not re.search(r"sess[aã]o n[ºo].*?:\s*\d+", texto, re.I):
        pendencias.append("P19")
    return pendencias


def _pendencias_relatorio(texto: str, titular: str | None) -> list[str]:
    pendencias: list[str] = []
    nome = re.search(r"paciente\s*:\s*([^\n]+)", texto, re.I)
    if not nome:
        pendencias.append("P01")
    elif titular and _texto_normalizado(nome.group(1)) != _texto_normalizado(titular):
        pendencias.append("P03")
    if not re.search(r"(?:crm|crp|cro|crefito)[-\s]?[a-z]*\s*\d", texto, re.I):
        pendencias.append("P06")
    if not re.search(r"data de emiss[aã]o\s*:\s*\d{2}/\d{2}/\d{4}", texto, re.I):
        pendencias.append("P09")
    if not re.search(r"manuten[cç][aã]o do tratamento", texto, re.I):
        pendencias.append("P21")
    if not re.search(r"_{5,}|assinatura", texto, re.I):
        pendencias.append("P22")
    return pendencias


def analisar_documento(
    anexo: Anexo,
    *,
    nome_titular: str | None = None,
    ha_pedido_pendente: bool = False,
    classificador: ClassificadorDocumento | None = None,
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
    classificacao = (classificador or classificar_documento)(extraido.texto)
    categoria = classificacao.categoria
    if categoria == Categoria.INVALIDO:
        # Anexos sem natureza assistencial ficam fora das etapas normativa e decisoria.
        return FatosDocumentaisModel(
            categoria=categoria,
            natureza_medica=False,
            aproveitavel=False,
            justificativa=classificacao.justificativa,
        )
    if categoria == Categoria.RELATORIO_CLINICO:
        # Relatorios complementares seguem os requisitos proprios da NT-02.
        pendencias = _pendencias_relatorio(extraido.texto, nome_titular)
    else:
        # Documentos fiscais usam os campos obrigatorios e condicionais da NT-02.
        pendencias = _pendencias_fiscais(extraido.texto, categoria, nome_titular)
    tuss = re.search(r"c[oó]digo tuss\s*:\s*(\d+)", extraido.texto, re.I)
    descricao = re.search(r"(?:referente a|descri[cç][aã]o)\s*:\s*([^\n]+)", extraido.texto, re.I)
    return FatosDocumentaisModel(
        categoria=categoria,
        natureza_medica=classificacao.natureza_medica,
        # Uma pendencia bloqueia a conclusao documental, mas preserva o pedido para complemento.
        aproveitavel=not pendencias,
        justificativa=classificacao.justificativa,
        valor_solicitado_brl=None if categoria == Categoria.RELATORIO_CLINICO else _valor(extraido.texto),
        data_atendimento=None if categoria == Categoria.RELATORIO_CLINICO else _data_atendimento(extraido.texto),
        codigo_tuss=tuss.group(1) if tuss else None,
        descricao_procedimento=descricao.group(1).strip() if descricao else None,
        indicacao_clinica="indicacao clinica" in _texto_normalizado(extraido.texto),
        pendencias=pendencias,
        relatorio_complementar=categoria == Categoria.RELATORIO_CLINICO and ha_pedido_pendente,
    )
