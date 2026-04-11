import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth import get_current_user, is_weak_secret_key
from backend.admin.orchestrator.path_utils import (
    admin_runtime_root as resolve_admin_runtime_root,
    resolve_shared_admin_runtime_root as resolve_admin_shared_runtime_root,
)
from backend.llm.model_config import (
    RUNTIME_CONFIG_PATH,
    get_available_ollama_models,
    get_configured_execution_controls,
    get_configured_model_routes,
    get_gpu_runtime_info,
    get_recommended_runtime_profiles,
)
from backend.llm.python_security_policy import scan_python_security_policy
from backend.models import User


router = APIRouter(prefix="/api/admin/orchestrator", tags=["admin-orchestrator"])

REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_SELF_RUN_FIXTURE_ROOT = (
    REPO_ROOT / "uploads" / "projects" / "_tmp_self_workflow_validation"
)
LEGACY_SELF_RUN_EXPERIMENT_ROOT = (
    REPO_ROOT / "uploads" / "projects" / "admin_self_experiments"
)
ORCH_MIN_FILES = max(1, int(os.getenv("ORCH_MIN_FILES", "27")))
ORCH_MIN_DIRS = max(0, int(os.getenv("ORCH_MIN_DIRS", "2")))
ORCH_TINY_SOURCE_BYTES = 1024
SOURCE_FILE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs"}
WORKSPACE_ENTRYPOINTS = [
    ("백엔드 진입점", Path("backend/main.py")),
    ("관리자 대시보드", Path("frontend/frontend/app/admin/page.tsx")),
    ("관리자 오케스트레이터", Path("frontend/frontend/app/admin/llm/page.tsx")),
    ("오케스트레이터 런타임 설정", Path("knowledge/orchestrator_runtime_config.json")),
    ("도커 오케스트레이션", Path("docker-compose.yml")),
    ("운영 규칙", Path("AGENTS.md")),
]
SCAN_EXCLUDED_DIRS = {
    ".git",
    ".next",
    ".delivery-venv",
    ".venv",
    ".zip-venv",
    "__pycache__",
    "node_modules",
    "archive",
    "uploads",
    "models",
}
OPERATIONAL_EVIDENCE_TARGETS = [
    {
        "id": "websocket",
        "target": "/api/llm/ws",
        "protocol": "websocket",
        "verification_method": "websocket-handshake-and-ping-pong",
        "note": "실도메인 websocket handshake 및 ping/pong 검증 결과를 연결합니다.",
        "warning_threshold_ms": 150.0,
    },
    {
        "id": "admin",
        "target": "/admin/llm",
        "protocol": "https",
        "verification_method": "http-response-and-page-render",
        "note": "관리자 오케스트레이터 운영 경로 실검증 결과를 연결합니다.",
        "warning_threshold_ms": 150.0,
    },
    {
        "id": "marketplace",
        "target": "/marketplace/orchestrator",
        "protocol": "https",
        "verification_method": "http-response-and-page-render",
        "note": "마켓플레이스 운영 경로 실검증 결과를 연결합니다.",
        "warning_threshold_ms": 200.0,
    },
    {
        "id": "system_settings",
        "target": "/api/admin/system-settings",
        "protocol": "https",
        "verification_method": "http-response-json",
        "note": "관리자 시스템 설정 운영 API 실검증 결과를 연결합니다.",
        "warning_threshold_ms": 120.0,
    },
    {
        "id": "workspace_self_run_record",
        "target": "/api/admin/workspace-self-run-record?latest=true",
        "protocol": "https",
        "verification_method": "http-response-latest-record",
        "note": "최신 workspace self-run record 운영 API 실검증 결과를 연결합니다.",
        "warning_threshold_ms": 120.0,
    },
]
CAPABILITY_GROUP_LABELS = {
    "diagnosis-control": "진단 통솔",
    "improvement-control": "개선 통솔",
    "expansion-control": "확장 통솔",
}
CAPABILITY_ORDER = [
    "project-scanner",
    "dependency-graph",
    "security-guard",
    "self-healing-engine",
    "code-generator",
    "admin-command-interface",
    "ollama-model-controller",
]


def require_admin(current_user: User = Depends(get_current_user)):
    is_admin = bool(getattr(current_user, "is_admin", False))
    is_superuser = bool(getattr(current_user, "is_superuser", False))
    if not (is_admin or is_superuser):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다")
    return current_user


class CapabilitySectionItem(BaseModel):
    label: str
    value: Any
    note: str | None = None


class CapabilitySection(BaseModel):
    id: str
    title: str
    items: List[CapabilitySectionItem]


class CapabilityCard(BaseModel):
    id: str
    title: str
    group_id: str
    state: str
    state_label: str | None = None
    state_reason: str | None = None
    summary: str
    metric: str
    detail: str | None = None
    attention_required: bool = False
    staleness_label: str | None = None
    last_run_started_at: str | None = None
    last_run_finished_at: str | None = None
    last_run_age_hours: float | None = None
    evidence_digest: Dict[str, Any] = {}


class CapabilityGroupSummary(BaseModel):
    id: str
    title: str
    state: str
    summary: str
    active_count: int
    standby_count: int
    warning_count: int
    error_count: int


class CapabilitySummaryResponse(BaseModel):
    generated_at: str
    evidence_snapshot_version: str = "v1"
    groups: List[CapabilityGroupSummary]
    capabilities: List[CapabilityCard]


class CapabilityDetailResponse(BaseModel):
    generated_at: str
    debug_signature: str | None = None
    sections_count: int | None = None
    capability: CapabilityCard
    highlights: List[str]
    suggested_actions: List[str]
    sections: List[CapabilitySection]
    evidence_bundle: Dict[str, Any] = {}
    target_file_ids: List[str] = []
    target_section_ids: List[str] = []
    target_feature_ids: List[str] = []
    target_chunk_ids: List[str] = []
    failure_tags: List[str] = []
    repair_tags: List[str] = []
    target_patch_entries: List[Dict[str, Any]] = []
    validation_findings: List[Dict[str, Any]]
    improvement_code_examples: List[Dict[str, Any]]


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except Exception:
        return path.as_posix()


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


def _tail_text(value: str, max_chars: int = 1600) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text[-max_chars:]


def _code_example(
    example_id: str,
    title: str,
    language: str,
    path: str,
    summary: str,
    code: str,
) -> Dict[str, Any]:
    return {
        "id": example_id,
        "title": title,
        "language": language,
        "path": path,
        "summary": summary,
        "code": code,
    }


def _build_file_evidence_snippets(
    rel_path: str,
    anchors: List[str],
    summary: str,
    radius: int = 2,
) -> List[Dict[str, Any]]:
    file_path = (REPO_ROOT / rel_path).resolve()
    if not file_path.exists() or not file_path.is_file():
        return []
    lines = _safe_read_text(file_path).splitlines()
    lowered_anchors = [anchor.lower() for anchor in anchors if anchor]
    matched_ranges: List[tuple[int, int]] = []
    for index, line in enumerate(lines):
        line_lower = line.lower()
        if not any(anchor in line_lower for anchor in lowered_anchors):
            continue
        start = max(0, index - radius)
        end = min(len(lines), index + radius + 1)
        if matched_ranges and start <= matched_ranges[-1][1]:
            previous_start, previous_end = matched_ranges[-1]
            matched_ranges[-1] = (previous_start, max(previous_end, end))
            continue
        matched_ranges.append((start, end))

    snippets: List[Dict[str, Any]] = []
    for start, end in matched_ranges:
        snippet = "\n".join(
            f"{line_number + 1}: {lines[line_number]}"
            for line_number in range(start, end)
        )
        snippets.append(
            {
                "path": rel_path,
                "line_start": start + 1,
                "line_end": end,
                "summary": summary,
                "snippet": snippet,
            }
        )
    return snippets


def _build_finding_file_evidence(
    capability_id: str,
    category: str,
) -> List[Dict[str, Any]]:
    evidence_map: Dict[str, List[tuple[str, List[str], str]]] = {
        "runtime-evidence": [
            (
                "backend/llm/admin_capabilities.py",
                ["latest_record.get(\"available\")", "expected_root"],
                "실행 근거 부재를 경고로 올리는 관리자 진단 코드입니다.",
            ),
        ],
        "runtime-scope": [
            (
                "backend/llm/admin_capabilities.py",
                ["scope_info[\"scope\"] != \"whole-project\"", "scope_info"],
                "전체 프로젝트 범위가 아닐 때 보조 진단으로 강등하는 조건입니다.",
            ),
        ],
        "self-run-status": [
            (
                "backend/llm/admin_capabilities.py",
                [
                    "latest_status == \"running\"",
                    "latest_status == \"failed\"",
                    "latest_status == \"no_changes\"",
                ],
                "최신 self-run 상태를 실패 또는 무변경으로 판정하는 코드입니다.",
            ),
        ],
        "runtime-exception": [
            (
                "backend/llm/admin_capabilities.py",
                ["orchestration_error", "runtime-exception"],
                "자가 실행 중 예외를 런타임 실패로 승격하는 코드입니다.",
            ),
            (
                "backend/admin_router.py",
                ["worker_process_exited", "runtime_diagnostic"],
                "worker 종료 후 stale running 을 실패로 전환하는 관리자 라우터 코드입니다.",
            ),
        ],
        "worker-process-exited": [
            (
                "backend/admin_router.py",
                ["worker_process_exited", "worker_alive"],
                "종료된 worker 를 running 으로 방치하지 않도록 실패 전환하는 코드입니다.",
            ),
            (
                "backend/admin_self_run_worker.py",
                ["worker_finished_at", "runtime_diagnostic"],
                "background worker 시작/종료를 approval record 에 기록하는 코드입니다.",
            ),
        ],
        "worker-timeout": [
            (
                "backend/admin_router.py",
                ["worker_timeout", "running_seconds"],
                "worker 제한시간 초과를 실패 상태로 바꾸는 코드입니다.",
            ),
        ],
        "worker-log": [
            (
                "backend/admin_router.py",
                ["worker_log_path", "worker.log"],
                "worker stdout/stderr 를 파일로 남겨 진단 근거를 보존하는 코드입니다.",
            ),
        ],
        "vector-db-connect": [
            (
                "backend/llm/project_indexer.py",
                ["QDRANT_HTTP_TIMEOUT_SEC", "QdrantClient", "timeout="],
                "Qdrant 연결 지연 시 빠르게 in-memory fallback 으로 전환하는 인덱서 코드입니다.",
            ),
        ],
        "planner-fallback": [
            (
                "backend/llm/admin_capabilities.py",
                ["fallback_reason", "planner-fallback"],
                "planner fallback 사유를 직접 실패 근거로 연결하는 코드입니다.",
            ),
            (
                "backend/llm/orchestrator.py",
                ["fallback_reason", "planner"],
                "오케스트레이터 본체에서 fallback 사유를 다루는 위치입니다.",
            ),
        ],
        "completion-gate": [
            (
                "backend/llm/admin_capabilities.py",
                ["completion_gate_ok is False", "completion_gate_error"],
                "완료 게이트 실패를 self-healing 경고로 승격하는 코드입니다.",
            ),
            (
                "backend/llm/orchestrator.py",
                ["completion_gate_ok", "completion_gate_error"],
                "오케스트레이터가 완료 게이트 결과를 기록하는 위치입니다.",
            ),
        ],
        "approval-gate": [
            (
                "backend/llm/admin_capabilities.py",
                ["approval_gate_ok is False", "approval_failed_fields"],
                "승인 게이트 실패를 self-healing 문제로 올리는 코드입니다.",
            ),
            (
                "backend/llm/orchestrator.py",
                ["approval_gate_ok", "approval_gate_failed_fields"],
                "원본 반영 가능 여부를 판정하는 오케스트레이터 코드입니다.",
            ),
        ],
        "postcheck": [
            (
                "backend/llm/admin_capabilities.py",
                ["postcheck_ok", "postcheck_error"],
                "postcheck 실패를 self-healing 원인으로 등록하는 코드입니다.",
            ),
        ],
        "secondary-validation": [
            (
                "backend/llm/admin_capabilities.py",
                ["secondary_validation_ok", "secondary_validation_error"],
                "2차 검증 실패를 self-healing 원인으로 등록하는 코드입니다.",
            ),
        ],
        "definition-of-done": [
            (
                "backend/llm/admin_capabilities.py",
                ["dod_ok", "dod_error"],
                "Definition of Done 실패를 경고로 유지하는 코드입니다.",
            ),
        ],
        "output-files": [
            (
                "backend/llm/admin_capabilities.py",
                ["len(written_files) < ORCH_MIN_FILES", "output-files"],
                "생성 파일 수 최소 기준 미달을 감지하는 코드입니다.",
            ),
        ],
        "output-directories": [
            (
                "backend/llm/admin_capabilities.py",
                ["len(changed_dirs) < ORCH_MIN_DIRS", "output-directories"],
                "변경 디렉터리 수 최소 기준 미달을 감지하는 코드입니다.",
            ),
        ],
        "source-output": [
            (
                "backend/llm/admin_capabilities.py",
                ["source_file_count", "source-output"],
                "변경 기록은 있으나 실제 소스 파일이 없는 상태를 경고하는 코드입니다.",
            ),
        ],
        "code-volume": [
            (
                "backend/llm/admin_capabilities.py",
                ["tiny_source_ratio_exceeded", "code-volume"],
                "축약된 코드 산출물이 많은지 검사하는 코드입니다.",
            ),
        ],
        "semantic-audit": [
            (
                "backend/llm/admin_capabilities.py",
                ["semantic_audit_ok is False", "semantic_audit_error"],
                "semantic audit 실패를 직접 경고로 올리는 코드입니다.",
            ),
        ],
        "semantic-score": [
            (
                "backend/llm/admin_capabilities.py",
                ["semantic_audit_score < semantic_audit_threshold", "semantic-score"],
                "semantic score 임계치 미달을 감지하는 코드입니다.",
            ),
        ],
    }
    if capability_id == "self-healing-engine":
        evidence_map.setdefault(
            "approval-gate",
            [],
        ).append(
            (
                "frontend/frontend/app/admin/llm/page.tsx",
                ["applyCapabilityAction(detailCapabilityAction, 'run')", "즉시 개선 실행"],
                "관리자 상세 패널에서 self-healing 즉시 실행을 다시 호출하는 UI 연결입니다.",
            )
        )
    evidence_items = evidence_map.get(category, [])
    resolved_items: List[Dict[str, Any]] = []
    for rel_path, anchors, summary in evidence_items:
        resolved_items.extend(
            _build_file_evidence_snippets(rel_path, anchors, summary)
        )
    return resolved_items


