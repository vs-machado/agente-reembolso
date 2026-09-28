<div align="center">
  <img src="https://github.com/user-attachments/assets/bf869ca7-d9a8-4fa0-bce9-026c9a4e1574" width="280" alt="Agente de Reembolso" />
  <h1>Agente de Reembolso</h1>
  <p>Chatbot de atendimento que conversa com o usuário, consulta documentos e normas da empresa e decide sobre pedidos de reembolso médico.</p>
</div>

O Agente de Reembolso é um chatbot de atendimento que conversa com o usuário para entender seu pedido, recebe comprovantes e consulta os documentos e normas da empresa para decidir se há direito ao reembolso médico. Conforme o caso, pode aprovar o pedido integral ou parcialmente, negá-lo, solicitar informações adicionais ou encaminhá-lo a um analista humano. Quando possível, calcula o valor a reembolsar e explica as regras usadas na decisão.

## Como funciona

O agente acompanha o pedido ao longo da conversa: recebe informações e comprovantes, identifica o que ainda falta e retoma a análise quando novos dados chegam. Um supervisor em LangGraph coordena as etapas conforme o estado do atendimento, sem exigir que a carteirinha, o pedido e o comprovante cheguem em uma ordem fixa. O histórico e os anexos são mantidos em memória durante a sessão:

1. **Triagem:** identifica a intenção, consulta o beneficiário pelo MCP e verifica se há dados suficientes para continuar. Pedidos sobre terceiros são bloqueados sem encerrar o atendimento do titular.
2. **Documento:** lê anexos PDF, DOCX, JPEG ou PNG (com OCR para imagens), classifica o conteúdo e extrai itens, valores e eventuais pendências. Documentos sem natureza médica não seguem para a análise normativa.
3. **Normas:** recupera fontes da `kb/` conforme a data do atendimento, avalia cobertura e alçada e consulta o histórico da operadora quando necessário. Os parâmetros encontrados nas normas (teto em URS, valor da URS, coparticipação e eventual limite anual) alimentam um cálculo determinístico sobre os valores extraídos dos comprovantes. O cálculo considera os reembolsos anteriores do ano quando a norma exige saldo anual; valores em moeda estrangeira dependem de cotação PTAX.
4. **Resposta:** consolida os resultados no contrato da API. Pode retornar `APROVADO`, `APROVADO_PARCIAL`, `NEGADO`, `PENDENTE_DOCUMENTO`, `FORA_DE_ESCOPO` ou `ESCALADO_ANALISTA`, conforme os fatos e a fundamentação disponíveis. No escalonamento, solicita um protocolo ao MCP; quando não há dados ou fontes suficientes para concluir, apresenta pendências em vez de presumir uma decisão. A resposta textual passa por revisão de dados sensíveis.

O índice normativo é construído antes de iniciar o serviço. A ingestão com LlamaIndex extrai os documentos PDF e DOCX de `kb/`, gera chunks com metadados de origem e vigência e persiste um índice vetorial local e outro BM25 em `storage/`. Na consulta, a busca híbrida combina os resultados por RRF e filtra as fontes pela data do fato. Um reranker ONNX local pode refinar a ordem quando seus artefatos estão presentes; sem eles, a recuperação continua funcionando.

**Vigência e invalidação de fontes:** o catálogo identifica circulares que revogam outras circulares e delimita o fim de vigência das revogadas. A recuperação exclui do caso as fontes fora do período aplicável e os materiais classificados apenas como apoio ou pendentes de curadoria. Já na leitura normativa, os trechos recuperados são separados em aplicáveis e afastados, com motivo; uma circular posterior pode alterar pontualmente uma regra antiga sem invalidar todo o regulamento. Essa seleção depende da data e dos fatos do pedido: não é uma remoção definitiva dos chunks do índice. Se houver conflito material entre fontes vigentes, o agente não força uma decisão automática.

