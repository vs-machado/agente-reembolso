from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
import unittest

from ingest.build import _blocos_docx, _blocos_pdf, _criar_nos, _dividir, _tokens


class PaginaFalsa:
    def __init__(self, texto: str) -> None:
        self._texto = texto

    def get_text(self) -> str:
        return self._texto


class TesteBuild(unittest.TestCase):
    def test_separa_marcadores_estruturais_de_pdf(self) -> None:
        pagina = PaginaFalsa(
            "TÍTULO I - DISPOSIÇÕES GERAIS\n"
            "CAPÍTULO I - DO OBJETO\n"
            "Art. 12. Regra aplicável ao reembolso.\n"
            "1. O pedido deve conter documento fiscal."
        )

        with patch("ingest.build.fitz.open", return_value=[pagina]):
            blocos = list(_blocos_pdf(Path("norma.pdf")))

        caminhos = [caminho for _, caminho, _ in blocos]
        textos = [texto for _, _, texto in blocos]
        self.assertGreaterEqual(len(blocos), 4)
        self.assertTrue(any("TÍTULO I" in texto for texto in textos))
        self.assertTrue(any("CAPÍTULO I" in texto for texto in textos))
        self.assertTrue(any("Art. 12" in texto for texto in textos))
        self.assertTrue(any("1. O pedido" in texto for texto in textos))
        self.assertTrue(all(caminho.startswith("pagina 1, bloco") for caminho in caminhos))

    def test_divisao_respeita_limite_e_sobreposicao(self) -> None:
        texto = " ".join(f"palavra{indice}" for indice in range(10))

        partes = list(_dividir(texto, limite=4, sobreposicao=1))

        self.assertEqual([len(_tokens(parte)) for parte in partes], [4, 4, 4])
        self.assertTrue(partes[0].endswith("palavra3"))
        self.assertTrue(partes[1].startswith("palavra3"))

    def test_faq_mantem_pergunta_e_resposta_no_mesmo_bloco(self) -> None:
        blocos = list(_blocos_docx(Path("kb/faq_interno.docx")))

        bloco = next(texto for _, _, texto in blocos if "Por onde começo?" in texto)

        self.assertIn("Confirme primeiro", bloco)

    def test_subchunks_referenciam_chunk_pai_rastreavel(self) -> None:
        subchunks, pais, _ = _criar_nos()
        pais_por_id = {pai["chunk_pai_id"]: pai for pai in pais}

        self.assertTrue(subchunks)
        for subchunk in subchunks:
            pai_id = subchunk.metadata["chunk_pai_id"]
            self.assertIn(pai_id, pais_por_id)
            self.assertEqual(subchunk.metadata["pagina"], pais_por_id[pai_id]["metadados"]["pagina"])
            self.assertTrue(subchunk.metadata["caminho_estrutural"])

    def test_chunks_pai_respeitam_limite_de_tamanho(self) -> None:
        _, pais, _ = _criar_nos()

        self.assertTrue(all(len(_tokens(pai["texto"])) <= 650 for pai in pais))


if __name__ == "__main__":
    unittest.main()
