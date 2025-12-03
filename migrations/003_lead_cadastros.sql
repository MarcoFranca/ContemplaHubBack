create table lead_cadastros (
    id              uuid primary key default gen_random_uuid(),

    org_id          uuid not null
                    references orgs(id) on delete cascade,

    lead_id         uuid not null
                    references leads(id) on delete cascade,

    proposta_id     uuid not null
                    references lead_propostas(id) on delete cascade,

    tipo_cliente    cadastro_cliente_tipo not null,

    status          cadastro_status not null
                    default 'pendente_dados',

    -- hash público pra página de onboarding (igual proposta pública)
    token_publico   text not null unique,

    -- se quiser registrar por onde foi disparado (página pública, interno, etc.)
    created_source  text default 'public_accept',   -- opcional

    -- metadados de auditoria
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),

    -- controle simples de expiração (ex.: cadastro expira após X dias)
    expires_at      timestamptz,

    -- IP e user-agent da primeira criação (opcional)
    first_ip        text,
    first_user_agent text
);

create index idx_lead_cadastros_org_lead
    on lead_cadastros (org_id, lead_id);

create index idx_lead_cadastros_org_proposta
    on lead_cadastros (org_id, proposta_id);

create index idx_lead_cadastros_token_publico
    on lead_cadastros (token_publico);


create table lead_cadastros_pf (
    cadastro_id     uuid primary key
                    references lead_cadastros(id) on delete cascade,

    -- DADOS PESSOAIS
    nome_completo           text,
    cpf                     text,
    data_nascimento         date,
    estado_civil            text,          -- pode virar enum depois
    nome_conjuge            text,
    cpf_conjuge             text,
    nome_mae                text,
    cidade_nascimento       text,
    nacionalidade           text,

    -- CONTATO
    email                   text,
    telefone_fixo           text,
    celular                 text,

    -- ENDEREÇO
    cep                     text,
    endereco                text,
    numero                  text,
    complemento             text,
    bairro                  text,
    cidade                  text,
    uf                      text,

    -- DOCUMENTO DE IDENTIDADE
    rg_numero               text,
    rg_orgao_emissor        text,
    rg_data_emissao         date,

    -- SITUAÇÃO PROFISSIONAL E RENDA
    profissao               text,
    renda_mensal            numeric(14,2),

    -- FORMA DE PAGAMENTO
    forma_pagamento         cadastro_forma_pagamento,
    banco_pagamento         text,
    agencia_pagamento       text,
    conta_pagamento         text,

    -- Conta pra devolução (se desejar separar)
    banco_devolucao         text,
    agencia_devolucao       text,
    conta_devolucao         text,

    -- LGPD / flags
    lgpd_consentimento      boolean default false,
    lgpd_consentido_em      timestamptz,

    -- espaço pra flexibilidade futura
    extra_json              jsonb
);


create type empresa_classificacao as enum (
  'igreja_evangelica_associacao_cooperativa_sindicato_sa',
  'igreja_catolica',
  'ltda_eireli',
  'me_mei',
  'produtor_rural',
  'outro'
);

create table lead_cadastros_pj (
    cadastro_id     uuid primary key
                    references lead_cadastros(id) on delete cascade,

    classificacao           empresa_classificacao,
    cnpj                    text,
    razao_social            text,
    nome_fantasia           text,
    inscricao_estadual      text,
    data_constituicao       date,

    email_empresa           text,
    telefone_comercial      text,
    telefone_celular        text,

    possui_residencia_fiscal_exterior boolean,

    ramo_atividade          text,
    receita_mensal          numeric(16,2),

    -- ENDEREÇO COMERCIAL
    cep_comercial           text,
    endereco_comercial      text,
    numero_comercial        text,
    complemento_comercial   text,
    bairro_comercial        text,
    cidade_comercial        text,
    uf_comercial            text,

    -- FORMA DE PAGAMENTO
    forma_pagamento         cadastro_forma_pagamento,
    banco_pagamento         text,
    agencia_pagamento       text,
    conta_pagamento         text,

    -- LGPD / flags
    lgpd_consentimento      boolean default false,
    lgpd_consentido_em      timestamptz,

    extra_json              jsonb
);


create table lead_cadastros_socios (
    id                  uuid primary key default gen_random_uuid(),
    cadastro_id         uuid not null
                        references lead_cadastros(id) on delete cascade,

    nome_completo       text,
    cpf                 text,
    data_nascimento     date,
    estado_civil        text,
    sexo                text,      -- 'M' / 'F' / etc (pode virar enum)

    nome_conjuge        text,
    cpf_conjuge         text,

    email               text,
    telefone_residencial text,
    telefone_comercial  text,
    telefone_celular    text,

    rg_numero           text,
    rg_orgao_emissor    text,
    rg_data_emissao     date,

    nacionalidade       text,
    cidade_nascimento   text,
    nome_mae            text,

    residencia_fiscal_exterior boolean,
    pessoa_politicamente_exposta boolean,
    profissao           text,
    remuneracao_mensal  numeric(16,2),

    -- Endereço residencial
    cep_residencial     text,
    endereco_residencial text,
    numero_residencial  text,
    complemento_residencial text,
    bairro_residencial  text,
    cidade_residencial  text,
    uf_residencial      text,

    -- se é o sócio que assina
    is_socio_assinante  boolean default false,

    created_at          timestamptz not null default now()
);

create index idx_socios_cadastro_id on lead_cadastros_socios(cadastro_id);


create table lead_cadastros_docs (
    id              uuid primary key default gen_random_uuid(),
    cadastro_id     uuid not null
                    references lead_cadastros(id) on delete cascade,

    -- se PF ou PJ, você consegue encontrar pelo cadastro
    doc_tipo        cadastro_doc_tipo not null,

    -- URL pública ou signed (você guarda o caminho do objeto)
    file_url        text not null,

    file_name       text,
    content_type    text,
    file_size_bytes bigint,

    uploaded_at     timestamptz not null default now(),

    -- alguma observação interna (ex.: "documento ilegível", etc.)
    observacao      text
);

create index idx_cadastros_docs_cadastro_id
    on lead_cadastros_docs(cadastro_id);
