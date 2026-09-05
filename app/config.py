from __future__ import annotations

import logging
import secrets
import sys

from pydantic import Field
from pydantic_settings import BaseSettings

logger = logging.getLogger("netbox_twenty.config")


def _generate_secret(name: str) -> str:
    secret = secrets.token_hex(32)
    print(
        f"[CONFIG] No {name} provided. Auto-generated secret: {secret}",
        file=sys.stderr,
    )
    logger.info("Auto-generated secret for %s", name)
    return secret


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # NetBox
    netbox_url: str = "http://netbox:8000"
    netbox_token: str = ""

    # Twenty CRM
    twenty_url: str = "http://twenty:3000"
    twenty_api_key: str = ""

    # Valkey
    valkey_host: str = "valkey"
    valkey_port: int = 6379

    # Public URL for webhook registrations
    public_base_url: str = "http://localhost:8000"

    # Webhook secrets – auto-generated if not set
    netbox_webhook_secret: str = Field(
        default_factory=lambda: _generate_secret("NETBOX_WEBHOOK_SECRET"),
    )
    twenty_webhook_token: str = Field(
        default_factory=lambda: _generate_secret("TWENTY_WEBHOOK_TOKEN"),
    )

    # Sync source identifier for loop prevention
    sync_source_header: str = "X-Sync-Source"
    sync_source_value: str = "NetboxTwentyMiddleware"


def get_settings() -> Settings:
    return Settings()
