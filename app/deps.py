# app/deps.py
from functools import lru_cache

from supabase import create_client, Client  # supabase-py
from app.core.config import settings


@lru_cache
def _supabase_client() -> Client:
    url = settings.SUPABASE_URL
    key = settings.SUPABASE_SERVICE_ROLE_KEY

    if not url or not key:
        raise RuntimeError("SUPABASE_URL ou SUPABASE_SERVICE_ROLE_KEY faltando no .env")

    return create_client(url, key)


def get_supabase_admin() -> Client:
    # FastAPI vai chamar via Depends
    return _supabase_client()
