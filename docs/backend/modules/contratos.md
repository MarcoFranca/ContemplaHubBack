# Modulo de contratos

## Responsabilidade

O modulo de contratos formaliza comercialmente uma cota e dispara integracoes operacionais posteriores.

Ele cobre:

- criacao de contrato novo a partir do lead;
- cadastro de contrato preexistente;
- controle de status do contrato;
- gestao de documento PDF;
- gatilhos para carteira, kanban, parceiros e comissao.

## Posicao no fluxo macro

- o contrato nasce na formalizacao/fechamento;
- ele nao e o ativo operacional primario do consorcio;
- ele formaliza uma operacao ancorada em uma `cota`.

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

Leitura correta:

- primeiro nasce a cota operacional;
- depois nasce o contrato de formalizacao.

### Registrar contrato existente

`POST /contracts/register-existing`

Diferencas:

- exige um conjunto minimo de dados operacionais ja consolidados
- permite informar `contract_status`;
- permite informar `cota_situacao`;
- pode vincular `parceiro_id` inicial;
- aceita `repasse_percentual_comissao`.

Entrada minima observada no schema:

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

Validacoes adicionais:

- valida administradora acessivel ao contexto:
  - aceita administradora da mesma `org_id`;
  - aceita administradora global, quando `administradoras.org_id` estiver nulo/vazio;
  - rejeita administradora vinculada a outra organizacao;
- valida parceiro na organizacao, quando informado;
- valida `contract_status` separadamente de `cota_situacao`;
- bloqueia combinacoes iniciais invalidas, como contrato `contemplado` com cota nao `contemplada`;
- bloqueia duplicidade de contrato por numero dentro da organizacao;
- bloqueia duplicidade operacional de cota por administradora, grupo e numero dentro da organizacao.

Datas operacionais:

- `data_assinatura` continua obrigatoria para o contrato;
- o fluxo `register-existing` nao assume automaticamente que `data_assinatura` equivale a `data_alocacao`;
- o fluxo `register-existing` nao assume automaticamente que `data_assinatura` equivale a `data_contemplacao`;
- essas datas operacionais permanecem nulas ate haver evento/regra explicita que justifique o preenchimento.

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

- `alocado` e `contemplado` movem lead para `pos_venda`
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

### Contrato nao governa assembleia, lance e contemplacao

Esses eventos pertencem a operacao da cota.

No dominio atual:

- assembleia e configurada/resolvida a partir da cota;
- lance e registrado por `cota_id`;
- contemplacao e registrada por `cota_id`.

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

### Camada de estado do contrato

O estado do contrato deve ser lido separado de outras camadas:

- status do contrato
- situacao da cota
- status da carteira

No fluxo `register-existing`, isso aparece explicitamente porque:

- `contract_status` entra no payload do contrato;
- `cota_situacao` entra no payload da cota;
- o backend valida essas camadas sem colapsa-las em um unico campo.

Outra implicacao importante:

- status de contrato nao autoriza inferir marcos operacionais da cota;
- contemplacao continua sendo evento da cota, nao do contrato.

### Relacao com parceiros

Um contrato pode ter vinculos em `contrato_parceiros`.

Esses vinculos podem nascer:

- manualmente no registro de contrato existente;
- automaticamente pela sincronizacao derivada da configuracao de comissao.

## Pontos pendentes de confirmacao

- se pode existir mais de um contrato por cota no banco ou se o modelo espera apenas o ultimo vigente;
- ciclo completo de assinatura e pagamento documental, hoje parcialmente refletido apenas pelos status e pelo PDF.
