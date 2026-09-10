"""Central configuration. Every tunable in the system lives here or in a Params dataclass
that is persisted alongside its output, so results are always reproducible."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SURV_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://localhost:5432/surveillance"
    redis_url: str = "redis://localhost:6379/0"

    # Master seed. Each generator component derives an independent child stream from this,
    # so adding a new scenario does not perturb previously generated background data.
    seed: int = 20240917

    data_dir: Path = DATA_DIR

    @property
    def trades_path(self) -> Path:
        return self.data_dir / "trades.parquet"

    @property
    def ground_truth_path(self) -> Path:
        return self.data_dir / "ground_truth.jsonl"

    @property
    def reference_path(self) -> Path:
        return self.data_dir / "reference.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
