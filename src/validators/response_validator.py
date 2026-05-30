from typing import Any
from errorHandling.error import ApiError

class ResponseValidator:
    @staticmethod
    def required_string(payload: dict[str, Any], key: str, fallback: str | None = None) -> str:
        value = payload.get(key, fallback)
        if not isinstance(value, str) or not value:
            raise ApiError(f"Response is missing required string field {key!r}.")
        return value

    @staticmethod
    def required_positive_int(payload: dict[str, Any], key: str) -> int:
        value = payload.get(key)
        if not isinstance(value, int) or value < 1:
            raise ApiError(f"Response is missing required positive integer field {key!r}.")
        return value

    @staticmethod
    def required_list(payload: dict[str, Any], key: str) -> list[Any]:
        value = payload.get(key)
        if not isinstance(value, list):
            raise ApiError(f"Response is missing required list field {key!r}.")
        return value
