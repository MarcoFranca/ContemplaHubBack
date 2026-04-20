# Arquitetura do backend

## Visao geral do sistema

O backend do ContemplaHub expõe uma API FastAPI que centraliza regras operacionais de:

- CRM comercial;
- operacao de consorcio;
- diagnostico financeiro;
- comissionamento e repasse;
- acesso de parceiros.

O desenho atual segue um padrao simples:

1. `routers` recebem requests HTTP e validam headers/body.
2. `schemas` definem os contratos de entrada e saida.
3. `services` concentram a maior parte das regras de negocio.
4. o acesso a dados ocorre via client Supabase usando tabelas, RPCs e Storage.

## Fluxo macro do produto

O modelo observado no codigo separa o sistema em camadas de dominio.

### 1. Camada comercial

- `lead` representa a oportunidade comercial;
- funil, diagnostico e proposta pertencem a esse contexto pre-contratacao.

### 2. Camada de formalizacao

- o `contrato` nasce na formalizacao/fechamento;
- ele formaliza comercial e juridicamente uma operacao ancorada em uma `cota`.

### 3. Camada operacional do consorcio

- a `cota` e o ativo operacional do consorcio;
- assembleia, lance e contemplacao pertencem a operacao da cota.

### 4. Camada de pos-venda

- `carteira` e uma dimensao operacional de pos-venda;
- ela nao substitui lead, cota ou contrato.

Arquivos centrais:

- `app/main.py`: registra app, CORS e todos os routers.
- `app/deps.py`: entrega o client admin do Supabase por request.
- `app/security/auth.py` e `app/core/auth_context.py`: resolvem identidade, tenant e perfil de acesso.
- `app/security/permissions.py`: aplica gates de permissao por tipo de ator.

## Stack e responsabilidades

### FastAPI

Responsavel por:

- roteamento HTTP;
- validacao Pydantic;
- composicao de dependencias;
- controle de status codes;
- upload de arquivos.

### Supabase / Postgres

Responsavel por:

- persistencia principal;
- autenticacao de usuarios;
- armazenamento de documentos e PDFs;
- RLS por organizacao;
- funcoes SQL auxiliares, como `get_kanban_metrics`.

### Backend vs banco

O projeto nao delega seguranca somente ao banco.

O backend:

- exige `org_id` em boa parte dos endpoints internos via header `X-Org-Id`;
- valida ownership de `lead`, `cota`, `contrato` e `parceiro` antes de operar;
- bloqueia acessos por papel (`admin`, `gestor`, `vendedor`, parceiro);
- restringe visualizacao de dados sensiveis no portal de parceiros;
- sincroniza efeitos colaterais entre dominios, por exemplo:
  - contrato -> carteira;
  - contrato/cota -> comissao;
  - comissao -> contrato_parceiros;
  - status de contrato -> etapa do lead.

## Modelo multi-tenant com `org_id`

O `org_id` e a chave de isolamento logico do sistema.

Padrao observado no codigo:

- tabelas de negocio sao sempre consultadas com filtro por `org_id`;
- autenticacao de usuarios internos resolve `profiles.user_id -> org_id`;
- autenticacao de parceiros resolve `partner_users.auth_user_id -> org_id`;
- operacoes cross-module reaplicam a verificacao de tenant no backend.

Exemplos reais:

- `leads`, `lead_propostas`, `lead_cadastros`, `lead_diagnosticos`
- `cotas`, `contratos`, `carteira_clientes`
- `parceiros_corretores`, `partner_users`, `contrato_parceiros`
- `cota_comissao_config`, `cota_comissao_regras`, `cota_comissao_parceiros`, `comissao_lancamentos`
- `lances`, `contemplacoes`

## Fluxo de autenticacao e autorizacao

### Usuarios internos

Fluxo atual:

1. request chega com `Authorization: Bearer <token>`.
2. o backend chama `sb.auth.get_user(token)`.
3. o `user_id` autenticado e buscado em `profiles`.
4. se existir `profiles.org_id`, o ator e tratado como `internal`.
5. o papel padrao cai para `vendedor` quando `role` vier vazio.

Permissoes derivadas:

- internos podem ver dados de cliente, contratos e comissoes;
- `admin` e `gestor` sao tratados como `manager`;
- alguns endpoints exigem explicitamente `require_manager`.

### Parceiros

Fluxo atual:

1. se o usuario nao foi resolvido em `profiles`, o backend busca `partner_users`.
2. o registro precisa estar `ativo = true`.
3. o contexto resultante inclui:
   - `parceiro_id`
   - `partner_user_id`
   - `can_view_client_data`
   - `can_view_contracts`
   - `can_view_commissions`

### Gates usados no codigo

- `require_auth_context`
- `require_internal_user`
- `require_partner_user`
- `require_manager`
- `require_partner_contract_access`
- `require_partner_commission_access`

## Papel do backend em relacao ao RLS

O backend usa client admin do Supabase (`SUPABASE_SERVICE_ROLE_KEY`), entao ele nao depende do RLS para se proteger sozinho.

Na pratica isso implica:

