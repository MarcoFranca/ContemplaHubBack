# Endpoints da API

## Convencoes gerais

### Headers

Headers observados no backend atual:

- `Authorization: Bearer <token>` para rotas autenticadas
- `X-Org-Id` para boa parte das rotas internas legadas
- `X-User-Id` em criacao de proposta interna
- `X-Internal-Token` no build administrativo de PDF

### Autenticacao

- rotas publicas: proposta publica, cadastro publico, marketing guide
- rotas internas legadas: dependem principalmente de `X-Org-Id`
- rotas autenticadas com contexto: lances, portal parceiro, partner-users, parte de comissoes, documentos de contrato, auth-debug

### Formato de erros

O backend usa `HTTPException` do FastAPI e geralmente retorna:

- `400` para payload/header invalido
- `401` para token invalido ou token interno ausente
- `403` para acesso fora do tenant/papel
- `404` para recurso inexistente
- `409` para conflito operacional
- `500` para erro interno ou de integracao

## Health

### `GET /health`

Uso:

- healthcheck simples da API

Resposta:

- `200 OK` com payload simples `pendente de confirmacao do corpo exato`

## Leads

### `POST /leads`

Cria lead.

Headers:

- `X-Org-Id`

Payload:

- `nome` obrigatorio
- `telefone` ou `email` obrigatorio
- opcionais:
  - `origem`
  - `owner_id`
  - `etapa` default `novo`
  - endereco principal

Resposta:

- `201`
- `LeadOut`

### `PATCH /leads/{lead_id}`

Atualiza lead.

Headers:

- `X-Org-Id`

Payload:

- qualquer campo de lead/endereco
- ao menos um campo e obrigatorio

Resposta:

- `200`
- `LeadOut`

### `PATCH /leads/{lead_id}/stage`

Move etapa do lead.

Headers:

- `X-Org-Id`

Payload:

- `stage`
- `reason` opcional

Regras:

- valida pertencimento do lead a organizacao;
- se sair de `novo` pela primeira vez, marca `first_contact_at`.

### `DELETE /leads/{lead_id}`

Exclui lead da organizacao informada.

Headers:

- `X-Org-Id`

Resposta:

- `204` sem corpo

## Meta Lead Ads

### `GET /api/public/webhooks/meta/leadgen`

Verifica o webhook da Meta.

Autenticacao:

- publica

Query params:

- `hub.mode`
- `hub.verify_token`
- `hub.challenge`

Regras:

- valida `hub.verify_token` prioritariamente contra a env `META_VERIFY_TOKEN`;
- quando a env nao estiver configurada, faz fallback compativel para uma integracao Meta ativa;
- responde o `hub.challenge` em texto puro quando a verificacao e valida;
- nao usa wrapper JSON, aspas extras nem response model;
- nao exige autenticacao ou usuario logado;
- aceita path com e sem trailing slash para evitar redirect 307/308 na validacao.

### `POST /api/public/webhooks/meta/leadgen`

Recebe eventos de leadgen da Meta.

Autenticacao:

- publica

Payload esperado:

- objeto `page` do webhook da Meta;
- eventos em `entry[].changes[]` com `field = leadgen`.

Regras:

- resolve a integracao por `page_id` e `form_id`;
- quando houver pagina conhecida mas formulario divergente ou integracao inativa, registra erro operacional claro em `meta_webhook_events`;
- nunca usa o payload para decidir `org_id`;
- valida `X-Hub-Signature-256` com `META_APP_SECRET` antes de processar o body;
- busca os dados reais do lead na Graph API usando `leadgen_id`;
- deduplica por `leadgen_id` da Meta e por `org_id + telefone/email` normalizados;
- normaliza telefone removendo prefixos como `p:` e caracteres nao numericos;
- remove o DDI `55` apenas quando o numero tiver 12 ou 13 digitos e comecar por `55`;
- preserva numeros de 10 ou 11 digitos iniciados em `55`, porque esse valor pode ser DDD brasileiro;
- mapeia `platform -> channel`, `campaign_name -> utm_campaign`, `adset_name -> utm_term`, `ad_name -> utm_content` e `form_name -> form_label`;
- preserva payload bruto da Meta e perguntas customizadas no `payload jsonb` do evento para auditoria/debug;
- cria lead novo com `etapa = novo` e `origem = meta_ads` quando o contato ainda nao existe;
- atualiza metadados do lead quando o contato ja existe;
- registra evento em `meta_webhook_events`;
- atualiza `last_webhook_at`, `last_success_at` e `last_error_*` na integracao;
- publica evento em `event_outbox`;
- registra logs estruturados de recebimento e processamento sem expor segredos.

