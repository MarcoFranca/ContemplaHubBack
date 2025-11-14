# Guia do Banco de Dados – Autentika / ContemplaHub

## 0. Convenções gerais

- **Banco:** Postgres (Supabase)
- **Schema principal:** `public`
- **Multi-tenant:** quase todas as tabelas de negócio têm `org_id` (referência a `orgs.id`).
- **Chaves primárias:**
  - `uuid` com `gen_random_uuid()` para entidades de negócio.
  - `bigint` com `serial`/`nextval(...)` para logs e métricas acumuladas.
- **Carimbos de tempo:**
  - `created_at` e `updated_at` são geralmente `timestamptz` com default `now()`.
- **Booleans:** usam `false`/`true` como padrão.
- **Enums / tipos USER-DEFINED:** usados para garantir consistência (ex.: `lead_stage`, `perfil_psico`, `deal_status`, `produto`, `lance_base_calc` etc.).

> Regra de ouro: **nunca** remover/renomear coluna ou tabela em produção sem:
> 1. Conferir uso no código (Next.js, FastAPI, relatórios, views `kanban_*`);
> 2. Criar migration compatível e script de migração de dados, se necessário.

---

## 1. Mapa de domínios e tabelas

### 1.1 Core multi-tenant & usuários
- `orgs` – organizações/corretoras.
- `profiles` – perfis de usuário (ligados ao `auth.users` do Supabase).

### 1.2 Leads, CRM e jornada
- `leads` – lead principal, estágio de funil, origem, UTM etc.
- `lead_stage_history` – histórico de mudanças de estágio.
- `lead_stage_spans` – spans agregados por estágio (tempo em cada etapa).
- `lead_interesses` – interesses por produto/valor/prazo.
- `lead_diagnosticos` – diagnóstico consultivo + scores + probabilidades.
- `activities` – tarefas/atividades ligadas ao lead (follow-ups, ligações etc.).
- `notes` – anotações livres do consultor.
- `attachments` – documentos ligados ao lead (storage path, mime type etc.).
- `consent_logs` – logs de consentimento LGPD.
- `landing_pages` – cadastro de LPs, hashes públicos, domínios permitidos.

### 1.3 Consórcio & operação
- `administradoras` – administradoras de consórcio (nome, CNPJ, site).
- `grupos` – grupos de consórcio (código, produto, assembleia).
- `cotas` – cotas atreladas a leads, grupos e administradoras.
- `lances` – lances dados pela corretora/cliente em assembleias.
- `contemplacoes` – registros de contemplação por cota.

### 1.4 Negócios, propostas e contratos
- `deals` – oportunidades/negócios no funil (pipeline comercial).
- `propostas` – propostas de consórcio geradas (inclui resultado e PDF).
- `contratos` – contratos fechados (número, PDF, status).
- `pagamentos` – pagamentos ligados a contratos (fluxo de comissão/receita).

### 1.5 Infraestrutura, eventos e métricas
- `event_outbox` – outbox pattern para automações (Twilio/Postmark/Jobs).
- `audit_logs` – trilha de auditoria (quem fez o quê, em qual entidade).
- `kanban_avg_days` – visão/agg: tempo médio em cada estágio do funil.
- `kanban_conversion` – visão/agg: % conversão por estágio.
- `kanban_diag_completion` – visão/agg: % de diagnóstico completo por etapa.
- `kanban_readiness_avg` – visão/agg: média de `readiness_score` por etapa.
- `kanban_tfirstcontact_avg` – visão/agg: tempo médio até primeiro contato.

---

## 2. Core multi-tenant

### 2.1 Tabela `orgs`

**Propósito:** representa cada corretora/organização. Base da separação multi-tenant.

**Campos principais:**

- `id :: uuid` (PK) – identificador da organização.
- `nome :: text` – nome fantasia interno.
- `slug :: text` – slug para URLs / identificação amigável.
- `active :: boolean` (default `true`) – se a org está ativa.
- `whatsapp_phone :: text` – número principal para automações Twilio.
- `email_from :: text` – remetente padrão para Postmark.
- `brand :: jsonb` – configs de branding (cores, logos, mensagens).
- `timezone :: text` (default `'America/Sao_Paulo'`) – fuso da org.
- `cnpj :: text` – CNPJ da corretora.
- `susep :: text` – código SUSEP (quando aplicável).
- `owner_user_id :: uuid` – user “dono” (admin raiz).
- `created_at :: timestamptz` (default `now()`).

**Relacionamentos esperados:**

- 1:N com quase tudo: `profiles`, `leads`, `cotas`, `deals`, `propostas`, `event_outbox`, etc.

---

### 2.2 Tabela `profiles`

**Propósito:** dados de perfil interno de usuários (time de venda, gestor etc.).

**Campos principais:**

- `user_id :: uuid` (PK) – referência a `auth.users.id`.
- `org_id :: uuid` – a qual organização o usuário pertence.
- `nome :: text` – nome do usuário.
- `telefone :: text` – contato.
- `role :: text` (default `'vendedor'`) – papel: `admin`, `gestor`, `vendedor` etc.
- `created_at :: timestamptz` (default `now()`).

**Relacionamentos:**

- `org_id` → `orgs.id`.
- Referenciado por:
  - `leads.owner_id`
  - `activities.created_by`
  - `deals.created_by`
  - `notes.created_by`
  - etc.

---

## 3. Leads, CRM e jornada

### 3.1 Tabela `leads`

**Propósito:** entidade central de lead/prospect, com estágio de funil e metadados de origem.

**Campos principais:**

- Identificação:
  - `id :: uuid` (PK)
  - `org_id :: uuid`
  - `nome :: text`
  - `telefone :: text`
  - `email :: text`

- Origem & perfil:
  - `origem :: lead_origin (USER-DEFINED)` – origem do lead (LP, indicação, orgânico etc.).
  - `perfil :: perfil_psico (USER-DEFINED, default 'nao_informado')` – perfil psicológico/comportamental.
  - `valor_interesse :: numeric` – valor de carta de interesse inicial.
  - `prazo_meses :: integer` – horizonte de prazo de interesse.

- LGPD & consentimento:
  - `consentimento :: boolean` (default `false`)
  - `consent_scope :: text`
  - `consent_ts :: timestamptz`

