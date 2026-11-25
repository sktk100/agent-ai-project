"""RAG 기반 단일 에이전트 PBL 학습 로직.

기존 단계형 HEALTH QUEST 앱과 분리된 새 학생용 앱에서 사용한다. 학생 질문을
근거 자료로 분석해 모의실습 후보 3개를 만들고, 선택한 상황의 서술형 PBL 답안을
코칭한다. 정답·진도·저장은 AI가 결정하지 않는다.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from typing import Literal

import streamlit as st
from agents import Agent, FileSearchTool, OpenAIResponsesModel, RunConfig, Runner, set_tracing_disabled
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, field_validator


set_tracing_disabled(True)

KnowledgeLevel = Literal["기초", "발전", "심화"]


class ScenarioCandidate(BaseModel):
    id: Literal["A", "B", "C"]
    title: str
    hospital_situation: str
    why_this_fits: str
    concepts_to_use: list[str]
    clinical_clues: list[str]
    pbl_task: str
    evidence_summary: str


class DiscoveryResult(BaseModel):
    knowledge_level: KnowledgeLevel
    level_reason: str
    question_summary: str
    key_concepts: list[str]
    scenarios: list[ScenarioCandidate]
    safety_note: str
    teacher_review_needed: bool = False

    @field_validator("scenarios")
    @classmethod
    def validate_three_scenarios(cls, value: list[ScenarioCandidate]) -> list[ScenarioCandidate]:
        if len(value) != 3 or [item.id for item in value] != ["A", "B", "C"]:
            raise ValueError("모의실습 후보는 A, B, C 세 개여야 합니다.")
        return value


class PBLReview(BaseModel):
    knowledge_alignment: str
    strengths: list[str]
    evidence_gaps: list[str]
    safety_feedback: str
    coaching_questions: list[str]
    revision_task: str
    concept_score: int = Field(ge=0, le=3)
    reasoning_score: int = Field(ge=0, le=3)
    evidence_score: int = Field(ge=0, le=3)
    reflection_score: int = Field(ge=0, le=3)
    teacher_review_needed: bool = False
    teacher_review_reason: str = ""


@dataclass(frozen=True)
class RAGConfig:
    api_key: str
    vector_store_ids: tuple[str, ...]
    model: str

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.vector_store_ids)


@dataclass(frozen=True)
class GuardResult:
    safe: bool
    message: str = ""


def _secret_or_env(*names: str, default: str = "") -> str:
    for name in names:
        try:
            value = st.secrets.get(name)
        except (FileNotFoundError, KeyError, AttributeError):
            value = None
        if value:
            return str(value).strip()
        value = os.getenv(name)
        if value:
            return value.strip()
    return default


def get_rag_config() -> RAGConfig:
    if os.getenv("RAG_PBL_FORCE_DEMO", "").lower() in {"1", "true", "yes", "on"}:
        return RAGConfig(api_key="", vector_store_ids=(), model="demo")
    default_store = _secret_or_env("VECTOR_STORE_ID")
    candidates = (
        _secret_or_env("VECTOR_STORE_FILE_ID", default=default_store),
        _secret_or_env("VECTOR_STORE_URL_ID"),
    )
    stores = tuple(dict.fromkeys(item for item in candidates if item))
    return RAGConfig(
        api_key=_secret_or_env("OPENAI_API_KEY"),
        vector_store_ids=stores,
        model=_secret_or_env("RAG_PBL_MODEL", "MODEL", "OPENAI_MODEL", default="gpt-4.1-mini"),
    )


def rag_status() -> dict[str, object]:
    config = get_rag_config()
    return {
        "configured": config.configured,
        "source_count": len(config.vector_store_ids),
        "model": config.model,
    }


PII_PATTERNS = (
    re.compile(r"(?<!\d)\d{6}\s*[- ]?\s*[1-4]\d{6}(?!\d)"),
    re.compile(r"(?<!\d)01[016789][ -]?\d{3,4}[ -]?\d{4}(?!\d)"),
    re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])"),
)
REAL_CARE_PATTERNS = (
    "내가 먹을 약", "무슨 약을 먹", "복용량", "몇 알", "처방해", "진단해",
    "실제 환자", "환자 실명", "주민등록", "정맥주사 놓는 법", "주사 놓는 법",
)


def guard_student_text(text: str) -> GuardResult:
    normalized = " ".join(str(text or "").split())
    if any(pattern.search(normalized) for pattern in PII_PATTERNS):
        return GuardResult(False, "개인 식별정보가 포함된 것 같습니다. 이름·전화번호·이메일·주민등록번호를 지우고 다시 작성해 주세요.")
    if any(token in normalized for token in REAL_CARE_PATTERNS):
        return GuardResult(False, "이곳은 가상 모의실습용입니다. 실제 환자 진단·처방·투약 질문은 입력하지 말고 교사 또는 의료진에게 확인해 주세요.")
    return GuardResult(True)


DISCOVERY_INSTRUCTIONS = """너는 특성화고등학교 간호학과 학생을 위한 RAG 기반 단일 AI 보건튜터다.
너 혼자 질문 분석, 지식수준 추정, 사례 생성까지 수행한다. 연결된 교과서와 공공 보건의료 자료를 file search로 반드시 검색한다.