Resposta:

- `200` com contagem de itens processados e erros do lote.

### `POST /meta/integrations/{id}/subscribe-page`

Inscreve a pagina da integracao no app da Meta.

Autenticacao:

- manager autenticado

Regras:

- opera apenas na `org_id` do usuario;
- usa o `access_token` da propria integracao e exige `Page Access Token` valido para esta operacao;
- quando a integracao ainda tiver apenas fallback com token de usuario, retorna erro amigavel orientando reconectar a conta Meta com acesso completo a pagina;
- chama `/{page-id}/subscribed_apps?subscribed_fields=leadgen` na Graph API;
- atualiza o status operacional salvo em `settings`.

### `DELETE /meta/integrations/{id}`

Remove a integracao Meta da organizacao autenticada.

Autenticacao:

- manager autenticado

Regras:

- opera apenas na `org_id` do usuario;
- tenta remover a inscricao da pagina no app da Meta antes de excluir o cadastro local;
- se a desinscricao na Meta falhar, ainda remove a integracao local e devolve o aviso em `detail`;
- registra auditoria da remocao com status da tentativa de desinscricao;
- a exclusao local atende o fluxo operacional de desvinculo e remocao do identificador da pagina no sistema.

### `GET /meta/integrations/{id}/subscription-status`

Verifica se a pagina esta inscrita no app da Meta.

Autenticacao:

- manager autenticado

Regras:

- opera apenas na `org_id` do usuario;
- exige `Page Access Token` valido para consultar a inscricao da pagina;
- consulta `/{page-id}/subscribed_apps`;
- quando `META_APP_ID` estiver configurado, compara explicitamente com o app atual;
- atualiza o status operacional salvo em `settings`.

### `GET /meta/integrations/{id}/forms`

Lista formularios disponiveis na pagina da integracao.

Autenticacao:

- manager autenticado

Regras:

- opera apenas na `org_id` do usuario;
- exige `Page Access Token` valido para consultar formularios da pagina;
- consulta `/{page-id}/leadgen_forms` na Graph API;
- nao altera o cadastro da integracao.

### `POST /meta/integrations/{id}/test-connection`

Valida se `page_id` e `access_token` da integracao estao operacionais.

Autenticacao:

- manager autenticado

Regras:

- opera apenas na `org_id` do usuario;
- exige `Page Access Token` valido para testar leitura da pagina;
- consulta `/{page-id}?fields=id,name` na Graph API;
- atualiza o status operacional salvo em `settings`.

### `GET /meta/integrations`

Lista integracoes Meta da organizacao.

Autenticacao:

- manager autenticado

Resposta:

- lista com metadados da integracao;
- exibe rascunhos ativos de OAuth apenas para o usuario que acabou de conectar a conta, para permitir acompanhamento imediato do callback;
- nao expone `access_token_encrypted` nem `verify_token`.

### `GET /meta/oauth/start`

Inicia o fluxo OAuth assistido da Meta.

Autenticacao:

- manager autenticado

Resposta:

- `auth_url` para redirecionamento ao consentimento da Meta
- a URL ja sai assinada com `state` server-side contendo `org_id` e `user_id`

Validacoes:

- `BACKEND_PUBLIC_URL` precisa apontar para a origem publica HTTPS do backend;
- o backend remove barra final antes de montar `redirect_uri`;
- o backend rejeita `localhost`, `http`, path adicional e dominio igual ao frontend;
- quando a env estiver incorreta, retorna erro amigavel em vez de gerar URL OAuth invalida.

