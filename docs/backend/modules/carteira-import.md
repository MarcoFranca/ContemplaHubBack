# Modulo de importacao de carteira

## Responsabilidade

Este modulo cobre a importacao em massa de cartas/cotas a partir de dados colados de planilha.

O fluxo foi desenhado para operar dentro do dominio atual do produto, sem criar uma tabela genérica paralela:

- `leads`
- `carteira_clientes`
- `administradoras`
- `grupos`
- `cotas`
- `contratos`
- `lances`
- `contemplacoes`

## Rotas

- `POST /carteira/import/preview`
- `POST /carteira/import/confirm`

## Autorizacao e multi-tenant

- o backend resolve `org_id` a partir da sessão autenticada via `get_current_profile()`;
- a importacao em massa só é permitida para `admin` e `gestor`;
- o client não envia `org_id` arbitrário para definir o tenant da operação;
- toda escrita continua respeitando `org_id` e o isolamento da organização.

## Fluxo de preview

O preview:

1. recebe texto colado pelo usuário em formato tabulado (`TSV`) ou `CSV`;
2. normaliza cabeçalhos, nome do cliente, moeda BR, percentual, data BR e boolean;
3. ignora linhas vazias e linhas separadoras compostas apenas por caracteres como `-`, `_`, `=` e `/`;
4. identifica por linha:
   - cliente encontrado ou a criar;
   - administradora a criar;
   - grupo a criar;
   - cota a criar;
   - contrato/lance/contemplação que podem ou não ser criados;
5. devolve status por linha:
   - `pronta`
   - `aviso`
   - `erro`
   - `ignorada`
6. linhas ignoradas entram apenas no resumo do preview e não aparecem na grade operacional retornada ao frontend.

## Fluxo de confirmacao

Na confirmação:

- o backend recalcula o preview para evitar confirmação cega;
- linhas com erro não são gravadas;
- linhas prontas/aviso tentam gravar entidades no domínio real;
- duplicidade operacional de cota continua bloqueada por:
  - `org_id`
  - `administradora_id`
  - `grupo_codigo`
  - `numero_cota`
- contemplação continua restrita a uma por cota.

## Regras de negocio aplicadas

- cliente é buscado por nome normalizado dentro da organização;
- se não existir lead correspondente, é criado lead já em `pos_venda` e garantida entrada em `carteira_clientes`;
- administradora é buscada primeiro nas acessíveis ao tenant e criada na org quando ausente;
- grupo é buscado/criado por `administradora_id + codigo`;
- a cota permanece gravando `grupo_codigo` em `cotas`, mas a importacao também cria/garante o registro em `grupos`;
- `TIPO DE LANCE = SORTEIO` não cria lance e vira observação operacional;
- `CONTEMPLADA = TRUE` tenta registrar contemplação apenas quando houver dados mínimos para isso;
- campos sem coluna operacional dedicada hoje, como `optin` e `valor final da carta`, ficam preservados em observações.

## Limitacoes atuais

- a planilha informada neste task não traz dados mínimos consistentes para registrar contrato existente pelo fluxo completo atual de contratos com comissão;
- por isso o preview só marca contrato como criável quando houver dados mínimos explícitos suficientes;
- valores auxiliares sem coluna operacional dedicada seguem fallback por observações, sem migration nova neste task.
