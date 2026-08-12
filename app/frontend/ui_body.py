"""Reconstruct Streamlit UI body from base64 halves (Phase 4.5 packaging)."""
from __future__ import annotations

import base64
from pathlib import Path
import runpy

_here = Path(__file__).resolve().parent


def _load_b64(name: str) -> str:
    p = _here / name
    ns: dict = {}
    exec(p.read_text(encoding="utf-8"), ns)
    return str(ns["B64"])


src = base64.b64decode(_load_b64("_ui_b1.py") + _load_b64("_ui_b2.py")).decode("utf-8")
target = _here / "_ui_body_runtime.py"
target.write_text(src, encoding="utf-8")
runpy.run_path(str(target), run_name="__main__")
