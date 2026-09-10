"""The ``encoding/json`` behaviour the Sentry payload depends on.

Python's ``json`` is not a drop-in substitute:

* ``json.Marshal`` **HTML-escapes by default**, so ``<``, ``>`` and ``&`` become
  ``\\u003c``, ``\\u003e`` and ``\\u0026``;
* Go sorts map keys, and does so by UTF-8 **bytes**;
* ``omitempty`` drops an empty string, slice or map but **never a struct** --
  which is why ``sdk`` and ``user`` always appear in the payload;
* ``time.Time`` renders as RFC3339 with trailing zeros trimmed from the fraction.

Every rule here is pinned by ``verification/go-truth.json``.
"""

from __future__ import annotations

import datetime as _dt
import math
from decimal import Decimal
from typing import Any, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "GoTime",
    "is_zero_instant",
    "to_utc_parts",
    "encode_string",
    "encode_value",
    "encode_string_map",
    "encode_str_list",
    "format_rfc3339_nano",
    "is_empty",
]


class GoTime(_dt.datetime):
    """A ``datetime`` that can carry Go's nanosecond precision.

    ``time.Time`` resolves to nanoseconds; ``datetime`` stops at microseconds,
    so a timestamp like ``...123456789Z`` would lose its last three digits.
    ``datetime`` itself forbids attribute assignment, hence the subclass.
    """

    _go_nanos: "Optional[int]" = None


_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def encode_string(s: str) -> str:
    """Port of ``encoding/json``'s string encoder with HTML escaping enabled."""
    # surrogateescape only round-trips U+DC80-DCFF (bytes that failed to decode).
    # Any other lone surrogate would raise; Go's JSON *decoder* has already
    # turned unpaired surrogates into U+FFFD, so the marshalled string never
    # contains one. Normalising here reproduces that end state.
    if any(0xD800 <= ord(ch) <= 0xDFFF and not (0xDC80 <= ord(ch) <= 0xDCFF) for ch in s):
        s = "".join(
            "\ufffd" if (0xD800 <= ord(ch) <= 0xDFFF and not (0xDC80 <= ord(ch) <= 0xDCFF)) else ch
            for ch in s
        )
    raw = s.encode("utf-8", "surrogateescape")
    out = ['"']
    i = 0
    n = len(raw)
    while i < n:
        b = raw[i]
        if b < 0x80:
            ch = chr(b)
            if ch in _ESCAPES:
                out.append(_ESCAPES[ch])
            elif ch in ("<", ">", "&"):
                out.append("\\u%04x" % b)
            elif b < 0x20:
                out.append("\\u%04x" % b)
            else:
                out.append(ch)
            i += 1
            continue
        size = _rune_len(raw, i)
        if size == 0:
            # Go emits one \ufffd escape per invalid byte.
            out.append("\\ufffd")
            i += 1
            continue
        ch = raw[i : i + size].decode("utf-8")
        cp = ord(ch)
        if cp in (0x2028, 0x2029):
            out.append("\\u%04x" % cp)
        else:
            out.append(ch)
        i += size
    out.append('"')
    return "".join(out)


def _rune_len(raw: bytes, i: int) -> int:
    b = raw[i]
    if b < 0xC0 or b > 0xF4:
        return 0
    size = 2 if b < 0xE0 else (3 if b < 0xF0 else 4)
    if i + size > len(raw):
        return 0
    try:
        raw[i : i + size].decode("utf-8")
    except UnicodeDecodeError:
        return 0
    return size


def _shortest_digits(x: float) -> Tuple[str, int]:
    _sign, raw, exponent = Decimal(repr(x)).as_tuple()
    digits = "".join(str(d) for d in raw)
    dp = len(digits) + int(exponent)
    return (digits.rstrip("0") or "0"), dp


def _positional(digits: str, dp: int) -> str:
    if dp <= 0:
        return "0." + "0" * (-dp) + digits
    if dp >= len(digits):
        return digits + "0" * (dp - len(digits))
    return digits[:dp] + "." + digits[dp:]


def _encode_float(f: float) -> str:
    """Port of ``encoding/json``'s float encoder."""
    if f != f or f in (float("inf"), float("-inf")):
        raise ValueError("json: unsupported value: {}".format(f))
    neg = math.copysign(1.0, f) < 0
    a = abs(f)
    if a == 0.0:
        return "-0" if neg else "0"
    digits, dp = _shortest_digits(a)
    if a < 1e-6 or a >= 1e21:
        mantissa = digits[0] + ("." + digits[1:] if len(digits) > 1 else "")
        exp = dp - 1
        body = "{}e{}{}".format(mantissa, "-" if exp < 0 else "+", abs(exp))
    else:
        body = _positional(digits, dp)
    return ("-" + body) if neg else body