### `GET /meta/oauth/callback`

Recebe o retorno do consentimento OAuth da Meta.

Autenticacao:

- publica

Regras:

- valida `state` assinado;
- valida que o `user_id` do `state` ainda pertence a `org_id` informada;
- troca `code` por token de usuario;
- busca paginas autorizadas;
- quando `/me/accounts` vier vazio, tenta recuperar `page_id` selecionados via `debug_token` e `granular_scopes`, antes de concluir que nao ha paginas;
- registra no callback indicadores objetivos de progresso:
  - `state_validado=true/false`
  - `token_exchange_ok=true/false`
  - `pages_count`
  - `oauth_session_saved=true/false`
- se nao houver paginas, registra diagnostico seguro do usuario OAuth e das permissoes concedidas/negadas;
- quando `/me/accounts` vier vazio, retorna erro claro orientando validar paginas acessiveis na conta Meta e permissoes como `pages_show_list` e `pages_read_engagement`;
- salva uma integracao temporaria por pagina retornada, ja com `org_id`, `page_id`, `page_name` e `access_token` mantido apenas no backend;
- quando a tabela exigir `verify_token` obrigatorio, o fluxo assistido reutiliza `META_VERIFY_TOKEN` ou gera um token tecnico estavel por `org_id + page_id`, sem depender de input manual;
- o fluxo assistido marca se o token salvo ja e um `Page Access Token` operacional ou apenas fallback com token de usuario;
- reutiliza a tabela `meta_lead_integrations` como persistencia temporaria do OAuth, sem criar tabela extra;

### `POST /api/public/webhooks/meta/leadgen`

Recebe o webhook de Lead Ads da Meta.

Autenticacao:

- publica

Regras:

- valida `X-Hub-Signature-256` quando `META_APP_SECRET` estiver configurado;
- registra o evento em `meta_webhook_events` usando o padrao compativel do client Python atual do Supabase (`insert(...).execute()` + leitura explicita quando necessario);
- tenta processar o lead e atualizar `leads` sem depender de `select("*")` encadeado apos `insert/update`;
- quando a persistencia do evento falhar, o backend registra erro claro em log em vez de falhar silenciosamente.
- registra logs de `state`, `code`, token mascarado, paginas encontradas, tentativa de persistencia, resultado do insert/update e erro detalhado;
- valida `FRONTEND_SITE_URL` com `urllib.parse` antes de redirecionar, sem aceitar valor vazio, path extra ou barra final duplicada;
- registra em log `FRONTEND_SITE_URL` e `redirect_url` final antes do redirect;
- redireciona o browser de volta para `https://SEU_FRONTEND/app/meta-integracoes?success=true&meta_connected=1`;
- quando o redirect nao puder ser montado, responde com fallback seguro em texto claro explicando o problema de configuracao do frontend, sem estourar `HTTPException` generica.

Observacoes:

- a callback e publica, mas a associacao com tenant vem do `state` assinado, nunca do client;
- em erro, redireciona para a mesma tela com `error=...`.

### `GET /meta/pages`

Lista paginas autorizadas da sessao OAuth assistida atual.

Autenticacao:

- manager autenticado

Regras:

- lista as paginas persistidas pelo callback OAuth para o usuario atual na org;
- nao expone token no client.

### `GET /meta/pages/{page_id}/forms`

Lista formularios da pagina selecionada na sessao OAuth atual.

Autenticacao:

- manager autenticado

Regras:

- usa a sessao OAuth temporaria mais recente do usuario na org;
- valida que a pagina escolhida pertence ao conjunto autorizado no fluxo atual;
- consulta a Graph API com token mantido apenas no backend.

### `POST /meta/integrations/from-oauth`

Finaliza a integracao assistida da Meta.

Autenticacao:

- manager autenticado

Regras:

