# Modulo Financeiro

## Responsabilidade

O modulo financeiro operacional agora cobre duas camadas complementares:

- operacao de pagamentos em `public.pagamentos`;
- orquestracao operacional da tela de comissao em `/app/financeiro/pagamentos`.

Responsabilidades atuais:

- criar pagamento operacional em `public.pagamentos`
- editar pagamento operacional
- listar pagamentos por contrato
- listar pagamentos por cota
- atualizar numero de contrato sem sair do fluxo financeiro
- listar contratos com contexto operacional para o frontend:
- listar cartas/cotas mesmo sem contrato completo:
  - com contrato existente;
  - com numero pendente;
  - sem contrato ainda cadastrado;
- cliente
- cota
- valor da carta
  - administradora
  - comissao ativa
  - parceiro vinculado
- status do contrato e da cota
- reaproveitar o motor de competencias existente para:
  - atualizar `cota_pagamento_competencias`
  - disparar geracao de comissao
- reaproveitar a fonte de verdade comercial da comissao:
  - `cota_comissao_config`
  - `cota_comissao_regras`
  - `cota_comissao_parceiros`
- permitir previsao segura do cronograma via projeção do backend, sem gerar lancamentos financeiros massivos

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
- `app/services/comissao_service.py`
  - `upsert_config_for_cota`
  - `generate_lancamentos_for_contrato` (projecao segura)

## Endpoints principais

- `GET /financeiro/contratos-options`
- `PUT /financeiro/contratos/{contrato_id}/numero`
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
- a tela `/app/financeiro/pagamentos` deixou de ser um CRUD puro de parcelas e passou a operar o fluxo:
  - carta/cota vendida
  - configuracao da comissao
  - cronograma previsto
  - parceiro/repasse
  - consulta dos lancamentos financeiros ja gerados por competencia
- a selecao operacional nasce da `cota`; quando nao existe contrato, a carta continua aparecendo para configuracao, mas projecao definitiva e lancamentos reais continuam dependentes do contrato.
