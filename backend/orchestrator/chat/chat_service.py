from __future__ import annotations

from datetime import datetime
from time import perf_counter
from typing import Any, Dict, List
from uuid import uuid4

from fastapi import Request
from sqlalchemy.orm import Session

from .flow_trace import (
    build_admin_flow_trace,
    build_lightweight_flow_trace,
    build_multi_command_plan,
)
from .project_context_store import get_project_context_bundle, upsert_project_memory_snapshot
from .llm_client import call_orchestrator_chat_llm
from .models import (
    AutoConnectMeta,
    AdvisoryEvidenceItem,
    AdvisoryNextAction,
    AdvisoryQuestion,
    ConversationMessage,
    OrchestratorChatRequest,
    OrchestratorChatResponse,
    ProposalItem,
    TargetPatchHint,
)


def _normalize_chat_message(message: str) -> str:
    return " ".join(str(message or "").strip().split())


def _contains_any(text: str, tokens: List[str]) -> bool:
    return any(token in text for token in tokens)


def _is_banter_message(message: str) -> bool:
    normalized = _normalize_chat_message(message)
    lowered = normalized.lower().rstrip("!?.,~ ")
    if not lowered:
        return False
    if lowered in {
        "안녕",
        "안녕하세요",
        "반가워",
        "반갑습니다",
        "고마워",
        "감사해",
        "감사합니다",
        "오케이",
        "okay",
        "ok",
        "hello",
        "hi",
        "hey",
    }:
        return True
    if len(lowered) <= 12 and _contains_any(lowered, ["ㅋㅋ", "ㅎㅎ", "ㄱㅅ", "thx", "thanks"]):
        return True
    return False


def _is_meta_conversation_question(message: str) -> bool:
    lowered = _normalize_chat_message(message).lower()
    if not lowered:
        return False
    return _contains_any(
        lowered,
        [
            "자유롭게 질문",
            "자유 질문",
            "질문해도 돼",
            "물어봐도 돼",
            "잡담해도 돼",
            "대화해도 돼",
            "이어서 물어봐도 돼",
            "편하게 질문",
            "질문 가능",
            "잡담 가능",
            "그냥 이어서 물어봐도 돼",
        ],
    )


def _looks_like_question(message: str) -> bool:
    normalized = _normalize_chat_message(message)
    lowered = normalized.lower()
    if not lowered:
        return False
    if "?" in normalized or "？" in normalized:
        return True
    if _is_meta_conversation_question(normalized):
        return True
    if lowered.startswith(("왜", "뭐", "무엇", "어떻게", "어디", "언제", "누가", "혹시", "그럼")):
        return True
    if _contains_any(
        lowered,
        [
            "궁금",
            "가능할까",
            "가능해",
            "될까",
            "되나",
            "되나요",
            "맞아",
            "맞나요",
            "알 수 있을까",
            "설명해줄 수",
            "비교해줄 수",
            "왜 그런지",
            "무슨 차이",
            "어떤 차이",
        ],
    ):
        return True
    return lowered.endswith(("인가", "인가요", "일까", "일까요", "될까", "될까요", "되나요", "맞나요", "있나요", "가능해", "가능한가"))


def _looks_like_directive(message: str) -> bool:
    lowered = _normalize_chat_message(message).lower()
    if not lowered:
        return False
    if lowered.startswith(("/run", "/pass", "/fix", "/fail", "/verify", "/search", "/news", "/ask", "/revise", "/resume")):
        return True
    if _contains_any(
        lowered,
        [
            "수정해줘",
            "구현해줘",
            "만들어줘",
            "바꿔줘",
            "고쳐줘",
            "다듬어줘",
            "정리해줘",
            "추가해줘",
            "삭제해줘",
            "적용해줘",
            "연결해줘",
            "연동해줘",
            "실행해줘",
            "검증해줘",
            "찾아줘",
            "분석해줘",
            "비교해줘",
            "설명해줘",
            "알려줘",
            "작성해줘",
            "해주세요",
            "부탁해",
            "부탁합니다",
            "진행해줘",
        ],
    ):
        return True
    return False


def infer_message_kind(message: str) -> str:
    normalized = _normalize_chat_message(message)
    if not normalized:
        return "general"
    if _is_banter_message(normalized):
        return "general"
    if _looks_like_question(normalized):
        return "question"
    if _looks_like_directive(normalized):
        return "directive"
    return "general"


