"""학생용 HEALTH QUEST의 RAG·Multi-Agent 피드백 연결부.

정답 판정과 보상은 quest_engine의 결정적 로직이 담당한다. 이 모듈은 학생이
제출 버튼을 눌렀을 때만 서술형 코칭을 생성하며, 설정이나 API 호출에 문제가
있으면 호출자가 기존 규칙형 피드백을 그대로 사용하도록 실패 안전하게 동작한다.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from dataclasses import dataclass

import streamlit as st
from agents import Agent, FileSearchTool, OpenAIResponsesModel, RunConfig, Runner, set_tracing_disabled
from openai import AsyncOpenAI


set_tracing_disabled(True)


@dataclass(frozen=True)
class AIConfig:
    api_key: str
    vector_store_id: str
    model: str
    enabled: bool = True


@dataclass(frozen=True)
class AIResult:
    text: str
    mode: str
    teacher_review_needed: bool = False
    error: str = ""


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


def get_ai_config() -> AIConfig:
    default_vs = _secret_or_env("VECTOR_STORE_ID")
    enabled_value = _secret_or_env("HEALTH_QUEST_AI_ENABLED", default="true").lower()
    return AIConfig(
        api_key=_secret_or_env("OPENAI_API_KEY"),
        vector_store_id=_secret_or_env("VECTOR_STORE_FILE_ID", default=default_vs),
        model=_secret_or_env("MODEL", "OPENAI_MODEL", default="gpt-4.1-mini"),
        enabled=enabled_value not in {"0", "false", "off", "no"},
    )


def get_ai_status() -> dict:
    """키나 Vector Store 식별자를 노출하지 않는 읽기 전용 연결 상태."""
    config = get_ai_config()
    return {
        "configured": bool(config.enabled and config.api_key and config.vector_store_id),
        "api_key_configured": bool(config.api_key),
        "rag_configured": bool(config.vector_store_id),
        "model": config.model,
    }


def _plain_text(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\*{1,3}", "", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    return text.strip()


def _cache_key(task: str, prompt: str, model: str, vector_store_id: str) -> str:
    raw = "\n".join((task, prompt, model, vector_store_id))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _contains_safety_risk(text: str) -> bool:
    keywords = (
        "주사", "채혈", "침습", "투약", "처방", "약 복용", "도뇨", "절개",
        "락스", "에탄올", "소독제 혼합", "실제 환자", "응급", "자해", "진단",
    )
    return any(keyword in text for keyword in keywords)


async def _run_agents(config: AIConfig, task: str, prompt: str) -> AIResult:
    rag = FileSearchTool(vector_store_ids=[config.vector_store_id], max_num_results=3)
    inquiry = Agent(
        name="Inquiry Coach",
        instructions=(
            "너는 특성화고등학교 간호학과 학생을 돕는 보건교육 코치다. "
            "반드시 연결된 Knowledge를 검색해 근거가 확인되는 범위에서만 답한다. "
            "학생의 답을 대신 완성하지 말고, 잘한 판단과 놓친 단서, 다음에 스스로 생각할 질문을 제시한다. "
            "출처를 지어내지 말고 근거가 부족하면 '교사 검토 필요'라고 쓴다. "
            "실제 환자 진단이나 처방을 하지 않는다. 별표·해시·이모지 같은 장식 문자를 사용하지 않는다."
        ),
        tools=[rag],
    )
    safety = Agent(
        name="Safety Reviewer",
        instructions=(
            "너는 고등학교 간호 모의실습의 안전 검토자다. 침습 처치, 투약, 화학물질 혼합, "
            "실제 환자 정보, 학생 업무 범위를 점검한다. 위험 행동은 구체적 실행법을 제공하지 말고 "
            "비침습적 모의실습과 교사·의료인 확인으로 전환한다. 별표·해시·이모지를 쓰지 않는다."
        ),
    )
    inquiry_tool = inquiry.as_tool(
        tool_name="review_with_knowledge",
        tool_description="연결된 수업 자료를 검색하여 간호 개념과 학생 판단의 근거를 검토한다.",
    )
    tools = [inquiry_tool]
    risk = _contains_safety_risk(prompt)
    if risk:
        tools.append(
            safety.as_tool(
                tool_name="review_safety",
                tool_description="간호 모의실습의 위험 요소와 학생 업무 범위를 검토한다.",
            )
        )

    manager = Agent(
        name="Health Inquiry Manager",
        instructions=(
            "너는 AI 보건튜터 팀의 조정자다. review_with_knowledge를 반드시 호출한다. "
            + ("안전 위험이 있으므로 review_safety도 반드시 호출한다. " if risk else "")
            + "도구 결과를 종합해 학생 눈높이의 자연스러운 한국어 피드백을 작성한다. "
            "정답을 새로 판정하거나 점수를 바꾸지 않는다. 제공된 학습 맥락과 학생 서술만 다룬다. "
            "확인되지 않은 내용은 교사 검토 필요로 표시한다. 별표, 해시, 이모지, 과도한 제목을 쓰지 않는다."
        ),
        tools=tools,
    )

    async with AsyncOpenAI(api_key=config.api_key) as client:
        model = OpenAIResponsesModel(model=config.model, openai_client=client)
        turn_inquiry = inquiry.clone(model=model)
        turn_tools = [
            turn_inquiry.as_tool(
                tool_name="review_with_knowledge",
                tool_description="연결된 수업 자료를 검색하여 간호 개념과 학생 판단의 근거를 검토한다.",
            )
        ]
        if risk:
            turn_safety = safety.clone(model=model)
            turn_tools.append(
                turn_safety.as_tool(
                    tool_name="review_safety",
                    tool_description="간호 모의실습의 위험 요소와 학생 업무 범위를 검토한다.",
                )
            )
        turn_manager = manager.clone(model=model, tools=turn_tools)
        result = await Runner.run(
            turn_manager,
            prompt,
            max_turns=6,
            run_config=RunConfig(tracing_disabled=True),
        )

    tool_names = set()
    for item in result.new_items:
        if getattr(item, "type", "") == "tool_call_item":
            raw_item = getattr(item, "raw_item", None)
            tool_name = getattr(raw_item, "name", "")
            if tool_name:
                tool_names.add(tool_name)
    if "review_with_knowledge" not in tool_names:
        raise RuntimeError("RAG inquiry agent was not called")
    if risk and "review_safety" not in tool_names:
        raise RuntimeError("Safety reviewer was not called")

    text = _plain_text(result.final_output)
    return AIResult(
        text=text,
        mode="RAG·Multi-Agent" if risk else "RAG·Inquiry Coach",
        teacher_review_needed="교사 검토 필요" in text,
    )


def generate_grounded_feedback(task: str, prompt: str, fallback: str) -> AIResult:
    """명시적 제출 시 RAG 피드백을 생성하고, 실패하면 규칙형 결과를 반환한다."""
    config = get_ai_config()
    if not config.enabled or not config.api_key or not config.vector_store_id:
        return AIResult(_plain_text(fallback), "규칙형 안전 모드")

    key = _cache_key(task, prompt, config.model, config.vector_store_id)
    cache = st.session_state.setdefault("hq_ai_feedback_cache", {})
    cached = cache.get(key)
    if cached:
        return AIResult(**cached)

    try:
        result = asyncio.run(_run_agents(config, task, prompt))
        if not result.text:
            raise ValueError("empty AI response")
    except Exception as exc:
        result = AIResult(
            _plain_text(fallback),
            "규칙형 안전 모드",
            teacher_review_needed=True,
            error=type(exc).__name__,
        )

    cache[key] = {
        "text": result.text,
        "mode": result.mode,
        "teacher_review_needed": result.teacher_review_needed,
        "error": result.error,
    }
    return result
