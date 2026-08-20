"""
Signing in with Google.

The only module in Lumen that knows Google exists. Everything downstream sees
an `ExternalIdentity`, so a second provider is a sibling of this file rather
than a change to anything else.

The exchange is the authorization-code flow: the browser is sent to Google,
comes back with a code, and the *server* exchanges that code using a secret
the browser has never seen. A flow that let the browser complete the exchange
would mean shipping the secret to it.

**Step five is the security boundary and is not shortened.** A Google ID token
is proof of nothing until five things are checked: that Google signed it, that
Google issued it, that it was issued for us and not for somebody else's
application, that it has not expired, and that Google has confirmed the person
controls the address. The last one matters more than it looks: an unconfirmed
address is a claim about a mailbox somebody may not own, and accepting it
would let a stranger sign in as whoever signs up with that address later.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

import httpx
import jwt

from lumen.auth.contracts import ExternalIdentity, NotAuthenticated, SignInStart
from lumen.config import AuthConfig
from lumen.operational.enums import AuthProvider

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"

# The two forms Google states itself in. Both are correct and which one
# arrives depends on the flow, so both are accepted and nothing else is.
ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})

# What we ask for: who they are and how to reach them. Nothing else. Asking
# for more would show a person a longer consent screen for capabilities this
# system has no use for.
SCOPES = "openid email profile"

# How long to wait on Google. Somebody is watching a spinner.
TIMEOUT_SECONDS = 10.0


class GoogleIdentityProvider:
    """
    Google, behind the identity-provider Protocol.

    The HTTP client and the key source are constructor arguments so a test
    drives the whole flow against a local key and a stub endpoint, with no
    network anywhere.
    """

    name = AuthProvider.GOOGLE.value

    def __init__(
        self,
        config: AuthConfig,
        *,
        client: httpx.Client | None = None,
        keys: Any | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._keys = keys or jwt.PyJWKClient(JWKS_URL, cache_keys=True)

    def start(self) -> SignInStart:
        """
        The URL to send somebody to, and the two secrets to keep meanwhile.

        `state` is what proves the person who comes back is the person who
        left: without it, somebody else can finish a sign-in inside this
        browser's session.

        The code verifier is generated here rather than in the browser. This
        deployment has a secret and can keep one, so proof-of-possession is
        defence in depth against a leaked code rather than the main
        protection — but it costs nothing and it is one more thing a stolen
        code alone cannot satisfy.
        """
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)

        from base64 import urlsafe_b64encode
        from hashlib import sha256

        challenge = (
            urlsafe_b64encode(sha256(verifier.encode()).digest()).decode().rstrip("=")
        )
        query = httpx.QueryParams(
            {
                "client_id": self._config.google_client_id,
                "redirect_uri": self._config.google_redirect_uri,
                "response_type": "code",
                "scope": SCOPES,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                # Ask every time rather than reusing a silent session, so
                # signing out here means something.
                "prompt": "select_account",
            }
        )
        return SignInStart(
            authorization_url=f"{AUTHORIZE_URL}?{query}",
            state=state,
            verifier=verifier,
        )

    def verify(self, code: str, verifier: str) -> ExternalIdentity:
        """
        Exchange the code and check what comes back, properly.

        Raises:
            NotAuthenticated: Anything at all went wrong. The reason is short
                and safe to repeat: it says what is wrong with the sign-in and
                never whether an account exists.
        """
        payload = self._exchange(code, verifier)
        id_token = str(payload.get("id_token") or "")
        if not id_token:
            raise NotAuthenticated("the sign-in did not include proof of identity")
        return self._read(id_token)

    def _exchange(self, code: str, verifier: str) -> dict[str, Any]:
        """
        Swap the code for tokens, using the secret the browser never sees.

        A failure here is logged by status only. The body of a failed token
        exchange echoes back parts of the request, and this is the one
        request in the system carrying a client secret.
        """
        secret = self._config.google_client_secret
        if not secret:
            raise NotAuthenticated("this deployment is not configured for sign-in")

        client = self._client or httpx.Client(timeout=TIMEOUT_SECONDS)
        try:
            response = client.post(
                TOKEN_URL,
                data={
                    "code": code,
                    "client_id": self._config.google_client_id,
                    "client_secret": secret,
                    "redirect_uri": self._config.google_redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": verifier,
                },
            )
        except httpx.HTTPError as exc:
            logger.warning("the sign-in provider could not be reached")
            raise NotAuthenticated("the sign-in provider could not be reached") from exc
        finally:
            if self._client is None:
                client.close()

        if response.status_code >= 400:
            logger.warning(
                "the sign-in provider refused the exchange",
                extra={"status": response.status_code},
            )
            raise NotAuthenticated("this sign-in could not be completed")

        return response.json()

    def _read(self, id_token: str) -> ExternalIdentity:
        """
        Check Google's assertion five ways, then read it.

        The order matters only in that nothing is read before everything is
        checked. A claim out of an unverified token is not a fact.
        """
        try:
            signing = self._keys.get_signing_key_from_jwt(id_token)
        except Exception as exc:  # noqa: BLE001 — an unfetchable key is a refusal
            logger.warning("the sign-in provider's keys could not be read")
            raise NotAuthenticated("this sign-in could not be verified") from exc

        try:
            claims = jwt.decode(
                id_token,
                signing.key,
                algorithms=["RS256"],
                audience=self._config.google_client_id,
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise NotAuthenticated("this sign-in took too long; try again") from exc
        except jwt.InvalidAudienceError as exc:
            raise NotAuthenticated("this sign-in was meant for another application") from exc
        except jwt.InvalidTokenError as exc:
            raise NotAuthenticated("this sign-in could not be verified") from exc

        if str(claims.get("iss") or "") not in ISSUERS:
            raise NotAuthenticated("this sign-in came from somewhere unexpected")

        if not claims.get("email_verified"):
            # A claim about an address the person may not control. Accepting
            # it would let a stranger sign in as whoever registers that
            # address here later.
            raise NotAuthenticated(
                "this account's email address has not been confirmed with the provider"
            )

        email = str(claims.get("email") or "").strip()
        if not email:
            raise NotAuthenticated("this sign-in carried no email address")

        return ExternalIdentity(
            provider=self.name,
            subject=str(claims["sub"]),
            email=email,
            email_verified=True,
            display_name=str(claims.get("name") or ""),
            avatar_url=str(claims.get("picture") or ""),
        )


__all__ = ["GoogleIdentityProvider", "AUTHORIZE_URL", "TOKEN_URL", "JWKS_URL", "ISSUERS"]
