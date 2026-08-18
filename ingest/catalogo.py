"""Extração estruturada e auditável de metadados normativos."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Literal

import fitz
from docx import Document as DocumentoDocx
from pydantic import BaseModel, Field

TipoDocumento = Literal["regulamento", "circular", "tabela", "nota_tecnica", "anexo", "manual", "faq", "desconhecido"]
StatusDocumento = Literal["vigente", "revogado", "substituido", "apoio", "apoio_desatualizado", "pendente_curadoria"]

MESES = {"janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}

# Política de precedência por tipo; menor número significa maior autoridade.
# Circulares (2) alteram pontualmente o Regulamento (3). Tabela, nota e anexo
# (4) complementam o Regulamento apenas em seus domínios. Manual e FAQ (6) são
# materiais de apoio e nunca fundamentam decisões autônomas.
AUTORIDADES = {"circular": 2, "regulamento": 3, "tabela": 4, "nota_tecnica": 4, "anexo": 4, "manual": 6, "faq": 6}


class EvidenciaModel(BaseModel):
    pagina: int = Field(ge=1)
    trecho: str


class DocumentoNormativoModel(BaseModel):
    documento_id: str
    arquivo: str
    tipo: TipoDocumento
    titulo: str
    data_publicacao: date | None = None
    vigencia_inicio: date | None = None
    vigencia_fim: date | None = None
    status: StatusDocumento
    autoridade: int | None = None
    substitui_documento_id: str | None = None
    invalida_documento_id: str | None = None
    alvos_normativos: list[str] = Field(default_factory=list)
    evidencias: list[EvidenciaModel] = Field(default_factory=list)
    pendencias: list[str] = Field(default_factory=list)


def _sem_acentos(texto: str) -> str:
    return "".join(caractere for caractere in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(caractere) != "Mn")


def _ler_paginas(caminho: Path) -> list[str]:
    if caminho.suffix.lower() == ".pdf":
        return [pagina.get_text() for pagina in fitz.open(caminho)]
    if caminho.suffix.lower() == ".docx":
        return ["\n".join(paragrafo.text for paragrafo in DocumentoDocx(caminho).paragraphs)]
    return []


def _data_por_extenso(texto: str) -> date | None:
    encontrado = re.search(r"(\d{1,2})(?:º|o)?\s+de\s+([a-zç]+)\s+de\s+(20\d{2})", _sem_acentos(texto))
    if not encontrado:
        return None
    dia, mes, ano = encontrado.groups()
    return date(int(ano), MESES[mes], int(dia)) if mes in MESES else None


def _tipo(texto: str) -> TipoDocumento:
    # O cabeçalho identifica o documento; o corpo pode citar todos os demais tipos.
    normalizado = _sem_acentos(texto[:400])
    cabecalho = _sem_acentos(texto.splitlines()[0] if texto.splitlines() else "")
    if "regulamento geral" in cabecalho:
        return "regulamento"
    if "nota tecnica" in cabecalho:
        return "nota_tecnica"
    if "tabela urs" in cabecalho:
        return "tabela"
    if "anexo" in cabecalho:
        return "anexo"
    if "circular normativa" in normalizado:
        return "circular"
    if "perguntas frequentes" in normalizado or "faq" in normalizado:
        return "faq"
    if "manual" in normalizado:
        return "manual"
    if "nota tecnica" in normalizado:
        return "nota_tecnica"
    if "anexo" in normalizado and "exclus" in normalizado:
        return "anexo"
    if "tabela urs" in normalizado:
        return "tabela"
    if "regulamento geral" in normalizado:
        return "regulamento"
    return "desconhecido"


def _titulo(texto: str, caminho: Path) -> str:
    linhas = [re.sub(r"\s+", " ", linha).strip() for linha in texto.splitlines()]
    linhas = [linha for linha in linhas if len(linha) > 4 and "pagina" not in _sem_acentos(linha) and "pag." not in _sem_acentos(linha)]
    return linhas[0].title() if linhas else caminho.stem.replace("_", " ").title()


def _identificador(tipo: TipoDocumento, texto: str, caminho: Path) -> str:
    normalizado = _sem_acentos(texto[:4000])
    if tipo == "circular":
        encontrado = re.search(r"circular normativa\s+(\d{1,2})\s*/\s*(20\d{2})", normalizado)
        if encontrado:
            return f"CIRC-{int(encontrado.group(1)):02d}-{encontrado.group(2)}"
    ano = re.search(r"exercicio\s+de\s+(20\d{2})", normalizado)
    if tipo == "regulamento" and ano:
        return f"REG-{ano.group(1)}"
    if tipo == "tabela" and ano:
        return f"TURS-{ano.group(1)}"
    if tipo == "nota_tecnica":
        numero = re.search(r"nota tecnica\s+(\d+)", normalizado)
        if numero:
            return f"NT-{int(numero.group(1)):02d}"
    if tipo == "anexo":
        numero = re.search(r"anexo\s+([ivxlcdm]+)", normalizado)
        if numero:
            return f"ANEXO-{numero.group(1).upper()}"
    return f"DOC-{sha256(caminho.name.encode()).hexdigest()[:12].upper()}"


def _datas(texto: str, tipo: TipoDocumento) -> tuple[date | None, date | None, date | None]:
    normalizado = _sem_acentos(texto)
    publicacao = _data_por_extenso(re.search(r"publicacao\s*:\s*([^\n.]+)", normalizado).group(1)) if re.search(r"publicacao\s*:\s*([^\n.]+)", normalizado) else None
    inicio = _data_por_extenso(re.search(r"inicio de vigencia\s*:\s*([^\n.]+)", normalizado).group(1)) if re.search(r"inicio de vigencia\s*:\s*([^\n.]+)", normalizado) else None
    periodo = re.search(r"entre\s+(\d{1,2}(?:º|o)?\s+de\s+\w+\s+de\s+20\d{2})\s+e\s+(\d{1,2}(?:º|o)?\s+de\s+\w+\s+de\s+20\d{2})", normalizado)
    if periodo:
        inicio, fim = _data_por_extenso(periodo.group(1)), _data_por_extenso(periodo.group(2))
        return publicacao, inicio, fim
    periodo_mesmo_ano = re.search(r"entre\s+(\d{1,2})(?:º|o)?\s+de\s+(\w+)\s+e\s+(\d{1,2})(?:º|o)?\s+de\s+(\w+)\s+de\s+(20\d{2})", normalizado)
    if periodo_mesmo_ano:
        dia_inicio, mes_inicio, dia_fim, mes_fim, ano = periodo_mesmo_ano.groups()
        return publicacao, date(int(ano), MESES[mes_inicio], int(dia_inicio)), date(int(ano), MESES[mes_fim], int(dia_fim))
    if tipo == "regulamento":
        exercicio = re.search(r"exercicio\s+de\s+(20\d{2})", normalizado)
        if exercicio:
            inicio = date(int(exercicio.group(1)), 1, 1)
    return publicacao, inicio, None


def _alvos(texto: str, tipo: TipoDocumento) -> list[str]:
    if tipo != "circular":
        return []
    alvos: set[str] = set()
    normalizado = _sem_acentos(texto)
    for trecho in re.findall(r"nova redacao aos?\s+arts?\.?(\s*[\d, e]+)", normalizado):
        alvos.update(f"ART-{numero}" for numero in re.findall(r"\d+", trecho))
    for ocorrencia in re.finditer(r"passa a vigorar", normalizado):
        artigos = re.findall(r"art\.\s*(\d+)", normalizado[max(0, ocorrencia.start() - 220):ocorrencia.start()])
        if artigos:
            alvos.add(f"ART-{artigos[-1]}")
    alvos.update(f"ART-{numero}" for numero in re.findall(r"restabelecida\s+a\s+redacao\s+original\s+do\s+art\.\s*(\d+)", normalizado))
    alvos.update(f"TUSS-{codigo}" for codigo in re.findall(r"codigo\s+tuss\s*-?\s*(\d+)", normalizado))
    return sorted(alvos)


def extrair_documento(caminho: Path) -> DocumentoNormativoModel:
    paginas = _ler_paginas(caminho)
    texto = "\n".join(paginas)
    tipo = _tipo(texto)
    publicacao, inicio, fim = _datas(texto, tipo)
    normalizado = _sem_acentos(texto)
    if tipo == "faq":
        status: StatusDocumento = "apoio_desatualizado" if "desatualiz" in normalizado else "apoio"
    elif tipo == "manual":
        status = "apoio"
    elif tipo == "desconhecido":
        status = "pendente_curadoria"
    else:
        status = "vigente" if inicio else "pendente_curadoria"
    pendencias = []
    if tipo == "desconhecido":
        pendencias.append("tipo_documento_nao_identificado")
    if status == "pendente_curadoria" and inicio is None:
        pendencias.append("vigencia_inicio_nao_identificada")
    evidencia = EvidenciaModel(pagina=1, trecho=re.sub(r"\s+", " ", texto[:500]).strip()) if texto else None
    return DocumentoNormativoModel(
        documento_id=_identificador(tipo, texto, caminho), arquivo=caminho.name, tipo=tipo,
        titulo=_titulo(texto, caminho), data_publicacao=publicacao, vigencia_inicio=inicio,
        vigencia_fim=fim, status=status, autoridade=AUTORIDADES.get(tipo),
        alvos_normativos=_alvos(texto, tipo), evidencias=[evidencia] if evidencia else [], pendencias=pendencias,
    )


def extrair_catalogo(diretorio: Path) -> list[DocumentoNormativoModel]:
    documentos = [extrair_documento(caminho) for caminho in sorted(diretorio.iterdir()) if caminho.suffix.lower() in {".pdf", ".docx"}]
    por_circular = {documento.documento_id: documento for documento in documentos if documento.tipo == "circular"}
    for documento in list(por_circular.values()):
        texto = _sem_acentos("\n".join(_ler_paginas(diretorio / documento.arquivo)))
        for numero, ano in re.findall(r"circular(?: normativa)?\s+(\d{1,2})\s*/\s*(20\d{2})", texto):
            alvo_id = f"CIRC-{int(numero):02d}-{ano}"
            if alvo_id == documento.documento_id or alvo_id not in por_circular:
                continue
            alvo = por_circular[alvo_id]
            if re.search(rf"revoga[^.]*circular(?: normativa)?\s+0?{int(numero)}\s*/\s*{ano}", texto):
                documento.invalida_documento_id = alvo_id
                alvo.status = "revogado"
            elif "redacao dada pela circular" in texto or "prevalece sobre ela" in texto:
                documento.substitui_documento_id = alvo_id
                alvo.status = "substituido"
            if alvo.status in {"revogado", "substituido"} and documento.vigencia_inicio:
                alvo.vigencia_fim = documento.vigencia_inicio - timedelta(days=1)
    return documentos
