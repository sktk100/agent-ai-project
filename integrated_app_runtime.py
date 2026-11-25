"""Shared runtime for the two HEALTH QUEST integrated deployments."""

import streamlit as st

from firebase_integration import (
    load_firebase_settings,
    persist_current_learning_state,
    require_authenticated_identity,
)
from quest_engine import get_current_scenario_data, get_scenario_progress, init_quest_state


def run_integrated_app() -> None:
    """Render the integrated mission-room and simulation application."""
    st.set_page_config(
        page_title="HEALTH QUEST — 통합 간호 PBL",
        page_icon=":material/clinical_notes:",
        layout="wide",
    )

    firebase_settings = load_firebase_settings()
    identity = require_authenticated_identity(firebase_settings)

    st.session_state["hq_integrated_mode"] = True
    init_quest_state()

    scenario = get_current_scenario_data()
    completed, total, _ = get_scenario_progress(scenario)
    simulation_unlocked = total > 0 and completed == total

    pages = [
        st.Page(
            "app.py",
            title="간호 미션룸",
            icon=":material/clinical_notes:",
            default=True,
        )
    ]

    if firebase_settings.enabled and identity is not None and identity.role == "student":
        pages.append(
            st.Page(
                "student_submission.py",
                title="수행평가 제출",
                icon=":material/upload_file:",
            )
        )

    if firebase_settings.enabled and identity is not None and identity.role == "teacher":
        pages.append(
            st.Page(
                "teacher_dashboard.py",
                title="교사 대시보드",
                icon=":material/dashboard:",
            )
        )

    if simulation_unlocked:
        pages.append(
            st.Page(
                "app_rag_pbl.py",
                title="모의상황실습",
                icon=":material/biotech:",
            )
        )

    navigation = st.navigation(pages, position="top")
    navigation.run()
    persist_current_learning_state(firebase_settings, identity)
