from datetime import datetime

from errorHandling.error import ApiError
from models import AppConfig, AuthInfo
from services.http_client import HttpClient
from validators.response_validator import ResponseValidator

class AuthService:
    def __init__(self, config: AppConfig, http_client: HttpClient):
        self.auth_url = config.auth_url
        self.http_client = http_client

    def authenticate(self) -> AuthInfo:
        payload = self.http_client.request_json("POST", self.auth_url)

        token = ResponseValidator.required_string(payload, "token")
        request_id = ResponseValidator.required_string(payload, "request_id")
        data_url = ResponseValidator.required_string(payload, "data_url")
        expires_at = self.parse_expires_at(payload.get("expires_at"))
        dataset = payload.get("dataset")

        return AuthInfo(
            token=token,
            request_id=request_id,
            data_url=data_url,
            expires_at=expires_at,
            dataset=dataset if isinstance(dataset, str) else None,
        )

    @staticmethod
    def parse_expires_at(value) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise ApiError("Response field 'expires_at' must be an ISO datetime string.")

        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise ApiError("Response field 'expires_at' must be a valid ISO datetime string.") from exc
