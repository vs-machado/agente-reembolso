import io
import json
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import rodar_treino
from avaliacao.avaliar import Veredito, _escapar_markdown, salvar
from avaliacao.turno import NotaTurno


class PersistenciaTreinoTest(unittest.TestCase):
    def setUp(self):
        self.raiz = Path(self.enterContext(TemporaryDirectory()))
        for nome in ("01-caso", "02-caso"):
            pasta = self.raiz / "casos_treino" / nome
            pasta.mkdir(parents=True)
            (pasta / "esperado.json").write_text("{}", encoding="utf-8")
        self.enterContext(patch.object(rodar_treino, "RAIZ", self.raiz))
        self.enterContext(patch.object(rodar_treino, "_carregar_env"))
        self.enterContext(patch("sys.argv", ["rodar_treino.py"]))
        self.disponivel = self.enterContext(
            patch.object(rodar_treino, "disponivel", return_value=True))
        self.saude = self.enterContext(patch.object(rodar_treino, "esperar_saude"))
        self.existentes = self.enterContext(
            patch.object(rodar_treino, "existentes", return_value=set()))
        self.conduzir = self.enterContext(
            patch.object(rodar_treino, "conduzir", return_value=[]))
        self.aprovado = Veredito(
            "01-caso", True,
            turnos=[NotaTurno(1, True, 10.0, "atendeu", [])])
        self.reprovado = Veredito(
            "02-caso", False, "turno e desfecho divergentes",
            turnos=[NotaTurno(1, False, 2.0, "nao atendeu", ["violacao teste"])],
            desfecho=["valor divergente"], memoria=["100 * 0.5 = 50"])
        self.avaliar = self.enterContext(
            patch.object(rodar_treino, "avaliar", return_value=self.aprovado))
        self.saida = self.enterContext(redirect_stdout(io.StringIO()))

    def ler_relatorio(self):
        arquivos = list((self.raiz / "relatorios" / "avaliacoes").glob("*.json"))
        self.assertEqual(len(arquivos), 1)
        self.assertIn(str(arquivos[0]), self.saida.getvalue())
        destino_md = arquivos[0].with_suffix(".md")
        self.assertEqual(list(destino_md.parent.glob("*.md")), [destino_md])
        self.assertIn(str(destino_md), self.saida.getvalue())
        texto_json = arquivos[0].read_text(encoding="utf-8")
        relatorio = json.loads(texto_json)
        self.assertEqual(texto_json, json.dumps(relatorio, ensure_ascii=False, indent=2) + "\n")
        markdown = destino_md.read_text(encoding="utf-8")
        self.assertEqual(markdown.count("# Relatorio de avaliacao\n"), 1)
        self.assertIn(
            "resultados das conversas avaliadas ate o momento; pode ser parcial; "
            "casos com erro de conducao nao entram na media", markdown)
        for rotulo, valor in (
            ("Nota (0-100)", relatorio["nota"]),
            ("Conversas avaliadas", relatorio["conversas"]),
            ("Conversas perfeitas", relatorio["conversas_perfeitas"]),
            ("Turnos atendidos", relatorio["turnos_atendidos"]),
            ("Desfechos corretos", relatorio["desfechos_corretos"]),
            ("Peso dos turnos", relatorio["pesos"]["turnos"]),
            ("Peso do desfecho", relatorio["pesos"]["desfecho"]),
        ):
            self.assertIn(f"| {rotulo} | {valor} |", markdown)
        self.assertEqual(markdown.count("## Detalhe: "), len(relatorio["detalhe"]))
        for conversa in relatorio["detalhe"]:
            self.assertIn(
                f"| {_escapar_markdown(conversa['conversa'])} | {conversa['nota']} "
                f"| {conversa['turnos_atendidos']} "
                f"| {'Sim' if conversa['desfecho_correto'] else 'Nao'} "
                f"| {'Sim' if conversa['aprovada'] else 'Nao'} "
                f"| {_escapar_markdown(conversa['motivo'])} |", markdown)
            for turno in conversa["turnos"]:
                violacoes = "<br>".join(_escapar_markdown(v) for v in turno["violacoes"])
                self.assertIn(
                    f"| {turno['turno']} | {turno['nota']} "
                    f"| {'Sim' if turno['passou'] else 'Nao'} "
                    f"| {_escapar_markdown(turno['porque'])} | {violacoes} |", markdown)
            for texto in conversa["divergencias_do_desfecho"] + conversa["memoria_de_calculo"]:
                self.assertIn(f"- {_escapar_markdown(texto)}", markdown)
        return relatorio

    def test_serializa_nota_e_detalhes_no_json_final(self):
        self.avaliar.side_effect = [self.aprovado, self.reprovado]
        versoes = []

        def salvar_e_registrar(vereditos, destino):
            salvar(vereditos, destino)
            versoes.append(destino.with_suffix(".md").read_text(encoding="utf-8"))

        with patch.object(rodar_treino, "salvar", side_effect=salvar_e_registrar):
            self.assertEqual(rodar_treino.main(), 1)

        self.assertEqual(len(versoes), 3)
        self.assertIn("Nenhuma conversa avaliada.", versoes[0])
        self.assertIn("| Turnos atendidos (%) | 100.00% |", versoes[1])
        self.assertNotIn("02-caso", versoes[1])
        self.assertIn("| Turnos atendidos (%) | 50.00% |", versoes[2])
        self.assertNotIn("Nenhuma conversa avaliada.", versoes[2])
        self.assertEqual(versoes[2].count("## Detalhe: 01\\-caso"), 1)
        self.assertIn("Memoria fornecida pelo gabarito; nao e tracing interno do agente.",
                      versoes[2])

        self.assertEqual(self.ler_relatorio(), {
            "nota": 50.0,
            "conversas": 2,
            "conversas_perfeitas": 1,
            "turnos_atendidos": "1/2",
            "desfechos_corretos": 1,
            "pesos": {"turnos": 0.7, "desfecho": 0.3},
            "detalhe": [
                {
                    "conversa": "01-caso", "nota": 100.0,
                    "turnos_atendidos": "1/1", "desfecho_correto": True,
                    "aprovada": True, "motivo": "",
                    "turnos": [{"turno": 1, "passou": True, "nota": 10.0,
                                "porque": "atendeu", "violacoes": []}],
                    "divergencias_do_desfecho": [], "memoria_de_calculo": [],
                },
                {
                    "conversa": "02-caso", "nota": 0.0,
                    "turnos_atendidos": "0/1", "desfecho_correto": False,
                    "aprovada": False, "motivo": "turno e desfecho divergentes",
                    "turnos": [{"turno": 1, "passou": False, "nota": 2.0,
                                "porque": "nao atendeu",
                                "violacoes": ["violacao teste"]}],
                    "divergencias_do_desfecho": ["valor divergente"],
                    "memoria_de_calculo": ["100 * 0.5 = 50"],
                },
            ],
        })

    def test_duas_execucoes_criam_destinos_distintos_com_mesmo_timestamp(self):
        instante = datetime(2026, 9, 8, 12, 30, tzinfo=timezone.utc)
        with patch.object(rodar_treino, "datetime") as relogio:
            relogio.now.return_value = instante
            self.assertEqual(rodar_treino.main(), 0)
            self.assertEqual(rodar_treino.main(), 0)
            self.assertEqual(relogio.now.call_count, 2)
            relogio.now.assert_called_with(timezone.utc)

        arquivos = list((self.raiz / "relatorios" / "avaliacoes").glob("*.json"))
        self.assertEqual(len(arquivos), 2)
        self.assertEqual({p.stem for p in arquivos}, {
            p.stem for p in arquivos[0].parent.glob("*.md")})
        for arquivo in arquivos:
            self.assertRegex(
                arquivo.name, r"^avaliacao-20260908T123000000000Z-[0-9a-f]{32}\.json$")
            self.assertEqual(json.loads(arquivo.read_text(encoding="utf-8"))["nota"], 100.0)

    def test_reprovacao_persiste_com_codigo_um(self):
        self.avaliar.return_value = self.reprovado

        self.assertEqual(rodar_treino.main(), 1)

        relatorio = self.ler_relatorio()
        self.assertEqual(relatorio["nota"], 0.0)
        self.assertEqual(relatorio["conversas"], 2)
        self.assertFalse(relatorio["detalhe"][0]["aprovada"])

    def test_todos_erros_ao_conduzir_preservam_relatorio_vazio(self):
        def falhar_conducao(*args, **kwargs):
            self.assertEqual(self.ler_relatorio()["detalhe"], [])
            raise RuntimeError("falha ao conduzir")

        self.conduzir.side_effect = falhar_conducao

        self.assertEqual(rodar_treino.main(), 1)

        relatorio = self.ler_relatorio()
        self.assertEqual(relatorio["nota"], 0.0)
        self.assertEqual(relatorio["conversas"], 0)
        self.assertEqual(relatorio["detalhe"], [])
        markdown = next((self.raiz / "relatorios" / "avaliacoes").glob("*.md")).read_text(
            encoding="utf-8")
        self.assertIn("| Turnos atendidos (%) | N/A (sem turnos) |", markdown)
        self.assertIn("Nenhuma conversa avaliada.", markdown)
        self.assertEqual(self.conduzir.call_count, 2)
        self.avaliar.assert_not_called()
        self.assertIn("nota: 0.0", self.saida.getvalue())

    def test_erro_ao_conduzir_continua_excluido_da_nota(self):
        self.conduzir.side_effect = [RuntimeError("falha ao conduzir"), []]

        self.assertEqual(rodar_treino.main(), 0)

        relatorio = self.ler_relatorio()
        self.assertEqual(relatorio["nota"], 100.0)
        self.assertEqual(relatorio["conversas"], 1)

    def test_falha_na_segunda_avaliacao_preserva_primeiro_score(self):
        self.avaliar.side_effect = [self.aprovado, RuntimeError("falha no juiz")]

        with self.assertRaisesRegex(RuntimeError, "falha no juiz"):
            rodar_treino.main()

        relatorio = self.ler_relatorio()
        self.assertEqual(relatorio["nota"], 100.0)
        self.assertEqual(relatorio["conversas"], 1)
        self.assertEqual(relatorio["detalhe"], [self.aprovado.como_dict()])

    def test_conversa_zerada_sem_turnos_nao_tem_desfecho_correto(self):
        self.avaliar.side_effect = [
            Veredito("01-caso", False, "resposta repetida", zerada=True), self.aprovado]

        self.assertEqual(rodar_treino.main(), 1)

        relatorio = self.ler_relatorio()
        self.assertFalse(relatorio["detalhe"][0]["desfecho_correto"])
        self.assertEqual(relatorio["detalhe"][0]["turnos_atendidos"], "0/0")

    def test_escapa_texto_livre_em_tabelas_titulos_e_listas(self):
        texto = "a|b\r\n<script>x</script> **forte** _italico_ [link](url) `codigo` \\fim\rfim"
        escapado = (
            "a\\|b<br>&lt;script&gt;x&lt;/script&gt; \\*\\*forte\\*\\* "
            "\\_italico\\_ \\[link\\]\\(url\\) \\`codigo\\` \\\\fim<br>fim")
        self.assertEqual(_escapar_markdown(texto), escapado)
        self.avaliar.return_value = Veredito(
            texto, False, texto, [NotaTurno(1, False, 2.0, texto, [texto])],
            [texto], [texto])

        self.assertEqual(rodar_treino.main(), 1)

        self.ler_relatorio()
        markdown = next((self.raiz / "relatorios" / "avaliacoes").glob("*.md")).read_text(
            encoding="utf-8")
        self.assertNotIn("<script>", markdown)
        self.assertIn(f"## Detalhe: {escapado}\n", markdown)
        self.assertIn(f"| 1 | 2.0 | Nao | {escapado} | {escapado} |\n", markdown)
        self.assertIn(f"- {escapado}\n", markdown)

    def test_preflight_sem_disponibilidade_nao_cria_relatorio(self):
        self.disponivel.return_value = False

        self.assertEqual(rodar_treino.main(), 2)

        self.assertFalse((self.raiz / "relatorios").exists())
        self.saude.assert_not_called()
        self.conduzir.assert_not_called()

    def test_preflight_sem_saude_nao_cria_relatorio(self):
        self.saude.side_effect = rodar_treino.ContainerNaoSubiu("indisponivel")

        self.assertEqual(rodar_treino.main(), 2)

        self.assertFalse((self.raiz / "relatorios").exists())
        self.conduzir.assert_not_called()

    def test_preflight_sem_casos_nao_cria_relatorio(self):
        with patch("sys.argv", ["rodar_treino.py", "--caso", "inexistente"]):
            self.assertEqual(rodar_treino.main(), 2)

        self.assertFalse((self.raiz / "relatorios").exists())
        self.existentes.assert_not_called()

    def test_preflight_falha_em_existentes_nao_cria_relatorio(self):
        self.existentes.side_effect = RuntimeError("falha na base")

        with self.assertRaisesRegex(RuntimeError, "falha na base"):
            rodar_treino.main()

        self.assertFalse((self.raiz / "relatorios").exists())
        self.conduzir.assert_not_called()
