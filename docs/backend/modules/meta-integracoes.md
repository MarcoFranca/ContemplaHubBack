# Modulo de integracoes Meta Lead Ads

## Responsabilidade

Este modulo conecta formularios da Meta Lead Ads ao funil comercial do ContemplaHub.

Ele cobre:

- cadastro multi-tenant das integracoes por `org_id`;
- conexao assistida via OAuth da Meta, preservando o modo manual existente;
- verificacao e recebimento do webhook publico;
- validacao de assinatura `X-Hub-Signature-256` com `META_APP_SECRET`;
- resolucao segura da organizacao pela tabela `meta_lead_integrations`;
- busca do lead real na Meta via `leadgen_id`;
- deduplicacao por telefone/email normalizados;
- criacao ou atualizacao do lead comercial;
- operacao assistida da Graph API para testar conexao, inscrever pagina e consultar formularios;
- rastreabilidade em `meta_webhook_events`, `event_outbox` e `audit_logs`.

## Tabelas principais

- `meta_lead_integrations`
- `meta_webhook_events`
- `leads`
- `event_outbox`
- `audit_logs`

## Fluxo principal

1. A Meta chama `GET /api/public/webhooks/meta/leadgen` para verificar o webhook.
2. O backend valida `hub.verify_token` prioritariamente contra a env `META_VERIFY_TOKEN`.
3. Se a env nao estiver configurada, faz fallback compativel para uma integracao ativa em `meta_lead_integrations`.
4. A Meta envia o evento em `POST /api/public/webhooks/meta/leadgen`.
5. O backend resolve a integracao por `page_id` e `form_id`, sem confiar no payload para tenancy.
6. O backend usa `leadgen_id` + `access_token_encrypted` da integracao para buscar o lead real na Graph API.
7. O sistema normaliza `telefone` e `email`, faz deduplicacao em `leads` por `org_id` e atualiza metadados quando o contato ja existe.
8. Quando o contato nao existe, cria lead novo com:
   - `etapa = novo`
   - `origem = meta_ads`
9. Registra o evento em `meta_webhook_events`.
10. Publica evento tecnico em `event_outbox`.
11. Registra auditoria.

## Regras de negocio importantes

- tenancy sempre vem de `meta_lead_integrations.org_id`;
- a verificacao publica do webhook nao depende de sessao, usuario logado ou middleware de auth;
- o POST do webhook exige assinatura valida quando `META_APP_SECRET` estiver configurado no ambiente;
- `default_owner_id` so e aceito se pertencer a mesma organizacao;
- `channel` da integracao fica em `meta_ads`;
- `source_label` e `form_label` ajudam a manter rastreabilidade comercial no lead;
- se o lead ja existir, o sistema atualiza metadados e nao duplica o cadastro;
- eventos duplicados do mesmo webhook sao ignorados por hash de evento.

## Operacao assistida

O backend agora expone operacoes administrativas para fechar a integracao em producao:

- `GET /meta/oauth/start`
- `GET /meta/oauth/callback`
- `GET /meta/pages`
- `GET /meta/pages/{page_id}/forms`
- `POST /meta/integrations/from-oauth`
- `POST /meta/integrations/{id}/test-connection`
- `POST /meta/integrations/{id}/subscribe-page`
- `GET /meta/integrations/{id}/subscription-status`
- `GET /meta/integrations/{id}/forms`

Essas operacoes:

- usam somente o `access_token` salvo na propria integracao;
- usam uma sessao OAuth temporaria server-side por `org_id` e `user_id` durante o fluxo assistido;
- respeitam `org_id` e `require_manager`;
- atualizam o status operacional em `settings`, sem expor o token ao frontend.
- validam `BACKEND_PUBLIC_URL` antes de montar `redirect_uri`, recusando `localhost`, `http`, path extra e dominio do frontend.

### Fluxo assistido via OAuth