- Marketing & tracking:
  - `utm_source :: text`
  - `utm_medium :: text`
  - `utm_campaign :: text`
  - `utm_term :: text`
  - `utm_content :: text`
  - `landing_id :: uuid` – referência à LP (`landing_pages.id`).
  - `source_label :: text` – label amigável (“Form Autentika Imóveis”).
  - `form_label :: text` – identificação do formulário.
  - `channel :: text` – canal primário (WhatsApp, LP, telefone etc.).
  - `referrer_url :: text`
  - `user_agent :: text`

- Funil & dono:
  - `etapa :: lead_stage (USER-DEFINED, default 'novo')` – estágio no Kanban.
  - `owner_id :: uuid` – responsável (vendedor/consultor).
  - `first_contact_at :: timestamptz` – data do primeiro contato.
  - `last_activity_at :: timestamptz` – última interação.

- Sistema:
  - `created_by :: uuid` – usuário que criou o lead (quando interno).
  - `created_at :: timestamptz` (default `now()`)
  - `updated_at :: timestamptz` (default `now()`)

**Relacionamentos:**

- `org_id` → `orgs.id`
- `owner_id` → `profiles.user_id`
- `landing_id` → `landing_pages.id`
- 1:N com:
  - `lead_interesses`
  - `lead_diagnosticos`
  - `lead_stage_history`
  - `lead_stage_spans`
  - `activities`
  - `notes`
  - `attachments`
  - `consent_logs`
  - `deals`
  - `propostas`
  - `cotas` (indiretamente via contratação)

---

### 3.2 Tabela `lead_stage_history`

**Propósito:** trilha de auditoria de mudança de estágio.

**Campos:**

- `id :: bigint` (PK, `serial`)
- `lead_id :: uuid`
- `from_stage :: lead_stage (USER-DEFINED)`
- `to_stage :: lead_stage (USER-DEFINED, NOT NULL)`
- `moved_by :: uuid` – usuário que arrastou o card no Kanban.
- `reason :: text` – motivo opcional.
- `created_at :: timestamptz` (default `now()`)

**Uso:** alimentar gráficos de funil, métricas de tempo por etapa, logs.

---

### 3.3 Tabela/visão `lead_stage_spans`

**Propósito:** visão agregada de quanto tempo o lead ficou em cada estágio.

**Campos:**

- `org_id :: uuid`
- `lead_id :: uuid`
- `stage :: lead_stage (USER-DEFINED)`
- `entered_at :: timestamptz`
- `next_change_at :: timestamptz`
- `duration_days :: numeric`

**Observação:** provavelmente uma VIEW ou tabela preenchida por job; usada nos painéis Kanban.

---

### 3.4 Tabela `lead_interesses`

**Propósito:** registrar interesses de produto por lead (por ex. mais de um tipo de carta/produto).

**Campos principais:**

- `id :: uuid` (PK)
- `org_id :: uuid`
- `lead_id :: uuid`
- `produto :: produto (USER-DEFINED)` – tipo de consórcio (imóvel, auto, serviço etc.).
- `valor_total :: numeric`
- `prazo_meses :: integer`
- `objetivo :: text` – descrição em linguagem natural do objetivo.
- `perfil_desejado :: USER-DEFINED` – perfil da cota desejada (ex.: agressivo, conservador etc.).
- `status :: text` (default `'aberto'`) – aberto, convertido, perdido etc.
- `linked_cota_id :: uuid` – cota vinculada (quando se concretiza).
- `observacao :: text`
- `closed_at :: timestamptz`
- `created_by :: uuid`
- `created_at :: timestamptz` (default `now()`)
- `updated_at :: timestamptz` (default `now()`)

---

### 3.5 Tabela `lead_diagnosticos`

**Propósito:** consolidar o diagnóstico consultivo completo de cada lead, com parte financeira, perfil e saídas do motor preditivo.

**Campos principais (agrupados):**

- Identidade:
  - `id :: uuid` (PK)
  - `org_id :: uuid` (NOT NULL)
  - `lead_id :: uuid` (NOT NULL)

- Objetivo & contexto:
  - `objetivo :: text`
  - `prazo_meta_meses :: integer`
  - `preferencia_produto :: text`
  - `regiao_preferencia :: text`

- Capacidade financeira:
  - `renda_mensal :: numeric`
  - `reserva_inicial :: numeric`
  - `comprometimento_max_pct :: numeric`
  - `renda_provada :: boolean` (default `false`)

- Configuração de carta alvo:
  - `valor_carta_alvo :: numeric`
  - `prazo_alvo_meses :: integer`

- Estratégia de lance (input + recomendações):
  - `estrategia_lance :: text`
  - `lance_base_pct :: numeric`
  - `lance_max_pct :: numeric`
  - `janela_preferida_semanas :: integer`

- Scores & probabilidades:
  - `score_risco :: integer`
  - `readiness_score :: integer`
  - `prob_conversao :: numeric`
  - `prob_contemplacao_short :: numeric`
  - `prob_contemplacao_med :: numeric`
  - `prob_contemplacao_long :: numeric`

- LGPD & extras:
  - `consent_scope :: text`
  - `consent_ts :: timestamp` (sem timezone)
  - `extras :: jsonb` – campo flexível para modelos futuros / versões.

- Sistema:
  - `created_at :: timestamp` (default `now()`)
  - `updated_at :: timestamp` (default `now()`)

> **Ponto importante:** essa tabela conversa diretamente com o endpoint de IA
> (`POST /diagnostico`) e com a tela de diagnóstico no app. Mudanças aqui
> impactam modelo, API e UI ao mesmo tempo.

---

### 3.6 Tabela `activities`

**Propósito:** organizar tarefas e compromissos (agenda de vendas).

**Campos:**

- `id :: uuid` (PK)
- `org_id :: uuid`
- `lead_id :: uuid`
- `tipo :: USER-DEFINED` – exemplo: ligação, reunião, visita, WhatsApp.
- `assunto :: text`
- `conteudo :: text`
- `due_at :: timestamptz` – data prevista.
- `done :: boolean` (default `false`)
- `done_at :: timestamptz`
- `created_by :: uuid`
- `created_at :: timestamptz` (default `now()`)

---

