ALTER TABLE public.cotas
    ADD COLUMN IF NOT EXISTS taxa_admin_antecipada_ativo boolean DEFAULT false,
    ADD COLUMN IF NOT EXISTS taxa_admin_antecipada_percentual numeric,
    ADD COLUMN IF NOT EXISTS taxa_admin_antecipada_forma_pagamento text,
    ADD COLUMN IF NOT EXISTS taxa_admin_antecipada_parcelas integer,
    ADD COLUMN IF NOT EXISTS taxa_admin_antecipada_valor_total numeric,
    ADD COLUMN IF NOT EXISTS taxa_admin_antecipada_valor_parcela numeric;
