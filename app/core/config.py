# app/core/config.py
from pydantic import BaseModel
from dotenv import load_dotenv
import os

load_dotenv()


class Settings(BaseModel):
    APP_NAME: str = "Autentika Backend"
    ENV: str = os.getenv("ENV", "dev")

    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_SERVICE_ROLE_KEY: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

    PARTNER_INVITE_REDIRECT_TO: str = os.getenv(
        "PARTNER_INVITE_REDIRECT_TO",
        "http://localhost:3000/auth/callback",
    )

    CONTRACTS_BUCKET: str = os.getenv("CONTRACTS_BUCKET", "contracts")
    CONTRACTS_MAX_FILE_BYTES: int = int(
        os.getenv("CONTRACTS_MAX_FILE_BYTES", str(5 * 1024 * 1024))
    )
    CONTRACTS_SIGNED_URL_EXPIRES_IN: int = int(
        os.getenv("CONTRACTS_SIGNED_URL_EXPIRES_IN", "300")
    )
    META_GRAPH_API_BASE: str = os.getenv(
        "META_GRAPH_API_BASE",
        "https://graph.facebook.com/v22.0",
    )
    META_APP_ID: str = os.getenv("META_APP_ID", "")
    META_APP_SECRET: str = os.getenv("META_APP_SECRET", "")
    META_VERIFY_TOKEN: str = os.getenv("META_VERIFY_TOKEN", "")
    META_OAUTH_SCOPES: str = os.getenv(
        "META_OAUTH_SCOPES",
        "pages_show_list,pages_read_engagement,pages_manage_metadata,leads_retrieval,business_management",
    )
    BACKEND_PUBLIC_URL: str = os.getenv("BACKEND_PUBLIC_URL", "http://localhost:8000")
    FRONTEND_SITE_URL: str = os.getenv("FRONTEND_SITE_URL", "http://localhost:3000")

    class Config:
        frozen = True


settings = Settings()
