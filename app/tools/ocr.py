"""Ferramenta local de OCR para imagens anexadas."""

from __future__ import annotations

import io

import pytesseract
from PIL import Image


def extrair_texto_imagem(conteudo: bytes) -> str:
    """Extrai o texto de uma imagem usando o modelo em portugues do Tesseract."""
    imagem = Image.open(io.BytesIO(conteudo))
    return pytesseract.image_to_string(imagem, lang="por").strip()
