"""Streamlit review queue: a person confirms or dismisses each proposed flag.

    streamlit run src/invoice_agent/ui/app.py

The page shows each flag with its evidence next to it (the invoice line, the agreed price or
delivery note or VAT rule, and the contract clause quoted word for word). Deciding needs a
name, and a decision is final. Nothing here pays, approves or rejects an invoice.
"""

from __future__ import annotations

import os

import streamlit as st

from invoice_agent.ui.review_queue import counts, decide, open_queue, queue

DB_PATH = os.environ.get("INVOICE_AGENT_DB", "data/invoice_agent.db")
STATUSES = {
    "Pending": "pending_review",
    "Confirmed": "approved",
    "Dismissed": "rejected",
    "All": None,
}

st.set_page_config(page_title="Invoice review queue", layout="wide")
st.title("Invoice review queue")
st.caption(
    "Flags proposed by the pipeline from synthetic supplier invoices. You confirm or dismiss "
    "each one; nothing here pays, approves or rejects an invoice."
)

try:
    repo = open_queue(DB_PATH)
except FileNotFoundError as error:
    st.error(str(error))
    st.stop()

with st.sidebar:
    reviewer = st.text_input("Your name (signs every decision)", key="reviewer")
    shown = st.radio("Show", list(STATUSES), key="status")
    n = counts(repo)
    st.metric("Pending", n["pending_review"])
    st.write(f"Confirmed {n['approved']} · Dismissed {n['rejected']}")
    st.caption(f"Database: {DB_PATH}")

if "notice" in st.session_state:
    st.success(st.session_state.pop("notice"))

flags = queue(repo, STATUSES[shown])
if not flags:
    st.info("Nothing here. Pending flags appear after invoices run through the pipeline.")

for view in flags:
    with st.container(border=True):
        st.subheader(f"#{view.flag_id} · {view.type_label}")
        st.write(f"**{view.invoice_id}** from {view.supplier} — {view.message}")
        left, right = st.columns(2)
        with left:
            if view.line:
                st.markdown("**Invoice line**")
                st.table({k: [str(v)] for k, v in view.line.items()})
            if view.explanation:
                st.markdown("**Agent explanation**")
                st.write(view.explanation)
        with right:
            st.markdown(f"**{view.evidence_title}**")
            if view.evidence:
                st.table({k: [str(v)] for k, v in view.evidence.items()})
            if view.quote:
                st.markdown(f"**Contract {view.clause_ref}** (quoted word for word)")
                st.markdown(f"> {view.quote}")

        if view.status == "pending_review":
            confirm_col, dismiss_col, _ = st.columns([2, 2, 1])
            pressed = None
            if confirm_col.button(
                "Confirm anomaly", key=f"confirm-{view.flag_id}", disabled=not reviewer.strip()
            ):
                pressed = True
            if dismiss_col.button(
                "Dismiss flag", key=f"dismiss-{view.flag_id}", disabled=not reviewer.strip()
            ):
                pressed = False
            if not reviewer.strip():
                st.caption("Enter your name in the sidebar to decide.")
            if pressed is not None:
                try:
                    st.session_state["notice"] = decide(repo, view.flag_id, pressed, reviewer)
                except ValueError as error:
                    st.error(str(error))
                else:
                    st.rerun()
        else:
            verb = "Confirmed" if view.status == "approved" else "Dismissed"
            st.write(f"{verb} by **{view.reviewer}**")

with st.expander("Audit log (latest 20 events)"):
    for event in reversed(repo.audit_log()[-20:]):
        st.write(f"`{event['at']}` **{event['actor']}** {event['action']} {event['detail']}")
