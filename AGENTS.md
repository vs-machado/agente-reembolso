# Diretrizes do Projeto

## Arquitetura

- O usuario e o arquiteto da aplicacao.
- Implemente somente o que foi solicitado explicitamente.
- Ao identificar, no README ou nos comentarios do codigo, uma decisao arquitetural
  que va alem da solicitacao, pare e consulte o usuario antes de prosseguir.
- Nao escolha autonomamente a estrutura do grafo, roteamento, ferramentas,
  persistencia, estrategia de RAG ou regras de negocio.

## Linguagem

- Escreva nomes de funcoes em portugues.
- Para tipos, classes e estruturas, use o nome do dominio em portugues seguido do
  sufixo tecnico em ingles: `ConversaState`, `DocumentoNode`, `ReembolsoService`,
  `AnexoRepository`, `RespostaModel` e `ClienteFactory`.
- Nao altere nomes impostos por contratos externos ou frameworks.

## Testes

- Escreva os nomes de classes e funcoes de teste em portugues.

## Commits

- Use a convencao Git Flow nos commits: `tipo(escopo): descricao`.
- Escreva o assunto e a descricao do commit em portugues.
- Inclua no corpo uma descricao das alteracoes e funcionalidades implementadas.
