import json
import random
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from errorHandling.error import ApiError
from models import AppConfig

class HttpClient:
    def __init__(self, config: AppConfig):
        self.timeout_seconds = config.timeout_seconds
        self.max_retries = config.max_retries
        self.transient_statuses = config.transient_statuses

    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                return self.request_json_once(method, url, headers=headers)
            except ApiError as exc:
                last_error = exc
                if not exc.retryable or attempt == self.max_retries:
                    raise
            except (TimeoutError, URLError) as exc:
                last_error = exc
                if attempt == self.max_retries:
                    raise ApiError(f"Network error after retries: {exc}", retryable=True) from exc

            self.sleep_before_retry(attempt)

        raise ApiError(f"Request failed after retries: {last_error}")

    def request_json_once(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        request_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            **(headers or {}),
        }
        request = Request(
            url,
            data=b"{}" if method.upper() == "POST" else None,
            headers=request_headers,
            method=method.upper(),
        )

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
                status = response.status
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            message = self.extract_error_message(body) or exc.reason
            raise ApiError(
                f"HTTP {exc.code}: {message}",
                status=exc.code,
                retryable=exc.code in self.transient_statuses,
            ) from exc

        if status in self.transient_statuses:
            raise ApiError(f"HTTP {status}", status=status, retryable=True)

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ApiError(f"Invalid JSON response: {exc}", retryable=True) from exc

        if not isinstance(payload, dict):
            raise ApiError("Expected a JSON object response.")

        return payload

    @staticmethod
    def extract_error_message(body: str) -> str | None:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return body.strip() or None

        if isinstance(payload, dict):
            message = payload.get("message") or payload.get("error")
            return str(message) if message is not None else None

        return None

    @staticmethod
    def sleep_before_retry(attempt: int) -> None:
        delay = min(0.5 * (2 ** (attempt - 1)), 5.0)
        time.sleep(delay + random.uniform(0.0, 0.25))
