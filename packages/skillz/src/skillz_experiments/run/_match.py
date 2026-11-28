"""The match rule of an outcome row: the candidate, case, split, and repeat that the row records.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Final

_ANY: Final = object()
"""The default of an optional filter: the row matches whatever value it holds."""


def matches(item: Mapping[str, object], *, candidate: object, case_id: object, split: object = _ANY,
            repeat: object = _ANY) -> bool:
    """Return whether `item` records `candidate` on `case_id`, and on `split` and `repeat` when the caller gives them.

    Each value is compared with `==`. An explicit `repeat=None` matches only a row with no repeat.
    The status is not part of the rule. A caller drops failed rows with `is_failed`.
    """
    return (item.get("search_candidate") == candidate and item.get("case_id") == case_id
            and (split is _ANY or item.get("split") == split) and (repeat is _ANY or item.get("repeat") == repeat))
