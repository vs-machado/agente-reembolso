from __future__ import annotations

from datetime import date
from types import SimpleNamespace
import unittest

import numpy as np

from app.rag.retriever import RerankerOnnx, _ids_aplicaveis, _mascara_aplicaveis, _vigente, fundir_rrf


class TesteRetriever(unittest.TestCase):
    def test_fusao_rrf_preserva_origens(self) -> None:
        primeiro = SimpleNamespace(node=SimpleNamespace(node_id="a"))
        segundo = SimpleNamespace(node=SimpleNamespace(node_id="b"))

        resultado = fundir_rrf((("vetorial", [primeiro, segundo]), ("bm25", [segundo])))

        self.assertEqual(resultado[0][0], segundo)
        self.assertEqual(resultado[0][2], ("bm25", "vetorial"))

    def test_vigencia_respeita_data_fato(self) -> None:
        metadados = {"status": "revogado", "vigencia_inicio": "2025-07-01", "vigencia_fim": "2025-10-31"}

        self.assertTrue(_vigente(metadados, date(2025, 8, 1)))
        self.assertFalse(_vigente(metadados, date(2026, 1, 1)))

    def test_material_de_apoio_nunca_fundamenta_decisao(self) -> None:
        self.assertFalse(_vigente({"status": "apoio_desatualizado"}, date(2026, 1, 1)))

    def test_documento_pendente_de_curadoria_nao_e_recuperado(self) -> None:
        self.assertFalse(_vigente({"status": "pendente_curadoria"}, date(2026, 1, 1)))

    def test_filtros_antecipam_vigencia_e_status(self) -> None:
        metadados = {
            "atual": {"status": "vigente", "vigencia_inicio": "2025-01-01", "vigencia_fim": None},
            "futuro": {"status": "vigente", "vigencia_inicio": "2027-01-01", "vigencia_fim": None},
            "apoio": {"status": "apoio", "vigencia_inicio": None, "vigencia_fim": None},
        }

        ids = _ids_aplicaveis(metadados, date(2026, 1, 1))
        mascara = _mascara_aplicaveis(list(metadados.values()), date(2026, 1, 1))

        self.assertEqual(ids, ["atual"])
        self.assertEqual(mascara, [1, 0, 0])

    def test_reranker_reordena_candidatos_em_lote(self) -> None:
        class TokenizerFalso:
            def encode(self, *_args):
                return SimpleNamespace(ids=[1, 2], attention_mask=[1, 1], type_ids=[0, 0])

        class SessaoFalsa:
            def get_inputs(self):
                return []

            def run(self, _saidas, entradas):
                self.entradas = entradas
                return [np.array([[0.1], [0.9]])]

        primeiro = SimpleNamespace(node=SimpleNamespace(get_content=lambda: "primeiro"))
        segundo = SimpleNamespace(node=SimpleNamespace(get_content=lambda: "segundo"))
        reranker = RerankerOnnx.__new__(RerankerOnnx)
        reranker._tokenizer = TokenizerFalso()
        reranker._sessao = SessaoFalsa()

        resultado = reranker.ordenar("consulta", [(primeiro, 0.2, ("bm25",)), (segundo, 0.1, ("vetorial",))])

        self.assertEqual([item[0] for item in resultado], [segundo, primeiro])
        self.assertEqual(reranker._sessao.entradas["input_ids"].shape, (2, 2))

    def test_reranker_aceita_lista_vazia(self) -> None:
        reranker = RerankerOnnx.__new__(RerankerOnnx)
        reranker._tokenizer = object()
        reranker._sessao = object()

        self.assertEqual(reranker.ordenar("consulta", []), [])
