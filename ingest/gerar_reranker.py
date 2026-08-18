"""Baixa e quantiza o cross-encoder usado pelo recuperador híbrido."""

from __future__ import annotations

from pathlib import Path
from urllib.request import urlretrieve

from onnxruntime.quantization import QuantType, quantize_dynamic


MODELO = "https://huggingface.co/cross-encoder/mmarco-mMiniLMv2-L12-H384-v1/resolve/main/onnx/model.onnx"
TOKENIZER = "https://huggingface.co/cross-encoder/mmarco-mMiniLMv2-L12-H384-v1/resolve/main/tokenizer.json"
RAIZ = Path(__file__).resolve().parents[1]
DIR_RERANKER = RAIZ / "storage" / "reranker"


def gerar_reranker(diretorio: Path = DIR_RERANKER) -> None:
    """Gera os artefatos locais usados em producao, sem downloads no servico."""
    diretorio.mkdir(parents=True, exist_ok=True)
    original = diretorio / "model.onnx"
    quantizado = diretorio / "model.int8.onnx"
    tokenizer = diretorio / "tokenizer.json"
    if quantizado.exists() and tokenizer.exists():
        return
    if not original.exists():
        urlretrieve(MODELO, original)
    if not tokenizer.exists():
        urlretrieve(TOKENIZER, tokenizer)
    if not quantizado.exists():
        quantize_dynamic(original, quantizado, weight_type=QuantType.QInt8)
    original.unlink()


if __name__ == "__main__":
    gerar_reranker()
