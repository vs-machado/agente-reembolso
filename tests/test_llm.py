from __future__ import annotations

import unittest
from unittest.mock import patch

from app.llm import TENTATIVAS_LLM, TIMEOUT_LLM_SEGUNDOS, criar_llm


class TesteConfiguracaoLlm(unittest.TestCase):
    @patch("app.llm._ambiente", return_value=("https://gateway.exemplo", "segredo"))
    @patch("langchain_google_genai.ChatGoogleGenerativeAI")
    def test_define_limites_para_chamadas_ao_gateway(self, classe_llm, ambiente) -> None:
        criar_llm()

        argumentos = classe_llm.call_args.kwargs
        self.assertEqual(argumentos["request_timeout"], TIMEOUT_LLM_SEGUNDOS)
        self.assertEqual(argumentos["retries"], TENTATIVAS_LLM)

    @patch("app.llm._ambiente", return_value=("https://gateway.exemplo", "segredo"))
    @patch("langchain_google_genai.ChatGoogleGenerativeAI")
    def test_permite_sobrescrever_limites_do_gateway(self, classe_llm, ambiente) -> None:
        criar_llm(request_timeout=12, retries=1)

        argumentos = classe_llm.call_args.kwargs
        self.assertEqual(argumentos["request_timeout"], 12)
        self.assertEqual(argumentos["retries"], 1)


if __name__ == "__main__":
    unittest.main()
