# Plano de Implementacao do RAG Hibrido

## Objetivo

Disponibilizar recuperacao normativa para o agente decidir sobre elegibilidade e
valores de reembolso, consultando regulamentos, notas tecnicas, tabelas, FAQs e
circulares da `kb/`.

O RAG devera usar LlamaIndex, vector store embarcado, busca vetorial e lexical
BM25, fusao de resultados e reranking. A recuperacao precisa considerar a
vigencia e as atualizacoes introduzidas por circulares.

## Decisoes Ja Alinhadas

- A unidade de recuperacao sera um subchunk; a unidade enviada ao agente sera
  seu chunk-pai estrutural.
- O chunking respeitara a estrutura do documento antes de aplicar limites de
  tokens.
- Chunks-pai terao alvo de 450 a 700 tokens.
- Subchunks terao alvo de 150 a 250 tokens.
- A busca vetorial e a busca BM25 consultarao os mesmos subchunks e metadados.
- Cada recuperador retornara inicialmente ate 30 resultados.
- A fusao inicial usara Reciprocal Rank Fusion (RRF), seguida de reranker.
- O contexto final inicialmente tera 6 a 10 chunks-pai deduplicados.
- `sentence window` nao sera o mecanismo principal; podera ser avaliado depois
  apenas para FAQs curtas.
- O reranker sera executado localmente no Cloud Run com ONNX Runtime.
- O cross-encoder inicial sera `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`.
- O modelo sera executado com quantizacao dinamica INT8; sua aceitacao depende
  de preservar as fontes esperadas nas consultas manuais.
- A data do atendimento sera a data-fato para selecionar documentos cuja
  vigencia cubra o caso analisado.
- A analise automatizada encaminhara ao analista apenas nas hipoteses expressas
  de alçada e categoria obrigatoria, usando o valor solicitado no documento.
- Circular sem alvos normativos identificaveis sera indexada como fonte
  complementar e nao revogara ou substituira fontes anteriores implicitamente.
- Os artefatos de recuperacao persistidos em `storage/` serao gerados antes do
  deploy e tratados como imutaveis durante o desafio tecnico.
- O backend vetorial inicial sera `SimpleVectorStore`, adequado ao volume
  estimado de subchunks e carregado localmente com 1 GiB de memoria no Cloud
  Run.

## Pendencias Arquiteturais

Estas decisoes devem ser confirmadas antes de implementar as fases indicadas:

- Comportamento do agente quando houver divergencia normativa sem regra de
  resolucao expressa, sem confundir essa situacao com encaminhamento por alçada;
  acompanhar em `plans/regras-decisao-e-alcada.md`.

## Proposta de Precedencia Normativa

Status: confirmado

Os documentos analisados indicam a seguinte ordem, aplicavel apenas a fontes
vigentes e ao escopo material da consulta:

1. Obrigacao legal ou regulacao setorial aplicavel.
2. Circular normativa vigente, especifica para a materia e para a data do
   atendimento.
3. Regulamento Geral, na parte nao alterada por circular.
4. Documento integrante especializado, somente em seu dominio:
   Anexo IV para exclusoes, Nota Tecnica 02 para documentos e pendencias, e
   Tabela URS para codigos, valor da URS e tetos.
5. Casuistica e exemplos da Nota Tecnica somente como orientacao.
6. Manual, FAQ e roteiros somente como apoio operacional, nunca como fundamento
   autonomo de decisao.

Regras complementares propostas:

- Entre circulares sobre a mesma materia, prevalece a de inicio de vigencia mais
  recente, nao necessariamente a de maior numero.
- Circular so altera dispositivos ou parametros expressamente abrangidos por seu
  texto; os demais itens do Regulamento permanecem validos.
- A data de atendimento sera comparada a `vigencia_inicio` e `vigencia_fim` de
  cada fonte para selecionar a regra aplicavel ao caso.
- Se duas fontes vigentes conflitarem sem regra expressa de prevalencia, o RAG
  deve expor ambas as fontes ao agente; essa situacao nao configura, por si so,
  encaminhamento ao analista humano.
- O FAQ interno esta declarado desatualizado e contem regras superadas; deve ser
  marcado como material de apoio e excluido das fontes decisorias.

## Regras de Alcada Identificadas

- O art. 78 do Regulamento Geral determina encaminhamento ao analista quando o
  valor solicitado exceder R$ 5.000,00 para consultas, exames, terapias e
  relatorios clinicos, ou R$ 7.500,00 para procedimentos cirurgicos e
  hospitalares.
