"""Service configuration.

Reads settings from environment variables, with Docker-secret file fallbacks
(the same secret files the Vizru platform containers use):

  MONGO_URL  or file at MONGO_URL_FILE   (default /run/secrets/mongo-url)
  MYSQL_DSN  or file at MYSQL_DSN_FILE   (default /run/secrets/mysql-dsn)
  LLM_API_KEY or file at LLM_API_KEY_FILE

The MySQL DSN uses the platform's PHP PDO format:
  mysql:host=receiver-mysql;dbname=vizru;user=vizru;password=vizru;
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache


def _read_secret(env_name: str, default_file: str) -> str | None:
    direct = os.environ.get(env_name)
    if direct:
        return direct.strip()
    path = os.environ.get(f"{env_name}_FILE", default_file)
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return None


def parse_php_pdo_dsn(dsn: str) -> dict:
    """Parse 'mysql:host=x;dbname=y;user=u;password=p;' into a dict."""
    body = dsn.split(":", 1)[1] if ":" in dsn else dsn
    out: dict[str, str] = {}
    for part in body.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out


@dataclass
class Settings:
    mongo_url: str = "mongodb://vizru:vizru@receiver-mongo:27017/vizru-live"
    mongo_db: str = "vizru-live"
    mysql_host: str = "receiver-mysql"
    mysql_port: int = 3306
    mysql_db: str = "vizru"
    mysql_user: str = "vizru"
    mysql_password: str = "vizru"

    # LLM (any OpenAI-compatible endpoint: OpenAI, OpenRouter, Groq, Ollama...)
    llm_api_key: str = ""
    llm_base_url: str | None = None
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.1
    llm_max_repair_attempts: int = 2

    # catalog cache
    catalog_ttl_seconds: int = 300

    # safety
    default_enable_log: str = "0"
    allow_types: list[str] = field(default_factory=list)  # empty = all


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()

    mongo_url = _read_secret("MONGO_URL", "/run/secrets/mongo-url")
    if mongo_url:
        s.mongo_url = mongo_url
    # db name from the URL path if present
    m = re.search(r"/([A-Za-z0-9_-]+)(?:\?|$)", s.mongo_url.split("@")[-1])
    if m:
        s.mongo_db = m.group(1)
    s.mongo_db = os.environ.get("MONGO_DB", s.mongo_db)

    mysql_dsn = _read_secret("MYSQL_DSN", "/run/secrets/mysql-dsn")
    if mysql_dsn:
        parts = parse_php_pdo_dsn(mysql_dsn)
        s.mysql_host = parts.get("host", s.mysql_host)
        s.mysql_port = int(parts.get("port", s.mysql_port))
        s.mysql_db = parts.get("dbname", s.mysql_db)
        s.mysql_user = parts.get("user", s.mysql_user)
        s.mysql_password = parts.get("password", s.mysql_password)
    for env, attr in (
        ("MYSQL_HOST", "mysql_host"), ("MYSQL_DB", "mysql_db"),
        ("MYSQL_USER", "mysql_user"), ("MYSQL_PASSWORD", "mysql_password"),
    ):
        if os.environ.get(env):
            setattr(s, attr, os.environ[env])
    if os.environ.get("MYSQL_PORT"):
        s.mysql_port = int(os.environ["MYSQL_PORT"])

    s.llm_api_key = _read_secret("LLM_API_KEY", "/run/secrets/llm-api-key") or ""
    s.llm_base_url = os.environ.get("LLM_BASE_URL") or None
    s.llm_model = os.environ.get("LLM_MODEL", s.llm_model)
    if os.environ.get("LLM_TEMPERATURE"):
        s.llm_temperature = float(os.environ["LLM_TEMPERATURE"])
    if os.environ.get("CATALOG_TTL_SECONDS"):
        s.catalog_ttl_seconds = int(os.environ["CATALOG_TTL_SECONDS"])
    return s