O modelo de chat utilizado é o **Gemini 2.5 Flash Lite**; embeddings usam `gemini-embedding-2`. As integrações externas usadas na execução são a API do Gemini, o servidor MCP da operadora e, para conversão de moeda quando aplicável, a API PTAX do Banco Central.

## Executar localmente

Pré-requisitos: Python 3.11, Docker com Compose e uma chave da API do Gemini. Para executar OCR de imagens fora do container, também é necessário Tesseract com o idioma português; a imagem Docker já inclui essa dependência.

1. Crie um ambiente virtual e instale as dependências:

   ```bash
   python -m venv .venv
   # Linux/macOS: source .venv/bin/activate
   # PowerShell: .venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

2. Crie `.env` a partir de `.env.example` e preencha `BOOTCAMP_API_KEY`. As variáveis `MCP_OPERADORA_URL` e `MCP_OPERADORA_TOKEN` já trazem valores para o servidor local.
3. Gere o índice **antes** de construir a imagem, pois o `Dockerfile` copia `storage/` pronto:

   ```bash
   python -m ingest.build
   ```

   Opcionalmente, para habilitar o reranker local (requer download de artefatos), execute `python -m ingest.gerar_reranker`. O diretório `storage/` não é versionado; refaça a ingestão quando os documentos da base mudarem.
4. Suba o agente e o MCP:

   ```bash
   docker compose up --build
   ```

O agente fica em `http://localhost:8000` e o MCP em `http://localhost:9000/mcp`. O Compose configura a URL interna do MCP e usa as personas de `casos_treino/` como cadastro local. Verifique `GET http://localhost:8000/health` (resposta `{"status":"ok"}`).

## API

| Rota | Uso |
| --- | --- |
| `GET /health` | Verifica se a API está no ar. |
| `POST /chat` | Processa um turno da sessão. |
| `POST /reset` | Apaga os checkpoints e sessões em memória. |

Exemplo de requisição ao `POST /chat`:

```json
{
  "session_id": "atendimento-001",
  "mensagem": "Quero solicitar reembolso de uma consulta.",
  "anexo": null
}
```

Para enviar um documento, substitua `anexo` por um objeto com `filename`, `mime_type` e `base64` (conteúdo do arquivo codificado em base64). Envie turnos seguintes com o mesmo `session_id`. A resposta contém `resposta`, `categoria_documento`, `decisao`, `valor_solicitado_brl`, `valor_reembolso_brl`, `regras_aplicadas`, `protocolo` e `pendencias`; campos ainda não apurados podem ser `null`. Como o estado é apenas em memória, ele se perde ao reiniciar o processo e não é compartilhado entre instâncias.

## Treino e testes

Com os dois serviços em execução e a chave configurada, rode `python rodar_treino.py` para simular as conversas de `casos_treino/`. Use `python rodar_treino.py -v` para acompanhar os turnos ou `python rodar_treino.py --caso 02` para um caso específico. Os relatórios JSON e Markdown são gravados em `relatorios/avaliacoes/` (diretório ignorado pelo Git). As chamadas do treino e do agente consomem a cota da API do Gemini.

Os testes automatizados podem ser executados com `python -m pytest tests` (instale `pytest` separadamente, pois ele não consta em `requirements.txt`).

## Organização

| Caminho | Responsabilidade |
| --- | --- |
| `app/main.py`, `app/schemas.py` | API HTTP e modelos de entrada/saída. |
| `app/agents/supervisor/` | Grafo, roteamento, estado da conversa e consolidação da resposta. |
| `app/agents/triagem/`, `documento/`, `normas/` | Etapas especializadas do atendimento. |
| `app/rag/`, `ingest/`, `kb/` | Recuperação, construção do índice e documentos normativos. |
| `app/tools/`, `app/guardrails/`, `app/calculo/` | Integrações, revisão da resposta e cálculos. |
| `mcp/` | Servidor local da operadora e suas três ferramentas: cadastro, histórico e protocolo. |
| `casos_treino/`, `avaliacao/`, `rodar_treino.py` | Cenários e avaliação local. |