- usa a integracao temporaria da pagina escolhida pelo usuario na org;
- valida a pagina e o formulario escolhidos;
- transforma o rascunho em integracao real;
- tenta inscrever a pagina no app para `leadgen`;
- desativa a flag de rascunho no registro finalizado.

### `POST /meta/integrations`

Cria integracao Meta da organizacao autenticada.

Autenticacao:

- manager autenticado

Payload:

- `nome`
- `page_id`
- `page_name` opcional
- `form_id` opcional
- `form_name` opcional
- `source_label`
- `default_owner_id` opcional
- `ativo`
- `verify_token`
- `access_token`
- `settings` opcional

Regras:

- `provider` e fixado como integracao Meta;
- `channel` e fixado como `meta_ads`;
- `default_owner_id` precisa pertencer a mesma `org_id`.

### `PATCH /meta/integrations/{id}`

Atualiza integracao Meta.

Autenticacao:

- manager autenticado

Regras:

- opera apenas na `org_id` do usuario;
- `access_token` e `verify_token` so sao alterados quando enviados no payload;
- mantem `access_token_encrypted` fora das responses.

### `GET /meta/integrations/{id}/events`

Lista eventos recebidos por integracao.

Autenticacao:

- manager autenticado

Query params:

- `limit` default `100`

Resposta:

- lista de `meta_webhook_events` da integracao, ordenada por `created_at desc`.

## Kanban

### `GET /kanban`

Retorna snapshot por colunas.

Headers:

- `X-Org-Id`

Query params:

- `show_active`
- `show_lost`

Resposta:

- `KanbanSnapshot`

Dados enriquecidos:

- interesse aberto mais recente
- scores de diagnostico
- insight de interesse
- `source_label`, `form_label`, `channel`, `utm_campaign`, `utm_term`, `utm_content`
- `meta_ads_summary` e `meta_ads_form_answers` quando `lead_diagnosticos.extras.meta_ads` existir

### `GET /kanban/metrics`

Retorna metricas agregadas.

Headers:

- `X-Org-Id`

Resposta:

- `KanbanMetrics`

Dependencia:

- RPC SQL `get_kanban_metrics`

## Diagnostico

### `GET /diagnostico/{lead_id}`

Busca diagnostico do lead.

Headers:

- `X-Org-Id`

Resposta:

- `DiagnosticRecord`

### `POST /diagnostico/{lead_id}`

Cria ou atualiza diagnostico.

Headers:

- `X-Org-Id`

Payload:

- contexto do objetivo, renda, carta-alvo e estrategia de lance

Resposta:

- `DiagnosticResponse`

Inclui:

- `scores`
- `record`

## Propostas

### `GET /lead-propostas/lead/{lead_id}`

Lista propostas do lead.

Headers:

- `X-Org-Id`

Resposta:

- lista de `LeadProposalRecord`

### `POST /lead-propostas/lead/{lead_id}`

Cria proposta.

Headers:

- `X-Org-Id`
- `X-User-Id` opcional

Payload:

- `titulo`
- `campanha`
- `status` (`rascunho` ou `enviado`)
- `cliente_overrides` opcional
- `meta` opcional
- `cenarios[]`

Resposta:

- `LeadProposalRecord`

### `GET /lead-propostas/{proposta_id}`

Busca proposta interna por ID.

Headers:

- `X-Org-Id`

### `PATCH /lead-propostas/{proposta_id}/status`

Atualiza status.

Headers:

- `X-Org-Id`

Payload:

- `status`: `rascunho | enviada | aprovada | recusada | inativa`

### `PATCH /lead-propostas/{proposta_id}/inativar`

Atalho para inativar proposta.

Headers:

- `X-Org-Id`

### `DELETE /lead-propostas/{proposta_id}`

Remove proposta.

Headers:

- `X-Org-Id`

### `GET /lead-propostas/p/{public_hash}`

Busca proposta publica por hash.

Autenticacao:

- publica

### `POST /lead-propostas/p/{public_hash}/accept`

Aceita proposta publica e inicia onboarding.

Autenticacao:

- publica

Payload:

