import json

import pytest

from marathon_calendar.sources.http_client import HttpClientError, JsonHttpClient


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class QueueOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, request, timeout):
        self.calls += 1
        return self.responses.pop(0)


def test_retries_transient_server_error_only():
    opener = QueueOpener(
        [
            FakeResponse(503, b"temporarily unavailable"),
            FakeResponse(200, json.dumps({"success": True}).encode()),
        ]
    )
    sleeps = []
    client = JsonHttpClient(opener=opener, sleeper=sleeps.append, max_retries=2)
    assert client.post_json("https://example.invalid/api", {"pageNo": 1}) == {"success": True}
    assert opener.calls == 2
    assert sleeps == [0.25]


def test_does_not_retry_client_error_or_invalid_json():
    opener = QueueOpener([FakeResponse(400, b"bad request")])
    client = JsonHttpClient(opener=opener, sleeper=lambda _: pytest.fail("unexpected retry"))
    with pytest.raises(HttpClientError) as error:
        client.post_json("https://example.invalid/api", {})
    assert error.value.retryable is False
    assert opener.calls == 1

    invalid = QueueOpener([FakeResponse(200, b"not-json")])
    with pytest.raises(HttpClientError) as error:
        JsonHttpClient(opener=invalid).post_json("https://example.invalid/api", {})
    assert error.value.error_type == "invalid_json"

