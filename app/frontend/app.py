"""Streamlit entry – loads the full app body (Phase 4.3)."""
from pathlib import Path
import runpy
_body = Path(__file__).resolve().parent / "app_body.py"
runpy.run_path(str(_body), run_name="__main__")