def encode_value(v: Any) -> str:
    """Encode one ``interface{}`` value the way ``json.Marshal`` does."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return _encode_float(v)
    if isinstance(v, str):
        return encode_string(v)
    if isinstance(v, Mapping):
        return encode_map(v)
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(encode_value(x) for x in v) + "]"
    return encode_string(str(v))


def _sorted_keys(m: Mapping[str, Any]) -> List[str]:
    """Go sorts map keys by their UTF-8 bytes."""
    return sorted(m.keys(), key=lambda k: k.encode("utf-8", "surrogateescape"))


def encode_map(m: Mapping[str, Any]) -> str:
    parts = ["{}:{}".format(encode_string(k), encode_value(m[k])) for k in _sorted_keys(m)]
    return "{" + ",".join(parts) + "}"


def encode_string_map(m: Mapping[str, str]) -> str:
    parts = ["{}:{}".format(encode_string(k), encode_string(m[k])) for k in _sorted_keys(m)]
    return "{" + ",".join(parts) + "}"


def encode_str_list(xs: Sequence[str]) -> str:
    return "[" + ",".join(encode_string(x) for x in xs) + "]"


def is_empty(v: Any) -> bool:
    """Port of ``isEmptyValue`` for the kinds this payload carries.

    Deliberately has **no** case for a struct: Go's does not either, which is why
    ``sdk`` and ``user`` survive ``omitempty``.
    """
    if v is None:
        return True
    if isinstance(v, bool):
        return v is False
    if isinstance(v, (int, float)):
        return v == 0
    if isinstance(v, (str, bytes, list, tuple, dict)):
        return len(v) == 0
    return False


def _civil_from_days(z: int) -> Tuple[int, int, int]:
    """Proleptic Gregorian date from days since 1970-01-01, for any year.

    ``datetime`` cannot represent year 0 or earlier, but Go's ``time.Time`` can:
    converting 0001-01-01T00:00+01:00 to UTC yields **0000-12-31T23:00:00Z**.
    """
    z += 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + (3 if mp < 10 else -9)
    return (y + (1 if m <= 2 else 0), m, d)


def to_utc_parts(t: _dt.datetime) -> Tuple[int, int, int, int, int, int]:
    """UTC calendar parts of ``t``, without ``datetime``'s year-1 floor.

    ``astimezone`` raises ``OverflowError`` when a positive offset pushes the
    instant below ``datetime.min``; Go simply rolls into year 0. The shift is
    therefore done in integer day/second arithmetic.
    """
    offset = t.utcoffset()
    if offset is None:
        return (t.year, t.month, t.day, t.hour, t.minute, t.second)
    days = _dt.date(t.year, t.month, t.day).toordinal() - 719163
    secs = t.hour * 3600 + t.minute * 60 + t.second - int(offset.total_seconds())
    days += secs // 86400
    secs %= 86400
    y, m, d = _civil_from_days(days)
    return (y, m, d, secs // 3600, (secs % 3600) // 60, secs % 60)


def is_zero_instant(t: _dt.datetime) -> bool:
    """Port of ``time.Time.IsZero``: the **instant**, not the wall-clock fields.

    Go compares seconds and nanoseconds since its own epoch, so zone is
    irrelevant and sub-microsecond digits count. ``0001-01-01T01:00:00+01:00``
    is zero; ``0001-01-01T00:00:00-01:00`` is not.
    """
    if (getattr(t, "_go_nanos", None) or 0) % 1000 != 0:
        return False
    y, mo, d, h, mi, s = to_utc_parts(t)
    return (y, mo, d, h, mi, s, t.microsecond) == (1, 1, 1, 0, 0, 0, 0)


def format_rfc3339_nano(t: _dt.datetime) -> str:
    """RFC3339 with trailing zeros trimmed from the fraction, as Go writes it."""
    y, mo, d, h, mi, s = to_utc_parts(t)
    frac = "{:06d}".format(t.microsecond)
    nanos = getattr(t, "_go_nanos", None)
    if nanos is not None:
        frac = "{:09d}".format(nanos)
    trimmed = frac.rstrip("0")
    dot = "." + trimmed if trimmed else ""
    return "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}{}Z".format(y, mo, d, h, mi, s, dot)
