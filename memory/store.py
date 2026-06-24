from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def write_json(name: str, payload: Any) -> Path:
    ensure_data_dir()
    path = DATA_DIR / name
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    return path


def read_json(name: str, default: Any) -> Any:
    ensure_data_dir()
    path = DATA_DIR / name
    if not path.exists():
        return default
    return json.loads(path.read_text())
