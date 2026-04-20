# Modulo de carteira

## Responsabilidade

O modulo de carteira representa a base de clientes ja convertidos ou preservados para relacionamento recorrente.

Ele cobre:

- entrada automatica de clientes vindos de contrato;
- criacao manual de cliente ja dentro da carteira;
- listagem enriquecida para operacao;
- reabertura de negociacao sem perder o vinculo de carteira.

## Posicao no fluxo macro

- carteira e pos-venda operacional;
- ela normalmente passa a existir depois da formalizacao;
- ela nao substitui lead, contrato nem cota;
- ela funciona como outra dimensao operacional do sistema.

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

Leitura correta:

- a entrada automatica acontece por conta da formalizacao/fechamento;
- isso nao transforma carteira em extensao do contrato;
- carteira passa a ser o dominio de acompanhamento pos-venda.

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

### Carteira nao se confunde com estado de contrato ou cota

Carteira possui sua propria camada de estado.

Ela nao responde por:

- assinatura/pagamento/alocacao do contrato;
- atividade, cancelamento ou contemplacao da cota;
- operacao de assembleia ou lance.

### Entrada automatica por contrato

Na criacao de contrato, o backend registra automaticamente o cliente na carteira.

Isso reduz falha operacional entre venda/contratacao e pos-venda.

### Carteira como dominio proprio

Mesmo quando alimentada automaticamente pelo contrato, carteira continua sendo dominio proprio porque:

- trabalha sobre relacionamento continuo;
- consolida cliente, cotas e contratos em visao operacional;
- permite nova negociacao sem recriar a base do cliente.

## Pontos pendentes de confirmacao

- estados possiveis completos de `carteira_clientes.status`;
- regras de saida/arquivamento da carteira;
- se ha SLAs ou tarefas de acompanhamento vinculadas a carteira em outro repositorio/modulo.
