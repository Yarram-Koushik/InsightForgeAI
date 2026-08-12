"""InsightForgeAI Streamlit UI – Phase 4.5 (Enterprise multi-user & scheduling).

This entry restores Phase 4.4 UI and wires Phase 4.5 Admin via admin_panel.
Full UI body is loaded from ui_body.py to keep this file maintainable.
"""
from __future__ import annotations

import runpy
from pathlib import Path

_BODY = Path(__file__).resolve().parent / "ui_body.py"
if _BODY.exists():
    runpy.run_path(str(_BODY), run_name="__main__")
else:
    import streamlit as st
    st.set_page_config(page_title="InsightForgeAI", page_icon="📊", layout="wide")
    st.error(
        "ui_body.py missing. Restore app/frontend/ui_body.py from git history "
        "(commit 29a29a0 Phase 4.4 UI) or re-pull Phase 4.5 artifacts."
    )
