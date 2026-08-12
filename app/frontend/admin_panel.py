"""
InsightForgeAI – Streamlit Admin panel (Phase 4.5)

Schedules, saved insights, audit log, usage metrics.
Imported by app.frontend.app to keep the main UI file lean.
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Optional

import pandas as pd
import streamlit as st


def render_admin_tab(
    *,
    workspace_id: str,
    selected_table: Optional[str],
    workspace: Any,
    run_agent,
    sched_mod,
    sec_mod,
    obs_mod,
) -> None:
    st.markdown("#### Enterprise admin")
    st.caption("Schedules · audit log · usage metrics · saved insights")
    st.markdown(f"**Workspace:** `{workspace_id}`")

    if sched_mod is None:
        st.warning("Scheduling module not available.")
        return

    sub_sched, sub_insights, sub_audit, sub_usage = st.tabs(
        ["⏰ Schedules", "💡 Saved insights", "📋 Audit", "📊 Usage"]
    )

    with sub_sched:
        st.markdown("##### Create schedule")
        c1, c2 = st.columns(2)
        with c1:
            s_name = st.text_input("Name", value="Daily revenue by region", key="sch_name")
            s_q = st.text_input("Question", value="total revenue by region", key="sch_q")
            s_table = st.text_input("Table", value=selected_table or "", key="sch_table")
            s_kind = st.selectbox("Kind", ["question", "dashboard"], key="sch_kind")
        with c2:
            s_channel = st.selectbox("Channel", ["log", "slack", "email"], key="sch_ch")
            s_daily = st.text_input("Daily at (UTC HH:MM)", value="08:00", key="sch_daily")
            s_interval = st.number_input(
                "Or interval minutes (0 = use daily)", min_value=0, value=0, key="sch_int"
            )
            s_webhook = st.text_input("Slack webhook override (optional)", key="sch_wh")
            s_email = st.text_input("Email to (optional)", key="sch_em")

        if st.button("Create schedule", type="primary", key="sch_create"):
            try:
                kwargs = dict(
                    name=s_name,
                    workspace_id=workspace_id,
                    kind=s_kind,
                    question=s_q,
                    table_name=s_table,
                    channel=s_channel,
                    webhook_url=s_webhook or None,
                    email_to=s_email or None,
                    created_by="streamlit-admin",
                )
                if s_interval and int(s_interval) > 0:
                    kwargs["interval_minutes"] = int(s_interval)
                else:
                    kwargs["daily_at"] = s_daily or "08:00"
                sch = sched_mod.create_schedule(**kwargs)
                st.success(f"Created schedule `{sch.id}` · next run {sch.next_run_at}")
                st.rerun()
            except Exception as e:
                st.error(str(e))

        st.markdown("##### Existing schedules")
        try:
            schedules = sched_mod.load_schedules(workspace_id)
        except Exception as e:
            schedules = []
            st.error(str(e))

        if not schedules:
            st.info("No schedules yet for this workspace.")

        for s in schedules:
            with st.container(border=True):
                st.markdown(f"**{s.name}** (`{s.id}`)")
                st.caption(
                    f"kind={s.kind} · channel={s.channel} · enabled={s.enabled} · "
                    f"status={s.last_status} · runs={s.run_count} · next={s.next_run_at}"
                )
                if s.question:
                    st.code(s.question, language=None)
                if s.last_error:
                    st.warning(s.last_error)
                b1, b2, b3 = st.columns(3)
                with b1:
                    if st.button("Run now", key=f"run_{s.id}"):
                        try:
                            res = sched_mod.run_schedule(
                                s,
                                workspace=workspace,
                                orchestrator_run=run_agent,
                            )
                            if res.get("success"):
                                st.success("Run ok")
                            else:
                                st.error(res.get("error") or "failed")
                            with st.expander("Summary"):
                                st.text(res.get("summary") or "")
                        except Exception as e:
                            st.error(str(e))
                with b2:
                    if st.button("Toggle enable", key=f"en_{s.id}"):
                        sched_mod.update_schedule(workspace_id, s.id, enabled=not s.enabled)
                        st.rerun()
                with b3:
                    if st.button("Delete", key=f"del_{s.id}"):
                        sched_mod.delete_schedule(workspace_id, s.id)
                        st.rerun()

    with sub_insights:
        st.markdown("##### Save insight")
        in_name = st.text_input("Insight name", key="ins_name")
        in_q = st.text_input("Question", key="ins_q")
        if st.button("Save insight", key="ins_save") and in_name and in_q:
            try:
                ins = sched_mod.SavedInsight(
                    id=str(uuid.uuid4())[:8],
                    name=in_name,
                    question=in_q,
                    table_name=selected_table or "",
                    workspace_id=workspace_id,
                    created_by="streamlit",
                )
                sched_mod.add_insight(ins)
                st.success(f"Saved `{ins.id}`")
                st.rerun()
            except Exception as e:
                st.error(str(e))

        for ins in sched_mod.load_insights(workspace_id):
            with st.container(border=True):
                st.markdown(f"**{ins.name}** · `{ins.table_name}`")
                st.caption(ins.question)
                if st.button("Delete insight", key=f"di_{ins.id}"):
                    sched_mod.remove_insight(workspace_id, ins.id)
                    st.rerun()

    with sub_audit:
        if sec_mod is None:
            st.warning("Security module not loaded.")
        else:
            limit = st.slider("Events", 20, 500, 100, key="aud_lim")
            events = sec_mod.read_audit(limit=limit)
            st.caption(f"{len(events)} event(s)")
            if events:
                st.dataframe(pd.DataFrame(events), use_container_width=True, hide_index=True)
            else:
                st.info("No audit events yet.")

    with sub_usage:
        if obs_mod is None:
            st.warning("Observability module not loaded.")
        else:
            snap = obs_mod.METRICS.snapshot()
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Requests", snap.get("requests_total", 0))
            c2.metric("Queries", snap.get("queries_total", 0))
            c3.metric("Schedule runs", snap.get("schedule_runs", 0))
            c4.metric("Schedule fails", snap.get("schedule_failures", 0))
            st.json(snap)
