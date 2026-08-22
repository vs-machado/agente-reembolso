"""Modelos internos do agente de documentos."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field

from app.schemas import Categoria


class NaturezaProcedimentoEnum(str, Enum):
    """Natureza documental interna, sem ampliar o contrato HTTP de categorias."""

    CONSULTA = "CONSULTA"
    EXAME = "EXAME"
    TERAPIA = "TERAPIA"
    RELATORIO_CLINICO = "RELATORIO_CLINICO"
    CIRURGICO_HOSPITALAR = "CIRURGICO_HOSPITALAR"
    MATERIAL_OPME = "MATERIAL_OPME"
    OUTRA = "OUTRA"


class TextoExtraidoModel(BaseModel):
    texto: str = ""
    paginas: int = 0
    erro: str | None = None


class ClassificacaoDocumentoModel(BaseModel):
    categoria: Categoria
    natureza_medica: bool
    justificativa: str = Field(min_length=1)


class ItemDocumentalModel(BaseModel):
    """Fatos de uma despesa individual discriminada no documento."""

    categoria: Categoria
    natureza_procedimento: NaturezaProcedimentoEnum | None = None
    valor_original: Decimal | None = None
    codigo_moeda_iso: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    valor_solicitado_brl: Decimal | None = None
    data_atendimento: date | None = None
    codigo_tuss: str | None = None
    descricao_procedimento: str | None = None
    indicacao_clinica: bool = False


class EvidenciaRelatorioClinicoModel(BaseModel):
    """Campos documentais do relatorio, sem transforma-lo em despesa."""

    identificacao_beneficiario: str | None = None
    identificacao_profissional: str | None = None
    registro_conselho: str | None = None
    data_emissao: date | None = None
    periodo_acompanhamento: str | None = None
    numero_sessoes_ano: int | None = Field(default=None, ge=0)
    manutencao_tratamento: bool | None = None
    assinatura_presente: bool | None = None


class EvidenciaPedidoMedicoModel(BaseModel):
    """Fatos de uma solicitacao medica, sem transforma-la em despesa."""

    identificacao_beneficiario: str | None = None
    identificacao_profissional: str | None = None
    registro_conselho: str | None = None
    data_emissao: date | None = None
    procedimento_solicitado: str | None = None
    assinatura_presente: bool | None = None


class DadosDocumentoModel(BaseModel):
    """Fatos do documento para validar pendencias sem depender de seu layout."""

    nome_beneficiario: str | None = None
    cpf_beneficiario_presente: bool = False
    nome_prestador: str | None = None
    cpf_cnpj_prestador_presente: bool = False
    registro_conselho: str | None = None
    data_atendimento: date | None = None
    descricao_procedimento: str | None = None
    valor_total_original: Decimal | None = None
    codigo_moeda_iso: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    assinatura_ou_carimbo_presente: bool = False
    numero_sessao_ano: int | None = Field(default=None, ge=1)


class AnaliseConteudoDocumentalModel(BaseModel):
    """Classificacao e fatos extraidos em uma unica leitura pelo modelo."""

    classificacao: ClassificacaoDocumentoModel
    dados_documento: DadosDocumentoModel
    # Somente despesas entram em itens; relatorios nao podem inflar o valor solicitado.
    itens: list[ItemDocumentalModel] = Field(default_factory=list)
    # A evidencia fica separada porque complementa a analise sem constituir despesa.
    evidencia_relatorio: EvidenciaRelatorioClinicoModel | None = None
    evidencia_pedido_medico: EvidenciaPedidoMedicoModel | None = None


class FatosDocumentaisModel(BaseModel):
    """Fatos seguros para persistir e encaminhar aos demais agentes."""

    categoria: Categoria
    natureza_medica: bool
    aproveitavel: bool
    justificativa: str
    dados_documento: DadosDocumentoModel | None = None
    # Assim o calculo consome apenas despesas sem perder a evidencia clinica complementar.
    itens: list[ItemDocumentalModel] = Field(default_factory=list)
    evidencia_relatorio: EvidenciaRelatorioClinicoModel | None = None
    evidencia_pedido_medico: EvidenciaPedidoMedicoModel | None = None
    pendencias: list[str] = Field(default_factory=list)
    relatorio_complementar: bool = False

    @property
    def categoria_dominante(self) -> Categoria | None:
        """Categoria de apresentacao; cada item conserva a propria categoria."""
        if not self.itens:
            return self.categoria
        if any(
            item.valor_original is not None
            and item.codigo_moeda_iso not in {None, "BRL"}
            and item.valor_solicitado_brl is None
            for item in self.itens
        ):
            # Categorias em moedas diferentes so podem ser comparadas depois da conversao.
            return self.categoria
        totais: dict[Categoria, Decimal] = {}
        for item in self.itens:
            totais[item.categoria] = totais.get(item.categoria, Decimal()) + (
                item.valor_solicitado_brl or Decimal()
            )
        return max(totais, key=totais.__getitem__)

    @property
    def valor_solicitado_total_brl(self) -> Decimal | None:
        if any(
            item.valor_original is not None
            and item.codigo_moeda_iso not in {None, "BRL"}
            and item.valor_solicitado_brl is None
            for item in self.itens
        ):
            return None
        valores = [item.valor_solicitado_brl for item in self.itens]
        if not any(valor is not None for valor in valores):
            return None
        return sum((valor or Decimal() for valor in valores), Decimal())