### 3.7 Tabela `notes`

**Propósito:** notas livres associadas ao lead.

**Campos:**

- `id :: uuid` (PK)
- `org_id :: uuid`
- `lead_id :: uuid`
- `body :: text` (NOT NULL)
- `created_by :: uuid`
- `created_at :: timestamptz` (default `now()`)

---

### 3.8 Tabela `attachments`

**Propósito:** anexos de documentos (RG, comprovante de residência, PDFs etc.).

**Campos:**

- `id :: uuid` (PK)
- `org_id :: uuid`
- `lead_id :: uuid`
- `storage_path :: text` (NOT NULL) – caminho no Supabase Storage.
- `filename :: text`
- `mime_type :: text`
- `size_bytes :: integer`
- `uploaded_by :: uuid`
- `created_at :: timestamptz` (default `now()`)

---

### 3.9 Tabela `consent_logs`

**Propósito:** manter um log imutável de consentimentos (LGPD).

**Campos:**

- `id :: bigint` (PK, `serial`)
- `lead_id :: uuid`
- `consentimento :: boolean` (NOT NULL)
- `scope :: text` – o que foi autorizado (ex.: “whatsapp_marketing”, “email_newsletter”).
- `ip :: text`
- `user_agent :: text`
- `created_at :: timestamptz` (default `now()`)

---

### 3.10 Tabela `landing_pages`

**Propósito:** cadastro das landing pages ligadas à corretora, controle de segurança e UTM default.

**Campos:**

- `id :: uuid` (PK)
- `org_id :: uuid` (NOT NULL)
- `owner_user_id :: uuid` (NOT NULL)
- `slug :: text` – slug da LP (ex.: `autentika-imoveis`).
- `public_hash :: text` (NOT NULL) – hash público para uso seguro em integrações.
- `utm_defaults :: jsonb` – UTM padrão caso a origem não envie.
- `active :: boolean` (default `true`)
- `webhook_secret :: varchar` – segredo para assinatura de webhooks.
- `allowed_domains :: text[]` – domínios permitidos para origem do POST.
- `created_at :: timestamptz` (default `now()`)

---

## 4. Consórcio & operação

### 4.1 Tabela `administradoras`

**Propósito:** manter cadastro das administradoras de consórcio.

**Campos:**

- `id :: uuid` (PK)
- `org_id :: uuid` – se cada corretora tiver seu próprio cadastro.
- `nome :: text` (NOT NULL)
- `cnpj :: text`
- `site :: text`
- `created_at :: timestamptz` (default `now()`)

---

### 4.2 Tabela `grupos`

**Propósito:** representar grupos de consórcio por administradora.

**Campos:**

- `id :: uuid` (PK)
- `org_id :: uuid`
- `administradora_id :: uuid`
- `codigo :: text` – código do grupo.
- `produto :: USER-DEFINED` – tipo de produto (mesmo enum usado em `cotas`).
- `assembleia_dia :: integer` – dia do mês da assembleia.
- `observacoes :: text`
- `created_at :: timestamptz` (default `now()`)

---

### 4.3 Tabela `cotas`

**Propósito:** representar as cotas contratadas pelos clientes (ligadas a leads/grupos/administradoras).

**Campos principais:**

- Identidade:
  - `id :: uuid` (PK)
  - `org_id :: uuid`
  - `lead_id :: uuid`
  - `administradora_id :: uuid`

- Dados da cota:
  - `numero_cota :: text` (NOT NULL)
  - `grupo_codigo :: text` (NOT NULL)
  - `valor_carta :: numeric`
  - `produto :: USER-DEFINED` (NOT NULL) – tipo de consórcio.
  - `situacao :: text` (default `'ativa'`) – ativa, cancelada, contemplada etc.
  - `data_adesao :: date`
  - `assembleia_dia :: integer`
  - `observacoes :: text`

- Financeiro:
  - `valor_parcela :: numeric`
  - `prazo :: integer`
  - `forma_pagamento :: text`
  - `indice_correcao :: text`
  - `parcela_reduzida :: boolean` (default `false`)
  - `percentual_reducao :: numeric`
  - `valor_parcela_sem_redutor :: numeric`
  - `taxa_admin_percentual :: numeric`
  - `taxa_admin_valor_mensal :: numeric`

- Regras & permissões:
  - `embutido_permitido :: boolean` (default `false`)
  - `embutido_max_percent :: numeric`
  - `fgts_permitido :: boolean` (default `false`)
  - `autorizacao_gestao :: boolean` (default `false`)
  - `furo_meses :: integer`
  - `tipo_lance_preferencial :: USER-DEFINED`
  - `data_ultimo_lance :: date`
  - `aporte :: numeric`
  - `objetivo :: text`
  - `estrategia :: text`

- Sistema:
  - `created_at :: timestamptz` (default `now()`)

---

### 4.4 Tabela `lances`

**Propósito:** registrar lances dados em assembleias para cada cota.

**Campos principais:**

- `id :: uuid` (PK)
- `org_id :: uuid`
- `cota_id :: uuid`
- `tipo :: USER-DEFINED` – tipo de lance (livre, embutido, FGTS etc.).
- `percentual :: numeric`
- `valor :: numeric`
- `origem :: text` (default `'planejado'`) – planejado vs efetivamente enviado.
- `assembleia_data :: date`
- `base_calculo :: lance_base_calc (USER-DEFINED, default 'saldo_devedor')`
- `pagamento :: jsonb` – detalhes do pagamento do lance.
- `resultado :: text` – contemplado, não contemplado, desclassificado etc.
- `created_by :: uuid`
- `created_at :: timestamptz` (default `now()`)

---

### 4.5 Tabela `contemplacoes`

**Propósito:** representar a contemplação de uma cota (quando há).

**Campos:**

- `id :: uuid` (PK)
- `org_id :: uuid`
- `cota_id :: uuid`
- `motivo :: USER-DEFINED` – sorteio, lance, sobra de caixa etc.
- `lance_percentual :: numeric`
- `data :: date` (NOT NULL) – data da contemplação.
- `created_at :: timestamptz` (default `now()`)

---

## 5. Negócios, propostas e contratos

### 5.1 Tabela `deals`

**Propósito:** representar oportunidades no funil comercial (Kanban de negócios).

