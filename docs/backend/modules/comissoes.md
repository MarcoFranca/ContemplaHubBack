# Modulo de comissoes

## Responsabilidade

O modulo de comissoes calcula e opera a remuneracao da empresa e o repasse devido aos parceiros.

Ele cobre:

- configuracao percentual por cota;
- regras de liberacao por evento;
- participacao de parceiros;
- projecao de competencias;
- controle de pagamento e repasse;
- reprocessamento e timeline financeira.

## Entidades principais

- `cota_comissao_config`
- `cota_comissao_regras`
- `cota_comissao_parceiros`
- `comissao_lancamentos`
- `contrato_parceiros`
- `parceiros_corretores`

## Fluxos principais

### Configurar comissao da cota

`PUT /comissoes/cotas/{cota_id}`

Payload principal:

- `percentual_total`
- `base_calculo`
- `modo`
- `imposto_padrao_pct`
- `primeira_competencia_regra`
- `furo_meses_override`
- `regras[]`
- `parceiros[]`

Passos:

1. valida que a cota existe na org;
2. valida parceiros informados;
3. cria ou atualiza `cota_comissao_config`;
4. recria regras e parceiros da cota;
5. sincroniza `contrato_parceiros`.

### Gerar lancamentos do contrato

`POST /comissoes/contratos/{contrato_id}/gerar`

Passos:

1. carrega contrato, cota e config;
2. projeta cronograma base;
3. calcula competencia por regra/evento;
4. gera lancamento da empresa;
5. gera lancamento de parceiros quando houver;
6. opcionalmente sobrescreve os existentes.

### Sincronizar evento de contemplacao

`POST /comissoes/contratos/{contrato_id}/sincronizar-eventos`

Regra:

- se houver contemplacao, eventos de comissao do tipo `contemplacao` viram `disponivel`.

### Operar pagamento e repasse

Rotas:

- processar pagamento
- atualizar status de lancamento
- atualizar repasse
- marcar repasse pago

## Regras de negocio importantes

### Comissao da empresa x repasse do parceiro

O codigo trabalha com duas camadas:

1. comissao total da operacao;
2. fatia de parceiro derivada dessa comissao.

Ou seja:

- parceiro nao recebe diretamente sobre a carta “por fora”;
- o parceiro recebe um percentual da comissao total configurada.

### Consistencia de percentuais

Validacoes implementadas:

- soma das regras = `percentual_total`
- soma dos parceiros <= `percentual_total`
- comissao `avista` deve ter exatamente uma regra

### Eventos de liberacao

Tipos observados:

- `adesao`
- `primeira_cobranca_valida`
- `proxima_cobranca`
- `contemplacao`
- `manual`

### Competencia e cronograma

A competencia prevista depende de:

- `data_adesao` da cota;
- `assembleia_dia`;
- `furo_meses` da cota ou override da config;
- data de contemplacao, quando aplicavel.

### Exclusao e cancelamento

Exclusao da configuracao:

- bloqueada se houver lancamentos pagos ou repasses pagos, salvo `force=true`

Cancelamento:

- marca lancamentos nao pagos como `cancelado`
- desativa a config da cota

### Timeline financeira

O modulo expoe visoes adicionais:

- competencias do contrato
- resumo financeiro
- timeline

Essas visoes sao operacionais/gerenciais e exigem manager.

## Relacao com regras do consorcio

- a carta/cota define a base economica;
- a contemplacao e um evento de negocio que pode liberar comissao;
- parceiros entram como beneficiarios secundarios do fluxo financeiro;
- contrato e cota continuam separados: lancamento carrega os dois IDs.

## Pontos pendentes de confirmacao

- regras exactas de processamento a partir da tabela de pagamentos externa;
- como sao gerados os registros da tabela `contemplacoes` fora do modulo de lances;
- modelo contábil final esperado para mais de um parceiro por cota/contrato.
