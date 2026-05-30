from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from errorHandling.error import ApiError
from models import AppConfig, AuthInfo
from services.http_client import HttpClient
from validators.response_validator import ResponseValidator

class WeatherService:
    def __init__(self, config: AppConfig, http_client: HttpClient):
        self.max_retries = config.max_retries
        self.max_parallel_requests = config.max_parallel_requests
        self.token_expiry_buffer_seconds = config.token_expiry_buffer_seconds
        self.http_client = http_client

    def fetch_average_temperature(self, auth: AuthInfo) -> tuple[str, float]:
        first_page = self.fetch_page(auth, 1)
        total_pages = ResponseValidator.required_positive_int(first_page, "total_pages")
        city = ResponseValidator.required_string(first_page, "city", fallback=auth.dataset)
        total_temperature = 0.0
        item_count = 0

        for page in self.fetch_pages(auth, first_page, total_pages):
            page_total, page_count = self.aggregate_page(page, city)
            total_temperature += page_total
            item_count += page_count

        if item_count == 0:
            raise ApiError("The data API returned no temperature records.")

        return city, total_temperature / item_count

    def fetch_pages(
        self,
        auth: AuthInfo,
        first_page: dict[str, Any],
        total_pages: int,
    ) -> list[dict[str, Any]]:
        if total_pages == 1:
            return [first_page]

        page_numbers = range(2, total_pages + 1)
        max_workers = min(self.max_parallel_requests, total_pages - 1)
        pages = [first_page]

        with self.create_executor(max_workers) as executor:
            futures = [
                executor.submit(self.fetch_page, auth, page_number)
                for page_number in page_numbers
            ]
            for future in as_completed(futures):
                pages.append(future.result())

        return pages

    def create_executor(self, max_workers: int) -> ThreadPoolExecutor:
        return ThreadPoolExecutor(max_workers=max_workers)

    def aggregate_page(self, page: dict[str, Any], city: str) -> tuple[float, int]:
        page_city = ResponseValidator.required_string(page, "city", fallback=city)
        if page_city != city:
            raise ApiError(f"Unexpected city mismatch: got {page_city!r}, expected {city!r}.")

        page_total = 0.0
        page_count = 0
        for item in ResponseValidator.required_list(page, "items"):
            page_total += self.extract_temperature(item)
            page_count += 1

        return page_total, page_count

    def fetch_page(self, auth: AuthInfo, page_number: int) -> dict[str, Any]:
        self.ensure_token_is_usable(auth)

        url = self.with_query(
            auth.data_url,
            {
                "request_id": auth.request_id,
                "page": str(page_number),
            },
        )
        headers = {"Authorization": f"Bearer {auth.token}"}

        for attempt in range(1, self.max_retries + 1):
            payload = self.http_client.request_json("POST", url, headers=headers)

            if "error" not in payload and "items" in payload:
                return payload

            message = payload.get("error") or payload.get("message") or "Data response is missing 'items'."
            if attempt == self.max_retries:
                raise ApiError(str(message), retryable=True)

            self.http_client.sleep_before_retry(attempt)

        raise ApiError(f"Page {page_number} failed after retries.")

    def ensure_token_is_usable(self, auth: AuthInfo) -> None:
        if auth.expires_at is None:
            return

        expires_at = auth.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        safety_deadline = datetime.now(timezone.utc) + timedelta(seconds=self.token_expiry_buffer_seconds)
        if expires_at <= safety_deadline:
            raise ApiError("Access token is expired or about to expire.", status=401)

    @staticmethod
    def with_query(url: str, params: dict[str, str]) -> str:
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}{urlencode(params)}"

    @staticmethod
    def extract_temperature(item: Any) -> float:
        if not isinstance(item, dict):
            raise ApiError("Temperature record is not an object.")

        for key in ("temperature_noon_c", "temperature_c", "temperature", "temp"):
            value = item.get(key)
            if isinstance(value, (int, float)):
                return float(value)

        raise ApiError(f"Temperature record is missing a numeric temperature: {item!r}")
