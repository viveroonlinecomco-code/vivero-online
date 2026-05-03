"""Configuración centralizada. Lee variables de entorno con validación."""
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Supabase
    supabase_url: str
    supabase_anon_key: str
    supabase_service_key: str
    supabase_jwt_secret: str

    # Twilio
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_verify_service_sid: str
    twilio_whatsapp_from: str = "whatsapp:+14155238886"

    # Gemini
    gemini_api_key: str
    gemini_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "text-embedding-004"

    # YOLO (opcional)
    ultralytics_api_key: str = ""

    # ePayco
    epayco_public_key: str = ""
    epayco_private_key: str = ""
    epayco_p_cust_id: str = ""        # P_CUST_ID_CLIENTE para validar firma
    epayco_p_key: str = ""            # P_KEY para validar firma

    # App
    app_base_url: str = "http://localhost:3000"
    env: str = Field(default="development")

    @property
    def is_production(self) -> bool:
        return self.env.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
