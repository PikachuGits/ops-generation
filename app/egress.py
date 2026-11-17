from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator

_ALLOWED_EGRESS = frozenset({"gateway", "review_queue"})
_active_client: ContextVar[str | None] = ContextVar("ops_generation_egress_client", default=None)


def active_egress_client() -> str | None:
    return _active_client.get()


@contextmanager
def authorized_egress(client_name: str) -> Iterator[None]:
    if client_name not in _ALLOWED_EGRESS:
        raise RuntimeError("未授权的出站客户端")
    token = _active_client.set(client_name)
    try:
        yield
    finally:
        _active_client.reset(token)
