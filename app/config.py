from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # По умолчанию — Postgres из docker-compose, проброшенный на localhost:5442.
    database_url: str = "postgresql+psycopg://checkstock:checkstock@localhost:5442/checkstock"
    # Разрешает POST /api/demo/reset — сброс к тестовым данным из задания.
    demo_mode: bool = True


settings = Settings()