- `source`
- `ip`
- `user_agent`

Efeitos:

- muda status para `aprovada`
- cria/reutiliza `lead_cadastros`
- tenta notificar time interno
- tenta notificar cliente com link de cadastro

Resposta:

- `ok`
- `cadastro_token`
- `cadastro_url`

## Cadastros publicos

### `GET /lead-cadastros/p/{token}`

Busca cadastro por token publico.

Resposta:

- dados resumidos do fluxo:
  - `id`
  - `org_id`
  - `lead_id`
  - `proposta_id`
  - `tipo_cliente`
  - `status`
  - `token_publico`

### `PATCH /lead-cadastros/p/{token}/pf`

Preenche cadastro PF.

Payload:

- dados pessoais
- contato
- endereco
- identidade
- renda
- forma de pagamento
- conta de devolucao

Efeito:

- faz `upsert` em `lead_cadastros_pf`
- atualiza `lead_cadastros.status` para `pendente_documentos`

### `GET /lead-cadastros/by-proposta/{proposta_id}/pf`

Consulta interna do cadastro PF por proposta.

Autenticacao:

- `pendente de confirmacao`; rota nao exige token/contexto hoje

## Contratos e cotas

### `POST /contracts/from-lead`

Cria cota + contrato a partir do lead.

Headers:

- `X-Org-Id`

Payload:

- dados da cota/carta
- `lead_id`
- parametros financeiros
- `opcoes_lance_fixo`
- `percentual_comissao`
- `imposto_retido_pct`
- observacoes de comissao

Efeitos:

- cria `cotas`
- cria `cota_lance_fixo_opcoes`
- cria `contratos`
- configura comissao
- gera lancamentos
- garante entrada em carteira
- sincroniza parceiros

### `POST /contracts/register-existing`

Registra contrato ja existente.

Headers:

- `X-Org-Id`

Payload minimo:

- `lead_id`
- `administradora_id`
- `grupo` ou `grupo_codigo`
- `numero_cota`
- `produto`
- `valor_carta`
- `prazo`
- `valor_parcela`
- `data_adesao`
- `numero_contrato`
- `data_assinatura`

Campos adicionais:

- `contract_status`
- `cota_situacao`
- `parceiro_id` opcional
- `repasse_percentual_comissao` opcional

Validacoes:

- valida lead na `org_id`
- valida administradora acessivel ao contexto autenticado:
  - aceita administradora da mesma `org_id`
  - aceita administradora global, quando o registro nao estiver vinculado a uma organizacao especifica
  - rejeita administradora vinculada a outra organizacao
- valida parceiro na `org_id`, quando informado
- valida `contract_status` separadamente de `cota_situacao`
- rejeita estados iniciais invalidos, por exemplo:
  - contrato `contemplado` com cota nao `contemplada`
  - contrato pendente com cota `cancelada`
- nao infere `data_alocacao` nem `data_contemplacao` a partir de `data_assinatura`
- bloqueia duplicidade operacional obvia:
  - contrato duplicado por `numero_contrato` dentro da organizacao
  - cota duplicada por `administradora_id + grupo_codigo + numero_cota` dentro da organizacao
  - registro repetido do mesmo lead para a mesma combinacao operacional

Resposta:

- `contrato_id`
- `cota_id`
- `contract_status`
- `cota_situacao`

### `PATCH /contracts/{contract_id}/status`

Atualiza status do contrato.

Headers:

- `X-Org-Id`

Payload:

- `status`
- `observacao`

Regras:

- valida transicao permitida;
- `alocado` move lead para `ativo`;
- `cancelado` move lead para `perdido`.

## Documentos de contrato

Todas as rotas abaixo exigem `Authorization` e `AuthContext`.

### `GET /contracts/{contract_id}/document`

Retorna metadata do documento.

### `POST /contracts/{contract_id}/document`

Upload do PDF/documento do contrato.

Payload:

- `multipart/form-data` com campo `file`

### `POST /contracts/{contract_id}/document/signed-url`

Gera URL assinada.

