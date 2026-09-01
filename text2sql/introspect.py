from __future__ import annotations
from typing import Dict, List, Tuple
from .db import get_conn

def get_tables(schema: str = "public") -> List[str]:
    q = """SELECT table_name
            FROM information_schema.tables
            WHERE table_schema=%s AND table_type='BASE TABLE'
            ORDER BY table_name;"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(q, (schema,))
        return [r[0] for r in cur.fetchall()]

def get_table_columns(schema: str = "public") -> Dict[str, List[Tuple[str,str,bool]]]:
    q = """SELECT table_name, column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema=%s
            ORDER BY table_name, ordinal_position;"""
    out: Dict[str, List[Tuple[str,str,bool]]] = {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(q, (schema,))
        for table, col, dtype, isnull in cur.fetchall():
            out.setdefault(table, []).append((col, dtype, (isnull == "YES")))
    return out

def get_foreign_keys(schema: str = "public") -> Dict[str, List[str]]:
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
            out.setdefault(t, []).append(f"* {t}.{c} -> {ft}.{fc}")
    return out

def schema_as_text(schema: str = "public") -> str:
    tables = get_tables(schema)
    cols = get_table_columns(schema)
    fks = get_foreign_keys(schema)
    lines: List[str] = []
    for t in tables:
        lines.append(f"TABLE {t}:")
        for col, dtype, isnull in cols.get(t, []):
            null_txt = " NULL" if isnull else ""
            lines.append(f"  - {col} ({dtype}{null_txt})")
        if t in fks:
            lines.append("  FKs:")
            for s in fks[t]:
                lines.append(f"    {s}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"
