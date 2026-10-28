"""Process settings, loaded from environment / .env. No secrets are hardcoded."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    tfl_app_key: str = ""
    # Register free at https://developer.data.nationalhighways.co.uk/
    nh_api_key: str = ""
    # NOTE: one.network has no collector (no feed spec); there is deliberately
    # no ONE_NETWORK_API_KEY here. Re-add it together with a collector if a
    # feed is ever specified.

    database_url: str = "sqlite:///./incidents.db"

    poll_interval_national_highways: int = 120
    poll_interval_tfl: int = 300
    poll_interval_traffic_scotland: int = 120
    poll_interval_traffic_watch_ni: int = 300

    # Only incidents reported within this window are exported/uploaded per run.
    export_lookback_hours: int = 6

    rainfall_dir: str = "/nfs/develop_pff/processing/UK/national/Rainfall/Hourly"

    # Empty = log to stdout. Prefer an NFS path, never /tmp or the root disk.
    log_path: str = ""
    log_max_bytes: int = 5_000_000
    log_backup_count: int = 3


settings = Settings()
