"""
Drop-in Streamlit sidebar fragment for Phase 4.1 Live Connectors.

Paste inside the sidebar section of app/frontend/app.py
(after the file uploader block, before dataset_names = ...).

Requires the connectors package to be on PYTHONPATH (normal when running
from project root).
"""

# ---------------------------------------------------------------------------
# Phase 4.1 – Live Data Connectors (Postgres / MySQL)
# ---------------------------------------------------------------------------
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
                # Keep config (password only in session) for the load step
                st.session_state["_conn_config"] = cfg
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)
            except Exception as e:
                st.error(str(e))

    # Show previous health message if any
    if st.session_state.get("_conn_msg") and not test_clicked:
        ok, msg = st.session_state["_conn_msg"]
        (st.success if ok else st.error)(msg)

    tables = st.session_state.get("_conn_tables") or []
    if tables:
        st.markdown(f"**Tables** ({len(tables)})")
        # Build labels
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
        # Map labels back to table dicts
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
                                # Persist like file upload
                                try:
                                    _persist_dataset(r["dataset_name"])
                                except Exception:
                                    pass
                            else:
                                st.error(f"{r.get('table')}: {r.get('error')}")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
