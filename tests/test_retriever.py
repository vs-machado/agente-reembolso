from __future__ import annotations

from datetime import date
from types import SimpleNamespace
import unittest

from app.rag.retriever import _aplicar_precedencia, _vigente, fundir_rrf


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

    def test_circular_remove_redacao_anterior_do_dispositivo_alterado(self) -> None:
        circular = SimpleNamespace(node=SimpleNamespace(metadata={"tipo": "circular", "alvos_normativos": ["ART-41"]}))
        regulamento = SimpleNamespace(node=SimpleNamespace(metadata={"tipo": "regulamento", "referencias_normativas": ["ART-41"]}))

        resultado = _aplicar_precedencia([(circular, 1, ("bm25",)), (regulamento, 0.5, ("vetorial",))])

        self.assertEqual([item[0] for item in resultado], [circular])
