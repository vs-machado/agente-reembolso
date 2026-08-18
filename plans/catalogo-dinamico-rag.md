# Plano Opcional: Catálogo Dinâmico para RAG

## Objetivo

Permitir a descoberta de novos arquivos em `kb/` sem alterar o código de
ingestão, preservando curadoria humana para metadados normativos cuja inferência
automática poderia levar a uma regra incorreta.

## Situação Atual

`ingest/build.py` usa a constante `CATALOGO` para a base fixa do desafio. A
abordagem é segura para os documentos conhecidos, pois declara explicitamente
vigência, status, autoridade e relações entre circulares. Arquivos fora do
catálogo são excluídos e registrados no inventário.

## Proposta

1. Mover os metadados de `CATALOGO` para `kb/catalogo.json`, versionado junto
   com a base de conhecimento.
2. Fazer o build descobrir PDFs e DOCX em `kb/`, extraindo candidatos para
   título, tipo, publicação, vigência, número de circular e referências a
   artigos por regras determinísticas.
3. Gerar `storage/inventario-pendente.json` para arquivos sem manifesto ou com
   campos obrigatórios ausentes, sem indexá-los como fonte decisória.
4. Exigir curadoria humana para `status`, relações de substituição/revogação,
   escopo material e alvos normativos de circulares.
5. Indexar fontes decisórias somente após sua entrada validada no manifesto;
   materiais de apoio podem ser indexados separadamente, sempre marcados como
   não decisórios.

## Critérios de Aceite

- Incluir um arquivo conhecido no manifesto não exige alteração em Python.
- Um arquivo desconhecido aparece no inventário pendente com rastreabilidade.
- Nenhum documento é considerado vigente, revogado ou substituído por
  inferência não revisada.
- O pipeline mantém o mesmo schema de metadados e as regras atuais de
  precedência normativa.

## Status

Opcional. Não implementar sem decisão explícita, pois altera a forma de
curadoria da base normativa.
