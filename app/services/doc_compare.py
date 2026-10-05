"""Compare two extracted documents and highlight field-level drift."""

from __future__ import annotations

from typing import Any


def compare_fields(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    keys = sorted(set(left) | set(right))
    added, removed, changed, same = [], [], [], []
    for key in keys:
        if key not in left:
            added.append(key)
        elif key not in right:
            removed.append(key)
        elif left[key] != right[key]:
            changed.append({"field": key, "from": left[key], "to": right[key]})
        else:
            same.append(key)
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged": same,
        "drift_ratio": round(len(changed) / max(1, len(keys)), 2),
    }
