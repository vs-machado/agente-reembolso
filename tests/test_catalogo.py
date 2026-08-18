from __future__ import annotations

from datetime import date
import unittest

from ingest.catalogo import DocumentoNormativoModel, _alvos, _datas


class TesteCatalogo(unittest.TestCase):
    def test_extrai_vigencia_de_tabela_no_mesmo_ano(self) -> None:
        _, inicio, fim = _datas("Este valor aplica-se aos atendimentos realizados entre 1º de janeiro e 31 de dezembro de 2026.", "tabela")

        self.assertEqual(inicio, date(2026, 1, 1))
        self.assertEqual(fim, date(2026, 12, 31))

    def test_extrai_alvos_de_circular(self) -> None:
        alvos = _alvos("O caput do art. 41 passa a vigorar com nova redação. O § 3º do art. 73 passa a vigorar.", "circular")

        self.assertEqual(alvos, ["ART-41", "ART-73"])

    def test_extrai_artigo_restabelecido_por_circular(self) -> None:
        alvos = _alvos("Fica restabelecida a redação original do art. 12. O restabelecimento não altera o art. 2.", "circular")

        self.assertEqual(alvos, ["ART-12"])

    def test_schema_preserva_pendencia_de_curadoria(self) -> None:
        documento = DocumentoNormativoModel(documento_id="DOC-1", arquivo="novo.pdf", tipo="desconhecido", titulo="Novo documento", status="pendente_curadoria", pendencias=["tipo_documento_nao_identificado"])

        self.assertEqual(documento.status, "pendente_curadoria")
