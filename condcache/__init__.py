"""Conditional requests and cache validation (standard-library only)."""

from .client import CachingClient, NaiveClient
from .origin import OriginStore, Revision
from .protocol import Request, Response, handle_conditional, handle_full
from . import validators

__all__ = [
    "CachingClient",
    "NaiveClient",
    "OriginStore",
    "Revision",
    "Request",
    "Response",
    "handle_conditional",
    "handle_full",
    "validators",
]
