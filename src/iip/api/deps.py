"""Shared FastAPI dependencies: database sessions and webhook authentication."""

import hmac
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from iip.config import Settings


def get_settings_from_app(request: Request) -> Settings:
    return request.app.state.settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request. The handler decides when to commit; anything
    uncommitted is rolled back when the request ends, including on errors."""
    factory: async_sessionmaker[AsyncSession] | None = getattr(
        request.app.state, "sessionmaker", None
    )
    if factory is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database not configured")
    async with factory() as session:
        yield session


def require_webhook_token(
    settings: Annotated[Settings, Depends(get_settings_from_app)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Check ``Authorization: Bearer <token>`` against the configured tokens.

    ``hmac.compare_digest`` compares in constant time. A plain ``==`` stops at the
    first differing character, so response timing could reveal how much of a guess
    was right, letting an attacker recover the token character by character.
    """
    tokens = settings.webhook_tokens
    if not tokens:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "webhook disabled: IIP_WEBHOOK_TOKEN is not set"
        )
    scheme, _, presented = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not presented:
        raise _unauthorized()
    presented_bytes = presented.strip().encode()
    # Check every configured token (no early exit), so timing doesn't reveal
    # which one matched.
    matched = False
    for token in tokens:
        matched |= hmac.compare_digest(presented_bytes, token.encode())
    if not matched:
        raise _unauthorized()


def _unauthorized() -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "missing or invalid bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )


SessionDep = Annotated[AsyncSession, Depends(get_session)]
