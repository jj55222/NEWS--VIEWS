"""
Central configuration. Loaded once, passed everywhere.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv


@dataclass
class Config:
    """Pipeline configuration loaded from environment."""

    # API keys
    exa_api_key: str = ""
    openrouter_api_key: str = ""
    openrouter_model: str = "deepseek/deepseek-v3.2"

    # Google Sheets (optional — pipeline can run without it)
    sheet_id: str = ""
    service_account_path: str = "./service_account.json"

    # Search defaults
    default_start_date: str = "2018-01-01"
    default_end_date: str = "2025-12-31"
    max_results_per_region: int = 30
    min_article_length: int = 500

    # Scoring thresholds
    min_prescore: int = 20
    min_story_value: float = 30.0
    min_researchability: float = 25.0

    # Rate limiting (seconds)
    exa_sleep: float = 0.3
    llm_sleep: float = 0.5
    region_sleep: float = 1.0

    # Output
    output_dir: str = "./output"
    log_dir: str = "./output/logs"

    @classmethod
    def from_env(cls, dotenv_path: str = None) -> "Config":
        """Load config from environment variables."""
        if dotenv_path:
            load_dotenv(dotenv_path)
        else:
            load_dotenv()

        return cls(
            exa_api_key=os.getenv("EXA_API_KEY", ""),
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
            openrouter_model=os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v3.2"),
            sheet_id=os.getenv("SHEET_ID", ""),
            service_account_path=os.getenv("SERVICE_ACCOUNT_PATH", "./service_account.json"),
            default_start_date=os.getenv("DEFAULT_START_DATE", "2018-01-01"),
            default_end_date=os.getenv("DEFAULT_END_DATE", "2025-12-31"),
            max_results_per_region=int(os.getenv("MAX_RESULTS_PER_REGION", "30")),
            min_article_length=int(os.getenv("MIN_ARTICLE_LENGTH", "500")),
            min_prescore=int(os.getenv("MIN_PRESCORE", "20")),
            output_dir=os.getenv("OUTPUT_DIR", "./output"),
            log_dir=os.getenv("LOG_DIR", "./output/logs"),
        )

    def validate(self) -> list[str]:
        """Return list of missing required config values."""
        errors = []
        if not self.exa_api_key:
            errors.append("EXA_API_KEY not set")
        if not self.openrouter_api_key:
            errors.append("OPENROUTER_API_KEY not set")
        return errors

    def ensure_dirs(self):
        """Create output directories if needed."""
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        Path(self.log_dir).mkdir(parents=True, exist_ok=True)
