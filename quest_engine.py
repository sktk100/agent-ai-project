"""HEALTH QUEST — 스마트 디지털 보건간호 PBL 학습지: 상태 관리·진도율 계산·AI 보건튜터(질문/개인/팀)·교사 피드백 및 학생 제출물 관리 엔진."""
import csv
import io
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
import streamlit as st
from quest_ai import generate_grounded_feedback
from quest_data import UNIT_CATALOG

SUBMISSIONS_STORE_PATH = Path(__file__).resolve().parent / "submissions_store.json"
KST = ZoneInfo("Asia/Seoul")


def _now_kst() -> datetime:
    return datetime.now(KST)

BADGE_DEFINITIONS = {
    "question_explorer": {
        "id": "question_explorer",
        "title": "자기주도 질문 탐구자",
        "desc": "단원의 모르는 개념과 의학용어 질문을 2~3개 작성하고 AI 보건튜터 피드백으로 맞춤형 실습을 설계했습니다.",
        "competency": "자기주도 탐구 역량",
    },
    "first_mission": {
        "id": "first_mission",
        "title": "기초 모의실습 해결",
        "desc": "기초 모의실습 상황에서 핵심 단서를 파악하고 첫 임상 판단 및 개인 서술을 완료했습니다.",
        "competency": "기초 임상추론 역량",
    },
    "evidence_thinker": {
        "id": "evidence_thinker",
        "title": "중급 PBL 우선순위 추론가",
        "desc": "중급 모의실습에서 환자안전과 법적 업무 경계를 고려해 우선순위 근거를 논리적으로 서술했습니다.",
        "competency": "문제해결 역량",
    },
    "pbl_master": {
        "id": "pbl_master",
        "title": "기초·중급·심화 완전학습 달성",
        "desc": "기초·중급·심화 3단계 모의실습을 모두 자기주도적으로 서술·해결하고 AI 개인 피드백을 확인했습니다.",
        "competency": "현장 실무 통합 역량",
    },
    "team_collaborator": {
        "id": "team_collaborator",
        "title": "팀 협력 & 최종 성찰 완수",
        "desc": "팀별 협력 해결안을 도출하여 AI 보건튜터 팀 피드백과 교사 최종 피드백까지 완수했습니다.",
        "competency": "보건의료 협업 역량",
    },
}


