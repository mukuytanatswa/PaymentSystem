from decimal import Decimal
from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str

    @field_validator('database_url', mode='before')
    @classmethod
    def fix_database_url_scheme(cls, v: str) -> str:
        if v.startswith('postgres://'):
            return v.replace('postgres://', 'postgresql+asyncpg://', 1)
        if v.startswith('postgresql://'):
            return v.replace('postgresql://', 'postgresql+asyncpg://', 1)
        return v
    stitch_client_id: str
    stitch_client_secret: str
    stitch_redirect_uri: str
    stitch_api_url: str = "https://api.stitch.money/graphql"
    smile_identity_api_key: str
    smile_identity_partner_id: str
    smile_identity_base_url: str = "https://api.smileidentity.com"
    stitch_webhook_secret: str
    admin_api_key: str
    encryption_key: str
    max_split_retries: int = 3
    max_retry_queue_size: int = 50
    dlq_alert_webhook_url: str = ""
    sentry_dsn: str = ""
    default_fee_percentage: Decimal = Decimal("2.5")
    environment: str = "development"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
