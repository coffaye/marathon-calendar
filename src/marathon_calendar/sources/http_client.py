from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass
class HttpClientError(Exception):
    url: str
    error_type: str
    message: str
    status_code: int | None = None
    retryable: bool = False

    def __str__(self) -> str:
        status = f" HTTP {self.status_code}" if self.status_code else ""
        return f"{self.error_type}{status}: {self.message}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "error_type": self.error_type,
            "message": self.message,
            "status_code": self.status_code,
            "retryable": self.retryable,
        }


class JsonHttpClient:
    """Small dependency-free JSON client with bounded, selective retries."""

    def __init__(
        self,
        *,
        timeout: float = 15.0,
        max_retries: int = 2,
        user_agent: str = "MarathonCalendar/0.2 (low-frequency research client)",
        backoff_base: float = 0.25,
        opener: Callable[..., Any] = urlopen,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.timeout = timeout
        self.max_retries = max(0, min(max_retries, 3))
        self.user_agent = user_agent
        self.backoff_base = backoff_base
        self.opener = opener
        self.sleeper = sleeper

    def post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            url,
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": self.user_agent,
            },
        )
        last_error: HttpClientError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    status = int(response.status)
                    raw = response.read()
                if status < 200 or status >= 300:
                    retryable = status == 429 or 500 <= status <= 599
                    raise HttpClientError(
                        url=url,
                        error_type="http_error",
                        message=f"HTTP status {status}",
                        status_code=status,
                        retryable=retryable,
                    )
                try:
                    decoded = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise HttpClientError(
                        url=url,
                        error_type="invalid_json",
                        message="response was not valid UTF-8 JSON",
                    ) from exc
                if not isinstance(decoded, dict):
                    raise HttpClientError(
                        url=url,
                        error_type="invalid_json_schema",
                        message="JSON response envelope must be an object",
                    )
                return decoded
            except HTTPError as exc:
                status = int(exc.code)
                retryable = status == 429 or 500 <= status <= 599
                last_error = HttpClientError(
                    url=url,
                    error_type="http_error",
                    message=f"HTTP status {status}",
                    status_code=status,
                    retryable=retryable,
                )
            except (URLError, TimeoutError, OSError) as exc:
                last_error = HttpClientError(
                    url=url,
                    error_type="network_error",
                    message=str(exc),
                    retryable=True,
                )
            except HttpClientError as exc:
                last_error = exc

            if last_error is None or not last_error.retryable or attempt >= self.max_retries:
                break
            self.sleeper(self.backoff_base * (2**attempt))

        assert last_error is not None
        raise last_error

