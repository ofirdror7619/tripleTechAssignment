from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AppConfig:
    auth_url: str
    max_retries: int
    max_auth_sessions: int
    max_parallel_requests: int
    timeout_seconds: float
    token_expiry_buffer_seconds: float
    transient_statuses: frozenset[int]


@dataclass(frozen=True)
class AuthInfo:
    token: str
    request_id: str
    data_url: str
    expires_at: datetime | None = None
    dataset: str | None = None
