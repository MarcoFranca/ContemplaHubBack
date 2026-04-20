# Backend ContemplaHub

Documentacao tecnica viva do backend do ContemplaHub, gerada com base no codigo atual em `app/`, `migrations/` e nas dependencias de autenticacao/autorizacao integradas ao Supabase.

## Visao geral

O backend atual e uma API FastAPI que atua como camada de orquestracao do dominio de consorcio sobre:

- autenticacao Supabase Auth;
- persistencia em Postgres/Supabase;
- RLS no banco;
- validacao adicional de autorizacao no backend;
- fluxos multi-tenant orientados por `org_id`.

O sistema cobre principalmente:

- CRM comercial de leads e kanban;
- diagnostico financeiro por lead;
- propostas comerciais com pagina publica e onboarding;
- cadastro de cotas, contratos e carteira;
- comissoes e repasses para parceiros;
- portal de parceiros com visao restrita por permissao;
- assets/documentos em Supabase Storage.

## Leitura correta do dominio

- `lead` e a unidade de entrada comercial;
- `contrato` nasce na formalizacao/fechamento;
- `cota` e o ativo operacional do consorcio;
- `assembleia`, `lance` e `contemplacao` pertencem a operacao da cota;
- `carteira` representa o pos-venda operacional, em dimensao propria.

## Navegacao

- [Arquitetura](./architecture.md)
- [API](./api/endpoints.md)
- [Schema Overview](./database/schema-overview.md)
- [Multi-tenant e RLS](./database/multi-tenant-rls.md)

## Modulos

- [Leads](./modules/leads.md)
- [Diagnosticos](./modules/diagnosticos.md)
- [Propostas](./modules/propostas.md)
- [Cotas](./modules/cotas.md)
- [Contratos](./modules/contratos.md)
- [Comissoes](./modules/comissoes.md)
- [Parceiros](./modules/parceiros.md)
- [Carteira](./modules/carteira.md)

## Fontes consideradas

- routers FastAPI em `app/routers/`
- schemas Pydantic em `app/schemas/`
- services de dominio em `app/services/`
- autenticacao/permissoes em `app/security/` e `app/core/`
- migrations SQL em `migrations/`

## Limites desta documentacao

- Os arquivos `migrations/001_init.sql` e `supabase/migrations/20251112190211_remote_schema.sql` estao vazios no repositorio atual. Por isso, a documentacao de banco foi inferida a partir das migrations incrementais e do uso real das tabelas no codigo.
- Onde o codigo aponta comportamento futuro, incompleto ou dependente de schema nao versionado aqui, o texto marca `pendente de confirmacao`.
