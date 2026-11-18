"""Small HTTP helper. Proxy applies only when the caller passes one."""

from __future__ import annotations

import urllib.error
import urllib.request

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class HttpError(Exception):
    def __init__(self, status: int, url: str, body: str = ""):
        super().__init__(f"HTTP {status} for {url}: {body[:200]}")
        self.status = status
        self.url = url


def get_bytes(
    url: str,
    *,
    timeout: float = 30,
    proxy: str | None = None,
    headers: dict[str, str] | None = None,
) -> bytes:
    request_headers = {"User-Agent": BROWSER_UA}
    if headers:
        request_headers.update(headers)
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(url, headers=request_headers)
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace") if exc.fp else ""
        raise HttpError(exc.code, url, body) from exc
