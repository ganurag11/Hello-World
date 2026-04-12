"""
Canva Connect API REST wrapper with OAuth 2.0 PKCE support.

Each public method maps 1-to-1 with a Canva API endpoint.
All error handling (retries, token refresh, rate limits) lives in _request().
"""

import base64
import hashlib
import http.server
import json
import os
import secrets
import time
import urllib.parse
import webbrowser
from threading import Thread
from typing import Any

import requests

import config


# ── Exceptions ────────────────────────────────────────────────────────────────

class CanvaAPIError(Exception):
    def __init__(self, status_code: int, message: str, request_id: str | None = None):
        super().__init__(f"Canva API {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.request_id = request_id


class CanvaAuthError(CanvaAPIError):
    pass


class CanvaRateLimitError(CanvaAPIError):
    pass


class CanvaExportTimeoutError(Exception):
    pass


class CanvaConnectionError(Exception):
    pass


# ── OAuth helpers (standalone, no client instance required) ───────────────────

def generate_pkce_pair() -> tuple[str, str]:
    """Returns (code_verifier, code_challenge)."""
    verifier = secrets.token_urlsafe(96)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def build_authorization_url(
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    state: str,
    scopes: list[str],
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return config.CANVA_OAUTH_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)


def _exchange_code_for_token(
    code: str,
    code_verifier: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict:
    resp = requests.post(
        config.CANVA_OAUTH_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def run_oauth_flow() -> dict:
    """
    Interactive OAuth 2.0 PKCE flow.
    Opens the browser, spins up a localhost callback server,
    exchanges the code for tokens, saves them to TOKEN_CACHE_PATH,
    and returns the token dict.
    """
    verifier, challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(16)
    auth_url = build_authorization_url(
        config.CANVA_CLIENT_ID,
        config.CANVA_REDIRECT_URI,
        challenge,
        state,
        config.CANVA_SCOPES,
    )

    # Capture the redirect in a simple HTTP server
    received: dict = {}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            received["code"]  = params.get("code", [None])[0]
            received["state"] = params.get("state", [None])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h1>Authorization complete. You can close this tab.</h1>")

        def log_message(self, *_):
            pass

    port = int(urllib.parse.urlparse(config.CANVA_REDIRECT_URI).port or 8080)
    server = http.server.HTTPServer(("localhost", port), _Handler)

    print(f"\nOpening Canva authorization in your browser...")
    print(f"If it doesn't open automatically, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    # Handle exactly one request then shut down
    server.handle_request()
    server.server_close()

    if received.get("state") != state:
        raise CanvaAuthError(0, "OAuth state mismatch — possible CSRF attack")

    code = received.get("code")
    if not code:
        raise CanvaAuthError(0, "No authorization code received")

    token_data = _exchange_code_for_token(
        code,
        verifier,
        config.CANVA_CLIENT_ID,
        config.CANVA_CLIENT_SECRET,
        config.CANVA_REDIRECT_URI,
    )

    # Persist tokens
    token_data["obtained_at"] = int(time.time())
    os.makedirs(os.path.dirname(config.TOKEN_CACHE_PATH) or ".", exist_ok=True)
    with open(config.TOKEN_CACHE_PATH, "w") as f:
        json.dump(token_data, f, indent=2)

    print(f"Tokens saved to {config.TOKEN_CACHE_PATH}")
    return token_data


def load_cached_tokens() -> dict | None:
    """Load tokens from the cache file, returning None if absent."""
    if os.path.exists(config.TOKEN_CACHE_PATH):
        with open(config.TOKEN_CACHE_PATH) as f:
            return json.load(f)
    return None


# ── Canva REST client ─────────────────────────────────────────────────────────

class CanvaClient:
    def __init__(
        self,
        access_token: str,
        client_id: str,
        client_secret: str,
        refresh_token: str | None = None,
    ):
        self._access_token  = access_token
        self._client_id     = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._session = requests.Session()

    # ── internal ──────────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    def _refresh_access_token(self) -> None:
        if not self._refresh_token:
            raise CanvaAuthError(401, "No refresh token available — run --oauth again")
        resp = requests.post(
            config.CANVA_OAUTH_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            timeout=30,
        )
        if not resp.ok:
            raise CanvaAuthError(resp.status_code, "Token refresh failed")
        data = resp.json()
        self._access_token  = data["access_token"]
        self._refresh_token = data.get("refresh_token", self._refresh_token)
        # Update cache
        if os.path.exists(config.TOKEN_CACHE_PATH):
            with open(config.TOKEN_CACHE_PATH) as f:
                cached = json.load(f)
            cached.update({"access_token": self._access_token,
                           "refresh_token": self._refresh_token,
                           "obtained_at": int(time.time())})
            with open(config.TOKEN_CACHE_PATH, "w") as f:
                json.dump(cached, f, indent=2)

    def _request(
        self,
        method: str,
        path: str,
        retry_on_401: bool = True,
        **kwargs: Any,
    ) -> dict:
        url = config.CANVA_API_BASE + path
        kwargs.setdefault("headers", self._headers())
        kwargs.setdefault("timeout", 30)

        for attempt in range(config.CANVA_MAX_RETRIES):
            try:
                resp = self._session.request(method, url, **kwargs)
            except requests.ConnectionError as exc:
                raise CanvaConnectionError(f"Network error: {exc}") from exc

            if resp.status_code == 200 or resp.status_code == 201:
                return resp.json() if resp.content else {}

            if resp.status_code == 401 and retry_on_401:
                self._refresh_access_token()
                kwargs["headers"] = self._headers()
                return self._request(method, path, retry_on_401=False, **kwargs)

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 2 ** attempt))
                if attempt < config.CANVA_MAX_RETRIES - 1:
                    time.sleep(retry_after)
                    continue
                raise CanvaRateLimitError(429, "Rate limit exceeded after retries")

            # Extract error message from Canva's error body when possible
            try:
                err = resp.json()
                message = err.get("message") or err.get("error", resp.text)
            except Exception:
                message = resp.text

            request_id = resp.headers.get("X-Request-Id")
            raise CanvaAPIError(resp.status_code, message, request_id)

        raise CanvaAPIError(0, "Max retries exhausted")

    # ── Design operations ─────────────────────────────────────────────────────

    def create_design(
        self,
        width: int,
        height: int,
        title: str | None = None,
    ) -> dict:
        """
        POST /v1/designs — create a blank custom-size design.

        Returns:
            {
                "design": {
                    "id": "DAGxyz...",
                    "urls": {"edit_url": "https://...", "view_url": "https://..."},
                    "title": "...",
                }
            }
        """
        body: dict = {
            "design_type": {
                "type": "custom",
                "width": width,
                "height": height,
            }
        }
        if title:
            body["title"] = title
        return self._request("POST", "/designs", json=body)

    def get_design(self, design_id: str) -> dict:
        """GET /v1/designs/{design_id}"""
        return self._request("GET", f"/designs/{design_id}")

    def list_designs(
        self,
        query: str | None = None,
        continuation: str | None = None,
    ) -> dict:
        """GET /v1/designs — list designs in the user's account."""
        params: dict = {}
        if query:
            params["query"] = query
        if continuation:
            params["continuation"] = continuation
        return self._request("GET", "/designs", params=params)

    # ── Export operations ─────────────────────────────────────────────────────

    def create_export_job(
        self,
        design_id: str,
        format: str = "png",
        export_quality: str = "regular",
    ) -> dict:
        """
        POST /v1/exports — start an async export job.

        Returns the export job object with an `id` field.
        """
        body = {
            "design_id": design_id,
            "format": {
                "type": format,
                "export_quality": export_quality,
            },
        }
        return self._request("POST", "/exports", json=body)

    def get_export_job(self, export_job_id: str) -> dict:
        """GET /v1/exports/{export_job_id}"""
        return self._request("GET", f"/exports/{export_job_id}")

    def poll_export_until_done(
        self,
        export_job_id: str,
        max_wait_seconds: int = config.CANVA_EXPORT_MAX_WAIT,
    ) -> list[str]:
        """
        Polls get_export_job() every CANVA_EXPORT_POLL_INTERVAL seconds until
        status == "success" or "failed", or max_wait_seconds is reached.

        Returns a list of download URLs (one per design page).
        Raises CanvaExportTimeoutError on timeout, CanvaAPIError on failure.
        """
        deadline = time.time() + max_wait_seconds
        while time.time() < deadline:
            job = self.get_export_job(export_job_id)
            status = job.get("job", {}).get("status", "")
            if status == "success":
                urls = [
                    page["url"]
                    for page in job.get("job", {}).get("urls", [])
                ]
                return urls
            if status == "failed":
                error = job.get("job", {}).get("error", {})
                raise CanvaAPIError(0, f"Export failed: {error}")
            time.sleep(config.CANVA_EXPORT_POLL_INTERVAL)

        raise CanvaExportTimeoutError(
            f"Export job {export_job_id} did not complete within {max_wait_seconds}s"
        )

    # ── Asset operations ──────────────────────────────────────────────────────

    def upload_asset(self, file_path: str, asset_name: str) -> dict:
        """
        POST /v1/asset-uploads — upload a local file as a Canva asset.
        Returns the asset-upload job dict (poll get_asset_upload_job for completion).
        """
        import mimetypes
        mime = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        metadata = json.dumps({"name": asset_name})
        with open(file_path, "rb") as f:
            resp = self._session.post(
                config.CANVA_API_BASE + "/asset-uploads",
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": mime,
                    "Asset-Upload-Metadata": base64.b64encode(metadata.encode()).decode(),
                },
                data=f,
                timeout=120,
            )
        if not resp.ok:
            raise CanvaAPIError(resp.status_code, resp.text)
        return resp.json()

    def get_asset_upload_job(self, job_id: str) -> dict:
        """GET /v1/asset-uploads/{job_id}"""
        return self._request("GET", f"/asset-uploads/{job_id}")


def client_from_cache() -> CanvaClient:
    """
    Build a CanvaClient from the token cache file.
    Falls back to env vars if no cache file exists.
    """
    tokens = load_cached_tokens()
    if tokens:
        return CanvaClient(
            access_token=tokens["access_token"],
            client_id=config.CANVA_CLIENT_ID,
            client_secret=config.CANVA_CLIENT_SECRET,
            refresh_token=tokens.get("refresh_token"),
        )
    return CanvaClient(
        access_token=config.CANVA_ACCESS_TOKEN,
        client_id=config.CANVA_CLIENT_ID,
        client_secret=config.CANVA_CLIENT_SECRET,
        refresh_token=config.CANVA_REFRESH_TOKEN or None,
    )
