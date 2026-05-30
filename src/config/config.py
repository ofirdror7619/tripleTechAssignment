import json
from pathlib import Path
from typing import Any
from errorHandling.error import ApiError
from models import AppConfig

CONFIG_PATH = Path(__file__).with_name("config.json")

class ConfigLoader:
    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> AppConfig:
        try:
            raw_config = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ApiError(f"Configuration file not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ApiError(f"Invalid JSON in configuration file {path}: {exc}") from exc

        if not isinstance(raw_config, dict):
            raise ApiError("Configuration root must be a JSON object.")

        urls = raw_config.get("urls")
        if not isinstance(urls, dict):
            raise ApiError("Configuration must include a 'urls' object.")

        auth_url = urls.get("auth")
        if not isinstance(auth_url, str) or not auth_url:
            raise ApiError("Configuration must include urls.auth as a non-empty string.")

        return AppConfig(
            auth_url=auth_url,
            max_retries=cls.required_positive_int(raw_config, "max_retries"),
            max_auth_sessions=cls.required_positive_int(raw_config, "max_auth_sessions"),
            max_parallel_requests=cls.required_positive_int(raw_config, "max_parallel_requests"),
            timeout_seconds=cls.required_positive_number(raw_config, "timeout_seconds"),
            token_expiry_buffer_seconds=cls.required_non_negative_number(
                raw_config,
                "token_expiry_buffer_seconds",
            ),
            transient_statuses=frozenset(cls.required_status_list(raw_config, "transient_statuses")),
        )

    @staticmethod
    def required_positive_int(config: dict[str, Any], key: str) -> int:
        value = config.get(key)
        if not isinstance(value, int) or value < 1:
            raise ApiError(f"Configuration value {key!r} must be a positive integer.")
        return value

    @staticmethod
    def required_positive_number(config: dict[str, Any], key: str) -> float:
        value = config.get(key)
        if not isinstance(value, (int, float)) or value <= 0:
            raise ApiError(f"Configuration value {key!r} must be a positive number.")
        return float(value)

    @staticmethod
    def required_non_negative_number(config: dict[str, Any], key: str) -> float:
        value = config.get(key)
        if not isinstance(value, (int, float)) or value < 0:
            raise ApiError(f"Configuration value {key!r} must be a non-negative number.")
        return float(value)

    @staticmethod
    def required_status_list(config: dict[str, Any], key: str) -> list[int]:
        value = config.get(key)
        if not isinstance(value, list) or not value:
            raise ApiError(f"Configuration value {key!r} must be a non-empty list.")

        statuses: list[int] = []
        for status in value:
            if not isinstance(status, int) or not 100 <= status <= 599:
                raise ApiError(f"Configuration value {key!r} must contain valid HTTP status codes.")
            statuses.append(status)

        return statuses