def build_fast_admin_chat_reply(
    message: str,
    *,
    requested_conversation_mode: str,
    message_kind: str,
    conversation_stage: str,
) -> str:
    normalized = message.strip()
    lowered = normalized.lower()

    if not normalized:
        return "입력 내용을 한 줄로 다시 보내주세요. 바로 이어서 정리해드리겠습니다."

    if lowered in {"안녕", "안녕하세요", "hello", "hi", "hey"}:
        return "안녕하세요. 네, 자유 질문·잡담·구현 지시 모두 가능합니다. 편하게 이어서 말씀해 주세요."

    if _is_banter_message(normalized):
        return "그럴 수 있죠. 잠깐 숨 고르고, 편하게 이어서 이야기해 주세요. 그냥 잡담처럼 말해도 되고 바로 질문이나 작업 요청으로 넘어가도 됩니다."

    if _is_meta_conversation_question(normalized):
        return "네, 가능합니다. 자유 질문도 받고, 잡담형 대화도 이어갈 수 있고, 필요하면 구현 지시로도 바로 전환할 수 있습니다. 편하게 이어서 물어보세요."

    if len(normalized) <= 24 and message_kind in {"general", "question"} and conversation_stage == "general":
        return (
            f"현재 입력은 짧은 {conversation_stage} 대화로 인식했습니다.\n"
            "- 원하는 작업 또는 질문 범위를 한 줄 더 붙여 주세요.\n"
            "- 예: `이 파일 기준으로 중복 응답 제거해줘`\n"
            "- 예: `관리자 챗봇 타임아웃 응답을 더 짧게 바꿔줘`"
        )

    if requested_conversation_mode == "directive_fixed" and len(normalized) <= 40:
        return (
            "지시형 입력으로 받았습니다.\n"
            "- 수정 대상 파일\n"
            "- 원하는 결과\n"
            "- 금지하거나 유지할 조건\n"
            "이 세 가지만 덧붙이면 바로 실행형 답으로 좁혀드리겠습니다."
        )

    return ""


def build_admin_chat_fallback_reply(
    message: str,
    *,
    message_kind: str,
    conversation_stage: str,
    command_plan: List[Any],
) -> str:
    normalized = message.strip()
    reply_lines: List[str] = []

    if message_kind == "directive":
        reply_lines.extend([
            "지시형 요청으로 해석했습니다.",
            f"- 현재 단계: {conversation_stage}",
            f"- 요청 요약: {normalized}",
            "- 바로 필요한 정보: 수정 대상 파일 / 원하는 결과 / 유지 조건",
        ])
        if command_plan:
            reply_lines.append("- 바로 진행할 순서:")
            reply_lines.extend([f"  · {item.trace_id}: {item.command_text}" for item in command_plan[:3]])
        reply_lines.append("- 위 3가지를 주시면 다음 답변부터 실행형으로 더 짧고 정확하게 정리합니다.")
        return "\n".join(reply_lines)

    if message_kind == "question":
        reply_lines.extend([
            "질문형 요청으로 해석했습니다.",
            f"- 현재 단계: {conversation_stage}",
            f"- 질문 요약: {normalized}",
            "- 더 정확한 답을 위해 비교 대상이나 원하는 출력 형식을 한 줄만 추가해 주세요.",
            "- 예: 원인만 / 수정안만 / 파일 기준 / 우선순위 기준",
        ])
        return "\n".join(reply_lines)

    return "\n".join([
        "관리자 대화를 계속 이어갈 수 있습니다.",
        f"- 현재 단계: {conversation_stage}",
        f"- 입력 요약: {normalized}",
        "- 다음 입력은 한 번에 한 가지 작업만 적는 것이 가장 안정적입니다.",
    ])


def _build_approval_gate_warning(project_memory: Dict[str, Any], message: str) -> str:
    approval_gate = project_memory.get("approval_gate") if isinstance(project_memory, dict) else None
    if not isinstance(approval_gate, dict):
        return ""
    blocked_paths = [str(item).strip() for item in (approval_gate.get("blocked_paths") or []) if str(item).strip()]
    matched = [path for path in blocked_paths if path and path in message]
    if not matched:
        return ""
    return (
        "승인 게이트 경고:\n"
        + "\n".join([f"- 금지 경로 감지: {path}" for path in matched])
        + "\n- 승인 범위와 금지 경로를 먼저 조정하거나 해당 파일을 제외한 작업문으로 다시 요청하세요."
    )


def is_lightweight_chat_request(
    request_model: OrchestratorChatRequest,
    request_context: Request,
) -> bool:
    return bool(request_model.lightweight) or request_context.url.path.endswith("/light")


def build_chat_history_context(
    conversation: List[Dict[str, Any]],
    *,
    history_limit: int,
    char_budget: int,
    re_module,
) -> str:
    if history_limit <= 0 or char_budget <= 0:
        return "assistant: 이전 대화 없음"

    history_lines: List[str] = []
    remaining = char_budget
    for item in reversed(conversation[-history_limit:]):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "assistant")
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        compact_content = re_module.sub(r"\s+", " ", content)
        line = f"{role}: {compact_content[:remaining]}"
        if len(line) > remaining:
            line = line[:remaining]
        if not line:
            continue
        history_lines.append(line)
        remaining -= len(line) + 1
        if remaining <= 0:
            break

    if not history_lines:
        return "assistant: 이전 대화 없음"
    history_lines.reverse()
    return "\n".join(history_lines)


