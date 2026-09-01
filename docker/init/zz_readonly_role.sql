-- Второй слой защиты: роль только для чтения.
-- Приложение ходит в базу под ней, поэтому даже успешно обойдённый
-- парсер SQL не сможет ничего изменить — прав на запись просто нет.
-- Guardrails на уровне приложения и права на уровне БД дополняют друг друга.

CREATE ROLE text2sql_ro LOGIN PASSWORD 'readonly';

GRANT CONNECT ON DATABASE northwind TO text2sql_ro;
GRANT USAGE ON SCHEMA public TO text2sql_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO text2sql_ro;

-- и на таблицы, которые появятся позже
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO text2sql_ro;