def _build_validation_findings(
    capability_id: str,
    capability: Dict[str, Any],
) -> List[Dict[str, Any]]:
    payload = capability["payload"]
    runtime_diagnostics = payload.get("runtime_diagnostics") or {}
    python_policy = payload.get("python_policy") or {}
    documentation_sync = payload.get("documentation_sync") or {}
    findings = runtime_diagnostics.get("findings") if isinstance(runtime_diagnostics, dict) else []
    if not isinstance(findings, list):
        findings = []

    validation_findings: List[Dict[str, Any]] = []
    runtime_source = str(
        runtime_diagnostics.get("record_path")
        or runtime_diagnostics.get("source_path")
        or "runtime-evidence"
    )
    runtime_actions = runtime_diagnostics.get("actions") if isinstance(runtime_diagnostics, dict) else []
    next_fix = runtime_actions[0] if isinstance(runtime_actions, list) and runtime_actions else "현재 진단 항목에 맞는 개선 실행을 다시 수행하세요."

    for index, finding in enumerate(findings[:6]):
        category = str(finding.get("category") or f"validation-{index + 1}")
        title = str(finding.get("message") or "검증 경고")
        problem = str(finding.get("details") or "상세 근거 없음")
        improvement = next_fix
        wrong_expression = (
            f"현재 실행 근거가 {category} 기준을 충족하지 못해 개선 항목이 실패 상태로 남아 있습니다."
        )
        if category == "self-run-status" and "아직 실행 중" in title:
            wrong_expression = (
                "현재 실행이 진행 중인데도 완료 후 기준으로 오판되면 개선 항목이 실패처럼 표시될 수 있습니다."
            )
            improvement = "동일 approval 기록이 완료될 때까지 대기한 뒤 같은 실행 근거로 다시 판정하세요."
        validation_findings.append(
            {
                "id": f"{capability_id}-{category}",
                "severity": str(finding.get("severity") or "warning"),
                "title": title,
                "problem": problem,
                "wrong_expression": wrong_expression,
                "improvement": improvement,
                "source_path": runtime_source,
                "file_evidence": _build_finding_file_evidence(capability_id, category),
            }
        )

    python_policy_findings = python_policy.get("findings") if isinstance(python_policy, dict) else []
    if isinstance(python_policy_findings, list) and capability_id in {"security-guard", "code-generator"}:
        for index, finding in enumerate(python_policy_findings[:6]):
            rule_id = str(finding.get("rule_id") or "python-policy")
            evidence = str(finding.get("evidence") or "근거 없음")
            validation_findings.append(
                {
                    "id": f"{capability_id}-python-policy-{index + 1}",
                    "severity": str(finding.get("severity") or "warning"),
                    "title": f"Python 보안 {rule_id}",
                    "problem": str(finding.get("message") or "Python 보안 정책 위반이 감지되었습니다."),
                    "wrong_expression": evidence,
                    "improvement": _python_policy_fix_hint(rule_id),
                    "source_path": str(finding.get("path") or "backend"),
                    "file_evidence": [],
                }
            )

    documentation_matches = documentation_sync.get("stale_matches") if isinstance(documentation_sync, dict) else []
    if isinstance(documentation_matches, list) and capability_id in {"project-scanner", "code-generator"}:
        for index, match in enumerate(documentation_matches[:6]):
            axis_label = str(match.get("axis_label") or match.get("axis") or "documentation-sync")
            validation_findings.append(
                {
                    "id": f"{capability_id}-documentation-sync-{index + 1}",
                    "severity": "warning",
                    "title": f"문서 stale 감지 · {axis_label}",
                    "problem": str(match.get("note") or match.get("match") or "문서 동기화 충돌이 감지되었습니다."),
                    "wrong_expression": str(match.get("match") or "stale-match"),
                    "improvement": "README, 상태 문서, readiness artifact 문구를 최신 실검증 결과와 같은 기준으로 다시 동기화하세요.",
                    "source_path": str(match.get("path") or "documentation_sync"),
                    "file_evidence": [],
                }
            )

    if capability_id == "code-generator" and not validation_findings:
        validation_findings.append(
            {
                "id": "code-generator-baseline",
                "severity": "warning",
                "title": "코드 생성 검증 근거가 비어 있습니다.",
                "problem": "최근 실행 산출물 근거가 없어 생성 품질을 정량 검증하지 못했습니다.",
                "wrong_expression": "파일 수, 폴더 수, 코드량 기준이 수집되지 않은 상태입니다.",
                "improvement": "전체 프로젝트 기준 self-run 을 재실행해 생성 근거를 다시 수집하세요.",
                "source_path": runtime_source,
                "file_evidence": _build_finding_file_evidence(
                    capability_id,
                    "runtime-evidence",
                ),
            }
        )

    return validation_findings


def _python_policy_fix_hint(rule_id: str) -> str:
    hints = {
        "hardcoded_weak_secret": "하드코딩된 비밀값을 제거하고 환경변수 또는 시크릿 저장소 기반으로만 주입하세요.",
        "weak_secret_fallback": "약한 fallback 을 제거하고 값이 없으면 기능을 비활성화하거나 안전한 런타임 생성값으로 대체하세요.",
        "python_syntax": "문법 오류 파일을 정상 Python 모듈로 교정해 검증 체인을 막지 않게 하세요.",
        "blocked_runtime_execution": "eval/exec/os.system 대신 명시적 함수 호출이나 안전한 라이브러리 API 로 교체하세요.",
        "unsafe_deserialization": "pickle 계열 대신 JSON 등 검증 가능한 포맷과 스키마 검증을 사용하세요.",
        "subprocess_shell_true": "shell=True 를 제거하고 인자 배열 기반 subprocess 호출로 바꾸세요.",
        "unsafe_yaml_loader": "yaml.safe_load 또는 SafeLoader 계열만 사용하세요.",
        "weak_hash_algorithm": "보안 의미가 있는 해시는 sha256 이상으로 교체하세요.",
    }
    return hints.get(rule_id, "원인 파일을 우선 수정한 뒤 python_security_validation 을 다시 실행하세요.")


def _build_improvement_code_examples(
    capability_id: str,
    capability: Dict[str, Any],
) -> List[Dict[str, Any]]:
    payload = capability["payload"]
    runtime_diagnostics = payload.get("runtime_diagnostics") or {}
    python_policy = payload.get("python_policy") or {}
    findings = runtime_diagnostics.get("findings") if isinstance(runtime_diagnostics, dict) else []
    if not isinstance(findings, list):
        findings = []
    categories = {
        str(item.get("category"))
        for item in findings
        if isinstance(item, dict) and item.get("category")
    }

    examples: List[Dict[str, Any]] = []

    if capability_id in {"self-healing-engine", "code-generator"}:
        examples.append(
            _code_example(
                "runtime-validation-guard",
                "실행 결과 검증 가드 예시",
                "python",
                "backend/llm/orchestrator.py",
                "산출물 수, 변경 디렉터리 수, 의미 품질 점수를 한 번에 검증하는 가드 예시입니다.",
                "required_files = minimums['files']\nrequired_dirs = minimums['dirs']\nif written_file_count < required_files:\n    raise RuntimeError(f'written_files={written_file_count} / min={required_files}')\nif changed_dir_count < required_dirs:\n    raise RuntimeError(f'changed_dirs={changed_dir_count} / min={required_dirs}')\nif semantic_audit_score < semantic_audit_threshold:\n    raise RuntimeError(\n        f'semantic score {semantic_audit_score} below {semantic_audit_threshold}'\n    )\n",
            )
        )

    if {"output-files", "output-directories", "code-volume"} & categories:
        examples.append(
            _code_example(
                "generator-output-check",
                "산출물 기준 보강 예시",
                "python",
                "backend/llm/admin_capabilities.py",
                "생성 파일 수와 코드량 부족 문제를 개선 항목으로 승격하는 예시입니다.",
                "def validate_generated_outputs(written_files, changed_dirs, tiny_source_count):\n    issues = []\n    if len(written_files) < ORCH_MIN_FILES:\n        issues.append('생성 파일 수 부족')\n    if len(changed_dirs) < ORCH_MIN_DIRS:\n        issues.append('변경 디렉터리 수 부족')\n    if tiny_source_count > 0:\n        issues.append('축약된 코드 산출물 다수 감지')\n    return issues\n",
            )
        )

    if {"completion-gate", "approval-gate", "semantic-audit", "planner-fallback"} & categories:
        examples.append(
            _code_example(
                "self-healing-failfast",
                "실패 원인별 즉시 복구 예시",
                "python",
                "backend/llm/orchestrator.py",
                "게이트 실패와 fallback_reason 을 바로 복구 단계로 전환하는 예시입니다.",
                "if completion_gate_ok is False:\n    recovery_steps.append('completion gate 실패 원인부터 복구')\nif approval_gate_ok is False:\n    recovery_steps.append('approval gate 필수 항목 보강')\nif fallback_reason:\n    recovery_steps.append(f'planner fallback 해소: {fallback_reason}')\nfor step in recovery_steps:\n    logger.warning('[self-healing] %s', step)\n",
            )
        )

    if {"worker-process-exited", "worker-timeout", "worker-log", "vector-db-connect"} & categories:
        examples.append(
            _code_example(
                "worker-runtime-fastfail",
                "worker 런타임/벡터 연결 복구 예시",
                "python",
                "backend/llm/project_indexer.py",
                "Qdrant 연결 지연이 self-run 전체를 멈추지 않도록 빠르게 fallback 하고 worker 로그를 남기는 예시입니다.",
                "QDRANT_TIMEOUT_SEC = max(1, int(os.getenv('QDRANT_HTTP_TIMEOUT_SEC', '3')))\n"
                "try:\n"
                "    client = QdrantClient(url=qdrant_url, timeout=QDRANT_TIMEOUT_SEC)\n"
                "    client.get_collection(CODE_INDEX_COLLECTION)\n"
                "except Exception as exc:\n"
                "    logger.warning('project indexer fallback: %s', exc)\n"
                "    client = None\n"
                "    # 이후 self-run 은 in-memory index 로 계속 진행\n",
            )
        )

    if capability_id == "code-generator":
        examples.append(
            _code_example(
                "frontend-improvement-render",
                "관리자 개선 상세 출력 예시",
                "tsx",
                "frontend/frontend/app/admin/llm/page.tsx",
                "검증 결과와 개선 코드 블록을 관리자 오케스트레이터 상세 패널에 출력하는 예시입니다.",
                "{capabilityDetail.validation_findings.map((finding) => (\n  <div key={finding.id}>\n    <p>{finding.title}</p>\n    <p>{finding.problem}</p>\n    <p>{finding.improvement}</p>\n  </div>\n))}\n<pre>{capabilityDetail.improvement_code_examples[0]?.code}</pre>\n",
            )
        )

    if capability_id in {"security-guard", "code-generator"} and python_policy.get("findings"):
        examples.append(
            _code_example(
                "python-security-secret-guard",
                "Python 보안 fallback 제거 예시",
                "python",
                "backend/auth.py",
                "약한 fallback 대신 환경변수 또는 런타임 생성 비밀값만 허용하는 예시입니다.",
                "def load_secret_from_env(env_name: str) -> str:\n    value = str(os.getenv(env_name) or '').strip()\n    if value:\n        return value\n    return secrets.token_urlsafe(48)\n",
            )
        )

    return examples