- Material, ortese, protese ou material especial (OPME) deve ser encaminhado ao
  analista independentemente do valor solicitado.
- A alçada considera o valor solicitado no documento, e nao o valor de
  reembolso calculado.
- Procedimentos classificados como "sob analise" na Tabela URS nao possuem teto
  automatizavel e tambem exigem encaminhamento ao analista.
- Fora dessas hipoteses, a existencia de pendencia documental ou divergencia
  entre fontes nao cria automaticamente competencia de analista.

## Fase 0 - Inventario da Base

Status: parcialmente implementada

- Catalogar os arquivos em `kb/` por tipo, data, tema e formato.
- Inspecionar a estrutura real de regulamentos, circulares, tabelas e FAQs.
- Registrar, para cada circular, as politicas, valores ou documentos que ela
  altera, substitui ou revoga.
- Identificar documentos sem data ou sem vigencia explicita para tratamento
  manual.

Aceite:

- Existe um inventario revisado dos documentos e de suas relacoes de vigencia.
- Nenhum documento e indexado como vigente por suposicao quando a fonte indicar
  o contrario.

## Fase 1 - Modelo de Dados e Extracao

Status: implementada

- Definir o schema de metadados comum para todos os documentos.
- Incluir no minimo: `documento_id`, `tipo`, `titulo`, `data_publicacao`,
  `vigencia_inicio`, `vigencia_fim`, `status`, `autoridade`,
  `substitui_documento_id`, `invalida_documento_id` e `caminho_estrutural`.
- Extrair PDFs e DOCX preservando paginas, titulos, itens e tabelas quando
  disponiveis.
- Converter tabelas em texto semantico com cabecalhos, valores, unidade e
  vigencia repetidos em cada registro indexavel.
- Persistir dados de origem suficientes para rastrear cada chunk ao documento e
  pagina correspondentes.

Aceite:

- Cada conteudo extraido pode ser rastreado ao arquivo de origem e pagina.
- Linhas de tabela preservam o significado sem depender de celulas vizinhas.

## Fase 2 - Chunking Estrutural Parent-Child

Status: implementada

- Criar chunks-pai por artigo, clausula, secao curta, pergunta-resposta ou bloco
  logico de tabela.
- Dividir pais maiores que o limite por inciso, alinea, subtitulo ou grupo de
  condicoes semanticamente relacionado.
- Criar subchunks indexaveis com o caminho estrutural no texto e referencia ao
  `chunk_pai_id` nos metadados.
- Evitar overlap fixo; usar sobreposicao de 60 a 100 tokens apenas quando uma
  divisao inevitavel separar uma frase, lista ou condicao dependente.
- Garantir que pergunta e resposta de uma FAQ permanecam no mesmo chunk-pai.

Aceite:

- Uma consulta que encontre um subchunk retorna seu pai completo e rastreavel.
- Excecoes e condicoes do mesmo bloco normativo nao sao separadas sem contexto.

## Fase 3 - Indexacao e Persistencia

Status: implementada

- Implementar `ingest/build.py` para carregar a base, extrair, chunkar, gerar
  embeddings e persistir os artefatos em `storage/`.
- Usar LlamaIndex na indexacao e um vector store embarcado persistivel.
- Persistir tambem os subchunks e dados necessarios ao BM25, sem chamadas de
  rede na inicializacao do container.
- Incluir os artefatos ONNX e o tokenizer do reranker na imagem de producao,
  sem baixar pesos durante a inicializacao do servico.
- Fazer o processo ser deterministico para uma mesma base e configuracao.
- Registrar contagens de documentos, chunks-pai, subchunks e itens excluidos.

Aceite:

- `python -m ingest.build` reconstrui o indice a partir de `kb/`.
- A aplicacao consegue carregar os artefatos de `storage/` sem reindexar.
- O servico nao depende de escrita, atualizacao ou download de indice durante a
  execucao no Cloud Run.

## Fase 4 - Recuperadores Isolados

Status: implementada

- Implementar o recuperador vetorial sobre os subchunks.
- Implementar `BM25Retriever` sobre os mesmos subchunks.
- Aplicar filtros de `status` e vigencia antes da recuperacao sempre que houver
  data de referencia disponivel.
- Deduplicar resultados por `chunk_pai_id` ao montar o contexto.
- Expor fontes, scores e metadados em logs de diagnostico, sem dados sensiveis.

Aceite:

- Consultas por codigos, valores, prazos e numeros de circular retornam fontes
  relevantes no BM25.
