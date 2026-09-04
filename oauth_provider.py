"""Minimal first-party OAuth 2.1 authorization server for one MCP deployment.

This is a pilot: only the service with AUTH_MODE=oauth (biodiversity-mcp-tier3)
uses this. The other two deployments keep the original shared-secret
x-api-key/api_key auth in server.py, untouched.

Implements the mcp.server.auth.provider.OAuthAuthorizationServerProvider
protocol, backed by Postgres tables (see schema.sql). Dynamic Client
Registration (RFC 7591) and PKCE validation are handled by the MCP SDK itself
(mcp.server.auth.handlers); this class only needs to persist and look up
clients/codes/tokens, plus drive a first-party username/password login page
(server.py wires up the actual /login GET+POST routes; this module exposes
verify_user() and complete_login() for them to call).
"""

import hashlib
import json
import secrets
import time
from contextlib import AbstractContextManager
from typing import Callable

from pydantic import AnyUrl

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

ACCESS_TOKEN_TTL_SECONDS = 3600
AUTH_CODE_TTL_SECONDS = 300
LOGIN_SESSION_TTL_SECONDS = 600


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, hash_hex = stored.split("$")
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return secrets.compare_digest(derived, expected)


class WikiOAuthProvider(OAuthAuthorizationServerProvider):
    def __init__(self, get_db_cursor: Callable[[], AbstractContextManager]):
        self._get_db_cursor = get_db_cursor

    # --- user login (not part of the OAuthAuthorizationServerProvider protocol;
    # called directly by the /login routes in server.py) ------------------------

    def verify_user(self, username: str, password: str) -> bool:
        username = (username or "").strip()
        if not username or not password:
            return False
        with self._get_db_cursor() as cursor:
            cursor.execute("SELECT password_hash FROM oauth_users WHERE username = %s", (username,))
            row = cursor.fetchone()
        return bool(row) and verify_password(password, row["password_hash"])

    async def complete_login(self, login_id: str, username: str) -> str:
        with self._get_db_cursor() as cursor:
            cursor.execute("SELECT * FROM oauth_pending_authorizations WHERE login_id = %s", (login_id,))
            pending = cursor.fetchone()
            if not pending or pending["expires_at"] < time.time():
                cursor.execute("DELETE FROM oauth_pending_authorizations WHERE login_id = %s", (login_id,))
                raise ValueError("Login session expired or invalid. Please reconnect the connector.")

            code = secrets.token_urlsafe(32)
            cursor.execute(
                """INSERT INTO oauth_authorization_codes
                   (code, client_id, redirect_uri, redirect_uri_provided_explicitly, scopes,
                    code_challenge, resource, subject, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    code,
                    pending["client_id"],
                    pending["redirect_uri"],
                    pending["redirect_uri_provided_explicitly"],
                    pending["scopes"],
                    pending["code_challenge"],
                    pending["resource"],
                    username,
                    time.time() + AUTH_CODE_TTL_SECONDS,
                ),
            )
            cursor.execute("DELETE FROM oauth_pending_authorizations WHERE login_id = %s", (login_id,))
        return construct_redirect_uri(pending["redirect_uri"], code=code, state=pending["state"])

    # --- OAuthAuthorizationServerProvider protocol ------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with self._get_db_cursor() as cursor:
            cursor.execute("SELECT * FROM oauth_clients WHERE client_id = %s", (client_id,))
            row = cursor.fetchone()
        if not row:
            return None
        return OAuthClientInformationFull(
            client_id=row["client_id"],
            client_secret=row["client_secret"],
            redirect_uris=[AnyUrl(u) for u in json.loads(row["redirect_uris"])],
            grant_types=json.loads(row["grant_types"]),
            token_endpoint_auth_method=row["token_endpoint_auth_method"],
            client_name=row["client_name"],
            scope=row["scope"],
        )

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        with self._get_db_cursor() as cursor:
            cursor.execute(
                """INSERT INTO oauth_clients
                   (client_id, client_secret, redirect_uris, grant_types, token_endpoint_auth_method, client_name, scope)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    client_info.client_id,
                    client_info.client_secret,
                    json.dumps([str(u) for u in client_info.redirect_uris]),
                    json.dumps(client_info.grant_types),
                    client_info.token_endpoint_auth_method,
                    client_info.client_name,
                    client_info.scope,
                ),
            )

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        login_id = secrets.token_urlsafe(24)
        with self._get_db_cursor() as cursor:
            cursor.execute(
                """INSERT INTO oauth_pending_authorizations
                   (login_id, client_id, redirect_uri, redirect_uri_provided_explicitly, scopes, state,
                    code_challenge, resource, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    login_id,
                    client.client_id,
                    str(params.redirect_uri),
                    params.redirect_uri_provided_explicitly,
                    json.dumps(params.scopes) if params.scopes else None,
                    params.state,
                    params.code_challenge,
                    params.resource,
                    time.time() + LOGIN_SESSION_TTL_SECONDS,
                ),
            )
        return f"/login?login_id={login_id}"

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str) -> AuthorizationCode | None:
        with self._get_db_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM oauth_authorization_codes WHERE code = %s AND client_id = %s",
                (authorization_code, client.client_id),
            )
            row = cursor.fetchone()
        if not row:
            return None
        return AuthorizationCode(
            code=row["code"],
            scopes=json.loads(row["scopes"]) if row["scopes"] else [],
            expires_at=row["expires_at"],
            client_id=row["client_id"],
            code_challenge=row["code_challenge"],
            redirect_uri=AnyUrl(row["redirect_uri"]),
            redirect_uri_provided_explicitly=row["redirect_uri_provided_explicitly"],
            resource=row["resource"],
            subject=row["subject"],
        )

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode) -> OAuthToken:
        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)
        now = int(time.time())
        with self._get_db_cursor() as cursor:
            cursor.execute("DELETE FROM oauth_authorization_codes WHERE code = %s", (authorization_code.code,))
            cursor.execute(
                """INSERT INTO oauth_access_tokens (token, client_id, scopes, resource, subject, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    access_token,
                    client.client_id,
                    json.dumps(authorization_code.scopes),
                    authorization_code.resource,
                    authorization_code.subject,
                    now + ACCESS_TOKEN_TTL_SECONDS,
                ),
            )
            cursor.execute(
                """INSERT INTO oauth_refresh_tokens (token, client_id, scopes, subject, expires_at)
                   VALUES (%s, %s, %s, %s, %s)""",
                (refresh_token, client.client_id, json.dumps(authorization_code.scopes), authorization_code.subject, None),
            )
        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            refresh_token=refresh_token,
            scope=" ".join(authorization_code.scopes) if authorization_code.scopes else None,
        )

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        with self._get_db_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM oauth_refresh_tokens WHERE token = %s AND client_id = %s",
                (refresh_token, client.client_id),
            )
            row = cursor.fetchone()
        if not row:
            return None
        return RefreshToken(
            token=row["token"],
            client_id=row["client_id"],
            scopes=json.loads(row["scopes"]) if row["scopes"] else [],
            expires_at=row["expires_at"],
            subject=row["subject"],
        )

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        access_token = secrets.token_urlsafe(32)
        new_refresh_token = secrets.token_urlsafe(32)
        now = int(time.time())
        use_scopes = scopes or refresh_token.scopes
        with self._get_db_cursor() as cursor:
            cursor.execute("DELETE FROM oauth_refresh_tokens WHERE token = %s", (refresh_token.token,))
            cursor.execute(
                """INSERT INTO oauth_access_tokens (token, client_id, scopes, resource, subject, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (access_token, client.client_id, json.dumps(use_scopes), None, refresh_token.subject, now + ACCESS_TOKEN_TTL_SECONDS),
            )
            cursor.execute(
                """INSERT INTO oauth_refresh_tokens (token, client_id, scopes, subject, expires_at)
                   VALUES (%s, %s, %s, %s, %s)""",
                (new_refresh_token, client.client_id, json.dumps(use_scopes), refresh_token.subject, None),
            )
        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            refresh_token=new_refresh_token,
            scope=" ".join(use_scopes) if use_scopes else None,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        with self._get_db_cursor() as cursor:
            cursor.execute("SELECT * FROM oauth_access_tokens WHERE token = %s", (token,))
            row = cursor.fetchone()
        if not row:
            return None
        if row["expires_at"] and row["expires_at"] < time.time():
            return None
        return AccessToken(
            token=row["token"],
            client_id=row["client_id"],
            scopes=json.loads(row["scopes"]) if row["scopes"] else [],
            expires_at=row["expires_at"],
            resource=row["resource"],
            subject=row["subject"],
        )

    async def revoke_token(self, token) -> None:
        with self._get_db_cursor() as cursor:
            cursor.execute("DELETE FROM oauth_access_tokens WHERE token = %s", (token.token,))
            cursor.execute("DELETE FROM oauth_refresh_tokens WHERE token = %s", (token.token,))
