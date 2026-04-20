# Modulo de propostas

## Responsabilidade

O modulo de propostas transforma o lead em uma oferta comercial estruturada e compartilhavel.

Ele cobre:

- criacao de cenarios de proposta;
- exposicao interna e publica;
- aceitacao publica pelo cliente;
- disparo do onboarding cadastral;
- notificacoes por email.

## Entidades principais

- `lead_propostas`
- `lead_cadastros`
- `lead_cadastros_pf`
- `lead_cadastros_pj`
- `lead_cadastros_socios`
- `lead_cadastros_docs`

## Fluxos principais

### Criar proposta

`POST /lead-propostas/lead/{lead_id}`

Passos observados:

1. valida que o lead pertence a `org_id`;
2. monta `cliente` a partir do proprio lead;
3. converte cenarios em `payload` JSON;
4. gera `public_hash` unico;
5. insere proposta em `lead_propostas`.

### Listar/consultar proposta interna

- `GET /lead-propostas/lead/{lead_id}`
- `GET /lead-propostas/{proposta_id}`

Regra:

- listagem interna tende a ocultar inativas via `ativo = true`.

### Exibir proposta publica

`GET /lead-propostas/p/{public_hash}`

Regra:

- acesso sem autenticacao;
- lookup feito apenas pelo hash unico.

### Aceitar proposta publica

`POST /lead-propostas/p/{public_hash}/accept`

Passos observados:

1. carrega proposta pelo hash;
2. atualiza status para `aprovada`;
3. garante ou cria `lead_cadastros`;
4. monta `cadastro_url`;
5. envia email interno;
6. tenta enviar email ao cliente;
7. devolve token/URL de cadastro para o frontend.

### Cadastro PF

`PATCH /lead-cadastros/p/{token}/pf`

Passos:

1. localiza o cadastro principal por `token_publico`;
2. valida `tipo_cliente = pf`;
3. faz upsert em `lead_cadastros_pf`;
4. muda status para `pendente_documentos`.

## Regras de negocio importantes

### Proposta e diferente de contrato

- proposta e pre-contratual e comercial;
- contrato so nasce depois em outro modulo;
- aceitar proposta dispara onboarding, nao formaliza contrato automaticamente.

### Hash e token publicos

O modulo usa dois identificadores publicos diferentes:

- `public_hash` da proposta
- `token_publico` do cadastro

Isso separa:

- visualizacao/aceite comercial
- preenchimento cadastral/documental

### Payload flexivel

Os cenarios da proposta vivem em `payload jsonb`.

Conteudo observado:

- cliente
- lista de cenarios
- meta
- extras

### Status de proposta

Status vistos no codigo:

- `rascunho`
- `enviada`
- `aprovada`
- `recusada`
- `inativa`

Observacao:

- a migration antiga menciona nomes diferentes como `enviado`/`aceito`; o schema atual precisa ser confirmado no banco.

## Relacao com regras do consorcio

As propostas carregam dados de cenarios como:

- produto
- valor da carta
- prazo
- redutor
- fundo de reserva
- seguro prestamista
- lance fixo
- lance embutido

Ou seja, proposta representa simulacao/oferta comercial da futura cota, nao a cota operacional em si.

## Pontos pendentes de confirmacao

- campo `ativo` de `lead_propostas` nao aparece na migration incremental, mas e usado no codigo;
- suporte completo a cadastro PJ e upload documental ainda nao aparece em routers equivalentes ao fluxo PF;
- validacao de expiracao de `lead_cadastros.expires_at` nao esta implementada no backend atual.
