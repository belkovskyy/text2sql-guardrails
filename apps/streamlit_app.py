import os

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

from text2sql.agent import Text2SQLAgent
from text2sql.guardrails import validate_and_sanitize_select
from text2sql.db import execute_select
from text2sql.introspect import schema_as_text
from text2sql.docgen import generate_documentation_markdown

load_dotenv()


def _ollama_list_models(ollama_url: str):
    """Return list of local Ollama model names via /api/tags. Empty list on error."""
    url = ollama_url.rstrip("/") + "/api/tags"
    try:
        r = requests.get(url, timeout=5)
        if r.status_code != 200:
            return []
        data = r.json() or {}
        models = data.get("models") or []
        names = []
        for m in models:
            name = m.get("name")
            if name:
                names.append(name)
        return sorted(set(names))
    except Exception:
        return []


@st.cache_data(show_spinner=False, ttl=300)
def _get_schema_text() -> str:
    """Cached schema text from Postgres introspection."""
    return schema_as_text()


def _search_schema_local(query: str, schema_text: str, context_lines: int = 1, max_hits: int = 50):
    """
    Simple keyword search over schema text.
    Returns snippets (a few lines around each hit).
    """
    q = (query or "").strip().lower()
    if not q:
        return []

    lines = schema_text.splitlines()
    hits = []
    seen = set()

    for i, line in enumerate(lines):
        if q in line.lower():
            start = max(0, i - context_lines)
            end = min(len(lines), i + context_lines + 1)
            snippet = "\n".join(lines[start:end]).strip()
            if snippet and snippet not in seen:
                seen.add(snippet)
                hits.append(snippet)
            if len(hits) >= max_hits:
                break

    return hits


st.set_page_config(page_title="Text-to-SQL Agent (Northwind / Postgres)", layout="wide")
st.title("Text-to-SQL Agent (Northwind / Postgres)")

# ---------------- Sidebar ----------------
mode = st.sidebar.radio(
    "Режим",
    ["NL → SQL → результат", "SQL runner (отладка)", "Схема БД (поиск)", "Документация БД"],
)

max_rows = st.sidebar.number_input("max rows", min_value=10, max_value=5000, value=200, step=10)
max_retries = st.sidebar.number_input("auto-fix retries", min_value=0, max_value=5, value=2, step=1)

st.sidebar.markdown("---")
st.sidebar.subheader("Ollama")

ollama_url = st.sidebar.text_input(
    "OLLAMA_URL",
    value=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"),
)

# Try to list models for dropdown. If Ollama is down, fallback to env text input.
models = []
try:
    models = _ollama_list_models(ollama_url)
except Exception:
    models = []

default_model = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
if models:
    ollama_model = st.sidebar.selectbox(
        "OLLAMA_MODEL",
        options=models,
        index=models.index(default_model) if default_model in models else 0,
    )
else:
    ollama_model = st.sidebar.text_input("OLLAMA_MODEL", value=default_model)

translate_to_en = st.sidebar.checkbox(
    "RU → EN (рекомендуется)",
    value=(os.getenv("TRANSLATE_RU_EN", "true").lower() == "true"),
)

st.sidebar.caption("Если NL→SQL не работает: проверь, что Ollama запущена и модель скачана (ollama list).")

agent = Text2SQLAgent(model=ollama_model, ollama_url=ollama_url)

# ---------------- Mode: NL -> SQL -> Result ----------------
if mode == "NL → SQL → результат":
    st.header("Текстовый запрос → SQL → выполнение")

    quick_queries = [
        "Покажи топ-5 клиентов по количеству заказов",
        "Топ-10 товаров по суммарному количеству проданных единиц",
        "Выручка по категориям (топ-5)",
        "Выручка по странам (топ-5)",
        "Какие сотрудники обработали больше всего заказов (топ-5)?",
        "Средний чек по клиентам (топ-5)",
        "Количество заказов по годам",
    ]

    preset = st.selectbox("Быстрые запросы", options=["(выбери)"] + quick_queries, index=0)
    default_q = "" if preset == "(выбери)" else preset

    q = st.text_area("Вопрос (RU)", value=default_q, height=90, placeholder="Например: Выручка по категориям (топ-5)")

    if st.button("Generate & Run"):
        if not q.strip():
            st.warning("Введи вопрос.")
        else:
            try:
                out = agent.answer(
                    q,
                    max_rows=int(max_rows),
                    max_retries=int(max_retries),
                    translate_to_en=translate_to_en,
                )
                st.subheader("SQL")
                st.code(out.sql, language="sql")

                if out.warning:
                    st.info(out.warning)

                st.subheader("Результат")
                if out.df is None or (hasattr(out.df, "empty") and out.df.empty):
                    st.warning("Запрос выполнился, но вернул пустой результат. Попробуй другой вопрос или модель/reties.")
                else:
                    st.dataframe(out.df, use_container_width=True)

            except Exception as e:
                st.error(str(e))
                st.exception(e)

# ---------------- Mode: SQL runner ----------------
elif mode == "SQL runner (отладка)":
    st.header("SQL runner (только SELECT/WITH)")

    with st.expander("Схема (интроспекция из Postgres)", expanded=False):
        st.code(_get_schema_text(), language="text")

    sql = st.text_area("SQL", value="SELECT * FROM customers LIMIT 5", height=140)
    if st.button("Run SQL"):
        try:
            gr = validate_and_sanitize_select(sql, max_rows=int(max_rows))
            if not gr.ok:
                st.error(f"SQL blocked by guardrails: {gr.reason}")
            else:
                st.caption(f"sanitized: `{gr.sanitized_sql}`")
                df = execute_select(gr.sanitized_sql, max_rows=int(max_rows))
                st.dataframe(df, use_container_width=True)
        except Exception as e:
            st.error(str(e))
            st.exception(e)

# ---------------- Mode: Schema search ----------------
elif mode == "Схема БД (поиск)":
    st.header("Схема БД (поиск)")
    schema_text = _get_schema_text()

    term = st.text_input("Поиск (таблица/колонка)", value="customer")
    if st.button("Искать"):
        hits = _search_schema_local(term, schema_text, context_lines=1, max_hits=50)
        if not hits:
            st.info("Ничего не найдено.")
        else:
            for h in hits:
                st.code(h, language="text")

    st.divider()
    st.caption("Полная схема:")
    st.code(schema_text, language="text")

# ---------------- Mode: Documentation ----------------
else:  # Документация БД
    st.header("Документация БД (автогенерация)")
    st.write("Генерируем Markdown-описание таблиц, колонок и связей из PostgreSQL (schema public).")

    colA, colB, colC = st.columns(3)
    include_counts = colA.checkbox("Включить количество строк", value=True)
    include_samples = colB.checkbox("Включить примеры строк", value=False)
    sample_rows = int(colC.number_input("Примеров строк", min_value=1, max_value=10, value=3, step=1))

    if st.button("Сгенерировать документацию"):
        try:
            with st.spinner("Генерирую..."):
                md = generate_documentation_markdown(
                    schema="public",
                    include_counts=include_counts,
                    include_samples=include_samples,
                    sample_rows=sample_rows,
                )
            st.success("Готово.")
            st.download_button(
                "Скачать Markdown (.md)",
                data=md.encode("utf-8"),
                file_name="db_documentation.md",
                mime="text/markdown",
            )
            st.markdown(md)
        except Exception as e:
            st.error(str(e))
            st.exception(e)

st.caption("MVP: schema introspection + SELECT-only guardrails + Ollama text-to-SQL + schema search + docs generator + self-healing.")
