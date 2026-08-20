from __future__ import annotations

from datetime import date
from pathlib import Path
import unittest

from ingest.catalogo import DocumentoNormativoModel, _datas, extrair_documento, extrair_referencias_normativas


class TesteCatalogo(unittest.TestCase):
    def test_extrai_vigencia_de_tabela_no_mesmo_ano(self) -> None:
        _, inicio, fim = _datas("Este valor aplica-se aos atendimentos realizados entre 1º de janeiro e 31 de dezembro de 2026.", "tabela")

        self.assertEqual(inicio, date(2026, 1, 1))
        self.assertEqual(fim, date(2026, 12, 31))

    def test_extrai_data_de_publicacao_sem_vigencia(self) -> None:
        publicacao, inicio, fim = _datas("Publicado em 22 de dezembro de 2025.", "anexo")

        self.assertEqual(publicacao, date(2025, 12, 22))
        self.assertIsNone(inicio)
        self.assertIsNone(fim)

    def test_define_vigencia_curada_para_anexo_e_nota_tecnica(self) -> None:
        raiz = Path(__file__).resolve().parents[1] / "kb"

        for arquivo in ("anexo_iv_exclusoes.pdf", "nota_tecnica_02_documentos.pdf"):
            documento = extrair_documento(raiz / arquivo)
            self.assertEqual(documento.vigencia_inicio, date(2025, 12, 22))
            self.assertEqual(documento.status, "vigente")

    def test_normaliza_referencias_estruturais(self) -> None:
        referencias = extrair_referencias_normativas(
            "TÍTULO VII. Capítulo II. Seção 3. Art. 73. § 3º. "
            "Anexo IV, item 11.3. Nota Técnica 02. Código TUSS-50000462."
        )
        self.assertEqual(
            referencias,
            [
                "ANEXO-IV",
                "ART-73",
                "ART-73-PAR-3",
                "CAPITULO-II",
                "ITEM-11.3",
                "NT-02",
                "SECAO-3",
                "TITULO-VII",
                "TUSS-50000462",
            ],
        )

    def test_reconhece_paragrafo_com_contexto_do_artigo(self) -> None:
        referencias = extrair_referencias_normativas("pagina 1, bloco 3, Art. 41: § 2º O limite aplica-se.")

        self.assertIn("ART-41-PAR-2", referencias)

    def test_schema_preserva_pendencia_de_curadoria(self) -> None:
        documento = DocumentoNormativoModel(documento_id="DOC-1", arquivo="novo.pdf", tipo="desconhecido", titulo="Novo documento", status="pendente_curadoria", pendencias=["tipo_documento_nao_identificado"])

        self.assertEqual(documento.status, "pendente_curadoria")
