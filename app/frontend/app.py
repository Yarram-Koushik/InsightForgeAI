import streamlit as st
import pandas as pd
import numpy as np
import duckdb
import warnings
import sys
from pathlib import Path
import importlib.util

current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent.parent
ingestion_path = project_root / "app" / "core" / "ingestion.py"

spec = importlib.util.spec_from_file_location("ingestion", ingestion_path)
ingestion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingestion)

schema_path = project_root / "app" / "core" / "schema.py"
spec_schema = importlib.util.spec_from_file_location("schema", schema_path)
schema_module = importlib.util.module_from_spec(spec_schema)
spec_schema.loader.exec_module(schema_module)
detect_schema_semantic = schema_module.detect_schema_semantic

cleaning_path = project_root / "app" / "core" / "cleaning.py"
spec_cleaning = importlib.util.spec_from_file_location("cleaning", cleaning_path)
cleaning_module = importlib.util.module_from_spec(spec_cleaning)
spec_cleaning.loader.exec_module(cleaning_module)
detect_cleaning_issues = cleaning_module.detect_cleaning_issues

data_manager_path = project_root / "app" / "core" / "data_manager.py"
spec_dm = importlib.util.spec_from_file_location("data_manager", data_manager_path)
dm_module = importlib.util.module_from_spec(spec_dm)
spec_dm.loader.exec_module(dm_module)

profiling_path = project_root / "app" / "core" / "profiling.py"
spec_prof = importlib.util.spec_from_file_location("profiling", profiling_path)
profiling_module = importlib.util.module_from_spec(spec_prof)
spec_prof.loader.exec_module(profiling_module)

generate_quality_report = profiling_module.generate_quality_report
column_level_profile = profiling_module.column_level_profile
Workspace = dm_module.Workspace