- Consultas parafraseadas retornam fontes relevantes no recuperador vetorial.

## Fase 5 - Fusao, Reranking e Contexto

Status: parcialmente implementada

- Executar vector search e BM25 para cada consulta normativa.
- Fundir os resultados com RRF e preservar a origem de cada candidato.
- Enviar os candidatos fundidos ao cross-encoder executado por ONNX Runtime.
- Selecionar de 6 a 10 chunks-pai para o contexto final, respeitando filtros,
  deduplicacao e limite de tokens.
- Incluir citacoes com documento, secao e pagina para permitir justificativa da
  decisao pelo agente.

Aceite:

- Um resultado lexical exato nao e descartado por baixa similaridade vetorial.
- Um resultado semantico relevante nao depende de coincidencia literal.
- O contexto final contem apenas fontes vigentes ou sinaliza explicitamente a
  impossibilidade de determinar a vigencia.

## Fase 6 - Regras de Vigencia e Conflito

Status: parcialmente implementada

- Implementar a precedencia confirmada para circulares, regulamentos, notas,
  tabelas e FAQs.
- Excluir da recuperacao ativa itens revogados ou substituidos quando a relacao
  for conhecida.
- Favorecer a fonte vigente mais recente somente dentro da regra de precedencia
  aprovada; recencia isolada nao determina autoridade.
- Retornar um sinal estruturado de conflito quando fontes vigentes divergirem.

Aceite:

- Uma circular que substitui uma regra anterior impede a recuperacao da regra
  obsoleta como fundamento principal.
- Casos ambiguos sao encaminhados ao agente como conflito, e nao como certeza.

## Fase 7 - Validacao Manual e Integracao

Status: pendente

- Criar um conjunto manual de 15 a 25 consultas representativas, sem chamar
  RAGAS.
- Cobrir regra geral, excecao, prazo, valor de tabela, codigo, FAQ, circular
  atualizadora, conflito e ausencia de cobertura documental.
- Verificar manualmente se a fonte correta aparece entre os candidatos e no
  contexto final.
- Integrar o recuperador ao agente somente apos a validacao basica.
- Executar os testes existentes e os casos de treino relevantes.

Aceite:

- Cada consulta manual possui fonte esperada, ou classificacao explicita de
  ausencia/conflito.
- O agente recebe citacoes rastreaveis e nao usa fonte revogada como base de
  decisao.

## Registro de Andamento

Atualizar este bloco a cada mudanca relevante de implementacao ou decisao.

| Data | Fase | Alteracao | Decisao ou pendencia |
|---|---|---|---|
| 2026-08-17 | Planejamento | Plano inicial criado. | Precedencia e data de referencia pendentes. |
| 2026-08-17 | Reranker | ONNX Runtime definido para execucao local no Cloud Run. | Modelo e quantizacao INT8 ainda serao validados. |
| 2026-08-17 | Reranker | `mmarco-mMiniLMv2-L12-H384-v1` e INT8 dinamico definidos. | Validar preservacao das fontes esperadas antes do deploy. |
| 2026-08-17 | Precedencia | Hierarquia proposta a partir da base normativa. | Confirmar regra e tratamento de conflitos antes da implementacao. |
| 2026-08-17 | Vigencia | Data do atendimento definida como data-fato do caso. | Comparar com o intervalo de vigencia de cada fonte. |
| 2026-08-17 | Alcada | Hipoteses expressas de encaminhamento registradas. | Definir resposta do agente para divergencia normativa sem regra expressa. |
| 2026-08-17 | Precedencia | Hierarquia normativa confirmada. | Circular ambigua nao pode revogar fonte anterior implicitamente. |
| 2026-08-17 | Circulares | Tratamento conservador de circular ambigua confirmado. | Indexar como complementar e encaminhar para curadoria normativa. |
| 2026-08-17 | Persistencia | Indice definido como imutavel durante o desafio tecnico. | Gerar antes do deploy e carregar de `storage/`. |
| 2026-08-17 | Vector store | `SimpleVectorStore` definido como backend inicial. | Reavaliar somente diante de medicao real de memoria ou latencia. |
| 2026-08-18 | Fases 0 a 5 | Inventario, extracao, parent-child, `SimpleVectorStore`, BM25, RRF e carga persistida implementados. | Pesos ONNX INT8 e validacao manual de fontes ainda precisam ser adicionados. |
| 2026-08-18 | Fase 6 | Vigencia, revogacoes conhecidas e precedencia por dispositivos alterados implementadas. | Deteccao semantica de conflitos exige curadoria normativa adicional. |