**Campos:**

- `id :: uuid` (PK)
- `org_id :: uuid`
- `lead_id :: uuid`
- `titulo :: text`
- `status :: deal_status (USER-DEFINED, default 'aberto')`
- `motivo_perda :: text`
- `valor_carta :: numeric`
- `prazo_meses :: integer`
- `administradora :: text` – nome textual (além do id).
- `created_by :: uuid`
- `closed_at :: timestamptz`
- `created_at :: timestamptz` (default `now()`)
- `updated_at :: timestamptz` (default `now()`)

---

### 5.2 Tabela `propostas`

**Propósito:** propostas de consórcio geradas no sistema (inclui resultado da simulação e PDF).

**Campos:**

- `id :: uuid` (PK)
- `org_id :: uuid`
- `lead_id :: uuid`
- `deal_id :: uuid`
- `tipo :: USER-DEFINED` – tipo de proposta (produto).
- `valor_carta :: numeric` (NOT NULL)
- `prazo_meses :: integer` (NOT NULL)
- `taxa_admin :: numeric`
- `indexador :: text`
- `resultado :: jsonb` – resultado completo da simulação (parcelas, totais).
- `generated_pdf_path :: text` – caminho do PDF no Storage.
- `created_by :: uuid`
- `created_at :: timestamptz` (default `now()`)

---

### 5.3 Tabela `contratos`

**Propósito:** contratos efetivamente assinados (pós-fechamento do deal).

**Campos:**

- `id :: uuid` (PK)
- `org_id :: uuid`
- `deal_id :: uuid`
- `cota_id :: uuid`
- `numero :: text` – número do contrato.
- `data_assinatura :: date`
- `status :: text` (default `'ativo'`)
- `pdf_path :: text` – caminho do contrato assinado.
- `created_at :: timestamptz` (default `now()`)

---

### 5.4 Tabela `pagamentos`

**Propósito:** registrar pagamentos ligados a contratos (comissão, taxa, repasses etc.).

**Campos:**

- `id :: uuid` (PK)
- `org_id :: uuid`
- `contrato_id :: uuid`
- `tipo :: text` (NOT NULL) – tipo de pagamento.
- `competencia :: date` – competência / mês de referência.
- `valor :: numeric` (NOT NULL)
- `pago_em :: timestamptz`
- `created_at :: timestamptz` (default `now()`)

---

## 6. Infraestrutura, eventos e métricas

### 6.1 Tabela `event_outbox`

**Propósito:** implementar o padrão **outbox** para envio confiável de eventos para Twilio, Postmark, tarefas do FastAPI etc.

**Campos:**

- `id :: bigint` (PK, `serial`)
- `org_id :: uuid` (NOT NULL)
- `event_type :: text` (NOT NULL) – ex.: `lead_created`, `assembleia_soon`.
- `aggregate_type :: text` – tipo de agregado (lead, cota, proposta etc.).
- `aggregate_id :: uuid`
- `payload :: jsonb` (NOT NULL, default `'{}'`) – dados do evento.
- `status :: text` (NOT NULL, default `'pending'`) – `pending`, `processing`, `done`, `error`.
- `created_at :: timestamptz` (NOT NULL, default `now()`)
- `processed_at :: timestamptz`

---

### 6.2 Tabela `audit_logs`

**Propósito:** trilha de auditoria do sistema.

**Campos:**

- `id :: bigint` (PK, `serial`)
- `org_id :: uuid`
- `actor_id :: uuid` – usuário que executou a ação.
- `entity :: text` – nome da entidade afetada (ex.: `lead`, `cota`).
- `entity_id :: uuid`
- `action :: text` – tipo de ação (create, update, delete etc.).
- `diff :: jsonb` – antes/depois ou campos alterados.
- `created_at :: timestamptz` (default `now()`)

---

### 6.3 Tabelas/visões `kanban_*`

Todas com padrão: métricas agregadas por `org_id` e estágio/etapa.

- `kanban_avg_days`
  - `org_id :: uuid`
  - `stage :: lead_stage (USER-DEFINED)`
  - `avg_days :: numeric`

- `kanban_conversion`
  - `org_id :: uuid`
  - `stage :: lead_stage (USER-DEFINED)`
  - `conversion_pct :: numeric`

- `kanban_diag_completion`
  - `org_id :: uuid`
  - `etapa :: lead_stage (USER-DEFINED)`
  - `diagnostic_completion_pct :: numeric`

- `kanban_readiness_avg`
  - `org_id :: uuid`
  - `etapa :: lead_stage (USER-DEFINED)`
  - `readiness_avg :: numeric`

- `kanban_tfirstcontact_avg`
  - `org_id :: uuid`
  - `etapa :: lead_stage (USER-DEFINED)`
  - `t_first_contact_avg_min :: numeric`

> **Uso:** alimentar dashboards (GA4/Data Studio/Next dashboard) sem precisar
> recalcular métricas pesadas a cada request.

## 7. Relacionamentos & Regras de Integridade

Esta seção resume como as tabelas se relacionam entre si (FOREIGN KEYS) e quais
regras de negócio estão “travadas” via UNIQUE INDEX e CHECK.

### 7.1. Visão geral dos relacionamentos

**Núcleo multi-tenant**

- `orgs.id`
  - Referenciado por:
    - `profiles.org_id`
    - `leads.org_id`
    - `activities.org_id`
    - `attachments.org_id`
    - `administradoras.org_id`
    - `cotas.org_id`
    - `grupos.org_id`
    - `lances.org_id`
    - `lead_diagnosticos.org_id`
    - `lead_interesses.org_id`
    - `notes.org_id`
    - `deals.org_id`
    - `propostas.org_id`
    - `contratos.org_id`
    - `pagamentos.org_id`
    - `event_outbox.org_id`

- `profiles.user_id`
  - Referenciado por:
    - `activities.created_by`
    - `attachments.uploaded_by`
    - `deals.created_by`
    - `lances.created_by`
    - `landing_pages.owner_user_id`
    - `lead_interesses.created_by`
    - `lead_stage_history.moved_by`
    - `leads.created_by`
    - `leads.owner_id`
    - `notes.created_by`
    - `orgs.owner_user_id`
    - `propostas.created_by`

