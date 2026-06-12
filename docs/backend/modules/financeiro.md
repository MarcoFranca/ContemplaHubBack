# Modulo Financeiro

## Responsabilidade

O modulo financeiro operacional cobre duas camadas complementares:

- operacao de pagamentos em `public.pagamentos`;
- orquestracao operacional da tela de comissao em `/app/financeiro/pagamentos`.

Responsabilidades atuais:

- criar e editar pagamento operacional em `public.pagamentos`;
- listar pagamentos por contrato e por cota;
- atualizar numero de contrato sem sair do fluxo financeiro;
- confirmar cronograma operacional de comissao por contrato;
- persistir parcelas previstas mes a mes em `public.pagamentos`;
- permitir excecoes manuais na operacao mensal:
  - pago
  - inadimplente
  - cancelar futuros
  - pular competencia
- listar cartas/cotas para o frontend mesmo quando o contrato ainda estiver incompleto;
- reaproveitar o motor de competencias existente para atualizar `cota_pagamento_competencias` e `comissao_lancamentos`;
- reaproveitar a fonte de verdade comercial da comissao:
  - `cota_comissao_config`
  - `cota_comissao_regras`
  - `cota_comissao_parceiros`
- permitir preview seguro do cronograma no backend e transformar o cronograma confirmado em operacao mensal persistida.

## Principais pontos de codigo

### Router

- `app/routers/financeiro.py`

### Schemas

- `app/schemas/financeiro.py`

### Service

- `app/services/pagamentos_service.py`

### Integracao com comissoes

- `app/services/comissao_competencia_service.py`
  - `processar_pagamento_para_comissao`
  - `upsert_competencia_from_pagamento`
  - `reprocessar_comissoes_contrato`
- `app/services/comissao_service.py`
  - `upsert_config_for_cota`
  - `generate_lancamentos_for_contrato` (projecao segura)

## Endpoints principais

- `GET /financeiro/contratos-options`
- `PUT /financeiro/contratos/{contrato_id}/numero`
- `POST /financeiro/pagamentos`
- `PUT /financeiro/pagamentos/{pagamento_id}`
- `POST /financeiro/contratos/{contrato_id}/cronograma`
- `POST /financeiro/pagamentos/{pagamento_id}/pular`
- `POST /financeiro/pagamentos/{pagamento_id}/cancelar-futuro`
- `GET /financeiro/contratos/{contrato_id}/pagamentos`
- `GET /financeiro/cotas/{cota_id}/pagamentos`

## Regras operacionais

- exige manager autenticado;
- exige `X-Org-Id`;
- cruza `X-Org-Id` com o tenant autenticado antes de qualquer operacao;
- nao altera modulo de lances;
- nao duplica logica do motor de comissao;
- registra `payload.source_module = financeiro_operacional` para operacoes manuais;
- registra `payload.source_module = financeiro_cronograma_comissao` para parcelas previstas do cronograma;
- pagamentos com `origem = manual` contam como entrada financeira elegivel para competencia/comissao quando o status permitir liberacao;
- o cronograma confirmado passa a ser o fluxo normal da carta; a equipe atua principalmente nas excecoes:
  - inadimplencia
  - cancelamento
  - pulo manual de competencia quando o ciclo do cliente precisa ser empurrado para frente;
- a tela `/app/financeiro/pagamentos` deixou de ser um CRUD puro de parcelas e passou a operar o fluxo:
  - carta/cota vendida
  - configuracao da comissao
  - cronograma previsto
  - operacao mensal persistida
  - parceiro/repasse
  - consulta dos lancamentos financeiros ja gerados por competencia
- a selecao operacional nasce da `cota`; quando nao existe contrato, a carta continua aparecendo para configuracao, mas cronograma persistido e lancamentos reais continuam dependentes do contrato.

## Reprocessamento de cronograma e `comissao_lancamentos`

- `_upsert_lancamento` (em `comissao_competencia_service.py`) localiza o lancamento existente por
  `(contrato_id, ordem, beneficiario_tipo, parceiro_id)`, que e exatamente a constraint unica
  `unq_comissao_lancamento_regra_benef` da tabela `comissao_lancamentos`.
- Isso e necessario porque `competencia_id` e `regra_id` podem mudar quando a regra comercial e
  reconfigurada (ex.: trocar de "a vista" para "parcelado em 12x" gera novas linhas em
  `cota_comissao_regras` com novos ids, mas mantendo `ordem` 1..N). Buscar pelo `regra_id`/`competencia_id`
  antigos faria o insert do novo lancamento colidir com a linha antiga (mesmo `ordem`/`beneficiario_tipo`),
  retornando `23505 duplicate key value violates unique constraint`.
- Ao reconfigurar a comissao de uma carta (numero de parcelas, parceiros, percentuais) e usar
  "Reprocessar cronograma", o lancamento existente para o mesmo `ordem`/`beneficiario_tipo`/`parceiro_id`
  e atualizado (remapeado para a nova `regra_id`/`competencia_id`), preservando historico de pagamento/repasse.
