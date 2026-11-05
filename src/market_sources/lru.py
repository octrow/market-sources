"""A tiny size-bounded mapping that evicts the least recently used entry."""

from __future__ import annotations

from collections import OrderedDict
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class LRUDict(OrderedDict[K, V], Generic[K, V]):
    """``OrderedDict`` capped at ``maxsize``; reads and writes mark an entry as fresh."""

    def __init__(self, maxsize: int) -> None:
        super().__init__()
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        self.maxsize = maxsize

    def __getitem__(self, key: K) -> V:
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value

    def get(self, key: K, default=None):  # type: ignore[override]
        if key in self:
            return self[key]
        return default

    def __setitem__(self, key: K, value: V) -> None:
        super().__setitem__(key, value)
        self.move_to_end(key)
        while len(self) > self.maxsize:
            self.popitem(last=False)


__all__ = ["LRUDict"]