def build_chat_system_prompt(
    mode_label: str,
    conversation_stage: str,
    requested_conversation_mode: str,
    *,
    lightweight: bool,
    response_style: str,
    multi_turn_enabled: bool,
    context_tags: List[str],
) -> str:
    base_lines = [
        f"당신은 관리자 {mode_label} 오케스트레이터입니다.",
        "반드시 한국어로만 답변하세요.",
        "관리자 오케스트레이터는 정보 실험, 연구, 신기술 검토, 수동형/반자동 운영 보조 목적입니다.",
        "마켓플레이스 오케스트레이터처럼 자동 실행을 강제하지 말고, 연구·검토·지시 대화를 자율적으로 이어가세요.",
        "관리자 오케스트레이터는 동료 개발자처럼 자연스럽게 대화해야 합니다.",
        "사용자가 인터넷/최신 기술/조사를 요청하면 문제를 함께 정의하고, 핵심 개념·적용 방식·주의점·다음 선택지를 설명하세요.",
        "사용자가 자가 확장이나 필요한 기능을 말하면 어떤 분석, 신기술, 접근법이 적합한지 제안하고 이유를 설명하세요.",
        "사용자가 실험을 요청하면 실험 목적, 가설, 확인 방법, 기대 결과를 짧게 정리한 뒤 결과를 설명하는 동료 개발자 말투를 유지하세요.",
        "사용자가 결과를 반영해 자가확장해달라고 하면 코드 자동생성기와 연결되는 실행 제안, 수정 포인트, 검증 순서를 함께 제시하세요.",
        "항상 질문에 직접 답하고, 필요하면 조사 요약 → 실험 포인트 → 실행 제안 순서로 이어가세요.",
        f"현재 대화 단계: {conversation_stage}",
        f"현재 대화 모드: {requested_conversation_mode}",
        f"응답 스타일: {response_style}",
        f"멀티 대화 유지: {'enabled' if multi_turn_enabled else 'disabled'}",
        "응답은 질문에 직접 답한 뒤, 필요하면 근거/선택지/다음 단계만 짧게 덧붙이세요.",
    ]
    if context_tags:
        base_lines.append(f"현재 컨텍스트 태그: {', '.join(context_tags[:8])}")
    if lightweight:
        base_lines.extend([
            "경량 chat/light 경로입니다.",
            "불필요한 서론, 장황한 근거, 긴 실행 계획은 생략하세요.",
            "질문 요약 1줄, 핵심 답변, 필요 시 다음 단계 1개만 제시하세요.",
        ])
    else:
        base_lines.extend([
            "질문이 연구형이면 비교, 장단점, 리스크, 적용 가능성을 우선 정리하세요.",
            "질문이 지시형이면 구현 방향, 파일 단위 수정 포인트, 검증 순서를 우선 정리하세요.",
            "설명만 끝내지 말고 사용자가 바로 이어서 실험·검증·자가확장으로 넘어갈 수 있게 다음 선택지를 제안하세요.",
            "자동 패널 숨김 여부와 관계없이 대화 자체는 막지 말고 자유 질의에 계속 응답하세요.",
        ])
    return "\n".join(base_lines)


def build_chat_user_prompt(
    message: str,
    conversation_context: str,
    command_plan_lines: str,
    *,
    lightweight: bool,
    conversation_summary: str,
    message_kind: str,
    project_root: str,
    project_memory_summary: str,
) -> str:
    prompt_lines = [
        f"[메시지 종류]\n{message_kind}",
        f"[대화 요약]\n{conversation_summary}",
        f"[작업 프로젝트 루트]\n{project_root or '-'}",
        f"[프로젝트 메모리]\n{project_memory_summary}",
        f"[현재 질문]\n{message}",
        f"[최근 대화]\n{conversation_context}",
    ]
    if not lightweight:
        prompt_lines.append(f"[Flow 계획 후보]\n{command_plan_lines}")
    return "\n".join(prompt_lines)


def summarize_project_memory(project_memory: Dict[str, Any]) -> str:
    if not isinstance(project_memory, dict) or not project_memory:
        return "프로젝트 메모리 없음"

    summary_lines: List[str] = []
    project_name = str(project_memory.get("project_name") or "").strip()
    if project_name:
        summary_lines.append(f"- 프로젝트명: {project_name}")

    remembered_goal = str(project_memory.get("remembered_goal") or "").strip()
    if remembered_goal:
        summary_lines.append(f"- 현재 목표: {remembered_goal}")

    constraints = [str(item).strip() for item in (project_memory.get("constraints") or []) if str(item).strip()]
    if constraints:
        summary_lines.append("- 제약 조건: " + " / ".join(constraints[:4]))

    decisions = [str(item).strip() for item in (project_memory.get("decisions") or []) if str(item).strip()]
    if decisions:
        summary_lines.append("- 기억한 결정: " + " / ".join(decisions[:4]))

    pending_tasks = [str(item).strip() for item in (project_memory.get("pending_tasks") or []) if str(item).strip()]
    if pending_tasks:
        summary_lines.append("- 남은 작업: " + " / ".join(pending_tasks[:4]))

    last_experiment = str(project_memory.get("last_experiment") or "").strip()
    if last_experiment:
        summary_lines.append(f"- 최근 실험: {last_experiment}")

    return "\n".join(summary_lines) if summary_lines else "프로젝트 메모리 없음"


