# Modulo de integracoes Meta Lead Ads

## Responsabilidade

Este modulo conecta formularios da Meta Lead Ads ao funil comercial do ContemplaHub.

Ele cobre:

- cadastro multi-tenant das integracoes por `org_id`;
- verificacao e recebimento do webhook publico;
- resolucao segura da organizacao pela tabela `meta_lead_integrations`;
- busca do lead real na Meta via `leadgen_id`;
- deduplicacao por telefone/email normalizados;
- criacao ou atualizacao do lead comercial;
- rastreabilidade em `meta_webhook_events`, `event_outbox` e `audit_logs`.

## Tabelas principais

- `meta_lead_integrations`
- `meta_webhook_events`
- `leads`
- `event_outbox`
- `audit_logs`

## Fluxo principal

1. A Meta chama `GET /api/public/webhooks/meta/leadgen` para verificar o webhook.
2. O backend valida `hub.verify_token` contra uma integracao ativa em `meta_lead_integrations`.
3. A Meta envia o evento em `POST /api/public/webhooks/meta/leadgen`.
4. O backend resolve a integracao por `page_id` e `form_id`, sem confiar no payload para tenancy.
5. O backend usa `leadgen_id` + `access_token_encrypted` da integracao para buscar o lead real na Graph API.
6. O sistema normaliza `telefone` e `email`, faz deduplicacao em `leads` por `org_id` e atualiza metadados quando o contato ja existe.
7. Quando o contato nao existe, cria lead novo com:
   - `etapa = novo`
   - `origem = meta_ads`
8. Registra o evento em `meta_webhook_events`.
9. Publica evento tecnico em `event_outbox`.
10. Registra auditoria.

## Regras de negocio importantes

- tenancy sempre vem de `meta_lead_integrations.org_id`;
- `default_owner_id` so e aceito se pertencer a mesma organizacao;
- `channel` da integracao fica em `meta_ads`;
- `source_label` e `form_label` ajudam a manter rastreabilidade comercial no lead;
- se o lead ja existir, o sistema atualiza metadados e nao duplica o cadastro;
- eventos duplicados do mesmo webhook sao ignorados por hash de evento.

## Endpoints relacionados

- `GET /api/public/webhooks/meta/leadgen`
- `POST /api/public/webhooks/meta/leadgen`
- `GET /meta/integrations`
- `POST /meta/integrations`
- `PATCH /meta/integrations/{id}`
- `GET /meta/integrations/{id}/events`

## Seguranca

- os endpoints administrativos exigem `require_manager`;
- o token da Meta nunca volta em responses para o frontend;
- o frontend nao usa service role para operar a integracao;
- o backend valida acesso e `org_id` alem do RLS.

## Pendentes de confirmacao

- a coluna `access_token_encrypted` hoje e consumida pelo backend como token operacional pronto para uso; se existir criptografia de aplicacao fora do banco, o decode correspondente ainda fica pendente de confirmacao.
