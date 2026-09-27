"""Bounded, non-retrying JSON transport shared by external providers."""
import json
import math
import time
from urllib.parse import urlsplit

import httpx

from app.integrations.memory import ProviderError


class ProviderHTTPError(ProviderError):
    def __init__(self, status: int):
        self.status = status
        super().__init__('provider_http_error')


class JSONTransport:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 60,
                 max_response_bytes: int = 262144, max_request_bytes: int = 262144, transport=None):
        parsed = urlsplit(base_url)
        if ((not api_key) or parsed.scheme not in {'http', 'https'} or not parsed.netloc
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ProviderError('provider_not_configured')
        if not math.isfinite(timeout) or timeout <= 0 or min(max_response_bytes, max_request_bytes) < 1:
            raise ValueError('Invalid provider limits')
        self.timeout = timeout
        self.max_response_bytes, self.max_request_bytes = max_response_bytes, max_request_bytes
        self.client = httpx.Client(base_url=base_url.rstrip('/') + '/',
            headers={**({'Authorization': f'Bearer {api_key}'} if api_key else {}), 'Accept': 'application/json'},
            timeout=timeout, transport=transport, follow_redirects=False)

    def request(self, method: str, path: str, *, payload: dict | None = None, deadline: float | None = None):
        deadline = deadline or time.monotonic() + self.timeout
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderError('provider_timeout')
        body = None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        if body is not None and len(body) > self.max_request_bytes:
            raise ProviderError('provider_input_too_large')
        try:
            with self.client.stream(method, path.lstrip('/'), content=body,
                    headers={'Content-Type': 'application/json'}, timeout=min(self.timeout, remaining)) as response:
                if response.status_code >= 300:
                    raise ProviderHTTPError(response.status_code)
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > self.max_response_bytes:
                        raise ProviderError('provider_output_too_large')
                    if time.monotonic() >= deadline:
                        raise ProviderError('provider_timeout')
                return json.loads(content)
        except (httpx.HTTPError, ValueError, UnicodeError):
            raise ProviderError('provider_invalid_response') from None

    def close(self):
        self.client.close()
