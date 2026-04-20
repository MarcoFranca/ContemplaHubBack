ALTER TABLE public.leads
    ADD COLUMN IF NOT EXISTS cep text,
    ADD COLUMN IF NOT EXISTS logradouro text,
    ADD COLUMN IF NOT EXISTS numero text,
    ADD COLUMN IF NOT EXISTS complemento text,
    ADD COLUMN IF NOT EXISTS bairro text,
    ADD COLUMN IF NOT EXISTS cidade text,
    ADD COLUMN IF NOT EXISTS estado text,
    ADD COLUMN IF NOT EXISTS latitude double precision,
    ADD COLUMN IF NOT EXISTS longitude double precision,
    ADD COLUMN IF NOT EXISTS address_updated_at timestamptz;

CREATE INDEX IF NOT EXISTS idx_leads_org_estado
    ON public.leads (org_id, estado);

CREATE INDEX IF NOT EXISTS idx_leads_org_cidade
    ON public.leads (org_id, cidade);

CREATE INDEX IF NOT EXISTS idx_leads_org_bairro
    ON public.leads (org_id, bairro);

CREATE INDEX IF NOT EXISTS idx_leads_org_lat_lng
    ON public.leads (org_id, latitude, longitude);
