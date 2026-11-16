"""Small shared definitions for the Sydney business lead exporter."""

from datetime import datetime, timezone


class LeadError(Exception):
    """A recoverable input, upstream-service, or data-integrity error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