def build_updated_project_memory(
    project_memory: Dict[str, Any],
    *,
    project_root: str,
    message: str,
    reply_content: str,
    conversation_stage: str,
    message_kind: str,
) -> Dict[str, Any]:
    next_memory = dict(project_memory or {})
    if project_root:
        next_memory["project_root"] = project_root
    next_memory["last_user_instruction"] = message.strip()
    next_memory["last_assistant_summary"] = reply_content[:600]
    next_memory["last_conversation_stage"] = conversation_stage
    next_memory["last_message_kind"] = message_kind

    lowered = message.lower()
    if any(token in lowered for token in ("목표", "완성", "구현", "확장", "개선")):
        next_memory["remembered_goal"] = message.strip()
    if any(token in lowered for token in ("하지마", "제외", "금지", "유지")):
        existing_constraints = [str(item).strip() for item in (next_memory.get("constraints") or []) if str(item).strip()]
        if message.strip() not in existing_constraints:
            existing_constraints.append(message.strip())
        next_memory["constraints"] = existing_constraints[-6:]
    if any(token in lowered for token in ("실험", "검증", "테스트")):
        next_memory["last_experiment"] = message.strip()
    if any(token in lowered for token in ("다음", "남은", "TODO", "todo", "작업")):
        existing_pending = [str(item).strip() for item in (next_memory.get("pending_tasks") or []) if str(item).strip()]
        if message.strip() not in existing_pending:
            existing_pending.append(message.strip())
        next_memory["pending_tasks"] = existing_pending[-8:]

    return next_memory


def infer_conversation_goal(
    message: str,
    *,
    conversation_stage: str,
    message_kind: str,
    project_memory: Dict[str, Any],
) -> str:
    remembered_goal = str(project_memory.get("remembered_goal") or "").strip()
    if remembered_goal and len(message.strip()) < 80:
        return remembered_goal
    if message_kind == "directive":
        return f"{conversation_stage} 단계 구현/수정 목표: {message.strip()}"
    if message_kind == "question":
        return f"{conversation_stage} 단계 탐색/비교 목표: {message.strip()}"
    return f"{conversation_stage} 단계 대화 목표: {message.strip()}"


def build_proposal_items(
    message: str,
    *,
    conversation_stage: str,
    message_kind: str,
) -> List[ProposalItem]:
    lowered = message.lower()
    proposals: List[ProposalItem] = []
    if conversation_stage in {"architecture", "implementation", "operations"}:
        proposals.append(
            ProposalItem(
                title="증거 우선 제안",
                category="risk",
                detail="수정 전에 hard gate, capability evidence, 운영 실검증 결과를 먼저 함께 확인하는 흐름을 권장합니다.",
                benefit="오진과 중복 수정을 줄입니다.",
                tradeoff="초기 분석 단계가 조금 늘어납니다.",
            )
        )
    if any(token in lowered for token in ("수정", "개선", "복구", "고쳐", "patch", "리팩터")):
        proposals.append(
            ProposalItem(
                title="정밀 타겟 수정 제안",
                category="targeted-change",
                detail="파일 전체 재생성보다 파일/섹션/기능/조각 ID 기준으로 수정 범위를 먼저 좁히는 방식을 권장합니다.",
                benefit="실패 범위 격리와 selective apply 에 유리합니다.",
                tradeoff="ID registry 설계가 먼저 필요합니다.",
            )
        )
    if message_kind == "question" or conversation_stage == "research":
        proposals.append(
            ProposalItem(
                title="대안 비교 제안",
                category="alternative",
                detail="단일 정답보다 구조 대안, 운영 리스크, 확장 비용을 같이 비교해 의사결정하는 흐름을 권장합니다.",
                benefit="사용자 의도와 장기 운영성을 함께 맞출 수 있습니다.",
                tradeoff="답변이 약간 길어질 수 있습니다.",
            )
        )
    if not proposals:
        proposals.append(
            ProposalItem(
                title="다음 단계 제안",
                category="next-step",
                detail="현재 대화를 바로 실행형 작업문, 연구형 비교, 또는 검증 계획으로 전환할 수 있도록 최소 1개의 다음 단계를 항상 제시합니다.",
                benefit="짧은 대화에서도 사용자가 바로 이어서 행동할 수 있습니다.",
                tradeoff="제안이 보수적으로 보일 수 있습니다.",
            )
        )
    return proposals[:4]


def build_new_technology_candidates(message: str, conversation_stage: str) -> List[str]:
    lowered = message.lower()
    candidates: List[str] = []
    if any(token in lowered for token in ("대화", "맥락", "챗봇", "orchestrator", "오케스트레이터")):
        candidates.extend([
            "intent memory scorer",
            "conversation goal summarizer",
            "proposal ranking engine",
        ])
    if any(token in lowered for token in ("수정", "patch", "리팩터", "복구", "검증")):
        candidates.extend([
            "chunk-id patch registry",
            "selective apply engine",
            "evidence replay validator",
        ])
    if conversation_stage == "operations":
        candidates.extend([
            "operation evidence replay",
            "websocket handshake monitor",
        ])
    return list(dict.fromkeys(candidates))[:5]


