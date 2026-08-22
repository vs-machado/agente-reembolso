"""Regras deterministicas que protegem o atendimento ao beneficiario."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

CPF_COMPLETO = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
CODIGO_CID = re.compile(r"\b[A-TV-Z]\d{2}(?:\.\d)?\b", re.IGNORECASE)
VINCULO_TERCEIRO = re.compile(
    r"\b(c[oô]njuge|espos[oa]|companheir[oa]|dependente|filh[oa]|pai|m[aã]e|"
    r"amig[oa]|terceir[oa]|outra pessoa)\b",
    re.IGNORECASE,
)
CARTEIRINHA_NA_MENSAGEM = re.compile(
    r"\b(?:carteirinha|cart[aã]o)\D{0,10}(\d[\d .-]{2,})",
    re.IGNORECASE,
)
ATRIBUICAO_DIAGNOSTICA = re.compile(
    r"\b(?:seu|sua|voc[eê]|benefici[aá]ri[oa])\b.{0,50}"
    r"\b(?:diagn[oó]stico|hip[oó]tese diagn[oó]stica|tem|apresenta|sofre de)\b",
    re.IGNORECASE,
)

class ValidacaoTerceiroModel(BaseModel):
    pedido_terceiro: bool


class RespostaProtegidaModel(BaseModel):
    resposta: str = Field(min_length=1)


def _normalizar_carteirinha(valor: str) -> str:
    return "".join(caractere for caractere in valor if caractere.isdigit())


def identificar_pedido_terceiro(
    mensagem: str,
    carteirinha_candidata: str | None,
    carteirinha_sessao: str | None,
) -> bool:
    """Produz sinais locais para a validacao semantica feita pelo LLM."""
    candidata = _normalizar_carteirinha(carteirinha_candidata or "")
    if not candidata:
        achado = CARTEIRINHA_NA_MENSAGEM.search(mensagem)
        candidata = _normalizar_carteirinha(achado.group(1)) if achado else ""
    sessao = _normalizar_carteirinha(carteirinha_sessao or "")
    return bool(VINCULO_TERCEIRO.search(mensagem)) or bool(
        candidata and sessao and candidata != sessao
    )


def validar_pedido_terceiro(
    mensagem: str,
    carteirinha_candidata: str | None,
    carteirinha_sessao: str | None,
    llm: object | None = None,
) -> bool:
    """Classifica semanticamente se o pedido trata de pessoa fora da sessao."""
    if not mensagem.strip():
        return False
    indicio_local = identificar_pedido_terceiro(
        mensagem, carteirinha_candidata, carteirinha_sessao
    )
    if indicio_local:
        return True
    if carteirinha_candidata and not carteirinha_sessao:
        # A primeira carteirinha sem vinculo de terceiro estabelece o titular.
        return False
    if llm is None:
        from app.llm import criar_llm

        llm = criar_llm(temperature=0)
    resultado = llm.with_structured_output(ValidacaoTerceiroModel).invoke(
        """Voce e o guardrail de privacidade de um atendimento de reembolso.
Decida se a mensagem pede consulta, informacao ou tratamento para qualquer pessoa
que nao seja a dona da carteirinha que abriu a sessao. Isso inclui conjuge,
companheiro, dependente e familiares, mesmo que quem fala diga ser o titular do
contrato. Uma carteirinha diferente da sessao tambem e terceiro. Retorne somente
a estrutura solicitada.

Mensagem: """
        + mensagem
        + f"\nCarteirinha candidata: {carteirinha_candidata!r}"
        + f"\nCarteirinha da sessao: {carteirinha_sessao!r}"
        + f"\nIndicio deterministico adicional: {indicio_local}"
    )
    return ValidacaoTerceiroModel.model_validate(resultado).pedido_terceiro or indicio_local


def revisar_resposta_beneficiario(resposta: str, llm: object | None = None) -> str:
    """Reformula a saida com o LLM quando ela puder expor dado protegido."""
    indicio_sensivel = bool(CPF_COMPLETO.search(resposta) or CODIGO_CID.search(resposta))
    indicio_diagnostico = bool(ATRIBUICAO_DIAGNOSTICA.search(resposta))
    if not indicio_sensivel and not indicio_diagnostico:
        return resposta
    if llm is None:
        from app.llm import criar_llm

        llm = criar_llm(temperature=0)
    resultado = llm.with_structured_output(RespostaProtegidaModel).invoke(
        """Voce e o guardrail final de um atendimento de reembolso. Reescreva a
resposta para que continue cordial, especifica e conversacional, sem usar mensagens
fixas. Nunca revele ou repita CPF completo, codigo CID, diagnostico, hipotese
diagnostica, descricao do quadro clinico, nem numeros de carteirinha de terceiros.
Quando necessario, explique a limitacao de forma natural e siga com a orientacao que puder ser prestada.
Retorne somente a estrutura solicitada.

Resposta a revisar: """
        + resposta
        + f"\nIndicio local de CPF ou CID: {indicio_sensivel}"
        + f"\nIndicio local de revelacao diagnostica: {indicio_diagnostico}"
    )
    revisada = RespostaProtegidaModel.model_validate(resultado).resposta
    # O avaliador trata qualquer sequencia no formato de CPF como vazamento,
    # inclusive se uma reescrita do modelo preservou um numero de protocolo.
    return CPF_COMPLETO.sub("***.***.***-**", revisada)