def init_quest_state():
    """세션 상태 초기화 (KeyError 방지를 위해 모든 상태 키를 기본값으로 보장)."""
    first_unit_key = list(UNIT_CATALOG.keys())[0]
    first_scen_id = UNIT_CATALOG[first_unit_key]["scenarios"][0]["scenario_id"]
    defaults = {
        "hq_site_title": "HEALTH QUEST — 간호 미션룸",
        "hq_nav_view": "STEP 01 · 단원 질문 & 피드백",
        "hq_selected_unit": first_unit_key,
        "hq_custom_unit_topic": "",
        "hq_student_level": "표준 (기초→중급→심화 순차 도전)",
        "hq_selected_scenario_id": first_scen_id,
        "hq_active_mission_idx": 0,  # 0: 기초, 1: 중급, 2: 심화
        "hq_reduce_motion": False,
        "hq_sound_enabled": False,
        "hq_pending_sound": None,
        "hq_selected_term_idx": 0,
        # 1단계: 학생 질문 2~3개 및 AI 피드백, 모의실습 3단계 생성 여부
        "hq_unit_questions": [],  # unit별 질문·피드백 리스트
        "hq_scenarios_generated": {},  # unit_name -> bool
        # 2~3단계: 기초·중급·심화 개인별 제출 및 AI 개인 피드백
        "hq_submissions": {},  # mission_id -> submission dict (ai_individual_feedback 포함)
        "hq_reflections": {},  # mission_id -> str
        # 4단계: 팀별 협력 서술, AI 팀 피드백, 교사 최종 피드백
        "hq_student_label": "",
        "hq_group_label": "",
        "hq_team_autofill_pending": False,
        "hq_team_notes": {
            "individual_summary": "",
            "peer_comparison": "",
            "team_conclusion": "",
            "thought_change": "",
            "ai_team_feedback": "",
            "teacher_feedback": "",
            "saved_at": "",
        },
        # 교사용 학생 제출 보고서 목록 (세션 + 로컬 파일 연동)
        "hq_submitted_reports": [],
        # 보상 및 진도 기록
        "hq_rewarded_keys": [],
        "hq_earned_badges": [],
        "hq_xp": 0,
        "hq_growth_log": [],
        "hq_pending_badge_notice": None,
        "hq_pending_celebration": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def get_current_unit_data() -> dict:
    unit_name = st.session_state.get("hq_selected_unit")
    if unit_name not in UNIT_CATALOG:
        unit_name = list(UNIT_CATALOG.keys())[0]
        st.session_state["hq_selected_unit"] = unit_name
    return UNIT_CATALOG[unit_name]


def get_current_scenario_data() -> dict:
    unit = get_current_unit_data()
    scen_id = st.session_state.get("hq_selected_scenario_id", "")
    for scen in unit["scenarios"]:
        if scen["scenario_id"] == scen_id:
            return scen
    first_scen = unit["scenarios"][0]
    st.session_state["hq_selected_scenario_id"] = first_scen["scenario_id"]
    return first_scen


def get_unit_questions_for_current() -> list[dict]:
    unit_name = st.session_state.get("hq_selected_unit", "")
    all_qs = st.session_state.get("hq_unit_questions", [])
    return [q for q in all_qs if q.get("unit_name") == unit_name]


def get_scenario_progress(scenario: dict) -> tuple[int, int, float]:
    """기초·중급·심화 3단계 모의실습 완료 개수 및 전체 학습 진도율(0.0~1.0) 반환."""
    missions = scenario.get("missions", [])
    total = len(missions)
    completed_missions = 0
    correct_missions = 0

    for m in missions:
        sub = st.session_state.get("hq_submissions", {}).get(m["id"])
        if sub is not None:
            completed_missions += 1
            if sub.get("is_correct"):
                correct_missions += 1

    unit_name = st.session_state.get("hq_selected_unit", "")
    has_questions = len(get_unit_questions_for_current()) >= 1
    has_generated = bool(st.session_state.get("hq_scenarios_generated", {}).get(unit_name))
    team_notes = st.session_state.get("hq_team_notes", {})
    has_team = bool(team_notes.get("ai_team_feedback"))
    has_teacher = bool(team_notes.get("teacher_feedback"))

    pct = 0.0
    if has_questions:
        pct += 0.20
    if has_generated:
        pct += 0.10
    if total > 0:
        pct += (completed_missions / total) * 0.35 + (correct_missions / total) * 0.20
    if has_team:
        pct += 0.08
    if has_teacher:
        pct += 0.07

    pct = min(1.0, round(pct, 2))
    if completed_missions == total and total > 0 and pct < 0.75:
        pct = 0.75
    if completed_missions == total and has_team and has_teacher:
        pct = 1.0

    return completed_missions, total, pct


def get_step_completion_flags() -> dict:
    """상단 진도 트래커에 표시할 단계별 완료 상태 반환."""
    unit_name = st.session_state.get("hq_selected_unit", "")
    scen = get_current_scenario_data()
    missions = scen.get("missions", [])
    subs = st.session_state.get("hq_submissions", {})
    team_notes = st.session_state.get("hq_team_notes", {})

    q_done = len(get_unit_questions_for_current()) >= 1
    scen_ready = bool(st.session_state.get("hq_scenarios_generated", {}).get(unit_name)) or q_done
    m1_done = bool(missions and subs.get(missions[0]["id"]))
    m2_done = bool(len(missions) > 1 and subs.get(missions[1]["id"]))
    m3_done = bool(len(missions) > 2 and subs.get(missions[2]["id"]))
    team_done = bool(team_notes.get("ai_team_feedback"))
    teacher_done = bool(team_notes.get("teacher_feedback"))

    return {
        "q_done": q_done,
        "scen_ready": scen_ready,
        "m1_done": m1_done,
        "m2_done": m2_done,
        "m3_done": m3_done,
        "team_done": team_done,
        "teacher_done": teacher_done,
    }


def award_action_once(action_key: str, action_type: str, label: str, xp_gain: int, badge_id: str | None = None) -> bool:
    if action_key in st.session_state["hq_rewarded_keys"]:
        return False

    st.session_state["hq_rewarded_keys"].append(action_key)
    st.session_state["hq_xp"] = st.session_state.get("hq_xp", 0) + xp_gain
    st.session_state["hq_growth_log"].append(
        {
            "timestamp": _now_kst().strftime("%H:%M:%S"),
            "action_type": action_type,
            "label": label,
            "xp_gain": xp_gain,
        }
    )

    if badge_id and badge_id in BADGE_DEFINITIONS and badge_id not in st.session_state["hq_earned_badges"]:
        st.session_state["hq_earned_badges"].append(badge_id)
        st.session_state["hq_pending_badge_notice"] = BADGE_DEFINITIONS[badge_id]

    return True


def _build_single_question_feedback(unit_data: dict, q_text: str, student_level: str) -> str:
    """단원 핵심 용어 사전 및 임상 원칙을 기반으로 학생 질문에 대한 맞춤형 AI 보건튜터 답변 생성."""
    clean_q = q_text.strip()
    matched_term = None
    matched_desc = None
    for term_name, desc in unit_data.get("key_terms", {}).items():
        core_word = term_name.split("(")[0].strip()
        if core_word in clean_q or any(tok in clean_q for tok in core_word.split("·") if len(tok) >= 2):
            matched_term = term_name
            matched_desc = desc
            break

    safety_keywords = ["직접 주사", "내가 처방", "약 먹", "락스", "혼합", "도뇨관 삽입", "절개", "가위로"]
    has_safety_concern = any(k in clean_q for k in safety_keywords)

    if matched_term:
        explanation = f"핵심 개념 ({matched_term}): {matched_desc}"
    else:
        first_term, first_desc = list(unit_data.get("key_terms", {}).items())[0]
        explanation = f"단원 핵심 개념 ({first_term}): {first_desc}"

    safety_note = ""
    if has_safety_concern:
        safety_note = "\n- 안전 수칙: 침습적 처치·임의 투약·소독제 혼합은 금지되며, 활력징후 관찰 후 담당 간호사·의사에게 즉시 보고합니다."

    return f"- {explanation}\n- 실습 적용 포인트: 아래 [기초 · 중급 · 심화 모의실습]에서 핵심 단서를 찾아 적용해 보세요.{safety_note}"


def submit_student_questions_and_get_feedback(
    unit_data: dict,
    questions: list[str],
    student_level: str,
    custom_topic: str = "",
) -> list[dict]:
    """학생이 작성한 2~3개의 질문을 받아 각각 AI 보건튜터 피드백을 생성하고, 수준별 모의실습 3단계(기초·중급·심화)를 활성화."""
    unit_name = st.session_state.get("hq_selected_unit", "")
    valid_qs = [q.strip() for q in questions if q and q.strip()]
    if not valid_qs:
        return []

    fallback_sections = []
    for idx, q_str in enumerate(valid_qs, start=1):
        fallback_sections.append(f"Q{idx} 피드백\n{_build_single_question_feedback(unit_data, q_str, student_level)}")
    ai_prompt = (
        f"작업: 학생 질문 피드백\n교과목: {unit_data.get('subject', '')}\n"
        f"실습 단원: {unit_name}\n학생이 선택한 난이도: {student_level}\n"
        f"세부 주제: {custom_topic.strip() or '기본 단원'}\n"
        + "\n".join(f"Q{idx}: {q}" for idx, q in enumerate(valid_qs, start=1))
        + "\n각 질문에 대해 Q1 피드백, Q2 피드백 형식으로 개념 근거, 실습 적용, 다음 생각 질문을 짧게 작성하세요."
    )
    grounded = generate_grounded_feedback(
        "student_questions",
        ai_prompt,
        "\n\n".join(fallback_sections),
    )

    feedback_parts = {}
    matches = list(re.finditer(r"(?m)^Q(\d+)\s*피드백\s*[:：]?\s*", grounded.text))
    for pos, match in enumerate(matches):
        start = match.end()
        end = matches[pos + 1].start() if pos + 1 < len(matches) else len(grounded.text)
        feedback_parts[int(match.group(1))] = grounded.text[start:end].strip()

    other_unit_qs = [item for item in st.session_state.get("hq_unit_questions", []) if item.get("unit_name") != unit_name]
    new_entries = []
    for idx, q_str in enumerate(valid_qs, start=1):
        fb_md = feedback_parts.get(idx) or _build_single_question_feedback(unit_data, q_str, student_level)
        entry = {
            "unit_name": unit_name,
            "q_num": idx,
            "question": q_str,
            "level": student_level,
            "custom_topic": custom_topic.strip(),
            "feedback_markdown": fb_md,
            "feedback_mode": grounded.mode,
            "teacher_review_needed": grounded.teacher_review_needed,
        }
        new_entries.append(entry)

    st.session_state["hq_unit_questions"] = other_unit_qs + new_entries
    st.session_state["hq_scenarios_generated"][unit_name] = True
    st.session_state["hq_pending_sound"] = "chime"

    award_action_once(
        action_key=f"unit_questions:{unit_name}",
        action_type="자기주도 질문 & AI 피드백",
        label=f"[{unit_name}] 질문 {len(new_entries)}개 등록 및 기초·중급·심화 실습 설계 완료",
        xp_gain=30,
        badge_id="question_explorer",
    )
    return new_entries


def evaluate_mission_submission(mission: dict, selected_idx: int, rationale: str) -> dict:
    """기초·중급·심화 각 모의실습에 대한 학생의 개인별 선택 + 자기주도 PBL 서술 평가 및 AI 보건튜터 개인별 피드백 생성."""
    m_id = mission["id"]
    prev_sub = st.session_state["hq_submissions"].get(m_id)
    attempts = (prev_sub["attempts"] + 1) if prev_sub else 1

    is_correct = selected_idx == mission["correct_index"]
    clean_rationale = (rationale or "").strip()

    keywords = mission.get("required_keywords", [])
    matched_kw = [kw for kw in keywords if kw in clean_rationale]

    level_label = mission.get("level_label", f"미션 {mission.get('step_num', 1)}")
    selected_exp = mission["option_explanations"][selected_idx]
    correct_opt_text = mission["options"][mission["correct_index"]]

    if is_correct and len(clean_rationale) >= 10:
        kw_note = f"(핵심어: {', '.join(matched_kw[:4])})" if matched_kw else "(임상 근거 서술 완료)"
        rationale_status = f"개인 서술 완료 {kw_note}"
        ai_feedback = (
            f"AI 보건튜터 개인 피드백 · {level_label}\n"
            f"- 판단 결과: 정답입니다. 핵심 단서({', '.join(mission['clinical_clues'][:2])})를 정확히 파악했습니다.\n"
            f"- 서술 평가: 환자 안전과 근거 기반 간호 원칙이 잘 드러납니다. {kw_note}\n"
            f"- 핵심 해설: {selected_exp}"
        )
    elif is_correct and len(clean_rationale) < 10:
        rationale_status = "정답 확인 완료 · 근거 서술 보충 권장"
        ai_feedback = (
            f"AI 보건튜터 개인 피드백 · {level_label}\n"
            f"- 판단 결과: 정답은 {selected_idx + 1}번입니다. ({selected_exp})\n"
            f"- 서술 코칭: 관찰 단서와 선택한 이유를 한 문장으로 더 적어보세요."
        )
    else:
        rationale_status = "재도전 코칭 (감점 없음)"
        ai_feedback = (
            f"AI 보건튜터 재도전 가이드 · {level_label}\n"
            f"- 선택 점검: {selected_idx + 1}번 보기는 {selected_exp}\n"
            f"- 핵심 힌트: {mission.get('missed_clue_guide', mission.get('hint', ''))}"
        )

    ai_prompt = (
        f"작업: 개인 PBL 판단 근거 코칭\n미션: {mission['title']}\n수준: {level_label}\n"
        f"상황: {mission.get('situation', '')}\n학생 선택: {mission['options'][selected_idx]}\n"
        f"객관식 판정: {'정답' if is_correct else '재도전 필요'}\n학생 근거: {clean_rationale}\n"
        f"확정 해설: {selected_exp}\n정답 판정은 바꾸지 말고, 학생 근거의 강점과 놓친 단서, 다음 수정 질문을 제시하세요."
    )
    grounded = generate_grounded_feedback("mission_rationale", ai_prompt, ai_feedback)

    result = {
        "mission_id": m_id,
        "level_label": level_label,
        "selected_idx": selected_idx,
        "selected_text": mission["options"][selected_idx],
        "correct_text": correct_opt_text,
        "is_correct": is_correct,
        "rationale": clean_rationale,
        "rationale_status": rationale_status,
        "matched_keywords": matched_kw,
        "ai_individual_feedback": grounded.text,
        "feedback_mode": grounded.mode,
        "teacher_review_needed": grounded.teacher_review_needed,
        "attempts": attempts,
        "submitted_at": _now_kst().strftime("%Y-%m-%d %H:%M:%S"),
    }
    st.session_state["hq_submissions"][m_id] = result
    st.session_state["hq_pending_sound"] = "success" if is_correct else "try_again"

    step_num = mission.get("step_num", 1)
    award_action_once(
        action_key=f"mission_submit:{m_id}",
        action_type=f"{level_label} 제출",
        label=f"[{mission['title']}] 개인별 PBL 해결안 제출 및 AI 피드백 확인",
        xp_gain=30,
        badge_id="first_mission" if step_num == 1 else ("evidence_thinker" if step_num == 2 else None),
    )

    scenario = get_current_scenario_data()
    completed, total, _ = get_scenario_progress(scenario)
    if completed == total and total > 0:
        newly_done = award_action_once(
            action_key=f"scenario_complete:{scenario['scenario_id']}",
            action_type="기초·중급·심화 완주",
            label=f"[{scenario['title']}] 기초·중급·심화 3단계 개인 PBL 완수",
            xp_gain=50,
            badge_id="pbl_master",
        )
        if newly_done:
            st.session_state["hq_pending_celebration"] = scenario["title"]
            st.session_state["hq_pending_sound"] = "fanfare"

    return result


def submit_team_collaboration_and_get_feedback(
    student_name: str,
    group_name: str,
    individual_summary: str,
    peer_comparison: str,
    team_conclusion: str,
    thought_change: str,
) -> str:
    """팀별 협력 해결안을 저장하고 AI 보건튜터의 팀 피드백을 생성."""
    unit = get_current_unit_data()
    scenario = get_current_scenario_data()

    ai_team_fb = (
        f"AI 보건튜터 팀별 협력학습 피드백 — {group_name or '우리 모둠'}\n"
        f"- 개인 추론과 모둠 비교: 기초·중급·심화 실습을 바탕으로 모둠원 간 생각 차이인 \"{peer_comparison or '단서 비교 완료'}\"를 비교하여 놓치기 쉬운 부분을 보완했습니다.\n"
        f"- 팀 최종 해결안과 우선순위: \"{team_conclusion or '환자안전 최우선 간호 행동 합의'}\"는 {unit.get('subject', '보건간호')} 교육과정의 표준주의, 환자안전, 비침습적 관찰과 신속 보고 원칙에 부합합니다.\n"
        f"- 협업 성장: 토의를 통해 \"{thought_change or '근거 기반 판단과 팀 소통의 중요성 확인'}\"으로 생각을 확장했습니다."
    )

    ai_prompt = (
        f"작업: 모둠 협력학습 피드백\n교과목: {unit.get('subject', '')}\n단원: {st.session_state.get('hq_selected_unit', '')}\n"
        f"모의실습: {scenario.get('title', '')}\n개인 해결 요약: {individual_summary}\n"
        f"팀원과 비교: {peer_comparison}\n팀 최종 해결안: {team_conclusion}\n"
        f"토의 후 바뀐 생각: {thought_change}\n"
        "개인 추론과 팀 비교의 연결성, 해결안의 안전성과 우선순위, 협업 성장을 자연스러운 문장으로 피드백하세요."
    )
    grounded = generate_grounded_feedback("team_collaboration", ai_prompt, ai_team_fb)
    ai_team_fb = grounded.text

    existing_teacher_fb = st.session_state.get("hq_team_notes", {}).get("teacher_feedback", "")
    st.session_state["hq_student_label"] = student_name.strip()
    st.session_state["hq_group_label"] = group_name.strip()
    st.session_state["hq_team_notes"] = {
        "individual_summary": individual_summary.strip(),
        "peer_comparison": peer_comparison.strip(),
        "team_conclusion": team_conclusion.strip(),
        "thought_change": thought_change.strip(),
        "ai_team_feedback": ai_team_fb,
        "feedback_mode": grounded.mode,
        "teacher_review_needed": grounded.teacher_review_needed,
        "teacher_feedback": existing_teacher_fb,
        "saved_at": _now_kst().strftime("%Y-%m-%d %H:%M"),
    }
    st.session_state["hq_pending_sound"] = "chime"

    award_action_once(
        action_key=f"team_collab:{scenario['scenario_id']}",
        action_type="팀 협력 해결안 제출",
        label=f"[{scenario['title']}] 팀 협력 해결안 제출 및 AI 팀 피드백 완료",
        xp_gain=40,
        badge_id="team_collaborator",
    )
    return ai_team_fb


def generate_or_save_teacher_feedback(custom_teacher_comment: str = "") -> str:
    """마지막 단계인 '교사의 피드백'을 생성 또는 저장하여 완전학습 루프를 완성."""
    unit_name = st.session_state.get("hq_selected_unit", "")
    scenario = get_current_scenario_data()
    completed, total, _ = get_scenario_progress(scenario)
    qs = get_unit_questions_for_current()

    if custom_teacher_comment and custom_teacher_comment.strip():
        teacher_fb = custom_teacher_comment.strip()
    else:
        teacher_fb = (
            f"[담당 교사 종합 피드백] {unit_name} 단원에서 스스로 {len(qs) or 2}개의 핵심 질문을 던지고, "
            f"기초·중급·심화 모의실습({completed}/{total}단계)을 PBL 근거에 따라 성실히 서술하여 해결했습니다. "
            f"특히 개인별 추론에 그치지 않고 모둠원과의 토의를 통해 환자안전과 예비 보건의료인의 업무 범위(정확한 관찰 및 즉시 보고)를 "
            f"명확히 정립한 점을 높이 평가합니다. 병원 임상실습 현장에서도 오늘 정리한 우선순위 원칙을 꼭 실천하기 바랍니다."
        )

    st.session_state["hq_team_notes"]["teacher_feedback"] = teacher_fb
    st.session_state["hq_pending_sound"] = "fanfare"
    award_action_once(
        action_key=f"teacher_feedback:{scenario['scenario_id']}",
        action_type="교사 최종 피드백 확인",
        label=f"[{scenario['title']}] 교사 최종 피드백 확인 및 디지털 학습지 완성",
        xp_gain=30,
    )
    return teacher_fb


def save_mission_reflection(mission: dict, reflection_text: str):
    m_id = mission["id"]
    clean_ref = (reflection_text or "").strip()
    st.session_state["hq_reflections"][m_id] = clean_ref
    if len(clean_ref) >= 5:
        award_action_once(
            action_key=f"reflection:{m_id}",
            action_type="성찰 기록",
            label=f"[{mission['title']}] 성찰 질문 작성 완료",
            xp_gain=15,
        )


# ==============================================================================
# 교사용 학생 제출 보고서 통합 관리함 (로컬 JSON 저장소 + 세션 연동 + 엑셀 CSV 내보내기)
# ==============================================================================
def load_submitted_reports() -> list[dict]:
    """저장된 학생 제출 보고서 목록을 불러옵니다 (.gitignore로 보호되어 깃허브에 노출되지 않음)."""
    records: list[dict] = []
    if SUBMISSIONS_STORE_PATH.exists():
        try:
            raw = json.loads(SUBMISSIONS_STORE_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                records = raw
        except Exception:
            records = []

    session_records = st.session_state.get("hq_submitted_reports", [])
    existing_ids = {r.get("report_id") for r in records if r.get("report_id")}
    for sr in session_records:
        if sr.get("report_id") not in existing_ids:
            records.append(sr)

    st.session_state["hq_submitted_reports"] = records
    return records


def _save_report_entry(report_entry: dict, identity_fields: tuple[str, ...]) -> dict:
    all_reports = load_submitted_reports()
    updated = False
    for index, existing in enumerate(all_reports):
        if all(existing.get(field) == report_entry.get(field) for field in identity_fields):
            all_reports[index] = report_entry
            updated = True
            break
    if not updated:
        all_reports.append(report_entry)

    st.session_state["hq_submitted_reports"] = all_reports
    try:
        SUBMISSIONS_STORE_PATH.write_text(
            json.dumps(all_reports, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass
    return report_entry


def save_individual_progress_to_teacher() -> dict | None:
    """현재 학생의 개인 질문·미션 서술을 개인 평가용 기록으로 저장하거나 갱신합니다."""
    student_name = st.session_state.get("hq_student_label", "").strip()
    if not student_name:
        return None

    unit_name = st.session_state.get("hq_selected_unit", "")
    unit_data = get_current_unit_data()
    scenario = get_current_scenario_data()
    missions = scenario.get("missions", [])
    subs = st.session_state.get("hq_submissions", {})
    qs = get_unit_questions_for_current()
    _, _, pct = get_scenario_progress(scenario)

    mission_texts = []
    for index in range(3):
        if index < len(missions) and (sub := subs.get(missions[index]["id"])):
            mission_texts.append(f"[{sub['selected_idx'] + 1}번 선택] {sub.get('rationale') or '(서술 없음)'}")
        else:
            mission_texts.append("(미제출)")

    feedback_modes = sorted(
        {
            item.get("feedback_mode", "")
            for item in [*qs, *subs.values()]
            if item.get("feedback_mode")
        }
    )
    review_needed = any(item.get("teacher_review_needed") for item in [*qs, *subs.values()])

    report_entry = {
        "report_id": f"individual_{student_name}_{unit_data.get('unit_id', 'u')}",
        "record_type": "개인 기록",
        "submitted_at": _now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "student_name": student_name,
        "group_name": "",
        "subject": unit_data.get("subject", "보건간호"),
        "unit_name": unit_name,
        "progress_pct": int(pct * 100),
        "xp": st.session_state.get("hq_xp", 0),
        "questions_summary": " | ".join([f"Q{q['q_num']}: {q['question']}" for q in qs]) if qs else "(질문 미입력)",
        "basic_mission": mission_texts[0],
        "inter_mission": mission_texts[1],
        "adv_mission": mission_texts[2],
        "individual_summary": "",
        "peer_comparison": "",
        "team_conclusion": "",
        "thought_change": "",
        "ai_team_feedback": "",
        "teacher_feedback": "",
        "ai_feedback_mode": ", ".join(feedback_modes) or "규칙형 안전 모드",
        "ai_teacher_review": "필요" if review_needed else "없음",
    }
    return _save_report_entry(report_entry, ("record_type", "student_name", "unit_name"))


def submit_final_report_to_teacher() -> dict:
    """학생이 작성한 질문·기초/중급/심화 서술·팀 협력·피드백 전체를 교사용 제출함에 등록합니다."""
    unit_name = st.session_state.get("hq_selected_unit", "")
    unit_data = get_current_unit_data()
    scenario = get_current_scenario_data()
    missions = scenario.get("missions", [])
    subs = st.session_state.get("hq_submissions", {})
    notes = st.session_state.get("hq_team_notes", {})
    qs = get_unit_questions_for_current()
    completed, total, pct = get_scenario_progress(scenario)

    if not notes.get("teacher_feedback"):
        generate_or_save_teacher_feedback()
        notes = st.session_state.get("hq_team_notes", {})
        completed, total, pct = get_scenario_progress(scenario)

    student_name = st.session_state.get("hq_student_label", "").strip() or "이름미입력_학생"
    group_name = st.session_state.get("hq_group_label", "").strip() or "기본모둠"
    now_str = _now_kst().strftime("%Y-%m-%d %H:%M:%S")

    q_summary = " | ".join([f"Q{q['q_num']}: {q['question']}" for q in qs]) if qs else "(질문 미입력)"

    m_texts = []
    for idx in range(3):
        if idx < len(missions):
            m = missions[idx]
            sub = subs.get(m["id"])
            if sub:
                m_texts.append(f"[{sub['selected_idx']+1}번 선택] {sub.get('rationale') or '(서술 없음)'}")
            else:
                m_texts.append("(미제출)")
        else:
            m_texts.append("-")

    report_entry = {
        "report_id": f"team_{group_name}_{unit_data.get('unit_id', 'u')}",
        "record_type": "모둠 기록",
        "submitted_at": now_str,
        "student_name": student_name,
        "group_name": group_name,
        "subject": unit_data.get("subject", "보건간호"),
        "unit_name": unit_name,
        "progress_pct": int(pct * 100),
        "xp": st.session_state.get("hq_xp", 0),
        "questions_summary": q_summary,
        "basic_mission": m_texts[0],
        "inter_mission": m_texts[1],
        "adv_mission": m_texts[2],
        "individual_summary": notes.get("individual_summary", ""),
        "peer_comparison": notes.get("peer_comparison", ""),
        "team_conclusion": notes.get("team_conclusion", ""),
        "thought_change": notes.get("thought_change", ""),
        "ai_team_feedback": notes.get("ai_team_feedback", ""),
        "teacher_feedback": notes.get("teacher_feedback", ""),
        "ai_feedback_mode": notes.get("feedback_mode", "규칙형 안전 모드"),
        "ai_teacher_review": "필요" if notes.get("teacher_review_needed") else "없음",
    }

    st.session_state["hq_pending_sound"] = "fanfare"
    return _save_report_entry(report_entry, ("record_type", "group_name", "unit_name"))


def export_submitted_reports_csv() -> bytes:
    """교사가 모든 학생의 제출 보고서를 한 번에 엑셀(CSV, UTF-8-BOM)로 다운로드할 수 있도록 변환합니다."""
    records = load_submitted_reports()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "제출일시",
            "학번/이름",
            "모둠명",
            "교과목",
            "학습단원",
            "진도율(%)",
            "성취점수(XP)",
            "학생 질문(2~3개)",
            "기초 모의실습(개인서술)",
            "중급 모의실습(개인서술)",
            "심화 모의실습(개인서술)",
            "팀원 의견 비교",
            "팀 최종 해결안",
            "토의 후 성찰",
            "AI 팀 피드백",
            "교사 최종 피드백",
        ]
    )
    for r in records:
        writer.writerow(
            [
                r.get("submitted_at", ""),
                r.get("student_name", ""),
                r.get("group_name", ""),
                r.get("subject", ""),
                r.get("unit_name", ""),
                r.get("progress_pct", 0),
                r.get("xp", 0),
                r.get("questions_summary", ""),
                r.get("basic_mission", ""),
                r.get("inter_mission", ""),
                r.get("adv_mission", ""),
                r.get("peer_comparison", ""),
                r.get("team_conclusion", ""),
                r.get("thought_change", ""),
                r.get("ai_team_feedback", "").replace("\n", " "),
                r.get("teacher_feedback", "").replace("\n", " "),
            ]
        )
    return output.getvalue().encode("utf-8-sig")


REPORT_COLUMNS = [
    ("기록유형", "record_type"),
    ("제출일시", "submitted_at"),
    ("학번/이름", "student_name"),
    ("모둠명", "group_name"),
    ("교과목", "subject"),
    ("학습단원", "unit_name"),
    ("진도율(%)", "progress_pct"),
    ("성장 코인", "xp"),
    ("학생 질문(2~3개)", "questions_summary"),
    ("기초 모의실습(개인서술)", "basic_mission"),
    ("중급 모의실습(개인서술)", "inter_mission"),
    ("심화 모의실습(개인서술)", "adv_mission"),
    ("팀원 의견 비교", "peer_comparison"),
    ("팀 최종 해결안", "team_conclusion"),
    ("토의 후 성찰", "thought_change"),
    ("AI 팀 피드백", "ai_team_feedback"),
    ("AI 피드백 방식", "ai_feedback_mode"),
    ("AI 교사 검토", "ai_teacher_review"),
    ("교사 최종 피드백", "teacher_feedback"),
]


def _reports_to_xlsx(records: list[dict], sheet_title: str) -> bytes:
    """학생 제출용 또는 교사용 통합 보고서를 실제 .xlsx 파일로 생성합니다."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_title[:31]
    sheet.freeze_panes = "A2"
    sheet.append([title for title, _ in REPORT_COLUMNS])

    header_fill = PatternFill("solid", fgColor="DDEBE6")
    header_font = Font(bold=True, color="274B40")
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for report in records:
        sheet.append([report.get(field, "") for _, field in REPORT_COLUMNS])

    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    widths = [14, 19, 24, 18, 18, 34, 12, 12, 45, 45, 45, 45, 36, 45, 36, 48, 48]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[chr(64 + index)].width = width
    sheet.auto_filter.ref = sheet.dimensions

    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def export_submitted_reports_xlsx() -> bytes:
    """교사가 모든 학생의 제출물을 통합 Excel 파일로 내려받습니다."""
    return _reports_to_xlsx(load_submitted_reports(), "학생 제출 통합")


def export_current_student_report_xlsx() -> bytes:
    """현재 학생이 자신의 최신 제출물만 Excel 파일로 내려받습니다."""
    student_name = st.session_state.get("hq_student_label", "").strip() or "이름미입력_학생"
    unit_name = st.session_state.get("hq_selected_unit", "")
    matches = [
        report for report in load_submitted_reports()
        if report.get("student_name") == student_name and report.get("unit_name") == unit_name
    ]
    return _reports_to_xlsx(matches[-1:] if matches else [], "나의 학습 기록")
