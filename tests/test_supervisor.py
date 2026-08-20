from __future__ import annotations

from datetime import date
from decimal import Decimal
import re
import unittest

from app.agents.documento import FatosDocumentaisModel, ItemDocumentalModel
from app.agents.normas import (
    AvaliacaoAlcadaModel,
    AvaliacaoNormativaModel,
    FonteNormativaModel,
    ParametrosCalculoNormativoModel,
    ResultadoCalculoNormativoModel,
)
from app.agents.supervisor import AcaoSupervisorEnum, Supervisor
from app.agents.triagem import ExtracaoTriagemModel
from app.guardrails import identificar_pedido_terceiro
from app.schemas import ChatRequest, Decisao
from app.tools import CotacaoPtaxModel, ResultadoMcp


class ClienteMcpSupervisorFalso:
    def __init__(self) -> None:
        self.protocolos: list[tuple[str, dict]] = []
        self.historicos: list[str] = []

    def consultar_beneficiario(self, carteirinha: str) -> ResultadoMcp:
        return ResultadoMcp(
            True,
            dados={"carteirinha": carteirinha, "nome": "Titular", "plano": "Essencial"},
        )

    def consultar_historico(self, carteirinha: str) -> ResultadoMcp:
        self.historicos.append(carteirinha)
        return ResultadoMcp(True, dados={"carteirinha": carteirinha, "pedidos": []})

    def abrir_protocolo(self, carteirinha: str, payload: dict) -> ResultadoMcp:
        self.protocolos.append((carteirinha, payload))
        return ResultadoMcp(True, dados={"protocolo": "20260000042"})


def extrair_triagem_falsa(mensagem: str) -> ExtracaoTriagemModel:
    ocorrencia = re.search(r"carteirinha\s+(\d+)", mensagem, re.IGNORECASE)
    return ExtracaoTriagemModel(
        intencao="SOLICITAR_REEMBOLSO",
        confianca_intencao=1,
        carteirinha=ocorrencia.group(1) if ocorrencia else None,
    )


def gerar_resposta_falsa(contexto: dict[str, object]) -> str:
    return f"decisao={contexto.get('decisao')}"


def validar_pedido_terceiro_falso(mensagem: str, candidata: str | None, sessao: str | None) -> bool:
    return identificar_pedido_terceiro(mensagem, candidata, sessao)


def revisar_resposta_falsa(resposta: str) -> str:
    return resposta


def fonte_normativa_falsa() -> FonteNormativaModel:
    return FonteNormativaModel(
        texto="Regra vigente.",
        citacao="Regulamento | Art. 1",
        metadados={"vigencia_inicio": "2026-01-01"},
        score=1,
        origens=("bm25",),
    )


