# Plano do Agente de Documento

## Objetivo

Processar anexos do beneficiario, classificando-os exclusivamente nas
categorias de `app/schemas.py`, extraindo fatos relevantes e validando a
consistencia documental com os dados da conversa e do cadastro autorizado.

## Limites de Responsabilidade

- Decodificar o anexo recebido e extrair texto de PDF, imagem ou DOCX usando as
  dependencias ja instaladas.
- Classificar o anexo nas categorias existentes, sem alterar `Categoria` ou
  criar valores novos.
- Reconhecer nota fiscal, recibo e relatorio clinico como documentos medicos
  candidatos a analise.
- Classificar todo documento sem natureza medica como `INVALIDO`, exclui-lo do
  contexto decisorio e solicitar o documento assistencial correto.
- Extrair valores, data de atendimento, prestador, identificadores e demais
  campos que estejam efetivamente presentes.
- Confrontar fatos dos documentos aproveitaveis recebidos na mesma sessao e
  registrar inconsistencias materiais rastreaveis, sem acusar fraude nem
  concluir irregularidade sem evidencia suficiente.
- Diante de inconsistencia, solicitar documento complementar ou reemissao. Se
  a inconsistencia persistir apos a complementacao, entregar ao Supervisor o
  sinal, as evidencias e a possibilidade de fraude para tratamento do pedido.
- Identificar todos os campos obrigatorios e condicionais da NT-02, retornando
  as pendencias em uma unica lista sem inferir campos ausentes.
- Comparar a identidade documentada com o titular validado para a sessao;
  diante de ausencia ou divergencia, registrar pendencia e orientar o
  atendimento proprio quando necessario.
- Nao aplicar regras de cobertura, vigencia, teto ou alçada.

## Entradas e Saidas Planejadas

Entradas:

- `Anexo` do contrato HTTP, incluindo nome, MIME e conteudo em base64.
- Carteirinha do titular e dados cadastrais minimizados, quando disponiveis.
- Historico de documentos validos e pendencias persistidos pela sessao.

Saidas internas tipadas:

- Lista de itens documentais, cada qual com sua `Categoria`, e justificativa
  curta da classificacao.
- Indicador de natureza medica e de documento aproveitavel.
- Fatos extraidos, com origem por pagina ou imagem quando possivel.
- Por item: valor solicitado, data de atendimento, codigo TUSS quando informado
  e finalidade ou indicacao clinica expressa no documento.
- Valor original e codigo ISO da moeda quando o documento indicar despesa
  estrangeira.
- Pendencias documentais e sinal de divergencia de identidade do beneficiario.
- Sinais de inconsistencia entre documentos da mesma sessao, com os fatos e
  referencias que os originaram.
- Indicador de inconsistencia persistente apos complementacao, sem decidir por
  escalonamento a analista.

## Fontes de Verdade e Campos

- Categoria, descricao do procedimento, valor pago, data de atendimento,
  identificacao do beneficiario e do prestador, registro profissional, assinatura
  ou carimbo e integridade do arquivo sao obtidos exclusivamente do documento.
- A carteirinha nao e extraida do documento: a identificacao nele presente e
  confrontada com o titular validado pelo MCP, sem expor CPF na resposta.
- Documentos aproveitaveis anteriores da mesma sessao permanecem como evidencia
  para comparar datas, valores, prestadores, procedimentos e identificadores.
  O agente registra somente inconsistencias verificaveis; nao afirma fraude.
- A descricao inequivoca do procedimento e obrigatoria. O codigo TUSS e usado
  quando estiver disponivel no documento; sua ausencia isolada nao gera
  pendencia se a descricao permitir a classificacao.
- O valor deve ser individualizado por procedimento ou sessao. Valor global com
  multiplos itens gera pendencia; o agente nao divide o total por estimativa.
- Indicacao clinica e obrigatoria somente para procedimentos do grupo 3 do
  Anexo IV. Pedido medico e obrigatorio somente para exame de alta
  complexidade, salvo urgencia ou emergencia comprovada.
- Para terapia, o numero da sessao e campo condicional. Quando ausente, o dado
  e sinalizado para que o agente de normas o obtenha do historico MCP, nunca da
  memoria do beneficiario.
- Relatorio clinico exigido para terapia deve conter identificacao do
  beneficiario e profissional, registro no conselho, data de emissao, periodo,
  sessoes no ano, indicacao de manutencao e assinatura.
- Legibilidade, documento inteiro, arquivo suportado, ausencia de rasura,
  traducao quando estrangeiro, comprovante de pagamento quando exigido e
  numeracao fiscal quando aplicavel tambem sao verificados.

O agente atualiza os campos ja previstos de `ChatResponse`; os schemas
existentes nao serao alterados.

Para apresentacao HTTP, a categoria e a que tiver a maior soma de valores
solicitados entre os itens. Essa categoria dominante nao substitui as categorias
dos itens no estado interno nem determina seus calculos individuais.

## Etapas

1. Definir modelos Pydantic internos para texto extraido, fatos documentais e
   classificacao, separados do contrato HTTP.
2. Implementar leitura por MIME com limites de tamanho, pagina e erro para
   impedir que anexo invalido quebre a sessao.
