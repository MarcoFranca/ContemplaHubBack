# Modulo de diagnosticos

## Responsabilidade

O modulo de diagnostico registra o contexto financeiro do lead e produz um score operacional usado no CRM.

Objetivos atuais:

- medir readiness comercial;
- estimar risco;
- gerar probabilidades simples de conversao e contemplacao;
- enriquecer o kanban.

## Entidades principais

- `lead_diagnosticos`

## Fluxos principais

### Consultar diagnostico

`GET /diagnostico/{lead_id}`

- exige `X-Org-Id`;
- retorna o registro persistido para o lead.

### Salvar diagnostico

`POST /diagnostico/{lead_id}`

- exige `X-Org-Id`;
- recebe dados de objetivo, renda, reserva, carta-alvo e estrategia;
- calcula os scores no backend;
- faz insert ou update manual em `lead_diagnosticos`.

## Regras de negocio importantes

### Scoring atual

O scoring e deterministicamente calculado em Python, sem IA externa.

Fatores observados:

- capacidade de pagamento da parcela teorica;
- percentual de reserva inicial;
- existencia de renda provada.

Saidas:

- `readiness_score`
- `score_risco`
- `prob_conversao`
- `prob_contemplacao_short`
- `prob_contemplacao_med`
- `prob_contemplacao_long`

### Persistencia por lead

O service assume um diagnostico vigente por `(org_id, lead_id)`, mas faz isso por verificacao previa e nao por `upsert` nativo.

Motivo explicito no codigo:

- nao ha constraint unica conhecida em `(org_id, lead_id)`.

## Dependencias com outros modulos

- kanban le scores do diagnostico para enriquecer cards e metricas;
- operacao comercial pode usar o diagnostico para orientar propostas e lances.

## Pontos pendentes de confirmacao

- se o produto pretende manter historico de diagnosticos por lead ou apenas ultimo estado;
- se probabilidades futuramente serao substituidas por motor/modelo externo;
- se existe governanca adicional de consentimento LGPD alem de `consent_scope` e `consent_ts`.
