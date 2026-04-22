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
- nunca usa o payload para decidir `org_id`;
- busca os dados reais do lead na Graph API usando `leadgen_id`;
- deduplica por `org_id + telefone/email` normalizados;
- cria lead novo com `etapa = novo` e `origem = meta_ads` quando o contato ainda nao existe;
- atualiza metadados do lead quando o contato ja existe;
- registra evento em `meta_webhook_events`;
- atualiza `last_webhook_at`, `last_success_at` e `last_error_*` na integracao;
- publica evento em `event_outbox`;
- registra logs estruturados de recebimento e processamento sem expor segredos.

Resposta:

- `200` com contagem de itens processados e erros do lote.

### `GET /meta/integrations`

Lista integracoes Meta da organizacao.

Autenticacao:

- manager autenticado

Resposta:

- lista com metadados da integracao;
- nao expone `access_token_encrypted` nem `verify_token`.

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