Payload:

- `expires_in`

### `DELETE /contracts/{contract_id}/document`

Remove documento do contrato.

## Carteira

### `GET /carteira`

Lista carteira com enriquecimento de cota, contrato e administradora.

Headers:

- `X-Org-Id`

### `POST /carteira/clientes`

Cria lead diretamente na carteira.

Headers:

- `X-Org-Id`

Payload:

- dados basicos do cliente
- endereco
- `observacoes`

Efeito:

- cria lead em etapa `ativo`
- cria/garante `carteira_clientes`

### `POST /carteira/{lead_id}/nova-negociacao`

Reabre negociacao de cliente que ja esta na carteira.

Headers:

- `X-Org-Id`

Payload:

- `stage` default `negociacao`
- `reason`

## Lances

Todas as rotas de lances usam autenticacao interna por `Authorization` e `profiles`.

### `GET /lances/cartas`

Lista cartas/cotas para operacao de lances.

Query params:

- `competencia` obrigatoria
- `status_cota`
- `administradora_id`
- `produto`
- `somente_autorizadas`
- `q`
- `page`
- `page_size`

### `GET /lances/cartas/{cota_id}`

Detalhe operacional da carta por competencia.

### `PATCH /lances/cartas/{cota_id}`

Atualiza configuracao operacional da cota.

### `POST /lances/cartas/{cota_id}/controle-mensal`

Upsert do controle do mes.

### `POST /lances/cartas/{cota_id}/registrar-lance`

Registra lance.

### `PATCH /lances/{lance_id}/resultado`

Atualiza resultado do lance.

### `POST /lances/cartas/{cota_id}/contemplar`

Marca contemplacao.

### `POST /lances/cartas/{cota_id}/cancelar`

Cancela cota.

### `POST /lances/cartas/{cota_id}/reativar`

Reativa cota.

### `GET /lances/config/regras-operadora`

Lista regras operacionais por operadora.

### `DELETE /lances/cartas/{cota_id}`

Exclui carta/cota na operacao de lances.

## Comissoes

### Gestao de parceiro

#### `GET /comissoes/parceiros`

Exige manager.

#### `POST /comissoes/parceiros`

Exige manager.

Pode criar tambem acesso de parceiro (`partner_users`) no mesmo fluxo.

#### `PATCH /comissoes/parceiros/{parceiro_id}`

Exige manager.

#### `PATCH /comissoes/parceiros/{parceiro_id}/toggle`

Exige manager.

Sincroniza status entre `parceiros_corretores` e `partner_users`.

#### `GET /comissoes/parceiros/{parceiro_id}/extrato`

Exige manager.

#### `GET /comissoes/parceiros/{parceiro_id}/delete-check`

Exige manager.

#### `DELETE /comissoes/parceiros/{parceiro_id}`

Exige manager.

### Configuracao por cota

#### `GET /comissoes/cotas/{cota_id}`

Headers:

- `X-Org-Id`

Retorna:

- config
- regras
- parceiros

#### `PUT /comissoes/cotas/{cota_id}`

Headers:

- `X-Org-Id`

Payload:

- `CotaComissaoConfigUpsertIn`

#### `GET /comissoes/cotas/{cota_id}/delete-check`

Headers:

- `X-Org-Id`

#### `DELETE /comissoes/cotas/{cota_id}`

Headers:

- `X-Org-Id`

Query:

- `force`

#### `POST /comissoes/cotas/{cota_id}/cancelar`

Headers:

- `X-Org-Id`

### Lancamentos e eventos

#### `POST /comissoes/contratos/{contrato_id}/gerar`

Headers:

- `X-Org-Id`

Payload:

- `sobrescrever`

#### `POST /comissoes/contratos/{contrato_id}/sincronizar-eventos`

Headers:

- `X-Org-Id`

#### `POST /comissoes/contratos/{contrato_id}/sincronizar-parceiros`

Headers:

- `X-Org-Id`

#### `POST /comissoes/cotas/{cota_id}/sincronizar-parceiros`