def _safe_read_json(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(_safe_read_text(path))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_json_like(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize_json_like(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_json_like(item) for item in value]
    return value


def _resolve_runtime_output_dir(runtime_diagnostics: Dict[str, Any]) -> Path | None:
    output_dir_text = str(
        runtime_diagnostics.get("output_dir")
        or runtime_diagnostics.get("failed_output_dir")
        or runtime_diagnostics.get("experiment_clone_path")
        or ""
    ).strip()
    if not output_dir_text:
        return None
    try:
        return Path(output_dir_text).expanduser().resolve()
    except Exception:
        return None


def _candidate_validation_payload_paths(runtime_diagnostics: Dict[str, Any]) -> List[Path]:
    candidates: List[Path] = []
    output_dir = _resolve_runtime_output_dir(runtime_diagnostics)
    if output_dir is not None:
        candidates.append((output_dir / "docs" / "automatic_validation_result.json").resolve())

    for candidate_text in [
        runtime_diagnostics.get("output_audit_path"),
        runtime_diagnostics.get("validation_result_json_path"),
    ]:
        text = str(candidate_text or "").strip()
        if not text:
            continue
        try:
            path = Path(text).expanduser()
            if not path.is_absolute() and output_dir is not None:
                path = (output_dir / path).resolve()
            candidates.append(path.resolve())
        except Exception:
            continue

    record_path_text = str(runtime_diagnostics.get("record_path") or "").strip()
    if record_path_text:
        try:
            record_dir = Path(record_path_text).expanduser().resolve().parent
            for pattern in ["**/docs/automatic_validation_result.json", "**/automatic_validation_result.json"]:
                for path in sorted(record_dir.glob(pattern), key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True):
                    candidates.append(path.resolve())
        except Exception:
            pass

    project_runs_root = (REPO_ROOT / "uploads" / "projects").resolve()
    if project_runs_root.exists() and project_runs_root.is_dir():
        try:
            for path in sorted(
                project_runs_root.glob("*/docs/automatic_validation_result.json"),
                key=lambda item: item.stat().st_mtime if item.exists() else 0,
                reverse=True,
            ):
                candidates.append(path.resolve())
        except Exception:
            pass

    unique_paths: List[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique_paths.append(path)
    return unique_paths


def _load_latest_validation_payload(runtime_diagnostics: Dict[str, Any]) -> tuple[Dict[str, Any], Path | None]:
    candidates = _candidate_validation_payload_paths(runtime_diagnostics)
    best_payload: Dict[str, Any] = {}
    best_path: Path | None = None
    best_rank = (-1, -1, -1, -1.0)

    def _payload_rank(payload: Dict[str, Any], path: Path) -> tuple[int, int, int, float]:
        evidence_bundle = payload.get("evidence_bundle") if isinstance(payload, dict) else {}
        readiness = evidence_bundle.get("readiness") if isinstance(evidence_bundle, dict) else {}
        readiness_artifacts = payload.get("readiness_artifacts") if isinstance(payload, dict) else {}
        if not isinstance(readiness, dict):
            readiness = {}
        if not isinstance(readiness_artifacts, dict):
            readiness_artifacts = {}

        operational_targets_by_id = readiness.get("operational_targets_by_id") or readiness_artifacts.get("operational_targets_by_id") or {}
        operational_evidence_summary = readiness.get("operational_evidence_summary") or readiness_artifacts.get("operational_evidence_summary") or {}
        operational_latency_summary = readiness.get("operational_latency_summary") or readiness_artifacts.get("operational_latency_summary") or {}
        operational_snapshot = readiness.get("operational_evidence_snapshot") or readiness_artifacts.get("operational_evidence_snapshot") or {}

        has_populated_targets = int(isinstance(operational_targets_by_id, dict) and bool(operational_targets_by_id))
        has_populated_summary = int(isinstance(operational_evidence_summary, dict) and bool(operational_evidence_summary))
        has_populated_latency = int(isinstance(operational_latency_summary, dict) and bool(operational_latency_summary))
        has_snapshot = int(isinstance(operational_snapshot, dict) and bool(operational_snapshot))

        try:
            mtime = path.stat().st_mtime
        except Exception:
            mtime = 0.0
        return (
            has_populated_targets + has_populated_summary + has_populated_latency,
            has_snapshot,
            int(bool(payload.get("completion_gate_ok") is True or ((payload.get("evidence_bundle") or {}).get("execution") or {}).get("completion_gate_ok") is True)),
            mtime,
        )

    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        payload = _safe_read_json(path)
        if not payload:
            continue
        rank = _payload_rank(payload, path)
        if rank >= best_rank:
            best_payload = payload
            best_path = path
            best_rank = rank
    return best_payload, best_path


def _capability_runtime_diagnostics(capability: Dict[str, Any]) -> Dict[str, Any]:
    direct_runtime = capability.get("runtime_diagnostics")
    if isinstance(direct_runtime, dict):
        return direct_runtime
    payload = capability.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("runtime_diagnostics"), dict):
        return payload.get("runtime_diagnostics") or {}
    return {}


def _build_capability_evidence_context(capability: Dict[str, Any]) -> Dict[str, Any]:
    runtime_diagnostics = _capability_runtime_diagnostics(capability)
    validation_payload_candidates = _candidate_validation_payload_paths(runtime_diagnostics)
    validation_payload, validation_payload_path = _load_latest_validation_payload(runtime_diagnostics)
    output_dir = validation_payload_path.parent.parent.resolve() if validation_payload_path is not None else _resolve_runtime_output_dir(runtime_diagnostics)

    evidence_bundle = validation_payload.get("evidence_bundle")
    if not isinstance(evidence_bundle, dict):
        evidence_bundle = {}
    contract = dict(evidence_bundle.get("contract") or {})
    execution = dict(evidence_bundle.get("execution") or {})
    readiness = dict(evidence_bundle.get("readiness") or {})
    selective_apply = dict(evidence_bundle.get("selective_apply") or {})
    readiness_artifacts = validation_payload.get("readiness_artifacts") if isinstance(validation_payload, dict) else {}
    documentation_sync = dict(validation_payload.get("documentation_sync") or {})
    try:
        from backend.llm.orchestrator import _build_document_stale_scan

        live_documentation_scan = _build_document_stale_scan(REPO_ROOT)
        live_documentation_sync = dict((live_documentation_scan or {}).get("documentation_sync") or {})
        if live_documentation_sync:
            documentation_sync = live_documentation_sync
    except Exception:
        live_documentation_scan = {}

    if not contract.get("evidence_schema_version"):
        contract["evidence_schema_version"] = str((readiness_artifacts or {}).get("evidence_schema_version") or "v1")
    if not contract.get("profile_id"):
        contract["profile_id"] = (
            validation_payload.get("validation_profile")
            or runtime_diagnostics.get("validation_profile")
            or ""
        )

    fallback_run_id = str(
        runtime_diagnostics.get("approval_id")
        or Path(str(runtime_diagnostics.get("record_path") or "")).parent.name
        or ""
    ).strip()
    if not execution.get("evidence_run_id"):
        execution["evidence_run_id"] = fallback_run_id

    target_patch_entries = [
        item for item in (selective_apply.get("target_patch_entries") or [])
        if isinstance(item, dict)
    ]

    target_file_ids = list(selective_apply.get("target_file_ids") or [])
    target_section_ids = list(selective_apply.get("target_section_ids") or [])
    target_feature_ids = list(selective_apply.get("target_feature_ids") or [])
    target_chunk_ids = list(selective_apply.get("target_chunk_ids") or [])
    failure_tags = list(selective_apply.get("failure_tags") or [])
    repair_tags = list(selective_apply.get("repair_tags") or [])

    selective_apply["target_file_ids"] = target_file_ids
    selective_apply["target_section_ids"] = target_section_ids
    selective_apply["target_feature_ids"] = target_feature_ids
    selective_apply["target_chunk_ids"] = target_chunk_ids
    selective_apply["failure_tags"] = failure_tags
    selective_apply["repair_tags"] = repair_tags
    selective_apply["target_patch_entries"] = target_patch_entries

    if not execution.get("self_run_status"):
        execution["self_run_status"] = runtime_diagnostics.get("latest_status")
    if execution.get("completion_gate_ok") is None:
        execution["completion_gate_ok"] = runtime_diagnostics.get("completion_gate_ok")
    if execution.get("semantic_audit_ok") is None:
        execution["semantic_audit_ok"] = runtime_diagnostics.get("semantic_audit_ok")
    if execution.get("evidence_generated_at") is None:
        execution["evidence_generated_at"] = fallback_run_id and runtime_diagnostics.get("verified_at")

    if isinstance(readiness_artifacts, dict) and not readiness.get("final_readiness_checklist_path"):
        readiness["final_readiness_checklist_path"] = readiness_artifacts.get("final_readiness_checklist_path")
    if isinstance(readiness_artifacts, dict) and not readiness.get("automatic_validation_result_path"):
        readiness["automatic_validation_result_path"] = readiness_artifacts.get("validation_result_json_path")
    if not readiness.get("output_audit_path"):
        readiness["output_audit_path"] = readiness_artifacts.get("output_audit_path") if isinstance(readiness_artifacts, dict) else ""
    if isinstance(readiness_artifacts, dict) and not isinstance(readiness.get("operational_targets_by_id"), dict):
        readiness["operational_targets_by_id"] = _normalize_json_like(readiness_artifacts.get("operational_targets_by_id") or {})
    if isinstance(readiness_artifacts, dict) and not isinstance(readiness.get("operational_evidence_summary"), dict):
        readiness["operational_evidence_summary"] = _normalize_json_like(readiness_artifacts.get("operational_evidence_summary") or {})
    if isinstance(readiness_artifacts, dict) and not isinstance(readiness.get("operational_latency_summary"), dict):
        readiness["operational_latency_summary"] = _normalize_json_like(readiness_artifacts.get("operational_latency_summary") or {})
    if documentation_sync:
        readiness["documentation_sync"] = _normalize_json_like(documentation_sync)

    readiness["operational_targets_by_id"] = _normalize_json_like(readiness.get("operational_targets_by_id") or {})
    readiness["operational_evidence_summary"] = _normalize_json_like(readiness.get("operational_evidence_summary") or {})
    readiness["operational_latency_summary"] = _normalize_json_like(readiness.get("operational_latency_summary") or {})

    operational_snapshot = readiness.get("operational_evidence_snapshot")
    if not isinstance(operational_snapshot, dict):
        operational_snapshot = {}
    targets = list(operational_snapshot.get("targets") or []) if isinstance(operational_snapshot, dict) else []
    operational_summary = readiness.get("operational_evidence_summary")
    if not isinstance(operational_summary, dict):
        operational_summary = operational_snapshot.get("summary") if isinstance(operational_snapshot, dict) else {}
    targets_by_id = readiness.get("operational_targets_by_id")
    if not isinstance(targets_by_id, dict):
        targets_by_id = operational_snapshot.get("targets_by_id") if isinstance(operational_snapshot, dict) else {}
    verified_target_count = operational_summary.get("verified_count") if isinstance(operational_summary, dict) else None
    required_target_count = operational_summary.get("required_count") if isinstance(operational_summary, dict) else None
    warning_target_count = operational_summary.get("warning_count") if isinstance(operational_summary, dict) else None
    failed_target_count = operational_summary.get("failed_count") if isinstance(operational_summary, dict) else None
    max_latency_ms = operational_summary.get("max_latency_ms") if isinstance(operational_summary, dict) else None
    if not isinstance(verified_target_count, int):
        verified_target_count = sum(1 for item in targets if isinstance(item, dict) and item.get("status") == "verified")
    if not isinstance(required_target_count, int):
        required_target_count = len(targets)
    if not isinstance(warning_target_count, int):
        warning_target_count = sum(1 for item in targets if isinstance(item, dict) and str(item.get("status") or "").lower() in {"warning", "degraded"})
    if not isinstance(failed_target_count, int):
        failed_target_count = sum(1 for item in targets if isinstance(item, dict) and str(item.get("status") or "").lower() not in {"verified", "warning", "degraded"})

    normalized_bundle = {
        "contract": contract,
        "execution": execution,
        "readiness": readiness,
        "operations": {
            "canonical_source": "evidence_bundle.readiness.operational_evidence_snapshot",
            "operational_evidence_deprecated": True,
        },
        "selective_apply": selective_apply,
    }
    evidence_digest = {
        "completion_gate_ok": execution.get("completion_gate_ok"),
        "self_run_status": execution.get("self_run_status"),
        "failure_tag_count": len(failure_tags),
        "target_file_id_count": len(target_file_ids),
        "operational_target_count": required_target_count,
        "operational_verified_count": verified_target_count,
        "operational_warning_count": warning_target_count,
        "operational_failed_count": failed_target_count,
        "operational_max_latency_ms": max_latency_ms,
    }
    return {
        "evidence_bundle": normalized_bundle,
        "evidence_digest": evidence_digest,
        "documentation_sync": documentation_sync,
        "live_documentation_scan": _normalize_json_like(live_documentation_scan if isinstance(live_documentation_scan, dict) else {}),
        "operational_targets_by_id": targets_by_id,
        "operational_evidence_summary": operational_summary,
        "target_file_ids": target_file_ids,
        "target_section_ids": target_section_ids,
        "target_feature_ids": target_feature_ids,
        "target_chunk_ids": target_chunk_ids,
        "failure_tags": failure_tags,
        "repair_tags": repair_tags,
        "target_patch_entries": target_patch_entries,
        "validation_payload_path": str(validation_payload_path) if validation_payload_path is not None else None,
        "validation_payload_candidate_paths": [str(path) for path in validation_payload_candidates],
        "validation_payload_readiness": _normalize_json_like((validation_payload.get("evidence_bundle") or {}).get("readiness") or {}),
        "response_readiness": _normalize_json_like(readiness),
    }


def _attach_capability_evidence_context(capability_map: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    for capability_id in CAPABILITY_ORDER:
        capability = capability_map.get(capability_id)
        if not isinstance(capability, dict):
            continue
        capability["evidence_context"] = _build_capability_evidence_context(capability)
    return capability_map


def _stabilize_self_run_record_payload(
    record_path: Path,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    if str(payload.get("status") or "") != "running":
        return payload
    try:
        from backend.admin_router import _stabilize_running_self_run_record
    except Exception:
        return payload
    try:
        return _stabilize_running_self_run_record(record_path, payload)
    except Exception:
        return payload


def _resolve_shared_admin_runtime_root() -> Path | None:
    try:
        return resolve_admin_shared_runtime_root()
    except Exception:
        return None


def _candidate_admin_runtime_roots() -> List[Path]:
    roots: List[Path] = []
    configured_root = os.getenv("ADMIN_RUNTIME_ROOT", "").strip()
    if configured_root:
        roots.append(Path(configured_root).expanduser().resolve())

    try:
        roots.append(resolve_admin_runtime_root())
    except Exception:
        pass

    shared_runtime_root = _resolve_shared_admin_runtime_root()
    if shared_runtime_root is not None:
        roots.append(shared_runtime_root)

    workspace_runtime_root = (REPO_ROOT / "uploads" / "tmp" / "codeai_admin_runtime").resolve()
    roots.append(workspace_runtime_root)

    roots.append((Path(tempfile.gettempdir()) / "codeai_admin_runtime").resolve())

    unique_roots: List[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        unique_roots.append(root)
    return unique_roots


def _admin_runtime_root() -> Path:
    return _candidate_admin_runtime_roots()[0]


def _operational_evidence_cache_path() -> Path:
    return _admin_runtime_root() / "operational_evidence_cache.json"


def _read_operational_evidence_cache() -> Dict[str, Any]:
    cache_path = _operational_evidence_cache_path()
    if not cache_path.exists() or not cache_path.is_file():
        return {}
    try:
        payload = json.loads(_safe_read_text(cache_path))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_operational_evidence_cache(payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized_payload = payload if isinstance(payload, dict) else {}
    cache_path = _operational_evidence_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(normalized_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return normalized_payload


def _self_run_record_root() -> Path:
    return _admin_runtime_root() / "admin_self_runs"


def _self_run_record_roots() -> List[Path]:
    return [root / "admin_self_runs" for root in _candidate_admin_runtime_roots()]


def _self_run_experiment_root() -> Path:
    return _admin_runtime_root() / "admin_self_experiments"


def _normalize_rel_paths(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    normalized: List[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        path = value.strip().replace("\\", "/")
        if path:
            normalized.append(path)
    return list(dict.fromkeys(normalized))


def _merge_states(*states: str) -> str:
    order = {"standby": 0, "active": 1, "warning": 2, "error": 3}
    return max(states, key=lambda item: order.get(item, 0)) if states else "active"


def _findings_state(findings: List[Dict[str, Any]]) -> str:
    if any(item.get("severity") == "error" for item in findings):
        return "error"
    if any(item.get("severity") == "warning" for item in findings):
        return "warning"
    return "active"


def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _record_sort_key(payload: Dict[str, Any], record_path: Path) -> tuple[float, float]:
    for key in ("finished_at", "worker_finished_at", "started_at", "created_at"):
        parsed = _parse_iso_datetime(payload.get(key))
        if parsed is not None:
            return (parsed.timestamp(), record_path.stat().st_mtime)
    return (0.0, record_path.stat().st_mtime)


def _hours_since(timestamp_text: str) -> float | None:
    parsed = _parse_iso_datetime(timestamp_text)
    if parsed is None:
        return None
    return round(max(0.0, (datetime.now(parsed.tzinfo) - parsed).total_seconds() / 3600), 1)


def _runtime_display_meta(
    latest_status: str,
    raw_state: str,
    last_run_age_hours: float | None,
    record_available: bool,
) -> Dict[str, Any]:
    if not record_available:
        return {
            "display_state": "standby",
            "state_label": "대기",
            "state_reason": "최근 self-run 기록이 없어 자동 감시 대기 상태입니다.",
            "staleness_label": "실행 기록 없음",
            "attention_required": False,
        }

    if latest_status == "running":
        return {
            "display_state": "warning",
            "state_label": "실행 중",
            "state_reason": "현재 self-run 이 진행 중이어서 결과 확정 전입니다.",
            "staleness_label": "실행 중",
            "attention_required": True,
        }

    if latest_status == "failed":
        if last_run_age_hours is not None and last_run_age_hours >= 6:
            return {
                "display_state": "standby",
                "state_label": "보관된 실패",
                "state_reason": "오래된 실패는 즉시 장애 대신 보관 경고로 낮춰 표시합니다.",
                "staleness_label": f"마지막 실패 {last_run_age_hours:.1f}시간 전",
                "attention_required": True,
            }
        if last_run_age_hours is not None and last_run_age_hours >= 1:
            return {
                "display_state": "warning",
                "state_label": "주의",
                "state_reason": "최근 실패 흔적이 남아 있어 재검증이 필요합니다.",
                "staleness_label": f"마지막 실패 {last_run_age_hours:.1f}시간 전",
                "attention_required": True,
            }
        return {
            "display_state": "error",
            "state_label": "즉시 조치",
            "state_reason": "방금 발생한 실패로 즉시 복구가 필요합니다.",
            "staleness_label": (
                f"마지막 실패 {last_run_age_hours:.1f}시간 전"
                if last_run_age_hours is not None
                else "방금 실패"
            ),
            "attention_required": True,
        }

    if latest_status == "no_changes":
        if last_run_age_hours is not None and last_run_age_hours >= 6:
            return {
                "display_state": "standby",
                "state_label": "대기",
                "state_reason": "오래된 무변경 실행 기록은 대기 상태로 낮춰 표시합니다.",
                "staleness_label": f"마지막 무변경 실행 {last_run_age_hours:.1f}시간 전",
                "attention_required": True,
            }
        return {
            "display_state": "warning",
            "state_label": "주의",
            "state_reason": "최근 실행이 변경 없이 끝나 개선 효과를 만들지 못했습니다.",
            "staleness_label": (
                f"마지막 무변경 실행 {last_run_age_hours:.1f}시간 전"
                if last_run_age_hours is not None
                else "최근 무변경 실행"
            ),
            "attention_required": True,
        }

    if raw_state == "error":
        return {
            "display_state": "error",
            "state_label": "즉시 조치",
            "state_reason": "최근 실행 근거에서 실제 오류가 감지되었습니다.",
            "staleness_label": None,
            "attention_required": True,
        }
    if raw_state == "warning":
        return {
            "display_state": "warning",
            "state_label": "주의",
            "state_reason": "최근 실행 근거에서 관찰이 필요한 신호가 감지되었습니다.",
            "staleness_label": None,
            "attention_required": True,
        }
    return {
        "display_state": "active",
        "state_label": "정상",
        "state_reason": "최근 실행 근거 기준으로 정상 상태입니다.",
        "staleness_label": (
            f"마지막 실행 {last_run_age_hours:.1f}시간 전"
            if last_run_age_hours is not None
            else None
        ),
        "attention_required": False,
    }


def _is_running_self_run_status(status: str) -> bool:
    return str(status or "").strip().lower() == "running"


def _classify_self_run_scope(source_path_text: str) -> Dict[str, str]:
    if not source_path_text:
        return {"scope": "unknown", "label": "실행 범위 불명"}
    source_path = Path(source_path_text)
    try:
        resolved = source_path.resolve()
    except Exception:
        resolved = source_path
    try:
        if resolved == REPO_ROOT.resolve():
            return {"scope": "whole-project", "label": "전체 프로젝트"}
    except Exception:
        pass
    for fixture_root, scope, label in [
        (LEGACY_SELF_RUN_FIXTURE_ROOT, "validation-fixture", "검증용 fixture"),
        (_self_run_experiment_root(), "validation-fixture", "실험 복제본"),
        (LEGACY_SELF_RUN_EXPERIMENT_ROOT, "validation-fixture", "실험 복제본"),
    ]:
        try:
            resolved.relative_to(fixture_root.resolve())
            return {"scope": scope, "label": label}
        except Exception:
            continue
    try:
        resolved.relative_to(REPO_ROOT.resolve())
        return {"scope": "workspace-subtree", "label": "프로젝트 하위 경로"}
    except Exception:
        return {"scope": "external", "label": "외부 경로"}


def _latest_self_run_record() -> Dict[str, Any]:
    record_roots = _self_run_record_roots()
    searched_roots = [str(path) for path in record_roots]

    candidates: List[Dict[str, Any]] = []
    for record_root in record_roots:
        if not record_root.exists() or not record_root.is_dir():
            continue
        for candidate_dir in (path for path in record_root.iterdir() if path.is_dir()):
            record_path = candidate_dir / "approval.json"
            if not record_path.exists():
                continue
            payload = _safe_read_json(record_path)
            if not payload:
                continue
            payload = _stabilize_self_run_record_payload(record_path, payload)
            candidates.append(
                {
                    "record_path": str(record_path),
                    "payload": payload,
                    "sort_key": _record_sort_key(payload, record_path),
                }
            )

    for candidate in sorted(candidates, key=lambda item: item["sort_key"], reverse=True):
        return {
            "available": True,
            "record_path": candidate["record_path"],
            "payload": candidate["payload"],
            "searched_roots": searched_roots,
        }
    any_root_exists = any(path.exists() and path.is_dir() for path in record_roots)
    return {
        "available": False,
        "reason": (
            "읽을 수 있는 self-run 기록이 없습니다."
            if any_root_exists
            else "self-run 기록 디렉터리가 없습니다."
        ),
        "searched_roots": searched_roots,
    }


def _should_archive_runtime_signal(
    scope_info: Dict[str, str],
    latest_status: str,
    approval_gate_ok: Any,
    orchestration_result: Dict[str, Any],
) -> bool:
    if scope_info.get("scope") != "whole-project":
        return True
    if latest_status == "running":
        return False
    applied = orchestration_result.get("applied") is True
    if applied:
        return False
    return latest_status in {"failed", "no_changes"} and approval_gate_ok is False


def _should_suppress_runtime_attention(
    *,
    archived_runtime_signal: bool,
    last_run_age_hours: Optional[float],
    latest_status: str,
) -> bool:
    if archived_runtime_signal:
        return True
    if latest_status in {"failed", "no_changes", "unknown"} and isinstance(last_run_age_hours, (int, float)) and last_run_age_hours >= 24:
        return True
    return False


def _inspect_generated_sources(output_dir_text: str, candidate_files: List[str]) -> Dict[str, Any]:
    if not output_dir_text:
        return {
            "source_file_count": 0,
            "tiny_source_count": 0,
            "tiny_source_ratio_exceeded": False,
            "tiny_sources": [],
            "average_source_size": 0,
        }

    output_dir = Path(output_dir_text)
    source_sizes: List[tuple[str, int]] = []
    for rel_path in candidate_files:
        suffix = Path(rel_path).suffix.lower()
        if suffix not in SOURCE_FILE_SUFFIXES:
            continue
        target = (output_dir / rel_path).resolve()
        try:
            size = target.stat().st_size
        except Exception:
            continue
        source_sizes.append((rel_path, size))

    tiny_sources = [
        rel_path for rel_path, size in source_sizes
        if size <= ORCH_TINY_SOURCE_BYTES
    ]
    average_source_size = (
        sum(size for _, size in source_sizes) / len(source_sizes)
        if source_sizes
        else 0
    )
    tiny_source_ratio_exceeded = bool(source_sizes) and (
        len(tiny_sources) >= max(3, int(len(source_sizes) * 0.6))
    )
    return {
        "source_file_count": len(source_sizes),
        "tiny_source_count": len(tiny_sources),
        "tiny_source_ratio_exceeded": tiny_source_ratio_exceeded,
        "tiny_sources": tiny_sources,
        "average_source_size": round(average_source_size, 1),
    }


def _build_runtime_diagnostics() -> Dict[str, Any]:
    latest_record = _latest_self_run_record()
    if not latest_record.get("available"):
        reason = latest_record.get("reason") or "실행 기록 없음"
        expected_root = str(_self_run_record_root())
        searched_roots = list(latest_record.get("searched_roots") or [expected_root])
        display_meta = _runtime_display_meta("unknown", "warning", None, False)
        findings = [
            {
                "severity": "warning",
                "category": "runtime-evidence",
                "message": "전체 프로젝트 self-run 근거가 없어 경고등을 실제 실행 기준으로 확정할 수 없습니다.",
                "details": (
                    f"{reason} expected={expected_root} "
                    f"searched={'; '.join(searched_roots)}"
                ),
            }
        ]
        return {
            "available": False,
            "state": "warning",
            "display_state": display_meta["display_state"],
            "state_label": display_meta["state_label"],
            "state_reason": display_meta["state_reason"],
            "attention_required": display_meta["attention_required"],
            "staleness_label": display_meta["staleness_label"],
            "scope": "unknown",
            "scope_label": "실행 근거 없음",
            "latest_status": "unknown",
            "summary": f"{reason} (expected: {expected_root})",
            "record_path": None,
            "findings": findings,
            "actions": [
                "관리자 self-run 저장 루트와 capability 진단 루트가 같은 경로를 보도록 먼저 복구하세요.",
                "전체 프로젝트 기준으로 workspace self-run 을 다시 실행해 실제 경고 근거를 수집하세요.",
            ],
            "searched_roots": searched_roots,
            "written_file_count": 0,
            "changed_file_count": 0,
            "changed_dir_count": 0,
            "last_run_started_at": None,
            "last_run_finished_at": None,
            "last_run_age_hours": None,
            "minimums": {"files": ORCH_MIN_FILES, "dirs": ORCH_MIN_DIRS},
            "source_inspection": {
                "source_file_count": 0,
                "tiny_source_count": 0,
                "tiny_source_ratio_exceeded": False,
                "tiny_sources": [],
                "average_source_size": 0,
            },
            "approval_gate_ok": None,
            "completion_gate_ok": None,
            "semantic_audit_ok": None,
            "semantic_audit_score": None,
            "semantic_audit_threshold": None,
        }

    payload = latest_record["payload"]
    orchestration_result = payload.get("orchestration_result") or {}
    diff_summary = payload.get("diff_summary") or {}
    source_path = str(payload.get("source_path") or "")
    scope_info = _classify_self_run_scope(source_path)
    written_files = _normalize_rel_paths(orchestration_result.get("written_files"))
    added_files = _normalize_rel_paths(diff_summary.get("added_files"))
    modified_files = _normalize_rel_paths(diff_summary.get("modified_files"))
    candidate_files = list(dict.fromkeys(written_files + added_files + modified_files))
    changed_dirs = sorted(
        {
            str(Path(path).parent).replace("\\", "/")
            for path in candidate_files
            if str(Path(path).parent) not in {"", "."}
        }
    )
    source_inspection = _inspect_generated_sources(
        str(orchestration_result.get("output_dir") or ""),
        candidate_files or written_files,
    )
    findings: List[Dict[str, Any]] = []
    actions: List[str] = []
    latest_status = str(payload.get("status") or "unknown")
    last_run_started_at = str(payload.get("started_at") or "") or None
    last_run_finished_at = str(
        payload.get("finished_at") or payload.get("worker_finished_at") or ""
    ) or None
    completion_gate_ok = orchestration_result.get("completion_gate_ok")
    approval_gate_ok = payload.get("approval_gate_ok")
    semantic_audit_ok = orchestration_result.get("semantic_audit_ok")
    semantic_audit_score = orchestration_result.get("semantic_audit_score")
    semantic_audit_threshold = orchestration_result.get("semantic_audit_threshold")
    fallback_reason = str(orchestration_result.get("fallback_reason") or "")
    orchestration_error = str(payload.get("orchestration_error") or "")
    runtime_diagnostic = str(payload.get("runtime_diagnostic") or "")
    worker_pid = payload.get("worker_pid")
    worker_alive = payload.get("worker_alive")
    running_seconds = payload.get("running_seconds")
    worker_log_path = str(payload.get("worker_log_path") or "")
    completion_gate_error = str(orchestration_result.get("completion_gate_error") or "")
    apply_error = str(orchestration_result.get("apply_error") or "")
    approval_failed_fields = payload.get("approval_gate_failed_fields") or []
    is_running_status = _is_running_self_run_status(latest_status)
    worker_log_excerpt = ""
    if worker_log_path:
        try:
            worker_log_excerpt = _tail_text(
                _safe_read_text(Path(worker_log_path)),
                max_chars=1800,
            )
        except Exception:
            worker_log_excerpt = ""

    archived_runtime_signal = _should_archive_runtime_signal(
        scope_info,
        latest_status,
        approval_gate_ok,
        orchestration_result,
    )

    if scope_info["scope"] != "whole-project":
        findings.append(
            {
                "severity": "warning",
                "category": "runtime-scope",
                "message": "최신 self-run 기록이 전체 프로젝트 기준이 아니어서 현재 경고등은 보조 진단으로만 해석해야 합니다.",
                "details": f"범위={scope_info['label']} source={source_path or '-'}",
            }
        )
        actions.append(
            "전체 프로젝트 루트를 대상으로 workspace self-run 을 다시 실행해 경고등 근거를 실제 프로젝트 기준으로 갱신하세요."
        )

    if is_running_status:
        findings.append(
            {
                "severity": "warning",
                "category": "self-run-status",
                "message": "최신 self-run 이 아직 실행 중입니다.",
                "details": runtime_diagnostic or "status=running",
            }
        )
        actions.append(
            "동일 approval 기록이 완료될 때까지 대기한 뒤 같은 실행 근거로 다시 판정하세요."
        )
    elif latest_status == "failed":
        findings.append(
            {
                "severity": "error",
                "category": "self-run-status",
                "message": "최신 self-run 이 실패 상태로 종료되었습니다.",
                "details": apply_error or completion_gate_error or "status=failed",
            }
        )
    elif latest_status == "no_changes":
        findings.append(
            {
                "severity": "warning",
                "category": "self-run-status",
                "message": "최신 self-run 이 변경 없이 종료되어 개선 산출물이 남지 않았습니다.",
                "details": "status=no_changes",
            }
        )

    if orchestration_error:
        findings.append(
            {
                "severity": "error",
                "category": "runtime-exception",
                "message": "자가 실행 중 런타임 예외가 기록되었습니다.",
                "details": orchestration_error[:240],
            }
        )
        actions.append("orchestration_error 내용을 기준으로 실행 예외부터 제거하세요.")

    if runtime_diagnostic:
        worker_severity = "warning"
        worker_category = "worker-log"
        if (
            "종료됐지만 approval 기록이 running 상태" in runtime_diagnostic
            or latest_status == "failed"
        ):
            worker_severity = "error"
            worker_category = "worker-process-exited"
        elif "초과했습니다" in runtime_diagnostic:
            worker_severity = "error"
            worker_category = "worker-timeout"
        findings.append(
            {
                "severity": worker_severity,
                "category": worker_category,
                "message": "background worker 런타임 진단이 기록되었습니다.",
                "details": runtime_diagnostic,
            }
        )

    if worker_log_excerpt:
        if "qdrant" in worker_log_excerpt.lower() or "connection" in worker_log_excerpt.lower():
            findings.append(
                {
                    "severity": "error",
                    "category": "vector-db-connect",
                    "message": "worker 로그에서 벡터 DB 연결 실패 흔적이 감지되었습니다.",
                    "details": _tail_text(worker_log_excerpt, max_chars=240),
                }
            )
            actions.append(
                "Qdrant 연결 지연 또는 실패 시 in-memory fallback 으로 계속 진행되도록 project indexer 를 우선 복구하세요."
            )
        findings.append(
            {
                "severity": "warning",
                "category": "worker-log",
                "message": "worker 로그 발췌가 남아 있어 직접 실패 원인을 추적할 수 있습니다.",
                "details": _tail_text(worker_log_excerpt, max_chars=240),
            }
        )

    if fallback_reason:
        findings.append(
            {
                "severity": "error",
                "category": "planner-fallback",
                "message": "플래너/생성 파이프라인이 fallback 경로로 이탈했습니다.",
                "details": fallback_reason,
            }
        )
        actions.append("fallback_reason 에 기록된 planner/spec 문제를 우선 복구하세요.")

    if not is_running_status and completion_gate_ok is False:
        findings.append(
            {
                "severity": "error",
                "category": "completion-gate",
                "message": "완료 게이트가 실패해 산출물이 승인 가능한 상태에 도달하지 못했습니다.",
                "details": completion_gate_error or "completion_gate_ok=False",
            }
        )
        actions.append("completion gate 실패 원인을 해소한 뒤 동일 지시로 재실행하세요.")

    if not is_running_status and approval_gate_ok is False:
        failed_fields_text = ", ".join(str(item) for item in approval_failed_fields) or "approval_gate_ok=False"
        findings.append(
            {
                "severity": "error",
                "category": "approval-gate",
                "message": "승인 게이트가 실패해 원본 반영 가능 상태가 아닙니다.",
                "details": failed_fields_text,
            }
        )
        actions.append("approval_gate_failed_fields 에 적힌 항목을 충족하도록 산출물을 보강하세요.")

    if not is_running_status and orchestration_result.get("postcheck_ok") is False:
        findings.append(
            {
                "severity": "error",
                "category": "postcheck",
                "message": "postcheck 검증이 실패했습니다.",
                "details": str(orchestration_result.get("postcheck_error") or "postcheck_ok=False"),
            }
        )
    if (
        not is_running_status
        and orchestration_result.get("secondary_validation_ok") is False
    ):
        findings.append(
            {
                "severity": "error",
                "category": "secondary-validation",
                "message": "2차 검증이 실패했습니다.",
                "details": str(orchestration_result.get("secondary_validation_error") or "secondary_validation_ok=False"),
            }
        )
    if not is_running_status and orchestration_result.get("dod_ok") is False:
        findings.append(
            {
                "severity": "warning",
                "category": "definition-of-done",
                "message": "Definition of Done 검증을 통과하지 못했습니다.",
                "details": str(orchestration_result.get("dod_error") or "dod_ok=False"),
            }
        )

    if scope_info["scope"] == "whole-project" and not is_running_status:
        if len(written_files) < ORCH_MIN_FILES:
            findings.append(
                {
                    "severity": "error",
                    "category": "output-files",
                    "message": "생성 로그 기준 산출물 파일 수가 최소 기준에 못 미칩니다.",
                    "details": f"written_files={len(written_files)} / min={ORCH_MIN_FILES}",
                }
            )
            actions.append("파일 생성 수가 최소 기준을 넘기도록 생성 범위와 계획 단계를 확장하세요.")
        if len(changed_dirs) < ORCH_MIN_DIRS:
            findings.append(
                {
                    "severity": "warning",
                    "category": "output-directories",
                    "message": "산출물 폴더 수가 최소 기준에 못 미칩니다.",
                    "details": f"dirs={len(changed_dirs)} / min={ORCH_MIN_DIRS}",
                }
            )
            actions.append("한 폴더 집중 수정이 아니라 필요한 디렉터리 구조까지 생성되도록 작업 범위를 조정하세요.")
        if source_inspection["source_file_count"] == 0 and candidate_files:
            findings.append(
                {
                    "severity": "warning",
                    "category": "source-output",
                    "message": "변경 파일은 있으나 실제 소스 코드 파일 생성 근거가 부족합니다.",
                    "details": f"changed_files={len(candidate_files)} source_files=0",
                }
            )
        if source_inspection["tiny_source_ratio_exceeded"]:
            findings.append(
                {
                    "severity": "error",
                    "category": "code-volume",
                    "message": "생성된 소스 파일 다수가 1KB 이하 축약본이라 코드량이 부족합니다.",
                    "details": (
                        f"tiny_sources={source_inspection['tiny_source_count']} / "
                        f"source_files={source_inspection['source_file_count']}"
                    ),
                }
            )
            actions.append("tiny source 비율이 낮아지도록 세부 구현과 로직 밀도를 늘려 다시 생성하세요.")

    if not is_running_status and semantic_audit_ok is False:
        findings.append(
            {
                "severity": "error",
                "category": "semantic-audit",
                "message": "의미 품질 감사가 실패했습니다.",
                "details": str(orchestration_result.get("semantic_audit_error") or "semantic_audit_ok=False"),
            }
        )
        actions.append("semantic audit 실패 이유를 제거한 뒤 재실행하세요.")
    elif (
        not is_running_status
        and isinstance(semantic_audit_score, (int, float))
        and isinstance(semantic_audit_threshold, (int, float))
    ):
        if semantic_audit_score < semantic_audit_threshold:
            findings.append(
                {
                    "severity": "warning",
                    "category": "semantic-score",
                    "message": "의미 품질 점수가 임계치 아래입니다.",
                    "details": f"score={semantic_audit_score} / threshold={semantic_audit_threshold}",
                }
            )

    if not actions:
        actions.append("현재 실행 기록 기준 즉시 복구가 필요한 직접 원인은 감지되지 않았습니다.")

    raw_state = _findings_state(findings)
    last_run_reference = last_run_finished_at or last_run_started_at or ""
    last_run_age_hours = _hours_since(last_run_reference)
    display_meta = _runtime_display_meta(
        latest_status,
        raw_state,
        last_run_age_hours,
        True,
    )
    suppressed_runtime_attention = _should_suppress_runtime_attention(
        archived_runtime_signal=archived_runtime_signal,
        last_run_age_hours=last_run_age_hours,
        latest_status=latest_status,
    )
    if suppressed_runtime_attention:
        display_meta = {
            "display_state": "standby",
            "state_label": "보관 진단",
            "state_reason": (
                "원본 반영 전 실패하거나 범위가 제한된 self-run 기록은 운영 경고 대신 보관 진단으로만 유지합니다."
                if archived_runtime_signal
                else "오래된 self-run 실패 기록은 즉시 경고 승격 대신 보관 진단으로만 유지합니다."
            ),
            "staleness_label": display_meta.get("staleness_label"),
            "attention_required": False,
        }
    summary = (
        f"최신 self-run 상태={latest_status}, 범위={scope_info['label']}, "
        f"오류 {sum(1 for item in findings if item.get('severity') == 'error')}건"
    )
    return {
        "available": True,
        "state": raw_state,
        "display_state": display_meta["display_state"],
        "state_label": display_meta["state_label"],
        "state_reason": display_meta["state_reason"],
        "attention_required": display_meta["attention_required"],
        "staleness_label": display_meta["staleness_label"],
        "archived_runtime_signal": archived_runtime_signal,
        "suppressed_runtime_attention": suppressed_runtime_attention,
        "scope": scope_info["scope"],
        "scope_label": scope_info["label"],
        "latest_status": latest_status,
        "requested_mode": str(payload.get("requested_mode") or ""),
        "execution_mode": str(payload.get("execution_mode") or ""),
        "source_path": source_path,
        "record_path": latest_record.get("record_path"),
        "summary": summary,
        "findings": findings,
        "actions": list(dict.fromkeys(actions)),
        "worker_pid": worker_pid,
        "worker_alive": worker_alive,
        "running_seconds": running_seconds,
        "last_run_started_at": last_run_started_at,
        "last_run_finished_at": last_run_finished_at,
        "last_run_age_hours": last_run_age_hours,
        "runtime_diagnostic": runtime_diagnostic,
        "worker_log_path": worker_log_path,
        "worker_log_excerpt": worker_log_excerpt,
        "written_file_count": len(written_files),
        "changed_file_count": len(candidate_files),
        "changed_dir_count": len(changed_dirs),
        "minimums": {"files": ORCH_MIN_FILES, "dirs": ORCH_MIN_DIRS},
        "source_inspection": source_inspection,
        "approval_gate_ok": approval_gate_ok,
        "completion_gate_ok": completion_gate_ok,
        "semantic_audit_ok": semantic_audit_ok,
        "semantic_audit_score": semantic_audit_score,
        "semantic_audit_threshold": semantic_audit_threshold,
        "approval_gate_failed_fields": approval_failed_fields,
        "fallback_reason": fallback_reason,
        "approval_id": str(payload.get("approval_id") or Path(str(latest_record.get("record_path") or "")).parent.name or ""),
        "experiment_clone_path": str(payload.get("experiment_clone_path") or ""),
        "validation_profile": str(orchestration_result.get("validation_profile") or payload.get("validation_profile") or ""),
        "output_dir": str(orchestration_result.get("output_dir") or ""),
        "failed_output_dir": str(orchestration_result.get("failed_output_dir") or ""),
    }


def _workspace_scan() -> Dict[str, Any]:
    directories: List[str] = []
    files: List[str] = []
    total_dirs = 0
    total_files = 0
    for child in sorted(REPO_ROOT.iterdir(), key=lambda item: item.name.lower()):
        if child.name in SCAN_EXCLUDED_DIRS:
            continue
        if child.is_dir():
            directories.append(child.name)
        else:
            files.append(child.name)

    for root, dir_names, file_names in os.walk(REPO_ROOT):
        dir_names[:] = [
            dir_name
            for dir_name in dir_names
            if dir_name not in SCAN_EXCLUDED_DIRS
        ]
        total_dirs += len(dir_names)
        total_files += len(file_names)

    entrypoints = []
    missing_expected = []
    for label, relative_path in WORKSPACE_ENTRYPOINTS:
        exists = (REPO_ROOT / relative_path).exists()
        entrypoints.append(
            {
                "label": label,
                "path": relative_path.as_posix(),
                "exists": exists,
            }
        )
        if not exists:
            missing_expected.append(relative_path.as_posix())

    risk_items = []
    if missing_expected:
        risk_items.append("핵심 진입 파일 또는 운영 파일 일부가 누락되었습니다.")
    if not (REPO_ROOT / "backend/requirements.txt").exists():
        risk_items.append("백엔드 requirements.txt가 없어 환경 재현성이 낮습니다.")

    return {
        "workspace_root": str(REPO_ROOT),
        "top_level_directories": directories,
        "top_level_files": files,
        "counts": {
            "directories": total_dirs,
            "files": total_files,
        },
        "entrypoints": entrypoints,
        "missing_expected": missing_expected,
        "risk_items": risk_items,
    }


def _parse_package_dependencies(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {
            "path": _relative_path(path),
            "dependencies": [],
            "dev_dependencies": [],
            "scripts": [],
        }
    try:
        payload = json.loads(_safe_read_text(path))
    except Exception:
        payload = {}
    dependencies = sorted((payload.get("dependencies") or {}).keys())
    dev_dependencies = sorted((payload.get("devDependencies") or {}).keys())
    scripts = sorted((payload.get("scripts") or {}).keys())
    return {
        "path": _relative_path(path),
        "dependencies": dependencies,
        "dev_dependencies": dev_dependencies,
        "scripts": scripts,
    }


def _parse_requirements(path: Path) -> Dict[str, Any]:
    requirements: List[str] = []
    if path.exists():
        for raw_line in _safe_read_text(path).splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            requirements.append(line)
    return {
        "path": _relative_path(path),
        "requirements": requirements,
    }


def _parse_compose_services(path: Path) -> List[str]:
    if not path.exists():
        return []
    services: List[str] = []
    inside_services = False
    for raw_line in _safe_read_text(path).splitlines():
        if raw_line.strip() == "services:":
            inside_services = True
            continue
        if inside_services and raw_line and not raw_line.startswith(" "):
            break
        if not inside_services:
            continue
        match = re.match(r"^\s{2}([a-zA-Z0-9_.-]+):\s*$", raw_line)
        if match:
            services.append(match.group(1))
    return services


def _dependency_graph() -> Dict[str, Any]:
    root_package = _parse_package_dependencies(REPO_ROOT / "package.json")
    frontend_package = _parse_package_dependencies(REPO_ROOT / "frontend/package.json")
    admin_frontend_package = _parse_package_dependencies(REPO_ROOT / "frontend/frontend/package.json")
    backend_requirements = _parse_requirements(REPO_ROOT / "backend/requirements.txt")
    compose_services = _parse_compose_services(REPO_ROOT / "docker-compose.yml")

    integration_points = [
        {
            "source": "frontend/frontend/app/admin/page.tsx",
            "target": "frontend/frontend/app/admin/llm/page.tsx",
            "kind": "iframe-embed",
            "note": "관리자 대시보드가 /admin/llm 을 내장 패널로 포함합니다.",
        },
        {
            "source": "frontend/frontend/app/admin/llm/page.tsx",
            "target": "/api/llm/orchestrate",
            "kind": "api",
            "note": "관리자 지시형 실행과 기능별 통솔 카드가 같은 실행 API를 공유합니다.",
        },
        {
            "source": "frontend/frontend/app/admin/llm/page.tsx",
            "target": "/api/admin/system-settings",
            "kind": "api",
            "note": "전역 운영값과 오케스트레이터 런타임 설정이 연결됩니다.",
        },
        {
            "source": "backend/main.py",
            "target": "backend/llm/orchestrator.py",
            "kind": "router",
            "note": "백엔드 메인 앱이 오케스트레이터 라우터를 직접 편입합니다.",
        },
    ]

    return {
        "packages": [root_package, frontend_package, admin_frontend_package],
        "backend": backend_requirements,
        "compose_services": compose_services,
        "integration_points": integration_points,
    }


def _security_guard(project_scan: Dict[str, Any]) -> Dict[str, Any]:
    auth_files = [
        "backend/auth.py",
        "backend/auth_router.py",
        "backend/admin_router.py",
    ]
    missing_auth_files = [
        path for path in auth_files if not (REPO_ROOT / path).exists()
    ]
    findings = []
    if is_weak_secret_key():
        findings.append(
            {
                "severity": "warning",
                "message": "SECRET_KEY가 로컬 기본값이거나 약한 값으로 감지되었습니다.",
            }
        )
    if missing_auth_files:
        findings.append(
            {
                "severity": "error",
                "message": "관리자 인증 핵심 파일 일부가 누락되었습니다.",
                "details": missing_auth_files,
            }
        )
    if project_scan["missing_expected"]:
        findings.append(
            {
                "severity": "warning",
                "message": "핵심 운영 파일 누락으로 인해 관리자 경로 검증이 불완전합니다.",
                "details": project_scan["missing_expected"],
            }
        )
    python_policy = scan_python_security_policy(REPO_ROOT / "backend")
    for item in python_policy["findings"][:20]:
        findings.append(
            {
                "severity": item.get("severity", "warning"),
                "message": item.get("message", "Python 보안 정책 경고"),
                "details": [
                    f"{item.get('path')}:{item.get('line')} | {item.get('evidence')}"
                ],
            }
        )
    runtime_config_exists = RUNTIME_CONFIG_PATH.exists()
    return {
        "auth_files": auth_files,
        "missing_auth_files": missing_auth_files,
        "runtime_config_exists": runtime_config_exists,
        "python_policy": python_policy,
        "findings": findings,
    }


def _model_control() -> Dict[str, Any]:
    available_models = get_available_ollama_models()
    configured_routes = get_configured_model_routes()
    execution_controls = get_configured_execution_controls()
    recommended_profiles = get_recommended_runtime_profiles()
    gpu_runtime = get_gpu_runtime_info()
    missing_configured_models = sorted(
        model_name
        for model_name in set(configured_routes.values())
        if model_name and model_name not in available_models
    )
    return {
        "runtime_config_path": _relative_path(RUNTIME_CONFIG_PATH),
        "available_models": available_models,
        "configured_routes": configured_routes,
        "execution_controls": execution_controls,
        "recommended_profiles": recommended_profiles,
        "gpu_runtime": gpu_runtime,
        "missing_configured_models": missing_configured_models,
    }


def _self_healing(
    project_scan: Dict[str, Any],
    security_guard: Dict[str, Any],
    model_control: Dict[str, Any],
    runtime_diagnostics: Dict[str, Any],
) -> Dict[str, Any]:
    actions = []
    actions.extend(runtime_diagnostics["actions"])
    if project_scan["missing_expected"]:
        actions.append("누락된 핵심 파일을 우선 복구하고 경로 단절 여부를 재검증하세요.")
    if security_guard["missing_auth_files"]:
        actions.append("관리자 인증 누락 파일을 복구한 뒤 관리자 API 접근 검증을 다시 수행하세요.")
    python_policy = security_guard.get("python_policy") or {}
    python_error_count = int(python_policy.get("error_count") or 0)
    python_warning_count = int(python_policy.get("warning_count") or 0)
    if python_error_count > 0:
        actions.append(f"python_security_validation 오류 {python_error_count}건을 우선 순위대로 수정한 뒤 self-run 을 재실행하세요.")
    elif python_warning_count > 0:
        actions.append(f"python_security_validation 경고 {python_warning_count}건을 정리해 재검증 품질을 높이세요.")
    if model_control["missing_configured_models"]:
        actions.append("현재 라우트에 연결된 Ollama 모델이 실제 서버에 존재하는지 맞춰 주세요.")
    if not model_control["gpu_runtime"].get("available"):
        actions.append("GPU 런타임이 감지되지 않아 CPU fallback 또는 드라이버 상태 점검이 필요합니다.")
    return {
        "actions": list(dict.fromkeys(actions)),
        "recovery_score": max(0, 100 - (len(actions) * 12)),
        "runtime_diagnostics": runtime_diagnostics,
    }


def _code_generator(
    project_scan: Dict[str, Any],
    dependency_graph: Dict[str, Any],
    runtime_diagnostics: Dict[str, Any],
    security_guard: Dict[str, Any],
) -> Dict[str, Any]:
    python_policy = security_guard.get("python_policy") or {}
    python_targets = []
    for finding in python_policy.get("findings") or []:
        candidate_path = str(finding.get("path") or "").strip()
        if candidate_path and candidate_path not in python_targets:
            python_targets.append(candidate_path)
    suggested_targets = [
        *python_targets[:6],
        "backend/llm/admin_capabilities.py",
        "frontend/frontend/app/admin/llm/page.tsx",
        "frontend/frontend/app/admin/page.tsx",
        "reports/COMMAND_LOG_20260318.md",
    ]
    suggested_targets = list(dict.fromkeys(suggested_targets))
    implementation_order = [
        "python_security_validation 오류를 우선 순위대로 제거",
        "latest self-run 실패 원인 5건과 보안 위반을 동일 작업문으로 병합",
        "관리자 오케스트레이터 페이지 경광판과 상세 패널 연결",
        "재검증 및 작업 기록",
    ]
    return {
        "suggested_targets": suggested_targets,
        "implementation_order": implementation_order,
        "frontend_dependency_count": len(dependency_graph["packages"][2]["dependencies"]),
        "missing_expected": project_scan["missing_expected"],
        "runtime_diagnostics": runtime_diagnostics,
        "python_policy": python_policy,
    }


def _admin_command_interface() -> Dict[str, Any]:
    commands = [
        {
            "title": "런타임 설정 조회/저장",
            "endpoint": "/api/llm/runtime-config",
            "usage": "역할별 모델 라우트와 실행 제한값을 확인하고 즉시 저장합니다.",
        },
        {
            "title": "전역 시스템 설정",
            "endpoint": "/api/admin/system-settings",
            "usage": "도메인, 저장소, 엔진, 기본 LLM 환경값을 중앙에서 제어합니다.",
        },
        {
            "title": "워크스페이스 자가 준비",
            "endpoint": "/api/admin/workspace-self-prepare",
            "usage": "자가진단/자가개선/자가확장 전 준비 작업을 수행합니다.",
        },
        {
            "title": "워크스페이스 자가 실행",
            "endpoint": "/api/admin/workspace-self-run",
            "usage": "관리자 승인 또는 직접 실행 기반 셀프 런을 수행합니다.",
        },
        {
            "title": "통솔 능력 요약",
            "endpoint": "/api/admin/orchestrator/capabilities/summary",
            "usage": "기능군별 현재 상태를 대시보드와 관리자 페이지에 공통 공급합니다.",
        },
    ]
    return {
        "commands": commands,
        "count": len(commands),
    }


def _build_capability_map() -> Dict[str, Dict[str, Any]]:
    project_scan = _workspace_scan()
    dependency_graph = _dependency_graph()
    security_guard = _security_guard(project_scan)
    model_control = _model_control()
    runtime_diagnostics = _build_runtime_diagnostics()
    documentation_sync = _build_capability_evidence_context({"payload": {"runtime_diagnostics": runtime_diagnostics}}).get("documentation_sync") or {}
    project_scan["runtime_diagnostics"] = runtime_diagnostics
    project_scan["documentation_sync"] = documentation_sync
    self_healing = _self_healing(project_scan, security_guard, model_control, runtime_diagnostics)
    code_generator = _code_generator(project_scan, dependency_graph, runtime_diagnostics, security_guard)
    code_generator["documentation_sync"] = documentation_sync
    admin_command_interface = _admin_command_interface()
    runtime_attention_enabled = not bool(runtime_diagnostics.get("suppressed_runtime_attention"))
    documentation_attention_enabled = isinstance(documentation_sync, dict) and not bool(documentation_sync.get("overall_status") in {"", "synced"})
    self_healing_requires_attention = any(
        [
            bool(project_scan["missing_expected"]),
            bool(security_guard["missing_auth_files"]),
            bool(model_control["missing_configured_models"]),
            not model_control["gpu_runtime"].get("available"),
            runtime_attention_enabled and bool(runtime_diagnostics["actions"]),
        ]
    )
    project_scanner_state = _merge_states(
        "warning" if project_scan["missing_expected"] else "active",
        "warning" if documentation_attention_enabled else "active",
        runtime_diagnostics["display_state"] if runtime_attention_enabled else "standby",
    )
    self_healing_state = _merge_states(
        "warning" if self_healing_requires_attention else "active",
        runtime_diagnostics["display_state"] if runtime_attention_enabled else "standby",
    )
    code_generator_state = _merge_states(
        "warning" if code_generator["missing_expected"] else "active",
        "warning" if documentation_attention_enabled else "active",
        runtime_diagnostics["display_state"] if runtime_attention_enabled else "standby",
    )
    documentation_stale_count = int(documentation_sync.get("stale_count") or 0) if isinstance(documentation_sync, dict) else 0
    documentation_state_reason = (
        f"문서 stale {documentation_stale_count}건이 최신 실검증 반영을 막고 있습니다."
        if documentation_stale_count > 0
        else runtime_diagnostics["state_reason"]
    )

    return {
        "project-scanner": {
            "title": "Project Scanner",
            "group_id": "diagnosis-control",
            "state": project_scanner_state,
            "state_label": runtime_diagnostics["state_label"],
            "state_reason": documentation_state_reason,
            "summary": "워크스페이스 구조와 최신 self-run 실행 근거를 함께 진단합니다.",
            "metric": f"핵심파일 {len(project_scan['entrypoints'])}개 · 실행범위 {runtime_diagnostics['scope_label']}",
            "detail": runtime_diagnostics["summary"],
            "attention_required": runtime_diagnostics["attention_required"],
            "staleness_label": runtime_diagnostics["staleness_label"],
            "last_run_started_at": runtime_diagnostics.get("last_run_started_at"),
            "last_run_finished_at": runtime_diagnostics.get("last_run_finished_at"),
            "last_run_age_hours": runtime_diagnostics.get("last_run_age_hours"),
            "payload": project_scan,
        },
        "dependency-graph": {
            "title": "Dependency Graph",
            "group_id": "diagnosis-control",
            "state": "active" if dependency_graph["integration_points"] else "warning",
            "summary": "프런트/백엔드/도커 의존과 연결 지점을 묶어 보여줍니다.",
            "metric": f"연결점 {len(dependency_graph['integration_points'])}개",
            "detail": f"서비스 {len(dependency_graph['compose_services'])}개",
            "payload": dependency_graph,
        },
        "security-guard": {
            "title": "Security Guard",
            "group_id": "diagnosis-control",
            "state": "error" if security_guard["missing_auth_files"] else ("warning" if security_guard["findings"] else "active"),
            "summary": "관리자 인증 경계와 런타임 보안 신호를 요약합니다.",
            "metric": f"Python 보안 오류 {security_guard['python_policy']['error_count']}건 · 경고 {security_guard['python_policy']['warning_count']}건",
            "detail": "관리자 인증/SECRET_KEY/런타임 설정 + python_security_validation",
            "payload": security_guard,
        },
        "self-healing-engine": {
            "title": "Self-Healing Engine",
            "group_id": "improvement-control",
            "state": self_healing_state,
            "state_label": runtime_diagnostics["state_label"],
            "state_reason": runtime_diagnostics["state_reason"],
            "summary": "최신 실행 실패 원인을 복구 순서로 재구성합니다.",
            "metric": f"복구안 {len(self_healing['actions'])}건",
            "detail": f"회복 점수 {self_healing['recovery_score']} · 최신상태 {runtime_diagnostics['latest_status']}",
            "attention_required": runtime_diagnostics["attention_required"],
            "staleness_label": runtime_diagnostics["staleness_label"],
            "last_run_started_at": runtime_diagnostics.get("last_run_started_at"),
            "last_run_finished_at": runtime_diagnostics.get("last_run_finished_at"),
            "last_run_age_hours": runtime_diagnostics.get("last_run_age_hours"),
            "payload": self_healing,
        },
        "code-generator": {
            "title": "Code Generator",
            "group_id": "improvement-control",
            "state": code_generator_state,
            "state_label": runtime_diagnostics["state_label"],
            "state_reason": documentation_state_reason,
            "summary": "경고 원인에 맞춰 재생성해야 할 코드/구조 개선 경로와 문서 stale 반영 필요 상태를 제안합니다.",
            "metric": (
                f"생성로그 {runtime_diagnostics['written_file_count']}개 / 최소 {runtime_diagnostics['minimums']['files']}개"
            ),
            "detail": (
                f"폴더 {runtime_diagnostics['changed_dir_count']}개 / 최소 {runtime_diagnostics['minimums']['dirs']}개 · Python 오류 {security_guard['python_policy']['error_count']}건"
            ),
            "attention_required": runtime_diagnostics["attention_required"],
            "staleness_label": runtime_diagnostics["staleness_label"],
            "last_run_started_at": runtime_diagnostics.get("last_run_started_at"),
            "last_run_finished_at": runtime_diagnostics.get("last_run_finished_at"),
            "last_run_age_hours": runtime_diagnostics.get("last_run_age_hours"),
            "payload": code_generator,
        },
        "admin-command-interface": {
            "title": "Admin Command Interface",
            "group_id": "expansion-control",
            "state": "active",
            "summary": "관리자 제어 명령과 연결 API를 한 묶음으로 보여줍니다.",
            "metric": f"명령 {admin_command_interface['count']}개",
            "detail": "실행/설정/진단 경로 정리",
            "payload": admin_command_interface,
        },
        "ollama-model-controller": {
            "title": "Ollama Model Controller",
            "group_id": "expansion-control",
            "state": "warning" if model_control["missing_configured_models"] or not model_control["available_models"] else "active",
            "summary": "현재 모델 라우트, GPU 상태, 권장 프로필을 구조화해 제공합니다.",
            "metric": f"모델 {len(model_control['available_models'])}개",
            "detail": f"프로필 {len(model_control['recommended_profiles'])}개",
            "payload": model_control,
        },
    }


def _build_sections(capability_id: str, capability: Dict[str, Any]) -> List[CapabilitySection]:
    payload = capability["payload"]
    if capability_id == "project-scanner":
        runtime_diagnostics = payload["runtime_diagnostics"]
        documentation_sync = payload.get("documentation_sync") or {}
        return [
            CapabilitySection(
                id="workspace-overview",
                title="워크스페이스 개요",
                items=[
                    CapabilitySectionItem(label="루트", value=payload["workspace_root"]),
                    CapabilitySectionItem(label="상위 디렉터리 수", value=len(payload["top_level_directories"])),
                    CapabilitySectionItem(label="스캔 디렉터리 수", value=payload["counts"]["directories"]),
                    CapabilitySectionItem(label="스캔 파일 수", value=payload["counts"]["files"]),
                ],
            ),
            CapabilitySection(
                id="latest-runtime-evidence",
                title="최신 self-run 근거",
                items=[
                    CapabilitySectionItem(label="실행 범위", value=runtime_diagnostics["scope_label"]),
                    CapabilitySectionItem(label="최신 상태", value=runtime_diagnostics["latest_status"]),
                    CapabilitySectionItem(label="기록 파일", value=runtime_diagnostics["record_path"] or "없음"),
                    CapabilitySectionItem(label="원본 경로", value=runtime_diagnostics.get("source_path") or "없음"),
                    CapabilitySectionItem(label="worker pid", value=runtime_diagnostics.get("worker_pid") or "없음"),
                    CapabilitySectionItem(label="worker alive", value=runtime_diagnostics.get("worker_alive") if runtime_diagnostics.get("worker_alive") is not None else "미기록"),
                    CapabilitySectionItem(label="worker 로그", value=runtime_diagnostics.get("worker_log_path") or "없음"),
                    CapabilitySectionItem(label="worker 진단", value=runtime_diagnostics.get("runtime_diagnostic") or "없음"),
                    CapabilitySectionItem(label="생성 로그 파일 수", value=runtime_diagnostics["written_file_count"]),
                    CapabilitySectionItem(label="변경 디렉터리 수", value=runtime_diagnostics["changed_dir_count"]),
                ],
            ),
            CapabilitySection(
                id="entrypoints",
                title="핵심 진입점",
                items=[
                    CapabilitySectionItem(
                        label=entrypoint["label"],
                        value="존재" if entrypoint["exists"] else "누락",
                        note=entrypoint["path"],
                    )
                    for entrypoint in payload["entrypoints"]
                ],
            ),
            CapabilitySection(
                id="risk-items",
                title="감지 리스크",
                items=[
                    CapabilitySectionItem(label=f"리스크 {index + 1}", value=item)
                    for index, item in enumerate(payload["risk_items"] or ["감지된 직접 리스크 없음"])
                ],
            ),
            CapabilitySection(
                id="runtime-findings",
                title="실행 근거 기반 경고",
                items=[
                    CapabilitySectionItem(
                        label=finding.get("category", f"runtime-{index + 1}"),
                        value=finding.get("message", "-"),
                        note=str(finding.get("details") or "") or None,
                    )
                    for index, finding in enumerate(runtime_diagnostics["findings"])
                ],
            ),
            CapabilitySection(
                id="documentation-sync",
                title="문서 stale 동기화",
                items=[
                    CapabilitySectionItem(label="overall_status", value=documentation_sync.get("overall_status") or "unknown"),
                    CapabilitySectionItem(label="stale_count", value=documentation_sync.get("stale_count") or 0),
                    CapabilitySectionItem(label="latest verification round", value=(documentation_sync.get("latest_verification_record") or {}).get("round") or "없음"),
                    CapabilitySectionItem(label="latest verification result", value=(documentation_sync.get("latest_verification_record") or {}).get("result") or "없음"),
                ] + [
                    CapabilitySectionItem(
                        label=f"{str(item.get('axis_label') or item.get('axis') or 'axis')}",
                        value="stale" if not bool(item.get("ok")) else "synced",
                        note=f"count={item.get('stale_count') or 0}",
                    )
                    for item in (documentation_sync.get("axes") or {}).values()
                    if isinstance(item, dict)
                ],
            ),
        ]

    if capability_id == "dependency-graph":
        package_items: List[CapabilitySectionItem] = []
        for package_info in payload["packages"]:
            package_items.append(
                CapabilitySectionItem(
                    label=package_info["path"],
                    value=f"deps {len(package_info['dependencies'])} / dev {len(package_info['dev_dependencies'])}",
                    note=", ".join(package_info["scripts"][:6]) or "script 없음",
                )
            )
        return [
            CapabilitySection(
                id="packages",
                title="패키지 의존",
                items=package_items,
            ),
            CapabilitySection(
                id="backend-dependencies",
                title="백엔드 requirements",
                items=[
                    CapabilitySectionItem(
                        label=payload["backend"]["path"],
                        value=f"패키지 {len(payload['backend']['requirements'])}개",
                        note=", ".join(payload["backend"]["requirements"][:8]) or "requirements 없음",
                    )
                ],
            ),
            CapabilitySection(
                id="integration-points",
                title="연결 지점",
                items=[
                    CapabilitySectionItem(
                        label=f"{item['source']} → {item['target']}",
                        value=item["kind"],
                        note=item["note"],
                    )
                    for item in payload["integration_points"]
                ],
            ),
        ]

    if capability_id == "security-guard":
        python_policy = payload["python_policy"]
        return [
            CapabilitySection(
                id="auth-boundary",
                title="관리자 인증 경계",
                items=[
                    CapabilitySectionItem(
                        label=path,
                        value="존재" if path not in payload["missing_auth_files"] else "누락",
                    )
                    for path in payload["auth_files"]
                ],
            ),
            CapabilitySection(
                id="python-security-validation",
                title="Python Security Validation",
                items=[
                    CapabilitySectionItem(label="오류", value=python_policy["error_count"]),
                    CapabilitySectionItem(label="경고", value=python_policy["warning_count"]),
                    CapabilitySectionItem(label="검사 파일 수", value=python_policy["files_total"]),
                ] + [
                    CapabilitySectionItem(
                        label=str(finding.get("rule_id") or f"policy-{index + 1}"),
                        value=f"{finding.get('path')}:{finding.get('line')}",
                        note=str(finding.get("evidence") or ""),
                    )
                    for index, finding in enumerate((python_policy.get("findings") or [])[:8])
                ],
            ),
            CapabilitySection(
                id="security-findings",
                title="보안 점검 결과",
                items=[
                    CapabilitySectionItem(
                        label=finding.get("severity", "info"),
                        value=finding.get("message", "-"),
                        note=", ".join(finding.get("details", [])) if isinstance(finding.get("details"), list) else None,
                    )
                    for finding in payload["findings"]
                ] or [CapabilitySectionItem(label="상태", value="직접 경고 없음")],
            ),
        ]

    if capability_id == "self-healing-engine":
        runtime_diagnostics = payload["runtime_diagnostics"]
        return [
            CapabilitySection(
                id="recovery-summary",
                title="복구 우선순위",
                items=[
                    CapabilitySectionItem(label="회복 점수", value=payload["recovery_score"]),
                ] + [
                    CapabilitySectionItem(label=f"복구안 {index + 1}", value=item)
                    for index, item in enumerate(payload["actions"])
                ],
            ),
            CapabilitySection(
                id="root-cause-chain",
                title="최근 실패 원인 체인",
                items=[
                    CapabilitySectionItem(
                        label=finding.get("category", f"원인 {index + 1}"),
                        value=finding.get("message", "-"),
                        note=str(finding.get("details") or "") or None,
                    )
                    for index, finding in enumerate(runtime_diagnostics["findings"])
                ],
            ),
            CapabilitySection(
                id="worker-runtime",
                title="worker 런타임 근거",
                items=[
                    CapabilitySectionItem(label="worker pid", value=runtime_diagnostics.get("worker_pid") or "없음"),
                    CapabilitySectionItem(label="worker alive", value=runtime_diagnostics.get("worker_alive") if runtime_diagnostics.get("worker_alive") is not None else "미기록"),
                    CapabilitySectionItem(label="실행 경과 초", value=runtime_diagnostics.get("running_seconds") or 0),
                    CapabilitySectionItem(label="worker 로그 경로", value=runtime_diagnostics.get("worker_log_path") or "없음"),
                    CapabilitySectionItem(label="worker 진단", value=runtime_diagnostics.get("runtime_diagnostic") or "없음", note=runtime_diagnostics.get("worker_log_excerpt") or None),
                ],
            ),
        ]

    if capability_id == "code-generator":
        runtime_diagnostics = payload["runtime_diagnostics"]
        source_inspection = runtime_diagnostics["source_inspection"]
        python_policy = payload.get("python_policy") or {}
        documentation_sync = payload.get("documentation_sync") or {}
        return [
            CapabilitySection(
                id="normalization-route",
                title="정상화 우선순위",
                items=[
                    CapabilitySectionItem(label="runtime 실패 건수", value=len(runtime_diagnostics["findings"])),
                    CapabilitySectionItem(label="python 보안 오류", value=python_policy.get("error_count", 0)),
                    CapabilitySectionItem(label="python 보안 경고", value=python_policy.get("warning_count", 0)),
                ] + [
                    CapabilitySectionItem(label=f"우선순위 {index + 1}", value=item)
                    for index, item in enumerate(payload["implementation_order"])
                ],
            ),
            CapabilitySection(
                id="generator-targets",
                title="수정 대상 후보",
                items=[
                    CapabilitySectionItem(label=f"대상 {index + 1}", value=item)
                    for index, item in enumerate(payload["suggested_targets"])
                ],
            ),
            CapabilitySection(
                id="implementation-order",
                title="구현 순서",
                items=[
                    CapabilitySectionItem(label=f"순서 {index + 1}", value=item)
                    for index, item in enumerate(payload["implementation_order"])
                ],
            ),
            CapabilitySection(
                id="generation-thresholds",
                title="산출물 기준 진단",
                items=[
                    CapabilitySectionItem(label="생성 로그 파일 수", value=runtime_diagnostics["written_file_count"], note=f"최소 {runtime_diagnostics['minimums']['files']}개"),
                    CapabilitySectionItem(label="변경 디렉터리 수", value=runtime_diagnostics["changed_dir_count"], note=f"최소 {runtime_diagnostics['minimums']['dirs']}개"),
                    CapabilitySectionItem(label="소스 파일 수", value=source_inspection["source_file_count"], note=f"평균 {source_inspection['average_source_size']} bytes"),
                    CapabilitySectionItem(label="1KB 이하 소스 수", value=source_inspection["tiny_source_count"], note=", ".join(source_inspection["tiny_sources"][:6]) or None),
                ],
            ),
            CapabilitySection(
                id="runtime-worker-diagnostics",
                title="실행 worker 진단",
                items=[
                    CapabilitySectionItem(label="worker pid", value=runtime_diagnostics.get("worker_pid") or "없음"),
                    CapabilitySectionItem(label="worker alive", value=runtime_diagnostics.get("worker_alive") if runtime_diagnostics.get("worker_alive") is not None else "미기록"),
                    CapabilitySectionItem(label="worker 로그 경로", value=runtime_diagnostics.get("worker_log_path") or "없음"),
                    CapabilitySectionItem(label="worker 진단", value=runtime_diagnostics.get("runtime_diagnostic") or "없음", note=runtime_diagnostics.get("worker_log_excerpt") or None),
                ],
            ),
            CapabilitySection(
                id="documentation-sync",
                title="문서 stale 동기화",
                items=[
                    CapabilitySectionItem(label="overall_status", value=documentation_sync.get("overall_status") or "unknown"),
                    CapabilitySectionItem(label="stale_count", value=documentation_sync.get("stale_count") or 0),
                    CapabilitySectionItem(label="latest verification round", value=(documentation_sync.get("latest_verification_record") or {}).get("round") or "없음"),
                    CapabilitySectionItem(label="latest verification result", value=(documentation_sync.get("latest_verification_record") or {}).get("result") or "없음"),
                ] + [
                    CapabilitySectionItem(
                        label=str(match.get("axis_label") or match.get("axis") or f"stale-{index + 1}"),
                        value=str(match.get("path") or "문서 경로 없음"),
                        note=str(match.get("match") or match.get("note") or ""),
                    )
                    for index, match in enumerate((documentation_sync.get("stale_matches") or [])[:6])
                    if isinstance(match, dict)
                ],
            ),
        ]

    if capability_id == "admin-command-interface":
        return [
            CapabilitySection(
                id="command-map",
                title="관리자 명령 맵",
                items=[
                    CapabilitySectionItem(
                        label=item["title"],
                        value=item["endpoint"],
                        note=item["usage"],
                    )
                    for item in payload["commands"]
                ],
            ),
        ]

    if capability_id == "ollama-model-controller":
        gpu_devices = payload["gpu_runtime"].get("devices", [])
        configured_routes = payload["configured_routes"]
        return [
            CapabilitySection(
                id="runtime-overview",
                title="런타임 개요",
                items=[
                    CapabilitySectionItem(label="런타임 설정 파일", value=payload["runtime_config_path"]),
                    CapabilitySectionItem(label="사용 가능 모델 수", value=len(payload["available_models"])),
                    CapabilitySectionItem(label="누락 라우트 모델 수", value=len(payload["missing_configured_models"])),
                ],
            ),
            CapabilitySection(
                id="configured-routes",
                title="현재 역할별 모델",
                items=[
                    CapabilitySectionItem(label=route_key, value=model_name)
                    for route_key, model_name in configured_routes.items()
                ],
            ),
            CapabilitySection(
                id="gpu-runtime",
                title="GPU 상태",
                items=[
                    CapabilitySectionItem(
                        label=device.get("name", f"GPU {index + 1}"),
                        value=f"{device.get('memory_used_mb', 0)} / {device.get('memory_total_mb', 0)} MB",
                        note=f"util {device.get('utilization_gpu', 0)}%",
                    )
                    for index, device in enumerate(gpu_devices)
                ] or [CapabilitySectionItem(label="GPU", value="감지되지 않음")],
            ),
        ]

    return [CapabilitySection(id="empty", title="상세", items=[CapabilitySectionItem(label="상태", value="지원되지 않는 기능")])]


def _build_highlights(capability_id: str, capability: Dict[str, Any]) -> List[str]:
    payload = capability["payload"]
    if capability_id == "project-scanner":
        runtime_diagnostics = payload["runtime_diagnostics"]
        documentation_sync = payload.get("documentation_sync") or {}
        return [
            f"상위 디렉터리 {len(payload['top_level_directories'])}개를 확인했습니다.",
            f"핵심 진입점 누락은 {len(payload['missing_expected'])}건입니다.",
            f"최신 실행 범위는 {runtime_diagnostics['scope_label']}입니다.",
            f"문서 stale 감지는 {documentation_sync.get('stale_count', 0)}건입니다.",
        ]
    if capability_id == "dependency-graph":
        return [
            f"관리 연결 지점 {len(payload['integration_points'])}개를 추적했습니다.",
            f"도커 서비스 {len(payload['compose_services'])}개를 감지했습니다.",
        ]
    if capability_id == "security-guard":
        return [
            f"보안 점검 신호 {len(payload['findings'])}건을 구조화했습니다.",
            f"python_security_validation 오류 {payload['python_policy']['error_count']}건, 경고 {payload['python_policy']['warning_count']}건입니다.",
            f"관리자 인증 핵심 파일 누락 {len(payload['missing_auth_files'])}건입니다.",
        ]
    if capability_id == "self-healing-engine":
        runtime_diagnostics = payload["runtime_diagnostics"]
        return [
            f"즉시 복구안 {len(payload['actions'])}건을 제안했습니다.",
            f"현재 회복 점수는 {payload['recovery_score']}점입니다.",
            f"최신 실행 근거에서 직접 감지된 원인은 {len(runtime_diagnostics['findings'])}건입니다.",
        ]
    if capability_id == "code-generator":
        runtime_diagnostics = payload["runtime_diagnostics"]
        documentation_sync = payload.get("documentation_sync") or {}
        return [
            f"수정 대상 후보 {len(payload['suggested_targets'])}개를 정리했습니다.",
            f"구현 순서 {len(payload['implementation_order'])}단계를 제안했습니다.",
            f"python_security_validation 오류 {payload['python_policy']['error_count']}건과 latest self-run 원인 {len(runtime_diagnostics['findings'])}건을 병합했습니다.",
            (
                f"생성 로그 파일 {runtime_diagnostics['written_file_count']}개, "
                f"변경 디렉터리 {runtime_diagnostics['changed_dir_count']}개를 최근 실행 근거로 확인했습니다."
            ),
            f"README/상태 문서 stale 감지는 {documentation_sync.get('stale_count', 0)}건입니다.",
        ]
    if capability_id == "admin-command-interface":
        return [
            f"관리 명령 {payload['count']}개를 연결했습니다.",
            "설정/실행/진단 경로를 한 곳에서 추적할 수 있습니다.",
        ]
    if capability_id == "ollama-model-controller":
        return [
            f"사용 가능한 Ollama 모델 {len(payload['available_models'])}개를 확인했습니다.",
            f"권장 프로필 {len(payload['recommended_profiles'])}개를 제공합니다.",
        ]
    return []


def _build_route_manifest_validation() -> Dict[str, Any]:
    required_routes = [
        "/api/admin/system-settings",
        "/api/admin/workspace-self-run-record",
        "/api/admin/orchestrator/capabilities/summary",
        "/api/admin/orchestrator/capabilities/code-generator",
        "/api/llm/ws",
    ]

    def _extract_router_prefix(source_text: str) -> str:
        prefix_match = re.search(r'APIRouter\(prefix\s*=\s*["\']([^"\']+)["\']', source_text)
        return str(prefix_match.group(1) if prefix_match else "").strip()

    def _extract_declared_routes(source_text: str) -> List[str]:
        prefix = _extract_router_prefix(source_text)
        route_patterns = re.findall(
            r'@router\.(?:get|post|put|patch|delete|websocket)\(\s*["\']([^"\']+)["\']',
            source_text,
        )
        declared_routes: List[str] = []
        for route_pattern in route_patterns:
            normalized_prefix = str(prefix or "").rstrip("/")
            normalized_route = str(route_pattern or "").strip()
            if not normalized_route.startswith("/"):
                normalized_route = "/" + normalized_route
            declared_routes.append(f"{normalized_prefix}{normalized_route}" if normalized_prefix else normalized_route)
        return declared_routes

    def _route_pattern_matches(required_route: str, declared_route: str) -> bool:
        if required_route == declared_route:
            return True
        route_regex_parts = [r"[^/]+" if segment.startswith("{") and segment.endswith("}") else re.escape(segment) for segment in str(declared_route or "").split("/")]
        route_regex = "/".join(route_regex_parts)
        return re.fullmatch(route_regex, required_route) is not None

    route_source_paths = [
        REPO_ROOT / "backend" / "admin_router.py",
        REPO_ROOT / "backend" / "llm" / "admin_capabilities.py",
        REPO_ROOT / "backend" / "llm" / "orchestrator.py",
    ]
    missing_routes: List[str] = []
    existing_routes: List[str] = []
    declared_routes: List[str] = []
    source_files: List[str] = []
    for source_path in route_source_paths:
        if not source_path.exists():
            continue
        source_text = _safe_read_text(source_path)
        declared_routes.extend(_extract_declared_routes(source_text))
        source_files.append(str(source_path.relative_to(REPO_ROOT)).replace("\\", "/"))
    declared_routes = list(dict.fromkeys(route for route in declared_routes if route))
    declared_route_count = len(declared_routes)
    for route in required_routes:
        if any(_route_pattern_matches(route, declared_route) for declared_route in declared_routes):
            existing_routes.append(route)
        else:
            missing_routes.append(route)
    return {
        "ok": len(missing_routes) == 0,
        "required_routes": required_routes,
        "existing_routes": existing_routes,
        "missing_routes": missing_routes,
        "declared_routes": declared_routes,
        "declared_route_count": declared_route_count,
        "route_manifest_source": source_files,
        "validation_summary": (
            f"required={len(required_routes)}, existing={len(existing_routes)}, "
            f"missing={len(missing_routes)}, declared_route_count={declared_route_count}"
        ),
        "result_status": "pass" if len(missing_routes) == 0 else "blocked",
        "record_scope_id": "phase-f-admin-route-recovery",
        "source_path": "backend/admin_router.py",
    }


def _build_nginx_target_validation() -> Dict[str, Any]:
    nginx_path = REPO_ROOT / "nginx" / "nginx.conf" / "nginx.conf"
    source_text = _safe_read_text(nginx_path) if nginx_path.exists() else ""
    required_snippets = {
        "admin_system_settings_backend": "/api/admin/system-settings",
        "api_proxy_backend": "location /api/",
        "websocket_proxy": "/api/llm/ws",
    }
    matched_targets = {
        key: marker
        for key, marker in required_snippets.items()
        if marker in source_text
    }
    source_path = "nginx/nginx.conf/nginx.conf"
    missing_targets = [key for key in required_snippets if key not in matched_targets]
    return {
        "ok": len(missing_targets) == 0,
        "required_targets": required_snippets,
        "matched_targets": matched_targets,
        "missing_targets": missing_targets,
        "validation_summary": (
            f"required={len(required_snippets)}, matched={len(matched_targets)}, missing={len(missing_targets)}"
        ),
        "result_status": "pass" if len(missing_targets) == 0 else "blocked",
        "record_scope_id": "phase-f-system-settings-504-recurrence",
        "source_path": source_path,
    }


def _build_cached_path_validation() -> Dict[str, Any]:
    loader_path = REPO_ROOT / "backend" / "llm" / "loader.py"
    admin_router_path = REPO_ROOT / "backend" / "admin_router.py"
    loader_text = _safe_read_text(loader_path) if loader_path.exists() else ""
    admin_router_text = _safe_read_text(admin_router_path) if admin_router_path.exists() else ""
    checks = {
        "loader_cached_status": "def get_cached_status" in loader_text,
        "system_settings_cached_path": "get_cached_status()" in admin_router_text,
    }
    missing_checks = [key for key, ok in checks.items() if not ok]
    return {
        "ok": len(missing_checks) == 0,
        "checks": checks,
        "missing_checks": missing_checks,
        "validation_summary": (
            f"checks={len(checks)}, passed={len(checks) - len(missing_checks)}, missing={len(missing_checks)}"
        ),
        "result_status": "pass" if len(missing_checks) == 0 else "blocked",
        "record_scope_id": "phase-f-system-settings-504-recurrence",
        "source_paths": ["backend/llm/loader.py", "backend/admin_router.py"],
    }


def _build_actions(capability_id: str, capability: Dict[str, Any]) -> List[str]:
    payload = capability["payload"]
    if capability_id == "project-scanner":
        runtime_actions = payload["runtime_diagnostics"]["actions"]
        documentation_sync = payload.get("documentation_sync") or {}
        documentation_actions = [
            "README, 상태 문서, readiness artifact 문구를 latest verification record 기준으로 다시 동기화하세요."
        ] if int(documentation_sync.get("stale_count") or 0) > 0 else []
        return documentation_actions + runtime_actions + (payload["risk_items"] or ["현재 스캔 기준 즉시 조치 리스크는 없습니다."])
    if capability_id == "dependency-graph":
        return [
            "연결 지점 변경 전 프록시, API, iframe 결합부를 함께 점검하세요.",
            "패키지 스크립트와 backend requirements 변경 시 빌드/부팅 검증을 같이 수행하세요.",
        ]
    if capability_id == "security-guard":
        if payload["findings"]:
            actions = [finding.get("message", "") for finding in payload["findings"]]
            python_policy = payload.get("python_policy") or {}
            if python_policy.get("error_count"):
                actions.insert(0, f"python_security_validation 오류 {python_policy['error_count']}건을 우선 수정하세요.")
            return list(dict.fromkeys(actions))
        return ["직접 보안 경고는 없지만 관리자 인증 경계 검증은 유지하세요."]
    if capability_id == "self-healing-engine":
        return payload["actions"] or ["즉시 복구가 필요한 차단 요소가 감지되지 않았습니다."]
    if capability_id == "code-generator":
        python_policy = payload.get("python_policy") or {}
        documentation_sync = payload.get("documentation_sync") or {}
        actions = payload["runtime_diagnostics"]["actions"] + payload["implementation_order"]
        if int(documentation_sync.get("stale_count") or 0) > 0:
            actions.insert(0, "문서 stale 감지 결과를 먼저 반영해 README/상태 문서/validation artifact를 최신 실검증 기준으로 동기화하세요.")
        if python_policy.get("error_count"):
            actions.insert(0, f"python_security_validation 오류 {python_policy['error_count']}건이 남아 있어 코드 자동생성 작업문에 먼저 포함해야 합니다.")
        return list(dict.fromkeys(actions))
    if capability_id == "admin-command-interface":
        return [item["endpoint"] for item in payload["commands"]]
    if capability_id == "ollama-model-controller":
        actions = []
        if payload["missing_configured_models"]:
            actions.append("라우트에 지정된 모델과 실제 Ollama 태그 목록을 일치시키세요.")
        if not payload["gpu_runtime"].get("available"):
            actions.append("GPU 런타임이 감지되지 않아 성능 하락 가능성이 있습니다.")
        actions.append("권장 프로필과 현재 실행 컨트롤 차이를 비교해 운영 프로필을 정리하세요.")
        return actions
    return []


def _build_capability_card(capability_id: str, capability: Dict[str, Any]) -> CapabilityCard:
    evidence_context = capability.get("evidence_context") if isinstance(capability.get("evidence_context"), dict) else _build_capability_evidence_context(capability)
    return CapabilityCard(
        id=capability_id,
        title=capability["title"],
        group_id=capability["group_id"],
        state=capability["state"],
        state_label=capability.get("state_label"),
        state_reason=capability.get("state_reason"),
        summary=capability["summary"],
        metric=capability["metric"],
        detail=capability.get("detail"),
        attention_required=bool(capability.get("attention_required")),
        staleness_label=capability.get("staleness_label"),
        last_run_started_at=capability.get("last_run_started_at"),
        last_run_finished_at=capability.get("last_run_finished_at"),
        last_run_age_hours=capability.get("last_run_age_hours"),
        evidence_digest=evidence_context["evidence_digest"],
    )


def _build_group_summaries(capability_map: Dict[str, Dict[str, Any]]) -> List[CapabilityGroupSummary]:
    group_rows: List[CapabilityGroupSummary] = []
    for group_id, title in CAPABILITY_GROUP_LABELS.items():
        group_capabilities = [
            capability_map[capability_id]
            for capability_id in CAPABILITY_ORDER
            if capability_map[capability_id]["group_id"] == group_id
        ]
        error_count = sum(1 for item in group_capabilities if item["state"] == "error")
        warning_count = sum(1 for item in group_capabilities if item["state"] == "warning")
        active_count = sum(1 for item in group_capabilities if item["state"] == "active")
        standby_count = sum(1 for item in group_capabilities if item["state"] == "standby")
        state = (
            "error"
            if error_count
            else ("warning" if warning_count else ("active" if active_count else "standby"))
        )
        summary = (
            f"정상 {active_count} · 대기 {standby_count} · 주의 {warning_count} · 오류 {error_count}"
            if group_capabilities
            else "연결된 기능 없음"
        )
        group_rows.append(
            CapabilityGroupSummary(
                id=group_id,
                title=title,
                state=state,
                summary=summary,
                active_count=active_count,
                standby_count=standby_count,
                warning_count=warning_count,
                error_count=error_count,
            )
        )
    return group_rows


@router.get("/capabilities/summary", response_model=CapabilitySummaryResponse)
def get_capability_summary(_: User = Depends(require_admin)):
    capability_map = _attach_capability_evidence_context(_build_capability_map())
    return CapabilitySummaryResponse(
        generated_at=_now_iso(),
        groups=_build_group_summaries(capability_map),
        capabilities=[
            _build_capability_card(capability_id, capability_map[capability_id])
            for capability_id in CAPABILITY_ORDER
        ],
    )


@router.get("/capabilities/{capability_id}", response_model=CapabilityDetailResponse)
def get_capability_detail(capability_id: str, _: User = Depends(require_admin)):
    capability_map = _attach_capability_evidence_context(_build_capability_map())
    capability = capability_map.get(capability_id)
    if capability is None:
        raise HTTPException(status_code=404, detail="지원되지 않는 능력입니다")
    evidence_context = capability.get("evidence_context") if isinstance(capability.get("evidence_context"), dict) else _build_capability_evidence_context(capability)
    sections = _build_sections(capability_id, capability)
    return CapabilityDetailResponse(
        generated_at=_now_iso(),
        debug_signature=f"{capability_id}:{evidence_context['evidence_bundle'].get('execution', {}).get('evidence_run_id') or 'no-run'}",
        sections_count=len(sections),
        capability=_build_capability_card(capability_id, capability),
        highlights=_build_highlights(capability_id, capability),
        suggested_actions=_build_actions(capability_id, capability),
        sections=sections,
        evidence_bundle=evidence_context["evidence_bundle"],
        target_file_ids=evidence_context["target_file_ids"],
        target_section_ids=evidence_context["target_section_ids"],
        target_feature_ids=evidence_context["target_feature_ids"],
        target_chunk_ids=evidence_context["target_chunk_ids"],
        failure_tags=evidence_context["failure_tags"],
        repair_tags=evidence_context["repair_tags"],
        target_patch_entries=evidence_context["target_patch_entries"],
        validation_findings=_build_validation_findings(capability_id, capability),
        improvement_code_examples=_build_improvement_code_examples(capability_id, capability),
    )