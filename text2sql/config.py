from __future__ import annotations
from pathlib import Path
from dotenv import load_dotenv

def load_env() -> None:
    root = Path(__file__).resolve().parents[1]
    env_path = root / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=True)