def _get_durable_store():
    if st.session_state.get("_durable_store") is not None:
        return st.session_state["_durable_store"]
    try:
        import os
        ws_path = project_root / "app" / "core" / "workspace_store.py"
        if not ws_path.exists():
            return None
        spec = importlib.util.spec_from_file_location("workspace_store_ui", ws_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["workspace_store_ui"] = mod
        spec.loader.exec_module(mod)
        wid = os.getenv("INSIGHTFORGE_WORKSPACE_ID", "default")
        store = mod.get_or_create_store(wid)
        st.session_state["_durable_store"] = store
        return store
    except Exception as e:
        st.session_state["_durable_store_error"] = str(e)
        return None


def _persist_dataset(record_name: str) -> None:
    try:
        store = _get_durable_store()
        if store is None:
            return
        rec = st.session_state.workspace.get(record_name)
        if rec is not None:
            store.save_dataset(rec, include_raw=False)
    except Exception:
        pass


def _persist_chat_turn(turn: dict, table_name: str) -> None:
    try:
        store = _get_durable_store()
        if store is None:
            return
        payload = {
            "id": turn.get("id"),
            "question": turn.get("question"),
            "success": turn.get("success"),
            "intent": turn.get("intent"),
            "intent_reason": turn.get("intent_reason"),
            "message": turn.get("message"),
            "sql": turn.get("sql"),
            "insight": turn.get("insight"),
            "clarify_questions": turn.get("clarify_questions") or [],
            "warnings": turn.get("warnings") or [],
            "error": turn.get("error"),
            "provider": turn.get("provider"),
            "model": turn.get("model"),
            "steps": turn.get("steps") or [],
            "table_name": table_name,
            "grounding_line": turn.get("grounding_line"),
        }
        store.append_chat_turn(payload)
    except Exception:
        pass

DatasetRecord = dm_module.DatasetRecord
apply_safe_cleaning = cleaning_module.apply_safe_cleaning
make_safe_table_name = ingestion.make_safe_table_name
get_excel_sheets_info = ingestion.get_excel_sheets_info
read_file = ingestion.read_file

orch_path = project_root / "app" / "agents" / "orchestrator.py"
spec_orch = importlib.util.spec_from_file_location("orchestrator", orch_path)
orch_module = importlib.util.module_from_spec(spec_orch)
sys.modules["orchestrator"] = orch_module
spec_orch.loader.exec_module(orch_module)
run_agent = orch_module.run_agent

export_path = project_root / "app" / "core" / "export.py"
spec_export = importlib.util.spec_from_file_location("export_helpers", export_path)
export_module = importlib.util.module_from_spec(spec_export)
sys.modules["export_helpers"] = export_module
spec_export.loader.exec_module(export_module)
build_evidence_pack = export_module.build_evidence_pack
evidence_to_json = export_module.evidence_to_json
evidence_to_markdown = export_module.evidence_to_markdown
dataframe_to_csv_bytes = export_module.dataframe_to_csv_bytes
chart_to_html_bytes = export_module.chart_to_html_bytes
chart_to_png_bytes = export_module.chart_to_png_bytes
safe_filename_part = export_module.safe_filename_part

# Phase 4.2 – multi-turn conversational memory
_ctx_mem = None
try:
    _cm_path = project_root / "app" / "core" / "context_memory.py"
    if _cm_path.exists():
        _cm_spec = importlib.util.spec_from_file_location("context_memory_ui", _cm_path)
        _ctx_mem = importlib.util.module_from_spec(_cm_spec)
        sys.modules["context_memory_ui"] = _ctx_mem
        _cm_spec.loader.exec_module(_ctx_mem)
except Exception:
    _ctx_mem = None

# Phase 3.5 – load governance modules ONCE at startup (never inside a tab)
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

_gov_mod = None
_sl_mod = None
try:
    import types
    if "app" not in sys.modules:
        _app_pkg = types.ModuleType("app")
        _app_pkg.__path__ = [str(project_root / "app")]
        sys.modules["app"] = _app_pkg
    if "app.core" not in sys.modules:
        _core_pkg = types.ModuleType("app.core")
        _core_pkg.__path__ = [str(project_root / "app" / "core")]
        sys.modules["app.core"] = _core_pkg

    _sl_path = project_root / "app" / "core" / "semantic_layer.py"
    if _sl_path.exists():
        _sl_spec = importlib.util.spec_from_file_location(
            "app.core.semantic_layer", _sl_path,
            submodule_search_locations=[str(project_root / "app" / "core")],
        )
        _sl_mod = importlib.util.module_from_spec(_sl_spec)
        sys.modules["app.core.semantic_layer"] = _sl_mod
        sys.modules["semantic_layer"] = _sl_mod
        _sl_spec.loader.exec_module(_sl_mod)

    _gov_path = project_root / "app" / "core" / "metric_governance.py"
    if _gov_path.exists() and _sl_mod is not None:
        _gov_spec = importlib.util.spec_from_file_location(
            "app.core.metric_governance", _gov_path,
            submodule_search_locations=[str(project_root / "app" / "core")],
        )
        _gov_mod = importlib.util.module_from_spec(_gov_spec)
        sys.modules["app.core.metric_governance"] = _gov_mod
        sys.modules["metric_governance"] = _gov_mod
        _gov_spec.loader.exec_module(_gov_mod)
except Exception:
    _gov_mod = None
    _sl_mod = None

warnings.filterwarnings("ignore", message="Could not infer format")
st.set_page_config(page_title="InsightForgeAI", page_icon="📊", layout="wide")

st.title("InsightForgeAI")
st.markdown("### AI-Powered Business Intelligence Assistant")
st.caption("Phase 4 – Live connectors · Multi-turn memory · Citations · Durable workspace")
st.markdown("---")

if "workspace" not in st.session_state:
    st.session_state.workspace = Workspace()
if "excel_sheets_cache" not in st.session_state:
    st.session_state.excel_sheets_cache = {}
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "chat_dataset" not in st.session_state:
    st.session_state.chat_dataset = None
if "_ws_restored" not in st.session_state:
    st.session_state["_ws_restored"] = False

if not st.session_state.get("_ws_restored"):
    store = _get_durable_store()
    if store is not None:
        try:
            info = store.load_into(st.session_state.workspace)
            restored = list((info or {}).get("restored") or [])
            if restored:
                st.session_state["_restored_names"] = restored
            try:
                turns = store.load_chat_history(limit=20)
                if turns and not st.session_state.chat_history:
                    light = []
                    for t in turns:
                        d = t.to_dict() if hasattr(t, "to_dict") else dict(t)
                        light.append({
                            "id": d.get("id"), "question": d.get("question"),
                            "success": d.get("success", True), "intent": d.get("intent"),
                            "intent_reason": d.get("intent_reason"), "message": d.get("message"),
                            "sql": d.get("sql"), "insight": d.get("insight"),
                            "clarify_questions": d.get("clarify_questions") or [],
                            "result_df": None, "forecast_df": None, "anomalies": [],
                            "chart_fig": None, "chart_type": None, "chart_reason": None,
                            "steps": d.get("steps") or [], "warnings": d.get("warnings") or [],
                            "error": d.get("error"), "provider": d.get("provider"),
                            "model": d.get("model"), "evidence": None, "_restored": True,
                            "grounding_line": d.get("grounding_line"),
                        })
                        if d.get("table_name") and not st.session_state.chat_dataset:
                            st.session_state.chat_dataset = d.get("table_name")
                    st.session_state.chat_history = light
            except Exception:
                pass
        except Exception as e:
            st.session_state["_durable_restore_error"] = str(e)
    st.session_state["_ws_restored"] = True

st.sidebar.title("Workspace")
st.sidebar.markdown("Upload files. Excel files will show all sheets.")

uploaded_files = st.sidebar.file_uploader(
    "Upload files",
    type=["csv", "xlsx", "xls", "json", "parquet"],
    accept_multiple_files=True,
    key="sidebar_workspace_uploader",
)

if uploaded_files:
    for uploaded_file in uploaded_files:
        file_key = uploaded_file.name
        if uploaded_file.name.lower().endswith((".xlsx", ".xls")):
            if file_key not in st.session_state.excel_sheets_cache:
                try:
                    sheets_info = get_excel_sheets_info(uploaded_file)
                    st.session_state.excel_sheets_cache[file_key] = sheets_info
                except Exception as e:
                    st.sidebar.error(f"Error reading Excel: {e}")
                    continue
            sheets_info = st.session_state.excel_sheets_cache[file_key]
            st.sidebar.markdown(f"**{uploaded_file.name}**")
            st.sidebar.caption(f"{len(sheets_info)} sheet(s) found")
            for sheet in sheets_info:
                sheet_label = f"{sheet['sheet_name']}  ({sheet['rows']} rows × {sheet['columns']} cols)"
                if sheet["is_empty"]:
                    st.sidebar.caption(f"⬜ {sheet_label} — Empty (skipped)")
                    continue
                checkbox_key = f"load_{file_key}_{sheet['sheet_name']}"
                if st.sidebar.checkbox(sheet_label, key=checkbox_key, value=False):
                    table_name = make_safe_table_name(f"{Path(uploaded_file.name).stem}_{sheet['sheet_name']}")
                    if table_name not in st.session_state.workspace.list_datasets():
                        try:
                            raw_df = read_file(uploaded_file, sheet_name=sheet["sheet_name"])
                            final_name = st.session_state.workspace.add_dataset(
                                name=table_name, raw_df=raw_df, source_filename=uploaded_file.name
                            )
                            issues = detect_cleaning_issues(raw_df)
                            cleaned_df, change_log = apply_safe_cleaning(raw_df, issues)
                            record = st.session_state.workspace.get(final_name)
                            record.apply_cleaning(cleaned_df, issues, change_log)
                            st.session_state.workspace.register_in_duckdb(final_name)
                            _persist_dataset(final_name)
                            st.sidebar.success(f"Loaded: {final_name} (saved)")
                        except Exception as e:
                            st.sidebar.error(f"Failed to load sheet: {e}")
        else:
            table_name = make_safe_table_name(uploaded_file.name)
            if table_name not in st.session_state.workspace.list_datasets():
                try:
                    raw_df = read_file(uploaded_file)
                    final_name = st.session_state.workspace.add_dataset(
                        name=table_name, raw_df=raw_df, source_filename=uploaded_file.name
                    )
                    issues = detect_cleaning_issues(raw_df)
                    cleaned_df, change_log = apply_safe_cleaning(raw_df, issues)
                    record = st.session_state.workspace.get(final_name)
                    record.apply_cleaning(cleaned_df, issues, change_log)
                    st.session_state.workspace.register_in_duckdb(final_name)
                    _persist_dataset(final_name)
                    st.sidebar.success(f"Loaded: {final_name} (saved)")
                except Exception as e:
                    st.sidebar.error(f"Error: {e}")


with st.sidebar.expander("🔌 Live connections (Postgres / MySQL)", expanded=False):
    st.caption("Credentials stay in this session only. Prefer env vars for demos.")
    dialect = st.selectbox(
        "Dialect",
        options=["postgres", "mysql"],
        key="conn_dialect",
    )
    c_name = st.text_input("Connection name", value="prod_db", key="conn_name")
    c_host = st.text_input("Host", value="localhost", key="conn_host")
    c_port = st.number_input(
        "Port",
        min_value=1,
        max_value=65535,
        value=5432 if dialect == "postgres" else 3306,
        key="conn_port",
    )
    c_db = st.text_input("Database", value="", key="conn_db")
    c_user = st.text_input("User", value="", key="conn_user")
    c_pass = st.text_input("Password", type="password", value="", key="conn_pass")
    c_schema = st.text_input(
        "Schema (Postgres default: public)",
        value="public" if dialect == "postgres" else "",
        key="conn_schema",
    )

    col_t, col_l = st.columns(2)
    test_clicked = col_t.button("Test & list tables", use_container_width=True)
    clear_conn = col_l.button("Clear", use_container_width=True)

    if clear_conn:
        for k in ("_conn_tables", "_conn_config", "_conn_msg"):
            st.session_state.pop(k, None)
        st.rerun()

    if test_clicked:
        if not c_host or not c_db or not c_user:
            st.error("Host, database and user are required.")
        else:
            try:
                from app.core.connectors.ui_helpers import build_config_from_form, test_and_list

                cfg = build_config_from_form(
                    name=c_name or "connection",
                    dialect=dialect,
                    host=c_host,
                    port=int(c_port),
                    database=c_db,
                    user=c_user,
                    password=c_pass,
                    schema=c_schema or None,
                )
                ok, msg, tables = test_and_list(cfg)
                st.session_state["_conn_msg"] = (ok, msg)
                st.session_state["_conn_tables"] = tables
                st.session_state["_conn_config"] = cfg
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)
            except Exception as e:
                st.error(str(e))

    if st.session_state.get("_conn_msg") and not test_clicked:
        ok, msg = st.session_state["_conn_msg"]
        (st.success if ok else st.error)(msg)

    tables = st.session_state.get("_conn_tables") or []
    if tables:
        st.markdown(f"**Tables** ({len(tables)})")
        labels = []
        for t in tables:
            est = t.get("row_estimate")
            cols = t.get("column_count")
            extra = []
            if est is not None:
                extra.append(f"~{est:,} rows")
            if cols is not None:
                extra.append(f"{cols} cols")
            label = t["name"]
            if t.get("schema"):
                label = f"{t['schema']}.{label}"
            if extra:
                label = f"{label}  ({', '.join(extra)})"
            labels.append(label)

        selected_labels = st.multiselect(
            "Select tables to load into workspace",
            options=labels,
            key="conn_table_select",
        )
        label_to_table = dict(zip(labels, tables))
        load_limit = st.number_input(
            "Row limit per table (safety)",
            min_value=100,
            max_value=500_000,
            value=50_000,
            step=1000,
            key="conn_load_limit",
        )
        if st.button("Load selected → workspace", type="primary", use_container_width=True):
            cfg = st.session_state.get("_conn_config")
            if cfg is None:
                st.error("Test the connection first.")
            else:
                chosen = [label_to_table[lb] for lb in selected_labels if lb in label_to_table]
                if not chosen:
                    st.warning("Select at least one table.")
                else:
                    try:
                        from app.core.connectors.ui_helpers import load_selected_tables

                        with st.spinner(f"Loading {len(chosen)} table(s)…"):
                            results = load_selected_tables(
                                st.session_state.workspace,
                                cfg,
                                chosen,
                                limit=int(load_limit),
                                run_cleaning=True,
                            )
                        for r in results:
                            if r.get("ok"):
                                st.success(
                                    f"Loaded `{r['dataset_name']}` "
                                    f"({r.get('rows', 0):,} rows) from {r.get('table')}"
                                )
                                try:
                                    _persist_dataset(r["dataset_name"])
                                except Exception:
                                    pass
                            else:
                                st.error(f"{r.get('table')}: {r.get('error')}")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))

