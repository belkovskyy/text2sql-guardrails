from __future__ import annotations
from dataclasses import dataclass
import re

import sqlparse
from sqlparse import tokens as T

# Ключевые слова, которых не должно быть ни на каком уровне запроса.
# Проверяются по типу токена, а не поиском по тексту: слово delete внутри
# строкового литерала или в имени колонки update_date запрос не блокирует.
FORBIDDEN = {
    "INSERT", "UPDATE", "DELETE", "MERGE", "UPSERT", "REPLACE",
    "DROP", "ALTER", "CREATE", "TRUNCATE", "RENAME",
    "GRANT", "REVOKE", "COMMENT",
    "CALL", "EXECUTE", "DO", "COPY", "VACUUM", "ANALYZE",
    "COMMIT", "ROLLBACK", "SAVEPOINT", "BEGIN", "START",
    "SET", "RESET", "LOCK", "LISTEN", "NOTIFY", "PREPARE", "DEALLOCATE",
}

ALLOWED_TYPES = {"SELECT"}

# Функции, доступные из обычного SELECT и потому не покрытые проверкой операторов:
# чтение и запись файлов на сервере, запуск команд, удержание соединения.
FORBIDDEN_FUNCTIONS = {
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
    "lo_import", "lo_export",
    "pg_sleep", "pg_sleep_for", "pg_sleep_until",
    "pg_terminate_backend", "pg_cancel_backend",
    "dblink", "dblink_exec", "query_to_xml",
    "load_extension", "readfile", "writefile", "system", "shell",
}

_FUNC_CALL = re.compile(r"\b([a-z_][a-z0-9_]*)\s*\(", re.I)


@dataclass
class GuardrailResult:
    ok: bool
    reason: str | None = None
    sanitized_sql: str | None = None


def _walk(token) -> list:
    out = []
    for child in token.tokens:
        out.append(child)
        if child.is_group:
            out.extend(_walk(child))
    return out


def _forbidden_keyword(statement) -> str | None:
    """Первое запрещённое ключевое слово на любом уровне вложенности.

    Ловит пишущие CTE вида `WITH x AS (DELETE ... RETURNING *) SELECT * FROM x`,
    которые PostgreSQL выполняет, а sqlparse относит к типу SELECT.
    """
    for token in _walk(statement):
        if token.ttype in (T.Keyword.DDL, T.Keyword.DML, T.Keyword.TZCast):
            if token.normalized.upper() in FORBIDDEN:
                return token.normalized.upper()
        elif token.ttype is T.Keyword and token.normalized.upper() in FORBIDDEN:
            return token.normalized.upper()
    return None


def _forbidden_function(statement) -> str | None:
    """Опасный вызов функции. Литералы и комментарии из текста убираются,
    чтобы строка 'pg_read_file(' в данных не считалась вызовом."""
    stripped = sqlparse.format(str(statement), strip_comments=True)
    stripped = re.sub(r"'[^']*'", "''", stripped)
    for name in _FUNC_CALL.findall(stripped):
        if name.lower() in FORBIDDEN_FUNCTIONS:
            return name.lower()
    return None


def _ensure_limit(sql: str, max_rows: int) -> str:
    if re.search(r"\blimit\s+\d+\b", sql, re.I):
        return sql.rstrip().rstrip(";")
    return sql.rstrip().rstrip(";") + f"\nLIMIT {int(max_rows)}"


def validate_and_sanitize_select(sql: str, max_rows: int = 200) -> GuardrailResult:
    if not sql or not sql.strip():
        return GuardrailResult(False, "empty_sql")

    statements = [s for s in sqlparse.parse(sql) if str(s).strip().strip(";")]
    if not statements:
        return GuardrailResult(False, "parse_error")
    if len(statements) > 1:
        return GuardrailResult(False, "multiple_statements")

    statement = statements[0]

    if statement.get_type() not in ALLOWED_TYPES:
        return GuardrailResult(False, "only_select_with_allowed")

    found = _forbidden_keyword(statement)
    if found:
        return GuardrailResult(False, f"forbidden_keyword:{found}")

    func = _forbidden_function(statement)
    if func:
        return GuardrailResult(False, f"forbidden_function:{func}")

    sanitized = _ensure_limit(str(statement).strip(), max_rows=max_rows)
    return GuardrailResult(True, None, sanitized)
