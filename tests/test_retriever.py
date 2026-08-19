from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
import unittest

import numpy as np
from llama_index.core.embeddings.mock_embed_model import MockEmbedding

from app.rag.retriever import AvaliacaoRelevanciaModel, FonteModel, RelevanciaReranker, RerankerOnnx, RetrieverHibrido, _ids_aplicaveis, _mascara_aplicaveis, _vigente, fundir_rrf


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

    def test_recupera_com_indice_persistido(self) -> None:
        with patch("app.rag.retriever.criar_embeddings_llamaindex", return_value=MockEmbedding(embed_dim=1536)):
            fontes = RetrieverHibrido().recuperar(
                "Qual e o valor de uma URS em 2026?",
                date(2026, 6, 10),
            )

        self.assertTrue(fontes)
        self.assertTrue(all(fonte.citacao for fonte in fontes))
        self.assertTrue(all(fonte.metadados["pagina"] for fonte in fontes))

    def test_reranker_llm_so_e_acionado_quando_solicitado(self) -> None:
        class RerankerFalso:
            def __init__(self) -> None:
                self.chamadas = 0

            def ordenar(self, _consulta, fontes):
                self.chamadas += 1
                return fontes

        reranker = RerankerFalso()
        with patch("app.rag.retriever.criar_embeddings_llamaindex", return_value=MockEmbedding(embed_dim=1536)):
            recuperador = RetrieverHibrido(reranker_relevancia=reranker)
            recuperador.recuperar("Qual e o valor de uma URS em 2026?", date(2026, 6, 10))
            self.assertEqual(reranker.chamadas, 0)

            recuperador.recuperar(
                "Qual e o valor de uma URS em 2026?",
                date(2026, 6, 10),
                validar_relevancia=True,
            )

        self.assertEqual(reranker.chamadas, 1)

    def test_reranker_llm_ordena_e_preserva_score_onnx(self) -> None:
        class LlmFalso:
            def with_structured_output(self, _schema):
                return self

            def invoke(self, _instrucao):
                return AvaliacaoRelevanciaModel.model_validate(
                    {
                        "ha_fonte_suficiente": True,
                        "pontuacoes": [
                            {"indice_fonte": 1, "relevante": True, "score": 30},
                            {"indice_fonte": 2, "relevante": True, "score": 90},
                        ],
                    }
                )

        fontes = [
            FonteModel("primeira", "Fonte 1", {}, 0.8, ("vetorial",)),
            FonteModel("segunda", "Fonte 2", {}, 0.2, ("bm25",)),
        ]

        resultado = RelevanciaReranker(LlmFalso()).ordenar("consulta", fontes)

        self.assertEqual([fonte.texto for fonte in resultado], ["segunda", "primeira"])
        self.assertEqual([fonte.score for fonte in resultado], [0.2, 0.8])
        self.assertEqual([fonte.score_llm for fonte in resultado], [90, 30])

    def test_reranker_llm_remove_contexto_sem_fonte_suficiente(self) -> None:
        class LlmFalso:
            def with_structured_output(self, _schema):
                return self

            def invoke(self, _instrucao):
                return AvaliacaoRelevanciaModel.model_validate(
                    {"ha_fonte_suficiente": False, "pontuacoes": []}
                )

        fontes = [FonteModel("trecho", "Fonte", {}, 0.8, ("vetorial",))]

        self.assertEqual(RelevanciaReranker(LlmFalso()).ordenar("consulta", fontes), [])
