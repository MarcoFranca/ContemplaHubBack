# Modulo de cotas

## Responsabilidade

O modulo de cotas representa a carta de consorcio em seu estado operacional e financeiro.

No modelo atual, a cota concentra:

- dados da carta;
- regras de pagamento e componentes da parcela;
- configuracao de lance;
- status operacional da carta;
- base para comissao e contemplacao.

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

### Grupo, cota, lance e assembleia

Relacao observada no codigo:

- a cota possui `grupo_codigo` e `numero_cota`;
- a cota pode ter `assembleia_dia`;
- regras da operadora ajudam a prever assembleia;
- lances sao registrados por competencia;
- contemplacao pode ocorrer por `lance`, `sorteio` ou `outro`.

### Lance fixo

Opcoes de lance fixo:

- sao vinculadas a uma cota;
- nao podem repetir `ordem`;
- nao podem repetir percentual.

## Pontos pendentes de confirmacao

- constraints unicas de `(org_id, administradora_id, grupo_codigo, numero_cota)` no banco;
- nome fisico da tabela de regras operacionais por administradora;
- se existe historico formal de alteracoes da cota alem de auditoria indireta.
