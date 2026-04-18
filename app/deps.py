# app/deps.py
from supabase import Client, create_client

from app.core.config import settings


def get_supabase_admin() -> Client:
    """
    Retorna uma instância nova do client do Supabase por request.

    Isso evita reaproveitar indefinidamente a mesma sessão/conexão HTTP,
    o que pode causar problemas intermitentes em httpx/httpcore/http2.
    """
    url = settings.SUPABASE_URL
    key = settings.SUPABASE_SERVICE_ROLE_KEY

    if not url or not key:
        raise RuntimeError("SUPABASE_URL ou SUPABASE_SERVICE_ROLE_KEY faltando no .env")

    return create_client(url, key)