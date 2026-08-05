"""Who is allowed to submit: the static API key and the Turnstile check.

These two are not redundant, and which one is load-bearing depends on where
the request came from.

The `X-API-Key` header authenticates the **tailnet** consumer — the CLI, or
the operator's Mac — and nothing else. On the public path it is an *identifier*, not
a control: the Phase 5 frontend is a static SPA, so its key ships inside the
bundle where devtools can read it. Any design that treats the key as a secret
on that path is lying to itself.

Turnstile is what actually holds the public path. It cannot be copied out of
a bundle because the token is minted per visitor per challenge, which is why
`test_turnstile_refuses_a_submission_carrying_a_valid_api_key` exists: it
encodes the sentence above as an assertion rather than a comment.
"""

import hmac
import logging

import httpx

from fie.config import Settings

logger = logging.getLogger(__name__)

TURNSTILE_TIMEOUT_SECONDS = 5.0


def api_key_valid(presented: str | None, settings: Settings) -> bool:
    """Constant-time compare against the configured key.

    Fails closed when no key is configured: an API that authenticates
    everyone because someone forgot an env var is worse than one that is
    down, because it looks like it is working. `create_app` refuses to start
    in that state, so this branch is the second line, not the first.
    """
    expected = settings.api_key.get_secret_value()
    if not expected or not presented:
        return False
    return hmac.compare_digest(presented, expected)


async def verify_turnstile(
    token: str | None,
    *,
    client: httpx.AsyncClient,
    settings: Settings,
    remote_ip: str | None = None,
) -> bool:
    """Verify one Turnstile token with Cloudflare's siteverify endpoint.

    Returns True unconditionally when the check is not required — that is the
    tailnet and local-dev configuration. `Settings.turnstile_required` with no
    secret is rejected at startup, so "not required" is always a deliberate
    choice and never a missing env var.

    A siteverify outage refuses the submission. The alternative is to let the
    one control holding the public path fail open, and a demo that is briefly
    unavailable is cheaper than one that is briefly free to script.
    """
    if not settings.turnstile_required:
        return True
    if not token:
        return False
    payload = {"secret": settings.turnstile_secret_key.get_secret_value(), "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip
    try:
        response = await client.post(
            settings.turnstile_verify_url, data=payload, timeout=TURNSTILE_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        outcome = response.json()
    except Exception:
        logger.warning("Turnstile siteverify failed; refusing the submission", exc_info=True)
        return False
    success = bool(outcome.get("success"))
    if not success:
        logger.info("Turnstile rejected a token: %s", outcome.get("error-codes"))
    return success
