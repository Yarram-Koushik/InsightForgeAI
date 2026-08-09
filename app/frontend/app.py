import streamlit as st
import pandas as pd
import numpy as np
import duckdb
import warnings
import sys
from pathlib import Path
import importlib.util
# Make sure the project root is in the path
current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent.parent
ingestion_path = project_root / "app" / "core" / "ingestion.py"

spec = importlib.util.spec_from_file_location("ingestion", ingestion_path)
ingestion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingestion)

# ---------- Load schema module ----------
schema_path = project_root / "app" / "core" / "schema.py"
spec_schema = importlib.util.spec_from_file_location("schema", schema_path)
schema_module = importlib.util.module_from_spec(spec_schema)
spec_schema.loader.exec_module(schema_module)

detect_schema_semantic = schema_module.detect_schema_semantic

# ---------- Load cleaning module ----------
cleaning_path = project_root / "app" / "core" / "cleaning.py"
spec_cleaning = importlib.util.spec_from_file_location("cleaning", cleaning_path)
cleaning_module = importlib.util.module_from_spec(spec_cleaning)
spec_cleaning.loader.exec_module(cleaning_module)

detect_cleaning_issues = cleaning_module.detect_cleaning_issues

# ---------- Load data manager ----------
data_manager_path = project_root / "app" / "core" / "data_manager.py"
spec_dm = importlib.util.spec_from_file_location("data_manager", data_manager_path)
dm_module = importlib.util.module_from_spec(spec_dm)
spec_dm.loader.exec_module(dm_module)

# ---------- Load profiling module ----------
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
# -----------------------------------------------------------

warnings.filterwarnings("ignore", message="Could not infer format")
st.set_page_config(
    page_title="InsightForgeAI",
    page_icon="📊",
    layout="wide"
)

st.title("InsightForgeAI")
st.markdown("### AI-Powered Business Intelligence Assistant")
st.caption("Phase 1 – Industry Upgrade in Progress")
st.markdown("---")

# ====================== HELPER FUNCTIONS (Temporary - will move later) ======================