class TesteSupervisorMultiagente(unittest.TestCase):
    def test_normas_define_alcada_sem_regra_rigida_no_supervisor(self) -> None:
        cliente = ClienteMcpSupervisorFalso()
        rotas_com_opcao: list[list[AcaoSupervisorEnum]] = []
        perguntas_normativas: list[str] = []
        calculos = 0

        def analisar(*args, **kwargs) -> FatosDocumentaisModel:
            return FatosDocumentaisModel(
                categoria="MATERIAL_OPME",
                natureza_medica=True,
                aproveitavel=True,
                justificativa="Documento medico valido.",
                itens=[
                    ItemDocumentalModel(
                        categoria="MATERIAL_OPME",
                        natureza_procedimento="MATERIAL_OPME",
                        valor_original=Decimal("9200"),
                        codigo_moeda_iso="BRL",
                        valor_solicitado_brl=Decimal("9200"),
                        data_atendimento=date(2026, 5, 10),
                        descricao_procedimento="Protese ortopedica",
                    )
                ],
            )

        def avaliar(itens, pergunta):
            perguntas_normativas.append(pergunta)
            fonte = fonte_normativa_falsa()
            return [
                AvaliacaoNormativaModel(
                    consulta="competencia da analise automatizada",
                    data_fato=date(2026, 5, 10),
                    ha_fonte_suficiente=True,
                    vigencia_confirmada=True,
                    fontes_recuperadas=[fonte],
                    fontes_aplicaveis=[fonte],
                    resultado_elegibilidade=True,
                    regras_aplicaveis=["ART-78"],
                    avaliacao_alcada=AvaliacaoAlcadaModel(
                        exige_analista=True,
                        permite_calculo=False,
                        justificativa="A fonte vigente exige analista.",
                        regras_aplicaveis=["ART-78"],
                        indices_fontes=[1],
                    ),
                    justificativa="Fonte vigente e material.",
                )
            ]

        def calcular(*args, **kwargs):
            nonlocal calculos
            calculos += 1
            raise AssertionError("caso de alcada nao pode calcular")

        def rotear(resumo, acoes):
            rotas_com_opcao.append(acoes)
            return AcaoSupervisorEnum.NORMAS if AcaoSupervisorEnum.NORMAS in acoes else acoes[0]

        supervisor = Supervisor(
            cliente_mcp=cliente,
            extrator_triagem=extrair_triagem_falsa,
            gerador_resposta=gerar_resposta_falsa,
            validador_pedido_terceiro=validar_pedido_terceiro_falso,
            revisor_resposta=revisar_resposta_falsa,
            analisador_documento=analisar,
            avaliador_normas=avaliar,
            calculador_normas=calcular,
            roteador=rotear,
        )
        resposta = supervisor.responder(
            ChatRequest.model_validate(
                {
                    "session_id": "alcada",
                    "mensagem": "Quero reembolso, carteirinha 1234",
                    "anexo": {
                        "filename": "protese.pdf",
                        "mime_type": "application/pdf",
                        "base64": "cGRm",
                    },
                }
            )
        )
        repetida = supervisor.responder(
            ChatRequest(session_id="alcada", mensagem="Qual e o andamento?")
        )

        self.assertEqual(resposta.decisao, Decisao.ESCALADO_ANALISTA)
        self.assertEqual(resposta.valor_solicitado_brl, Decimal("9200"))
        self.assertIsNone(resposta.valor_reembolso_brl)
        self.assertEqual(resposta.regras_aplicadas, ["ART-78"])
        self.assertEqual(repetida.protocolo, "20260000042")
        self.assertEqual(len(cliente.protocolos), 1)
        self.assertEqual(calculos, 0)
        self.assertIn("'plano': 'Essencial'", perguntas_normativas[0])
        self.assertTrue(any(len(opcoes) > 1 for opcoes in rotas_com_opcao))
        estado = supervisor._grafo.get_state({"configurable": {"thread_id": "alcada"}})
        self.assertEqual(estado.values["anexos"][0]["anexo"]["base64"], "cGRm")

    def test_anexo_de_terceiro_nao_chega_ao_agente_documento(self) -> None:
        cliente = ClienteMcpSupervisorFalso()
        anexos_analisados: list[str] = []

        def analisar(anexo, **kwargs):
            anexos_analisados.append(anexo.filename)
            raise AssertionError("anexo de terceiro nao pode ser analisado")

        supervisor = Supervisor(
            cliente_mcp=cliente,
            extrator_triagem=extrair_triagem_falsa,
            gerador_resposta=gerar_resposta_falsa,
            validador_pedido_terceiro=validar_pedido_terceiro_falso,
            revisor_resposta=revisar_resposta_falsa,
            analisador_documento=analisar,
        )
        supervisor.responder(
            ChatRequest(session_id="terceiro", mensagem="Carteirinha 1234")
        )
        resposta = supervisor.responder(
            ChatRequest.model_validate(
                {
                    "session_id": "terceiro",
                    "mensagem": "Pedido para carteirinha 9999",
                    "anexo": {
                        "filename": "terceiro.pdf",
                        "mime_type": "application/pdf",
                        "base64": "cGRm",
                    },
                }
            )
        )

        self.assertEqual(resposta.decisao, Decisao.FORA_DE_ESCOPO)
        self.assertEqual(anexos_analisados, [])

    def test_dependente_resulta_em_fora_de_escopo_mesmo_para_titular(self) -> None:
        supervisor = Supervisor(
            cliente_mcp=ClienteMcpSupervisorFalso(),
            extrator_triagem=extrair_triagem_falsa,
            gerador_resposta=gerar_resposta_falsa,
            validador_pedido_terceiro=validar_pedido_terceiro_falso,
            revisor_resposta=revisar_resposta_falsa,
        )
        supervisor.responder(
            ChatRequest(session_id="dependente", mensagem="Carteirinha 1234")
        )

        resposta = supervisor.responder(
            ChatRequest(
                session_id="dependente",
                mensagem="Sou titular e quero reembolso para meu dependente.",
            )
        )

        self.assertEqual(resposta.decisao, Decisao.FORA_DE_ESCOPO)

    def test_falha_de_historico_nao_e_tratada_como_saldo_zero(self) -> None:
        cliente = ClienteMcpSupervisorFalso()
        tentativas = 0
        calculos = 0

        def consultar_historico(carteirinha: str) -> ResultadoMcp:
            nonlocal tentativas
            tentativas += 1
            if tentativas == 1:
                return ResultadoMcp(False, erro="indisponivel")
            return ResultadoMcp(True, dados={"pedidos": []})

        cliente.consultar_historico = consultar_historico

        def analisar(*args, **kwargs) -> FatosDocumentaisModel:
            return FatosDocumentaisModel(
                categoria="SESSAO_TERAPIA",
                natureza_medica=True,
                aproveitavel=True,
                justificativa="Recibo valido.",
                itens=[
                    ItemDocumentalModel(
                        categoria="SESSAO_TERAPIA",
                        valor_solicitado_brl=Decimal("100"),
                        data_atendimento=date(2026, 5, 10),
                    )
                ],
            )

        def avaliar(itens, pergunta):
            fonte = fonte_normativa_falsa()
            return [
                AvaliacaoNormativaModel(
                    consulta="terapia",
                    ha_fonte_suficiente=True,
                    vigencia_confirmada=True,
                    fontes_recuperadas=[fonte],
                    fontes_aplicaveis=[fonte],
                    resultado_elegibilidade=True,
                    parametros_calculo=ParametrosCalculoNormativoModel(
                        teto_urs=Decimal("20"),
                        valor_urs_brl=Decimal("10"),
                        coparticipacao_percentual=Decimal("0"),
                        limite_anual_brl=Decimal("1000"),
                        exige_limite_anual=True,
                    ),
                    avaliacao_alcada=AvaliacaoAlcadaModel(
                        exige_analista=False,
                        permite_calculo=True,
                        justificativa="A fonte autoriza decisao automatizada.",
                        indices_fontes=[1],
                    ),
                    justificativa="Aplicavel.",
                )
            ]

        def calcular(*args, **kwargs):
            nonlocal calculos
            calculos += 1
            return ResultadoCalculoNormativoModel(valor_reembolso_brl=Decimal("100"))

        supervisor = Supervisor(
            cliente_mcp=cliente,
            extrator_triagem=extrair_triagem_falsa,
            gerador_resposta=gerar_resposta_falsa,
            validador_pedido_terceiro=validar_pedido_terceiro_falso,
            revisor_resposta=revisar_resposta_falsa,
            analisador_documento=analisar,
            avaliador_normas=avaliar,
            calculador_normas=calcular,
            roteador=lambda resumo, acoes: (
                AcaoSupervisorEnum.NORMAS
                if AcaoSupervisorEnum.NORMAS in acoes
                else acoes[0]
            ),
        )
        primeira = supervisor.responder(
            ChatRequest.model_validate(
                {
                    "session_id": "historico",
                    "mensagem": "Carteirinha 1234",
                    "anexo": {
                        "filename": "terapia.pdf",
                        "mime_type": "application/pdf",
                        "base64": "cGRm",
                    },
                }
            )
        )
        segunda = supervisor.responder(
            ChatRequest(session_id="historico", mensagem="Pode tentar novamente?")
        )

        self.assertIsNone(primeira.decisao)
        self.assertIn("consulta ao historico de reembolsos", primeira.pendencias)
        self.assertEqual(calculos, 1)
        self.assertEqual(tentativas, 2)
        self.assertEqual(segunda.decisao, Decisao.APROVADO)
        self.assertEqual(segunda.valor_reembolso_brl, Decimal("100"))

    def test_converte_despesa_estrangeira_com_a_tool_ptax_antes_do_calculo(self) -> None:
        cotacoes: list[tuple[str, date]] = []

        def analisar(*args, **kwargs) -> FatosDocumentaisModel:
            return FatosDocumentaisModel(
                categoria="CONSULTA_MEDICA",
                natureza_medica=True,
                aproveitavel=True,
                justificativa="Recibo valido em moeda estrangeira.",
                itens=[
                    ItemDocumentalModel(
                        categoria="CONSULTA_MEDICA",
                        valor_original=Decimal("100"),
                        codigo_moeda_iso="USD",
                        data_atendimento=date(2026, 5, 10),
                    )
                ],
            )

        def avaliar(itens, pergunta):
            fonte = fonte_normativa_falsa()
            return [
                AvaliacaoNormativaModel(
                    consulta="consulta medica",
                    ha_fonte_suficiente=True,
                    vigencia_confirmada=True,
                    fontes_recuperadas=[fonte],
                    fontes_aplicaveis=[fonte],
                    resultado_elegibilidade=True,
                    parametros_calculo=ParametrosCalculoNormativoModel(
                        teto_urs=Decimal("100"),
                        valor_urs_brl=Decimal("10"),
                        coparticipacao_percentual=Decimal("0"),
                    ),
                    avaliacao_alcada=AvaliacaoAlcadaModel(
                        exige_analista=False,
                        permite_calculo=True,
                        justificativa="A fonte autoriza decisao automatizada.",
                        indices_fontes=[1],
                    ),
                    justificativa="Aplicavel.",
                )
            ]

        def consultar_cotacao(moeda: str, data_atendimento: date) -> CotacaoPtaxModel:
            cotacoes.append((moeda, data_atendimento))
            return CotacaoPtaxModel(
                moeda=moeda,
                cotacao_venda=Decimal("5"),
                data_cotacao=data_atendimento,
                url_consultada="https://bcb.gov.br/ptax",
            )

        supervisor = Supervisor(
            cliente_mcp=ClienteMcpSupervisorFalso(),
            extrator_triagem=extrair_triagem_falsa,
            gerador_resposta=gerar_resposta_falsa,
            validador_pedido_terceiro=validar_pedido_terceiro_falso,
            revisor_resposta=revisar_resposta_falsa,
            analisador_documento=analisar,
            avaliador_normas=avaliar,
            consultor_cotacao=consultar_cotacao,
            roteador=lambda resumo, acoes: (
                AcaoSupervisorEnum.NORMAS
                if AcaoSupervisorEnum.NORMAS in acoes
                else acoes[0]
            ),
        )

        resposta = supervisor.responder(
            ChatRequest.model_validate(
                {
                    "session_id": "moeda-estrangeira",
                    "mensagem": "Quero reembolso, carteirinha 1234",
                    "anexo": {
                        "filename": "consulta-usd.pdf",
                        "mime_type": "application/pdf",
                        "base64": "cGRm",
                    },
                }
            )
        )

        self.assertEqual(cotacoes, [("USD", date(2026, 5, 10))])
        self.assertEqual(resposta.valor_solicitado_brl, Decimal("500"))
        self.assertEqual(resposta.valor_reembolso_brl, Decimal("500"))
        self.assertEqual(resposta.decisao, Decisao.APROVADO)


if __name__ == "__main__":
    unittest.main()
