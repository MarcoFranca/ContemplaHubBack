# Modulo de leads

## Responsabilidade

O modulo de leads sustenta o CRM comercial do ContemplaHub.

Ele cobre:

- cadastro e atualizacao do lead;
- movimentacao no funil comercial;
- exibicao em kanban;
- enriquecimento com interesse e diagnostico;
- base de origem para propostas, cotas, contratos e carteira.

## Entidades principais

- `leads`
- `lead_interesses`
- `lead_diagnosticos` como enrichment do kanban

## Fluxos principais

### Criacao de lead

Entrada por `POST /leads`.

Regras observadas:

- `nome` e obrigatorio;
- pelo menos `telefone` ou `email` precisa existir;
- `org_id` vem de `X-Org-Id`;
- endereco principal pode ser salvo junto do lead.

### Criacao por Meta Lead Ads

Entrada por `POST /api/public/webhooks/meta/leadgen`.

Regras observadas:

- a organizacao nao vem do payload publico, e resolvida por `meta_lead_integrations`;
- o backend usa `leadgen_id` para buscar o lead real na Meta;
- a deduplicacao ocorre por `leadgen_id` da Meta e por `org_id + telefone/email` normalizados;
- quando o contato nao existe, o lead nasce com:
  - `etapa = novo`
  - `origem = meta_ads`
- quando o contato ja existe, o backend atualiza metadados comerciais sem duplicar o cadastro;
- se a integracao tiver `default_owner_id`, o lead recebe esse responsavel quando ainda nao houver um dono definido.
- os campos customizados do formulario Meta sao preservados em `lead_diagnosticos.extras.meta_ads`, junto com contexto de campanha, conjunto, criativo (`ad_name`), formulario e plataforma, sem criar colunas novas no lead.

### Atualizacao de lead

Entrada por `PATCH /leads/{lead_id}`.

Regras:

- atualizacao parcial;
- exige pelo menos um campo no payload;
- busca lead por `org_id` antes de atualizar.

### Mudanca de etapa

Entrada por `PATCH /leads/{lead_id}/stage`.

Etapas observadas:

- `novo`
- `diagnostico`
- `proposta`
- `negociacao`
- `contrato`
- `ativo`
- `perdido`

Regra de negocio importante:

- ao sair de `novo` pela primeira vez, o backend grava `first_contact_at`.

### Snapshot do kanban

Entrada por `GET /kanban`.

Composicao atual:

1. busca leads nas etapas solicitadas;
2. busca interesse aberto mais recente em `lead_interesses`;
3. busca scores e `extras` em `lead_diagnosticos`;
4. expoe metadados de origem (`source_label`, `form_label`, `channel`, `utm_*`) quando existirem;
5. monta `LeadCard` com insight de interesse e resumo compacto do formulario Meta quando houver.

## Regras de negocio importantes

- lead pertence sempre a uma `org_id`;
- a etapa comercial dirige outros modulos do sistema;
- `lead` nao equivale a contrato nem a cota;
- o kanban usa o lead como unidade de trabalho comercial;
- `ativo` e `perdido` representam estados finais do funil principal, mas ainda podem coexistir com carteira.

## Dependencias com outros modulos

- diagnostico salva em `lead_diagnosticos`;
- proposta nasce de um `lead_id`;
- cota/contrato tambem nascem a partir de `lead_id`;
- carteira usa `lead_id` como referencia principal;
- mudanca de status de contrato pode mover o lead automaticamente.
- Meta Lead Ads pode alimentar o funil diretamente na etapa `novo`.

## Pontos pendentes de confirmacao

- existencia de historico formal de etapas no banco;
- existencia de trigger/outbox de eventos para mudanca de etapa;
- regras completas para transicoes proibidas entre etapas alem das hoje implementadas.
