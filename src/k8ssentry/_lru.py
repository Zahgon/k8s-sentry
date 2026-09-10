"""Port of ``github.com/hashicorp/golang-lru`` v0.5.1's ``Cache``.

Only the three calls ``isNewTermination`` makes are reproduced: ``New``,
``Get`` and ``Add``. Recency matters -- both ``Get`` and ``Add`` move an entry
to the front -- because the cache is what stops a single container termination
being reported repeatedly.

``Get`` returns Go's ``(value, ok)`` pair rather than raising, so the caller can
distinguish "absent" from "present and nil".
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Hashable, Tuple

__all__ = ["Cache", "New"]


class Cache:
    """Port of ``lru.Cache``: fixed size, least-recently-used eviction."""

    def __init__(self, size: int) -> None:
        if size <= 0:
            raise ValueError("Must provide a positive size")
        self._size = size
        self._d: "OrderedDict[Hashable, Any]" = OrderedDict()
        self._lock = threading.Lock()

    def Get(self, key: Hashable) -> Tuple[Any, bool]:
        """Port of ``Get``: returns ``(value, ok)`` and refreshes recency."""
        with self._lock:
            if key in self._d:
                self._d.move_to_end(key)
                return self._d[key], True
            return None, False

    def Add(self, key: Hashable, value: Any) -> bool:
        """Port of ``Add``: returns whether an eviction occurred."""
        with self._lock:
            if key in self._d:
                self._d[key] = value
                self._d.move_to_end(key)
                return False
            self._d[key] = value
            if len(self._d) > self._size:
                self._d.popitem(last=False)
                return True
            return False

    def Len(self) -> int:
        with self._lock:
            return len(self._d)

    def Contains(self, key: Hashable) -> bool:
        """Port of ``Contains``: membership **without** touching recency."""
        with self._lock:
            return key in self._d


def New(size: int) -> Cache:
    """Port of ``lru.New``, which returns ``(cache, error)`` in Go."""
    return Cache(size)
