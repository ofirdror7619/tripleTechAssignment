import io
import json
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from config import ConfigLoader
from errorHandling.error import ApiError
from main import HomeworkClient
from models import AppConfig, AuthInfo
from services.auth_service import AuthService
from services.http_client import HttpClient
from services.weather_service import WeatherService
from validators.response_validator import ResponseValidator


class FakeHttpClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.sleep_attempts = []
        self.lock = threading.Lock()

    def request_json(self, method, url, *, headers=None):
        with self.lock:
            self.calls.append({"method": method, "url": url, "headers": headers or {}})
            if not self.responses:
                raise AssertionError("No fake HTTP response configured.")

            response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def sleep_before_retry(self, attempt):
        with self.lock:
            self.sleep_attempts.append(attempt)


class PageAwareHttpClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []
        self.lock = threading.Lock()

    def request_json(self, method, url, *, headers=None):
        page = int(parse_qs(urlparse(url).query)["page"][0])
        with self.lock:
            self.calls.append({"method": method, "url": url, "headers": headers or {}, "page": page})

        try:
            response = self.pages[page]
        except KeyError as exc:
            raise AssertionError(f"No fake page configured for page {page}.") from exc
        if isinstance(response, Exception):
            raise response
        return response

    def sleep_before_retry(self, attempt):
        pass


class RecordingWeatherService(WeatherService):
    def __init__(self, config, http_client):
        super().__init__(config, http_client)
        self.worker_counts = []

    def create_executor(self, max_workers):
        self.worker_counts.append(max_workers)
        return super().create_executor(max_workers)


class FakeAuthService:
    def __init__(self, auth_results):
        self.auth_results = list(auth_results)
        self.call_count = 0

    def authenticate(self):
        self.call_count += 1
        return self.auth_results.pop(0)


class FakeWeatherService:
    def __init__(self, results):
        self.results = list(results)
        self.call_count = 0

    def fetch_average_temperature(self, auth):
        self.call_count += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FlakyHttpClient(HttpClient):
    def __init__(self, config, results):
        super().__init__(config)
        self.results = list(results)
        self.sleep_attempts = []

    def request_json_once(self, method, url, *, headers=None):
        if not self.results:
            raise AssertionError("No fake HTTP result configured.")

        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def sleep_before_retry(self, attempt):
        self.sleep_attempts.append(attempt)


def test_config(**overrides):
    values = {
        "auth_url": "https://example.test/auth",
        "max_retries": 3,
        "max_auth_sessions": 2,
        "max_parallel_requests": 3,
        "timeout_seconds": 5.0,
        "token_expiry_buffer_seconds": 5.0,
        "transient_statuses": frozenset({408, 429, 500}),
    }
    values.update(overrides)
    return AppConfig(**values)


def valid_config_dict(**overrides):
    values = {
        "urls": {"auth": "https://example.test/auth"},
        "max_retries": 3,
        "max_auth_sessions": 2,
        "max_parallel_requests": 3,
        "timeout_seconds": 5.0,
        "token_expiry_buffer_seconds": 5.0,
        "transient_statuses": [408, 429, 500],
    }
    values.update(overrides)
    return values


def write_temp_config(config):
    temp_dir = tempfile.TemporaryDirectory()
    config_path = Path(temp_dir.name) / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return temp_dir, config_path


class AuthServiceTests(unittest.TestCase):
    def test_authenticate_returns_auth_info(self):
        http_client = FakeHttpClient(
            [
                {
                    "token": "abc",
                    "expires_at": "2026-05-29T14:15:54+00:00",
                    "request_id": "request-1",
                    "data_url": "https://example.test/data",
                    "dataset": "venice",
                }
            ]
        )

        auth = AuthService(test_config(), http_client).authenticate()

        self.assertEqual(auth.token, "abc")
        self.assertEqual(auth.expires_at, datetime(2026, 5, 29, 14, 15, 54, tzinfo=timezone.utc))
        self.assertEqual(auth.request_id, "request-1")
        self.assertEqual(auth.data_url, "https://example.test/data")
        self.assertEqual(auth.dataset, "venice")
        self.assertEqual(http_client.calls[0]["method"], "POST")

    def test_authenticate_rejects_missing_token(self):
        http_client = FakeHttpClient(
            [
                {
                    "request_id": "request-1",
                    "data_url": "https://example.test/data",
                }
            ]
        )

        with self.assertRaisesRegex(ApiError, "token"):
            AuthService(test_config(), http_client).authenticate()

    def test_authenticate_rejects_invalid_expires_at(self):
        http_client = FakeHttpClient(
            [
                {
                    "token": "abc",
                    "expires_at": "not-a-date",
                    "request_id": "request-1",
                    "data_url": "https://example.test/data",
                }
            ]
        )

        with self.assertRaisesRegex(ApiError, "valid ISO datetime"):
            AuthService(test_config(), http_client).authenticate()


