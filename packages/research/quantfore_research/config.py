"""Environment-backed settings for research services and scripts."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    """Runtime settings for local research scripts.

    The default SQLite database keeps the first proof-engine iteration easy to
    run locally. Set QUANTFORE_DATABASE_URL for Postgres or another target.
    """

    database_url: str = "sqlite+pysqlite:///./quantfore_research.db"
    echo_sql: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("QUANTFORE_DATABASE_URL", cls.database_url),
            echo_sql=_env_bool("QUANTFORE_ECHO_SQL", cls.echo_sql),
        )
