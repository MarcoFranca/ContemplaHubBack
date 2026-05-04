begin;

do $$
begin
    if exists (
        select 1
        from information_schema.columns
        where table_schema = 'public'
          and table_name = 'lead_stage_history'
          and column_name = 'from_stage'
          and udt_name = 'lead_stage'
    ) then
        execute 'alter table public.lead_stage_history alter column from_stage type text';
    end if;

    if exists (
        select 1
        from information_schema.columns
        where table_schema = 'public'
          and table_name = 'lead_stage_history'
          and column_name = 'to_stage'
          and udt_name = 'lead_stage'
    ) then
        execute 'alter table public.lead_stage_history alter column to_stage type text';
    end if;

    if exists (
        select 1
        from information_schema.columns
        where table_schema = 'public'
          and table_name = 'leads'
          and column_name = 'etapa'
          and udt_name = 'lead_stage'
    ) then
        execute 'alter table public.leads alter column etapa type text';
    end if;
end $$;

update public.leads
set etapa = 'contrato'
where etapa = 'fechamento';

update public.leads
set etapa = 'contato_realizado'
where etapa = 'contato';

update public.leads
set etapa = 'pos_venda'
where etapa = 'ativo';

update public.lead_stage_history
set from_stage = 'contrato'
where from_stage = 'fechamento';

update public.lead_stage_history
set to_stage = 'contrato'
where to_stage = 'fechamento';

update public.lead_stage_history
set from_stage = 'contato_realizado'
where from_stage = 'contato';

update public.lead_stage_history
set to_stage = 'contato_realizado'
where to_stage = 'contato';

update public.lead_stage_history
set from_stage = 'pos_venda'
where from_stage = 'ativo';

update public.lead_stage_history
set to_stage = 'pos_venda'
where to_stage = 'ativo';

do $$
begin
    if exists (select 1 from pg_type where typname = 'lead_stage') then
        drop type public.lead_stage;
    end if;
end $$;

create type public.lead_stage as enum (
    'novo',
    'tentativa_contato',
    'contato_realizado',
    'diagnostico',
    'proposta',
    'negociacao',
    'contrato',
    'pos_venda',
    'frio',
    'perdido'
);

alter table public.lead_stage_history
    alter column from_stage type public.lead_stage
    using case
        when from_stage is null then null
        else from_stage::public.lead_stage
    end;

alter table public.lead_stage_history
    alter column to_stage type public.lead_stage
    using to_stage::public.lead_stage;

alter table public.leads
    alter column etapa type public.lead_stage
    using etapa::public.lead_stage;

alter table public.leads
    alter column etapa set default 'novo'::public.lead_stage;

create or replace function public.get_kanban_metrics(p_org uuid)
returns jsonb
language sql
stable
as $$
with leads_org as (
    select *
    from public.leads
    where org_id = p_org
),
base as (
    select
        l.etapa,
        count(*)::int as count,
        avg(extract(epoch from (now() - l.created_at)) / 86400.0)::numeric as avg_days
    from leads_org l
    group by l.etapa
),
conv as (
    select
        coalesce(
            100.0 * sum(case when etapa in ('contrato', 'pos_venda') then 1 else 0 end)::numeric
            / nullif(count(*), 0),
            0
        )::numeric as conversion
    from leads_org
),
diag as (
    select
        l.etapa,
        avg(
            case
                when d.readiness_score is not null
                 and d.objetivo is not null
                 and d.valor_carta_alvo is not null
                then 1 else 0
            end
        )::numeric as diagnostic_completion_pct
    from leads_org l
    left join public.lead_diagnosticos d on d.lead_id = l.id
    group by l.etapa
),
ready as (
    select
        l.etapa,
        avg(coalesce(d.readiness_score, 0))::numeric as readiness_avg
    from leads_org l
    left join public.lead_diagnosticos d on d.lead_id = l.id
    group by l.etapa
),
t1c as (
    select
        l.etapa,
        avg(extract(epoch from (l.first_contact_at - l.created_at)) / 60.0)::numeric as t_first_contact_avg_min
    from leads_org l
    where l.first_contact_at is not null
    group by l.etapa
)
select coalesce(
    jsonb_agg(
        jsonb_build_object(
            'etapa', b.etapa,
            'count', b.count,
            'conversion', c.conversion,
            'avgDays', coalesce(b.avg_days, 0),
            'diagnosticCompletionPct', coalesce(di.diagnostic_completion_pct, 0),
            'readinessAvg', coalesce(r.readiness_avg, 0),
            'tFirstContactAvgMin', coalesce(t.t_first_contact_avg_min, 0)
        )
        order by b.etapa
    ),
    '[]'::jsonb
)
from base b
cross join conv c
left join diag di on di.etapa = b.etapa
left join ready r on r.etapa = b.etapa
left join t1c t on t.etapa = b.etapa;
$$;

commit;
