from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

import pandas as pd

from .db import get_conn


@dataclass
class TableDoc:
    name: str
    columns: List[Tuple[str, str, bool]]  # (col, dtype, nullable)
    primary_keys: List[str]
    foreign_keys: List[str]
    referenced_by: List[str]
    row_count: Optional[int] = None
    sample: Optional[pd.DataFrame] = None


def _get_tables(schema: str = "public") -> List[str]:
    q = """SELECT table_name
           FROM information_schema.tables
           WHERE table_schema=%s AND table_type='BASE TABLE'
           ORDER BY table_name;"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(q, (schema,))
        return [r[0] for r in cur.fetchall()]


def _get_columns(schema: str = "public") -> Dict[str, List[Tuple[str, str, bool]]]:
    q = """SELECT table_name, column_name, data_type, is_nullable
           FROM information_schema.columns
           WHERE table_schema=%s
           ORDER BY table_name, ordinal_position;"""
    out: Dict[str, List[Tuple[str, str, bool]]] = {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(q, (schema,))
        for table, col, dtype, isnull in cur.fetchall():
            out.setdefault(table, []).append((col, dtype, (isnull == "YES")))
    return out


def _get_primary_keys(schema: str = "public") -> Dict[str, List[str]]:
    q = """SELECT
              tc.table_name,
              kcu.column_name
           FROM information_schema.table_constraints tc
           JOIN information_schema.key_column_usage kcu
             ON tc.constraint_name = kcu.constraint_name
            AND tc.table_schema = kcu.table_schema
           WHERE tc.constraint_type = 'PRIMARY KEY'
             AND tc.table_schema=%s
           ORDER BY tc.table_name, kcu.ordinal_position;"""
    out: Dict[str, List[str]] = {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(q, (schema,))
        for t, c in cur.fetchall():
            out.setdefault(t, []).append(c)
    return out


def _get_foreign_keys(schema: str = "public") -> Dict[str, List[str]]:
    q = """SELECT
        tc.table_name,
        kcu.column_name,
        ccu.table_name AS foreign_table_name,
        ccu.column_name AS foreign_column_name
    FROM information_schema.table_constraints AS tc
    JOIN information_schema.key_column_usage AS kcu
      ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
    JOIN information_schema.constraint_column_usage AS ccu
      ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema
    WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema=%s
    ORDER BY tc.table_name, kcu.column_name;"""
    out: Dict[str, List[str]] = {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(q, (schema,))
        for t, c, ft, fc in cur.fetchall():
            out.setdefault(t, []).append(f"{t}.{c} → {ft}.{fc}")
    return out


def _invert_fks(fks: Dict[str, List[str]]) -> Dict[str, List[str]]:
    # Build "referenced_by" from "A.col → B.col"
    out: Dict[str, List[str]] = {}
    for t, rels in fks.items():
        for rel in rels:
            # rel format: "A.x → B.y"
            try:
                left, right = [x.strip() for x in rel.split("→")]
                b_table = right.split(".")[0].strip()
            except Exception:
                continue
            out.setdefault(b_table, []).append(rel)
    return out


def _safe_row_count(schema: str, table: str, timeout_ms: int = 1500) -> Optional[int]:
    q = f'SELECT COUNT(*) FROM "{schema}"."{table}"'
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {int(timeout_ms)};")
            cur.execute(q)
            return int(cur.fetchone()[0])
    except Exception:
        return None


def _safe_sample(schema: str, table: str, n: int = 3, timeout_ms: int = 1500) -> Optional[pd.DataFrame]:
    q = f'SELECT * FROM "{schema}"."{table}" LIMIT {int(n)}'
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {int(timeout_ms)};")
            cur.execute(q)
            rows = cur.fetchall()
            cols = [d.name for d in cur.description] if cur.description else []
        return pd.DataFrame(rows, columns=cols)
    except Exception:
        return None


def build_table_docs(
    schema: str = "public",
    include_counts: bool = False,
    include_samples: bool = False,
    sample_rows: int = 3,
) -> List[TableDoc]:
    tables = _get_tables(schema)
    cols = _get_columns(schema)
    pks = _get_primary_keys(schema)
    fks = _get_foreign_keys(schema)
    ref_by = _invert_fks(fks)

    docs: List[TableDoc] = []
    for t in tables:
        td = TableDoc(
            name=t,
            columns=cols.get(t, []),
            primary_keys=pks.get(t, []),
            foreign_keys=fks.get(t, []),
            referenced_by=ref_by.get(t, []),
        )
        if include_counts:
            td.row_count = _safe_row_count(schema, t)
        if include_samples:
            td.sample = _safe_sample(schema, t, n=sample_rows)
        docs.append(td)
    return docs


def _md_table(rows: List[List[str]], header: List[str]) -> str:
    # simple markdown table
    out = []
    out.append("| " + " | ".join(header) + " |")
    out.append("|" + "|".join(["---"] * len(header)) + "|")
    for r in rows:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def generate_documentation_markdown(
    schema: str = "public",
    include_counts: bool = True,
    include_samples: bool = False,
    sample_rows: int = 3,
) -> str:
    docs = build_table_docs(
        schema=schema,
        include_counts=include_counts,
        include_samples=include_samples,
        sample_rows=sample_rows,
    )

    lines: List[str] = []
    lines.append(f"# Database Documentation — schema `{schema}`")
    lines.append("")
    lines.append(f"Tables: **{len(docs)}**")
    lines.append("")
    lines.append("## Relationships (FK)")
    any_fk = False
    for td in docs:
        for rel in td.foreign_keys:
            any_fk = True
            lines.append(f"- {rel}")
    if not any_fk:
        lines.append("_No foreign keys found._")
    lines.append("")

    lines.append("## Tables")
    for td in docs:
        lines.append(f"### `{td.name}`")
        if td.row_count is not None:
            lines.append(f"- Rows: **{td.row_count}**")
        if td.primary_keys:
            lines.append(f"- PK: `{', '.join(td.primary_keys)}`")
        if td.foreign_keys:
            lines.append(f"- FK: " + ", ".join([f"`{x}`" for x in td.foreign_keys]))
        if td.referenced_by:
            # limit list for readability
            rb = td.referenced_by[:8]
            more = f" (+{len(td.referenced_by)-len(rb)} more)" if len(td.referenced_by) > len(rb) else ""
            lines.append(f"- Referenced by: " + ", ".join([f"`{x}`" for x in rb]) + more)

        lines.append("")
        rows = []
        for col, dtype, nullable in td.columns:
            rows.append([f"`{col}`", dtype, "YES" if nullable else "NO"])
        lines.append(_md_table(rows, header=["column", "type", "nullable"]))
        lines.append("")

        if td.sample is not None and not td.sample.empty:
            lines.append(f"Sample (`LIMIT {sample_rows}`):")
            df = td.sample.copy()
            if df.shape[1] > 8:
                df = df.iloc[:, :8]
            header = [str(c) for c in df.columns]
            rows2 = []
            for _, r in df.iterrows():
                rows2.append([("" if pd.isna(v) else str(v)) for v in r.tolist()])
            lines.append(_md_table(rows2, header=header))
            lines.append("")

    return "\n".join(lines).strip() + "\n"

