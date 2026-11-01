from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    # SQLite is the zero-setup default for local use; point this at PostgreSQL in production,
    # e.g. postgresql+psycopg2://journal:journal@localhost:5432/journal
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'data' / 'journal.db').as_posix()}"
    data_dir: Path = BACKEND_DIR / "data"

    frontend_url: str = "http://localhost:3000"
    backend_url: str = "http://localhost:8000"

    # Used to encrypt Google OAuth tokens at rest. Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    app_secret_key: str = ""

    # Google OAuth (Drive). Create an OAuth "Web application" client in Google Cloud Console and
    # add {backend_url}/api/drive/auth/callback as an authorized redirect URI.
    google_client_id: str = ""
    google_client_secret: str = ""
    sync_interval_minutes: int = 10

    # OpenRouter multimodal models. Comma-separated, tried in order: when one is rate-limited or
    # unavailable the next is used, e.g. "qwen/qwen3-vl-8b-instruct,nvidia/nemotron-3.5-super-vl:free"
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    vision_models: str = "nvidia/nemotron-3.5-super-vl:free,dots-studio/dots-3-note-preview:free,qwen/qwen3-vl-8b-instruct"
    assistant_models: str = "nvidia/nemotron-3.5-super-vl:free,dots-studio/dots-3-note-preview:free,qwen/qwen3-vl-8b-instruct"
    # Minutes to wait before retrying screenshots whose AI stage was rate-limited
    ai_retry_minutes: int = 30

    # OCR: path to the tesseract binary if it isn't on PATH
    tesseract_cmd: str = ""

    # Confidence thresholds (Stage 4)
    high_confidence: float = 0.85
    medium_confidence: float = 0.6

    @property
    def google_redirect_uri(self) -> str:
        return f"{self.backend_url}/api/drive/auth/callback"

    @property
    def vision_model_list(self) -> list[str]:
        return [m.strip() for m in self.vision_models.split(",") if m.strip()]

    @property
    def assistant_model_list(self) -> list[str]:
        return [m.strip() for m in self.assistant_models.split(",") if m.strip()]

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "drive_cache"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.uploads_dir.mkdir(parents=True, exist_ok=True)
    s.cache_dir.mkdir(parents=True, exist_ok=True)
    return s
