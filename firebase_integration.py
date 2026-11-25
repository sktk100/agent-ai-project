"""Firebase authentication context, privileged storage, and authorization guards.

The Streamlit app is the only data client. Firestore and Storage rules therefore
deny direct browser access, while every Admin SDK operation is guarded again in
Python by the authenticated user's role and UID.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Iterable, Mapping

import streamlit as st


DEFAULT_CLASSES = ("1반", "2반", "3반", "4반", "5반")
ALLOWED_UPLOAD_TYPES = {
    "application/pdf": 20 * 1024 * 1024,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": 20 * 1024 * 1024,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": 20 * 1024 * 1024,
    "image/jpeg": 20 * 1024 * 1024,
    "image/png": 20 * 1024 * 1024,
    "audio/mpeg": 25 * 1024 * 1024,
    "audio/wav": 25 * 1024 * 1024,
    "audio/x-wav": 25 * 1024 * 1024,
    "audio/webm": 25 * 1024 * 1024,
    "video/mp4": 100 * 1024 * 1024,
    "video/webm": 100 * 1024 * 1024,
}


class FirebaseConfigurationError(RuntimeError):
    """Raised when Firebase is enabled but its private server config is incomplete."""


class AuthorizationError(PermissionError):
    """Raised before a privileged Firebase operation when the role check fails."""


@dataclass(frozen=True)
class FirebaseSettings:
    enabled: bool = False
    project_id: str = ""
    storage_bucket: str = ""
    service_account: dict[str, Any] = field(default_factory=dict)
    teacher_emails: tuple[str, ...] = ()
    class_ids: tuple[str, ...] = DEFAULT_CLASSES

    @property
    def configured(self) -> bool:
        required = {"project_id", "client_email", "private_key", "token_uri"}
        return bool(
            self.enabled
            and self.project_id
            and self.storage_bucket
            and required.issubset(self.service_account)
        )


@dataclass(frozen=True)
class AppIdentity:
    uid: str
    email: str
    name: str
    role: str
    class_id: str = ""
    student_number: str = ""

    def as_dict(self) -> dict[str, str]:
        return dataclasses.asdict(self)


def _plain_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key, item in value.items():
        if hasattr(item, "to_dict"):
            item = item.to_dict()
        result[str(key)] = item
    return result


def load_firebase_settings() -> FirebaseSettings:
    """Read non-secret flags plus private credentials from Streamlit Secrets."""
    try:
        firebase_section = _plain_mapping(st.secrets.get("firebase", {}))
        authz_section = _plain_mapping(st.secrets.get("authz", {}))
    except (FileNotFoundError, KeyError):
        return FirebaseSettings()

    service_account = _plain_mapping(firebase_section.get("service_account", {}))
    if service_account.get("private_key"):
        service_account["private_key"] = str(service_account["private_key"]).replace("\\n", "\n")

    teachers = tuple(
        sorted(
            {
                str(email).strip().casefold()
                for email in authz_section.get("teacher_emails", [])
                if str(email).strip()
            }
        )
    )
    classes = tuple(
        str(value).strip()
        for value in authz_section.get("class_ids", DEFAULT_CLASSES)
        if str(value).strip()
    ) or DEFAULT_CLASSES

    return FirebaseSettings(
        enabled=bool(firebase_section.get("enabled", False)),
        project_id=str(firebase_section.get("project_id", "")).strip(),
        storage_bucket=str(firebase_section.get("storage_bucket", "")).strip().removeprefix("gs://"),
        service_account=service_account,
        teacher_emails=teachers,
        class_ids=classes,
    )


def resolve_role(email: str, teacher_emails: Iterable[str]) -> str:
    normalized = email.strip().casefold()
    allowed = {item.strip().casefold() for item in teacher_emails}
    return "teacher" if normalized and normalized in allowed else "student"


def require_role(identity: AppIdentity, *allowed_roles: str, owner_uid: str | None = None) -> None:
    if identity.role not in allowed_roles:
        raise AuthorizationError("이 작업을 수행할 권한이 없습니다.")
    if owner_uid is not None and identity.role != "teacher" and identity.uid != owner_uid:
        raise AuthorizationError("다른 학생의 자료에는 접근할 수 없습니다.")


def validate_upload(filename: str, content_type: str, size: int) -> str | None:
    limit = ALLOWED_UPLOAD_TYPES.get(content_type)
    if limit is None:
        return f"허용되지 않은 파일 형식입니다: {filename} ({content_type or '형식 없음'})"
    if size <= 0:
        return f"빈 파일은 제출할 수 없습니다: {filename}"
    if size > limit:
        return f"파일 용량 제한을 초과했습니다: {filename} ({limit // (1024 * 1024)}MB 이하)"
    return None


def safe_storage_name(filename: str) -> str:
    name = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", filename.strip())
    name = name.strip("._") or "upload.bin"
    return name[:120]


def _json_safe(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return _json_safe(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "__dict__"):
        return _json_safe(vars(value))
    return str(value)


def build_learning_snapshot(identity: AppIdentity) -> dict[str, Any]:
    """Create an explicit, secret-free snapshot from the current learning session."""
    keys = (
        "hq_selected_unit",
        "hq_selected_scenario_id",
        "hq_unit_questions",
        "hq_scenarios_generated",
        "hq_submissions",
        "hq_reflections",
        "hq_team_notes",
        "hq_xp",
        "hq_growth_log",
        "hq_earned_badges",
        "ragpbl_questions",
        "ragpbl_prior_knowledge",
        "ragpbl_selected_id",
        "ragpbl_problem",
        "ragpbl_hypothesis",
        "ragpbl_action_plan",
        "ragpbl_evidence",
        "ragpbl_evaluation",
        "ragpbl_review",
        "ragpbl_attempts",
        "ragpbl_source_mode",
    )
    snapshot = {key: _json_safe(st.session_state.get(key)) for key in keys}
    snapshot.update(
        {
            "student_uid": identity.uid,
            "student_email": identity.email,
            "student_name": identity.name,
            "class_id": identity.class_id,
            "student_number": identity.student_number,
        }
    )
    return snapshot


class FirebaseBackend:
    """Small privileged backend. Every public method enforces application authorization."""

    def __init__(self, settings: FirebaseSettings):
        if not settings.configured:
            raise FirebaseConfigurationError("Firebase 서버 설정이 완성되지 않았습니다.")
        try:
            import firebase_admin
            from firebase_admin import credentials, firestore, storage
        except ImportError as exc:
            raise FirebaseConfigurationError("firebase-admin 패키지가 설치되지 않았습니다.") from exc

        app_name = f"health-quest-{settings.project_id}"
        try:
            app = firebase_admin.get_app(app_name)
        except ValueError:
            credential = credentials.Certificate(settings.service_account)
            app = firebase_admin.initialize_app(
                credential,
                {"projectId": settings.project_id, "storageBucket": settings.storage_bucket},
                name=app_name,
            )

        self._firestore = firestore
        self.db = firestore.client(app=app)
        self.bucket = storage.bucket(app=app)

    def get_user_profile(self, uid: str) -> dict[str, Any] | None:
        if not uid:
            return None
        snapshot = self.db.collection("users").document(uid).get()
        return snapshot.to_dict() if snapshot.exists else None

    def upsert_user_profile(self, identity: AppIdentity, profile: Mapping[str, Any]) -> None:
        require_role(identity, "student", "teacher", owner_uid=identity.uid)
        allowed = {
            "uid": identity.uid,
            "email": identity.email,
            "display_name": str(profile.get("display_name", identity.name)).strip()[:80],
            "role": identity.role,
            "class_id": str(profile.get("class_id", identity.class_id)).strip()[:30],
            "student_number": str(profile.get("student_number", identity.student_number)).strip()[:30],
            "updated_at": self._firestore.SERVER_TIMESTAMP,
        }
        self.db.collection("users").document(identity.uid).set(allowed, merge=True)

    def save_learning_snapshot(self, identity: AppIdentity, snapshot: Mapping[str, Any]) -> None:
        require_role(identity, "student", owner_uid=identity.uid)
        unit = str(snapshot.get("hq_selected_unit", "미선택"))
        unit_key = hashlib.sha256(unit.encode("utf-8")).hexdigest()[:20]
        doc_id = f"{identity.uid}_{unit_key}"
        payload = dict(_json_safe(snapshot))
        payload["updated_at"] = self._firestore.SERVER_TIMESTAMP
        self.db.collection("learning_sessions").document(doc_id).set(payload, merge=True)

    def create_submission(
        self,
        identity: AppIdentity,
        metadata: Mapping[str, Any],
        files: list[tuple[str, str, bytes]],
    ) -> str:
        require_role(identity, "student", owner_uid=identity.uid)
        submission_id = uuid.uuid4().hex
        stored_files: list[dict[str, Any]] = []
        for filename, content_type, data in files:
            error = validate_upload(filename, content_type, len(data))
            if error:
                raise ValueError(error)
            safe_name = safe_storage_name(filename)
            path = (
                f"classes/{identity.class_id}/students/{identity.uid}/"
                f"submissions/{submission_id}/{safe_name}"
            )
            blob = self.bucket.blob(path)
            blob.upload_from_string(data, content_type=content_type)
            stored_files.append(
                {
                    "name": safe_name,
                    "content_type": content_type,
                    "size": len(data),
                    "storage_path": f"gs://{self.bucket.name}/{path}",
                }
            )

        payload = {
            "submission_id": submission_id,
            "student_uid": identity.uid,
            "student_email": identity.email,
            "student_name": identity.name,
            "class_id": identity.class_id,
            "student_number": identity.student_number,
            "unit_name": str(metadata.get("unit_name", ""))[:200],
            "submission_type": str(metadata.get("submission_type", "개인"))[:30],
            "team_members": str(metadata.get("team_members", ""))[:500],
            "reflection": str(metadata.get("reflection", ""))[:5000],
            "status": "submitted",
            "files": stored_files,
            "teacher_feedback": "",
            "rubric_score": None,
            "submitted_at": self._firestore.SERVER_TIMESTAMP,
            "updated_at": self._firestore.SERVER_TIMESTAMP,
        }
        self.db.collection("submissions").document(submission_id).set(payload)
        return submission_id

    def list_student_submissions(self, identity: AppIdentity) -> list[dict[str, Any]]:
        require_role(identity, "student", owner_uid=identity.uid)
        query = self.db.collection("submissions").where("student_uid", "==", identity.uid).stream()
        return [item.to_dict() for item in query]

    def list_teacher_dashboard(self, identity: AppIdentity) -> dict[str, list[dict[str, Any]]]:
        require_role(identity, "teacher")
        users = [item.to_dict() for item in self.db.collection("users").stream()]
        sessions = [item.to_dict() for item in self.db.collection("learning_sessions").stream()]
        submissions = [item.to_dict() for item in self.db.collection("submissions").stream()]
        return {"users": users, "sessions": sessions, "submissions": submissions}

    def save_teacher_feedback(
        self,
        identity: AppIdentity,
        submission_id: str,
        score: int,
        feedback: str,
        status: str,
    ) -> None:
        require_role(identity, "teacher")
        if status not in {"reviewing", "revision_requested", "evaluated"}:
            raise ValueError("허용되지 않은 평가 상태입니다.")
        self.db.collection("submissions").document(submission_id).update(
            {
                "rubric_score": int(score),
                "teacher_feedback": feedback.strip()[:5000],
                "status": status,
                "teacher_uid": identity.uid,
                "evaluated_at": self._firestore.SERVER_TIMESTAMP,
                "updated_at": self._firestore.SERVER_TIMESTAMP,
            }
        )

    def signed_download_url(self, identity: AppIdentity, storage_path: str) -> str:
        require_role(identity, "teacher")
        prefix = f"gs://{self.bucket.name}/"
        if not storage_path.startswith(prefix):
            raise ValueError("잘못된 Storage 경로입니다.")
        blob = self.bucket.blob(storage_path.removeprefix(prefix))
        return blob.generate_signed_url(expiration=timedelta(minutes=15), method="GET")


def get_backend(settings: FirebaseSettings | None = None) -> FirebaseBackend:
    return FirebaseBackend(settings or load_firebase_settings())


def current_identity() -> AppIdentity | None:
    raw = st.session_state.get("hq_identity")
    if not isinstance(raw, Mapping):
        return None
    required = ("uid", "email", "name", "role")
    if not all(raw.get(key) for key in required):
        return None
    return AppIdentity(
        uid=str(raw["uid"]),
        email=str(raw["email"]),
        name=str(raw["name"]),
        role=str(raw["role"]),
        class_id=str(raw.get("class_id", "")),
        student_number=str(raw.get("student_number", "")),
    )


def _user_value(key: str, default: str = "") -> str:
    try:
        value = getattr(st.user, key, None)
        if value is None and hasattr(st.user, "get"):
            value = st.user.get(key, default)
        return str(value or default)
    except Exception:
        return default


def _render_login_landing() -> None:
    st.markdown(
        """
        <style>
        .hq-login-hero {padding:2.2rem;border-radius:28px;background:linear-gradient(135deg,#f8f2e8,#e7f1ea);margin:1rem 0 1.4rem}
        .hq-login-hero h1 {color:#17324d;margin:0 0 .5rem;font-size:2.25rem}
        .hq-login-hero p {color:#55645d;margin:0}
        </style>
        <div class="hq-login-hero">
          <h1>HEALTH QUEST</h1>
          <p>간호 미션에서 모의상황실습까지 이어지는 참여형 보건간호 PBL</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    student_col, teacher_col = st.columns(2, gap="large")
    with student_col.container(border=True, height="stretch"):
        st.subheader("학생 로그인", icon=":material/school:")
        st.write("기초·중급·심화 미션, 모의상황실습, 수행평가 제출을 진행합니다.")
        if st.button("Google 계정으로 학생 로그인", type="primary", use_container_width=True):
            st.login("google")
    with teacher_col.container(border=True, height="stretch"):
        st.subheader("교사 로그인", icon=":material/admin_panel_settings:")
        st.write("담당 학급의 진도, 제출 파일, 음성 기록과 평가 피드백을 확인합니다.")
        if st.button("Google 계정으로 교사 로그인", use_container_width=True):
            st.login("google")
    st.caption("교사 화면은 사전에 등록한 두 Google 계정만 접근할 수 있습니다.")


def require_authenticated_identity(settings: FirebaseSettings) -> AppIdentity | None:
    """Require Google OIDC only when Firebase mode is explicitly enabled."""
    if not settings.enabled:
        st.session_state["hq_firebase_mode"] = False
        st.session_state.pop("hq_identity", None)
        return None

    st.session_state["hq_firebase_mode"] = True
    try:
        logged_in = bool(st.user.is_logged_in)
    except Exception:
        logged_in = False
    if not logged_in:
        _render_login_landing()
        st.stop()

    email = _user_value("email").strip().casefold()
    uid = _user_value("sub") or _user_value("id")
    name = _user_value("name") or email.split("@", 1)[0]
    if not email or not uid:
        st.error("Google 로그인 정보에서 이메일 또는 사용자 ID를 확인할 수 없습니다.")
        st.button("로그아웃", on_click=st.logout)
        st.stop()

    role = resolve_role(email, settings.teacher_emails)
    provisional = AppIdentity(uid=uid, email=email, name=name, role=role)
    try:
        backend = get_backend(settings)
        profile = backend.get_user_profile(uid) or {}
    except Exception as exc:
        st.error("Firebase 서버 연결 설정을 확인해 주세요.")
        st.caption(f"오류 유형: {type(exc).__name__}")
        st.stop()

    if role == "teacher":
        identity = provisional
        backend.upsert_user_profile(identity, {"display_name": name})
    else:
        class_id = str(profile.get("class_id", ""))
        student_number = str(profile.get("student_number", ""))
        display_name = str(profile.get("display_name", name))
        if not class_id or not student_number:
            st.header("학생 정보 등록", icon=":material/badge:")
            st.write("처음 한 번만 학급과 학번을 등록합니다. 이후 학습기록은 이 계정에 연결됩니다.")
            with st.form("student_profile_form"):
                entered_name = st.text_input("이름", value=display_name, max_chars=40)
                selected_class = st.selectbox("학급", settings.class_ids)
                entered_number = st.text_input("학번", placeholder="예: 2101", max_chars=30)
                saved = st.form_submit_button("학생 정보 저장", type="primary", use_container_width=True)
            if saved:
                if not entered_name.strip() or not entered_number.strip():
                    st.error("이름과 학번을 입력해 주세요.")
                else:
                    identity = AppIdentity(
                        uid=uid,
                        email=email,
                        name=entered_name.strip(),
                        role="student",
                        class_id=selected_class,
                        student_number=entered_number.strip(),
                    )
                    backend.upsert_user_profile(identity, identity.as_dict())
                    st.session_state["hq_identity"] = identity.as_dict()
                    st.rerun()
            st.stop()
        identity = AppIdentity(
            uid=uid,
            email=email,
            name=display_name,
            role="student",
            class_id=class_id,
            student_number=student_number,
        )

    st.session_state["hq_identity"] = identity.as_dict()
    if identity.role == "student":
        st.session_state["hq_student_label"] = f"{identity.student_number} {identity.name}".strip()
    return identity


def persist_current_learning_state(settings: FirebaseSettings, identity: AppIdentity | None) -> None:
    if not settings.enabled or identity is None or identity.role != "student":
        return
    snapshot = build_learning_snapshot(identity)
    serialized = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    if st.session_state.get("hq_last_firebase_snapshot_hash") == digest:
        return
    try:
        get_backend(settings).save_learning_snapshot(identity, snapshot)
        st.session_state["hq_last_firebase_snapshot_hash"] = digest
        st.session_state["hq_firebase_sync_error"] = ""
    except Exception as exc:
        st.session_state["hq_firebase_sync_error"] = type(exc).__name__
