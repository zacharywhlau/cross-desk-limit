"""Data sources ("treats"). The ONLY place in src/ where pandas may be imported.

Every table reaches the logic layer as ``list[dict]`` with the same keys, whatever
the source, so there is only one parsing path to debug.
"""

from __future__ import annotations

__all__ = ["api", "cache", "mock", "source", "sql", "tabular"]
