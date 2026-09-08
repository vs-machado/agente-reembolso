"""Cliente da API oficial do Gemini para a avaliação local.

Usa a mesma chave que você configura no `.env`, e o mesmo modelo que
responde ao seu agente. `temperature=0`: a nota tem de ser a mesma se a correção
for repetida.

    BOOTCAMP_API_KEY=...

Fala direto com `POST /v1beta/models/{modelo}:generateContent`, que é o formato
do Gemini.
"""

from __future__ import annotations

import json
import os
import re

import httpx

TIMEOUT = 120.0
TENTATIVAS = 3
ENDPOINT_GEMINI = "https://generativelanguage.googleapis.com"


class LLMIndisponivel(RuntimeError):
    pass


def _config() -> tuple[str, str, str]:
    chave = os.getenv("BOOTCAMP_API_KEY", "")
    modelo = os.getenv("BOOTCAMP_JUIZ_MODELO", "gemini-2.5-flash-lite")
    if not chave:
        raise LLMIndisponivel("defina BOOTCAMP_API_KEY para usar o juiz")
    return ENDPOINT_GEMINI, chave, modelo


def _extrair_json(texto: str) -> dict:
    """O modelo às vezes embrulha o JSON em cerca de código."""
    texto = texto.strip()
    if texto.startswith("```"):
        texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto, flags=re.S)
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        inicio, fim = texto.find("{"), texto.rfind("}")
        if inicio >= 0 and fim > inicio:
            return json.loads(texto[inicio:fim + 1])
        raise


def _texto_da_resposta(devolvido: dict) -> str:
    for candidato in devolvido.get("candidates") or []:
        partes = (candidato.get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in partes if isinstance(p, dict))
    return ""


def perguntar_json(sistema: str, usuario: str) -> dict:
    """Uma chamada, resposta em JSON. Determinística por construção."""
    endpoint, chave, modelo = _config()
    url = f"{endpoint}/v1beta/models/{modelo}:generateContent"
    corpo = {
        "systemInstruction": {"parts": [{"text": sistema}]},
        "contents": [{"role": "user", "parts": [{"text": usuario}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    cabecalhos = {"x-goog-api-key": chave, "content-type": "application/json"}

    ultimo = None
    for tentativa in range(TENTATIVAS):
        try:
            with httpx.Client(timeout=TIMEOUT) as c:
                r = c.post(url, json=corpo, headers=cabecalhos)
                r.raise_for_status()
                return _extrair_json(_texto_da_resposta(r.json()))
        except (httpx.HTTPError, KeyError, json.JSONDecodeError) as erro:
            ultimo = erro
            if tentativa == TENTATIVAS - 1:
                break
    raise LLMIndisponivel(f"o juiz não conseguiu resposta do modelo: {ultimo}")


def disponivel() -> bool:
    return bool(os.getenv("BOOTCAMP_API_KEY"))
