"""Port of the ``github.com/getsentry/sentry-go`` v0.7.0 surface this tool uses.

The Python ``sentry-sdk`` is **not** a substitute for the payload: its event
model and serialisation differ from sentry-go's, and the payload is precisely
what this migration has to preserve. The transport is a different matter and
lives behind :mod:`k8ssentry.transport`.

Two serialisation facts drive everything, both recorded from executing Go:

* ``Event.MarshalJSON`` wraps the struct in an outer type that **shadows**
  ``Timestamp``, so ``timestamp`` is emitted **last**, after every other field,
  and is omitted entirely when zero;
* ``omitempty`` never omits a struct, so ``sdk`` and ``user`` appear in every
  payload -- a bare event marshals to ``{"sdk":{},"user":{}}``.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

from ._gojson import (
    encode_str_list,
    encode_string,
    encode_string_map,
    encode_value,
    format_rfc3339_nano,
    is_empty,
    is_zero_instant,
)

__all__ = [
    "Level",
    "LevelDebug",
    "LevelInfo",
    "LevelWarning",
    "LevelError",
    "LevelFatal",
    "SdkInfo",
    "User",
    "Event",
    "NewEvent",
]

Level = str

LevelDebug: Level = "debug"
LevelInfo: Level = "info"
LevelWarning: Level = "warning"
LevelError: Level = "error"
LevelFatal: Level = "fatal"


def _encode_packages(packages: List[Dict[str, str]]) -> str:
    """Port of ``[]SdkPackage``: a struct, so omitempty applies per field.

    Encoding these as generic maps would sort the keys and keep empty ones,
    where Go drops an empty Name/Version and keeps declaration order.
    """
    out = []
    for pkg in packages:
        fields = []
        for key in ("name", "version"):
            val = pkg.get(key, "")
            if not is_empty(val):
                fields.append('"{}":{}'.format(key, encode_string(val)))
        out.append("{" + ",".join(fields) + "}")
    return "[" + ",".join(out) + "]"


class SdkInfo:
    """Port of ``sentry.SdkInfo``."""

    __slots__ = ("Name", "Version", "Integrations", "Packages")

    def __init__(self) -> None:
        self.Name = ""
        self.Version = ""
        self.Integrations: List[str] = []
        self.Packages: List[Dict[str, str]] = []

    def marshal(self) -> str:
        parts = []
        if not is_empty(self.Name):
            parts.append('"name":' + encode_string(self.Name))
        if not is_empty(self.Version):
            parts.append('"version":' + encode_string(self.Version))
        if not is_empty(self.Integrations):
            parts.append('"integrations":' + encode_str_list(self.Integrations))
        if not is_empty(self.Packages):
            parts.append('"packages":' + _encode_packages(self.Packages))
        return "{" + ",".join(parts) + "}"


class User:
    """Port of ``sentry.User``."""

    __slots__ = ("Email", "ID", "IPAddress", "Username")

    def __init__(self) -> None:
        self.Email = ""
        self.ID = ""
        self.IPAddress = ""
        self.Username = ""

    def marshal(self) -> str:
        parts = []
        for key, val in (
            ("email", self.Email),
            ("id", self.ID),
            ("ip_address", self.IPAddress),
            ("username", self.Username),
        ):
            if not is_empty(val):
                parts.append('"{}":{}'.format(key, encode_string(val)))
        return "{" + ",".join(parts) + "}"


class Event:
    """Port of ``sentry.Event`` and its custom ``MarshalJSON``."""

    __slots__ = (
        "Breadcrumbs",
        "Contexts",
        "Dist",
        "Environment",
        "EventID",
        "Extra",
        "Fingerprint",
        "Level",
        "Message",
        "Platform",
        "Release",
        "Sdk",
        "ServerName",
        "Threads",
        "Tags",
        "Timestamp",
        "Transaction",
        "User",
        "Logger",
        "Modules",
        "Request",
        "Exception",
    )

    def __init__(self) -> None:
        self.Breadcrumbs: List[Any] = []
        self.Contexts: Dict[str, Any] = {}
        self.Dist = ""
        self.Environment = ""
        self.EventID = ""
        self.Extra: Dict[str, Any] = {}
        self.Fingerprint: List[str] = []
        self.Level: Level = ""
        self.Message = ""
        self.Platform = ""
        self.Release = ""
        self.Sdk = SdkInfo()
        self.ServerName = ""
        self.Threads: List[Any] = []
        self.Tags: Dict[str, str] = {}
        self.Timestamp: Optional[_dt.datetime] = None
        self.Transaction = ""
        self.User = User()
        self.Logger = ""
        self.Modules: Dict[str, str] = {}
        self.Request: Any = None
        self.Exception: List[Any] = []

    def MarshalJSON(self) -> str:
        """Port of ``(*Event).MarshalJSON`` for regular error events.

        Field order is the Go struct's declaration order, with ``timestamp``
        moved to the end because the outer wrapper shadows it.
        """
        parts: List[str] = []

        def add(key: str, encoded: str) -> None:
            parts.append('"{}":{}'.format(key, encoded))

        if not is_empty(self.Breadcrumbs):
            add("breadcrumbs", encode_value(self.Breadcrumbs))
        if not is_empty(self.Contexts):
            add("contexts", encode_value(self.Contexts))
        if not is_empty(self.Dist):
            add("dist", encode_string(self.Dist))
        if not is_empty(self.Environment):
            add("environment", encode_string(self.Environment))
        if not is_empty(self.EventID):
            add("event_id", encode_string(self.EventID))
        if not is_empty(self.Extra):
            add("extra", encode_value(self.Extra))
        if not is_empty(self.Fingerprint):
            add("fingerprint", encode_str_list(self.Fingerprint))
        if not is_empty(self.Level):
            add("level", encode_string(self.Level))
        if not is_empty(self.Message):
            add("message", encode_string(self.Message))
        if not is_empty(self.Platform):
            add("platform", encode_string(self.Platform))
        if not is_empty(self.Release):
            add("release", encode_string(self.Release))
        # A struct is never omitted by omitempty.
        add("sdk", self.Sdk.marshal())
        if not is_empty(self.ServerName):
            add("server_name", encode_string(self.ServerName))
        if not is_empty(self.Threads):
            add("threads", encode_value(self.Threads))
        if not is_empty(self.Tags):
            add("tags", encode_string_map(self.Tags))
        if not is_empty(self.Transaction):
            add("transaction", encode_string(self.Transaction))
        add("user", self.User.marshal())
        if not is_empty(self.Logger):
            add("logger", encode_string(self.Logger))
        if not is_empty(self.Modules):
            add("modules", encode_string_map(self.Modules))
        if self.Request is not None:
            add("request", encode_value(self.Request))
        if not is_empty(self.Exception):
            add("exception", encode_value(self.Exception))

        # Shadowed in the outer struct, so it lands after everything else.
        if self.Timestamp is not None and not _is_zero_time(self.Timestamp):
            add("timestamp", encode_string(format_rfc3339_nano(self.Timestamp)))

        return "{" + ",".join(parts) + "}"


def _is_zero_time(t: _dt.datetime) -> bool:
    """Go's ``time.Time.IsZero`` compares the **instant**, not the wall clock.

    Zone is irrelevant and sub-microsecond digits count, so
    ``0001-01-01T01:00:00+01:00`` is zero while ``0001-01-01T00:00:00-01:00``
    is not. Comparing calendar fields gets both backwards.
    """
    return is_zero_instant(t)


def NewEvent() -> Event:
    """Port of ``sentry.NewEvent``: the four maps start non-nil but empty."""
    return Event()
