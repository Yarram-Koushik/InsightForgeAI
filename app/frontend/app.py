"""InsightForgeAI Streamlit UI – Phase 4.3 (industry)."""
from __future__ import annotations
import os, sys, uuid, warnings, importlib.util, types
from pathlib import Path
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
import streamlit as st

warnings.filterwarnings("ignore", category=UserWarning)
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

for pkg, path in [("app", PROJECT_ROOT / "app"), ("app.core", PROJECT_ROOT / "app" / "core"), ("app.agents", PROJECT_ROOT / "app" / "agents")]:
    if pkg not in sys.modules:
        m = types.ModuleType(pkg)
        m.__path__ = [str(path)]
        sys.modules[pkg] = m

def _load(name: str, path: Path, package: str | None = None):
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
    spec = importlib.util.spec_from_file_location(name, path, submodule_search_locations=[str(path.parent)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return mod

_ingestion = _load("app.core.ingestion", PROJECT_ROOT / "app/core/ingestion.py", "app.core")
_schema = _load("app.core.schema", PROJECT_ROOT / "app/core/schema.py", "app.core")
_cleaning = _load("app.core.cleaning", PROJECT_ROOT / "app/core/cleaning.py", "app.core")
_dm = _load("app.core.data_manager", PROJECT_ROOT / "app/core/data_manager.py", "app.core")
_profiling = _load("app.core.profiling", PROJECT_ROOT / "app/core/profiling.py", "app.core")
_export = _load("app.core.export", PROJECT_ROOT / "app/core/export.py", "app.core")
_orch = _load("app.agents.orchestrator", PROJECT_ROOT / "app/agents/orchestrator.py", "app.agents")

Workspace = _dm.Workspace
DatasetRecord = getattr(_dm, "DatasetRecord", None)
read_file = _ingestion.read_file
make_safe_table_name = _ingestion.make_safe_table_name
detect_schema_semantic = _schema.detect_schema_semantic
detect_cleaning_issues = _cleaning.detect_cleaning_issues
apply_safe_cleaning = getattr(_cleaning, "apply_safe_cleaning", None)
generate_quality_report = _profiling.generate_quality_report
column_level_profile = _profiling.column_level_profile
run_agent = _orch.run_agent
build_evidence_pack = getattr(_export, "build_evidence_pack", None)

_gov_mod = _sl_mod = _gov_err = None
try:
    _sl_mod = _load("app.core.semantic_layer", PROJECT_ROOT / "app/core/semantic_layer.py", "app.core")
    _gov_mod = _load("app.core.metric_governance", PROJECT_ROOT / "app/core/metric_governance.py", "app.core")
except Exception as e:
    _gov_err = str(e)

_conn_helpers = None
try:
    _conn_helpers = _load("app.core.connectors.ui_helpers", PROJECT_ROOT / "app/core/connectors/ui_helpers.py", "app.core")
except Exception:
    pass

_ctx_mod = None
try:
    _ctx_mod = _load("app.core.context_memory", PROJECT_ROOT / "app/core/context_memory.py", "app.core")
except Exception:
    pass

st.set_page_config(page_title="InsightForgeAI", page_icon="📊", layout="wide", initial_sidebar_state="expanded")

if "workspace" not in st.session_state:
    st.session_state.workspace = Workspace()
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "chat_dataset" not in st.session_state:
    st.session_state.chat_dataset = None
if "_ws_restored" not in st.session_state:
    st.session_state._ws_restored = False
if "connector_tables" not in st.session_state:
    st.session_state.connector_tables = []
if "connector_config" not in st.session_state:
    st.session_state.connector_config = None

def _get_durable_store():
    if st.session_state.get("_durable_store") is not None:
        return st.session_state["_durable_store"]
    try:
        ws_path = PROJECT_ROOT / "app/core/workspace_store.py"
        if not ws_path.exists():
            return None
        mod = _load("workspace_store_ui", ws_path)
        store = mod.get_or_create_store(os.getenv("INSIGHTFORGE_WORKSPACE_ID", "default"))
        st.session_state["_durable_store"] = store
        return store
    except Exception as e:
        st.session_state["_durable_store_error"] = str(e)
        return None

def _persist_dataset(name: str) -> None:
    try:
        store = _get_durable_store()
        if store is None:
            return
        rec = st.session_state.workspace.get(name)
        if rec is not None:
            store.save_dataset(rec, include_raw=False)
    except Exception:
        pass

def _persist_chat_turn(turn: dict, table_name: str) -> None:
    try:
        store = _get_durable_store()
        if store is None:
            return
        store.append_chat_turn({
            "id": turn.get("id"), "question": turn.get("question"), "success": turn.get("success"),
            "intent": turn.get("intent"), "intent_reason": turn.get("intent_reason"),
            "message": turn.get("message"), "sql": turn.get("sql"), "insight": turn.get("insight"),
            "clarify_questions": turn.get("clarify_questions") or [], "warnings": turn.get("warnings") or [],
            "error": turn.get("error"), "provider": turn.get("provider"), "model": turn.get("model"),
            "steps": turn.get("steps") or [], "grounding_line": turn.get("grounding_line"),
            "citations": turn.get("citations") or [], "table_name": table_name,
        })
    except Exception:
        pass

if not st.session_state.get("_ws_restored"):
    store = _get_durable_store()
    if store is not None:
        try:
            info = store.load_into(st.session_state.workspace)
            if (info or {}).get("restored"):
                st.session_state["_restored_names"] = list(info["restored"])
            turns = store.load_chat_history(limit=20)
            if turns and not st.session_state.chat_history:
                light = []
                for t in turns:
                    d = t.to_dict() if hasattr(t, "to_dict") else dict(t)
                    light.append({
                        "id": d.get("id"), "question": d.get("question"), "success": d.get("success", True),
                        "intent": d.get("intent"), "message": d.get("message"), "sql": d.get("sql"),
                        "insight": d.get("insight"), "clarify_questions": d.get("clarify_questions") or [],
                        "result_df": None, "forecast_df": None, "anomalies": [], "chart_fig": None,
                        "chart_type": None, "chart_reason": None, "extra_charts": [],
                        "steps": d.get("steps") or [], "warnings": d.get("warnings") or [],
                        "error": d.get("error"), "grounding_line": d.get("grounding_line"),
                        "citations": d.get("citations") or [], "_restored": True,
                    })
                    if d.get("table_name") and not st.session_state.chat_dataset:
                        st.session_state.chat_dataset = d.get("table_name")
                st.session_state.chat_history = light
        except Exception as e:
            st.session_state["_durable_restore_error"] = str(e)
    st.session_state["_ws_restored"] = True

st.sidebar.title("InsightForgeAI")
st.sidebar.caption("Phase 4.3 · Automated Analytics Depth")
st.sidebar.markdown("### Workspace")
uploaded_files = st.sidebar.file_uploader("Upload files", type=["csv", "xlsx", "xls", "json", "parquet"], accept_multiple_files=True, key="sidebar_upload")

if uploaded_files:
    for uf in uploaded_files:
        try:
            name = make_safe_table_name(Path(uf.name).stem)
            df = read_file(uf)
            if df is None or df.empty:
                st.sidebar.warning(f"Empty: {uf.name}")
                continue
            schema = detect_schema_semantic(df)
            issues = detect_cleaning_issues(df)
            cleaned = apply_safe_cleaning(df, issues) if apply_safe_cleaning else df
            if DatasetRecord is not None:
                rec = DatasetRecord(name=name, source_file=uf.name, raw_df=df,
                    cleaned_df=cleaned if isinstance(cleaned, pd.DataFrame) else df,
                    schema_info=schema, cleaning_log=issues)
                st.session_state.workspace.add_record(rec)
            else:
                st.session_state.workspace.add_dataframe(name, cleaned if isinstance(cleaned, pd.DataFrame) else df)
            _persist_dataset(name)
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
    idx = available_tables.index(st.session_state.chat_dataset) if st.session_state.chat_dataset in available_tables else 0
    selected_table = st.sidebar.selectbox("Active dataset", options=available_tables, index=idx, key="active_ds")
    st.session_state.chat_dataset = selected_table
else:
    selected_table = None
    st.sidebar.info("Upload a file or connect a database to begin.")

st.sidebar.markdown("---")
st.sidebar.markdown("### Database connectors")
if _conn_helpers is None:
    st.sidebar.caption("Connectors not available.")
else:
    with st.sidebar.expander("Postgres / MySQL", expanded=False):
        dialects = list(getattr(_conn_helpers, "SUPPORTED_DIALECTS", ["postgres", "mysql"]))
        dialect = st.selectbox("Dialect", options=dialects, key="conn_dialect")
        cname = st.text_input("Name", value="prod", key="conn_name")
        host = st.text_input("Host", value="localhost", key="conn_host")
        port = st.number_input("Port", value=5432 if str(dialect).startswith("post") else 3306, key="conn_port")
        database = st.text_input("Database", key="conn_db")
        user = st.text_input("User", key="conn_user")
        password = st.text_input("Password", type="password", key="conn_pass")
        schema = st.text_input("Schema (optional)", key="conn_schema")
        if st.button("Test & list tables", key="conn_test"):
            cfg = _conn_helpers.build_config_from_form(name=cname, dialect=dialect, host=host, port=int(port),
                database=database, user=user, password=password, schema=schema or None)
            ok, msg, tables = _conn_helpers.test_and_list(cfg)
            if ok:
                st.success(msg)
                st.session_state.connector_tables = tables
                st.session_state.connector_config = cfg
            else:
                st.error(msg)
        if st.session_state.connector_tables:
            names = [t.get("name") or t.get("table") for t in st.session_state.connector_tables]
            pick = st.multiselect("Select tables", options=names, key="conn_pick")
            if st.button("Load selected", key="conn_load") and pick:
                items = [t for t in st.session_state.connector_tables if (t.get("name") or t.get("table")) in pick]
                for r in _conn_helpers.load_selected_tables(st.session_state.workspace, st.session_state.connector_config, items, limit=100000, run_cleaning=True):
                    if r.get("ok"):
                        st.success(f"Loaded `{r.get('dataset_name')}`")
                        _persist_dataset(r.get("dataset_name"))
                    else:
                        st.error(f"{r.get('table')}: {r.get('error')}")
                st.rerun()

st.title("InsightForgeAI")
st.caption("ChatGPT for company data · free stack · industry-grade analytics")

if not available_tables:
    st.info("Upload files from the sidebar or connect a database to get started.")
    st.stop()

tab_chat, tab_eda, tab_quality, tab_sql, tab_gov, tab_schema = st.tabs(
    ["💬 Chat & Analytics", "🔍 EDA Pack", "📈 Quality", "🛠 SQL Lab", "📐 Metrics Governance", "🗂 Schema"]
)

def _render_turn(turn, i):
    with st.container(border=True):
        st.markdown(f"**You:** {turn.get('question') or ''}")
        if turn.get("grounding_line"):
            st.caption(f"📎 {turn['grounding_line']}")
        if turn.get("citations"):
            with st.expander("Citations / evidence", expanded=False):
                st.json(turn["citations"])
        if turn.get("message") and turn.get("success"):
            st.markdown(turn["message"])
        if turn.get("insight") and turn.get("insight") != turn.get("message"):
            st.markdown("**Insight**")
            st.markdown(turn["insight"])
        if turn.get("clarify_questions"):
            st.markdown("**Try asking:**")
            for cq in turn["clarify_questions"]:
                st.markdown(f"- {cq}")
        if turn.get("sql"):
            with st.expander("Generated SQL (evidence)", expanded=False):
                st.code(turn["sql"], language="sql")
        if turn.get("chart_fig") is not None:
            st.markdown(f"**Chart · {(turn.get('chart_type') or 'chart').title()}**")
            if turn.get("chart_reason"):
                st.caption(turn["chart_reason"])
            try:
                st.plotly_chart(turn["chart_fig"], use_container_width=True, key=f"c_{turn.get('id', i)}")
            except Exception:
                st.warning("Could not render chart.")
        for j, ch in enumerate(turn.get("extra_charts") or []):
            try:
                st.markdown(f"**{ch.get('title') or f'Chart {j+1}'}**")
                if ch.get("reason"):
                    st.caption(ch["reason"])
                if ch.get("fig") is not None:
                    st.plotly_chart(ch["fig"], use_container_width=True, key=f"e_{turn.get('id', i)}_{j}")
            except Exception:
                pass
        if turn.get("result_df") is not None:
            with st.expander("Data table", expanded=turn.get("chart_fig") is None):
                st.dataframe(turn["result_df"], use_container_width=True, hide_index=True)
        for w in turn.get("warnings") or []:
            st.warning(w)
        if turn.get("error") and not turn.get("success"):
            st.error(turn["error"])
        with st.expander("Agent pipeline steps", expanded=False):
            steps = turn.get("steps") or []
            st.code(" → ".join(steps) if steps else "No steps")

with tab_chat:
    st.markdown(f"**Active dataset:** `{selected_table}`")
    for i, turn in enumerate(st.session_state.chat_history):
        _render_turn(turn, i)
    with st.container(border=True):
        col_in, col_btn, col_eda, col_clear = st.columns([5.5, 1, 1.2, 1])
        with col_in:
            nl_question = st.text_input("Your question", placeholder="e.g. Why did sales drop by region? | +10% amount on North | Show RFM", key="nl_q", label_visibility="collapsed")
        with col_btn:
            ask_clicked = st.button("Ask", type="primary", use_container_width=True)
        with col_eda:
            eda_clicked = st.button("Run EDA pack", use_container_width=True)
        with col_clear:
            if st.button("Clear chat", use_container_width=True):
                st.session_state.chat_history = []
                st.rerun()

        def _make_turn(result, question):
            return {
                "id": str(uuid.uuid4())[:8], "question": question,
                "success": bool(getattr(result, "success", False)),
                "intent": getattr(result, "intent", None),
                "intent_reason": getattr(result, "intent_reason", None),
                "message": getattr(result, "message", None),
                "sql": getattr(result, "sql", None),
                "insight": getattr(result, "insight", None),
                "clarify_questions": list(getattr(result, "clarify_questions", None) or []),
                "result_df": getattr(result, "result_df", None),
                "forecast_df": getattr(result, "forecast_df", None),
                "anomalies": list(getattr(result, "anomalies", None) or []),
                "chart_fig": getattr(result, "chart_fig", None),
                "chart_type": getattr(result, "chart_type", None),
                "chart_reason": getattr(result, "chart_reason", None),
                "extra_charts": list(getattr(result, "extra_charts", None) or []),
                "steps": list(getattr(result, "steps", None) or []),
                "warnings": list(getattr(result, "warnings", None) or []),
                "error": getattr(result, "error", None),
                "provider": getattr(result, "provider", None),
                "model": getattr(result, "model", None),
                "grounding_line": getattr(result, "grounding_line", None),
                "citations": list(getattr(result, "citations", None) or []),
            }

        if ask_clicked and (nl_question or "").strip():
            raw_q = nl_question.strip()
            expanded_q = raw_q
            if _ctx_mod is not None and hasattr(_ctx_mod, "expand_question_with_history"):
                try:
                    expanded_q = _ctx_mod.expand_question_with_history(raw_q, st.session_state.chat_history)
                except Exception:
                    pass
            with st.spinner("Running analytics pipeline…"):
                try:
                    result = run_agent(question=expanded_q, table_name=selected_table, workspace=st.session_state.workspace)
                except Exception as e:
                    class _E:
                        success = False; message = error = str(e); intent = "error"
                        insight = sql = result_df = chart_fig = grounding_line = provider = model = intent_reason = chart_type = chart_reason = None
                        steps = warnings = citations = clarify_questions = anomalies = extra_charts = []
                        forecast_df = None
                    result = _E()
            turn = _make_turn(result, raw_q)
            st.session_state.chat_history.append(turn)
            _persist_chat_turn(turn, selected_table or "")
            st.rerun()

        if eda_clicked and selected_table:
            with st.spinner("Building EDA pack…"):
                try:
                    result = run_agent(question="run eda pack", table_name=selected_table, workspace=st.session_state.workspace)
                except Exception as e:
                    st.error(f"EDA failed: {e}")
                    result = None
            if result is not None:
                turn = _make_turn(result, "Run EDA pack")
                st.session_state.chat_history.append(turn)
                _persist_chat_turn(turn, selected_table or "")
                st.rerun()

with tab_eda:
    st.markdown(f"#### One-click EDA on `{selected_table}`")
    st.caption("Deterministic profile · correlations · charts · narrative. No LLM required.")
    if st.button("Generate EDA pack", type="primary", key="eda_tab"):
        with st.spinner("Profiling…"):
            try:
                from app.core.eda_pack import build_eda_pack
                rec = st.session_state.workspace.get(selected_table)
                df = getattr(rec, "cleaned_df", None) or getattr(rec, "raw_df", None)
                pack = build_eda_pack(df, table_name=selected_table)
            except Exception as e:
                st.error(str(e)); pack = None
        if pack is not None and pack.success:
            c1, c2, c3 = st.columns(3)
            c1.metric("Rows", f"{pack.rows:,}")
            c2.metric("Columns", pack.columns)
            c3.metric("Quality", f"{pack.quality_score}/100" if pack.quality_score is not None else "—")
            for line in (pack.narrative or []):
                st.markdown(f"- {line}")
            if pack.column_profile is not None:
                with st.expander("Column profile", expanded=True):
                    st.dataframe(pack.column_profile, use_container_width=True, hide_index=True)
            if pack.correlations:
                with st.expander("Top correlations"):
                    st.dataframe(pd.DataFrame(pack.correlations), use_container_width=True, hide_index=True)
            for ch in pack.charts or []:
                st.markdown(f"**{ch.get('title')}**")
                if ch.get("fig") is not None:
                    try:
                        st.plotly_chart(ch["fig"], use_container_width=True)
                    except Exception:
                        pass
        elif pack is not None:
            st.error(pack.error or "EDA failed")

with tab_quality:
    st.markdown(f"#### Data quality · `{selected_table}`")
    try:
        rec = st.session_state.workspace.get(selected_table)
        df = getattr(rec, "cleaned_df", None) or getattr(rec, "raw_df", None)
        st.json(generate_quality_report(df))
        st.dataframe(column_level_profile(df), use_container_width=True, hide_index=True)
    except Exception as e:
        st.error(str(e))

with tab_sql:
    st.markdown("#### SQL Lab (DuckDB)")
    for t in available_tables:
        st.code(t, language=None)
    sql_query = st.text_area("SQL", value=f'SELECT * FROM "{selected_table}" LIMIT 20', height=140, key="sql_lab")
    if st.button("▶ Run Query", type="primary", key="sql_run") and sql_query.strip():
        result_df, error = st.session_state.workspace.execute_sql(sql_query)
        if error:
            st.error(error)
        else:
            st.success(f"{len(result_df):,} rows × {len(result_df.columns)} cols")
            st.dataframe(result_df, use_container_width=True, hide_index=True)

with tab_gov:
    st.markdown(f"#### Metric Governance · `{selected_table}`")
    if _gov_mod is None or _sl_mod is None:
        st.warning("Governance modules failed to load.")
        if _gov_err:
            st.code(_gov_err)
    else:
        try:
            model = _gov_mod.build_governed_semantic_model(st.session_state.workspace, selected_table)
            cat = _gov_mod.catalog_summary(selected_table) if hasattr(_gov_mod, "catalog_summary") else {}
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Metrics", len(model.metrics))
            c2.metric("Source", getattr(model, "source", "—"))
            c3.metric("Overrides", cat.get("override_count", "—"))
            c4.metric("Disabled", cat.get("disabled_count", "—"))
            rows = [{"Name": m.name, "Label": m.label,
                     "Agg": m.agg.value if hasattr(m.agg, "value") else str(m.agg),
                     "SQL": m.sql_expression() if hasattr(m, "sql_expression") else getattr(m, "expr", "")}
                    for m in model.metrics]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            if st.button("Reset catalog", key="gov_reset"):
                _gov_mod.reset_catalog(selected_table)
                st.success("Catalog cleared."); st.rerun()
        except Exception as e:
            st.error(str(e)); st.exception(e)

with tab_schema:
    st.markdown(f"#### Schema · `{selected_table}`")
    try:
        st.dataframe(st.session_state.workspace.get_table_schema(selected_table), use_container_width=True, hide_index=True)
    except Exception as e:
        st.error(str(e))
        try:
            rec = st.session_state.workspace.get(selected_table)
            df = getattr(rec, "cleaned_df", None) or getattr(rec, "raw_df", None)
            if df is not None:
                st.dataframe(pd.DataFrame({
                    "Column": list(df.columns),
                    "Dtype": [str(df[c].dtype) for c in df.columns],
                    "Non-Null": [int(df[c].notna().sum()) for c in df.columns],
                    "Unique": [int(df[c].nunique()) for c in df.columns],
                }), use_container_width=True, hide_index=True)
        except Exception as e2:
            st.error(str(e2))
