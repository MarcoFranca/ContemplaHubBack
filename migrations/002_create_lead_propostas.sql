-- 002_create_lead_propostas.sql
-- Tabela de propostas comerciais ligadas a um lead

CREATE TABLE IF NOT EXISTS public.lead_propostas (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- escopo multi-org
    org_id  uuid NOT NULL REFERENCES public.orgs(id),
    lead_id uuid NOT NULL REFERENCES public.leads(id) ON DELETE CASCADE,

    -- identificação
    titulo   text,                          -- "Estratégia Autoconstrução 500k"
    campanha text,                          -- "Porto - Autoconstrução 40% redutor"
    status   text DEFAULT 'rascunho',       -- 'rascunho' | 'enviado' | 'aceito' | ...

    -- link público para o cliente
    public_hash text UNIQUE,                -- ex: "pX3f9Q" (backend gera)

    -- payload flexível com os cenários de carta
    payload jsonb NOT NULL DEFAULT '{}',

    -- URL opcional para PDF gerado
    pdf_url text,

    -- auditoria básica
    created_at timestamptz DEFAULT now(),
    created_by uuid REFERENCES public.profiles(user_id),

    updated_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS lead_propostas_org_id_idx
    ON public.lead_propostas(org_id);

CREATE INDEX IF NOT EXISTS lead_propostas_lead_id_idx
    ON public.lead_propostas(lead_id);

CREATE INDEX IF NOT EXISTS lead_propostas_public_hash_idx
    ON public.lead_propostas(public_hash);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_lead_propostas_updated'
    ) THEN
        CREATE TRIGGER trg_lead_propostas_updated
        BEFORE UPDATE ON public.lead_propostas
        FOR EACH ROW
        EXECUTE FUNCTION public.set_current_timestamp_updated_at();
    END IF;
END$$;

-- RLS
ALTER TABLE public.lead_propostas ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    -- SELECT
    IF NOT EXISTS (
        SELECT 1
        FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename  = 'lead_propostas'
          AND policyname = 'lead_propostas_org_select'
    ) THEN
        CREATE POLICY lead_propostas_org_select
        ON public.lead_propostas
        FOR SELECT
        USING (org_id = public.app_org_id());
    END IF;

    -- INSERT
    IF NOT EXISTS (
        SELECT 1
        FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename  = 'lead_propostas'
          AND policyname = 'lead_propostas_org_insert'
    ) THEN
        CREATE POLICY lead_propostas_org_insert
        ON public.lead_propostas
        FOR INSERT
        WITH CHECK (org_id = public.app_org_id());
    END IF;

    -- UPDATE
    IF NOT EXISTS (
        SELECT 1
        FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename  = 'lead_propostas'
          AND policyname = 'lead_propostas_org_update'
    ) THEN
        CREATE POLICY lead_propostas_org_update
        ON public.lead_propostas
        FOR UPDATE
        USING (org_id = public.app_org_id())
        WITH CHECK (org_id = public.app_org_id());
    END IF;

    -- DELETE
    IF NOT EXISTS (
        SELECT 1
        FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename  = 'lead_propostas'
          AND policyname = 'lead_propostas_org_delete'
    ) THEN
        CREATE POLICY lead_propostas_org_delete
        ON public.lead_propostas
        FOR DELETE
        USING (org_id = public.app_org_id());
    END IF;
END$$;