class WeatherServiceTests(unittest.TestCase):
    def test_fetch_average_temperature_across_all_pages(self):
        http_client = FakeHttpClient(
            [
                {
                    "page": 1,
                    "total_pages": 2,
                    "city": "venice",
                    "items": [
                        {"temperature_noon_c": 10.0},
                        {"temperature_noon_c": 20.0},
                    ],
                },
                {
                    "page": 2,
                    "total_pages": 2,
                    "city": "venice",
                    "items": [
                        {"temperature_noon_c": 30.0},
                        {"temperature_noon_c": 40.0},
                    ],
                },
            ]
        )
        auth = AuthInfo(token="token-1", request_id="request-1", data_url="https://example.test/data")

        city, average = WeatherService(test_config(), http_client).fetch_average_temperature(auth)

        self.assertEqual(city, "venice")
        self.assertEqual(average, 25.0)
        self.assertEqual(len(http_client.calls), 2)
        self.assertIn("request_id=request-1", http_client.calls[0]["url"])
        self.assertIn("page=1", http_client.calls[0]["url"])
        self.assertEqual(http_client.calls[0]["headers"]["Authorization"], "Bearer token-1")

    def test_fetch_average_temperature_fetches_remaining_pages(self):
        http_client = PageAwareHttpClient(
            {
                1: {
                    "page": 1,
                    "total_pages": 3,
                    "city": "venice",
                    "items": [{"temperature_noon_c": 10.0}],
                },
                2: {
                    "page": 2,
                    "total_pages": 3,
                    "city": "venice",
                    "items": [{"temperature_noon_c": 20.0}],
                },
                3: {
                    "page": 3,
                    "total_pages": 3,
                    "city": "venice",
                    "items": [{"temperature_noon_c": 30.0}],
                },
            }
        )
        auth = AuthInfo(token="token-1", request_id="request-1", data_url="https://example.test/data")

        city, average = WeatherService(test_config(max_parallel_requests=2), http_client).fetch_average_temperature(auth)

        requested_pages = {call["page"] for call in http_client.calls}
        self.assertEqual(city, "venice")
        self.assertEqual(average, 20.0)
        self.assertEqual(len(http_client.calls), 3)
        self.assertEqual(requested_pages, {1, 2, 3})

    def test_parallel_fetch_propagates_worker_errors(self):
        http_client = PageAwareHttpClient(
            {
                1: {
                    "page": 1,
                    "total_pages": 3,
                    "city": "venice",
                    "items": [{"temperature_noon_c": 10.0}],
                },
                2: ApiError("parallel page failed", status=500),
                3: {
                    "page": 3,
                    "total_pages": 3,
                    "city": "venice",
                    "items": [{"temperature_noon_c": 30.0}],
                },
            }
        )
        auth = AuthInfo(token="token-1", request_id="request-1", data_url="https://example.test/data")

        with self.assertRaisesRegex(ApiError, "parallel page failed"):
            WeatherService(test_config(max_parallel_requests=2), http_client).fetch_average_temperature(auth)

    def test_parallel_fetch_rejects_invalid_parallel_page_items(self):
        http_client = PageAwareHttpClient(
            {
                1: {
                    "page": 1,
                    "total_pages": 2,
                    "city": "venice",
                    "items": [{"temperature_noon_c": 10.0}],
                },
                2: {
                    "page": 2,
                    "total_pages": 2,
                    "city": "venice",
                    "items": "not-a-list",
                },
            }
        )
        auth = AuthInfo(token="token-1", request_id="request-1", data_url="https://example.test/data")

        with self.assertRaisesRegex(ApiError, "items"):
            WeatherService(test_config(max_parallel_requests=2), http_client).fetch_average_temperature(auth)

    def test_parallel_fetch_limits_worker_count(self):
        http_client = PageAwareHttpClient(
            {
                1: {"page": 1, "total_pages": 5, "city": "venice", "items": [{"temperature_noon_c": 10.0}]},
                2: {"page": 2, "total_pages": 5, "city": "venice", "items": [{"temperature_noon_c": 20.0}]},
                3: {"page": 3, "total_pages": 5, "city": "venice", "items": [{"temperature_noon_c": 30.0}]},
                4: {"page": 4, "total_pages": 5, "city": "venice", "items": [{"temperature_noon_c": 40.0}]},
                5: {"page": 5, "total_pages": 5, "city": "venice", "items": [{"temperature_noon_c": 50.0}]},
            }
        )
        service = RecordingWeatherService(test_config(max_parallel_requests=2), http_client)
        auth = AuthInfo(token="token-1", request_id="request-1", data_url="https://example.test/data")

        city, average = service.fetch_average_temperature(auth)

        self.assertEqual(city, "venice")
        self.assertEqual(average, 30.0)
        self.assertEqual(service.worker_counts, [2])

    def test_fetch_page_retries_api_error_response(self):
        http_client = FakeHttpClient(
            [
                {"error": "temporary data issue"},
                {
                    "page": 1,
                    "total_pages": 1,
                    "city": "oslo",
                    "items": [{"temperature_noon_c": 8.0}],
                },
            ]
        )
        auth = AuthInfo(token="token-1", request_id="request-1", data_url="https://example.test/data")

        page = WeatherService(test_config(max_retries=2), http_client).fetch_page(auth, 1)

        self.assertEqual(page["city"], "oslo")
        self.assertEqual(http_client.sleep_attempts, [1])
        self.assertEqual(len(http_client.calls), 2)

    def test_fetch_average_temperature_rejects_empty_dataset(self):
        http_client = FakeHttpClient(
            [
                {
                    "page": 1,
                    "total_pages": 1,
                    "city": "empty_city",
                    "items": [],
                }
            ]
        )
        auth = AuthInfo(token="token-1", request_id="request-1", data_url="https://example.test/data")

        with self.assertRaisesRegex(ApiError, "no temperature records"):
            WeatherService(test_config(), http_client).fetch_average_temperature(auth)

    def test_fetch_average_temperature_rejects_missing_temperature(self):
        http_client = FakeHttpClient(
            [
                {
                    "page": 1,
                    "total_pages": 1,
                    "city": "bad_city",
                    "items": [{"date": "2024-01-01"}],
                }
            ]
        )
        auth = AuthInfo(token="token-1", request_id="request-1", data_url="https://example.test/data")

        with self.assertRaisesRegex(ApiError, "missing a numeric temperature"):
            WeatherService(test_config(), http_client).fetch_average_temperature(auth)

    def test_fetch_page_rejects_expired_token_before_request(self):
        http_client = FakeHttpClient([])
        auth = AuthInfo(
            token="token-1",
            request_id="request-1",
            data_url="https://example.test/data",
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )

        with self.assertRaisesRegex(ApiError, "expired or about to expire"):
            WeatherService(test_config(), http_client).fetch_page(auth, 1)

        self.assertEqual(http_client.calls, [])

    def test_fetch_average_temperature_rejects_city_mismatch(self):
        http_client = FakeHttpClient(
            [
                {
                    "page": 1,
                    "total_pages": 2,
                    "city": "paris",
                    "items": [{"temperature_noon_c": 10.0}],
                },
                {
                    "page": 2,
                    "total_pages": 2,
                    "city": "rome",
                    "items": [{"temperature_noon_c": 20.0}],
                },
            ]
        )
        auth = AuthInfo(token="token-1", request_id="request-1", data_url="https://example.test/data")

        with self.assertRaisesRegex(ApiError, "city mismatch"):
            WeatherService(test_config(), http_client).fetch_average_temperature(auth)

    def test_fetch_average_temperature_rejects_missing_total_pages(self):
        http_client = FakeHttpClient(
            [
                {
                    "page": 1,
                    "city": "paris",
                    "items": [{"temperature_noon_c": 10.0}],
                }
            ]
        )
        auth = AuthInfo(token="token-1", request_id="request-1", data_url="https://example.test/data")

        with self.assertRaisesRegex(ApiError, "total_pages"):
            WeatherService(test_config(), http_client).fetch_average_temperature(auth)

    def test_fetch_average_temperature_rejects_invalid_items(self):
        http_client = FakeHttpClient(
            [
                {
                    "page": 1,
                    "total_pages": 1,
                    "city": "paris",
                    "items": "not-a-list",
                }
            ]
        )
        auth = AuthInfo(token="token-1", request_id="request-1", data_url="https://example.test/data")

        with self.assertRaisesRegex(ApiError, "items"):
            WeatherService(test_config(), http_client).fetch_average_temperature(auth)

    def test_fetch_page_raises_after_retry_limit(self):
        http_client = FakeHttpClient(
            [
                {"error": "temporary data issue"},
                {"error": "still broken"},
            ]
        )
        auth = AuthInfo(token="token-1", request_id="request-1", data_url="https://example.test/data")

        with self.assertRaisesRegex(ApiError, "still broken"):
            WeatherService(test_config(max_retries=2), http_client).fetch_page(auth, 1)

        self.assertEqual(http_client.sleep_attempts, [1])
        self.assertEqual(len(http_client.calls), 2)