> **Regra prática:** praticamente tudo ponta para `orgs` e diversos registros
> de ação/ownership apontam para `profiles`. Se quebrar essas FKs, você quebra o
> multi-tenant e o controle de dono.

---

### 7.2. Domínio Leads & CRM

#### 7.2.1. `leads`

**FOREIGN KEYS:**

- `leads.org_id → orgs.id`
- `leads.owner_id → profiles.user_id`
- `leads.created_by → profiles.user_id`
- `leads.landing_id → landing_pages.id`

**CHECKS importantes:**

- `leads_contact_at_least_one`  
  Garante que o lead tenha **pelo menos um contato** (telefone ou e-mail).

**Índices & unicidade:**

- `leads_pkey` → PK (`id`)
- `idx_leads_org_etapa` → filtro rápido por org + etapa (Kanban).
- `idx_leads_owner` → filtro por dono (carteira do vendedor).
- `idx_leads_created` / `idx_leads_created_by` → ordenações de histórico.
- `leads_contato_unique (org_id, telefone, email)`  
  → impede duplicar o mesmo contato na mesma org.

> **Impacto em regra de negócio:**  
> - Não permitir dois leads iguais com mesmo telefone+email dentro da mesma corretora.  
> - Sempre garantir ao menos um canal de contato preenchido.

---

#### 7.2.2. `activities`

**FOREIGN KEYS:**

- `activities.org_id → orgs.id`
- `activities.lead_id → leads.id`
- `activities.created_by → profiles.user_id`

**Índices:**

- `activities_pkey` → PK.
- `idx_acts_lead` → listar atividades por lead.
- `idx_acts_lead_due` → agenda (lead + due_at).
- `idx_acts_org_tipo` → dashboards por org + tipo de atividade.

---

#### 7.2.3. `notes`

**FOREIGN KEYS:**

- `notes.org_id → orgs.id`
- `notes.lead_id → leads.id`
- `notes.created_by → profiles.user_id`

**Índices:**

- `notes_pkey` → PK.
- `idx_notes_lead` → notas por lead.

---

#### 7.2.4. `attachments`

**FOREIGN KEYS:**

- `attachments.org_id → orgs.id`
- `attachments.lead_id → leads.id`
- `attachments.uploaded_by → profiles.user_id`

**Índices:**

- `attachments_pkey` → PK.
- `idx_attach_lead` → anexos por lead.

---

#### 7.2.5. `consent_logs`

**FOREIGN KEYS:**

- `consent_logs.lead_id → leads.id`

**Índices:**

- `consent_logs_pkey` → PK.
- `idx_consent_lead` → recuperar histórico de consentimento de um lead.

---

#### 7.2.6. `lead_stage_history`

**FOREIGN KEYS:**

- `lead_stage_history.lead_id → leads.id`
- `lead_stage_history.moved_by → profiles.user_id`

**Índices:**

- `lead_stage_history_pkey` → PK.
- `idx_lsh_lead` / `idx_stagehist_lead` → histórico de estágios por lead.

---

#### 7.2.7. `lead_interesses`

**FOREIGN KEYS:**

- `lead_interesses.org_id → orgs.id`
- `lead_interesses.lead_id → leads.id`
- `lead_interesses.linked_cota_id → cotas.id`
- `lead_interesses.created_by → profiles.user_id`

**Índices:**

- `lead_interesses_pkey` → PK.
- `idx_interesses_org` → interesses por org.
- `idx_interesses_lead_status` → interesses por lead + status (aberto/fechado).

---

#### 7.2.8. `lead_diagnosticos`

**FOREIGN KEYS:**

- `lead_diagnosticos.org_id → orgs.id`
- `lead_diagnosticos.lead_id → leads.id`

**Índices:**

- `lead_diagnosticos_pkey` → PK.
- `idx_lead_diag_org` → diagnósticos por org.
- `idx_lead_diag_lead` → diagnóstico por lead.

> **Boas práticas:** manter 1 diagnóstico “ativo” por lead ou controlar versões
> via app/API para não gerar duplicidade sem intenção.

---

#### 7.2.9. `landing_pages`

**FOREIGN KEYS:**

- `landing_pages.org_id → orgs.id`
- `landing_pages.owner_user_id → profiles.user_id`

**Índices & unicidade:**

- `landing_pages_pkey` → PK.
- `idx_landings_owner` → LPs por owner.
- `unq_landing_hash` → garante `public_hash` único.
- `unq_landing_slug` → garante `slug` único.

> Isso permite usar tanto o `slug` quanto o `public_hash` em URLs/API com
> segurança, sem colisões.

---

### 7.3. Consórcio & Operação

#### 7.3.1. `administradoras`

**FOREIGN KEYS:**

- `administradoras.org_id → orgs.id`

**Índices & unicidade:**

- `administradoras_pkey` → PK.
- `administradora_nome_unique` → nome único da administradora
  (por base – cuidado ao renomear).

---

#### 7.3.2. `grupos`

**FOREIGN KEYS:**

- `grupos.org_id → orgs.id`
- `grupos.administradora_id → administradoras.id`

**Índices & unicidade:**

- `grupos_pkey` → PK.
- `unq_grupo_admin_codigo (administradora_id, codigo)`  
  → garante que uma administradora não tenha dois grupos com o mesmo código.

---

#### 7.3.3. `cotas`

**FOREIGN KEYS:**

- `cotas.org_id → orgs.id`
- `cotas.lead_id → leads.id`
- `cotas.administradora_id → administradoras.id`

**Índices:**

- `cotas_pkey` → PK.
- `idx_cotas_lead` → cotas por lead.
- `idx_cotas_org_situacao` → filtros por org + situação (ativa/cancelada/etc).

> **Observação:** unicidade de `numero_cota` + `grupo_codigo` não está explícita
> em índice único; se virar requisito de negócio, vale criar um
> `UNIQUE (grupo_codigo, numero_cota)` numa próxima migration.

---

#### 7.3.4. `lances`

**FOREIGN KEYS:**

- `lances.org_id → orgs.id`
- `lances.cota_id → cotas.id`
- `lances.created_by → profiles.user_id`

**Índices & unicidade:**

- `lances_pkey` → PK.
- `idx_lances_cota` → lances por cota.
- `unq_lance_cota_data (cota_id, assembleia_data)`  
  → garante no máximo **um lance por cota por assembleia**.

