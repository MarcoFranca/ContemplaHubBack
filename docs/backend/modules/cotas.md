# Modulo de cotas

## Responsabilidade

O modulo de cotas representa a carta de consorcio em seu estado operacional e financeiro.

No modelo atual, a cota concentra:

- dados da carta;
- regras de pagamento e componentes da parcela;
- configuracao de lance;
- status operacional da carta;
- base para comissao e contemplacao.

## Posicao no fluxo macro

- a cota e o ativo operacional do consorcio;
- assembleia, lance e contemplacao pertencem a essa camada;
- contrato formaliza a venda, mas nao substitui a cota;
- carteira passa a consumir a cota em visao de pos-venda.

## Entidades principais

- `cotas`
- `cota_lance_fixo_opcoes`
- `lances`
- `contemplacoes`
- `cota_comissao_config`
- `cota_comissao_regras`
- `cota_comissao_parceiros`

## Fluxos principais

### Criacao de cota junto ao contrato

Nao existe rota dedicada de criacao isolada da cota.

A cota nasce em:

- `POST /contracts/from-lead`
- `POST /contracts/register-existing`

Passos:

1. normaliza payload financeiro;
2. cria `cotas`;
3. opcionalmente cria `cota_lance_fixo_opcoes`;
4. depois cria `contratos`.

No fluxo `register-existing`:

- a situacao inicial da cota pode ser informada por `cota_situacao`;
- essa situacao e validada separadamente do `contract_status`.

### Atualizacao operacional da cota

`PATCH /lances/cartas/{cota_id}`

Permite alterar campos como:

- grupo
- numero da cota
- produto
- valor da carta
- valor da parcela
- fundo de reserva
- seguro prestamista
- taxa administrativa antecipada
- prazo
- dia de assembleia
- estrategia e objetivo
- opcoes de lance fixo

### Controle mensal de lances

`POST /lances/cartas/{cota_id}/controle-mensal`

Mantem o estado do mes operacional.

### Contemplacao, cancelamento e reativacao

- `POST /lances/cartas/{cota_id}/contemplar`
- `POST /lances/cartas/{cota_id}/cancelar`
- `POST /lances/cartas/{cota_id}/reativar`

### Operacao de assembleia, lance e contemplacao

Esses tres conceitos pertencem ao dominio da cota:

- assembleia usa `assembleia_dia` e/ou regra operacional da administradora;
- lance e registrado por `cota_id` e competencia;
- contemplacao e persistida por `cota_id`.

## Regras de negocio importantes

### Separacao entre cota e contrato

Esta e uma regra central do dominio atual:

- cota = carta/ativo operacional
- contrato = formalizacao comercial

Consequencias:

- contrato referencia `cota_id`;
- comissao depende da cota para valor e cronograma;
- operacao de lances trabalha sobre cota, nao sobre contrato.

### Componentes financeiros da cota

Campos relevantes observados:

- `valor_carta`
- `valor_parcela`
- `fundo_reserva_percentual`
- `fundo_reserva_valor_mensal`
- `seguro_prestamista_*`
- `taxa_admin_antecipada_*`

Esses campos mostram que a cota guarda a estrutura economica da operacao.

### Regras de contemplacao

A contemplacao tem reflexo em:

- status da cota;
- registro de `contemplacoes`;
- eventos de comissao do tipo `contemplacao`.

Regra de leitura:

- contemplacao nao nasce no contrato;
- contemplacao nasce na operacao da cota.

### Grupo, cota, lance e assembleia

Relacao observada no codigo:

- a cota possui `grupo_codigo` e `numero_cota`;
- a cota pode ter `assembleia_dia`;
- regras da operadora ajudam a prever assembleia;
- lances sao registrados por competencia;
- contemplacao pode ocorrer por `lance`, `sorteio` ou `outro`.

### Camada de estado da cota

A situacao da cota e uma camada de estado propria, separada de:

- `contratos.status`
- `carteira_clientes.status`

Valores observados:

- `ativa`
- `contemplada`
- `cancelada`

No cadastro de contrato ja existente:

- `cota_situacao` nasce na cota, nao no contrato;
- o backend rejeita combinacoes evidentemente invalidas com `contract_status`.

### Lance fixo

Opcoes de lance fixo:

- sao vinculadas a uma cota;
- nao podem repetir `ordem`;
- nao podem repetir percentual.

## Pontos pendentes de confirmacao

- constraints unicas de `(org_id, administradora_id, grupo_codigo, numero_cota)` no banco;
- nome fisico da tabela de regras operacionais por administradora;
- se existe historico formal de alteracoes da cota alem de auditoria indireta.
