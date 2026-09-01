import sys
from pathlib import Path
# Make project root importable when running scripts directly
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from text2sql.db import execute_select

if __name__ == "__main__":
    df = execute_select("SELECT * FROM orders LIMIT 5")
    print(df.head())
    print("rows:", len(df))