def clean_data(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    report = {
        "duplicate_rows_removed": 0,
        "missing_values_filled": 0,
        "datetime_columns_converted": [],
        "columns_dropped": []
    }

    df_clean = df.copy()

    empty_cols = [col for col in df_clean.columns if df_clean[col].isna().all()]
    if empty_cols:
        df_clean = df_clean.drop(columns=empty_cols)
        report["columns_dropped"] = empty_cols

    before = len(df_clean)
    df_clean = df_clean.drop_duplicates()
    report["duplicate_rows_removed"] = before - len(df_clean)

    for col in df_clean.columns:
        if df_clean[col].dtype == "object":
            try:
                converted = pd.to_datetime(df_clean[col], errors="coerce")
                if converted.notna().mean() > 0.7:
                    df_clean[col] = converted
                    report["datetime_columns_converted"].append(col)
            except Exception:
                pass

    for col in df_clean.columns:
        missing_count = df_clean[col].isna().sum()
        if missing_count == 0:
            continue

        if pd.api.types.is_numeric_dtype(df_clean[col]):
            df_clean[col] = df_clean[col].fillna(df_clean[col].median())
            report["missing_values_filled"] += missing_count
        elif pd.api.types.is_datetime64_any_dtype(df_clean[col]):
            pass
        else:
            

            try:
                mode_val = df_clean[col].mode(dropna=True)
                fill_value = mode_val.iloc[0] if not mode_val.empty else "Unknown"
            except Exception:
                fill_value = "Unknown"
            df_clean[col] = df_clean[col].fillna(fill_value)
            report["missing_values_filled"] += missing_count

    return df_clean, report


def generate_data_profile(df: pd.DataFrame) -> dict:
    total_cells = df.shape[0] * df.shape[1]
    missing_cells = int(df.isna().sum().sum())
    duplicate_rows = int(df.duplicated().sum())

    completeness = ((total_cells - missing_cells) / total_cells) * 100 if total_cells > 0 else 0
    uniqueness = ((len(df) - duplicate_rows) / len(df)) * 100 if len(df) > 0 else 0
    quality_score = round((completeness * 0.6) + (uniqueness * 0.4), 1)

    numerical_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    numerical_profile = []
    for col in numerical_cols:
        numerical_profile.append({
            "Column": col,
            "Min": round(df[col].min(), 2) if not df[col].isna().all() else None,
            "Max": round(df[col].max(), 2) if not df[col].isna().all() else None,
            "Mean": round(df[col].mean(), 2) if not df[col].isna().all() else None,
            "Median": round(df[col].median(), 2) if not df[col].isna().all() else None,
            "Std Dev": round(df[col].std(), 2) if not df[col].isna().all() else None,
            "Missing %": round((df[col].isna().sum() / len(df)) * 100, 2)
        })

    categorical_cols = df.select_dtypes(include=["object", "string", "category", "bool"]).columns.tolist()
    categorical_profile = []
    for col in categorical_cols:
        top_values = df[col].value_counts().head(3).to_dict()
        categorical_profile.append({
            "Column": col,
            "Unique Values": df[col].nunique(),
            "Top Values": str(top_values),
            "Missing %": round((df[col].isna().sum() / len(df)) * 100, 2)
        })

    return {
        "quality_score": quality_score,
        "completeness": round(completeness, 1),
        "uniqueness": round(uniqueness, 1),
        "total_rows": len(df),
        "total_columns": len(df.columns),
        "memory_mb": round(df.memory_usage(deep=True).sum() / (1024 ** 2), 2),
        "numerical_profile": pd.DataFrame(numerical_profile),
        "categorical_profile": pd.DataFrame(categorical_profile)
    }


# ====================== SESSION STATE ======================
if "workspace" not in st.session_state:
    st.session_state.workspace = Workspace()
if "excel_sheets_cache" not in st.session_state:
    st.session_state.excel_sheets_cache = {}

# ====================== SIDEBAR ======================
st.sidebar.title("Workspace")
st.sidebar.markdown("Upload files. Excel files will show all sheets.")

uploaded_files = st.sidebar.file_uploader(
    "Upload files",
    type=["csv", "xlsx", "xls", "json", "parquet"],
    accept_multiple_files=True
)

# ====================== PROCESS UPLOADS ======================
# ====================== PROCESS UPLOADS ======================
if uploaded_files:
    for uploaded_file in uploaded_files:
        file_key = uploaded_file.name

        # ---------- Excel Special Handling ----------
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

                            # Create dataset record (keeps Raw data)
                            final_name = st.session_state.workspace.add_dataset(
                                name=table_name,
                                raw_df=raw_df,
                                source_filename=uploaded_file.name
                            )

                            # Detect + Safe clean
                            issues = detect_cleaning_issues(raw_df)
                            cleaned_df, change_log = apply_safe_cleaning(raw_df, issues)

                            # Apply cleaning to the record (keeps lineage)
                            record = st.session_state.workspace.get(final_name)
                            record.apply_cleaning(cleaned_df, issues, change_log)

                            st.sidebar.success(f"Loaded: {final_name}")

                        except Exception as e:
                            st.sidebar.error(f"Failed to load sheet: {e}")

        # ---------- Other file types ----------
        else:
            table_name = make_safe_table_name(uploaded_file.name)

            if table_name not in st.session_state.workspace.list_datasets():
                try:
                    raw_df = read_file(uploaded_file)

                    final_name = st.session_state.workspace.add_dataset(
                        name=table_name,
                        raw_df=raw_df,
                        source_filename=uploaded_file.name
                    )

                    issues = detect_cleaning_issues(raw_df)
                    cleaned_df, change_log = apply_safe_cleaning(raw_df, issues)

                    record = st.session_state.workspace.get(final_name)
                    record.apply_cleaning(cleaned_df, issues, change_log)

                    st.sidebar.success(f"Loaded: {final_name}")

                except Exception as e:
                    st.sidebar.error(f"Error: {e}")

# Dataset selector
dataset_names = st.session_state.workspace.list_datasets()

if dataset_names:
    selected_table = st.sidebar.selectbox("Select Dataset", options=dataset_names)
else:
    selected_table = None
    st.sidebar.info("No datasets loaded yet.")
# ====================== MAIN AREA ======================
if selected_table:
    record = st.session_state.workspace.get(selected_table)

    st.markdown(f"### Dataset: `{record.name}`")
    st.caption(f"Source: {record.source_filename} | ID: {record.id}")

    # Metrics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Raw Rows", f"{record.metadata['original_rows']:,}")
    c2.metric("Cleaned Rows", f"{record.metadata.get('cleaned_rows', record.metadata['original_rows']):,}")
    c3.metric("Columns", record.metadata["original_columns"])
    c4.metric("Issues Found", len(record.issues))

    # View selector
    view_mode = st.radio("View Mode", ["Cleaned Data", "Raw Data"], horizontal=True)

    if view_mode == "Cleaned Data":
        df = record.cleaned_df
    else:
        df = record.raw_df

    tab1, tab2, tab3, tab4 = st.tabs(["Schema", "Cleaning & Lineage", "Data Profile", "Data Preview"])

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
            issues_df = pd.DataFrame(record.issues)
            st.dataframe(issues_df, width="stretch", hide_index=True)
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

        # Overall Score with color
        score = quality["overall_score"]
        if score >= 85:
            color = "green"
        elif score >= 70:
            color = "orange"
        else:
            color = "red"

        st.markdown(f"### Overall Quality Score: :{color}[{score}/100]")

        # Dimension scores
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Completeness", f"{quality['completeness']['score']}%", 
                help=quality['completeness']['details'])
        c2.metric("Uniqueness", f"{quality['uniqueness']['score']}%", 
                help=quality['uniqueness']['details'])
        c3.metric("Validity", f"{quality['validity']['score']}%", 
                help=quality['validity']['details'])
        c4.metric("Memory", f"{quality['memory_mb']} MB")

        # Explanation
        with st.expander("How is the Quality Score calculated?"):
            st.markdown("""
            **Weighted Score:**
            - Completeness → 50% weight
            - Uniqueness → 30% weight
            - Validity → 20% weight

            **Completeness**: Percentage of non-missing values  
            **Uniqueness**: Percentage of non-duplicate rows  
            **Validity**: Basic checks for negative values in amount/quantity columns and unusually long text
            """)

        # Validity problems
        if quality["validity"]["problems"]:
            st.markdown("##### Potential Validity Issues")
            for problem in quality["validity"]["problems"]:
                st.warning(problem)

        # Column level profile
        st.markdown("##### Column-Level Profile")
        col_profile = column_level_profile(df)
        st.dataframe(col_profile, width="stretch", hide_index=True)

    with tab4:
        st.dataframe(df.head(30), width="stretch")

else:
    st.info("Upload files from the sidebar to get started.")