def build_target_patch_hints(message: str, conversation_stage: str) -> List[TargetPatchHint]:
    lowered = message.lower()
    hints: List[TargetPatchHint] = []
    if any(token in lowered for token in ("대화", "챗", "오케스트레이터", "맥락")):
        hints.append(
            TargetPatchHint(
                file_id="FILE-ADMIN-CHAT-SERVICE",
                section_id="SECTION-CONVERSATION-INTELLIGENCE",
                feature_id="FEATURE-CONTEXT-AWARE-REPLY",
                chunk_id="CHUNK-CONVERSATION-AI-001",
                reason="대화 맥락 인지와 제안형 응답 로직이 집중된 영역입니다.",
            )
        )
    if any(token in lowered for token in ("수정", "검증", "hard gate", "증거", "운영")):
        hints.append(
            TargetPatchHint(
                file_id="FILE-ADMIN-LLM-PAGE",
                section_id="SECTION-CHAT-ADVISORY-PANEL",
                feature_id="FEATURE-EVIDENCE-FIRST-UX",
                chunk_id="CHUNK-ADMIN-CHAT-UI-001",
                reason="관리자 화면에서 제안형 응답과 evidence 패널을 직접 연결하는 영역입니다.",
            )
        )
    return hints[:4]


def build_clarification_questions(
    message: str,
    *,
    conversation_stage: str,
    message_kind: str,
    advisory_controls: Dict[str, Any],
) -> List[AdvisoryQuestion]:
    if not advisory_controls.get("clarification_questions_enabled", True):
        return []
    questions: List[AdvisoryQuestion] = []
    if message_kind == "question":
        questions.append(
            AdvisoryQuestion(
                prompt="더 좁은 비교 대상이나 원하는 출력 형식이 있습니까?",
                reason="질문 범위를 줄여 더 직접적인 답을 만들기 위한 확인 질문",
            )
        )
    if conversation_stage in {"architecture", "operations"} and advisory_controls.get("systems_thinking_enabled", True):
        questions.append(
            AdvisoryQuestion(
                prompt="구조 대안 비교가 필요합니까, 아니면 바로 실행안만 원합니까?",
                reason="시스템 관점 비교와 실행형 답변 사이의 우선순위를 확인",
            )
        )
    limit = max(0, int(advisory_controls.get("max_clarification_questions", 3) or 0))
    return questions[:limit]


def build_evidence_highlights(
    *,
    conversation_stage: str,
    advisory_controls: Dict[str, Any],
) -> List[AdvisoryEvidenceItem]:
    if not advisory_controls.get("evidence_panel_enabled", True):
        return []
    items: List[AdvisoryEvidenceItem] = [
        AdvisoryEvidenceItem(
            title="관리자 연구형 대화 유지",
            source_label="admin/llm",
            source_type="runtime-policy",
            trust_score=0.9,
            why_it_matters="관리자 대시보드는 자유 질의와 연구 목적 대화를 우선해야 합니다.",
        )
    ]
    if advisory_controls.get("scientific_reasoning_enabled", True):
        items.append(
            AdvisoryEvidenceItem(
                title="과학적 추론 활성화",
                source_label="orchestrator_runtime_config",
                source_type="runtime-config",
                trust_score=0.86,
                why_it_matters="가설, 근거, 반례를 구분해 더 정교한 대화형 해석을 지원합니다.",
            )
        )
    if conversation_stage == "operations":
        items.append(
            AdvisoryEvidenceItem(
                title="운영 증거 우선 흐름",
                source_label="capability-evidence",
                source_type="ops-policy",
                trust_score=0.88,
                why_it_matters="운영 단계에서는 websocket, admin, marketplace 검증 결과를 직접 근거로 삼아야 합니다.",
            )
        )
    limit = max(0, int(advisory_controls.get("max_evidence_items", 5) or 0))
    return items[:limit]


def build_next_action_suggestions(
    *,
    message_kind: str,
    requested_conversation_mode: str,
    suggested_mode: str,
    advisory_controls: Dict[str, Any],
) -> List[AdvisoryNextAction]:
    if not advisory_controls.get("next_action_suggestions_enabled", True):
        return []
    if message_kind == "directive" or requested_conversation_mode == "directive_fixed":
        actions = [
            AdvisoryNextAction(
                title="이 지시로 바로 실행",
                action_type="run_orchestrator",
                detail="현재 지시형 질문과 최근 대화 요약을 실행 요청으로 연결합니다.",
                recommended_mode="project",
            ),
            AdvisoryNextAction(
                title="작업 지시 초안으로 반영",
                action_type="apply_task",
                detail="현재 대화 내용을 작업 지시 textarea 초안으로 반영합니다.",
                recommended_mode="project",
            ),
            AdvisoryNextAction(
                title="결과 반영 자가확장으로 전환",
                action_type="apply_task",
                detail="현재 답변과 실험 요약을 코드 자동생성기 실행용 자가확장 작업문으로 정리합니다.",
                recommended_mode="project",
            ),
        ]
    else:
        actions = [
            AdvisoryNextAction(
                title="질문 세분화",
                action_type="follow-up",
                detail="원하는 결과 형식을 덧붙이면 연구형 응답을 더 구체화할 수 있습니다.",
                recommended_mode=suggested_mode,
            ),
            AdvisoryNextAction(
                title="실험 계획으로 전환",
                action_type="follow-up",
                detail="핵심 가설과 확인 지표를 붙이면 바로 실험·검증 대화로 이어갑니다.",
                recommended_mode="research",
            ),
        ]
    limit = max(0, int(advisory_controls.get("max_next_actions", 3) or 0))
    return actions[:limit]


