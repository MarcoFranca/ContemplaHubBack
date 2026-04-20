# Multi-tenant e seguranca

## Principio geral

O backend do ContemplaHub assume que isolamento por tenant e obrigatorio em duas camadas:

1. banco com RLS e politicas por `org_id`;
2. backend com filtros e validacoes explicitas antes de cada operacao.

Essa combinacao e necessaria porque o backend usa client admin do Supabase em boa parte do codigo.

## Uso de `org_id`

`org_id` aparece como identificador de tenant em todos os dominios principais.

Padroes reais observados:

- queries do tipo `.eq("org_id", org_id)`
- carga do contexto do usuario autenticado a partir de `profiles` ou `partner_users`
- validacoes cruzadas como:
  - `lead["org_id"] != org_id`
  - `contrato["org_id"] != org_id`
  - `partner_users.org_id = ctx.org_id`

## Fontes de `org_id`

### Endpoints internos legados

Boa parte da API interna ainda exige:

- header `X-Org-Id`

Isso aparece em:

- leads
- kanban
- diagnostico
- lead-propostas
- contracts
- carteira
- varias rotas de comissoes

Implicacao:

- o frontend precisa enviar o tenant explicitamente;
- o backend nao deve confiar apenas nesse header sem conferir pertencimento do registro consultado.

### Endpoints autenticados por contexto

Nos modulos de parceiros e parte de comissoes, o `org_id` vem do contexto autenticado:

- `profiles` para usuarios internos
- `partner_users` para parceiros

Vantagem:

- reduz spoofing por header;
- permite gating por papel/permissao na mesma dependencia.

## Fluxo de autenticacao

### Resolucao do ator

O backend extrai o token bearer e:

1. chama `sb.auth.get_user(token)`;
2. procura `user_id` em `profiles`;
3. se nao encontrar, procura em `partner_users` ativos;
4. monta `AuthContext`.

Campos importantes do contexto:

- `user_id`
- `org_id`
- `actor_type`
- `role`
- `parceiro_id`
- `partner_user_id`
- `can_view_client_data`
- `can_view_contracts`
- `can_view_commissions`

## RLS

### O que esta confirmado

Na migration de `lead_propostas`, o RLS esta explicitamente habilitado e usa `public.app_org_id()`:

- `SELECT` por `org_id = public.app_org_id()`
- `INSERT` com `WITH CHECK`
- `UPDATE` com `USING` e `WITH CHECK`
- `DELETE` com `USING`

### O que fica pendente

Como o dump completo do schema nao esta presente, as politicas exatas das demais tabelas ficam `pendente de confirmacao`.

Mesmo assim, pelo padrao do projeto e pelo `AGENTS.md`, a expectativa correta de arquitetura e:

- toda tabela de negocio deve respeitar `org_id`;
- toda policy deve impedir atravessamento entre organizacoes;
- backend nao deve assumir que a policy sozinha basta.

## Validacao adicional no backend

Exemplos reais:

### Leads

- `POST /leads` sempre grava `org_id` vindo do header;
- `PATCH/DELETE /leads/{id}` filtram por `org_id` e `id`.

### Contratos

- o backend carrega o contrato e compara `contrato["org_id"]` com o tenant corrente;
- ao criar contrato, garante que o `lead_id` pertence ao `org_id`.

### Comissoes

- busca qualquer registro via helper `get_org_record_or_404`;
- exclusao de configuracao consulta antes se ja ha pagamento ou repasse pago;
- parceiros so podem ser usados se pertencerem a mesma organizacao.

### Portal de parceiros

- o parceiro so visualiza contratos presentes em `contrato_parceiros`;
- mesmo com acesso ao contrato, dados de cliente podem ser mascarados;
- comissao so aparece se `can_view_commissions = true`.

## Regras de acesso por papel

### Interno

- `admin` e `gestor` sao managers;
- managers podem criar parceiro, convidar acesso, reprocessar comissao, ver endpoints restritos.

### Parceiro

Permissoes granulares:

- `can_view_client_data`
- `can_view_contracts`
- `can_view_commissions`

Efeitos praticos:

- sem `can_view_client_data`, nome/email/telefone do cliente sao mascarados;
- sem `can_view_contracts`, listagem e detalhe de contratos retornam `403`;
- sem `can_view_commissions`, listagem e detalhe de comissoes ficam bloqueados.

## Riscos comuns

### 1. Confiar apenas em `X-Org-Id`

Risco:

- cliente malicioso pode tentar header de outra org.

Mitigacao atual:

- varios endpoints cruzam o registro carregado com `org_id`.

Melhoria futura:

- migrar mais rotas para depender sempre de `AuthContext`, reduzindo uso de header solto.

### 2. Uso de service role

Risco:

- bypass acidental do RLS se uma query esquecer filtros e ownership.

Mitigacao atual:

- filtros manuais por `org_id`;
- dependencias de permissao;
- helpers de leitura `get_org_record_or_404`.

### 3. Exposicao excessiva no portal de parceiros

Risco:

- vazamento de PII do cliente.

Mitigacao atual:

- serializacao mascarada quando `can_view_client_data = false`.

### 4. Falta de constraints unicas em tabelas logicas 1:1

Risco:

- duplicidade operacional em `lead_diagnosticos`, `carteira_clientes` ou `partner_users`.

Mitigacao atual:

- varios fluxos fazem upsert manual ou validacao previa.

Status:

- `pendente de confirmacao` se o banco ja possui unique constraints nao visiveis neste repo.

## Recomendacoes futuras

- reduzir dependencia de `X-Org-Id` e usar contexto autenticado em toda rota interna;
- versionar o schema completo ou o source de Drizzle neste repositorio;
- criar testes automatizados de autorizacao por tenant;
- padronizar helpers de ownership para leads, cotas, contratos e parceiros;
- documentar formalmente quais tabelas ainda dependem de RLS confirmado apenas por convencao.