1. O manager chama `GET /meta/oauth/start`.
2. O backend gera uma URL de consentimento com `state` assinado contendo `org_id` e `user_id`.
3. A Meta redireciona para `GET /meta/oauth/callback`.
4. O backend troca `code` por token de usuario e lista as paginas autorizadas.
5. O backend valida se o `user_id` do `state` ainda pertence a `org_id` do fluxo.
6. O backend salva uma integracao temporaria por pagina retornada, ja com `page_id`, `page_name` e `access_token_encrypted`.
7. O frontend consulta `GET /meta/pages` e `GET /meta/pages/{page_id}/forms` para montar o fluxo assistido sem ver o token.
8. O frontend confirma a selecao em `POST /meta/integrations/from-oauth`.
9. O backend converte o registro temporario da pagina em integracao real da org e tenta inscrever a pagina em `leadgen`.

### Diagnostico operacional do callback

O callback registra logs seguros antes e depois de cada etapa:

- recebimento de `code` e `state`;
- validacao do `state`;
- validacao de pertencimento do usuario a `org_id`;
- troca do token com mascaramento;
- busca de paginas e quantidade retornada;
- tentativa de persistencia na `meta_lead_integrations`;
- resultado do insert/update;
- redirect final de sucesso ou erro.

Para ambientes como Railway, o backend tambem inclui o motivo principal da falha no proprio texto da mensagem de log, em vez de depender apenas de campos `extra`.
O callback tambem registra indicadores objetivos de progresso: `state_validado`, `token_exchange_ok`, `pages_count` e `oauth_session_saved`.

Quando `/me/accounts` retorna vazio, o backend tambem registra diagnostico seguro com:

- usuario Meta resolvido via `/me`;
- permissoes concedidas e negadas via `/me/permissions`;
- indicacao clara para revisar acesso a paginas e escopos como `pages_show_list` e `pages_read_engagement`.

Como fallback operacional, o backend tambem tenta recuperar `page_id` via `debug_token` e `granular_scopes`, resolvendo os dados da pagina antes de desistir do fluxo.

Quando a persistencia falha por schema/tabela ausente, o backend devolve erro amigavel indicando necessidade de revisar estrutura ou migration.
Quando o redirect para o frontend falha, o backend valida `FRONTEND_SITE_URL` com `urllib.parse`, registra `FRONTEND_SITE_URL` e `redirect_url` final no log, e devolve fallback seguro em texto claro com o problema encontrado, em vez de estourar excecao generica.

### Sessao temporaria de OAuth

Para evitar tabela nova e nao expor segredo ao browser, o fluxo assistido usa um rascunho operacional na tabela existente:

- o rascunho fica vinculado a `org_id`, `created_by` e `settings.oauth_draft.oauth_user_id`;
- o token Meta fica apenas no backend, em `access_token_encrypted`;
- o `verify_token` obrigatorio da tabela e preenchido automaticamente com `META_VERIFY_TOKEN` quando existir, ou com um token tecnico estavel por org/pagina no fluxo assistido;
- `page_id` e `page_name` ja sao persistidos com os valores reais retornados pela Graph API;
- a listagem principal pode mostrar rascunhos ativos para o proprio usuario que acabou de conectar, facilitando diagnostico e continuidade do fluxo;
- depois da finalizacao, o mesmo registro passa a representar a integracao real.

## Endpoints relacionados

- `GET /api/public/webhooks/meta/leadgen`
- `POST /api/public/webhooks/meta/leadgen`
- `GET /meta/integrations`
- `POST /meta/integrations`
- `PATCH /meta/integrations/{id}`
- `GET /meta/oauth/start`
- `GET /meta/oauth/callback`
- `GET /meta/pages`
- `GET /meta/pages/{page_id}/forms`
- `POST /meta/integrations/from-oauth`
- `POST /meta/integrations/{id}/test-connection`
- `POST /meta/integrations/{id}/subscribe-page`
- `GET /meta/integrations/{id}/subscription-status`
- `GET /meta/integrations/{id}/forms`
- `GET /meta/integrations/{id}/events`

## Seguranca

- os endpoints administrativos exigem `require_manager`;
- o token da Meta nunca volta em responses para o frontend;
- o frontend nao usa service role para operar a integracao;
- o backend valida acesso e `org_id` alem do RLS.

## Pendentes de confirmacao

- a coluna `access_token_encrypted` hoje e consumida pelo backend como token operacional pronto para uso; se existir criptografia de aplicacao fora do banco, o decode correspondente ainda fica pendente de confirmacao.
