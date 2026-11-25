"""Run: streamlit run app_rag_pbl.py

HEALTH QUEST — RAG PBL 실습실
기존 app.py와 독립적으로 배포하는 질문 기반 단일 에이전트 학습 앱.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import streamlit as st
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from quest_data import UNIT_CATALOG
from rag_pbl_single_agent import (
    DiscoveryResult,
    PBLReview,
    ScenarioCandidate,
    generate_scenarios,
    guard_student_text,
    rag_status,
    review_pbl_answer,
)


st.set_page_config(
    page_title="HEALTH QUEST — RAG PBL 실습실",
    page_icon=":material/clinical_notes:",
    layout="wide",
)

st.html(
    """
    <style>
    .st-key-ragpbl_hero {
        background: linear-gradient(135deg, #eaf2ff 0%, #dce9ff 58%, #f3f7ff 100%);
        border: 1px solid #bfd2f2;
        border-radius: 24px;
        box-shadow: 0 14px 34px rgba(60, 91, 143, 0.10);
        padding: 1.9rem 2rem 1.7rem;
    }

    .st-key-ragpbl_hero h1 {
        color: #19365f;
        font-size: clamp(2rem, 3vw, 3.15rem);
        letter-spacing: -0.035em;
        line-height: 1.18;
    }

    .st-key-ragpbl_hero p {
        color: #314b70;
    }

    .st-key-ragpbl_question_help details {
        background: linear-gradient(135deg, #fdebe6 0%, #f9ddd7 100%);
        border: 1px solid #efbfb4;
        border-radius: 16px;
        box-shadow: 0 8px 20px rgba(151, 82, 68, 0.08);
        overflow: hidden;
    }

    .st-key-ragpbl_question_help details > summary {
        color: #6f3d35;
        font-weight: 700;
        letter-spacing: -0.015em;
    }

    .st-key-ragpbl_discovery_form {
        background: rgba(255, 253, 249, 0.92);
        border-color: #d8e2ef;
        border-radius: 20px;
        box-shadow: 0 10px 28px rgba(40, 57, 83, 0.06);
    }

    @media (max-width: 700px) {
        .st-key-ragpbl_hero {
            border-radius: 18px;
            padding: 1.3rem 1.15rem 1.2rem;
        }
    }
    </style>
    """
)


STATE_DEFAULTS = {
    "ragpbl_discovery": None,
    "ragpbl_selected_id": None,
    "ragpbl_review": None,
    "ragpbl_attempts": 0,
    "ragpbl_source_mode": "",
    "ragpbl_last_unit": "",
    "ragpbl_questions": [],
    "ragpbl_prior_knowledge": "",
    "ragpbl_last_error": "",
}
for state_key, default in STATE_DEFAULTS.items():
    st.session_state.setdefault(state_key, default)


def unit_display_name(unit_name: str) -> str:
    if unit_name.startswith("[") and "] " in unit_name:
        return unit_name.split("] ", 1)[1]
    return unit_name


def reset_learning() -> None:
    for state_key, default in STATE_DEFAULTS.items():
        st.session_state[state_key] = default


def fallback_discovery(unit_name: str, questions: list[str], prior_knowledge: str) -> DiscoveryResult:
    unit = UNIT_CATALOG[unit_name]
    combined = " ".join([*questions, prior_knowledge])
    reasoning_words = ("왜", "차이", "근거", "우선", "관찰", "평가", "보고", "때문")
    reasoning_count = sum(combined.count(word) for word in reasoning_words)
    if len(prior_knowledge) >= 150 and reasoning_count >= 4:
        level = "심화"
        level_reason = "여러 개념을 임상 단서·우선순위·평가와 연결해 질문하고 있습니다."
    elif len(prior_knowledge) >= 60 or reasoning_count >= 2:
        level = "발전"
        level_reason = "핵심 개념을 알고 있으며 실제 상황에 적용하려는 질문이 나타납니다."
    else:
        level = "기초"
        level_reason = "핵심 용어의 뜻과 기본 적용부터 확인하면 다음 판단이 더 쉬워집니다."

    source_scenarios = unit.get("scenarios", [])
    missions = source_scenarios[0].get("missions", []) if source_scenarios else []
    candidates: list[ScenarioCandidate] = []
    for index, mission in enumerate(missions[:3]):
        candidates.append(
            ScenarioCandidate(
                id=("A", "B", "C")[index],
                title=str(mission.get("title", f"모의실습 {index + 1}")).replace("[기초] ", "").replace("[중급] ", "").replace("[심화] ", ""),
                hospital_situation=mission.get("situation", unit.get("summary", "가상 병동 상황")),
                why_this_fits=f"학생 질문과 {mission.get('learning_objective', '단원 학습목표')}를 연결해 생각할 수 있습니다.",
                concepts_to_use=list(mission.get("required_keywords", []))[:5] or list(unit.get("key_terms", {}).keys())[:4],
                clinical_clues=list(mission.get("clinical_clues", []))[:4],
                pbl_task=mission.get("question", "환자 안전을 위한 우선순위 해결안을 근거와 함께 작성하세요."),
                evidence_summary=mission.get("reference_source", "현재 단원 자료 — 교사 검토 필요"),
            )
        )
    while len(candidates) < 3:
        index = len(candidates)
        candidates.append(
            ScenarioCandidate(
                id=("A", "B", "C")[index],
                title=f"{unit_display_name(unit_name)} 적용 상황 {index + 1}",
                hospital_situation=unit.get("summary", "가상의 병동에서 환자 안전 문제를 확인하는 상황입니다."),
                why_this_fits="현재 단원의 핵심 개념을 병원과 유사한 상황에 적용할 수 있습니다.",
                concepts_to_use=list(unit.get("key_terms", {}).keys())[:4],
                clinical_clues=["환자의 호소", "관찰 가능한 변화", "안전과 보고 원칙"],
                pbl_task="문제를 정의하고 가장 안전한 해결 순서와 평가 방법을 작성하세요.",
                evidence_summary="현재 단원 수업 자료 — 교사 검토 필요",
            )
        )
    return DiscoveryResult(
        knowledge_level=level,
        level_reason=level_reason,
        question_summary=" / ".join(questions),
        key_concepts=list(unit.get("key_terms", {}).keys())[:5],
        scenarios=candidates,
        safety_note="가상 상황에서 관찰·보고·비침습적 안전 간호 범위로 해결하세요.",
        teacher_review_needed=True,
    )


def fallback_review(answer_parts: list[str]) -> PBLReview:
    lengths = [len(part.strip()) for part in answer_parts]
    score = lambda length: 3 if length >= 100 else 2 if length >= 50 else 1 if length >= 20 else 0
    return PBLReview(
        knowledge_alignment="현재 답안은 단원 자료와 연결해 교사가 확인해야 하는 시연 모드 결과입니다.",
        strengths=["문제·판단·해결·평가를 나누어 PBL 답안을 작성했습니다."],
        evidence_gaps=["임상 단서와 해결 행동이 왜 연결되는지 수업 용어를 사용해 한 문장 더 설명해 보세요."],
        safety_feedback="실제 처치 지시가 아닌 가상 상황의 관찰·보고·비침습적 안전 간호 범위를 유지하세요.",
        coaching_questions=["가장 먼저 확인해야 할 단서는 무엇이며, 그 단서가 우선순위를 바꾸는 이유는 무엇인가요?"],
        revision_task="피드백을 반영해 근거와 평가 기준을 각각 한 문장 이상 보완하세요.",
        concept_score=score(lengths[3]),
        reasoning_score=score(lengths[1]),
        evidence_score=score(lengths[2]),
        reflection_score=score(lengths[4]),
        teacher_review_needed=True,
        teacher_review_reason="RAG 연결 없이 현재 단원 데이터로 생성한 시연 피드백이므로 교사 확인이 필요합니다.",
    )


def selected_scenario() -> ScenarioCandidate | None:
    discovery = st.session_state.get("ragpbl_discovery")
    selected_id = st.session_state.get("ragpbl_selected_id")
    if not discovery or not selected_id:
        return None
    return next((item for item in discovery.scenarios if item.id == selected_id), None)


def build_excel_report(
    learner_code: str,
    group_name: str,
    subject: str,
    unit_name: str,
    discovery: DiscoveryResult,
    scenario: ScenarioCandidate,
    review: PBLReview,
) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "RAG PBL 학습기록"
    rows = [
        ("학습자 코드", learner_code or "미입력"),
        ("모둠", group_name or "미입력"),
        ("교과", subject),
        ("단원", unit_display_name(unit_name)),
        ("지식수준", f"{discovery.knowledge_level} — {discovery.level_reason}"),
        ("학생 질문", " / ".join(st.session_state.get("ragpbl_questions", []))),
        ("선택 상황", f"{scenario.id}. {scenario.title}"),
        ("가상 병원 상황", scenario.hospital_situation),
        ("PBL 과제", scenario.pbl_task),
        ("문제 정의와 핵심 단서", st.session_state.get("ragpbl_problem", "")),
        ("가설과 판단", st.session_state.get("ragpbl_hypothesis", "")),
        ("우선순위 해결안", st.session_state.get("ragpbl_action_plan", "")),
        ("수업 지식에 근거한 이유", st.session_state.get("ragpbl_evidence", "")),
        ("평가 방법과 성찰", st.session_state.get("ragpbl_evaluation", "")),
        ("AI 코칭 — 잘한 점", " / ".join(review.strengths)),
        ("AI 코칭 — 보완할 근거", " / ".join(review.evidence_gaps)),
        ("다음 수정 과제", review.revision_task),
        ("교사 검토", review.teacher_review_reason if review.teacher_review_needed else "현재 추가 검토 표시 없음"),
    ]
    sheet.append(["HEALTH QUEST — RAG PBL 실습 기록", ""])
    for label, value in rows:
        sheet.append([label, value])
    sheet["A1"].font = Font(bold=True, size=15, color="17324D")
    sheet["A1"].fill = PatternFill("solid", fgColor="DCECE5")
    sheet["B1"].fill = PatternFill("solid", fgColor="DCECE5")
    for row in range(2, sheet.max_row + 1):
        sheet.cell(row, 1).font = Font(bold=True, color="17324D")
        sheet.cell(row, 1).fill = PatternFill("solid", fgColor="EEF5F1")
        sheet.cell(row, 2).alignment = Alignment(wrap_text=True, vertical="top")
    sheet.column_dimensions["A"].width = 28
    sheet.column_dimensions["B"].width = 100
    sheet.freeze_panes = "A2"
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


status = rag_status()
subjects = sorted({unit.get("subject", "보건간호") for unit in UNIT_CATALOG.values()})

with st.sidebar:
    st.header("RAG PBL 실습실", icon=":material/clinical_notes:")
    st.caption("질문에서 시작하는 단일 AI 보건튜터")

    subject = st.selectbox("교과목", subjects, key="ragpbl_subject")
    units = [name for name, value in UNIT_CATALOG.items() if value.get("subject") == subject]
    unit_name = st.selectbox("실습 단원", units, format_func=unit_display_name, key="ragpbl_unit")

    if st.session_state.ragpbl_last_unit and st.session_state.ragpbl_last_unit != unit_name:
        reset_learning()
    st.session_state.ragpbl_last_unit = unit_name

    if not status["configured"]:
        st.warning(
            "서버 RAG 설정이 없어 현재 단원 데이터 시연 모드로 실행됩니다.",
            icon=":material/warning:",
        )

    if st.button("새 학습 시작", icon=":material/restart_alt:", width="stretch"):
        reset_learning()
        st.rerun()

    st.info(
        "현재 앱은 세션 안에서만 기록됩니다. 완료 후 Excel을 내려받아 선생님이 안내한 제출처에 올리세요.",
        icon=":material/info:",
    )
    st.caption("실제 환자 정보, 학생의 건강정보, 전화번호·이메일·주민등록번호는 입력하지 마세요.")


discovery: DiscoveryResult | None = st.session_state.ragpbl_discovery
scenario = selected_scenario()
review: PBLReview | None = st.session_state.ragpbl_review
stage = 4 if review else 3 if scenario else 2 if discovery else 1

if st.session_state.get("hq_integrated_mode", False) and st.session_state.get("ragpbl_entry_source") == "mission_room":
    st.info(
        "간호 미션룸에서 완료한 교과목과 단원을 이어받았습니다. "
        "이제 배운 내용에서 궁금한 질문을 자신의 말로 적어 보세요.",
        icon=":material/route:",
    )

with st.container(key="ragpbl_hero"):
    st.title("HEALTH QUEST — RAG PBL 실습실")
    st.markdown("학생의 질문을 수업 근거와 연결해 나만의 임상 모의실습을 설계합니다.")
    st.caption("예상 소요시간 10~15분 · 질문 분석 → 상황 선택 → 서술형 PBL 해결 → 피드백과 재도전")
    st.progress(stage / 4, text=f"현재 {stage}/4단계")
    with st.container(horizontal=True):
        for index, label in enumerate(("질문·지식 확인", "상황 3개 비교", "PBL 서술", "피드백·성장"), start=1):
            color = "green" if index < stage else "blue" if index == stage else "gray"
            st.badge(label, color=color)


st.header("1. 내가 궁금한 질문과 현재 생각", icon=":material/edit_note:")
st.caption("질문의 어려운 단어보다 개념을 어떻게 연결하는지가 중요합니다. 두 질문은 필수이고 세 번째는 선택입니다.")

if not discovery:
    unit = UNIT_CATALOG[unit_name]
    samples = list(unit.get("sample_questions", []))[:3]
    with st.container(key="ragpbl_question_help"):
        with st.expander("질문을 만드는 데 도움이 필요하면 보기", icon=":material/lightbulb:"):
            for sample in samples:
                st.markdown(f"- {sample}")
            st.caption("예시는 참고만 하고, 궁금한 점을 자신의 말로 바꾸어 적으세요.")

    with st.form("ragpbl_discovery_form", border=True):
        question_1 = st.text_input(
            "질문 1",
            placeholder="예: 장갑을 벗은 뒤에도 손 위생이 필요한 이유는 무엇일까?",
            max_chars=300,
        )
        question_2 = st.text_input(
            "질문 2",
            placeholder="예: 환자 상황에서 어떤 단서를 먼저 확인해야 할까?",
            max_chars=300,
        )
        question_3 = st.text_input(
            "질문 3 — 선택",
            placeholder="추가로 궁금한 의학용어나 판단 질문이 있으면 적으세요.",
            max_chars=300,
        )
        prior_knowledge = st.text_area(
            "내가 현재 알고 있는 내용",
            placeholder="수업에서 배운 개념을 자신의 말로 2~4문장 적으세요. 잘 몰라도 괜찮습니다.",
            height=130,
            max_chars=1200,
        )
        analyze = st.form_submit_button(
            "질문 분석하고 모의실습 3개 만들기",
            icon=":material/psychology:",
            type="primary",
            width="stretch",
        )

    if analyze:
        questions = [item.strip() for item in (question_1, question_2, question_3) if item.strip()]
        if len(questions) < 2:
            st.error("서로 다른 질문을 두 개 이상 작성해 주세요.")
        elif len({item.casefold() for item in questions}) != len(questions):
            st.error("같은 질문을 반복하지 말고 서로 다른 궁금증을 작성해 주세요.")
        elif len(prior_knowledge.strip()) < 20:
            st.error("현재 알고 있는 내용을 자신의 말로 20자 이상 작성해 주세요.")
        else:
            guard = guard_student_text(" ".join([*questions, prior_knowledge]))
            if not guard.safe:
                st.error(guard.message)
            else:
                st.session_state.ragpbl_questions = questions
                st.session_state.ragpbl_prior_knowledge = prior_knowledge.strip()
                with st.spinner("교과서와 공공 보건의료 자료를 검색해 질문과 지식수준을 분석하고 있습니다..."):
                    try:
                        if not status["configured"]:
                            raise RuntimeError("RAG not configured")
                        result = generate_scenarios(
                            subject=subject,
                            unit=unit_display_name(unit_name),
                            questions=questions,
                            prior_knowledge=prior_knowledge.strip(),
                        )
                        source_mode = "교과서·공공자료 RAG"
                        st.session_state.ragpbl_last_error = ""
                    except Exception as exc:
                        result = fallback_discovery(unit_name, questions, prior_knowledge.strip())
                        source_mode = "현재 단원 데이터 시연"
                        st.session_state.ragpbl_last_error = type(exc).__name__
                st.session_state.ragpbl_discovery = result
                st.session_state.ragpbl_source_mode = source_mode
                st.rerun()
else:
    with st.container(border=True):
        st.badge(f"현재 지식수준: {discovery.knowledge_level}", color="green")
        st.markdown(discovery.level_reason)
        st.caption("이 표시는 질문 수가 아니라 개념 연결과 근거 표현을 바탕으로 한 학습 출발점이며, 성적 판정이 아닙니다.")
        st.markdown("핵심 개념: " + " · ".join(discovery.key_concepts))
        st.caption(f"생성 방식: {st.session_state.ragpbl_source_mode}")
        if st.session_state.ragpbl_last_error:
            st.warning(
                "RAG 호출을 완료하지 못해 단원 데이터 시연 결과를 표시했습니다. 서버의 API 키·Vector Store·사용량 설정을 확인하세요.",
                icon=":material/cloud_off:",
            )


if discovery:
    st.header("2. 모의실습 상황 3개 중 하나 선택", icon=":material/view_carousel:")
    st.caption("각 상황의 임상 단서와 PBL 과제를 비교한 뒤, 내가 가장 깊이 해결해 보고 싶은 상황을 고르세요.")
    cards = st.columns(3, gap="medium")
    for column, item in zip(cards, discovery.scenarios):
        with column.container(border=True, height="stretch"):
            st.badge(f"상황 {item.id}", color="blue")
            st.subheader(item.title)
            st.markdown(item.hospital_situation)
            st.caption("핵심 단서")
            for clue in item.clinical_clues:
                st.markdown(f"- {clue}")
            st.caption("연결할 개념: " + " · ".join(item.concepts_to_use))
            st.markdown(item.pbl_task)
            st.space("stretch")
            selected = st.session_state.ragpbl_selected_id == item.id
            if st.button(
                "선택됨" if selected else f"상황 {item.id} 선택",
                key=f"ragpbl_select_{item.id}",
                type="primary" if selected else "secondary",
                icon=":material/check_circle:" if selected else ":material/arrow_forward:",
                width="stretch",
            ):
                st.session_state.ragpbl_selected_id = item.id
                st.session_state.ragpbl_review = None
                st.rerun()


scenario = selected_scenario()
if scenario:
    st.header("3. 선택한 상황의 PBL 해결안 작성", icon=":material/edit_note:")
    with st.container(border=True):
        st.badge(f"선택 상황 {scenario.id}", color="green")
        st.subheader(scenario.title)
        st.markdown(scenario.hospital_situation)
        st.info(scenario.pbl_task, title="PBL 핵심 과제", icon=":material/target:")
        st.caption("자료 근거 요약: " + scenario.evidence_summary)

    st.caption("정답 문장을 찾기보다 임상 단서 → 판단 → 우선순위 → 근거 → 평가가 이어지도록 자신의 말로 작성하세요.")
    with st.form("ragpbl_answer_form", border=True):
        st.text_area(
            "1. 문제 정의와 핵심 단서",
            key="ragpbl_problem",
            placeholder="환자 상황에서 가장 중요한 문제와 관찰한 단서를 구분해 적으세요.",
            height=110,
            max_chars=1800,
        )
        st.text_area(
            "2. 나의 가설과 판단",
            key="ragpbl_hypothesis",
            placeholder="단서가 무엇을 의미한다고 판단했는지 적으세요.",
            height=110,
            max_chars=1800,
        )
        st.text_area(
            "3. 우선순위 해결안",
            key="ragpbl_action_plan",
            placeholder="학생이 수행할 수 있는 관찰·보고·비침습적 안전 간호를 우선순위대로 적으세요.",
            height=130,
            max_chars=2200,
        )
        st.text_area(
            "4. 수업에서 배운 지식에 근거한 이유",
            key="ragpbl_evidence",
            placeholder="어떤 단원 개념을 적용했고 왜 이 해결안이 필요한지 설명하세요.",
            height=130,
            max_chars=2200,
        )
        st.text_area(
            "5. 결과를 확인할 평가 방법과 나의 성찰",
            key="ragpbl_evaluation",
            placeholder="환자 상태가 좋아졌는지 무엇으로 확인할지, 다음에 보완할 점을 적으세요.",
            height=110,
            max_chars=1800,
        )
        submit_answer = st.form_submit_button(
            "내 PBL 해결안 제출하고 근거 피드백 받기",
            icon=":material/fact_check:",
            type="primary",
            width="stretch",
        )

    if submit_answer:
        answer_parts = [
            st.session_state.get("ragpbl_problem", ""),
            st.session_state.get("ragpbl_hypothesis", ""),
            st.session_state.get("ragpbl_action_plan", ""),
            st.session_state.get("ragpbl_evidence", ""),
            st.session_state.get("ragpbl_evaluation", ""),
        ]
        short_sections = [index + 1 for index, value in enumerate(answer_parts) if len(value.strip()) < 20]
        guard = guard_student_text(" ".join(answer_parts))
        if short_sections:
            st.error("자신의 생각이 드러나도록 모든 항목을 20자 이상 작성해 주세요. 보완할 항목: " + ", ".join(map(str, short_sections)))
        elif not guard.safe:
            st.error(guard.message)
        else:
            with st.spinner("수업 자료를 다시 검색해 답안의 개념·추론·근거·성찰을 확인하고 있습니다..."):
                try:
                    if not status["configured"]:
                        raise RuntimeError("RAG not configured")
                    result = review_pbl_answer(
                        subject=subject,
                        unit=unit_display_name(unit_name),
                        discovery=discovery,
                        scenario=scenario,
                        problem_definition=answer_parts[0],
                        hypothesis=answer_parts[1],
                        action_plan=answer_parts[2],
                        evidence=answer_parts[3],
                        evaluation=answer_parts[4],
                    )
                    st.session_state.ragpbl_last_error = ""
                except Exception as exc:
                    result = fallback_review(answer_parts)
                    st.session_state.ragpbl_last_error = type(exc).__name__
            st.session_state.ragpbl_review = result
            st.session_state.ragpbl_attempts += 1
            st.rerun()


review = st.session_state.ragpbl_review
scenario = selected_scenario()
if review and scenario and discovery:
    st.header("4. RAG 근거 피드백과 성장 기록", icon=":material/trending_up:")
    score_columns = st.columns(4)
    score_data = (
        ("개념 적용", review.concept_score),
        ("임상 추론", review.reasoning_score),
        ("근거 활용", review.evidence_score),
        ("평가·성찰", review.reflection_score),
    )
    for column, (label, value) in zip(score_columns, score_data):
        column.metric(label, f"{value}/3", border=True, height="stretch")

    feedback_columns = st.columns(2, gap="medium")
    with feedback_columns[0].container(border=True, height="stretch"):
        st.subheader("확인된 강점", icon=":material/check_circle:")
        for item in review.strengths:
            st.markdown(f"- {item}")
        st.markdown(review.knowledge_alignment)
    with feedback_columns[1].container(border=True, height="stretch"):
        st.subheader("다음에 보완할 근거", icon=":material/rate_review:")
        for item in review.evidence_gaps:
            st.markdown(f"- {item}")
        for item in review.coaching_questions:
            st.markdown(f"- 생각 질문: {item}")

    st.info(review.revision_task, title="재도전 과제", icon=":material/replay:")
    st.caption("안전 피드백: " + review.safety_feedback)
    if review.teacher_review_needed:
        st.warning(review.teacher_review_reason, title="교사 확인 필요", icon=":material/supervisor_account:")

    learner_columns = st.columns(2)
    with learner_columns[0]:
        learner_code = st.text_input(
            "학습자 코드 — 교사가 부여한 번호",
            key="ragpbl_learner_code",
            placeholder="예: 2-1-07",
            max_chars=30,
        )
    with learner_columns[1]:
        group_name = st.text_input(
            "모둠 이름 — 선택",
            key="ragpbl_group_name",
            placeholder="예: 나이팅게일 1모둠",
            max_chars=40,
        )
    st.caption("학습자 코드와 모둠 이름은 Excel 파일에만 포함되며 AI 분석에는 전송하지 않습니다.")

    with st.container(horizontal=True):
        if st.button("피드백 반영해 다시 작성", icon=":material/edit:", width="stretch"):
            st.session_state.ragpbl_review = None
            st.rerun()
        report_bytes = build_excel_report(
            learner_code,
            group_name,
            subject,
            unit_name,
            discovery,
            scenario,
            review,
        )
        st.download_button(
            "내 RAG PBL 학습기록 Excel 받기",
            data=report_bytes,
            file_name="HEALTH_QUEST_RAG_PBL_학습기록.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            icon=":material/download:",
            type="primary",
            width="stretch",
        )
    st.caption(f"피드백 받은 횟수: {st.session_state.ragpbl_attempts}회 · 다운로드 파일은 선생님이 안내한 제출처에 직접 제출합니다.")
