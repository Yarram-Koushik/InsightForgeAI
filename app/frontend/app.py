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

warnings.filterwarnings("ignore", message="Could not infer format")
st.set_page_config(page_title="InsightForgeAI", page_icon="📊", layout="wide")

st.title("InsightForgeAI")
st.markdown("### AI-Powered Business Intelligence Assistant")
st.caption("Phase 2 – Intelligent Analysis Layer | Sub-Phase 2.5: Forecasting & Advanced Analytics")
st.markdown("---")

if "workspace" not in st.session_state:
    st.session_state.workspace = Workspace()
if "excel_sheets_cache" not in st.session_state:
    st.session_state.excel_sheets_cache = {}

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
        "Multi-agent pipeline: Router → SQL → Insight → Forecast → Visualization. "
        "Every answer shows the route taken and the SQL used."
    )

    with st.container(border=True):
        nl_question = st.text_input(
            "Your question",
            placeholder="e.g. How many per Branch?  |  Forecast next 30 days  |  Why are counts different?",
            key="nl_question",
            label_visibility="collapsed",
        )
        col_ask, col_status = st.columns([1, 4])
        with col_ask:
            ask_clicked = st.button("Ask", type="primary", use_container_width=True)
        with col_status:
            st.caption("Agents: Router · SQL · Insight · Forecast · Viz · Clarify  |  Keys in .env")

        if ask_clicked and nl_question.strip():
            with st.spinner("Agents working..."):
                agent_result = run_agent(
                    workspace=st.session_state.workspace,
                    table_name=selected_table,
                    question=nl_question.strip(),
                )

            intent_label = (agent_result.intent or "unknown").replace("_", " ").title()
            st.caption(f"Route: **{intent_label}**" + (f" — {agent_result.intent_reason}" if agent_result.intent_reason else ""))

            if agent_result.message:
                if agent_result.success:
                    st.success(agent_result.message)
                else:
                    st.error(agent_result.message)

            if agent_result.clarify_questions:
                st.markdown("**Try asking:**")
                for q in agent_result.clarify_questions:
                    st.markdown(f"- {q}")

            if agent_result.sql:
                with st.expander("Generated SQL (evidence)", expanded=False):
                    st.code(agent_result.sql, language="sql")

            if agent_result.success and getattr(agent_result, "chart_fig", None) is not None:
                chart_label = (agent_result.chart_type or "chart").title()
                reason = agent_result.chart_reason or ""
                st.markdown(f"#### Chart · {chart_label}")
                if reason:
                    st.caption(reason)
                st.plotly_chart(agent_result.chart_fig, width="stretch")

            fdf = getattr(agent_result, "forecast_df", None)
            if fdf is not None and agent_result.success:
                with st.expander("Forecast values (future periods)", expanded=False):
                    st.dataframe(fdf, width="stretch", hide_index=True)

            anoms = getattr(agent_result, "anomalies", None) or []
            if anoms:
                with st.expander(f"Anomaly flags ({len(anoms)})", expanded=False):
                    st.dataframe(anoms, width="stretch", hide_index=True)

            if agent_result.result_df is not None and agent_result.success:
                with st.expander("Data table", expanded=getattr(agent_result, "chart_fig", None) is None):
                    st.dataframe(agent_result.result_df, width="stretch", hide_index=True)

            if agent_result.insight:
                st.markdown("#### Insight")
                st.markdown(agent_result.insight)

            if agent_result.warnings:
                for w in agent_result.warnings:
                    st.warning(w)

            if agent_result.error and not agent_result.message:
                st.error(agent_result.error)

            with st.expander("Agent pipeline steps", expanded=False):
                if agent_result.steps:
                    st.code(" → ".join(agent_result.steps))
                else:
                    st.caption("No steps recorded.")
                if agent_result.provider:
                    st.caption(f"Provider: {agent_result.provider} · Model: {agent_result.model or '—'}")

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
