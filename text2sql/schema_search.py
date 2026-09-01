from __future__ import annotations
import re
from typing import List

def search_schema(schema_text: str, query: str, max_lines: int = 160) -> str:
    """Keyword-search over schema text. Returns a compact snippet for prompts."""
    if not query.strip():
        return schema_text

    q = query.lower()
    tokens = [t for t in re.split(r"[^a-zA-Z0-9_]+", q) if t]
    if not tokens:
        return schema_text

    lines = schema_text.splitlines()
    out: List[str] = []
    current_table = None
    for ln in lines:
        if ln.startswith("TABLE "):
            current_table = ln
        low = ln.lower()
        if any(tok in low for tok in tokens):
            if current_table and (not out or out[-1] != current_table):
                out.append(current_table)
            out.append(ln)

    if not out:
        return schema_text

    if len(out) > max_lines:
        out = out[:max_lines] + ["... (truncated)"]
    return "\n".join(out).strip()
