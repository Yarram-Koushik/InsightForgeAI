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

# ---------- Export / evidence (Phase 2.6) ----------
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

warnings.filterwarnings("ignore", message="Could not infer format")
st.set_page_config(page_title="InsightForgeAI", page_icon="📊", layout="wide")

st.title("InsightForgeAI")
st.markdown("### AI-Powered Business Intelligence Assistant")
st.caption("Phase 2 – Intelligent Analysis Layer | Sub-Phase 2.6: Full Chat · Evidence · Export")
st.markdown("---")

if "workspace" not in st.session_state:
    st.session_state.workspace = Workspace()
if "excel_sheets_cache" not in st.session_state:
    st.session_state.excel_sheets_cache = {}
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "chat_dataset" not in st.session_state:
    st.session_state.chat_dataset = None

st.sidebar.title("Workspace")
st.sidebar.markdown("Upload files. Excel files will show all sheets.")

uploaded_files = st.sidebar.file_uploader(
    "Upload files", type=["csv", "xlsx", "xls", "json", "parquet"], accept_multiple_files=True
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
                            st.sidebar.success(f"Loaded: {final_name}")
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
                    st.sidebar.success(f"Loaded: {final_name}")
                except Exception as e:
                    st.sidebar.error(f"Error: {e}")

dataset_names = st.session_state.workspace.list_datasets()
if dataset_names:
    selected_table = st.sidebar.selectbox("Select Dataset", options=dataset_names)
else:
    selected_table = None
    st.sidebar.info("No datasets loaded yet.")

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
    st.caption(
        "Conversational analysis with full evidence. "
        "Router → SQL → Insight → Forecast → Visualization · Export any answer."
    )

    if st.session_state.chat_dataset != selected_table:
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

                if turn.get("message"):
                    if turn.get("success"):
                        st.success(turn["message"])
                    else:
                        st.error(turn["message"])

                if turn.get("clarify_questions"):
                    st.markdown("**Try asking:**")
                    for q in turn["clarify_questions"]:
                        st.markdown(f"- {q}")

                if turn.get("sql"):
                    with st.expander("Generated SQL (evidence)", expanded=False):
                        st.code(turn["sql"], language="sql")

                if turn.get("chart_fig") is not None:
                    chart_label = (turn.get("chart_type") or "chart").title()
                    reason = turn.get("chart_reason") or ""
                    st.markdown(f"**Chart · {chart_label}**")
                    if reason:
                        st.caption(reason)
                    st.plotly_chart(turn["chart_fig"], width="stretch", key=f"chart_{turn.get('id', i)}")

                if turn.get("forecast_df") is not None:
                    with st.expander("Forecast values (future periods)", expanded=False):
                        st.dataframe(turn["forecast_df"], width="stretch", hide_index=True)

                if turn.get("anomalies"):
                    with st.expander(f"Anomaly flags ({len(turn['anomalies'])})", expanded=False):
                        st.dataframe(turn["anomalies"], width="stretch", hide_index=True)

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
                    if steps:
                        st.code(" → ".join(steps))
                    else:
                        st.caption("No steps recorded.")
                    if turn.get("provider"):
                        st.caption(f"Provider: {turn.get('provider')} · Model: {turn.get('model') or '—'}")

                with st.expander("Export this answer", expanded=False):
                    stamp = safe_filename_part(turn.get("id") or str(i))
                    qpart = safe_filename_part(turn.get("question") or "answer")
                    c1, c2, c3 = st.columns(3)

                    if turn.get("result_df") is not None:
                        payload = dataframe_to_csv_bytes(turn["result_df"])
                        if payload.data:
                            c1.download_button(
                                "⬇ Result CSV",
                                data=payload.data,
                                file_name=f"result_{qpart}_{stamp}.csv",
                                mime=payload.mime,
                                key=f"csv_result_{i}",
                            )
                            if payload.note:
                                st.caption(payload.note)

                    if turn.get("forecast_df") is not None:
                        payload = dataframe_to_csv_bytes(turn["forecast_df"])
                        if payload.data:
                            c2.download_button(
                                "⬇ Forecast CSV",
                                data=payload.data,
                                file_name=f"forecast_{qpart}_{stamp}.csv",
                                mime=payload.mime,
                                key=f"csv_fc_{i}",
                            )

                    pack = turn.get("evidence")
                    if pack:
                        c3.download_button(
                            "⬇ Evidence JSON",
                            data=evidence_to_json(pack),
                            file_name=f"evidence_{qpart}_{stamp}.json",
                            mime="application/json",
                            key=f"ev_json_{i}",
                        )
                        st.download_button(
                            "⬇ Evidence Markdown",
                            data=evidence_to_markdown(pack),
                            file_name=f"evidence_{qpart}_{stamp}.md",
                            mime="text/markdown",
                            key=f"ev_md_{i}",
                        )

                    if turn.get("chart_fig") is not None:
                        html_payload = chart_to_html_bytes(turn["chart_fig"], title=qpart)
                        if html_payload and html_payload.data:
                            st.download_button(
                                "⬇ Chart HTML",
                                data=html_payload.data,
                                file_name=html_payload.filename,
                                mime=html_payload.mime,
                                key=f"chart_html_{i}",
                            )
                        png_payload = chart_to_png_bytes(turn["chart_fig"], title=qpart)
                        if png_payload and png_payload.data:
                            st.download_button(
                                "⬇ Chart PNG",
                                data=png_payload.data,
                                file_name=png_payload.filename,
                                mime=png_payload.mime,
                                key=f"chart_png_{i}",
                            )
                        else:
                            st.caption("PNG export needs `pip install kaleido` (optional). HTML always works.")
    else:
        st.info("Ask a question below to start the conversation. Answers stay in this session with full evidence.")

    with st.container(border=True):
        col_in, col_btn, col_clear = st.columns([6, 1, 1])
        with col_in:
            nl_question = st.text_input(
                "Your question",
                placeholder="e.g. How many per Branch?  |  Forecast next 30 days  |  Why are counts different?",
                key="nl_question",
                label_visibility="collapsed",
            )
        with col_btn:
            ask_clicked = st.button("Ask", type="primary", use_container_width=True)
        with col_clear:
            clear_clicked = st.button("Clear", use_container_width=True)

        st.caption("Agents: Router · SQL · Insight · Forecast · Viz · Clarify  |  Keys in .env")

        if clear_clicked:
            st.session_state.chat_history = []
            st.rerun()

        if ask_clicked and nl_question.strip():
            with st.spinner("Agents working..."):
                agent_result = run_agent(
                    workspace=st.session_state.workspace,
                    table_name=selected_table,
                    question=nl_question.strip(),
                )

            import uuid
            pack = build_evidence_pack(
                question=nl_question.strip(),
                table_name=selected_table,
                agent_result=agent_result,
                source_filename=getattr(record, "source_filename", None),
            )
            turn = {
                "id": str(uuid.uuid4())[:8],
                "question": nl_question.strip(),
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
            }
            st.session_state.chat_history.append(turn)
            if len(st.session_state.chat_history) > 30:
                st.session_state.chat_history = st.session_state.chat_history[-30:]
            st.rerun()

    st.markdown("---")
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["Schema", "Cleaning & Lineage", "Data Profile", "Data Preview", "SQL Query"])

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
        c1.metric("Completeness", f"{quality['completeness']['score']}%", help=quality['completeness']['details'])
        c2.metric("Uniqueness", f"{quality['uniqueness']['score']}%", help=quality['uniqueness']['details'])
        c3.metric("Validity", f"{quality['validity']['score']}%", help=quality['validity']['details'])
        c4.metric("Memory", f"{quality['memory_mb']} MB")
        st.markdown("##### Column-Level Profile")
        st.dataframe(column_level_profile(df), width="stretch", hide_index=True)

    with tab4:
        st.dataframe(df.head(30), width="stretch")

    with tab5:
        st.markdown("#### SQL Query Engine (DuckDB)")
        available_tables = st.session_state.workspace.list_duckdb_tables()
        if available_tables:
            st.markdown(f"**Available tables:** `{', '.join(available_tables)}`")
            selected_for_schema = st.selectbox("Inspect table schema", options=available_tables, key="schema_inspect")
            if selected_for_schema:
                st.dataframe(st.session_state.workspace.get_table_schema(selected_for_schema), width="stretch", hide_index=True)
        else:
            st.warning("No tables registered yet.")
        default_sql = f'SELECT * FROM "{selected_table}" LIMIT 20' if selected_table else "SELECT 1"
        sql_query = st.text_area("Write your SQL query", value=default_sql, height=120, key="sql_input")
        if st.button("▶ Run Query", type="primary") and sql_query.strip():
            result_df, error = st.session_state.workspace.execute_sql(sql_query)
            if error:
                st.error(f"Query error: {error}")
            else:
                st.success(f"Returned {len(result_df):,} rows × {len(result_df.columns)} columns")
                st.dataframe(result_df, width="stretch", hide_index=True)
else:
    st.info("Upload files from the sidebar to get started.")
