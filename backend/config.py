"""Application configuration — everything comes from environment variables (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # PostgreSQL
    postgres_host: str = field(default_factory=lambda: os.getenv("POSTGRES_HOST", "localhost"))
    postgres_port: int = field(default_factory=lambda: _int("POSTGRES_PORT", 5432))
    postgres_db: str = field(default_factory=lambda: os.getenv("POSTGRES_DB", "ai_database"))
    postgres_user: str = field(default_factory=lambda: os.getenv("POSTGRES_USER", "postgres"))
    postgres_password: str = field(default_factory=lambda: os.getenv("POSTGRES_PASSWORD", ""))
    # Optional separate (least-privilege) role used ONLY to run user SQL. Falls back to the main role.
    exec_user: str = field(default_factory=lambda: os.getenv("POSTGRES_EXEC_USER", ""))
    exec_password: str = field(default_factory=lambda: os.getenv("POSTGRES_EXEC_PASSWORD", ""))
    pool_min: int = field(default_factory=lambda: _int("DB_POOL_MIN", 1))
    pool_max: int = field(default_factory=lambda: _int("DB_POOL_MAX", 10))
    connect_timeout: int = field(default_factory=lambda: _int("DB_CONNECT_TIMEOUT", 5))
    statement_timeout_ms: int = field(default_factory=lambda: _int("SQL_STATEMENT_TIMEOUT_MS", 15000))
    max_result_rows: int = field(default_factory=lambda: _int("SQL_MAX_RESULT_ROWS", 1000))

    # Ollama / SLM (local only)
    ollama_base_url: str = field(default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"))
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "qwen2.5:3b"))
    ollama_timeout: int = field(default_factory=lambda: _int("OLLAMA_TIMEOUT_SECONDS", 120))

    # Auth
    secret_key: str = field(default_factory=lambda: os.getenv("SECRET_KEY", ""))
    session_minutes: int = field(default_factory=lambda: _int("SESSION_EXPIRE_MINUTES", 60))
    cookie_secure: bool = field(default_factory=lambda: _bool("COOKIE_SECURE", False))
    login_max_failures: int = field(default_factory=lambda: _int("LOGIN_MAX_FAILURES", 5))
    login_lockout_minutes: int = field(default_factory=lambda: _int("LOGIN_LOCKOUT_MINUTES", 15))
    trusted_proxies: tuple[str, ...] = field(
        default_factory=lambda: tuple(x.strip() for x in os.getenv("TRUSTED_PROXIES", "127.0.0.1,::1").split(",") if x.strip())
    )

    max_upload_mb: int = field(default_factory=lambda: _int("MAX_UPLOAD_MB", 5))
    max_attach_rows: int = field(default_factory=lambda: _int("MAX_ATTACH_ROWS", 500))
    max_attach_columns: int = field(default_factory=lambda: _int("MAX_ATTACH_COLUMNS", 50))

    # Multi-query execution
    max_statements: int = field(default_factory=lambda: _int("MAX_STATEMENTS", 500))
    max_sql_length: int = field(default_factory=lambda: _int("MAX_SQL_LENGTH", 1_000_000))
    max_export_rows: int = field(default_factory=lambda: _int("MAX_EXPORT_ROWS", 20000))

    # Permission matrix override (JSON file) — see backend/auth/permissions.py
    permissions_file: str = field(default_factory=lambda: os.getenv("PERMISSIONS_FILE", ""))

    @property
    def conninfo(self) -> str:
        return self._conninfo(self.postgres_user, self.postgres_password)

    @property
    def exec_conninfo(self) -> str:
        if self.exec_user:
            return self._conninfo(self.exec_user, self.exec_password)
        return self.conninfo

    def _conninfo(self, user: str, password: str) -> str:
        from psycopg.conninfo import make_conninfo

        return make_conninfo(
            host=self.postgres_host, port=self.postgres_port, dbname=self.postgres_db,
            user=user, password=password, connect_timeout=self.connect_timeout,
            application_name="ai-database-assistant",
        )

    def validate(self) -> None:
        if len(self.secret_key) < 32:
            raise RuntimeError(
                "SECRET_KEY must be set (>= 32 chars). Generate one with: "
                "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )


settings = Settings()
