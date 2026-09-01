from __future__ import annotations

import textwrap
from pathlib import Path

import nbformat as nbf


def md(s: str):
    return nbf.v4.new_markdown_cell(textwrap.dedent(s).strip())


def code(s: str):
    return nbf.v4.new_code_cell(textwrap.dedent(s).rstrip())


def main():
    nb = nbf.v4.new_notebook()
    nb["metadata"] = {
        "kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
        "language_info": {"name": "python", "version": "3.x"},
    }

    nb.cells = [
        md(
            """
# Text-to-SQL (Northwind / Postgres) — mini-eval notebook

Этот ноутбук оценивает качество Text-to-SQL агента на базе Northwind (PostgreSQL).

Метрики:
- **Execution success rate**: доля вопросов, для которых SQL успешно выполняется (с учётом auto-fix retries).
- (Опционально) **Result match**: совпадение результата с эталонным SQL для части вопросов (gold SQL).

Для NL→SQL нужен **Ollama (локально)** и скачанная модель (например `qwen2.5:3b` / `qwen2.5:7b-instruct`).
"""
        ),
        md(
            """
## 0) Как запустить

1) Активируй conda-env проекта (`text2sql`)
2) Перейди в корень репозитория `text2sql_agent`
3) Проверь `.env` (PG_*, OLLAMA_*)
4) Открой этот ноутбук из `notebooks/` и сделай **Run All**

Методы проекта:
- Schema introspection: таблицы/колонки/PK/FK из Postgres → контекст для модели
- Guardrails (SELECT-only): блокируем DDL/DML, принудительно ставим LIMIT
- RU→EN→SQL: вопрос на русском → внутренний перевод → SQL
- Self-healing: при SQL-ошибке делаем auto-fix попытки (retries)
"""
        ),
        code(
            r"""
from __future__ import annotations

import os
import sys
import time
import subprocess
from pathlib import Path
from typing import Dict, Any, List

import pandas as pd
from dotenv import load_dotenv

# --- project root ---
PROJECT_ROOT = Path.cwd().parent if (Path.cwd().name == "notebooks") else Path.cwd()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# --- .env ---
dotenv_path = PROJECT_ROOT / ".env"
if dotenv_path.exists():
    load_dotenv(dotenv_path, override=True)
else:
    print("⚠️ .env не найден. Проверь PROJECT_ROOT:", PROJECT_ROOT)

PG_DB = os.getenv("PG_DB", "northwind")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
TRANSLATE_RU_EN = os.getenv("TRANSLATE_RU_EN", "true").lower() in ("1","true","yes","y")
DEFAULT_RETRIES = int(os.getenv("AUTO_FIX_RETRIES", "2"))

print("PROJECT_ROOT:", PROJECT_ROOT)
print("PG_DB:", PG_DB)
print("OLLAMA_URL:", OLLAMA_URL)
print("OLLAMA_MODEL:", OLLAMA_MODEL)
print("TRANSLATE_RU_EN:", TRANSLATE_RU_EN, "| DEFAULT_RETRIES:", DEFAULT_RETRIES)
"""
        ),
        md("## 1) Импорты из проекта + проверка подключения к БД"),
        code(
            r"""
from text2sql.db import execute_select
from text2sql.introspect import schema_as_text
from text2sql.agent import Text2SQLAgent

execute_select("SELECT COUNT(*) AS n_orders FROM orders", max_rows=10)
"""
        ),
        md("## 2) Проверка Ollama (опционально)"),
        code(
            r"""
def ollama_list_models() -> List[str]:
    try:
        out = subprocess.check_output(["ollama", "list"], text=True, encoding="utf-8", errors="ignore")
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        if len(lines) >= 2 and "NAME" in lines[0]:
            return [l.split()[0] for l in lines[1:] if l.split()]
    except Exception as e:
        print("Не смог выполнить `ollama list`:", e)
    return []

models = ollama_list_models()
models[:20], len(models)
"""
        ),
        md("## 3) Схема БД (снимок)"),
        code(
            r"""
schema_txt = schema_as_text()
print(schema_txt[:2500])
print("...")
print("len(schema_txt) =", len(schema_txt))
"""
        ),
        md(
            """
## 4) Eval-набор (вопросы)

Поля:
- id: идентификатор кейса
- question_ru: вопрос (RU)
- gold_sql: (опционально) эталонный SQL для сравнения результата
"""
        ),
        code(
            r"""
EVAL_SET: List[Dict[str, Any]] = [
    {
        "id": "q01_top_customers_orders",
        "question_ru": "Покажи топ-5 клиентов по количеству заказов",
        "gold_sql": '''
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
LIMIT 5
''',
    },
    {
        "id": "q02_top_products_qty",
        "question_ru": "Топ-10 товаров по суммарному количеству проданных единиц",
        "gold_sql": '''
SELECT p.product_name,
       SUM(od.quantity) AS total_units
FROM order_details od
JOIN products p ON p.product_id = od.product_id
GROUP BY p.product_name
ORDER BY total_units DESC
LIMIT 10
''',
    },
    {
        "id": "q03_top_products_revenue",
        "question_ru": "Топ-10 товаров по выручке",
        "gold_sql": '''
SELECT p.product_name,
       SUM(od.unit_price * od.quantity * (1 - od.discount)) AS revenue
FROM order_details od
JOIN products p ON p.product_id = od.product_id
GROUP BY p.product_name
ORDER BY revenue DESC
LIMIT 10
''',
    },
    {
        "id": "q04_rev_by_category",
        "question_ru": "Выручка по категориям (топ-5)",
        "gold_sql": '''
SELECT cat.category_name,
       SUM(od.unit_price * od.quantity * (1 - od.discount)) AS revenue
FROM order_details od
JOIN products p ON p.product_id = od.product_id
JOIN categories cat ON cat.category_id = p.category_id
GROUP BY cat.category_name
ORDER BY revenue DESC
LIMIT 5
''',
    },
    {
        "id": "q05_rev_by_country",
        "question_ru": "Топ-5 стран по выручке",
        "gold_sql": '''
SELECT c.country,
       SUM(od.unit_price * od.quantity * (1 - od.discount)) AS revenue
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
JOIN order_details od ON od.order_id = o.order_id
GROUP BY c.country
ORDER BY revenue DESC
LIMIT 5
''',
    },
    {
        "id": "q06_top_employees",
        "question_ru": "Какие сотрудники обработали больше всего заказов (топ-5)?",
        "gold_sql": '''
SELECT e.first_name || ' ' || e.last_name AS employee,
       COUNT(*) AS n_orders
FROM orders o
JOIN employees e ON e.employee_id = o.employee_id
GROUP BY employee
ORDER BY n_orders DESC
LIMIT 5
''',
    },
    {
        "id": "q07_orders_by_year",
        "question_ru": "Сколько заказов было по годам?",
        "gold_sql": '''
SELECT EXTRACT(YEAR FROM o.order_date)::int AS year,
       COUNT(*) AS n_orders
FROM orders o
WHERE o.order_date IS NOT NULL
GROUP BY year
ORDER BY year
''',
    },
]
len(EVAL_SET)
"""
        ),
        md("## 5) Result match (опционально, где есть gold_sql)"),
        code(
            r"""
def normalize_df(df: pd.DataFrame, float_round: int = 6, max_rows: int = 200) -> pd.DataFrame:
    out = df.copy().head(max_rows)
    for c in out.columns:
        if pd.api.types.is_float_dtype(out[c]):
            out[c] = out[c].round(float_round)
    out = out.reindex(sorted(out.columns), axis=1)
    try:
        out = out.sort_values(by=list(out.columns)).reset_index(drop=True)
    except Exception:
        out = out.reset_index(drop=True)
    return out

def df_signature(df: pd.DataFrame):
    n = normalize_df(df)
    cols = tuple(n.columns.tolist())
    rows = tuple(tuple(x) for x in n.itertuples(index=False, name=None))
    return cols, rows

def compare_results(df_pred: pd.DataFrame, df_gold: pd.DataFrame) -> bool:
    return df_signature(df_pred) == df_signature(df_gold)
"""
        ),
        md("## 6) Прогон eval"),
        code(
            r"""
def run_one(agent: Text2SQLAgent, question_ru: str, max_rows: int = 200, retries: int = 2, translate_ru_en: bool = True):
    t0 = time.time()
    out = {"question_ru": question_ru, "ok": False, "sql": None, "rows": None, "error": None, "latency_s": None, "retries": retries}
    try:
        res = agent.answer(question_ru, max_rows=max_rows, translate_to_en=translate_ru_en, max_retries=retries)
        out["ok"] = True
        out["sql"] = res.sql
        out["rows"] = int(len(res.df)) if hasattr(res, "df") else None
    except Exception as e:
        out["error"] = str(e)
    out["latency_s"] = round(time.time() - t0, 3)
    return out

agent = Text2SQLAgent(model=OLLAMA_MODEL, ollama_url=OLLAMA_URL)

results = []
MAX_ROWS = 200
RETRIES = DEFAULT_RETRIES

for item in EVAL_SET:
    r = run_one(agent, item["question_ru"], max_rows=MAX_ROWS, retries=RETRIES, translate_ru_en=TRANSLATE_RU_EN)
    r["id"] = item["id"]
    results.append(r)

df_res = pd.DataFrame(results)
df_res[["id","ok","latency_s","rows","error","sql"]]
"""
        ),
        md("## 7) Метрики"),
        code(
            r"""
total = len(df_res)
ok = int(df_res["ok"].sum())
print(f"Total: {total}")
print(f"Execution success: {ok}/{total} = {ok/total:.2%}" if total else "No data")
df_res.loc[~df_res["ok"], ["id","error"]].head(50)
"""
        ),
        md(
            """
## 8) Итог для собеса (что говорить)

- “Я делаю безопасный text-to-SQL: схема из БД → prompt → SQL → guardrails → выполнение → auto-fix по ошибке → метрики”.
- Показать execution success rate на небольшом eval-наборе, и сравнить 3B vs 7B (если успеешь).
"""
        ),
    ]

    root = Path(__file__).resolve().parents[1]  # repo root (../scripts)
    out_dir = root / "notebooks"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "eval_text2sql_northwind.ipynb"
    nbf.write(nb, str(out_path))
    print("OK ->", out_path)


if __name__ == "__main__":
    main()

