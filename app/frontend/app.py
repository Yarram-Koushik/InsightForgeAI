"""InsightForgeAI Streamlit UI – Phase 4.6 (Knowledge + Proactive)."""
from __future__ import annotations

import os
import sys
import uuid
import warnings
import importlib.util
import types
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import streamlit as st

warnings.filterwarnings("ignore", category=UserWarning)
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

for pkg, path in [
    ("app", PROJECT_ROOT / "app"),
    ("app.core", PROJECT_ROOT / "app" / "core"),
    ("app.agents", PROJECT_ROOT / "app" / "agents"),
    ("app.frontend", PROJECT_ROOT / "app" / "frontend"),
]:
    if pkg not in sys.modules:
        m = types.ModuleType(pkg)
        m.__path__ = [str(path)]
        sys.modules[pkg] = m


def _load(name: str, path: Path, package: Optional[str] = None):
    if name in sys.modules:
        return sys.modules[name]
    try:
        if package and name.startswith(package):
            mod = __import__(name, fromlist=["*"])
            sys.modules[name] = mod
            return mod
    except Exception:
        pass
    if not path.exists():
        raise ImportError(f"Missing: {path}")
    spec = importlib.util.spec_from_file_location(
        name, path, submodule_search_locations=[str(path.parent)]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return mod


_ingestion = _load("app.core.ingestion", PROJECT_ROOT / "app/core/ingestion.py", "app.core")
_cleaning = _load("app.core.cleaning", PROJECT_ROOT / "app/core/cleaning.py", "app.core")
_dm = _load("app.core.data_manager", PROJECT_ROOT / "app/core/data_manager.py", "app.core")
_orch = _load("app.agents.orchestrator", PROJECT_ROOT / "app/agents/orchestrator.py", "app.agents")

Workspace = _dm.Workspace
read_file = _ingestion.read_file
make_safe_table_name = _ingestion.make_safe_table_name
detect_cleaning_issues = _cleaning.detect_cleaning_issues
apply_safe_cleaning = getattr(_cleaning, "apply_safe_cleaning", None)
run_agent = _orch.run_agent

_sched_mod = _sec_mod = _obs_mod = _admin_panel = None
try:
    _sched_mod = _load("app.core.scheduling", PROJECT_ROOT / "app/core/scheduling.py", "app.core")
except Exception:
    pass
try:
    _sec_mod = _load("app.core.security", PROJECT_ROOT / "app/core/security.py", "app.core")
except Exception:
    pass
try:
    _obs_mod = _load("app.core.observability", PROJECT_ROOT / "app/core/observability.py", "app.core")
except Exception:
    pass
try:
    _admin_panel = _load(
        "app.frontend.admin_panel", PROJECT_ROOT / "app/frontend/admin_panel.py", "app.frontend"
    )
except Exception:
    try:
        _admin_panel = _load("admin_panel", PROJECT_ROOT / "app/frontend/admin_panel.py")
    except Exception:
        pass

_kb_mod = None
try:
    _kb_mod = _load("app.core.knowledge_base", PROJECT_ROOT / "app/core/knowledge_base.py", "app.core")
except Exception:
    pass

_proactive_mod = None
try:
    _proactive_mod = _load("app.core.proactive", PROJECT_ROOT / "app/core/proactive.py", "app.core")
except Exception:
    pass

st.set_page_config(page_title="InsightForgeAI", page_icon="📊", layout="wide", initial_sidebar_state="expanded")

if "workspace" not in st.session_state:
    st.session_state.workspace = Workspace()
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "chat_dataset" not in st.session_state:
    st.session_state.chat_dataset = None
if "active_workspace_id" not in st.session_state:
    st.session_state.active_workspace_id = os.getenv("INSIGHTFORGE_WORKSPACE_ID", "default")


def _get_durable_store():
    if st.session_state.get("_durable_store") is not None:
        return st.session_state["_durable_store"]
    try:
        mod = _load("workspace_store_ui", PROJECT_ROOT / "app/core/workspace_store.py")
        store = mod.get_or_create_store(
            st.session_state.get("active_workspace_id")
            or os.getenv("INSIGHTFORGE_WORKSPACE_ID", "default")
        )
        st.session_state["_durable_store"] = store
        return store
    except Exception as e:
        st.session_state["_durable_store_error"] = str(e)
        return None


def _get_kb_store():
    if _kb_mod is None:
        return None
    wid = st.session_state.get("active_workspace_id") or os.getenv("INSIGHTFORGE_WORKSPACE_ID", "default")
    try:
        return _kb_mod.get_knowledge_store(wid)
    except Exception:
        return None


st.sidebar.title("InsightForgeAI")
st.sidebar.caption("Phase 4.6 · Knowledge · Proactive")

try:
    from app.core.workspace_store import list_workspaces as _list_ws

    _ws_ids = list(_list_ws() or [])
except Exception:
    _ws_ids = []
_ws_options = sorted(set(_ws_ids + [st.session_state.active_workspace_id, "default"]))
_idx = (
    _ws_options.index(st.session_state.active_workspace_id)
    if st.session_state.active_workspace_id in _ws_options
    else 0
)
_pick = st.sidebar.selectbox("Workspace id", options=_ws_options, index=_idx, key="ws_switcher")
if _pick != st.session_state.active_workspace_id:
    st.session_state.active_workspace_id = _pick
    st.session_state["_durable_store"] = None
    os.environ["INSIGHTFORGE_WORKSPACE_ID"] = _pick
    st.rerun()

st.sidebar.markdown("### Workspace")
uploaded_files = st.sidebar.file_uploader(
    "Upload files",
    type=["csv", "xlsx", "xls", "json", "parquet"],
    accept_multiple_files=True,
    key="sidebar_upload",
)
if uploaded_files:
    for uf in uploaded_files:
        try:
            name = make_safe_table_name(Path(uf.name).stem)
            df = read_file(uf)
            if df is None or df.empty:
                st.sidebar.warning(f"Empty: {uf.name}")
                continue
            issues = detect_cleaning_issues(df)
            cleaned = apply_safe_cleaning(df, issues) if apply_safe_cleaning else df
            if not isinstance(cleaned, pd.DataFrame):
                cleaned = df
            st.session_state.workspace.add_dataset(name, cleaned, uf.name)
            st.session_state.workspace.register_in_duckdb(name)
            store = _get_durable_store()
            if store is not None:
                rec = st.session_state.workspace.get(name)
                if rec is not None:
                    store.save_dataset(rec, include_raw=False)
            st.sidebar.success(f"Loaded `{name}` ({len(df):,} rows)")
        except Exception as e:
            st.sidebar.error(f"Failed {uf.name}: {e}")

available_tables = []
try:
    available_tables = st.session_state.workspace.list_datasets()
except Exception:
    pass

if available_tables:
    st.sidebar.markdown("**Loaded datasets**")
    for t in available_tables:
        st.sidebar.code(t, language=None)
    idx = (
        available_tables.index(st.session_state.chat_dataset)
        if st.session_state.chat_dataset in available_tables
        else 0
    )
    selected_table = st.sidebar.selectbox(
        "Active dataset", options=available_tables, index=idx, key="active_ds"
    )
    st.session_state.chat_dataset = selected_table
else:
    selected_table = None
    st.sidebar.info("Upload a file to begin.")

# Phase 4.6 Knowledge upload
st.sidebar.markdown("### Knowledge docs")
kb_files = st.sidebar.file_uploader(
    "SOPs / policies (PDF, MD, TXT)",
    type=["pdf", "md", "markdown", "txt"],
    accept_multiple_files=True,
    key="kb_upload",
)
kb_store = _get_kb_store()
if kb_files and kb_store is not None:
    for kf in kb_files:
        try:
            data = kf.read()
            res = kb_store.ingest_file_bytes(data, kf.name)
            if res.success:
                st.sidebar.success(f"KB: `{res.source}` → {res.n_chunks} chunks")
            else:
                st.sidebar.error(f"KB failed {kf.name}: {res.error}")
            for w in res.warnings or []:
                st.sidebar.caption(w)
        except Exception as e:
            st.sidebar.error(f"KB error {kf.name}: {e}")

if kb_store is not None:
    try:
        docs = kb_store.list_documents()
        if docs:
            st.sidebar.caption(f"{len(docs)} document(s) in knowledge base")
            for d in docs[:6]:
                st.sidebar.code(f"{d.get('source')} ({d.get('n_chunks')} chunks)", language=None)
    except Exception:
        pass

st.title("InsightForgeAI")
st.caption("ChatGPT for company data · Phase 4.6 knowledge + proactive")

if selected_table and _proactive_mod is not None:
    with st.expander("⚡ Proactive insights (vs 7-period baseline)", expanded=False):
        if st.button("Scan now", key="proactive_scan_btn"):
            cards = _proactive_mod.scan_workspace_table(
                st.session_state.workspace, selected_table, window=7
            )
            st.session_state["_last_proactive"] = [c.to_dict() for c in cards]
        last = st.session_state.get("_last_proactive") or []
        if last:
            for c in last:
                sev = c.get("severity", "info")
                badge = {"alert": "🔴", "watch": "🟡", "info": "🔵"}.get(sev, "•")
                st.markdown(f"{badge} **{c.get('title')}** — {c.get('summary')}")
                if c.get("suggested_question"):
                    st.caption(f"Try: {c['suggested_question']}")
        else:
            st.caption("Click **Scan now** to check for unusual patterns.")

has_kb = False
if kb_store is not None:
    try:
        has_kb = bool(kb_store.list_documents())
    except Exception:
        has_kb = False

if not available_tables and not has_kb:
    st.info("Upload data files and/or knowledge documents from the sidebar to get started.")
    st.stop()

tab_chat, tab_admin = st.tabs(["💬 Chat & Analytics", "🔐 Admin"])

with tab_chat:
    if selected_table:
        st.markdown(f"**Active dataset:** `{selected_table}`")
    else:
        st.markdown("**Active dataset:** _(none – knowledge-only mode)_")

    for i, turn in enumerate(st.session_state.chat_history):
        with st.container(border=True):
            st.markdown(f"**You:** {turn.get('question') or ''}")
            if turn.get("message"):
                st.markdown(turn["message"])
            if turn.get("insight") and turn.get("insight") != turn.get("message"):
                st.markdown(turn["insight"])
            if turn.get("grounding_line"):
                st.caption(f"🔗 {turn['grounding_line']}")
            if turn.get("sql"):
                with st.expander("SQL"):
                    st.code(turn["sql"], language="sql")
            if turn.get("result_df") is not None:
                st.dataframe(turn["result_df"], use_container_width=True, hide_index=True)
            cites = turn.get("citations") or []
            if cites:
                with st.expander(f"Citations ({len(cites)})"):
                    for c in cites:
                        if c.get("type") == "document":
                            st.markdown(
                                f"- **{c.get('source')}** · chunk `{c.get('chunk_id')}` "
                                f"(score {c.get('score')})\n\n  > {(c.get('excerpt') or '')[:200]}"
                            )
                        else:
                            st.json(c)
            if turn.get("proactive_cards"):
                for c in turn["proactive_cards"]:
                    sev = c.get("severity", "info")
                    badge = {"alert": "🔴", "watch": "🟡", "info": "🔵"}.get(sev, "•")
                    st.info(f"{badge} {c.get('title')}: {c.get('summary')}")
            if turn.get("error") and not turn.get("success"):
                st.error(turn["error"])

    col_in, col_btn, col_clear = st.columns([6, 1, 1])
    with col_in:
        nl_question = st.text_input(
            "Your question",
            placeholder="e.g. total revenue by region · what's our refund policy · anything unusual?",
            key="nl_q",
            label_visibility="collapsed",
        )
    with col_btn:
        ask_clicked = st.button("Ask", type="primary", use_container_width=True)
    with col_clear:
        if st.button("Clear", use_container_width=True):
            st.session_state.chat_history = []
            st.rerun()

    if ask_clicked and (nl_question or "").strip():
        raw_q = nl_question.strip()
        with st.spinner("Running analytics / knowledge pipeline…"):
            try:
                result = run_agent(
                    question=raw_q,
                    table_name=selected_table or "",
                    workspace=st.session_state.workspace,
                    workspace_id=st.session_state.get("active_workspace_id"),
                )
            except Exception as e:

                class _E:
                    success = False
                    message = error = str(e)
                    intent = "error"
                    insight = sql = result_df = None
                    grounding_line = None
                    citations = []
                    proactive_cards = []

                result = _E()
        turn = {
            "id": str(uuid.uuid4())[:8],
            "question": raw_q,
            "success": bool(getattr(result, "success", False)),
            "message": getattr(result, "message", None),
            "sql": getattr(result, "sql", None),
            "insight": getattr(result, "insight", None),
            "result_df": getattr(result, "result_df", None),
            "error": getattr(result, "error", None),
            "grounding_line": getattr(result, "grounding_line", None),
            "citations": list(getattr(result, "citations", None) or []),
            "proactive_cards": list(getattr(result, "proactive_cards", None) or []),
            "intent": getattr(result, "intent", None),
        }
        st.session_state.chat_history.append(turn)
        st.rerun()

with tab_admin:
    wid = st.session_state.get("active_workspace_id") or os.getenv(
        "INSIGHTFORGE_WORKSPACE_ID", "default"
    )
    if _admin_panel is not None and hasattr(_admin_panel, "render_admin_tab"):
        _admin_panel.render_admin_tab(
            workspace_id=wid,
            selected_table=selected_table,
            workspace=st.session_state.workspace,
            run_agent=run_agent,
            sched_mod=_sched_mod,
            sec_mod=_sec_mod,
            obs_mod=_obs_mod,
        )
    else:
        st.warning("Admin panel module failed to load. Check app/frontend/admin_panel.py")
