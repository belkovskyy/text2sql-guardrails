import json
from pathlib import Path
from text2sql.agent import Text2SQLAgent

DATA = Path(__file__).resolve().parent / "northwind_ru.jsonl"

def main(limit: int = 999, retries: int = 2):
    agent = Text2SQLAgent()
    ok = 0
    total = 0
    for line in DATA.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        total += 1
        if total > limit:
            break
        q = row["question_ru"]
        try:
            out = agent.answer(q, max_rows=200, max_retries=retries)
            ok += 1
            print(f"[OK] {row['id']}: attempts={out.attempts} sql={out.sql.splitlines()[0][:90]}...")
        except Exception as e:
            print(f"[FAIL] {row['id']}: {q}\n  {e}")
    print(f"success_rate: {ok}/{total} = {ok/total if total else 0:.3f}")

if __name__ == "__main__":
    main()
