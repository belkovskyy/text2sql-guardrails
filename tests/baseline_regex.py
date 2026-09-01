"""Исходная защита на регулярных выражениях — baseline для сравнения.

Блокирует запрещённые операторы поиском слов по тексту и требует, чтобы запрос
начинался с SELECT или WITH. Структуру SQL не разбирает, поэтому:
- пропускает опасные функции (pg_read_file, pg_sleep, pg_terminate_backend, ...)
  и EXECUTE после точки с запятой — они не входят в список слов;
- ложно блокирует delete/drop внутри строковых литералов и комментариев.

Токенная версия в text2sql/guardrails.py оба класса случаев закрывает. Сравнение
двух реализаций на одних и тех же запросах — в test_comparison.py.
"""
from __future__ import annotations

import re

from text2sql.guardrails import GuardrailResult, _ensure_limit

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|call|copy)\b",
    re.I,
)
_START = re.compile(r"\s*(with|select)\b", re.I)


def validate_and_sanitize_select(sql: str, max_rows: int = 200) -> GuardrailResult:
    if not sql or not sql.strip():
        return GuardrailResult(False, "empty_sql")
    if not _START.match(sql):
        return GuardrailResult(False, "only_select_with_allowed")
    if _FORBIDDEN.search(sql):
        return GuardrailResult(False, "ddl_dml_blocked")
    sanitized = _ensure_limit(sql.strip(), max_rows=max_rows)
    return GuardrailResult(True, None, sanitized)
