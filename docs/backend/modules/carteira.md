# Modulo de carteira

## Responsabilidade

O modulo de carteira representa a base de clientes ja convertidos ou preservados para relacionamento recorrente.

Ele cobre:

- entrada automatica de clientes vindos de contrato;
- criacao manual de cliente ja dentro da carteira;
- listagem enriquecida para operacao;
- reabertura de negociacao sem perder o vinculo de carteira.

## Entidades principais

- `carteira_clientes`
- `leads`
- `cotas`
- `contratos`
- `administradoras`

## Fluxos principais

### Garantir cliente na carteira

Service central: `ensure_carteira_cliente`

Regras:

- busca por `(org_id, lead_id)`;
- se ja existir, nao duplica;
- se nao existir, cria com `status = ativo`.

Esse service e chamado automaticamente por contratos.

### Listar carteira

`GET /carteira`

Retorna:

- item da carteira;
- lead associado;
- cota mais recente;
- contrato mais recente da cota;
- administradora da cota.

### Criar cliente direto na carteira

`POST /carteira/clientes`

Fluxo:

1. cria lead com etapa `ativo`;
2. garante registro em `carteira_clientes`.

### Abrir nova negociacao

`POST /carteira/{lead_id}/nova-negociacao`

Fluxo:

1. confirma ownership do lead;
2. garante carteira;
3. move lead para etapa comercial, normalmente `negociacao`.

## Regras de negocio importantes

### Carteira nao substitui lead

O cliente em carteira continua referenciado pelo mesmo `lead_id`.

Consequencias:

- relacionamento comercial e historico permanecem centrados no lead;
- reentrada em negociacao nao exige criar outro lead.

### Cliente convertido pode voltar ao funil

O modulo deixa explicito que:

- o cliente pode permanecer em carteira;
- ao mesmo tempo pode voltar ao fluxo comercial para nova negociacao.

### Entrada automatica por contrato

Na criacao de contrato, o backend registra automaticamente o cliente na carteira.

Isso reduz falha operacional entre venda/contratacao e pos-venda.

## Pontos pendentes de confirmacao

- estados possiveis completos de `carteira_clientes.status`;
- regras de saida/arquivamento da carteira;
- se ha SLAs ou tarefas de acompanhamento vinculadas a carteira em outro repositorio/modulo.