Headers:

- `X-Org-Id`

#### `GET /comissoes/contratos/{contrato_id}`

Headers:

- `X-Org-Id`

Lista lancamentos do contrato.

#### `GET /comissoes/lancamentos`

Headers:

- `X-Org-Id`

Filtros:

- `parceiro_id`
- `contrato_id`
- `cota_id`
- `status`
- `repasse_status`
- `competencia_de`
- `competencia_ate`

#### `PATCH /comissoes/lancamentos/{lancamento_id}/status`

Headers:

- `X-Org-Id`

#### `PATCH /comissoes/lancamentos/{lancamento_id}/repasse`

Headers:

- `X-Org-Id`

Valido apenas para lancamentos de parceiro.

#### `POST /comissoes/lancamentos/{lancamento_id}/marcar-repasse-pago`

Exige manager e `X-Org-Id`.

### Processamento e visoes gerenciais

#### `POST /comissoes/pagamentos/{pagamento_id}/processar`

Exige manager e `X-Org-Id`.

#### `POST /comissoes/contratos/{contrato_id}/reprocessar-competencias`

Exige manager e `X-Org-Id`.

#### `GET /comissoes/contratos/{contrato_id}/competencias`

Exige manager e `X-Org-Id`.

#### `GET /comissoes/contratos/{contrato_id}/resumo-financeiro`

Exige manager e `X-Org-Id`.

#### `GET /comissoes/contratos/{contrato_id}/timeline`

Exige manager e `X-Org-Id`.

## Partner users

Todas as rotas exigem manager autenticado.

### `GET /partner-users`

Lista acessos de parceiros.

### `GET /partner-users/{partner_user_id}`

Detalha acesso.

### `POST /partner-users/invite`

Cria ou atualiza acesso e envia convite Supabase.

### `POST /partner-users/{partner_user_id}/resend-invite`

Reenvia convite.

### `PATCH /partner-users/{partner_user_id}`

Atualiza acesso.

### `PATCH /partner-users/{partner_user_id}/toggle`

Ativa/desativa acesso.

## Portal do parceiro

Todas as rotas exigem autenticacao de parceiro.

### `GET /partner/me`

Retorna dados do acesso e cadastro do parceiro.

### `GET /partner/contracts`

Lista contratos permitidos ao parceiro.

Filtros:

- `status`
- `q`
- `page`
- `page_size`
- `sort_by`
- `sort_order`

### `GET /partner/contracts/{contract_id}`

Detalhe do contrato do parceiro.

Regras:

- contrato precisa estar em `contrato_parceiros`;
- dados do cliente podem vir mascarados;
- itens de comissao so aparecem se `can_view_commissions = true`.

### `GET /partner/commissions`

Lista lancamentos do parceiro.

Filtros:

- `status`
- `repasse_status`
- `q`
- `page`
- `page_size`
- `sort_by`
- `sort_order`

### `POST /partner/contracts/{contract_id}/document/signed-url`

Gera URL assinada do PDF do contrato.

Payload:

- `expires_in`

## Marketing guide

### `POST /api/marketing/guide/submit`

Publico.

Captura lead de landing page e registra consentimento.

### `GET /api/marketing/guide/download`

Publico.

Parametros:

- `lead_id`
- `mode` (`redirect` ou `json`)

Fluxo:

- garante que o PDF exista no Storage;
- gera signed URL;
- redireciona ou devolve JSON.

### `POST /api/marketing/guide/build-pdf`

Administrativo.

Headers:

- `X-Internal-Token`

Parametros:

- `landing_hash`

## Auth debug

Endpoints de debug de contexto/autorizacao:

- `GET /auth-debug/context`
- `GET /auth-debug/internal`
- `GET /auth-debug/partner`
- `GET /auth-debug/manager-only`
- `GET /auth-debug/partner-only`
- `GET /auth-debug/internal-only`

Uso recomendado:

- ambientes de desenvolvimento/homologacao;
- `pendente de confirmacao` se essas rotas devem permanecer expostas em producao.
