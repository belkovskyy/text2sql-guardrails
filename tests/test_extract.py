"""Извлечение SQL из ответа модели и детерминированные фиксы схемы."""

from text2sql.agent import _extract_sql, apply_synonym_fixes


def test_extract_from_code_fence():
    text = "Ответ:\n```sql\nSELECT * FROM orders\n```\nготово"
    assert _extract_sql(text) == "SELECT * FROM orders"


def test_extract_from_plain_text():
    text = "Вот запрос: SELECT id FROM t WHERE x = 1"
    assert _extract_sql(text) == "SELECT id FROM t WHERE x = 1"


def test_extract_takes_first_statement():
    assert _extract_sql("SELECT 1; SELECT 2") == "SELECT 1"


def test_extract_strips_trailing_semicolon():
    assert _extract_sql("SELECT 1;") == "SELECT 1"


def test_extract_empty():
    assert _extract_sql("") == ""
    assert _extract_sql("тут нет запроса") == "тут нет запроса"


def test_synonym_fix_applied():
    out, changed = apply_synonym_fixes("SELECT customer_name FROM customers")
    assert changed is True
    assert "company_name" in out
    assert "customer_name" not in out


def test_synonym_fix_not_needed():
    out, changed = apply_synonym_fixes("SELECT company_name FROM customers")
    assert changed is False
    assert out == "SELECT company_name FROM customers"