목표:
1. 학생이 작성한 의학·간호·의학용어 질문 2~3개와 자기 설명을 분석한다.
2. 지식수준을 기초·발전·심화 중 하나로 추정하되 점수나 낙인처럼 표현하지 않는다. 질문 수만으로 판단하지 말고 개념 연결, 임상 단서, 근거 표현을 함께 본다.
3. 선택한 수업 단원 안에서 병원과 유사하지만 완전히 가상인 모의실습 후보 A·B·C를 정확히 3개 만든다. 세 후보는 맥락과 핵심 과제가 서로 달라야 한다.
4. 학생이 정답을 고르는 문제가 아니라 단원 지식을 총동원해 PBL 서술형 해결안을 작성할 수 있도록 각 후보에 임상 단서와 열린 과제를 제공한다.

안전 규칙:
- 실제 환자 개인정보, 실제 진단·처방, 약물 용량, 침습 처치 수행 절차를 만들지 않는다.
- 학생 업무 범위는 관찰, 사정 보조, 비침습적 안전 간호, 보고, 의사소통, 교육 모의실습으로 제한한다.
- 근거가 확인되지 않으면 교사 검토 필요로 표시한다. 출처명·페이지·수치를 지어내지 않는다.
- 학생 질문을 그대로 정답으로 평가하지 않는다. 별표, 해시, 이모지 장식을 쓰지 않는다.
"""


REVIEW_INSTRUCTIONS = """너는 특성화고등학교 간호학과 학생의 서술형 PBL 답안을 코칭하는 RAG 기반 단일 AI 보건튜터다.
연결된 교과서와 공공 보건의료 자료를 file search로 반드시 검색해 선택된 가상 상황과 학생 답안을 검토한다.

평가 원칙:
- 답안을 대신 완성하지 않는다. 잘한 점, 놓친 임상 단서, 근거가 필요한 부분, 다음 수정 질문을 제시한다.
- 개념 적용, 임상 추론, 근거 활용, 성찰을 각각 0~3점으로 표시한다. 총점 경쟁이나 공개 순위에는 사용하지 않는다.
- 선택지 정답 판정이 아니라 서술의 질을 코칭한다. 빈 답이나 예시 복사는 높은 점수를 주지 않는다.
- 실제 진단·처방·약물 용량·침습 처치 절차는 제공하지 않는다. 위험하거나 근거가 불명확하면 교사 검토 필요로 표시한다.
- 별표, 해시, 이모지 장식을 쓰지 않는다.
"""


async def _run_structured(config: RAGConfig, instructions: str, prompt: str, output_type: type[BaseModel]):
    if not config.configured:
        raise RuntimeError("RAG configuration is incomplete")
    search = FileSearchTool(vector_store_ids=list(config.vector_store_ids), max_num_results=6)
    async with AsyncOpenAI(api_key=config.api_key) as client:
        model = OpenAIResponsesModel(model=config.model, openai_client=client)
        agent = Agent(
            name="RAG PBL Health Tutor",
            instructions=instructions,
            model=model,
            tools=[search],
            output_type=output_type,
        )
        result = await Runner.run(
            agent,
            prompt,
            max_turns=5,
            run_config=RunConfig(tracing_disabled=True),
        )
    return result.final_output


def _run(coro):
    try:
        return asyncio.run(coro)
    except RuntimeError as exc:
        if "asyncio.run() cannot be called" not in str(exc):
            raise
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def generate_scenarios(
    *,
    subject: str,
    unit: str,
    questions: list[str],
    prior_knowledge: str,
) -> DiscoveryResult:
    config = get_rag_config()
    prompt = f"""수업 교과: {subject}
수업 단원: {unit}
학생 질문:
{chr(10).join(f'{index + 1}. {question}' for index, question in enumerate(questions))}
학생이 자기 말로 설명한 현재 지식: {prior_knowledge}

위 입력과 검색 근거를 바탕으로 지식수준을 진단하고 모의실습 상황 A, B, C를 생성하라."""
    return _run(_run_structured(config, DISCOVERY_INSTRUCTIONS, prompt, DiscoveryResult))


def review_pbl_answer(
    *,
    subject: str,
    unit: str,
    discovery: DiscoveryResult,
    scenario: ScenarioCandidate,
    problem_definition: str,
    hypothesis: str,
    action_plan: str,
    evidence: str,
    evaluation: str,
) -> PBLReview:
    config = get_rag_config()
    prompt = f"""수업 교과: {subject}
수업 단원: {unit}
학생 지식수준 추정: {discovery.knowledge_level} ({discovery.level_reason})
선택 상황: {scenario.model_dump_json(ensure_ascii=False)}

학생 PBL 답안
문제 정의와 핵심 단서: {problem_definition}
가설과 판단: {hypothesis}
우선순위 해결안: {action_plan}
수업 지식에 근거한 이유: {evidence}
평가 방법과 성찰: {evaluation}

검색 근거에 비추어 학생이 스스로 수정할 수 있는 코칭 피드백을 작성하라."""
    return _run(_run_structured(config, REVIEW_INSTRUCTIONS, prompt, PBLReview))