---

#### 7.3.5. `contemplacoes`

**FOREIGN KEYS:**

- `contemplacoes.org_id → orgs.id`
- `contemplacoes.cota_id → cotas.id`

**Índices & unicidade:**

- `contemplacoes_pkey` → PK.
- `idx_cont_cota` → contemplações por cota.
- `unq_contemplacao_cota (cota_id)`  
  → garante **no máximo uma contemplação registrada por cota.**

---

### 7.4. Negócios, Propostas e Contratos

#### 7.4.1. `deals`

**FOREIGN KEYS:**

- `deals.org_id → orgs.id`
- `deals.lead_id → leads.id`
- `deals.created_by → profiles.user_id`

**Índices:**

- `deals_pkey` → PK.
- `idx_deals_lead` → deals por lead.
- `idx_deals_org_status` → deals por org + status (aberto, ganho, perdido).

---

#### 7.4.2. `propostas`

**FOREIGN KEYS:**

- `propostas.org_id → orgs.id`
- `propostas.lead_id → leads.id`
- `propostas.deal_id → deals.id`
- `propostas.created_by → profiles.user_id`

**Índices:**

- `propostas_pkey` → PK.
- `idx_props_deal` → propostas por deal.
- `idx_props_lead` / `idx_prop_lead_created` → propostas por lead e ordenação por data.

---

#### 7.4.3. `contratos`

**FOREIGN KEYS:**

- `contratos.org_id → orgs.id`
- `contratos.deal_id → deals.id`
- `contratos.cota_id → cotas.id`

**Índices:**

- `contratos_pkey` → PK.
- `idx_contratos_deal` → contratos por deal.

> **Fluxo típico:** `lead → deal → proposta(s) → contrato → pagamentos`.

---

#### 7.4.4. `pagamentos`

**FOREIGN KEYS:**

- `pagamentos.org_id → orgs.id`
- `pagamentos.contrato_id → contratos.id`

**Índices:**

- `pagamentos_pkey` → PK.
- `idx_pgto_contrato` → pagamentos por contrato.
- `idx_pgto_comp` → filtros por competência (mês/ano).

---

### 7.5. Infraestrutura & Métricas

#### 7.5.1. `event_outbox`

**FOREIGN KEYS:**

- `event_outbox.org_id → orgs.id`

**Índices:**

- `event_outbox_pkey` → PK.
- `idx_outbox_org` → eventos por org.
- `idx_outbox_status_created (status, created_at)`  
  → processamento eficiente do outbox (buscar `pending` mais antigos primeiro).

---

#### 7.5.2. `audit_logs`

**FOREIGN KEYS:**

- *Sem FKs explícitas*, mas campos:
  - `org_id :: uuid` – referencia org.
  - `actor_id :: uuid` – geralmente referência a `profiles.user_id`.
  - `entity :: text` / `entity_id :: uuid` – referenciam entidades de negócio de forma lógica.

**Índices:**

- `audit_logs_pkey` → PK.

---

### 7.6. `orgs` e `profiles` (relação especial)

- `orgs.owner_user_id → profiles.user_id`  
  Garante que toda organização tenha **um dono** vinculado a um profile.

- `profiles.org_id → orgs.id`  
  Garante que todo profile pertença a uma organização.

> **Cuidado ao deletar:** apagar uma `org` ou um `profile` sem tratar
> dependências pode quebrar muitas FKs. Qualquer remoção deve ser feita via
> jobs de “soft delete” ou rotinas específicas que cascatiem tudo com segurança.

---

### 7.7. Regras de negócio importantes amarradas em índices/constraints

- **Lead único por contato na mesma org**
  - `leads_contato_unique (org_id, telefone, email)`

- **Pelo menos um canal de contato**
  - CHECK `leads_contact_at_least_one` em `leads`.

- **Uma contemplação por cota**
  - `unq_contemplacao_cota (cota_id)` em `contemplacoes`.

- **Um lance por cota por assembleia**
  - `unq_lance_cota_data (cota_id, assembleia_data)` em `lances`.

- **Um grupo por código por administradora**
  - `unq_grupo_admin_codigo (administradora_id, codigo)` em `grupos`.

- **Slug e hash únicos de landing page**
  - `unq_landing_slug (slug)`
  - `unq_landing_hash (public_hash)` em `landing_pages`.

- **Nome de administradora único**
  - `administradora_nome_unique (nome)` em `administradoras`.

> **Resumo:** antes de mexer em qualquer coluna ou regra dessas,
> sempre pergunte:  
> “**Qual regra de negócio esse índice/constraint está protegendo?**”
> e atualize o app, migrations e este guia em conjunto.

## 8. Segurança, Auth & Row Level Security (RLS)

### 8.1. Visão geral

Todo o modelo é pensado como **multi-tenant por organização (`orgs`)**, com:

- isolamento por `org_id` em praticamente todas as tabelas;
- controles de acesso baseados em:
  - **claims do JWT** (`org_id`, `role`, `sub`) e
  - **perfil na tabela `profiles`** (papel real do usuário na org).

Há duas famílias de funções de auth:

- Funções `auth.*` (nativas do Supabase + helpers)
- Funções `public.*` de conveniência para RLS e regras de negócio

---

### 8.2. Helpers de Auth (JWT & contexto)

#### 8.2.1. Funções no schema `auth`

- `auth.uid() :: uuid`  
  Retorna o `sub` do JWT (ID do usuário logado).

- `auth.email() :: text`  
  Retorna o e-mail do usuário a partir das claims do JWT.

- `auth.role() :: text`  
  Lê a claim `role` do JWT (útil em triggers/RLS mais simples).

- `auth.jwt() :: jsonb`  
  Retorna o JSON cru das claims do JWT.

> Estas funções são usadas principalmente em triggers (`trg_leads_etapa_history`) e em algumas policies legadas.

#### 8.2.2. Funções no schema `public` (auth helpers)

- `public.jwt() :: jsonb`  
  Versão “segura” para pegar o JWT (retorna `{}` se não houver claim).

- `public.app_uid() :: uuid`  
  Lê `sub` diretamente de `request.jwt.claims`.

