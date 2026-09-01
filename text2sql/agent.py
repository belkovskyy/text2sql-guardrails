from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Optional, Tuple

import requests

from .config import load_env
from .db import execute_select
from .guardrails import validate_and_sanitize_select
from .introspect import schema_as_text
from .schema_search import search_schema

load_env()

DEFAULT_OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
DEFAULT_TRANSLATE = os.getenv("TRANSLATE_RU_EN", "true").lower() in {"1", "true", "yes", "y"}

@dataclass
class AgentOutput:
    sql: str
    df: Any  # pandas.DataFrame
    warning: Optional[str] = None
    attempts: int = 1

def _extract_sql(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"```sql\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if m:
        candidate = m.group(1).strip()
    else:
        m = re.search(r"\b(with|select)\b[\s\S]*", text, flags=re.IGNORECASE)
        candidate = (m.group(0).strip() if m else text.strip())

    candidate = candidate.strip().strip("`").strip()
    parts = [p.strip() for p in candidate.split(";") if p.strip()]
    if parts:
        candidate = parts[0]
    return candidate.strip()

# Deterministic fixes for common Northwind hallucinations (cheap + effective)
SYNONYM_FIXES = [
    (re.compile(r"\bcustomer_name\b", re.I), "company_name"),
    (re.compile(r"\bsupplier_name\b", re.I), "company_name"),
    (re.compile(r"\bshipper_name\b", re.I), "company_name"),
]

def apply_synonym_fixes(sql: str) -> Tuple[str, bool]:
    changed = False
    out = sql
    for pat, repl in SYNONYM_FIXES:
        new = pat.sub(repl, out)
        if new != out:
            out = new
            changed = True
    return out, changed

class Text2SQLAgent:
    def __init__(self, model: str = DEFAULT_MODEL, ollama_url: str = DEFAULT_OLLAMA_URL):
        self.model = model
        self.ollama_url = ollama_url.rstrip("/")
        self._schema_cache: Optional[str] = None

    def get_schema_text(self, refresh: bool = False) -> str:
        if refresh or self._schema_cache is None:
            self._schema_cache = schema_as_text()
        return self._schema_cache

    def _chat(self, system: str, user: str) -> str:
        """Call Ollama. Prefer /api/chat, fallback to /api/generate for older builds."""
        # 1) Try /api/chat (newer Ollama)
        chat_payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": 0.0},
        }
        try:
            r = requests.post(f"{self.ollama_url}/api/chat", json=chat_payload, timeout=90)
            if r.status_code == 404:
                raise requests.HTTPError("404_not_found", response=r)
            r.raise_for_status()
            data = r.json()
            return (data.get("message", {}) or {}).get("content", "") or ""
        except requests.HTTPError as e:
            # If /api/chat not found, fallback to /api/generate (older Ollama)
            resp = getattr(e, "response", None)
            if resp is None or resp.status_code != 404:
                raise

        # 2) Fallback: /api/generate
        prompt = f"""SYSTEM:
{system}

USER:
{user}

ASSISTANT:
"""
        gen_payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0},
        }
        r2 = requests.post(f"{self.ollama_url}/api/generate", json=gen_payload, timeout=120)
        r2.raise_for_status()
        data2 = r2.json()
        return data2.get("response", "") or ""

    def _few_shots(self) -> str:
        # These drastically reduce GroupingError / wrong joins on Northwind
        return """# Examples (Northwind)

## Revenue by category (top-5)
SELECT c.category_name,
       SUM(od.unit_price * od.quantity * (1 - od.discount)) AS revenue
FROM order_details od
JOIN products p   ON p.product_id = od.product_id
JOIN categories c ON c.category_id = p.category_id
GROUP BY c.category_name
ORDER BY revenue DESC
LIMIT 5;

## Top-5 customers by number of orders
WITH t AS (
  SELECT o.customer_id, COUNT(*) AS order_count
  FROM orders o
  WHERE o.customer_id IS NOT NULL
  GROUP BY o.customer_id
)
SELECT c.company_name, t.order_count
FROM t
JOIN customers c ON c.customer_id = t.customer_id
ORDER BY t.order_count DESC
LIMIT 5;

## Top-5 employees by number of orders
SELECT e.first_name, e.last_name, COUNT(*) AS n_orders
FROM orders o
JOIN employees e ON e.employee_id = o.employee_id
GROUP BY e.employee_id, e.first_name, e.last_name
ORDER BY n_orders DESC
LIMIT 5;

## Revenue by country (top-5)
SELECT c.country,
       SUM(od.unit_price * od.quantity * (1 - od.discount)) AS revenue
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
JOIN order_details od ON od.order_id = o.order_id
GROUP BY c.country
ORDER BY revenue DESC
LIMIT 5;

## Average check per customer (top-5)
WITH order_totals AS (
  SELECT o.order_id, o.customer_id,
         SUM(od.unit_price * od.quantity * (1 - od.discount)) AS order_total
  FROM orders o
  JOIN order_details od ON od.order_id = o.order_id
  WHERE o.customer_id IS NOT NULL
  GROUP BY o.order_id, o.customer_id
),
customer_stats AS (
  SELECT customer_id,
         AVG(order_total) AS avg_check,
         COUNT(*) AS n_orders
  FROM order_totals
  GROUP BY customer_id
)
SELECT c.company_name, cs.avg_check, cs.n_orders
FROM customer_stats cs
JOIN customers c ON c.customer_id = cs.customer_id
ORDER BY cs.avg_check DESC
LIMIT 5;
"""

    def generate_sql(self, question: str, translate_to_en: bool = DEFAULT_TRANSLATE) -> str:
        full_schema = self.get_schema_text()
        schema_hint = search_schema(full_schema, question, max_lines=180)

        sys = (
            "You are a senior data analyst. Produce ONE PostgreSQL query.\n"
            "Rules:\n"
            "- Output ONLY SQL (no explanations).\n"
            "- Only SELECT / WITH. No DDL/DML.\n"
            "- Use ONLY table/column names from schema; NEVER invent columns.\n"
            "- Prefer aliases; qualify columns when joining.\n"
            "- Use FK relations from schema for joins.\n"
            "- GROUP BY rule: every selected column that is NOT aggregated must be in GROUP BY.\n"
        )

        user = f"""User question (RU): {question}

Internally translate RU -> EN for correctness, then output final PostgreSQL SQL.

Relevant schema (snippet):
{schema_hint}

Full schema:
{full_schema}

{self._few_shots()}

Output ONLY SQL.
"""
        text = self._chat(sys, user)
        return _extract_sql(text)

    def repair_sql(self, question: str, bad_sql: str, db_error: str) -> str:
        full_schema = self.get_schema_text()
        schema_hint = search_schema(full_schema, question + " " + bad_sql, max_lines=200)

        extra_tips = []
        if "must appear in the GROUP BY" in db_error or "GroupingError" in db_error:
            extra_tips.append("Fix GROUP BY: remove non-aggregated columns from SELECT or add them to GROUP BY.")
            extra_tips.append("For revenue by category use SUM(unit_price*quantity*(1-discount)) grouped by c.category_name.")
        if "UndefinedColumn" in db_error:
            extra_tips.append("Fix column names: use EXACT schema names (e.g., customers.company_name). No invented columns.")
        if "operator does not exist" in db_error:
            extra_tips.append("Fix JOIN: join only FK-compatible columns with matching types (see schema FKs).")

        tips = "\n".join(f"- {t}" for t in extra_tips) if extra_tips else ""

        sys = (
            "You are a senior data analyst. Fix SQL for PostgreSQL.\n"
            "Rules:\n"
            "- Return ONLY ONE SQL statement (SELECT/WITH). No explanations.\n"
            "- Use ONLY names from schema.\n"
            "- Keep joins simple and correct using FK relations.\n"
            "- Enforce correct GROUP BY rules.\n"
        )

        user = f"""User question (RU): {question}

Previous SQL (buggy):
{bad_sql}

PostgreSQL error:
{db_error}

Relevant schema:
{schema_hint}

{tips}

{self._few_shots()}

Task: output corrected SQL ONLY.
"""
        text = self._chat(sys, user)
        return _extract_sql(text)

    def run_sql(self, sql: str, max_rows: int = 200):
        gr = validate_and_sanitize_select(sql, max_rows=max_rows)
        if not gr.ok:
            raise ValueError(f"SQL blocked by guardrails: {gr.reason}")
        df = execute_select(gr.sanitized_sql, max_rows=max_rows)
        return df, gr.sanitized_sql

    def answer(self, question: str, max_rows: int = 200, max_retries: int = 2, translate_to_en: bool = DEFAULT_TRANSLATE) -> AgentOutput:
        sql = self.generate_sql(question, translate_to_en=translate_to_en)

        attempts = 0
        warn_parts = []

        for attempt in range(max_retries + 1):
            attempts += 1

            sql, changed = apply_synonym_fixes(sql)
            if changed:
                warn_parts.append("Applied synonym fixes (e.g., customer_name->company_name)")

            try:
                df, sanitized = self.run_sql(sql, max_rows=max_rows)
                if sanitized.strip().rstrip(";") != sql.strip().rstrip(";"):
                    warn_parts.append("SQL was sanitized (LIMIT enforced / formatting)")
                warning = "; ".join(warn_parts) if warn_parts else None
                return AgentOutput(sql=sanitized, df=df, warning=warning, attempts=attempts)
            except Exception as e:
                if attempt >= max_retries:
                    raise
                sql = self.repair_sql(question, sql, str(e))

        raise RuntimeError("Unexpected agent loop exit")
