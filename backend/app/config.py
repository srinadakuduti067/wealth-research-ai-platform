from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    gcp_project: str = ""
    gcp_location: str = "us-central1"
    generation_location: str = "global"
    gemini_model: str = "gemini-3.5-flash"
    embedding_model: str = "text-embedding-005"
    embedding_dimensions: int = 768
    database_url: str = "postgresql://research:research@localhost:5432/research"
    web_database_url: str = ""
    quote_fixture_path: str = "data/quotes_fixture.json"
    quotes_mode: str = "live"  # "live" = public feed with fixture fallback; "fixture" = demo only

    @property
    def fixture_file(self) -> Path:
        path = Path(self.quote_fixture_path)
        return path if path.is_absolute() else ROOT / path


settings = Settings()
