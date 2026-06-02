# Modulo Financeiro

## Responsabilidade

O modulo financeiro operacional registra pagamentos manuais e alimenta o motor de competencias e comissoes.

Responsabilidades atuais:

- criar pagamento operacional em `public.pagamentos`
- editar pagamento operacional
- listar pagamentos por contrato
- listar pagamentos por cota
- listar contratos com contexto operacional para o frontend:
  - cliente
  - cota
  - valor da carta
  - administradora
  - comissao ativa
  - parceiro vinculado
- reaproveitar o motor de competencias existente para:
  - atualizar `cota_pagamento_competencias`
  - disparar geracao de comissao

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

## Endpoints principais

- `GET /financeiro/contratos-options`
- `POST /financeiro/pagamentos`
- `PUT /financeiro/pagamentos/{pagamento_id}`
- `GET /financeiro/contratos/{contrato_id}/pagamentos`
- `GET /financeiro/cotas/{cota_id}/pagamentos`

## Regras operacionais

- exige manager autenticado;
- exige `X-Org-Id`;
- cruza `X-Org-Id` com o tenant autenticado antes de qualquer operacao;
- nao altera modulo de lances;
- nao duplica logica do motor de comissao;
- registra `payload.source_module = financeiro_operacional` para rastreabilidade.
- pagamentos com `origem = manual` passam a valer como entrada financeira elegivel para competencia/comissao, desde que o status final do pagamento permita liberacao.
