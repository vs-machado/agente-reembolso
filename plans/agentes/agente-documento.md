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

- Categoria em `Categoria` e justificativa curta da classificacao.
- Indicador de natureza medica e de documento aproveitavel.
- Fatos extraidos, com origem por pagina ou imagem quando possivel.
- `valor_solicitado_brl`, data de atendimento, codigo TUSS quando informado e
  finalidade ou indicacao clinica expressa no documento.
- Pendencias documentais e sinal de divergencia de identidade do beneficiario.

## Fontes de Verdade e Campos

- Categoria, descricao do procedimento, valor pago, data de atendimento,
  identificacao do beneficiario e do prestador, registro profissional, assinatura
  ou carimbo e integridade do arquivo sao obtidos exclusivamente do documento.
- A carteirinha nao e extraida do documento: a identificacao nele presente e
  confrontada com o titular validado pelo MCP, sem expor CPF na resposta.
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

## Etapas

1. Definir modelos Pydantic internos para texto extraido, fatos documentais e
   classificacao, separados do contrato HTTP.
2. Implementar leitura por MIME com limites de tamanho, pagina e erro para
   impedir que anexo invalido quebre a sessao.
3. Extrair texto antes da classificacao e reter somente fatos estruturados e a
   referencia ao anexo, evitando inserir conteudo irrelevante no contexto.
4. Classificar com saida estruturada limitada ao enum `Categoria`.
5. Aplicar o bloqueio deterministico para `INVALIDO`: o conteudo nao segue para
   normas, calculo ou abertura de protocolo.
6. Validar os campos C1 a C7 da NT-02 e os campos condicionais C8 a C10,
   registrando todos os codigos de pendencia aplicaveis de uma vez.
7. Validar campos documentais contra a identidade do titular e devolver
   pendencias recuperaveis em turno posterior.
8. Preservar documentos validos e pendencias na mesma sessao para que um
   relatorio clinico posterior complete, em vez de reiniciar, o pedido.

## Status da Implementacao

- Implementado: modelos internos Pydantic, leitura limitada de PDF, imagem e
  DOCX, classificacao estruturada pelo LLM limitada a `Categoria`, bloqueio de
  anexos classificados como `INVALIDO`, extracao de valor, data, TUSS e
  descricao, e validacoes documentais atualmente cobertas pelos codigos P01 a
  P19, P21 e P22 da NT-02.
- Implementado: testes deterministas para os anexos de treino, base64 invalido
  e classificacao estruturada.
- Pendente: integrar `analisar_documento` ao estado e ao roteamento do
  supervisor, para que anexos de `POST /chat` sejam processados e seus fatos
  retornem em `ChatResponse`.
- Pendente: completar as validacoes condicionais restantes, preservar e
  resolver pendencias entre turnos pelo supervisor e encaminhar somente fatos
  documentais aproveitaveis ao agente de normas.

## Decisao Registrada

- O relatorio clinico que sana pendencia de terapia integra o protocolo original
  como evidencia complementar, sem constituir nova despesa. Quando apresentado
  isoladamente, sem pedido pendente, e classificado como `RELATORIO_CLINICO` e
  segue o teto proprio previsto na Tabela URS, limitado a uma vez por ano civil.
  Fundamentacao: NT-02, Anexo C, caso 36 e Anexo D, item 8.4.

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

## Testes Planejados

- Testes para os anexos de treino, incluindo `conta_energia.pdf`.
- Testes por MIME, base64 invalido, OCR sem texto e arquivo corrompido.
- Testes de classificacao limitada ao enum e de bloqueio de documento nao
  medico.
- Testes de extracao de valores e de divergencia de carteirinha.
- Testes de acumulacao de documentos e resolucao de pendencia entre turnos.
