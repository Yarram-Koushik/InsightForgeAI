"""Streamlit entry – Phase 4.3 full UI."""
from pathlib import Path
_p = Path(__file__).resolve().parent
_code = (_p / "app_body_part1.py").read_text(encoding="utf-8") + (_p / "app_body_part2.py").read_text(encoding="utf-8")
exec(compile(_code, str(_p / "app_body.py"), "exec"), {"__name__": "__main__", "__file__": str(_p / "app.py")})
