from __future__ import annotations

import unittest
from unittest.mock import patch

from app.llm import (
    DIMENSOES,
    TENTATIVAS_LLM,
    TIMEOUT_LLM_SEGUNDOS,
    criar_embeddings,
    criar_embeddings_llamaindex,
    criar_llm,
)


class TesteConfiguracaoLlm(unittest.TestCase):
    @patch("app.llm._chave_api", return_value="segredo")
    @patch("langchain_google_genai.ChatGoogleGenerativeAI")
    def test_define_limites_para_chamadas_ao_gemini(self, classe_llm, chave) -> None:
        criar_llm()

        argumentos = classe_llm.call_args.kwargs
        self.assertEqual(argumentos["google_api_key"], "segredo")
        self.assertNotIn("base_url", argumentos)
        self.assertEqual(argumentos["request_timeout"], TIMEOUT_LLM_SEGUNDOS)
        self.assertEqual(argumentos["retries"], TENTATIVAS_LLM)

    @patch("app.llm._chave_api", return_value="segredo")
    @patch("langchain_google_genai.ChatGoogleGenerativeAI")
    def test_permite_sobrescrever_limites_do_gemini(self, classe_llm, chave) -> None:
        criar_llm(request_timeout=12, retries=1)

        argumentos = classe_llm.call_args.kwargs
        self.assertEqual(argumentos["request_timeout"], 12)
        self.assertEqual(argumentos["retries"], 1)

    @patch("app.llm._chave_api", return_value="segredo")
    @patch("langchain_google_genai.GoogleGenerativeAIEmbeddings")
    def test_limita_dimensoes_dos_embeddings_langchain(self, classe_embeddings, chave) -> None:
        criar_embeddings()

        argumentos = classe_embeddings.call_args.kwargs
        self.assertEqual(argumentos["output_dimensionality"], DIMENSOES)
        self.assertNotIn("base_url", argumentos)

    @patch("app.llm._chave_api", return_value="segredo")
    @patch("llama_index.embeddings.google_genai.GoogleGenAIEmbedding")
    def test_limita_dimensoes_dos_embeddings_llamaindex(self, classe_embeddings, chave) -> None:
        criar_embeddings_llamaindex()

        argumentos = classe_embeddings.call_args.kwargs
        self.assertEqual(
            argumentos["embedding_config"], {"output_dimensionality": DIMENSOES}
        )
        self.assertNotIn("http_options", argumentos)


if __name__ == "__main__":
    unittest.main()