- `public.app_role() :: text`  
  Retorna `role` do JWT (ex.: `'owner' | 'admin' | 'gestor' | 'vendedor' | 'viewer'`).

- `public.app_auth_org_id() :: uuid`  
  Lê `org_id` do JWT (campo `org_id` na claim).

- `public.app_org_id() :: uuid`  
  Versão mais resiliente que tenta `org_id` e `orgId` dentro de `public.jwt()`.

- `public.auth_org_id() :: uuid`  
  Resolve a org **via tabela `profiles`**:
  ```sql
  select org_id from public.profiles where user_id = auth.uid();
  ```
Útil quando não queremos depender do org_id no JWT.
- `public.app_is_manager() :: boolean`
Retorna `true` se o papel do JWT for `admin` ou `gestor`.

- `public.can_manage_org(target_org uuid) :: boolean`
Retorna `true` quando:

o usuário pertence à organização (`app_auth_org_id() = target_org`), e

é gestor/admin (`app_is_manager()`).

### 8.3. RLS por domínio/tabela

Abaixo está o resumo humano das principais policies RLS usadas no banco.

---

### 8.3.1. Leads (`leads`)

#### 🔒 Isolamento por organização
Policies `leads org read/insert/update/delete` garantem que:

- somente leads onde `org_id = auth_org_id()` são visíveis.

#### 👤 Controle por papel/carteira

**Leitura:**
- Gestor/Admin → vê todos os leads da organização.  
- Viewer → vê todos os leads da organização (somente leitura).  
- Vendedor → vê apenas leads onde `owner_id = app_uid()`.  
- Proprietário da carteira sempre vê seus próprios leads (`leads_owner_select`).  

**Escrita:**
- Gestor/Admin → pode editar qualquer lead da organização.
- Vendedor → só pode criar/editar leads da própria carteira.
- Delete → somente admin/owner.

Resumo:
- Gestor/Admin: CRUD total.
- Vendedor: CRUD apenas da própria carteira.
- Viewer: somente leitura.

---

### 8.3.2. Atividades/Notas/Anexos/Consentimento/Histórico

Tabelas:
- `activities`
- `notes`
- `attachments`
- `consent_logs`
- `lead_stage_history`

Todas seguem o padrão:

```sql
EXISTS (
  SELECT 1
  FROM leads l
  WHERE l.id = <tabela>.lead_id
    AND (
      l.owner_id = auth.uid()
      OR (l.org_id = app_auth_org_id() AND app_is_manager())
    )
);
```

Regras:

- Se o usuário pode ver o lead → pode ver registros relacionados.
- Se pode editar o lead → pode editar registros relacionados.

---

### 8.3.3. Diagnóstico (`lead_diagnosticos`)

Regras principais:

**Leitura:**

Usuário só vê diagnósticos da própria organização (`org_id = app_auth_org_id()`)

ou ligados a leads acessíveis da org.

**Criação/Update:**

Permitido apenas quando:

- `org_id = app_auth_org_id()`, e
- `lead_id` pertence a um lead da mesma organização.

**Delete:**

Permitido somente para admin/gestor (via `app_is_manager()`).

---

### 8.3.4. Negócios e propostas

### Deals (`deals`)

```sql
EXISTS (
  SELECT 1
  FROM leads l
  WHERE l.id = deals.lead_id
    AND (
      l.owner_id = auth.uid()
      OR (l.org_id = app_auth_org_id() AND app_is_manager())
    )
);

```

### Propostas (`propostas`)

```sql
EXISTS (
  SELECT 1
  FROM deals d
  JOIN leads l ON l.id = d.lead_id
  WHERE d.id = propostas.deal_id
    AND (
      l.owner_id = auth.uid()
      OR (l.org_id = app_auth_org_id() AND app_is_manager())
    )
);

```

### Contratos (`contratos`) e Pagamentos (`pagamentos`)

- Acesso garantido por `org_id = auth_org_id()`.
- Controle fino normalmente feito pela API (apenas gestores acessam via UI).

---

### 8.3.5. Consórcio: administradoras, grupos, cotas, lances, contemplações

Tabelas:

- `administradoras`
- `grupos`
- `cotas`
- `lances`
- `contemplacoes`

Todas possuem políticas:

- `_org read`
- `_org insert`
- `_org update`
- `_org delete`

Regras:

- Usuário só vê registros com `org_id = auth_org_id()`.
- Em geral somente gestores usam estas rotas na UI.
- Se desejar endurecer no futuro, basta exigir `app_is_manager()`.

---

### 8.3.6. Landing Pages (`landing_pages`)

Policies principais:

- `landings_owner_select`
    
    → Dono da landing (`owner_user_id = auth.uid()`) pode ler.
    
- `landings_org_manager_select`
    
    → Gestor/Admin pode ler todas da org:
    
    ```sql
    (org_id = app_auth_org_id()) AND app_is_manager();
    
    ```
    
- `landings_owner_write` / `landings_write`
    
    → Escrita permitida se for:
    
    - o próprio owner da LP, ou
    - gestor/admin da organização.

Resumo:

- Vendedor edita apenas suas próprias LPs.
- Admin/Gestor gerencia todas.

---

### 8.3.7. Perfis e organizações (`profiles`, `orgs`)

### `orgs`

Policies `orgs_select` / `orgs_read`:

- Usuário só vê orgs onde tem role `'admin'` ou `'gestor'`.
- Ou a org atual, se for admin/gestor.

### `profiles`

Baseado em `can_manage_org(org_id)`:

- Admin/Gestor podem:
    - criar perfis
    - editar perfis
    - excluir perfis
    - listar todos os perfis da org

Policies importantes:

- `profiles.select.self` → usuário vê apenas seu próprio perfil.
- `profiles.update.self` → usuário atualiza apenas seu próprio perfil.

Regras extras via trigger `profiles_guard()`:

- Dono da organização **nunca pode perder** papel de admin.
- Nunca pode existir org sem admin.
- Impede apagar o último admin.

---

### 8.3.8. Logs & Outbox

### `audit_logs`

Somente admin/gestor pode consultar:

```sql
(org_id = app_auth_org_id()) AND app_is_manager();

```

### `event_outbox`

- Tabela técnica.
- Normalmente acessada via Service Role ou backend.

