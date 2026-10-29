from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EET_", env_file=".env", extra="ignore")

    app_name: str = "Validador EET"
    database_url: str = "postgresql+psycopg://eet:eet@localhost:5433/eet"
    upload_dir: str = "./uploads"
    srid: int = 4326  # WGS84; ajustar al sistema de referencia de la malla oficial
    regla_version: str = "tabla7-v1"

    @property
    def upload_path(self) -> Path:
        p = Path(self.upload_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()