# Modulo de contratos

## Responsabilidade

O modulo de contratos formaliza comercialmente uma cota e dispara integracoes operacionais posteriores.

Ele cobre:

- criacao de contrato novo a partir do lead;
- cadastro de contrato preexistente;
- controle de status do contrato;
- gestao de documento PDF;
- gatilhos para carteira, kanban, parceiros e comissao.

## Entidades principais

- `contratos`
- `cotas`
- `contrato_parceiros`
- documentos PDF do contrato em Storage

## Fluxos principais

### Criar contrato a partir do lead

`POST /contracts/from-lead`

Passos:

1. valida `X-Org-Id`;
2. confirma que o lead pertence a organizacao;
3. cria cota;
4. cria opcoes de lance fixo;
5. cria contrato com status `pendente_assinatura`;
6. configura comissao da carta;
7. gera lancamentos;
8. garante cliente na carteira;
9. sincroniza parceiros do contrato.

### Registrar contrato existente

`POST /contracts/register-existing`

Diferencas:

- permite informar `contract_status`;
- permite informar `cota_situacao`;
- pode vincular `parceiro_id` inicial;
- aceita `repasse_percentual_comissao`.

### Atualizar status

`PATCH /contracts/{contract_id}/status`

Transicoes validas:

- `pendente_assinatura -> pendente_pagamento | cancelado`
- `pendente_pagamento -> alocado | cancelado`
- `alocado -> contemplado | cancelado`
- `contemplado -> cancelado`

Correcoes permitidas:

- `pendente_pagamento -> pendente_assinatura`
- `alocado -> pendente_pagamento | pendente_assinatura`
- `contemplado -> alocado | pendente_pagamento`
- `cancelado -> pendente_pagamento | pendente_assinatura | alocado`

Efeitos colaterais:

- `alocado` move lead para `ativo`
- `cancelado` move lead para `perdido`

### Documento do contrato

Rotas:

- `GET /contracts/{contract_id}/document`
- `POST /contracts/{contract_id}/document`
- `POST /contracts/{contract_id}/document/signed-url`
- `DELETE /contracts/{contract_id}/document`

Uso:

- upload e consulta de PDF/arquivo do contrato no bucket configurado;
- parceiros tambem podem gerar signed URL, mas apenas para contratos aos quais possuem acesso.

## Regras de negocio importantes

### Contrato e diferente de cota

O codigo deixa isso bem explicito:

- a cota contem dados da carta;
- o contrato referencia a cota;
- o contrato controla status comercial/juridico da formalizacao.

### Status do contrato

Status vistos:

- `pendente_assinatura`
- `pendente_pagamento`
- `alocado`
- `contemplado`
- `cancelado`

### Integracoes automáticas

Na criacao/atualizacao do contrato, o backend integra:

- carteira
- comissao
- sincronizacao de parceiros
- kanban do lead

### Relacao com parceiros

Um contrato pode ter vinculos em `contrato_parceiros`.

Esses vinculos podem nascer:

- manualmente no registro de contrato existente;
- automaticamente pela sincronizacao derivada da configuracao de comissao.

## Pontos pendentes de confirmacao

- se pode existir mais de um contrato por cota no banco ou se o modelo espera apenas o ultimo vigente;
- ciclo completo de assinatura e pagamento documental, hoje parcialmente refletido apenas pelos status e pelo PDF.
