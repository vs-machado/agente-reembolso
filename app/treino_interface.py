"""Transmite os casos reais de treino para a interface local."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from threading import Event, Thread
from uuid import uuid4

from avaliacao.avaliar import avaliar, nota_final, salvar
from avaliacao.conversar import conduzir
from avaliacao.dispositivos import existentes
from avaliacao.llm import LLMIndisponivel, disponivel

RAIZ = Path(__file__).resolve().parent.parent
LOG = logging.getLogger("uvicorn.error")


def executar_treino(caso: str):
    def emitir(tipo: str, **dados) -> str:
        return json.dumps({"tipo": tipo, **dados}, ensure_ascii=False) + "\n"

    if not disponivel():
        yield emitir("erro", mensagem="Configure BOOTCAMP_API_KEY para executar o treino.")
        return

    pastas = sorted(p for p in (RAIZ / "casos_treino").iterdir()
                    if p.is_dir() and (p / "esperado.json").exists())
    if caso != "todos":
        pastas = [p for p in pastas if p.name.startswith(caso)]
    vereditos = []
    sem_avaliacao = 0
    cancelado = Event()
    destino = (RAIZ / "relatorios" / "avaliacoes" /
               f"avaliacao-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}-{uuid4().hex}.json")
    try:
        dispositivos = existentes(RAIZ / "kb")
        for pasta in pastas:
            gabarito = json.loads((pasta / "esperado.json").read_text(encoding="utf-8"))
            yield emitir("caso", nome=pasta.name, titulo=gabarito.get("_titulo", pasta.name))
            fila = Queue()

            def enviar_turno(turno):
                if cancelado.is_set():
                    raise RuntimeError("A interface foi fechada.")
                fila.put(("turno", turno))

            def conduzir_caso():
                try:
                    transcricao = conduzir(
                        pasta, "http://127.0.0.1:8000", sessao=f"ui-{uuid4().hex}",
                        ao_turno=enviar_turno, limpar=False,
                    )
                    fila.put(("fim", transcricao))
                except Exception as erro:
                    fila.put(("falha", erro))

            Thread(target=conduzir_caso, daemon=True).start()
            while True:
                tipo, valor = fila.get()
                if tipo == "turno":
                    yield emitir("turno", **valor)
                elif tipo == "falha":
                    raise valor
                else:
                    transcricao = valor
                    break

            try:
                veredito = avaliar(pasta.name, transcricao, gabarito, dispositivos, usar_modelo=True)
            except LLMIndisponivel:
                sem_avaliacao += 1
                yield emitir("avaliacao_indisponivel", conversa=pasta.name)
                continue
            vereditos.append(veredito)
            salvar(vereditos, destino)
            yield emitir("avaliacao", **veredito.como_dict())
        nota = nota_final(vereditos) if vereditos else None
        LOG.info("treino_nota_final caso=%s nota=%s conversas=%d sem_avaliacao=%d relatorio=%s",
                 caso, nota if nota is not None else "indisponivel", len(vereditos),
                 sem_avaliacao, destino if vereditos else "indisponivel")
        yield emitir("fim", nota=nota,
                    conversas=len(vereditos), sem_avaliacao=sem_avaliacao)
    except Exception as erro:
        yield emitir("erro", mensagem=f"Treino interrompido: {type(erro).__name__}: {erro}")
    finally:
        cancelado.set()