### 8.4. Funções de negócio e métricas

A seguir estão as principais funções SQL utilizadas pela aplicação para cálculos,
diagnósticos e dashboards internos.

---

### 8.4.1. `get_kanban_metrics(p_org uuid) :: jsonb`

Função STABLE que calcula métricas por etapa dos leads.

Para cada etapa, retorna:

- `count` — quantidade de leads
- `avgDays` — dias médios desde a criação
- `diagnosticCompletionPct` — % de leads com diagnóstico “completo”
- `readinessAvg` — média do readiness score da etapa
- `tFirstContactAvgMin` — tempo médio até o primeiro contato (minutos)

Além disso, calcula:

- `conversion` — % global de leads em etapa `contrato` ou `ativo`

Uso:

```sql
select public.get_kanban_metrics(app_auth_org_id());
```
Retorno (exemplo simplificado):

```json
[
  {
    "etapa": "novo",
    "count": 12,
    "avgDays": 1.4,
    "diagnosticCompletionPct": 0.22,
    "readinessAvg": 48,
    "tFirstContactAvgMin": 62,
    "conversion": 12.5
  }
]

```

---

### 8.4.2. `get_lance_otimo(p_lead uuid) :: numeric`

Função STABLE usada para calcular a recomendação de lance ideal (como % do crédito),

baseado no diagnóstico do lead.

Regras:

- Base: 20% (`v_base = 0.20`)
- Se `readiness_score >= 75` → +5p.p. → 25%
- Se `readiness_score <= 40` → -5p.p. → 15%
- Nunca abaixo de 0% (`greatest(0,...)`)

Uso:

```sql
select public.get_lance_otimo('<lead_id>');

```

Retorno é uma fração, por exemplo:

- `0.25` → 25%
- `0.15` → 15%

---

### 8.4.3. Funções auxiliares diversas

- `public.set_updated_at()`
    
    Atualiza `updated_at = now()` automaticamente em updates.
    
- `public.tg_touch_updated_at()`
    
    Similar, usada em triggers de atualização.
    
- `public.admin_count(target_org uuid)`
    
    Conta quantos admins existem na organização.
    
- `public.is_owner(target_org uuid, target_user uuid)`
    
    Verifica se determinado usuário é o dono da organização.
    

```
---

# ✅ 8.5. Triggers (Markdown puro)

```

### 8.5. Triggers de segurança e integridade

---

### 8.5.1. `orgs_owner_immutable()`

Trigger que impede modificar o campo `owner_user_id` após criação.

Trecho relevante:

```sql
if new.owner_user_id is distinct from old.owner_user_id then
  raise exception 'owner_user_id é imutável.';
end if;

```

Protege contra mudança acidental de dono da corretora.

---

### 8.5.2. `profiles_guard()`

Trigger crítico de segurança para manter a integridade dos perfis da organização.

Regras aplicadas:

1. **Dono da org sempre é admin**
    
    Se tentar rebaixar o dono (alterar role) → erro.
    
2. **Nunca permitir que a organização fique sem admin**
    - UPDATE de admin → outro role
    - DELETE de admin
        
        → se admin_count(org) <= 1 → erro.
        
3. Protege contra remoção acidental do último admin.

---

### 8.5.3. `set_updated_at()` e `tg_touch_updated_at()`

Triggers genéricos usados pelo padrão:

```sql
NEW.updated_at = now();

```

Garantem consistência automática de timestamps.

---

### 8.5.4. `trg_leads_etapa_history()`

Trigger que cria histórico e publica eventos ao mudar a etapa de um lead.

Quando `NEW.etapa` ≠ `OLD.etapa`:

1. Adiciona registro em `lead_stage_history`
    - `from_stage`
    - `to_stage`
    - `moved_by` (tentando ler `auth.uid()`)
    - `created_at = now()`
2. Publica evento no `event_outbox`:

```sql
insert into public.event_outbox
(org_id, event_type, aggregate_type, aggregate_id, payload)
values (
  NEW.org_id,
  'stage_changed',
  'lead',
  NEW.id,
  jsonb_build_object(
    'from', OLD.etapa,
    'to', NEW.etapa,
    'at', now(),
    'actor', v_actor
  )
);

```

Permite ao backend criar automações como:

- envio de WhatsApp ao mudar etapa
- e-mail automático
- integrações externas
- dashboards reativos

```
---

# ✅ 8.6. TL;DR da Segurança (Markdown puro)

```

### 8.6. TL;DR da Segurança

Resumo do modelo completo de segurança da Autentika Seguros:

---

### 🔒 Isolamento por organização

Praticamente todas as tabelas têm:

- `org_id`
- policies `_org read/insert/update/delete`
- funções helper `auth_org_id()` / `app_auth_org_id()`

O usuário **nunca enxerga dados de outra organização**.

---

### 👤 Camadas de papel

- **Owner**
    
    Dono da org, sempre admin (forçado por trigger).
    
- **Admin/Gestor**
    
    Acesso 360º: leads, LPs, perfis, times, relatórios.
    
- **Vendedor**
    
    CRUD completo **apenas** nos seus próprios leads e artefatos relacionados.
    
- **Viewer**
    
    Somente leitura.
    

---

### 🧩 Perfis e organizações são rigidamente protegidos

- Não dá para remover o último admin.
- Não dá para rebaixar o dono da organização.
- Gestão de equipe (`profiles`) só para admin/gestor.

---

### 📑 Artefatos derivados (atividades, notas, anexos, lances, propostas)

Herdam acesso do lead via:

```sql
EXISTS (SELECT 1 FROM leads l WHERE ...)

```

Se o usuário pode ver o lead → vê o resto.

Se pode editar o lead → edita o resto.

---

### 📦 Automação confiável via `event_outbox`

Cada evento crítico (ex.: mudança de etapa do lead) gera:

- histórico interno (`lead_stage_history`)
- evento externo (`event_outbox`)

Permite automações idempotentes e logs auditáveis.

---

### 🛡️ Segurança consistente em toda a stack

- Supabase RLS + Postgres
- Funções SQL helpers
- Triggers de integridade
- Claims do JWT controlando role/org
- Frontend e backend reforçando as regras

O sistema segue o princípio:

**"Supabase armazena, FastAPI pensa, Next.js mostra.**