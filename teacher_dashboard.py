"""Authenticated teacher-only dashboard for Firebase classroom records."""

import streamlit as st

from firebase_integration import current_identity, get_backend, load_firebase_settings


st.set_page_config(page_title="HEALTH QUEST — 교사 대시보드", page_icon="📋", layout="wide")
settings = load_firebase_settings()
identity = current_identity()

if not settings.enabled or identity is None or identity.role != "teacher":
    st.error("등록된 교사 계정만 접근할 수 있습니다.")
    st.stop()

st.title("교사 평가 대시보드", anchor=False)
st.caption(f"교사: {identity.name} · 두 교사가 5개 반을 공동으로 확인하는 초기 운영 모드")

try:
    backend = get_backend(settings)
    data = backend.list_teacher_dashboard(identity)
except Exception as exc:
    st.error("Firebase 자료를 불러오지 못했습니다.")
    st.caption(f"오류 유형: {type(exc).__name__}")
    st.stop()

students = [item for item in data["users"] if item.get("role") == "student"]
submissions = data["submissions"]
sessions = data["sessions"]

metric_cols = st.columns(4)
metric_cols[0].metric("등록 학생", len(students), border=True)
metric_cols[1].metric("학습 기록", len(sessions), border=True)
metric_cols[2].metric("수행평가 제출", len(submissions), border=True)
metric_cols[3].metric(
    "평가 대기",
    sum(1 for item in submissions if item.get("status") in {"submitted", "reviewing"}),
    border=True,
)

filter_cols = st.columns(3)
selected_class = filter_cols[0].selectbox("학급", ["전체", *settings.class_ids])
selected_status = filter_cols[1].selectbox(
    "상태", ["전체", "submitted", "reviewing", "revision_requested", "evaluated"]
)
search_name = filter_cols[2].text_input("학생 이름·학번 검색")

filtered = []
for item in submissions:
    if selected_class != "전체" and item.get("class_id") != selected_class:
        continue
    if selected_status != "전체" and item.get("status") != selected_status:
        continue
    haystack = f"{item.get('student_name', '')} {item.get('student_number', '')}".casefold()
    if search_name.strip().casefold() not in haystack:
        continue
    filtered.append(item)

st.subheader(f"제출 자료 {len(filtered)}건", anchor=False)
for item in filtered:
    submission_id = str(item.get("submission_id", ""))
    title = (
        f"{item.get('class_id', '')} · {item.get('student_number', '')} "
        f"{item.get('student_name', '')} · {item.get('unit_name', '단원 미입력')}"
    )
    with st.expander(title):
        st.write(item.get("reflection") or "성찰 내용 없음")
        st.caption(
            f"제출 유형: {item.get('submission_type', '개인')} · "
            f"상태: {item.get('status', 'submitted')}"
        )
        for index, file_info in enumerate(item.get("files", [])):
            storage_path = str(file_info.get("storage_path", ""))
            st.markdown(f"- {file_info.get('name')} ({file_info.get('content_type')})")
            if storage_path and st.button("15분 미리보기 링크 만들기", key=f"signed_{submission_id}_{index}"):
                try:
                    st.session_state[f"signed_url_{submission_id}_{index}"] = backend.signed_download_url(
                        identity, storage_path
                    )
                except Exception as exc:
                    st.error(f"미리보기 링크 생성 실패: {type(exc).__name__}")
            if url := st.session_state.get(f"signed_url_{submission_id}_{index}"):
                content_type = str(file_info.get("content_type", ""))
                if content_type.startswith("audio/"):
                    st.audio(url)
                elif content_type.startswith("video/"):
                    st.video(url)
                elif content_type.startswith("image/"):
                    st.image(url)
                else:
                    st.link_button("파일 열기", url)

        with st.form(f"feedback_{submission_id}"):
            score = st.slider("루브릭 점수", 0, 100, int(item.get("rubric_score") or 0))
            feedback = st.text_area(
                "교사 피드백",
                value=str(item.get("teacher_feedback", "")),
                max_chars=5000,
            )
            status = st.selectbox(
                "평가 상태",
                ["reviewing", "revision_requested", "evaluated"],
                index=(
                    ["reviewing", "revision_requested", "evaluated"].index(item.get("status"))
                    if item.get("status") in {"reviewing", "revision_requested", "evaluated"}
                    else 0
                ),
            )
            save = st.form_submit_button("평가 저장", type="primary")
        if save:
            try:
                backend.save_teacher_feedback(identity, submission_id, score, feedback, status)
                st.success("교사 평가를 저장했습니다.")
                st.rerun()
            except Exception as exc:
                st.error(f"평가 저장 실패: {type(exc).__name__}")

if not filtered:
    st.info("조건에 맞는 제출 자료가 없습니다.")