dataset_names = st.session_state.workspace.list_datasets()
if st.session_state.get("_restored_names"):
    st.sidebar.success("Restored from disk: " + ", ".join(st.session_state["_restored_names"]))
    st.session_state["_restored_names"] = None
if dataset_names:
    selected_table = st.sidebar.selectbox("Select Dataset", options=dataset_names, key="sidebar_select_dataset")
else:
    selected_table = None
    st.sidebar.info("No datasets loaded yet. Upload a file — it will survive F5.")

if selected_table:
    record = st.session_state.workspace.get(selected_table)
    st.markdown(f"### Dataset: `{record.name}`")
    st.caption(f"Source: {record.source_filename} | ID: {record.id}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Raw Rows", f"{record.metadata['original_rows']:,}")
    c2.metric("Cleaned Rows", f"{record.metadata.get('cleaned_rows', record.metadata['original_rows']):,}")
    c3.metric("Columns", record.metadata["original_columns"])
    c4.metric("Issues Found", len(record.issues))
    view_mode = st.radio("View Mode", ["Cleaned Data", "Raw Data"], horizontal=True)
    df = record.cleaned_df if view_mode == "Cleaned Data" else record.raw_df

    st.markdown("---")
    st.markdown("### Ask InsightForge")
    st.caption("Multi-turn analysis with citations. Follow-ups like \"by region\" or \"vs last year\" stay grounded.")

    if st.session_state.chat_dataset != selected_table:
        if not any(t.get("_restored") for t in (st.session_state.chat_history or [])):
            st.session_state.chat_history = []
        st.session_state.chat_dataset = selected_table

    hist = st.session_state.chat_history
    if hist:
        st.markdown(f"**Conversation** · {len(hist)} turn(s)")
        for i, turn in enumerate(hist):
            with st.chat_message("user"):
                st.markdown(turn.get("question") or "")
            with st.chat_message("assistant"):
                intent_label = (turn.get("intent") or "unknown").replace("_", " ").title()
                st.caption(f"Route: **{intent_label}**" + (f" — {turn.get('intent_reason')}" if turn.get("intent_reason") else ""))
                if turn.get("grounding_line"):
                    st.caption(f"🔗 {turn['grounding_line']}")
                if turn.get("message"):
                    (st.success if turn.get("success") else st.error)(turn["message"])
                if turn.get("clarify_questions"):
                    st.markdown("**Try asking:**")
                    for q in turn["clarify_questions"]:
                        st.markdown(f"- {q}")
                if turn.get("sql"):
                    with st.expander("Generated SQL (evidence)", expanded=False):
                        st.code(turn["sql"], language="sql")
                if turn.get("chart_fig") is not None:
                    st.markdown(f"**Chart · {(turn.get('chart_type') or 'chart').title()}**")
                    if turn.get("chart_reason"):
                        st.caption(turn["chart_reason"])
                    st.plotly_chart(turn["chart_fig"], width="stretch", key=f"chart_{turn.get('id', i)}")
                if turn.get("result_df") is not None:
                    with st.expander("Data table", expanded=turn.get("chart_fig") is None):
                        st.dataframe(turn["result_df"], width="stretch", hide_index=True)
                if turn.get("insight"):
                    st.markdown("**Insight**")
                    st.markdown(turn["insight"])
                for w in turn.get("warnings") or []:
                    st.warning(w)
                if turn.get("error") and not turn.get("message"):
                    st.error(turn["error"])
                with st.expander("Agent pipeline steps", expanded=False):
                    steps = turn.get("steps") or []
                    st.code(" → ".join(steps) if steps else "No steps recorded.")
    else:
        st.info("Ask a question below to start the conversation.")

    with st.container(border=True):
        col_in, col_btn, col_clear = st.columns([6, 1, 1])
        with col_in:
            nl_question = st.text_input("Your question", placeholder="e.g. List order_id and customer_name", key="nl_question", label_visibility="collapsed")
        with col_btn:
            ask_clicked = st.button("Ask", type="primary", use_container_width=True)
        with col_clear:
            clear_clicked = st.button("Clear", use_container_width=True)
        st.caption("Agents: Router · SQL · Insight · Forecast · Viz · Clarify")
        if clear_clicked:
            st.session_state.chat_history = []
            st.rerun()
        if ask_clicked and nl_question.strip():
            raw_q = nl_question.strip()
            # Phase 4.2 – expand short follow-ups against conversation history
            question_for_agent = raw_q
            if _ctx_mem is not None and hasattr(_ctx_mem, "expand_question_with_history"):
                try:
                    question_for_agent = _ctx_mem.expand_question_with_history(
                        raw_q, st.session_state.chat_history
                    )
                except Exception:
                    question_for_agent = raw_q
            with st.spinner("Agents working..."):
                agent_result = run_agent(
                    workspace=st.session_state.workspace,
                    table_name=selected_table,
                    question=question_for_agent,
                )
            import uuid
            pack = build_evidence_pack(
                question=raw_q,
                table_name=selected_table,
                agent_result=agent_result,
                source_filename=getattr(record, "source_filename", None),
            )
            turn = {
                "id": str(uuid.uuid4())[:8],
                "question": raw_q,
                "success": bool(agent_result.success),
                "intent": agent_result.intent,
                "intent_reason": agent_result.intent_reason,
                "message": agent_result.message,
                "sql": agent_result.sql,
                "insight": agent_result.insight,
                "clarify_questions": list(agent_result.clarify_questions or []),
                "result_df": agent_result.result_df,
                "forecast_df": getattr(agent_result, "forecast_df", None),
                "anomalies": list(getattr(agent_result, "anomalies", []) or []),
                "chart_fig": getattr(agent_result, "chart_fig", None),
                "chart_type": getattr(agent_result, "chart_type", None),
                "chart_reason": getattr(agent_result, "chart_reason", None),
                "steps": list(agent_result.steps or []),
                "warnings": list(agent_result.warnings or []),
                "error": agent_result.error,
                "provider": agent_result.provider,
                "model": agent_result.model,
                "evidence": pack,
                "grounding_line": getattr(agent_result, "grounding_line", None),
                "citations": list(getattr(agent_result, "citations", None) or []),
            }
            st.session_state.chat_history.append(turn)
            _persist_chat_turn(turn, selected_table)
            if len(st.session_state.chat_history) > 30:
                st.session_state.chat_history = st.session_state.chat_history[-30:]
            st.rerun()

    st.markdown("---")
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["Schema", "Cleaning & Lineage", "Data Profile", "Data Preview", "SQL Query", "Metrics Governance"])

    with tab1:
        st.markdown("#### Semantic Schema Detection")
        schema_df = detect_schema_semantic(df)
        display_cols = ["column", "semantic_type", "confidence", "physical_type", "unique_count", "missing_pct", "recommendation"]
        schema_df = schema_df[display_cols]
        schema_df.columns = ["Column", "Semantic Type", "Confidence", "Physical Type", "Unique Values", "Missing %", "Recommendation"]
        st.dataframe(schema_df, width="stretch", hide_index=True)

    with tab2:
        st.markdown("#### Detected Issues")
        if record.issues:
            st.dataframe(pd.DataFrame(record.issues), width="stretch", hide_index=True)
        else:
            st.success("No major issues detected.")
        st.markdown("#### Change Log (Lineage)")
        if record.lineage:
            st.dataframe(pd.DataFrame(record.lineage), width="stretch", hide_index=True)
        else:
            st.info("No automatic changes were applied.")

    with tab3:
        st.markdown("#### Transparent Data Quality Report")
        quality = generate_quality_report(df)
        score = quality["overall_score"]
        color = "green" if score >= 85 else ("orange" if score >= 70 else "red")
        st.markdown(f"### Overall Quality Score: :{color}[{score}/100]")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Completeness", f"{quality['completeness']['score']}%")
        c2.metric("Uniqueness", f"{quality['uniqueness']['score']}%")
        c3.metric("Validity", f"{quality['validity']['score']}%")
        c4.metric("Memory", f"{quality['memory_mb']} MB")
        st.dataframe(column_level_profile(df), width="stretch", hide_index=True)

    with tab4:
        st.dataframe(df.head(30), width="stretch")

    with tab5:
        st.markdown("#### SQL Query Engine (DuckDB)")
        available_tables = st.session_state.workspace.list_duckdb_tables()
        if available_tables:
            st.markdown("**Available tables (use these exact names):**")
            for t in available_tables:
                st.code(t, language=None)
            selected_for_schema = st.selectbox("Inspect table schema", options=available_tables, key="schema_inspect")
            if selected_for_schema:
                st.dataframe(st.session_state.workspace.get_table_schema(selected_for_schema), width="stretch", hide_index=True)
        else:
            st.warning("No tables registered yet.")
        default_sql = f'SELECT region, SUM(amount) AS total\nFROM "{selected_table}"\nGROUP BY region\nORDER BY total DESC' if selected_table else "SELECT 1"
        sql_query = st.text_area("Write your SQL query", value=default_sql, height=140, key="sql_input")
        if st.button("▶ Run Query", type="primary") and sql_query.strip():
            result_df, error = st.session_state.workspace.execute_sql(sql_query)
            if error:
                st.error(f"Query error: {error}")
            else:
                st.success(f"Returned {len(result_df):,} rows × {len(result_df.columns)} columns")
                st.dataframe(result_df, width="stretch", hide_index=True)

    with tab6:
        st.markdown("#### Metric Governance (Phase 3.5)")
        st.caption(f"Active dataset: `{selected_table}`")
        st.caption("Browse metrics, override definitions, disable or add custom metrics.")
        if _gov_mod is None or _sl_mod is None:
            st.warning("Metric governance modules failed to load at startup. Check app/core/metric_governance.py and semantic_layer.py.")
        else:
            try:
                gov = _gov_mod
                sl = _sl_mod
                model = gov.build_governed_semantic_model(st.session_state.workspace, selected_table)
                cat_info = gov.catalog_summary(selected_table)
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Metrics", len(model.metrics))
                c2.metric("Source", model.source)
                c3.metric("Overrides", cat_info["override_count"])
                c4.metric("Disabled", cat_info["disabled_count"])
                rows = []
                for m in model.metrics:
                    rows.append({
                        "Name": m.name, "Label": m.label,
                        "Agg": m.agg.value if hasattr(m.agg, "value") else str(m.agg),
                        "SQL": m.sql_expression(), "Preferred": m.preferred,
                    })
                if rows:
                    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
                else:
                    st.info("No metrics in the governed model.")
                st.markdown("---")
                metric_names = [m.name for m in model.metrics]
                if metric_names:
                    sel = st.selectbox("Select metric", options=metric_names, key="gov_sel_metric")
                    chosen = next((m for m in model.metrics if m.name == sel), None)
                    if chosen:
                        with st.form(key="gov_edit_form"):
                            new_label = st.text_input("Label", value=chosen.label)
                            new_expr = st.text_input("Expression override (optional)", value=chosen.expr or "")
                            new_preferred = st.checkbox("Preferred", value=chosen.preferred)
                            col_a, col_b = st.columns(2)
                            save_btn = col_a.form_submit_button("Save override", type="primary")
                            disable_btn = col_b.form_submit_button("Disable metric")
                            if save_btn:
                                updated = sl.Metric(
                                    name=chosen.name, label=new_label.strip() or chosen.label,
                                    description=chosen.description or "", agg=chosen.agg,
                                    additivity=chosen.additivity, measure_column=chosen.measure_column,
                                    entity_column=chosen.entity_column, numerator=chosen.numerator,
                                    denominator=chosen.denominator, expr=new_expr.strip() or chosen.expr,
                                    filters=list(chosen.filters or []), preferred=bool(new_preferred),
                                    confidence=chosen.confidence, tags=list(chosen.tags or []),
                                    reason="User override via Metrics Governance UI",
                                )
                                gov.set_metric_override(selected_table, updated)
                                st.success(f"Saved override for `{chosen.name}`")
                                st.rerun()
                            if disable_btn:
                                gov.disable_metric(selected_table, chosen.name)
                                st.success(f"Disabled `{chosen.name}`")
                                st.rerun()
                if st.button("Reset catalog (back to pure auto)", key="gov_reset"):
                    gov.reset_catalog(selected_table)
                    st.success("Catalog cleared.")
                    st.rerun()
            except Exception as e:
                st.error(f"Could not render metric governance UI: {e}")
                st.exception(e)
else:
    st.info("Upload files from the sidebar to get started. After load, data is saved under data/workspaces/ and survives F5.")
