import unittest
import urllib.error
from unittest.mock import patch

from cookall_data.http import FetchError, HttpClient


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.headers = {}
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        return None
    def read(self):
        return self.payload


class HttpTests(unittest.TestCase):
    def test_access_failure_stops_after_bounded_retries(self):
        client = HttpClient(retries=2, min_interval=0)
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")) as call:
            with self.assertRaises(FetchError):
                client.get_json("https://example.invalid/api")
        self.assertEqual(call.call_count, 2)

    def test_http_200_api_error_is_not_treated_as_empty_data(self):
        client = HttpClient(retries=1, min_interval=0)
        with patch("urllib.request.urlopen", return_value=FakeResponse(b'{"error":{"code":"params","info":"bad parameter"}}')):
            with self.assertRaises(FetchError):
                client.get_json("https://example.invalid/api")

    def test_429_uses_retry_after_and_retries(self):
        client = HttpClient(retries=2, min_interval=0)
        limited = urllib.error.HTTPError(
            "https://example.invalid/api", 429, "Too Many Requests", {"Retry-After": "0"}, None
        )
        with patch("urllib.request.urlopen", side_effect=[limited, FakeResponse(b'{"ok":true}')]) as call:
            result = client.get_json("https://example.invalid/api")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(call.call_count, 2)


if __name__ == "__main__":
    unittest.main()