- o RLS protege o banco para acessos que respeitem contexto SQL;
- o backend precisa filtrar tenant e permissao manualmente em toda operacao sensivel;
- por isso existem varias validacoes do tipo:
  - `eq("org_id", org_id)`
  - comparacao entre `ctx.org_id` e `record.org_id`
  - validacao de `parceiro_id` antes de exibir contrato ou comissao.

Esse ponto e critico porque service role ignora politicas se usado sem cuidado.

## Modulos principais do sistema

### Leads e Kanban

Responsabilidades:

- criar, atualizar, mover etapa e excluir leads;
- montar snapshot do kanban por etapa;
- enriquecer cards com interesse e diagnostico;
- marcar `first_contact_at` quando o lead sai de `novo`.

Tabelas principais:

- `leads`
- `lead_interesses`
- `lead_diagnosticos`

### Diagnosticos

Responsabilidades:

- capturar contexto financeiro do lead;
- calcular score de readiness/risco;
- persistir diagnostico por `(org_id, lead_id)`.

Observacao:

- o scoring atual e um MVP deterministico em codigo Python, nao um motor externo.

### Propostas e onboarding

Responsabilidades:

- criar cenarios de proposta por lead;
- gerar hash publico;
- expor visualizacao publica;
- aceitar proposta publicamente;
- iniciar cadastro documental em `lead_cadastros`;
- disparar emails operacionais.

### Cotas e contratos

Responsabilidades:

- registrar carta/cota separadamente do contrato;
- cadastrar contrato novo a partir do lead;
- registrar contrato ja existente;
- sincronizar status do contrato com kanban e carteira.

### Comissoes

Responsabilidades:

- configurar percentual total, regras por evento e repasse para parceiros;
- projetar lancamentos por competencia;
- sincronizar evento de contemplacao;
- cancelar ou excluir configuracao com validacoes.

### Parceiros e portal

Responsabilidades:

- manter cadastro operacional do parceiro;
- criar acesso de login vinculado a `partner_users`;
- controlar permissoes granulares;
- expor contratos/comissoes em portal restrito;
- mascarar dados de cliente quando o parceiro nao pode ver PII.

### Carteira

Responsabilidades:

- garantir que leads com contrato entrem em carteira;
- listar clientes ativos da carteira;
- permitir nova negociacao sem remover o cliente da carteira.

### Marketing guide

Modulo auxiliar de captura de leads por landing page e entrega de PDF em Storage.

Status para esta documentacao:

- fora do escopo principal do dominio de consorcio solicitado;
- endpoints documentados na API para completude.

## Regras de dominio importantes

### Separacao entre lead, cota e contrato

O codigo atual reforca que:

- lead representa a oportunidade comercial;
- cota/carta representa o ativo de consorcio;
- contrato representa a formalizacao comercial da cota.

Consequencias observadas:

- contrato e criado com referencia para `cota_id`;
- varias regras financeiras e de lance vivem na cota, nao no contrato;
- movimentacoes de comissao partem de contrato e cota em conjunto.

### Contrato nao e cota

Essa separacao precisa permanecer explicita:

- contrato controla a formalizacao;
- cota controla o ativo operacional;
- eventos de assembleia, lance e contemplacao nao nascem no contrato.

### Contemplacao ocorre na cota, nao no contrato

No modelo atual:

- o registro estruturado de contemplacao fica na operacao da cota;
- a comissao sincroniza esse evento a partir da relacao entre contrato e cota;
- campos de contrato que reflitam contemplacao devem ser lidos como reflexo do evento operacional, nao como sua origem.

### Carteira e outra dimensao operacional

Carteira nao e:

- etapa do lead;
- status do contrato;
- situacao da cota.

Carteira e o dominio de acompanhamento pos-venda.

### Camadas de estado do sistema

O sistema possui pelo menos tres camadas de estado separadas.

#### Status do contrato

Camada de formalizacao:

- `pendente_assinatura`
- `pendente_pagamento`
- `alocado`
- `contemplado`
- `cancelado`

#### Situacao da cota

Camada operacional:

- `ativa`
- `contemplada`
- `cancelada`

#### Status da carteira

Camada de pos-venda:

- `ativo` observado no codigo
- outros estados completos ficam `pendente de confirmacao`

Regra de leitura:

- nunca usar `contratos.status` como substituto de `cotas.status`;
- nunca usar `carteira_clientes.status` como substituto das camadas anteriores.

### Entrada automatica na carteira

Ao criar contrato:

- o backend chama `ensure_carteira_cliente`;
- se o lead ja estiver na carteira, nao duplica;
- se nao estiver, entra com `status = ativo`.

### Sincronizacoes internas

- contrato `alocado` move lead para etapa `ativo`
- contrato `cancelado` move lead para `perdido`
- configuracao de comissao sincroniza `contrato_parceiros`
- geracao de lancamentos tambem sincroniza `contrato_parceiros`

## Pontos pendentes de confirmacao

- schema fisico completo do banco, pois o dump versionado esta vazio;
- constraints unicas globais de varias tabelas alem das vistas nas migrations;
- historico formal de etapa do lead e outbox de eventos: o codigo cita isso como `TODO`;
- integracao exata entre Drizzle e este repositorio Python: a stack foi informada no `AGENTS.md`, mas nao ha artefatos Drizzle visiveis neste repo.
