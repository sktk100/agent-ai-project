"""Student file, image, audio, and video submission portal."""

import streamlit as st

from firebase_integration import (
    current_identity,
    get_backend,
    load_firebase_settings,
    validate_upload,
)


st.set_page_config(page_title="HEALTH QUEST — 수행평가 제출", page_icon="📤", layout="wide")
settings = load_firebase_settings()
identity = current_identity()

if not settings.enabled or identity is None:
    st.error("Firebase 로그인 후 사용할 수 있는 페이지입니다.")
    st.stop()
if identity.role != "student":
    st.error("학생 계정만 수행평가를 제출할 수 있습니다.")
    st.stop()

st.title("수행평가 자료 제출", anchor=False)
st.caption(f"{identity.class_id} · {identity.student_number} {identity.name}")
st.info("실제 환자의 얼굴·이름·연락처·진료정보가 포함된 파일은 제출하지 마세요.", icon=":material/privacy_tip:")

with st.form("firebase_submission_form", clear_on_submit=False):
    submission_type = st.radio("제출 유형", ["개인", "팀"], horizontal=True)
    unit_name = st.text_input(
        "단원 또는 모의상황 이름",
        value=str(st.session_state.get("hq_selected_unit", "")),
        max_chars=200,
    )
    team_members = st.text_input(
        "팀원 학번·이름 — 팀 제출일 때만",
        placeholder="예: 2101 홍길동, 2102 김간호",
        max_chars=500,
    )
    reflection = st.text_area(
        "판단 근거와 성찰",
        placeholder="모의상황에서 어떤 단서를 근거로 판단했고 다음에는 무엇을 보완할지 작성하세요.",
        height=160,
        max_chars=5000,
    )
    files = st.file_uploader(
        "PDF·문서·이미지·음성·영상",
        type=["pdf", "docx", "xlsx", "png", "jpg", "jpeg", "mp3", "wav", "webm", "mp4"],
        accept_multiple_files=True,
        help="문서·이미지 20MB, 음성 25MB, 영상 100MB 이하",
    )
    recorded = st.audio_input("마이크로 개인 또는 팀 보고를 녹음하세요")
    submitted = st.form_submit_button("교사에게 안전하게 제출", type="primary", use_container_width=True)

if submitted:
    upload_items: list[tuple[str, str, bytes]] = []
    errors: list[str] = []
    for uploaded in files or []:
        data = uploaded.getvalue()
        content_type = uploaded.type or "application/octet-stream"
        if error := validate_upload(uploaded.name, content_type, len(data)):
            errors.append(error)
        else:
            upload_items.append((uploaded.name, content_type, data))
    if recorded is not None:
        data = recorded.getvalue()
        content_type = recorded.type or "audio/wav"
        if error := validate_upload("recording.wav", content_type, len(data)):
            errors.append(error)
        else:
            upload_items.append(("recording.wav", content_type, data))

    if submission_type == "팀" and not team_members.strip():
        errors.append("팀 제출에는 팀원 학번과 이름을 입력해 주세요.")
    if not reflection.strip() and not upload_items:
        errors.append("성찰 내용을 작성하거나 제출 파일을 추가해 주세요.")

    if errors:
        for error in errors:
            st.error(error)
    else:
        try:
            with st.spinner("Firebase Storage와 교사 제출함에 저장하는 중..."):
                submission_id = get_backend(settings).create_submission(
                    identity,
                    {
                        "unit_name": unit_name,
                        "submission_type": submission_type,
                        "team_members": team_members,
                        "reflection": reflection,
                    },
                    upload_items,
                )
            st.success(f"제출이 완료되었습니다. 제출번호: {submission_id[:8]}")
        except Exception as exc:
            st.error("제출 중 오류가 발생했습니다. 파일은 공개되지 않았습니다.")
            st.caption(f"오류 유형: {type(exc).__name__}")

st.divider()
st.subheader("나의 최근 제출 기록", anchor=False)
try:
    records = get_backend(settings).list_student_submissions(identity)
except Exception:
    records = []
for item in sorted(records, key=lambda value: str(value.get("submitted_at", "")), reverse=True):
    with st.container(border=True):
        st.markdown(f"**{item.get('unit_name') or '단원 미입력'}** · {item.get('status', 'submitted')}")
        st.caption(f"교사 피드백: {item.get('teacher_feedback') or '아직 등록되지 않았습니다.'}")
if not records:
    st.caption("아직 Firebase에 저장된 제출 기록이 없습니다.")
