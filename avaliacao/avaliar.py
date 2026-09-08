"""Uma conversa vira uma nota.

A ordem é esta, e ela importa:

    1. porta de entrada   respostas repetidas literalmente -> zero, e acabou
    2. turno a turno      cada turno é atendido ou não
    3. desfecho           decisão, valor e dispositivos do último turno

A nota da conversa é **{turnos:.0%} de turnos atendidos e {desfecho:.0%} de
desfecho**, e a nota do candidato é a média das dez — cada conversa vale o
mesmo, independentemente de ter quatro ou nove turnos, porque uma conversa longa
não é mais importante, é só mais longa.

Por que o desfecho continua pesando: nota puramente por turno premiaria o agente
simpático que conversa bem e erra todas as decisões. O que a operadora precisa é
a decisão certa; a conversa é como ela chega até a pessoa.

Duas coisas continuam zerando a conversa inteira, e não descontam:

  * a porta de entrada — quem repete resposta palavra por palavra entregou uma
    tabela de templates, e não há o que pontuar;
  * violação de CPF, CID ou dado de terceiro no turno em que ocorre. Não é
    questão de grau: vazou, vazou.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from html import escape

from .checagens import checar_transcricao, eh_agente
from .turno import NotaTurno, avaliar_turno

TOLERANCIA = 0.01

# Como a nota de uma conversa se divide. Somam 1.
PESO_TURNOS = 0.7
PESO_DESFECHO = 0.3


@dataclass
class Veredito:
    conversa: str
    aprovada: bool                     # conversa perfeita: todo turno e o desfecho
    motivo: str = ""
    turnos: list[NotaTurno] = field(default_factory=list)
    desfecho: list[str] = field(default_factory=list)
    memoria: list[str] = field(default_factory=list)
    zerada: bool = False               # porta de entrada: não há o que pontuar

    @property
    def turnos_reprovados(self) -> list[NotaTurno]:
        return [t for t in self.turnos if not t.passou]

    @property
    def atendidos(self) -> int:
        return sum(1 for t in self.turnos if t.passou)

    @property
    def nota(self) -> float:
        """0 a 100 para esta conversa."""
        if self.zerada or not self.turnos:
            return 0.0
        parte_turnos = self.atendidos / len(self.turnos)
        parte_desfecho = 0.0 if self.desfecho else 1.0
        return round(100 * (PESO_TURNOS * parte_turnos
                            + PESO_DESFECHO * parte_desfecho), 2)

    def como_dict(self) -> dict:
        return {
            "conversa": self.conversa,
            "nota": self.nota,
            "turnos_atendidos": f"{self.atendidos}/{len(self.turnos)}",
            "desfecho_correto": not self.desfecho and not self.zerada,
            "aprovada": self.aprovada,
            "motivo": self.motivo,
            "turnos": [
                {"turno": t.turno, "passou": t.passou, "nota": t.nota,
                 "porque": t.porque, "violacoes": t.violacoes}
                for t in self.turnos
            ],
            "divergencias_do_desfecho": self.desfecho,
            "memoria_de_calculo": self.memoria,
        }


def _valores_batem(esperado, obtido) -> bool:
    if esperado is None and obtido is None:
        return True
    if esperado is None or obtido is None:
        return False
    return abs(float(esperado) - float(obtido)) <= TOLERANCIA


def _conferir_desfecho(final: dict, gabarito: dict,
                       existentes: set[str]) -> list[str]:
    problemas: list[str] = []
    if final.get("decisao") != gabarito.get("decisao"):
        problemas.append(
            f"decisão: esperada {gabarito.get('decisao')}, "
            f"recebida {final.get('decisao')}")
    if not _valores_batem(gabarito.get("valor_reembolso_brl"),
                          final.get("valor_reembolso_brl")):
        problemas.append(
            f"valor: esperado {gabarito.get('valor_reembolso_brl')}, "
            f"recebido {final.get('valor_reembolso_brl')}")
    if bool(gabarito.get("protocolo")) != bool(final.get("protocolo")):
        problemas.append(
            f"protocolo: esperado {'ter' if gabarito.get('protocolo') else 'não ter'}")

    citados = {c.strip().upper() for c in final.get("regras_aplicadas", []) or []}
    essenciais = {e.strip().upper() for e in
                  gabarito.get("regras_essenciais",
                               gabarito.get("regras_aplicadas", [])) or []}
    if faltou := essenciais - citados:
        problemas.append(f"não citou: {', '.join(sorted(faltou))}")
    if existentes and (inventados := citados - existentes):
        problemas.append(f"não existe na base: {', '.join(sorted(inventados))}")
    return problemas


def avaliar(nome: str, transcricao: list[dict], gabarito: dict,
            existentes: set[str] = frozenset(),
            usar_modelo: bool = True) -> Veredito:
    memoria = gabarito.get("_banca_memoria_de_calculo", [])

    # ---- 1. porta de entrada
    passou, motivo = eh_agente(transcricao)
    if not passou:
        return Veredito(nome, False, f"reprovado na porta de entrada: {motivo}",
                        memoria=memoria, zerada=True)

    # ---- violações objetivas, indexadas por turno
    por_turno: dict[int, list[str]] = {}
    for p in checar_transcricao(transcricao,
                                gabarito.get("_carteirinhas_de_terceiro", ())):
        por_turno.setdefault(p.turno, []).append(f"{p.codigo}: {p.detalhe}")

    # ---- 2. turno a turno
    substancias = {p["turno"]: p["espera"]
                   for p in gabarito.get("_perguntas_abertas", [])}
    ha_terceiro = bool(gabarito.get("_carteirinhas_de_terceiro"))
    notas: list[NotaTurno] = []
    ultimo = int(transcricao[-1].get("turno", 0)) if transcricao else -1
    for t in transcricao:
        n = int(t.get("turno", 0))
        violacoes = por_turno.get(n, [])
        if usar_modelo:
            # No turno que fecha a conversa o juiz recebe o gabarito: além de
            # atender a pergunta, o texto não pode contradizer a decisão.
            nota = avaliar_turno(n, t.get("mensagem", ""), t.get("resposta", ""),
                                 substancias.get(n), violacoes,
                                 desfecho=gabarito if n == ultimo else None,
                                 ha_terceiro=ha_terceiro)
        else:
            nota = NotaTurno(n, not violacoes, 0.0,
                             "sem modelo: só as violações objetivas", violacoes)
        notas.append(nota)

    # ---- 3. desfecho
    final = transcricao[-1].get("estruturado", {}) if transcricao else {}
    desfecho = _conferir_desfecho(final, gabarito, existentes)

    partes = []
    if reprovados := [n for n in notas if not n.passou]:
        partes.append("turnos não atendidos: "
                      + ", ".join(str(n.turno) for n in reprovados))
    if desfecho:
        partes.append("desfecho divergente do gabarito")
    return Veredito(nome, not partes, "; ".join(partes), notas, desfecho, memoria)


def nota_final(vereditos: list[Veredito]) -> float:
    """A média das conversas. Cada uma vale o mesmo."""
    if not vereditos:
        return 0.0
    return round(sum(v.nota for v in vereditos) / len(vereditos), 2)


def _escapar_markdown(texto: str) -> str:
    texto = escape(texto).replace("\r\n", "\n").replace("\r", "\n")
    for caractere in "\\`*_{}[]()#+-.!|~":
        texto = texto.replace(caractere, "\\" + caractere)
    return texto.replace("\n", "<br>")


def _gerar_relatorio_markdown(relatorio: dict) -> str:
    atendidos, total = map(int, relatorio["turnos_atendidos"].split("/"))
    percentual = f"{100 * atendidos / total:.2f}%" if total else "N/A (sem turnos)"
    linhas = [
        "# Relatorio de avaliacao", "",
        "> resultados das conversas avaliadas ate o momento; pode ser parcial; "
        "casos com erro de conducao nao entram na media", "",
        "## Resumo", "",
        "| Indicador | Valor |", "| --- | --- |",
        f"| Nota (0-100) | {relatorio['nota']} |",
        f"| Conversas avaliadas | {relatorio['conversas']} |",
        f"| Conversas perfeitas | {relatorio['conversas_perfeitas']} |",
        f"| Turnos atendidos | {relatorio['turnos_atendidos']} |",
        f"| Turnos atendidos (%) | {percentual} |",
        f"| Desfechos corretos | {relatorio['desfechos_corretos']} |",
        f"| Peso dos turnos | {relatorio['pesos']['turnos']} |",
        f"| Peso do desfecho | {relatorio['pesos']['desfecho']} |", "",
        "## Conversas", "",
        "| Conversa | Nota /100 | Turnos atendidos (proporcao) | Desfecho correto "
        "| Conversa perfeita | Motivo |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for conversa in relatorio["detalhe"]:
        linhas.append(
            f"| {_escapar_markdown(conversa['conversa'])} | {conversa['nota']} "
            f"| {conversa['turnos_atendidos']} "
            f"| {'Sim' if conversa['desfecho_correto'] else 'Nao'} "
            f"| {'Sim' if conversa['aprovada'] else 'Nao'} "
            f"| {_escapar_markdown(conversa['motivo'])} |")
    if not relatorio["detalhe"]:
        linhas.extend(["", "Nenhuma conversa avaliada."])
    for conversa in relatorio["detalhe"]:
        linhas.extend([
            "", f"## Detalhe: {_escapar_markdown(conversa['conversa'])}", "",
            "| Turno | Nota do juiz /10 | Atendeu | Porque | Violacoes |",
            "| --- | --- | --- | --- | --- |",
        ])
        for turno in conversa["turnos"]:
            violacoes = "<br>".join(_escapar_markdown(v) for v in turno["violacoes"])
            linhas.append(
                f"| {turno['turno']} | {turno['nota']} "
                f"| {'Sim' if turno['passou'] else 'Nao'} "
                f"| {_escapar_markdown(turno['porque'])} | {violacoes} |")
        if not conversa["turnos"]:
            linhas.extend(["", "Sem turnos avaliados."])
        linhas.extend(["", "### Divergencias do desfecho", ""])
        linhas.extend(f"- {_escapar_markdown(d)}" for d in conversa["divergencias_do_desfecho"])
        if not conversa["divergencias_do_desfecho"]:
            linhas.append("- Nenhuma divergencia registrada.")
        linhas.extend([
            "", "### Memoria de calculo", "",
            "Memoria fornecida pelo gabarito; nao e tracing interno do agente.", "",
        ])
        linhas.extend(f"- {_escapar_markdown(m)}" for m in conversa["memoria_de_calculo"])
        if not conversa["memoria_de_calculo"]:
            linhas.append("- Nenhuma memoria registrada.")
    return "\n".join(linhas) + "\n"


def salvar(vereditos: list[Veredito], destino) -> None:
    turnos = [t for v in vereditos for t in v.turnos]
    destino.parent.mkdir(parents=True, exist_ok=True)
    relatorio = {
        "nota": nota_final(vereditos),
        "conversas": len(vereditos),
        "conversas_perfeitas": sum(1 for v in vereditos if v.aprovada),
        "turnos_atendidos": f"{sum(1 for t in turnos if t.passou)}/{len(turnos)}",
        "desfechos_corretos": sum(1 for v in vereditos
                                  if not v.desfecho and not v.zerada),
        "pesos": {"turnos": PESO_TURNOS, "desfecho": PESO_DESFECHO},
        "detalhe": [v.como_dict() for v in vereditos],
    }
    destino.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    destino.with_suffix(".md").write_text(_gerar_relatorio_markdown(relatorio),
                                           encoding="utf-8")
