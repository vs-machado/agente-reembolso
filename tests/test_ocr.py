from __future__ import annotations

import unittest
from unittest.mock import patch

from app.tools.ocr import extrair_texto_imagem


class TesteOcr(unittest.TestCase):
    @patch("app.tools.ocr.pytesseract.image_to_string")
    def test_extrai_texto_em_portugues(self, ocr_falso) -> None:
        ocr_falso.return_value = "  RECIBO MEDICO\n"
        imagem_png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\rIDAT\x08\x1dc\xf8\xcf\xc0\xf0\x1f\x00\x05"
            b"\x80\x02?I\xc2\xf5\xa8\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        texto = extrair_texto_imagem(imagem_png)

        self.assertEqual(texto, "RECIBO MEDICO")
        ocr_falso.assert_called_once()
        self.assertEqual(ocr_falso.call_args.kwargs["lang"], "por")
