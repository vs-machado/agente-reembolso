"""Ferramentas — cliente do servidor MCP.

Três ferramentas, por HTTP streamable, em `MCP_OPERADORA_URL`:

    consultar_beneficiario(carteirinha)   plano, adesão, sessões, situação
    consultar_historico(carteirinha)      pedidos de reembolso anteriores
    abrir_protocolo(carteirinha, payload) número de protocolo

O token de `MCP_OPERADORA_TOKEN` vai no header `Authorization`; sem ele o
servidor responde 401.

Dois detalhes que costumam derrubar cliente:

  * erro de ferramenta no MCP volta como `isError` no resultado, e **não** como
    exceção de transporte. Quem não olha esse campo trata "beneficiário não
    localizado" como se fosse resposta válida;
  * o formato do retorno pode mudar durante a prova. Escreva de forma que um
    campo com outra forma não derrube o fluxo inteiro.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.tools.cotacao import CotacaoPtaxModel, consultar_cotacao_ptax


class ErroMcp(RuntimeError):
    """Falha de transporte ou de execucao de ferramenta no MCP."""


@dataclass(frozen=True)
class ResultadoMcp:
    sucesso: bool
    dados: dict[str, Any] | None = None
    erro: str | None = None


class ClienteMcp:
    """Cliente sincrono pequeno para as ferramentas autorizadas da operadora."""

    def __init__(self, url: str | None = None, token: str | None = None) -> None:
        self.url = url or os.getenv("MCP_OPERADORA_URL", "http://localhost:9000/mcp")
        self.token = token if token is not None else os.getenv("MCP_OPERADORA_TOKEN", "")

    def consultar_beneficiario(self, carteirinha: str) -> ResultadoMcp:
        return self._chamar("consultar_beneficiario", {"carteirinha": carteirinha})

    def abrir_protocolo(self, carteirinha: str, payload: dict[str, Any]) -> ResultadoMcp:
        return self._chamar(
            "abrir_protocolo", {"carteirinha": carteirinha, "payload": payload}
        )

    def consultar_historico(self, carteirinha: str) -> ResultadoMcp:
        return self._chamar("consultar_historico", {"carteirinha": carteirinha})

    def _chamar(self, nome: str, argumentos: dict[str, Any]) -> ResultadoMcp:
        try:
            return asyncio.run(self._chamar_async(nome, argumentos))
        except Exception as erro:  # transporte pode falhar antes de haver resultado MCP
            return ResultadoMcp(False, erro=f"falha no MCP: {erro}")

    async def _chamar_async(self, nome: str, argumentos: dict[str, Any]) -> ResultadoMcp:
        cabecalhos = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        async with httpx.AsyncClient(headers=cabecalhos, timeout=30) as cliente_http:
            async with streamable_http_client(self.url, http_client=cliente_http) as canais:
                leitura, escrita, _ = canais
                async with ClientSession(leitura, escrita) as sessao:
                    await sessao.initialize()
                    resultado = await sessao.call_tool(
                        nome,
                        argumentos,
                        read_timeout_seconds=timedelta(seconds=30),
                    )
        if resultado.isError:
            return ResultadoMcp(False, erro=_texto_resultado(resultado))
        dados = _dados_resultado(resultado)
        if not isinstance(dados, dict):
            return ResultadoMcp(False, erro="retorno do MCP nao e um objeto")
        return ResultadoMcp(True, dados=dados)


def _texto_resultado(resultado: Any) -> str:
    partes = []
    for conteudo in getattr(resultado, "content", []) or []:
        texto = getattr(conteudo, "text", None)
        if texto:
            partes.append(texto)
    return " ".join(partes) or "erro de ferramenta no MCP"


def _dados_resultado(resultado: Any) -> Any:
    estruturado = getattr(resultado, "structuredContent", None)
    if estruturado is not None:
        return estruturado
    for conteudo in getattr(resultado, "content", []) or []:
        texto = getattr(conteudo, "text", None)
        if texto:
            try:
                return json.loads(texto)
            except json.JSONDecodeError:
                continue
    return None


__all__ = [
    "ClienteMcp",
    "CotacaoPtaxModel",
    "ErroMcp",
    "ResultadoMcp",
    "consultar_cotacao_ptax",
]
