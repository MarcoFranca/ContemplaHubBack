from typing import Optional
from supabase import Client

from app.services.comissao_competencia_service import processar_pagamento_para_comissao


def on_pagamento_saved(
    supa: Client,
    *,
    org_id: str,
    pagamento_id: str,
    actor_id: Optional[str] = None,
) -> None:
    processar_pagamento_para_comissao(
        supa,
        org_id=org_id,
        pagamento_id=pagamento_id,
        actor_id=actor_id,
    )