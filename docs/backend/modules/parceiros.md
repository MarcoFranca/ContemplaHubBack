# Modulo de parceiros

## Responsabilidade

O modulo de parceiros administra:

- cadastro operacional do parceiro/corretor;
- login e permissao de acesso;
- vinculos com contratos;
- acesso ao portal parceiro.

## Entidades principais

- `parceiros_corretores`
- `partner_users`
- `contrato_parceiros`
- `comissao_lancamentos` quando o beneficiario e parceiro

## Fluxos principais

### Cadastrar parceiro

`POST /comissoes/parceiros`

Campos observados:

- nome
- cpf/cnpj
- telefone
- email
- pix
- ativo
- observacoes

Fluxo opcional:

- no mesmo request pode criar acesso de portal em `partner_users`.

### Criar/reenviar acesso

Rotas:

- `POST /partner-users/invite`
- `POST /partner-users/{partner_user_id}/resend-invite`

Regras:

- apenas manager pode executar;
- convite usa Supabase Auth admin;
- payload de metadata marca o ator como `partner`.

### Ativar/desativar parceiro

Rotas:

- `PATCH /comissoes/parceiros/{parceiro_id}/toggle`
- `PATCH /partner-users/{partner_user_id}/toggle`

Regra:

- o sistema sincroniza o status do cadastro operacional e do acesso.

### Portal do parceiro

Rotas:

- `GET /partner/me`
- `GET /partner/contracts`
- `GET /partner/contracts/{contract_id}`
- `GET /partner/commissions`
- `POST /partner/contracts/{contract_id}/document/signed-url`

## Regras de negocio importantes

### Cadastro operacional x acesso de login

O modelo separa:

- `parceiros_corretores`: entidade de negocio
- `partner_users`: credencial/permissao de portal

Isso permite:

- parceiro existir sem acesso de login;
- acesso ser ativado/desativado sem apagar o parceiro;
- controle granular de permissao por usuario de parceiro.

### Permissoes granulares

Cada `partner_users` pode ter:

- `can_view_client_data`
- `can_view_contracts`
- `can_view_commissions`

Impacto direto:

- sem dados de cliente, o portal mascara nome, email e telefone;
- sem contratos, nao acessa listagem/detalhe;
- sem comissoes, nao ve lancamentos.

### Viculo com contratos

O portal so enxerga contratos presentes em `contrato_parceiros`.

Esse vinculo pode ser:

- manual;
- sincronizado a partir da configuracao de comissao.

### Exclusao de parceiro

Um parceiro nao pode ser excluido se ainda houver:

- acesso em `partner_users`
- vinculo em `cota_comissao_parceiros`
- vinculo em `contrato_parceiros`
- lancamento em `comissao_lancamentos`

## Pontos pendentes de confirmacao

- se o produto pretende suportar mais de um `partner_user` por parceiro no futuro;
- se o portal tera acoes de upload/aceite alem de download do documento do contrato;
- politica de seguranca para manutencao das rotas `auth-debug` em producao.
