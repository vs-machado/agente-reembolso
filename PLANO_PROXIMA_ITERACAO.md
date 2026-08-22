# Plano Da Proxima Iteracao

## Base

- Worktree: `C:\web_projects\reembolso-bootcamp-2026-treino`.
- Melhor checkpoint: `07e1707 fix(triagem): reforcar resposta contextual`.
- Melhor medicao registrada: caso 01 `70,0`, caso 02 `54,4`, caso 03 `70,0`; media `64,8`.
- O runner usa `/reset`; cada caso precisa de um agente isolado ou de execucao serial. Nao executar os tres casos em paralelo contra o mesmo agente.
- `storage/` nao e versionado. A worktree precisa conter o indice gerado localmente e `storage/reranker` copiado da worktree principal.

## Estado Da Tentativa Atual

Nao houve checkpoint vencedor nesta iteracao. As alteracoes locais ainda nao commitadas sao experimentais.

### Aproveitavel

- `app/agents/normas/models.py`: aceitar `Decimal` no formato brasileiro devolvido pela LLM, por exemplo `"1141,20"` e `"1.141,20"`.
- `tests/test_normas.py`: teste que cobre essa normalizacao.

Essa correcao resolve uma falha observada: a LLM retornou `limite_anual_brl="1141,20"`; o Pydantic rejeitou o valor, a leitura normativa levantou excecao e o endpoint `/chat` retornou 500, deixando um turno sem resposta.

### Nao Aproveitavel Sem Novo Experimento

- `app/agents/normas/services.py`: a tentativa de generalizar a instrucao de dispositivos normativos removeu exemplos concretos, mas nao impediu a LLM de emitir rotulos internos como `FONTE 1`.
- `app/llm.py`: reduzir de `60s` com `3` tentativas para `20s` com `1` tentativa piorou a confiabilidade. O caso 02 terminou sem respostas nos turnos 3, 6 e 7 e recebeu nota `0,0`.

## Passos

1. Retornar ao conteudo de `07e1707`, preservando apenas a normalizacao de decimal e seu teste.
2. Restaurar `TIMEOUT_LLM_SEGUNDOS=60.0` e `TENTATIVAS_LLM=3` antes de medir score novamente.
3. Executar `python -m unittest tests.test_llm tests.test_normas tests.test_supervisor tests.test_retriever`.
4. Medir os tres casos de forma serial, com um agente reiniciado por caso. Registrar turnos reprovados, divergencias de desfecho e duracao dos nos normativos.
5. Criar experimento isolado para confiabilidade: `30s` com `2` tentativas. Nao misturar esse teste com mudancas de prompt ou RAG.
6. Manter o novo timeout somente se o caso 02 concluir sem respostas ausentes e os tres casos nao regredirem em rodadas repetidas.
7. Em experimento separado, redesenhar a instrucao normativa sem enumerar artigos ou codigos concretos e validar que os identificadores finais sejam normativos, nunca rotulos internos de fonte.

## Timeout E Retry

- A configuracao anterior pode esperar aproximadamente tres minutos quando a chamada fica indisponivel; e excessiva para diagnostico local.
- A configuracao de `20s` com `1` tentativa foi agressiva demais.
- Proximo candidato: `30s` com `2` tentativas, com teto aproximado de 90 segundos por chamada.
- A mudanca so deve virar checkpoint se for medida isoladamente e nao introduzir turnos sem resposta.

## Resultados Medidos

### Configuracao

- Codigo: `07e1707` com apenas a normalizacao de decimal brasileiro em `ParametrosCalculoNormativoModel` e o teste correspondente.
- Timeout e retry: configuracao original, `60s` e `3` tentativas.
- Execucao: um container de agente novo para cada caso, endpoint MCP em `19000`, runner containerizado com as credenciais injetadas por `--env-file`.
- Data: 22/08/2026.

| Caso | Checkpoint `07e1707` | Rodada com decimal | Variacao | Observacoes |
| --- | ---: | ---: | ---: | --- |
| 01 fora de ordem | 70,0 | 100,0 | +30,0 | 8/8 turnos e desfecho correto. |
| 02 sessao pelo historico | 54,4 | 70,0 | +15,6 | 9/9 turnos; nao houve erro de parsing nem resposta ausente. Desfecho ainda divergiu: esperado `APROVADO_PARCIAL` e `111.27`; recebido `APROVADO` e `85.59`. |
| 03 invalido e alcada | 70,0 | 80,0 | +10,0 | 5/7 turnos e desfecho correto. Turnos 4 e 6 reprovados por detalhamento incompleto. |
| Media | 64,8 | 83,3 | +18,5 | Media simples das tres conversas. |

### Avaliacao

- A normalizacao de decimal deve ser mantida: removeu uma causa concreta de HTTP 500 no caso 02 e a rodada concluiu todos os seus turnos.
- O aumento total de `18,5` pontos e promissor, mas uma rodada nao separa o efeito da correcao da variacao inerente da LLM. Repetir a medicao serial completa antes de criar checkpoint de score.
- Nenhuma conclusao sobre `30s` e `2` tentativas foi obtida nesta rodada. A configuracao medida foi a original, `60s` e `3` tentativas.
- O proximo problema funcional comprovado e o desfecho normativo do caso 02; investigar recuperacao e fundamentacao sem alterar timeout/retry no mesmo experimento.

## Restricoes

- Nao usar respostas hardcoded, tabelas por roteiro ou regras de conteudo em `if`/`elif` da orquestracao.
- Manter resposta conversacional gerada por LLM e decisao ancorada em fatos e fontes recuperadas.
- Preferir melhorias de recuperacao, grounding e validacao de rastreabilidade.
- Scores sao variaveis; uma rodada unica nao demonstra ganho.
