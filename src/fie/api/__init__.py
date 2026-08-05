"""FastAPI service surface (Phase 4).

`create_app` is the single entry point. Its dependencies are injected the way
`build_graph`'s are, so the whole surface — including the abuse controls that
guard real money — runs against a fake engine in `make test`, with no
network, no keys and no corpus.

Layout: `app` (routes and wiring), `jobs` (the bounded asyncio job registry),
`store` (SQLite persistence), `governor` (spend cap, per-IP quota, answer
cache), `security` (API key, Turnstile), `progress` (checkpoint reads),
`models` (the HTTP contract), `logs` (structlog wiring).
"""

from fie.api.app import create_app

__all__ = ["create_app"]
