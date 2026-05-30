#!/usr/bin/env python3
import sys
from config import ConfigLoader
from errorHandling.error import ApiError
from models import AppConfig
from services.auth_service import AuthService
from services.http_client import HttpClient
from services.weather_service import WeatherService

class HomeworkClient:
    def __init__(
        self,
        config: AppConfig,
        auth_service: AuthService,
        weather_service: WeatherService,
    ):
        self.max_auth_sessions = config.max_auth_sessions
        self.auth_service = auth_service
        self.weather_service = weather_service

    def run(self):
        for session_attempt in range(1, self.max_auth_sessions + 1):
            auth = self.auth_service.authenticate()

            try:
                city, average = self.weather_service.fetch_average_temperature(auth)
            except ApiError as exc:
                if exc.status in {401, 403} and session_attempt < self.max_auth_sessions:
                    continue
                raise

            print(f"City: {city}")
            print(f"Average temperature: {self.format_average(average)}")
            return

        raise ApiError("Could not fetch data before the token expired.", status=401)

    @staticmethod
    def format_average(value: float) -> str:
        return f"{value:.3f}".rstrip("0").rstrip(".")


def create_client() -> HomeworkClient:
    config = ConfigLoader.load()
    http_client = HttpClient(config)
    auth_service = AuthService(config, http_client)
    weather_service = WeatherService(config, http_client)
    return HomeworkClient(config, auth_service, weather_service)


if __name__ == "__main__":
    try:
        create_client().run()
    except ApiError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