3. Extrair texto antes da classificacao e reter somente fatos estruturados e a
   referencia ao anexo, evitando inserir conteudo irrelevante no contexto.
4. Classificar com saida estruturada limitada ao enum `Categoria`.
5. Decompor itens com valores discriminados, preservando categoria e fatos de
   cada item no estado interno; valores globais sem discriminacao permanecem em
   pendencia e nao sao divididos por estimativa.
6. Aplicar o bloqueio deterministico para `INVALIDO`: o conteudo nao segue para
   normas, calculo ou abertura de protocolo.
7. Validar os campos C1 a C7 da NT-02 e os campos condicionais C8 a C10,
   registrando todos os codigos de pendencia aplicaveis de uma vez.
8. Validar campos documentais contra a identidade do titular e devolver
   pendencias recuperaveis em turno posterior.
9. Comparar os fatos estruturados do documento atual com documentos
   aproveitaveis anteriores da mesma sessao e devolver sinal rastreavel quando
   houver inconsistencia material.
10. Preservar documentos validos e pendencias na mesma sessao para que um
   relatorio clinico posterior complete, em vez de reiniciar, o pedido.

## Status da Implementacao

- Implementado: modelos internos Pydantic, leitura limitada de PDF, imagem e
  DOCX, analise estruturada em uma unica chamada ao LLM com classificacao
  limitada a `Categoria`, bloqueio de anexos classificados como `INVALIDO`,
  separacao entre itens de despesa e evidencias de relatorio clinico, extracao
  de valor original, codigo ISO da moeda, valor em BRL quando assim documentado,
  data, TUSS e descricao, e validacoes documentais atualmente
  cobertas pelos codigos P01 a P06, P09, P13, P15, P18, P19, P21 e P22 da
  NT-02.
- Concluido no escopo isolado: `FatosDocumentaisModel` preserva itens de
  despesa, evidencias clinicas e fatos documentais independentes do layout. A
  categoria dominante usa a maior soma dos valores em BRL; sem conversao de
  moeda estrangeira, conserva a categoria geral ate a apuracao externa.
- Implementado: testes deterministas para os anexos de treino, base64 invalido
  e classificacao estruturada, incluindo fatos sem dependencia de rotulos do
  layout e moeda estrangeira sem conversao estimada.
- Fora do escopo deste plano concluido: a integracao de `analisar_documento` ao
  estado e roteamento do Supervisor, a resolucao de pendencias entre turnos e o
  encaminhamento de fatos aproveitaveis a Normas serao tratados na adequacao
  geral dos subagentes.

## Decisao Registrada

- Documento com valores discriminados e decomposto em itens internos, mantendo
  categoria e fatos proprios para cada um. Normas calcula cada item pela sua
  categoria; a categoria dominante nao participa de calculos. Sem alterar o
  contrato HTTP, `categoria_documento` apresentara a categoria cuja soma de
  valores solicitados for maior, e os valores de resposta serao agregados. O
  arredondamento ocorre apenas depois da soma total. Esta decisao esta
  parcialmente implementada e deve ser revisada na integracao do grafo.
- O relatorio clinico que sana pendencia de terapia integra o protocolo original
  como evidencia complementar, sem constituir nova despesa. Quando apresentado
  isoladamente, sem pedido pendente, e classificado como `RELATORIO_CLINICO` e
  permanece pendente ate que recibo ou outro documento fiscal associado informe
  a data de atendimento. Sem essa data, nao fundamenta vigencia, elegibilidade
  nem calculo. Fundamentacao: NT-02, Anexo C, caso 36 e Anexo D, item 8.4.

## Criterios de Aceite

- Nota fiscal, recibo e relatorio clinico sao classificados com valores do enum
  existente.
- Conta de energia ou outro anexo nao medico resulta em `INVALIDO`, nao gera
  despesa e nao contamina as etapas seguintes.
- Valor inexistente no anexo nao e inferido pelo modelo.
- Ausencia de campo obrigatorio ou vicio documental gera uma unica pendencia
  com todos os itens faltantes, sem indeferimento imediato.
- Divergencia ou ausencia de identidade do beneficiario gera pendencia objetiva
  sem uso de dados de terceiro.
- Um documento complementar recebido depois remove apenas a pendencia que ele
  resolve.
- Uma inconsistencia entre documentos da mesma sessao permanece rastreavel e
  nao e apresentada ao beneficiario como acusacao de fraude.
- Inconsistencia inicial gera pendencia para complemento; persistindo depois da
  complementacao, o sinal e entregue ao Supervisor sem abertura de protocolo.

## Testes Planejados

- Testes para os anexos de treino, incluindo `conta_energia.pdf`.
- Testes por MIME, base64 invalido, OCR sem texto e arquivo corrompido.
- Testes de classificacao limitada ao enum e de bloqueio de documento nao
  medico.
- Testes de extracao de valores e de divergencia de carteirinha.
- Testes de acumulacao de documentos e resolucao de pendencia entre turnos.
- Testes de inconsistencias entre documentos aproveitaveis da mesma sessao.
- Testes de complemento que resolve e que mantem inconsistencia documental.
