from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone


class FetchError(RuntimeError):
    pass


class HttpClient:
    def __init__(
        self,
        user_agent: str = "CookAllPhase0/0.1 (educational recipe data research)",
        timeout: float = 20.0,
        retries: int = 6,
        min_interval: float = 1.0,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = retries
        self.min_interval = min_interval
        self._last_request = 0.0

    @staticmethod
    def _retry_after(headers: Any, fallback: float) -> float:
        value = headers.get("Retry-After") if headers else None
        if not value:
            return fallback
        try:
            return max(float(value), 0.0)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max((retry_at - datetime.now(timezone.utc)).total_seconds(), 0.0)
            except (TypeError, ValueError, OverflowError):
                return fallback

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        error: Exception | None = None
        for attempt in range(self.retries):
            delay = self.min_interval - (time.monotonic() - self._last_request)
            if delay > 0:
                time.sleep(delay)
            request = urllib.request.Request(
                url,
                headers={"User-Agent": self.user_agent, "Accept": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    self._last_request = time.monotonic()
                    payload = json.loads(response.read().decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise FetchError(f"Expected a JSON object from {url}")
                    if payload.get("error", {}).get("code") == "maxlag":
                        error = FetchError(f"MediaWiki maxlag: {payload['error'].get('info', payload['error'])}")
                        if attempt + 1 < self.retries:
                            time.sleep(self._retry_after(getattr(response, "headers", None), max(5.0, 2**attempt)))
                            continue
                    if "error" in payload:
                        api_error = payload["error"]
                        raise FetchError(f"API error from {url}: {api_error.get('code', 'unknown')} - {api_error.get('info', api_error)}")
                    return payload
            except urllib.error.HTTPError as exc:
                error = exc
                if attempt + 1 < self.retries and exc.code in {429, 503}:
                    time.sleep(self._retry_after(exc.headers, max(5.0, 2**attempt)))
                    continue
                if attempt + 1 < self.retries and exc.code >= 500:
                    time.sleep(0.5 * (2**attempt))
                    continue
                break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                error = exc
                if attempt + 1 < self.retries:
                    time.sleep(0.5 * (2**attempt))
        raise FetchError(f"Could not fetch {url} after {self.retries} attempts: {error}")
