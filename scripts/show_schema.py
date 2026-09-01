import sys
from pathlib import Path
# Make project root importable when running scripts directly
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from text2sql.introspect import schema_as_text

if __name__ == "__main__":
    print(schema_as_text())