class HomeworkClientTests(unittest.TestCase):
    def test_run_reauthenticates_when_token_expires(self):
        auth_service = FakeAuthService(
            [
                AuthInfo(token="expired-token", request_id="request-1", data_url="https://example.test/data"),
                AuthInfo(token="fresh-token", request_id="request-2", data_url="https://example.test/data"),
            ]
        )
        weather_service = FakeWeatherService(
            [
                ApiError("expired", status=401),
                ("venice", 20.175),
            ]
        )
        client = HomeworkClient(test_config(max_auth_sessions=2), auth_service, weather_service)

        output = io.StringIO()
        with redirect_stdout(output):
            client.run()

        self.assertEqual(auth_service.call_count, 2)
        self.assertEqual(weather_service.call_count, 2)
        self.assertIn("City: venice", output.getvalue())
        self.assertIn("Average temperature: 20.175", output.getvalue())


class ValidationTests(unittest.TestCase):
    def test_response_validator_rejects_missing_string(self):
        with self.assertRaisesRegex(ApiError, "required string"):
            ResponseValidator.required_string({}, "token")

    def test_config_loader_rejects_invalid_transient_status(self):
        temp_dir, config_path = write_temp_config(valid_config_dict(transient_statuses=[500, 999]))
        with temp_dir:
            with self.assertRaisesRegex(ApiError, "valid HTTP status codes"):
                ConfigLoader.load(config_path)

    def test_config_loader_rejects_missing_auth_url(self):
        temp_dir, config_path = write_temp_config(valid_config_dict(urls={}))
        with temp_dir:
            with self.assertRaisesRegex(ApiError, "urls.auth"):
                ConfigLoader.load(config_path)

    def test_config_loader_rejects_missing_max_retries(self):
        config = valid_config_dict()
        del config["max_retries"]

        temp_dir, config_path = write_temp_config(config)
        with temp_dir:
            with self.assertRaisesRegex(ApiError, "max_retries"):
                ConfigLoader.load(config_path)

    def test_config_loader_rejects_missing_max_parallel_requests(self):
        config = valid_config_dict()
        del config["max_parallel_requests"]

        temp_dir, config_path = write_temp_config(config)
        with temp_dir:
            with self.assertRaisesRegex(ApiError, "max_parallel_requests"):
                ConfigLoader.load(config_path)


class HttpClientTests(unittest.TestCase):
    def test_request_json_retries_transient_api_error(self):
        http_client = FlakyHttpClient(
            test_config(max_retries=2),
            [
                ApiError("HTTP 500", status=500, retryable=True),
                {"ok": True},
            ],
        )

        payload = http_client.request_json("POST", "https://example.test/data")

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(http_client.sleep_attempts, [1])


if __name__ == "__main__":
    unittest.main()
