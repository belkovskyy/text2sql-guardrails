"""Проверка защиты: что блокируется и что должно проходить."""

import pytest

from text2sql.guardrails import validate_and_sanitize_select as check

# Запросы, которые должны быть заблокированы.
ATTACKS = [
    ("прямой DROP", "DROP TABLE orders"),
    ("прямой DELETE", "DELETE FROM orders"),
    ("прямой UPDATE", "UPDATE orders SET total = 0"),
    ("прямой INSERT", "INSERT INTO orders (id) VALUES (1)"),
    ("TRUNCATE", "TRUNCATE TABLE orders"),
    ("ALTER", "ALTER TABLE orders DROP COLUMN total"),
    ("GRANT", "GRANT ALL ON orders TO PUBLIC"),
    ("стек: select + drop", "SELECT 1; DROP TABLE orders"),
    ("стек: select + delete", "SELECT * FROM orders; DELETE FROM orders"),
    ("стек с переносом строки", "SELECT 1;\nUPDATE orders SET total = 0"),
    ("пишущий CTE с DELETE", "WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x"),
    ("пишущий CTE с INSERT", "WITH x AS (INSERT INTO orders VALUES (1) RETURNING *) SELECT * FROM x"),
    ("пишущий CTE с UPDATE", "WITH x AS (UPDATE orders SET total = 0 RETURNING *) SELECT * FROM x"),
    ("вложенный DROP в подзапросе", "SELECT * FROM (SELECT 1) t; DROP TABLE orders"),
    ("COPY TO PROGRAM", "COPY (SELECT 1) TO PROGRAM 'external_cmd'"),
    ("смена настроек сессии", "SET statement_timeout = 0"),
    ("создание функции", "CREATE FUNCTION f() RETURNS int AS $$ SELECT 1 $$ LANGUAGE sql"),
    ("вызов процедуры", "CALL do_something()"),
    ("транзакция", "BEGIN; DELETE FROM orders; COMMIT"),
    ("пустой запрос", "   "),
    ("чтение файла с сервера", "SELECT pg_read_file('server_secret_path')"),
    ("список каталога", "SELECT pg_ls_dir('server_dir')"),
    ("запись файла", "SELECT lo_export(1, 'out_path')"),
    ("удержание соединения", "SELECT pg_sleep(30)"),
    ("обрыв чужой сессии", "SELECT pg_terminate_backend(123)"),
    ("EXECUTE после точки с запятой", "SELECT 1; EXECUTE stmt"),
]

# Запросы, которые должны проходить.
LEGIT = [
    ("простая выборка", "SELECT * FROM orders"),
    ("агрегация с группировкой", "SELECT customer_id, COUNT(*) FROM orders GROUP BY customer_id"),
    ("джойн", "SELECT o.id, c.name FROM orders o JOIN customers c ON c.id = o.customer_id"),
    ("обычный CTE", "WITH top AS (SELECT * FROM orders LIMIT 10) SELECT * FROM top"),
    ("несколько CTE", "WITH a AS (SELECT 1 x), b AS (SELECT 2 y) SELECT * FROM a, b"),
    ("подзапрос", "SELECT * FROM orders WHERE customer_id IN (SELECT id FROM customers)"),
    ("оконная функция", "SELECT id, ROW_NUMBER() OVER (ORDER BY total DESC) FROM orders"),
    ("UNION", "SELECT id FROM orders UNION SELECT id FROM archive_orders"),
    ("слово delete в строке", "SELECT * FROM notes WHERE body = 'please delete me'"),
    ("слово drop в строке", "SELECT * FROM notes WHERE body LIKE '%drop table%'"),
    ("колонка update_date", "SELECT update_date FROM orders"),
    ("колонка created_at", "SELECT created_at, deleted_at FROM orders"),
    ("слово в комментарии", "SELECT * FROM orders /* не трогаем DROP TABLE */"),
    ("однострочный комментарий", "SELECT * FROM orders -- DROP TABLE orders"),
    ("уже с LIMIT", "SELECT * FROM orders LIMIT 5"),
    ("точка с запятой в конце", "SELECT * FROM orders;"),
    ("имя функции в строке", "SELECT * FROM logs WHERE msg = 'pg_read_file(x)'"),
    ("обычная функция", "SELECT count(*), upper(name) FROM customers"),
]


@pytest.mark.parametrize("name,sql", ATTACKS, ids=[n for n, _ in ATTACKS])
def test_attack_is_blocked(name, sql):
    result = check(sql)
    assert result.ok is False, f"пропущен опасный запрос: {sql}"


@pytest.mark.parametrize("name,sql", LEGIT, ids=[n for n, _ in LEGIT])
def test_legit_passes(name, sql):
    result = check(sql)
    assert result.ok is True, f"заблокирован легитимный запрос: {sql} ({result.reason})"


def test_limit_is_added():
    result = check("SELECT * FROM orders", max_rows=50)
    assert result.ok
    assert "LIMIT 50" in result.sanitized_sql


def test_existing_limit_is_kept():
    result = check("SELECT * FROM orders LIMIT 5", max_rows=50)
    assert result.ok
    assert "LIMIT 50" not in result.sanitized_sql
    assert "LIMIT 5" in result.sanitized_sql