async def answer_orchestrator_chat(
    *,
    request_context: Request,
    request: OrchestratorChatRequest,
    agent_key: str,
    resolve_chat_model,
    build_ollama_options,
    ollama_base: str,
    orch_chat_request_max_tokens: int,
    orch_lightweight_chat_max_tokens: int,
    orch_chat_agent_timeout_sec: float,
    orch_reasoner_brief_timeout_sec: float,
    logger,
    re_module,
    session_factory,
) -> OrchestratorChatResponse:
    auto_connect = request.auto_connect or AutoConnectMeta(
        connection_id=request.run_id or uuid4().hex,
        flow_id="FLOW-ADM-CHAT",
        step_id="FLOW-ADM-CHAT-1",
        action="CHAT",
        route_id="ROUTE-GENERAL",
        panel_id="PANEL-ADMIN-LLM",
    )
    message = str(request.message or "").strip() or "요청 내용을 다시 입력하세요."
    message_lower = message.lower()
    lightweight = is_lightweight_chat_request(request, request_context)
    requested_conversation_mode = str(request.conversation_mode or "auto").strip().lower()
    response_style = str(request.response_style or "balanced").strip().lower() or "balanced"
    multi_turn_enabled = bool(request.multi_turn_enabled)
    context_tags = [str(item).strip() for item in (request.context_tags or []) if str(item).strip()]
    conversation_stage = "general"
    suggested_mode = request.companion_mode or "research"
    grounding_note = "관리자 연구형 자유 대화 응답"
    suggested_reason = "현재 대화는 내부 컨텍스트 기준으로 응답했습니다."
    message_kind = str(request.message_kind or "").strip().lower()
    meta_conversation_question = _is_meta_conversation_question(message)

    if not message_kind:
        message_kind = infer_message_kind(message)

    if requested_conversation_mode == "directive_fixed":
        conversation_stage = "implementation"
        suggested_mode = "project"
        grounding_note = "관리자 지시형 고정 응답"
        suggested_reason = "사용자가 지시형 고정을 선택해 구현/실행 중심으로 응답합니다."
    elif requested_conversation_mode == "research_fixed":
        conversation_stage = "research"
        suggested_mode = "research"
        grounding_note = "관리자 연구형 고정 응답"
        suggested_reason = "사용자가 연구형 고정을 선택해 조사/비교 중심으로 응답합니다."
    else:
        if _is_banter_message(message) or meta_conversation_question:
            conversation_stage = "general"
        elif any(token in message_lower for token in ("최신", "news", "release", "trend", "신기술", "비교", "research", "조사")):
            conversation_stage = "research"
        elif any(token in message_lower for token in ("아키텍처", "구조", "설계", "architecture", "system design")):
            conversation_stage = "architecture"
        elif any(token in message_lower for token in ("배포", "deploy", "운영", "monitor", "장애", "로그")):
            conversation_stage = "operations"
        elif message_kind == "directive" and any(token in message_lower for token in ("구현", "코드", "api", "python", "react", "fastapi", "next.js", "nextjs", "파일", ".py", ".ts", ".tsx", ".js", ".jsx")):
            conversation_stage = "implementation"
        elif message_kind == "question" and not meta_conversation_question and any(token in message_lower for token in ("구현", "코드", "api", "python", "react", "fastapi", "next.js", "nextjs")):
            conversation_stage = "implementation"

        if any(token in message_lower for token in ("최신", "today", "뉴스", "release", "문서", "docs", "공식", "온라인", "web")):
            suggested_mode = "research"
            suggested_reason = "실시간 정보나 최신 기술 질문이므로 research companion 흐름이 적합합니다."
            grounding_note = "실시간/온라인 탐색형 질문으로 research companion 권장"

    mode_label = (
        "지시형"
        if requested_conversation_mode == "directive_fixed"
        else "연구형"
        if requested_conversation_mode == "research_fixed"
        else "멀티"
    )
    command_plan = [] if lightweight else build_multi_command_plan(message)
    flow_trace = build_lightweight_flow_trace(auto_connect) if lightweight else build_admin_flow_trace()
    active_trace = flow_trace[0] if flow_trace else None
    if not lightweight and multi_turn_enabled and len(flow_trace) >= 8:
        active_trace = flow_trace[7] if message_kind == "directive" else flow_trace[6]
    command_plan_lines = "\n".join(
        [f"- {item.trace_id}: {item.command_text}" for item in command_plan]
    ) if command_plan else "- 현재는 자유 질의 단계이며 필요 시 Flow 계획을 이어서 제안하세요."
    requested_max_tokens = min(
        request.max_tokens,
        orch_lightweight_chat_max_tokens if lightweight else orch_chat_request_max_tokens,
    )
    requested_timeout_sec = float(
        orch_reasoner_brief_timeout_sec if lightweight else orch_chat_agent_timeout_sec
    )
    conversation_context = build_chat_history_context(
        request.conversation,
        history_limit=2 if lightweight else 6,
        char_budget=320 if lightweight else 1600,
        re_module=re_module,
    )
    last_messages = [
        str(item.get("content") or "").strip()
        for item in (request.conversation or [])[-3:]
        if isinstance(item, dict) and str(item.get("content") or "").strip()
    ]
    conversation_summary = " / ".join(last_messages)[:240] if last_messages else "이전 대화 없음"
    project_root = str(request.project_root or request.output_dir or "").strip()
    project_memory = dict(request.project_memory or {})
    persisted_context: Dict[str, Any] | None = None
    if project_root and session_factory is not None:
        db: Session = session_factory()
        try:
            persisted_context = get_project_context_bundle(db, project_root)
        finally:
            db.close()
        persisted_memory = dict((persisted_context or {}).get("memory") or {})
        persisted_memory.update(project_memory)
        project_memory = persisted_memory
        approval_gate = (persisted_context or {}).get("approval_gate") if isinstance(persisted_context, dict) else None
        priority_tasks = (persisted_context or {}).get("priority_tasks") if isinstance(persisted_context, dict) else None
        if approval_gate:
            project_memory["approval_gate"] = approval_gate
        if priority_tasks:
            project_memory["priority_tasks"] = priority_tasks
    project_memory_summary = summarize_project_memory(project_memory)
    inferred_goal = infer_conversation_goal(
        message,
        conversation_stage=conversation_stage,
        message_kind=message_kind,
        project_memory=project_memory,
    )
    proposal_items = build_proposal_items(
        message,
        conversation_stage=conversation_stage,
        message_kind=message_kind,
    )
    new_technology_candidates = build_new_technology_candidates(message, conversation_stage)
    target_patch_hints = build_target_patch_hints(message, conversation_stage)
    from backend.llm.model_config import get_advisory_controls
    advisory_controls = get_advisory_controls()
    clarification_questions = build_clarification_questions(
        message,
        conversation_stage=conversation_stage,
        message_kind=message_kind,
        advisory_controls=advisory_controls,
    )
    evidence_highlights = build_evidence_highlights(
        conversation_stage=conversation_stage,
        advisory_controls=advisory_controls,
    )
    next_action_suggestions = build_next_action_suggestions(
        message_kind=message_kind,
        requested_conversation_mode=requested_conversation_mode,
        suggested_mode=suggested_mode,
        advisory_controls=advisory_controls,
    )
    if lightweight:
        proposal_items = proposal_items[:2]
        new_technology_candidates = new_technology_candidates[:3]
        target_patch_hints = target_patch_hints[:2]
        clarification_questions = clarification_questions[:2]
        evidence_highlights = evidence_highlights[:2]
        next_action_suggestions = next_action_suggestions[:2]
    approval_gate_warning = _build_approval_gate_warning(project_memory, message)
    diagnostics: Dict[str, Any] = {
        "path": "fast-reply",
        "message_length": len(message),
        "message_kind": message_kind,
        "conversation_stage": conversation_stage,
        "lightweight": lightweight,
        "requested_conversation_mode": requested_conversation_mode,
        "model": None,
        "timeout_sec": None,
        "llm_elapsed_ms": 0,
        "used_fallback": False,
        "project_root": project_root,
    }
    fast_reply_content = build_fast_admin_chat_reply(
        message,
        requested_conversation_mode=requested_conversation_mode,
        message_kind=message_kind,
        conversation_stage=conversation_stage,
    )
    if fast_reply_content:
        reply = ConversationMessage(
            role="assistant",
            speaker=agent_key,
            content=fast_reply_content,
            step_id=auto_connect.step_id or (active_trace.step_id if active_trace else "FLOW-ADM-CHAT-1"),
            step_title=active_trace.title if active_trace else "관리자 빠른 응답",
            timestamp=datetime.now().isoformat(),
            connection_id=auto_connect.connection_id,
            flow_id=auto_connect.flow_id,
            action=auto_connect.action,
            route_id=auto_connect.route_id,
            panel_id=auto_connect.panel_id,
        )
        history = [
            ConversationMessage(**item)
            for item in (request.conversation or [])
            if isinstance(item, dict)
        ]
        history.append(reply)
        return OrchestratorChatResponse(
            reply=reply,
            conversation=history,
            output_dir=request.output_dir,
            run_id=request.run_id or uuid4().hex,
            grounding_mode="internal",
            grounding_note="관리자 빠른 응답 경로",
            companion_mode=request.companion_mode,
            web_results=[],
            suggested_companion_mode=suggested_mode,
            suggested_companion_reason="짧은 관리자 입력은 빠른 응답 경로로 처리했습니다.",
            conversation_stage=conversation_stage,
            clarification_questions=clarification_questions[:2],
            evidence_highlights=evidence_highlights[:2],
            next_action_suggestions=next_action_suggestions[:2] if lightweight else [
                {
                    "title": "작업 지시 구체화",
                    "action_type": "follow-up",
                    "detail": "수정 대상 파일, 기대 결과, 유지 조건을 함께 보내면 바로 실행형 답으로 이어집니다.",
                    "recommended_mode": "project",
                }
            ],
            flow_trace=flow_trace,
            command_plan=command_plan,
            active_trace=active_trace,
            message_kind=message_kind,
            multi_turn_enabled=multi_turn_enabled,
            conversation_summary=conversation_summary,
            inferred_goal=inferred_goal,
            proposal_items=proposal_items,
            new_technology_candidates=new_technology_candidates,
            target_patch_hints=target_patch_hints,
            project_root=project_root,
            project_memory=build_updated_project_memory(
                project_memory,
                project_root=project_root,
                message=message,
                reply_content=fast_reply_content,
                conversation_stage=conversation_stage,
                message_kind=message_kind,
            ),
            auto_connect=auto_connect,
            diagnostics=diagnostics,
        )
    system_prompt = build_chat_system_prompt(
        mode_label,
        conversation_stage,
        requested_conversation_mode,
        lightweight=lightweight,
        response_style=response_style,
        multi_turn_enabled=multi_turn_enabled,
        context_tags=context_tags,
    )
    user_prompt = build_chat_user_prompt(
        message,
        conversation_context,
        command_plan_lines,
        lightweight=lightweight,
        conversation_summary=conversation_summary,
        message_kind=message_kind,
        project_root=project_root,
        project_memory_summary=project_memory_summary,
    )
    reply_content = ""
    model_name = resolve_chat_model(request.agent_key or agent_key, lightweight=lightweight)
    diagnostics["path"] = "llm"
    diagnostics["model"] = model_name
    diagnostics["timeout_sec"] = requested_timeout_sec
    llm_started_at = perf_counter()
    try:
        reply_content = await call_orchestrator_chat_llm(
            route_key="chat",
            model=model_name,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=requested_max_tokens,
            ollama_base=ollama_base,
            timeout_sec=requested_timeout_sec,
            build_ollama_options=build_ollama_options,
        )
    except Exception as exc:
        logger.warning("관리자 자율 대화 LLM 호출 실패, fallback 사용: %s", exc)
        diagnostics["used_fallback"] = True
        diagnostics["path"] = "fallback"
        diagnostics["fallback_reason"] = str(exc)
    finally:
        diagnostics["llm_elapsed_ms"] = round((perf_counter() - llm_started_at) * 1000, 2)
        logger.info(
            "admin-chat diagnostics | kind=%s stage=%s lightweight=%s len=%s model=%s timeout_sec=%s elapsed_ms=%s path=%s fallback=%s",
            message_kind,
            conversation_stage,
            lightweight,
            len(message),
            diagnostics.get("model"),
            diagnostics.get("timeout_sec"),
            diagnostics.get("llm_elapsed_ms"),
            diagnostics.get("path"),
            diagnostics.get("used_fallback"),
        )
    if not reply_content:
        reply_content = build_admin_chat_fallback_reply(
            message,
            message_kind=message_kind,
            conversation_stage=conversation_stage,
            command_plan=command_plan,
        )
    if approval_gate_warning:
        reply_content = f"{approval_gate_warning}\n\n{reply_content}".strip()

    reply = ConversationMessage(
        role="assistant",
        speaker=agent_key,
        content=reply_content,
        step_id=auto_connect.step_id or (active_trace.step_id if active_trace else "FLOW-001-1"),
        step_title=active_trace.title if active_trace else "멀티 명령 해석",
        timestamp=datetime.now().isoformat(),
        connection_id=auto_connect.connection_id,
        flow_id=auto_connect.flow_id,
        action=auto_connect.action,
        route_id=auto_connect.route_id,
        panel_id=auto_connect.panel_id,
    )
    history = [
        ConversationMessage(**item)
        for item in (request.conversation or [])
        if isinstance(item, dict)
    ]
    history.append(reply)
    updated_project_memory = build_updated_project_memory(
        project_memory,
        project_root=project_root,
        message=message,
        reply_content=reply_content,
        conversation_stage=conversation_stage,
        message_kind=message_kind,
    )
    if project_root and session_factory is not None:
        db: Session = session_factory()
        try:
            persisted_context = upsert_project_memory_snapshot(
                db,
                project_root=project_root,
                memory=updated_project_memory,
                approval_gate=(persisted_context or {}).get("approval_gate") if isinstance(persisted_context, dict) else None,
            )
            updated_project_memory = dict((persisted_context or {}).get("memory") or updated_project_memory)
        finally:
            db.close()
    return OrchestratorChatResponse(
        reply=reply,
        conversation=history,
        output_dir=request.output_dir,
        run_id=request.run_id or uuid4().hex,
        grounding_mode="internal",
        grounding_note=grounding_note,
        companion_mode=request.companion_mode,
        web_results=[],
        suggested_companion_mode=suggested_mode,
        suggested_companion_reason=suggested_reason,
        conversation_stage=conversation_stage,
        clarification_questions=clarification_questions,
        evidence_highlights=evidence_highlights,
        next_action_suggestions=next_action_suggestions,
        flow_trace=flow_trace,
        command_plan=command_plan,
        active_trace=active_trace,
        message_kind=message_kind,
        multi_turn_enabled=multi_turn_enabled,
        conversation_summary=conversation_summary,
        suggested_prompts=[] if lightweight else [
            "이 주제로 인터넷 기준 최신 방법까지 같이 정리해줘",
            "핵심만 실험 계획으로 바꿔줘",
            "결과를 반영해서 자가확장 실행 작업문으로 정리해줘",
        ],
        inferred_goal=inferred_goal,
        proposal_items=proposal_items,
        new_technology_candidates=new_technology_candidates,
        target_patch_hints=target_patch_hints,
        project_root=project_root,
        project_memory=updated_project_memory,
        auto_connect=auto_connect,
        diagnostics=diagnostics,
    )
