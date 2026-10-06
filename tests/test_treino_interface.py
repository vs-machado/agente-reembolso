from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from app.treino_interface import executar_treino
from avaliacao.llm import LLMIndisponivel


class TesteTreinoInterface(unittest.TestCase):
    def test_transmite_turnos_e_avaliacao_real(self) -> None:
        def conduzir_falso(pasta, url, sessao, ao_turno, limpar):
            self.assertEqual(url, "http://127.0.0.1:8000")
            self.assertFalse(limpar)
            turno = {"turno": 1, "mensagem": "Olá", "resposta": "Como posso ajudar?", "estruturado": {}}
            ao_turno(turno)
            return [turno]

        class VereditoFalso:
            nota = 75

            def como_dict(self):
                return {"conversa": "01_fora_de_ordem", "nota": 75}

        with patch("app.treino_interface.disponivel", return_value=True), \
             patch("app.treino_interface.existentes", return_value=set()), \
             patch("app.treino_interface.conduzir", side_effect=conduzir_falso), \
             patch("app.treino_interface.avaliar", return_value=VereditoFalso()), \
             patch("app.treino_interface.salvar") as salvar, \
             patch("app.treino_interface.LOG.info") as registrar:
            eventos = [json.loads(linha) for linha in executar_treino("01")]

        self.assertEqual([evento["tipo"] for evento in eventos], ["caso", "turno", "avaliacao", "fim"])
        self.assertEqual(eventos[1]["mensagem"], "Olá")
        self.assertEqual(eventos[-1]["nota"], 75)
        salvar.assert_called_once()
        self.assertIn("treino_nota_final", registrar.call_args.args[0])
        self.assertEqual(registrar.call_args.args[2], 75)

    def test_indisponibilidade_do_juiz_nao_interrompe_proximos_casos(self) -> None:
        def conduzir_falso(pasta, url, sessao, ao_turno, limpar):
            turno = {"turno": 1, "mensagem": "Olá", "resposta": "Resposta do agente", "estruturado": {}}
            ao_turno(turno)
            return [turno]

        with patch("app.treino_interface.disponivel", return_value=True), \
             patch("app.treino_interface.existentes", return_value=set()), \
             patch("app.treino_interface.conduzir", side_effect=conduzir_falso), \
             patch("app.treino_interface.avaliar", side_effect=LLMIndisponivel("erro temporário")) as avaliar, \
             patch("app.treino_interface.salvar") as salvar, \
             patch("app.treino_interface.LOG.info") as registrar:
            eventos = [json.loads(linha) for linha in executar_treino("todos")]

        self.assertEqual([evento["tipo"] for evento in eventos].count("turno"), 3)
        self.assertEqual([evento["tipo"] for evento in eventos].count("avaliacao_indisponivel"), 3)
        self.assertEqual(eventos[-1], {"tipo": "fim", "nota": None, "conversas": 0, "sem_avaliacao": 3})
        self.assertEqual(avaliar.call_count, 3)
        salvar.assert_not_called()
        self.assertEqual(registrar.call_args.args[2], "indisponivel")


if __name__ == "__main__":
    unittest.main()
