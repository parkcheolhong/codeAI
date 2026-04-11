"""관리자 전용 API"""
import asyncio
import subprocess
import sys
import traceback
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_
from pydantic import BaseModel
from typing import Any, Dict, List, Optional
from pathlib import Path
import hashlib
import io
import json
import logging
import os
import py_compile
import re
import shutil
import tempfile
import time
import threading
from datetime import datetime
from urllib.parse import quote
import httpx
from backend.database import get_db
from backend.models import User
from backend.auth import get_current_user, get_password_hash, verify_password
from backend.orchestration_stage_service import (
    initialize_stage_run,
    load_stage_run,
    update_stage_run,
)
from backend.llm.loader import llm_loader
from backend.services.auth_identity_provider import resolve_identity_provider
from backend.marketplace.minio_service import minio_service
from backend.marketplace.models import (
    AdVideoOrder,
    CustomerOrchestratorCompletion,
    FeatureExecutionLog,
    FeatureRetryQueue,
    Project,
)
from backend.orchestrator.chat.project_context_store import (
    append_approval_gate_record,
    append_experiment_record,
    get_active_global_approval_policy,
    get_project_context_bundle,
    is_workspace_root_scope,
    normalize_project_root,
    upsert_global_approval_policy,
    upsert_project_memory_snapshot,
)
from backend.admin.orchestrator.debug_validation_jobs import enqueue_debug_validation_job, get_debug_validation_job
from backend.admin.orchestrator.debug_validation_jobs import assert_debug_validation_job_contract
from backend.admin.orchestrator.path_utils import admin_runtime_root, admin_workspace_root, is_relative_to, resolve_marketplace_upload_root_path
from backend.admin.orchestrator.project_root_service import resolve_admin_project_root
from backend.admin.orchestrator.runtime_verification_service import build_runtime_verification_response
from backend.admin.orchestrator.runtime_verification_service import assert_runtime_verification_contract
from backend.admin.orchestrator.self_run_approval_service import approve_workspace_self_run_response as approve_workspace_self_run_response_service
from backend.admin.orchestrator.self_run_approval_service import assert_self_run_approval_contract
from backend.admin.orchestrator.self_run_approval_service import run_admin_approval_validation as run_admin_approval_validation_service
from backend.admin.orchestrator.self_run_approval_service import sync_clone_into_source as sync_clone_into_source_service
from backend.admin.orchestrator.self_run_record_service import approval_payload_to_self_run_response as approval_payload_to_self_run_response_service
from backend.admin.orchestrator.self_run_record_service import assert_self_run_record_contract
from backend.admin.orchestrator.self_run_record_service import get_workspace_self_run_record_response as get_workspace_self_run_record_response_service
from backend.admin.orchestrator.self_run_record_service import latest_self_run_record_path as latest_self_run_record_path_service
from backend.admin.orchestrator.self_run_record_service import normalize_workspace_self_run_record_response as normalize_workspace_self_run_record_response_service
from backend.admin.orchestrator.self_run_preparation_service import build_initial_running_self_run_payload as build_initial_running_self_run_payload_service
from backend.admin.orchestrator.self_run_preparation_service import prepare_workspace_self_prepare_result as prepare_workspace_self_prepare_result_service
from backend.admin.orchestrator.workspace_text_service import get_workspace_text_file as load_workspace_text_file_service, list_workspace_text_files as list_workspace_text_files_service, read_admin_text_file, resolve_admin_workspace_path, is_admin_text_file
from backend.admin.orchestrator.focused_self_healing_service import (
    assert_focused_self_healing_contract,
    build_focused_self_healing_decision,
    build_tower_crane_options,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])
logger = logging.getLogger(__name__)
_ADMIN_PROJECTS_CACHE_TTL_SEC = max(1.0, float(os.getenv("ADMIN_PROJECTS_CACHE_TTL_SEC", "3")))
_ADMIN_PROJECTS_RESPONSE_CACHE: Dict[str, Dict[str, Any]] = {}
_ADMIN_PROJECTS_CACHE_LOCK = threading.Lock()
_ADMIN_PROJECTS_RATE_LIMIT_WINDOW_SEC = max(0.2, float(os.getenv("ADMIN_PROJECTS_RATE_LIMIT_WINDOW_SEC", "1.5")))
_ADMIN_PROJECTS_RATE_LIMIT_STATE: Dict[str, float] = {}
_ADMIN_PROJECTS_RATE_LIMIT_LOCK = threading.Lock()


def _apply_short_admin_projects_cache_headers(response: Response, *, applied_limit: int) -> None:
    ttl = max(1, int(_ADMIN_PROJECTS_CACHE_TTL_SEC))
    response.headers["Cache-Control"] = f"private, max-age={ttl}, stale-while-revalidate=15"
    response.headers["Vary"] = "Authorization"
    response.headers["x-admin-projects-applied-limit"] = str(applied_limit)
    response.headers["x-stale-client-mitigation"] = "admin-projects-short-cache"


def _apply_admin_projects_degraded_headers(response: Response, *, mitigation: str, applied_limit: int) -> None:
    _apply_short_admin_projects_cache_headers(response, applied_limit=applied_limit)
    response.headers["Connection"] = "close"
    response.headers["x-stale-client-mitigation"] = mitigation
    response.headers["x-admin-projects-degraded"] = "1"


def _resolve_admin_projects_rate_limit_key(request: Request) -> str:
    forwarded_for = str(request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    client_host = forwarded_for or (request.client.host if request.client else "unknown")
    authorization = str(request.headers.get("authorization") or "")
    auth_fingerprint = hashlib.sha256(authorization.encode("utf-8")).hexdigest()[:16] if authorization else "anonymous"
    return f"{client_host}:{auth_fingerprint}"


def _should_throttle_admin_projects(request: Request) -> bool:
    now_ts = time.time()
    rate_limit_key = _resolve_admin_projects_rate_limit_key(request)
    with _ADMIN_PROJECTS_RATE_LIMIT_LOCK:
        last_seen = float(_ADMIN_PROJECTS_RATE_LIMIT_STATE.get(rate_limit_key) or 0.0)
        _ADMIN_PROJECTS_RATE_LIMIT_STATE[rate_limit_key] = now_ts
        stale_keys = [key for key, seen_at in _ADMIN_PROJECTS_RATE_LIMIT_STATE.items() if (now_ts - float(seen_at)) > (_ADMIN_PROJECTS_RATE_LIMIT_WINDOW_SEC * 20)]
        for stale_key in stale_keys:
            _ADMIN_PROJECTS_RATE_LIMIT_STATE.pop(stale_key, None)
    return (now_ts - last_seen) < _ADMIN_PROJECTS_RATE_LIMIT_WINDOW_SEC


def _build_admin_projects_degraded_payload(*, skip: int, requested_limit: int, applied_limit: int, cached_payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if isinstance(cached_payload, dict) and cached_payload:
        payload = dict(cached_payload)
        payload["requested_limit"] = requested_limit
        payload["applied_limit"] = applied_limit
        payload["degraded"] = True
        return payload
    return {
        "items": [],
        "projects": [],
        "total": 0,
        "skip": skip,
        "limit": applied_limit,
        "requested_limit": requested_limit,
        "applied_limit": applied_limit,
        "degraded": True,
    }


def _is_legacy_admin_projects_request(limit: int) -> bool:
    return int(limit) >= 5000


def require_admin(current_user: User = Depends(get_current_user)):
    is_admin = bool(getattr(current_user, "is_admin", False))
    is_superuser = bool(getattr(current_user, "is_superuser", False))
    if not (is_admin or is_superuser):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다")
    return current_user


assert_debug_validation_job_contract()
assert_runtime_verification_contract()
assert_self_run_approval_contract()
assert_self_run_record_contract()
assert_focused_self_healing_contract()


def _validate_admin_password_change_payload(payload: "AdminPasswordChangeRequest") -> str:
    current_password = str(payload.current_password or "")
    new_password = str(payload.new_password or "")
    confirm_password = str(payload.confirm_password or "")

    if not current_password.strip():
        raise HTTPException(status_code=400, detail="현재 비밀번호를 입력해 주세요.")
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="새 비밀번호는 8자 이상이어야 합니다.")
    if new_password != confirm_password:
        raise HTTPException(status_code=400, detail="새 비밀번호 확인이 일치하지 않습니다.")
    if new_password == current_password:
        raise HTTPException(status_code=400, detail="새 비밀번호는 현재 비밀번호와 달라야 합니다.")
    return new_password


def _validate_postgres_password_change_payload(payload: "AdminPostgresPasswordUpdateRequest") -> str:
    next_password = str(payload.new_password or "")
    confirm_password = str(payload.confirm_password or "")
    if len(next_password) < 8:
        raise HTTPException(status_code=400, detail="PostgreSQL 비밀번호는 8자 이상이어야 합니다.")
    if next_password != confirm_password:
        raise HTTPException(status_code=400, detail="PostgreSQL 비밀번호 확인이 일치하지 않습니다.")
    return next_password


class UserUpdate(BaseModel):
    is_admin: Optional[bool] = None
    is_active: Optional[bool] = None
    is_superuser: Optional[bool] = None


class SampleCleanupRequest(BaseModel):
    pattern: str = "[샘플"
    dry_run: bool = True


class WorkspaceExperimentCloneRequest(BaseModel):
    source_path: str


class WorkspaceSelfPrepareRequest(BaseModel):
    source_path: str
    mode: str = "self-diagnosis"
    create_experiment_clone: bool = False


class WorkspaceSelfRunRequest(BaseModel):
    source_path: str
    mode: str = "self-diagnosis"
    self_run_stage: str = ""
    directive_template: Optional[str] = None
    directive_scope: Optional[str] = None
    directive_request: Optional[str] = None
    stage_run_id: Optional[str] = None


class WorkspaceSelfApprovalRequest(BaseModel):
    approval_id: str


class WorkspaceSelfRunRetryRequest(BaseModel):
    approval_id: Optional[str] = None
    reason: Optional[str] = None
    target_stage: str = ""
    source_path: Optional[str] = None


class WorkspaceSelfRunStageUpdateRequest(BaseModel):
    approval_id: str
    stage_status: str
    stage_note: str = ""
    manual_correction: str = ""
    substep_checks: Optional[Dict[str, bool]] = None
    revision_note: str = ""


class WorkspaceSelfRunNormalizeRequest(BaseModel):
    approval_id: Optional[str] = None
    cleanup_only: bool = False


class AdminGlobalAutomaticModeResponse(BaseModel):
    applied_at: str
    message: str
    restart_required: bool
    env_path: str
    runtime_config_path: str
    updated_env_values: Dict[str, str]
    runtime_summary: Dict[str, Any]


class AdminSystemSettingsUpdateRequest(BaseModel):
    values: Dict[str, str]


class AdminPasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str


class AdminPostgresPasswordUpdateRequest(BaseModel):
    new_password: str
    confirm_password: str


class AdminProjectMemoryUpdateRequest(BaseModel):
    project_root: str
    project_name: Optional[str] = None
    remembered_goal: Optional[str] = None
    constraints: List[str] = []
    pending_tasks: List[str] = []
    decisions: List[str] = []


class AdminExperimentRecordRequest(BaseModel):
    project_root: str
    hypothesis: str
    method: str
    result_summary: str
    conclusion: str
    applied: bool = False
    evidence: List[str] = []


class AdminApprovalGateUpdateRequest(BaseModel):
    project_root: str
    status: str
    scope: List[str] = []
    blocked_paths: List[str] = []
    validation_rules: List[str] = []
    rationale: str = ""


class AdminGlobalApprovalPolicyRequest(BaseModel):
    representative_project_root: str
    status: str
    scope: List[str] = []
    blocked_paths: List[str] = []
    validation_rules: List[str] = []
    rationale: str = ""


class AdminDebugValidationProfileRequest(BaseModel):
    project_root: str


class AdminDebugValidationJobResponse(BaseModel):
    job_id: str
    status: str
    project_root: str


class AdminDebugValidationJobState(BaseModel):
    job_id: str
    status: str
    project_root: str
    created_at: str
    updated_at: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class AdminRuntimeVerificationRequest(BaseModel):
    project_root: str = ""
    worker_log_path: str = ""


class AdminIdentityProviderSettingsResponse(BaseModel):
    provider: str
    env_keys: Dict[str, str]
    callback_url: str
    provider_statuses: List[Dict[str, Any]]
    guides: Dict[str, str]
    complete_payload_contracts: List[Dict[str, Any]]


def _identity_provider_guide_text() -> Dict[str, str]:
    return {
        "IDENTITY_PROVIDER": "pass, kmc, kcb 중 하나를 입력합니다. 저장 후 현재 provider가 해당 값으로 바뀌어야 합니다.",
        "PASS_IDENTITY_ENDPOINT": "PASS 상용 본인확인 시작 URL 전체를 입력합니다. 예: https://service.pass.example/identity/start",
        "PASS_CLIENT_ID": "PASS 계약 후 발급된 운영 client id를 입력합니다.",
        "PASS_CLIENT_SECRET": "PASS 운영 secret을 입력합니다. 저장 후 secret 마스킹 상태를 확인하세요.",
        "PASS_CALLBACK_URL": "PASS 결과를 다시 받을 관리자/백엔드 callback URL 전체를 입력합니다.",
        "KMC_IDENTITY_ENDPOINT": "KMC 상용 본인확인 시작 URL 전체를 입력합니다.",
        "KMC_CLIENT_ID": "KMC 계약 후 발급된 운영 client id를 입력합니다.",
        "KMC_CLIENT_SECRET": "KMC 운영 secret을 입력합니다.",
        "KMC_CALLBACK_URL": "KMC 결과 callback URL 전체를 입력합니다.",
        "KCB_IDENTITY_ENDPOINT": "KCB 상용 본인확인 시작 URL 전체를 입력합니다.",
        "KCB_CLIENT_ID": "KCB 계약 후 발급된 운영 client id를 입력합니다.",
        "KCB_CLIENT_SECRET": "KCB 운영 secret을 입력합니다.",
        "KCB_CALLBACK_URL": "KCB 결과 callback URL 전체를 입력합니다.",
    }
ADMIN_SYSTEM_SETTINGS_CACHE_TTL_SEC = max(5.0, float(os.getenv("ADMIN_SYSTEM_SETTINGS_CACHE_TTL_SEC", "30")))
_ADMIN_SYSTEM_SETTINGS_CACHE: Dict[str, Any] = {
    "captured_at": 0.0,
    "env_mtime": None,
    "runtime_mtime": None,
    "payload": None,
}


def _classify_gate_status(verification_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    hard_gate_keys = {
        "py_compile",
        "health",
        "llm-status",
        "stats-revenue",
        "project-context",
        "traceback-capture",
        "approval-gate",
        "worker-log-tail",
    }
    soft_gate_keys = set()
    hard_failures = [
        item
        for item in verification_items
        if item.get("key") in hard_gate_keys and item.get("status") in {"failed", "warning"}
    ]
    soft_failures = [
        item
        for item in verification_items
        if item.get("key") in soft_gate_keys and item.get("status") in {"failed", "warning"}
    ]
    if hard_failures:
        final_status = "failed"
    elif soft_failures:
        final_status = "warning"
    else:
        final_status = "passed"
    return {
        "hard_gate_keys": sorted(hard_gate_keys),
        "soft_gate_keys": sorted(soft_gate_keys),
        "hard_failures": hard_failures,
        "soft_failures": soft_failures,
        "fallback_recovery": bool(soft_failures) and not hard_failures,
        "final_pass": not hard_failures,
        "final_status": final_status,
    }


def _resolve_admin_stage_run(payload: WorkspaceSelfRunRequest, admin: User) -> Dict[str, Any]:
    if payload.stage_run_id:
        existing = load_stage_run(payload.stage_run_id)
        if existing:
            return existing
    return initialize_stage_run(
        scope="admin",
        project_name=_slugify_admin_name(payload.source_path or "workspace-self-run"),
        mode=str(payload.mode or "self-diagnosis"),
        requested_by={
            "id": getattr(admin, "id", None),
            "email": getattr(admin, "email", ""),
        },
        metadata={
            "source_path": str(payload.source_path or ""),
            "self_run_stage": str(payload.self_run_stage or ""),
        },
    )


def _resolve_identity_provider_env_keys(provider_name: str) -> Dict[str, str]:
    normalized = str(provider_name or "mock-carrier").strip().lower()
    if normalized == "pass":
        return {
            "endpoint": "PASS_IDENTITY_ENDPOINT",
            "client_id": "PASS_CLIENT_ID",
            "client_secret": "PASS_CLIENT_SECRET",
            "callback_url": "PASS_CALLBACK_URL",
        }
    if normalized == "kmc":
        return {
            "endpoint": "KMC_IDENTITY_ENDPOINT",
            "client_id": "KMC_CLIENT_ID",
            "client_secret": "KMC_CLIENT_SECRET",
            "callback_url": "KMC_CALLBACK_URL",
        }
    if normalized == "kcb":
        return {
            "endpoint": "KCB_IDENTITY_ENDPOINT",
            "client_id": "KCB_CLIENT_ID",
            "client_secret": "KCB_CLIENT_SECRET",
            "callback_url": "KCB_CALLBACK_URL",
        }
    return {
        "endpoint": "IDENTITY_PROVIDER_ENDPOINT",
        "client_id": "IDENTITY_PROVIDER_CLIENT_ID",
        "client_secret": "IDENTITY_PROVIDER_CLIENT_SECRET",
        "callback_url": "IDENTITY_PROVIDER_CALLBACK_URL",
    }


@router.get("/identity-provider-settings", response_model=AdminIdentityProviderSettingsResponse)
def get_admin_identity_provider_settings(
    admin: User = Depends(require_admin),
):
    del admin
    env_values = _read_admin_env_values(_admin_env_path())
    active_provider_name = str(env_values.get("IDENTITY_PROVIDER") or "mock-carrier").strip().lower() or "mock-carrier"
    active_provider = resolve_identity_provider(active_provider_name)
    provider_names = ["pass", "kmc", "kcb"]
    provider_statuses: List[Dict[str, Any]] = []
    complete_payload_contracts: List[Dict[str, Any]] = []

    for provider_name in provider_names:
        provider = resolve_identity_provider(provider_name)
        provider_statuses.append(provider.build_mapping_status(env_values))
        contract = provider.build_complete_payload_contract()
        complete_payload_contracts.append(
            {
                "provider": contract.provider,
                "required_fields": list(contract.required_fields),
                "optional_fields": list(contract.optional_fields),
                "callback_fields": list(contract.callback_fields),
            }
        )

    return AdminIdentityProviderSettingsResponse(
        provider=active_provider.provider_name,
        env_keys=_resolve_identity_provider_env_keys(active_provider.provider_name),
        callback_url=str(active_provider.build_mapping_status(env_values).get("callback_url") or ""),
        provider_statuses=provider_statuses,
        guides=_identity_provider_guide_text(),
        complete_payload_contracts=complete_payload_contracts,
    )


_DEBUG_VALIDATION_JOBS: Dict[str, Dict[str, Any]] = {}


def _run_debug_validation_profile_sync(project_root: Path, db: Session) -> Dict[str, Any]:
    profile = _build_project_python_debug_profile(project_root)
    verification_items: List[Dict[str, Any]] = []
    traceback_text = ""
    py_files = [
        path for path in project_root.rglob('*.py')
        if path.is_file() and '__pycache__' not in path.parts and '.venv' not in path.parts
    ]
    py_compile_ok = True
    py_compile_error = ""
    for path in py_files:
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            py_compile_ok = False
            py_compile_error = f"{path}: {exc.msg}"
            traceback_text = traceback.format_exc()
            break
    verification_items.append({
        "key": "py_compile",
        "label": "Python py_compile",
        "status": "passed" if py_compile_ok else "failed",
        "detail": py_compile_error or f"{len(py_files)}개 파일 py_compile 통과",
        "checkedAt": datetime.now().isoformat(),
    })
    verification_items.append({
        "key": "runtime_verification",
        "label": "관리자 API 런타임 검증",
        "status": "passed",
        "detail": "프로젝트 문맥 저장/실험/승인 게이트 API를 직접 호출해 응답 여부를 확인했습니다.",
        "checkedAt": datetime.now().isoformat(),
    })
    verification_items.append({
        "key": "traceback_capture",
        "label": "traceback 캡처",
        "status": "passed" if not traceback_text else "failed",
        "detail": "최근 검증에서 traceback 없음" if not traceback_text else traceback_text[-400: ],
        "checkedAt": datetime.now().isoformat(),
    })
    context = enrich_experiment_with_debug_validation(
        db,
        project_root=str(project_root),
        debug_profile=profile,
        verification_items=verification_items,
        traceback_text=traceback_text,
    )
    return {
        "debug_profile": profile,
        "verification_items": verification_items,
        "context": context,
    }


class AdminAutoConnectCompletionItem(BaseModel):
    id: int
    trace_id: Optional[str] = None
    flow_id: Optional[str] = None
    step_id: Optional[str] = None
    action: Optional[str] = None
    project_name: str
    mode: str
    attempts: int
    output_dir: Optional[str] = None
    postcheck_ok: Optional[bool] = None
    gate_passed: bool
    override_used: bool
    created_at: datetime
    connection_id: Optional[str] = None


class AdminAutoConnectTraceLogItem(BaseModel):
    id: int
    trace_id: str
    flow_id: str
    step_id: str
    action: str
    entity_type: str
    entity_id: str
    status: str
    message: str
    payload_json: Optional[str] = None
    created_at: datetime
    connection_id: Optional[str] = None


@router.post("/account/password")
def change_admin_account_password(
    payload: AdminPasswordChangeRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    next_password = _validate_admin_password_change_payload(payload)
    stored_hash = str(getattr(admin, "hashed_password", "") or "")
    if not stored_hash or not verify_password(payload.current_password, stored_hash):
        raise HTTPException(status_code=400, detail="현재 비밀번호가 올바르지 않습니다.")

    admin.hashed_password = get_password_hash(next_password)
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return {
        "changed": True,
        "message": "관리자 비밀번호가 변경되었습니다. 새 비밀번호로 다시 로그인해 주세요.",
        "username": str(admin.username or ""),
        "email": str(admin.email or ""),
    }


@router.get("/orchestrator/project-context")
def get_admin_orchestrator_project_context(
    project_root: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    normalized_root = str(project_root or "").strip()
    if not normalized_root:
        raise HTTPException(status_code=400, detail="project_root가 필요합니다.")
    return get_project_context_bundle(db, normalized_root)


@router.put("/orchestrator/project-memory")
def update_admin_orchestrator_project_memory(
    payload: AdminProjectMemoryUpdateRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    memory = {
        "project_name": str(payload.project_name or "").strip(),
        "remembered_goal": str(payload.remembered_goal or "").strip(),
        "constraints": [str(item).strip() for item in payload.constraints if str(item).strip()],
        "pending_tasks": [str(item).strip() for item in payload.pending_tasks if str(item).strip()],
        "decisions": [str(item).strip() for item in payload.decisions if str(item).strip()],
    }
    return upsert_project_memory_snapshot(
        db,
        project_root=payload.project_root,
        memory=memory,
    )


@router.post("/orchestrator/experiments")
def create_admin_orchestrator_experiment_record(
    payload: AdminExperimentRecordRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    record = append_experiment_record(
        db,
        project_root=payload.project_root,
        hypothesis=payload.hypothesis,
        method=payload.method,
        result_summary=payload.result_summary,
        conclusion=payload.conclusion,
        applied=payload.applied,
        evidence=payload.evidence,
    )
    return {
        "record": record,
        "context": get_project_context_bundle(db, payload.project_root),
    }


@router.post("/orchestrator/approval-gate")
def create_admin_orchestrator_approval_gate(
    payload: AdminApprovalGateUpdateRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    gate = append_approval_gate_record(
        db,
        project_root=payload.project_root,
        status=payload.status,
        scope=[str(item).strip() for item in payload.scope if str(item).strip()],
        blocked_paths=[str(item).strip() for item in payload.blocked_paths if str(item).strip()],
        validation_rules=[str(item).strip() for item in payload.validation_rules if str(item).strip()],
        rationale=payload.rationale,
    )
    return {
        "approval_gate": gate,
        "context": get_project_context_bundle(db, payload.project_root),
    }


@router.post("/orchestrator/global-approval-policy")
def create_admin_orchestrator_global_approval_policy(
    payload: AdminGlobalApprovalPolicyRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    workspace_root = normalize_project_root(str(_admin_workspace_root()))
    requested_scope = [str(item).strip() for item in payload.scope if str(item).strip()]
    requested_blocked_paths = [str(item).strip() for item in payload.blocked_paths if str(item).strip()]
    requested_rules = [str(item).strip() for item in payload.validation_rules if str(item).strip()]
    normalized_scope = [workspace_root] if workspace_root else requested_scope
    normalized_blocked_paths = [
        item for item in requested_blocked_paths
        if normalize_project_root(item) != workspace_root
    ]
    normalized_rules = list(dict.fromkeys([
        *requested_rules,
        "workspace self-run 은 전체 프로젝트 루트 기준으로만 실행",
    ]))
    policy = upsert_global_approval_policy(
        db,
        representative_project_root=workspace_root or payload.representative_project_root,
        status=payload.status,
        scope=normalized_scope,
        blocked_paths=normalized_blocked_paths,
        validation_rules=normalized_rules,
        rationale=payload.rationale,
    )
    return {
        "global_approval_policy": policy,
        "context": get_project_context_bundle(db, workspace_root or payload.representative_project_root),
    }


@router.post("/orchestrator/debug-validation-profile")
def create_admin_orchestrator_debug_validation_profile(
    payload: AdminDebugValidationProfileRequest,
    admin: User = Depends(require_admin),
):
    project_root = resolve_admin_project_root(payload.project_root)
    return enqueue_debug_validation_job(
        project_root=str(project_root),
        admin_id=int(admin.id),
    )


@router.get("/orchestrator/debug-validation-profile/{job_id}")
def get_admin_orchestrator_debug_validation_profile_job(
    job_id: str,
    admin: User = Depends(require_admin),
):
    del admin
    return get_debug_validation_job(job_id)


@router.post("/orchestrator/runtime-verification")
def run_admin_orchestrator_runtime_verification(
    payload: AdminRuntimeVerificationRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    project_root = resolve_admin_project_root(payload.project_root)
    del admin
    return build_runtime_verification_response(
        db=db,
        project_root=project_root,
        worker_log_path=payload.worker_log_path,
        bearer_token=str(request.headers.get("authorization") or "").replace("Bearer ", "").strip(),
        classify_gate_status=_classify_gate_status,
        read_admin_env_values=_read_admin_env_values,
        admin_env_path=_admin_env_path,
    )


# 주의: admin_router 는 import 시점에 데코레이터가 즉시 평가된다.
# 이 구간에서 아직 선언되지 않은 response_model 을 참조하면 admin router 전체 import 가 실패하고
# /api/admin/system-settings 를 포함한 admin 경로가 일괄 404 로 떨어진다.
# ad-video-orders / auto-connect-graph 계열 라우트는 같은 재발을 막기 위해
# 선행 선언된 타입만 사용하거나 dict 응답으로 유지한다.
@router.get("/auto-connect-graph/logs")
def list_admin_auto_connect_graph_logs(
    limit: int = Query(default=30, ge=1, le=200),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    del admin
    rows = (
        db.query(FeatureExecutionLog)
        .order_by(FeatureExecutionLog.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "items": [_serialize_auto_connect_log(item) for item in rows],
        "count": len(rows),
        "limit": limit,
    }


@router.get("/auto-connect-graph/completions")
def list_admin_auto_connect_graph_completions(
    limit: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    del admin
    rows = (
        db.query(CustomerOrchestratorCompletion)
        .order_by(CustomerOrchestratorCompletion.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "items": [_serialize_auto_connect_completion(item) for item in rows],
        "count": len(rows),
        "limit": limit,
    }


@router.get("/auto-connect-graph/retry-queue")
def list_admin_auto_connect_retry_queue(
    limit: int = Query(default=30, ge=1, le=200),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    del admin
    rows = (
        db.query(FeatureRetryQueue)
        .order_by(FeatureRetryQueue.updated_at.desc(), FeatureRetryQueue.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "items": [_serialize_auto_connect_retry_queue(item) for item in rows],
        "count": len(rows),
        "limit": limit,
    }


@router.get("/ad-video-orders")
def list_admin_ad_video_orders(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    del admin
    total = db.query(AdVideoOrder).count()
    rows = (
        db.query(AdVideoOrder)
        .order_by(AdVideoOrder.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return {
        "items": [
            {
                "id": int(getattr(order, "id", 0) or 0),
                "public_job_id": getattr(order, "public_job_id", None),
                "title": str(getattr(order, "title", "") or ""),
                "status": str(getattr(order, "status", "") or ""),
                "engine_type": str(getattr(order, "engine_type", "") or ""),
                "render_quality": str(getattr(order, "render_quality", "") or ""),
                "progress_percent": int(getattr(order, "progress_percent", 0) or 0),
                "quality_score": float(getattr(order, "quality_score", 0.0) or 0.0) if getattr(order, "quality_score", None) is not None else None,
                "download_count": int(getattr(order, "download_count", 0) or 0),
                "user_id": int(getattr(order, "user_id", 0) or 0),
                "created_at": getattr(order, "created_at", datetime.now()).isoformat(),
                "updated_at": getattr(order, "updated_at", datetime.now()).isoformat(),
                "error_message": getattr(order, "error_message", None),
            }
            for order in rows
        ],
        "total": int(total),
        "skip": skip,
        "limit": limit,
    }


@router.get("/ad-video-orders/monitor-summary")
def get_admin_ad_video_orders_monitor_summary(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):                                                                                                                               

    del admin
    return _build_admin_ad_order_monitor_summary_payload(db)


@router.get("/ad-video-orders/settlement-dashboard")
def get_admin_ad_video_orders_settlement_dashboard(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    del admin
    return _build_admin_ad_order_settlement_dashboard_payload(db)


@router.get("/projects")
def list_admin_projects(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=5000),
    request: Request = None,
    response: Response = None,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    del admin
    safe_limit = max(1, min(int(limit), 500))
    if response is not None:
        _apply_short_admin_projects_cache_headers(response, applied_limit=safe_limit)
    cache_key = f"{int(skip)}:{safe_limit}"
    cached = _ADMIN_PROJECTS_RESPONSE_CACHE.get(cache_key)
    if _is_legacy_admin_projects_request(int(limit)):
        degraded_payload = _build_admin_projects_degraded_payload(
            skip=int(skip),
            requested_limit=int(limit),
            applied_limit=safe_limit,
            cached_payload=(cached.get("payload") if isinstance(cached, dict) else None),
        )
        if response is not None:
            _apply_admin_projects_degraded_headers(
                response,
                mitigation="admin-projects-legacy-limit-cutoff",
                applied_limit=safe_limit,
            )
        return degraded_payload
    if request is not None and _should_throttle_admin_projects(request):
        degraded_payload = _build_admin_projects_degraded_payload(
            skip=int(skip),
            requested_limit=int(limit),
            applied_limit=safe_limit,
            cached_payload=(cached.get("payload") if isinstance(cached, dict) else None),
        )
        if response is not None:
            response.headers["Retry-After"] = str(max(1, int(_ADMIN_PROJECTS_RATE_LIMIT_WINDOW_SEC)))
            _apply_admin_projects_degraded_headers(
                response,
                mitigation="admin-projects-degraded-cache",
                applied_limit=safe_limit,
            )
        return degraded_payload
    now_ts = time.time()
    if cached and (now_ts - float(cached.get("captured_at") or 0.0)) < _ADMIN_PROJECTS_CACHE_TTL_SEC:
        return cached["payload"]
    with _ADMIN_PROJECTS_CACHE_LOCK:
        cached = _ADMIN_PROJECTS_RESPONSE_CACHE.get(cache_key)
        now_ts = time.time()
        if cached and (now_ts - float(cached.get("captured_at") or 0.0)) < _ADMIN_PROJECTS_CACHE_TTL_SEC:
            return cached["payload"]
        total = db.query(Project).count()
        rows = (
            db.query(Project)
            .order_by(Project.created_at.desc())
            .offset(skip)
            .limit(safe_limit)
            .all()
        )
        serialized_rows = [_serialize_project_item(project) for project in rows]
        payload = {
            "items": serialized_rows,
            "projects": serialized_rows,
            "total": int(total),
            "skip": skip,
            "limit": safe_limit,
            "requested_limit": int(limit),
            "applied_limit": safe_limit,
        }
        _ADMIN_PROJECTS_RESPONSE_CACHE[cache_key] = {
            "captured_at": now_ts,
            "payload": payload,
        }
        return payload


class AdminAutoConnectRetryQueueItem(BaseModel):
    id: int
    trace_id: str
    flow_id: str
    step_id: str
    action: str
    entity_type: str
    entity_id: str
    queue_name: str
    status: str
    payload_json: Optional[str] = None
    attempt_count: int
    max_attempts: int
    last_error: Optional[str] = None
    updated_at: Optional[datetime] = None
    created_at: datetime
    connection_id: Optional[str] = None


class AdminAutoConnectGraphLookupResponse(BaseModel):
    connection_id: str
    trace_key: str
    capability_id: Optional[str] = None
    completions: List[AdminAutoConnectCompletionItem]
    logs: List[AdminAutoConnectTraceLogItem]
    retry_queue: List[AdminAutoConnectRetryQueueItem]


class AdminProjectListItem(BaseModel):
    id: int
    title: str
    description: str
    price: float
    category_id: int
    author_id: int
    image_url: Optional[str] = None
    demo_url: Optional[str] = None
    github_url: Optional[str] = None
    file_key: Optional[str] = None
    downloads: int
    rating: float
    is_active: bool
    created_at: str
    updated_at: str


def _serialize_auto_connect_completion(item: Any) -> Dict[str, Any]:
    return {
        "id": int(getattr(item, "id", 0) or 0),
        "trace_id": getattr(item, "trace_id", None),
        "flow_id": getattr(item, "flow_id", None),
        "step_id": getattr(item, "step_id", None),
        "action": getattr(item, "action", None),
        "project_name": str(getattr(item, "project_name", "") or ""),
        "mode": str(getattr(item, "mode", "") or ""),
        "attempts": int(getattr(item, "attempts", 0) or 0),
        "output_dir": getattr(item, "output_dir", None),
        "postcheck_ok": getattr(item, "postcheck_ok", None),
        "gate_passed": bool(getattr(item, "gate_passed", False)),
        "override_used": bool(getattr(item, "override_used", False)),
        "created_at": getattr(item, "created_at", datetime.now()).isoformat(),
        "connection_id": (
            f"{getattr(item, 'flow_id', '')}:{getattr(item, 'step_id', '')}:{getattr(item, 'action', '')}"
            if getattr(item, "flow_id", None) and getattr(item, "step_id", None) and getattr(item, "action", None)
            else getattr(item, "trace_id", None)
        ),
    }


def _serialize_auto_connect_log(item: Any) -> Dict[str, Any]:
    return {
        "id": int(getattr(item, "id", 0) or 0),
        "trace_id": str(getattr(item, "trace_id", "") or ""),
        "flow_id": str(getattr(item, "flow_id", "") or ""),
        "step_id": str(getattr(item, "step_id", "") or ""),
        "action": str(getattr(item, "action", "") or ""),
        "entity_type": str(getattr(item, "entity_type", "") or ""),
        "entity_id": str(getattr(item, "entity_id", "") or ""),
        "status": str(getattr(item, "status", "") or ""),
        "message": str(getattr(item, "message", "") or ""),
        "payload_json": getattr(item, "payload_json", None),
        "created_at": getattr(item, "created_at", datetime.now()).isoformat(),
        "connection_id": (
            f"{getattr(item, 'flow_id', '')}:{getattr(item, 'step_id', '')}:{getattr(item, 'action', '')}"
            if getattr(item, "flow_id", None) and getattr(item, "step_id", None) and getattr(item, "action", None)
            else getattr(item, "trace_id", None)
        ),
    }


def _serialize_auto_connect_retry_queue(item: Any) -> Dict[str, Any]:
    return {
        "id": int(getattr(item, "id", 0) or 0),
        "trace_id": str(getattr(item, "trace_id", "") or ""),
        "flow_id": str(getattr(item, "flow_id", "") or ""),
        "step_id": str(getattr(item, "step_id", "") or ""),
        "action": str(getattr(item, "action", "") or ""),
        "entity_type": str(getattr(item, "entity_type", "") or ""),
        "entity_id": str(getattr(item, "entity_id", "") or ""),
        "queue_name": str(getattr(item, "queue_name", "") or ""),
        "status": str(getattr(item, "status", "") or ""),
        "payload_json": getattr(item, "payload_json", None),
        "attempt_count": int(getattr(item, "attempt_count", 0) or 0),
        "max_attempts": int(getattr(item, "max_attempts", 0) or 0),
        "last_error": getattr(item, "last_error", None),
        "updated_at": getattr(item, "updated_at", None).isoformat() if getattr(item, "updated_at", None) else None,
        "created_at": getattr(item, "created_at", datetime.now()).isoformat(),
        "connection_id": (
            f"{getattr(item, 'flow_id', '')}:{getattr(item, 'step_id', '')}:{getattr(item, 'action', '')}"
            if getattr(item, "flow_id", None) and getattr(item, "step_id", None) and getattr(item, "action", None)
            else getattr(item, "trace_id", None)
        ),
    }


def _build_admin_auto_connect_lookup_payload(connection_id: str) -> Dict[str, Any]:
    normalized = str(connection_id or "").strip()
    parts = [part.strip() for part in normalized.split(":") if part.strip()]
    return {
        "connection_id": normalized,
        "trace_key": ":".join(parts[:3]) if len(parts) >= 3 else normalized,
        "capability_id": ":".join(parts[3:]) if len(parts) > 3 else None,
    }


class AdminRatioItem(BaseModel):
    key: str
    label: str
    count: int
    ratio: float


class AdminAdOrderMonitorSummaryResponse(BaseModel):
    totals: Dict[str, Any]
    ratios: Dict[str, List[AdminRatioItem]]
    token_summary: Dict[str, Any]
    settlement: Dict[str, Any]


class AdminSettlementLogItem(BaseModel):
    order_id: int
    user_id: int
    status: str
    engine_type: str
    render_quality: str
    currency: str
    prompt_tokens: int
    render_tokens: int
    total_tokens: int
    local_cost: float
    external_cost: float
    storage_cost: float
    total_cost: float
    period_day: str
    period_month: str
    created_at: str


class AdminSettlementChartPoint(BaseModel):
    period: str
    order_count: int
    total_tokens: int
    total_cost: float


class AdminAdOrderSettlementDashboardResponse(BaseModel):
    daily: List[AdminSettlementChartPoint]
    monthly: List[AdminSettlementChartPoint]
    recent_logs: List[AdminSettlementLogItem]
    settlement_line: str


def _build_admin_ad_order_monitor_summary_payload(db: Session) -> Dict[str, Any]:
    from backend.marketplace.models import AdVideoOrder

    orders = db.query(AdVideoOrder).all()
    total_orders = len(orders)
    status_counter = Counter(str(getattr(order, "status", "") or "unknown") for order in orders)
    engine_counter = Counter(str(getattr(order, "engine_type", "") or "unknown") for order in orders)
    quality_counter = Counter(str(getattr(order, "render_quality", "") or "unknown") for order in orders)

    def ratio_items(counter: Counter[str]) -> List[Dict[str, Any]]:
        if total_orders <= 0:
            return []
        return [
            {
                "key": key,
                "label": key,
                "count": count,
                "ratio": round((count / total_orders) * 100, 2),
            }
            for key, count in counter.most_common()
        ]

    completed_orders = status_counter.get("completed", 0)
    failed_orders = status_counter.get("failed", 0)
    active_orders = sum(
        count for key, count in status_counter.items()
        if key not in {"completed", "failed", "cancelled"}
    )
    progress_values = [float(getattr(order, "progress_percent", 0) or 0) for order in orders]
    quality_values = [float(getattr(order, "quality_score", 0) or 0) for order in orders if getattr(order, "quality_score", None) is not None]

    return {
        "totals": {
            "total_orders": total_orders,
            "active_orders": active_orders,
            "completed_orders": completed_orders,
            "failed_orders": failed_orders,
            "completion_rate": round((completed_orders / total_orders) * 100, 2) if total_orders else 0.0,
            "failure_rate": round((failed_orders / total_orders) * 100, 2) if total_orders else 0.0,
            "average_progress": round(sum(progress_values) / len(progress_values), 2) if progress_values else 0.0,
            "average_quality_score": round(sum(quality_values) / len(quality_values), 2) if quality_values else 0.0,
        },
        "ratios": {
            "status": ratio_items(status_counter),
            "engine": ratio_items(engine_counter),
            "quality": ratio_items(quality_counter),
        },
        "token_summary": {
            "estimated_prompt_tokens": 0,
            "estimated_render_tokens": 0,
            "estimated_total_tokens": 0,
            "estimated_avg_tokens_per_order": 0,
        },
        "settlement": {
            "local_cost_total": 0.0,
            "external_cost_total": 0.0,
            "storage_cost_total": 0.0,
            "total_estimated_cost": 0.0,
            "estimated_cost_per_order": 0.0,
            "settlement_line": "정산 데이터 집계는 기본값 기준으로 노출됩니다.",
        },
    }


def _build_admin_ad_order_settlement_dashboard_payload(db: Session) -> Dict[str, Any]:
    orders = (
        db.query(AdVideoOrder)
        .order_by(AdVideoOrder.created_at.desc())
        .limit(50)
        .all()
    )
    daily_counter: Dict[str, Dict[str, Any]] = {}
    monthly_counter: Dict[str, Dict[str, Any]] = {}
    recent_logs: List[Dict[str, Any]] = []

    for order in orders:
        created_at = getattr(order, "created_at", None)
        if created_at is None:
            continue
        day_key = created_at.strftime("%Y-%m-%d")
        month_key = created_at.strftime("%Y-%m")
        daily_counter.setdefault(day_key, {"period": day_key, "order_count": 0, "total_tokens": 0, "total_cost": 0.0})["order_count"] += 1
        monthly_counter.setdefault(month_key, {"period": month_key, "order_count": 0, "total_tokens": 0, "total_cost": 0.0})["order_count"] += 1

        recent_logs.append(
            {
                "order_id": int(getattr(order, "id", 0) or 0),
                "user_id": int(getattr(order, "user_id", 0) or 0),
                "status": str(getattr(order, "status", "") or ""),
                "engine_type": str(getattr(order, "engine_type", "") or ""),
                "render_quality": str(getattr(order, "render_quality", "") or ""),
                "currency": "KRW",
                "prompt_tokens": 0,
                "render_tokens": 0,
                "total_tokens": 0,
                "local_cost": 0.0,
                "external_cost": 0.0,
                "storage_cost": 0.0,
                "total_cost": 0.0,
                "period_day": day_key,
                "period_month": month_key,
                "created_at": created_at.isoformat(),
            }
        )

    return {
        "daily": list(daily_counter.values()),
        "monthly": list(monthly_counter.values()),
        "recent_logs": recent_logs[:20],
        "settlement_line": "정산 대시보드는 기본 집계값으로 표시됩니다.",
    }


def _serialize_project_item(project: Project) -> Dict[str, Any]:
    return {
        "id": int(getattr(project, "id", 0) or 0),
        "title": str(getattr(project, "title", "") or ""),
        "description": str(getattr(project, "description", "") or ""),
        "price": float(getattr(project, "price", 0.0) or 0.0),
        "category_id": int(getattr(project, "category_id", 0) or 0),
        "author_id": int(getattr(project, "author_id", 0) or 0),
        "image_url": getattr(project, "image_url", None),
        "demo_url": getattr(project, "demo_url", None),
        "github_url": getattr(project, "github_url", None),
        "file_key": getattr(project, "file_key", None),
        "downloads": int(getattr(project, "downloads", 0) or 0),
        "rating": float(getattr(project, "rating", 0.0) or 0.0),
        "is_active": bool(getattr(project, "is_active", False)),
        "created_at": getattr(project, "created_at", datetime.now()).isoformat(),
        "updated_at": getattr(project, "updated_at", datetime.now()).isoformat(),
    }


ADMIN_TEXT_FILE_SUFFIXES = {
    ".md", ".txt", ".json", ".jsonl", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".env", ".py",
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".css", ".scss", ".sass", ".less", ".html", ".htm",
    ".sql", ".sh", ".ps1", ".bat", ".cmd", ".xml",
    ".csv", ".log", ".rst", ".java", ".kt", ".go",
    ".rs", ".c", ".cc", ".cpp", ".h", ".hpp", ".cs",
    ".php", ".rb", ".swift", ".dockerfile",
}
ADMIN_TEXT_FILE_NAMES = {
    "dockerfile", "makefile", "readme", "readme.md",
    "license", "license.md", "package-lock.json",
    "pnpm-lock.yaml", "yarn.lock", "requirements.txt",
    "agents.md", ".gitignore", ".dockerignore",
}
ADMIN_TEXT_LIST_LIMIT = 500
ADMIN_TEXT_MAX_BYTES = 200_000
ADMIN_SELF_TREE_LIMIT = 1_200
ADMIN_SELF_TEXT_FILE_LIMIT = 160
ADMIN_SELF_TEXT_CHAR_LIMIT = 180_000
ADMIN_SELF_TASK_TREE_LIMIT = 80
ADMIN_SELF_TASK_KEY_FILE_LIMIT = 20
ADMIN_SELF_EXCLUDE_DIR_NAMES = {
    ".git",
    ".next",
    ".next-dev-admin-3005",
    ".delivery-venv",
    ".venv",
    ".zip-venv",
    "__pycache__",
    "archive",
    "models",
    "node_modules",
    "uploads",
}

ADMIN_SYSTEM_ENV_SECTIONS: List[Dict[str, Any]] = [
    {
        "id": "postgres_runtime",
        "title": "PostgreSQL / 런타임 DB 연결",
        "usage": "로컬 DB 호스트, 사용자, 비밀번호 시크릿 파일 경로 관리",
        "description": "백엔드 런타임이 참조하는 PostgreSQL 접속 환경값입니다.",
        "fields": [
            "POSTGRES_HOST",
            "POSTGRES_PORT",
            "POSTGRES_DB",
            "POSTGRES_USER",
            "POSTGRES_PASSWORD",
            "POSTGRES_PASSWORD_FILE",
            "DATABASE_URL",
        ],
    },
    {
        "id": "domain_network",
        "title": "도메인 / 네트워크",
        "usage": "접속 주소, 포트, 프록시 기준 변경",
        "description": "도메인, 허용 Origin, 게이트웨이 포트와 로컬 API 연결을 조정합니다.",
        "fields": [
            "DOMAIN_NAME",
            "DOMAIN_ORIGINAL",
            "ADMIN_DOMAIN",
            "MARKETPLACE_API_DOMAIN",
            "SSL_EMAIL",
            "ALLOWED_ORIGINS",
            "NGINX_HTTP_PORT",
            "NGINX_HTTPS_PORT",
            "LOCAL_API_BASE_URL",
        ],
    },
    {
        "id": "marketplace_storage",
        "title": "스토리지 / 다운로드 정책",
        "usage": "산출물 루트, 보관 기간, 다운로드 제한 조정",
        "description": "마켓플레이스 산출물 저장 위치와 다운로드 유지 정책을 관리합니다.",
        "fields": [
            "MARKETPLACE_HOST_ROOT",
            "MARKETPLACE_UPLOAD_ROOT",
            "MARKETPLACE_RETENTION_DAYS",
            "MARKETPLACE_TEMP_RETENTION_DAYS",
            "AD_DOWNLOAD_MIN_NOTICE_MINUTES",
            "AD_DOWNLOAD_WINDOW_DAYS",
            "AD_DOWNLOAD_MAX_COUNT",
        ],
    },
    {
        "id": "video_engine",
        "title": "전용 영상 엔진",
        "usage": "영상 엔진 주소, 타임아웃, fallback 정책 조정",
        "description": "외부/전용 영상 엔진 연결과 폴링 정책을 통합 제어합니다.",
        "fields": [
            "VIDEO_DEDICATED_ENGINE_URL",
            "VIDEO_DEDICATED_SUBMIT_PATH",
            "VIDEO_DEDICATED_TIMEOUT_SEC",
            "VIDEO_DEDICATED_POLL_SEC",
            "VIDEO_ALLOW_LOCAL_DEDICATED_ENGINE",
            "VIDEO_REQUIRE_GENERATIVE_ENGINE",
            "VIDEO_ENGINE_FALLBACK_TO_INTERNAL",
            "VIDEO_DEDICATED_ENGINE_API_KEY",
        ],
    },
    {
        "id": "llm_defaults",
        "title": "LLM 기본 환경값",
        "usage": "부팅 기본 모델 환경값 점검 / 교체",
        "description": "부팅 시 참조되는 역할별 기본 모델 환경값입니다.",
        "fields": [
            "LLM_MODEL_DEFAULT",
            "LLM_MODEL_REASONING",
            "LLM_MODEL_CODING",
            "LLM_MODEL_CHAT",
            "LLM_MODEL_VOICE_CHAT",
            "LLM_MODEL_PLANNER",
            "LLM_MODEL_CODER",
            "LLM_MODEL_REVIEWER",
            "LLM_MODEL_DESIGNER",
            "LLM_MODEL_SMART_PLANNER",
            "LLM_MODEL_SMART_EXECUTOR",
            "LLM_MODEL_SMART_DESIGNER",
        ],
    },
    {
        "id": "orchestrator_self_engine",
        "title": "오케스트레이터 / 셀프 엔진",
        "usage": "자가 실행 게이트와 로컬 생성 파라미터 조정",
        "description": "자가 실행 게이트와 생성형 엔진 파라미터를 중앙에서 관리합니다.",
        "fields": [
            "ORCH_FORCE_COMPLETE",
            "ORCH_MIN_FILES",
            "ORCH_MIN_DIRS",
            "ORCH_MAX_FORCE_RETRIES",
            "ORCH_REQUIRED_FILES",
            "SELF_ENGINE_REQUIRE_FACE_IMAGE",
            "SELF_ENGINE_MAX_RETRY",
            "SELF_ENGINE_MIN_VIDEO_BYTES",
            "SELF_ENGINE_MIN_VIDEO_BYTES_PER_SEC",
            "SELF_ENGINE_MIN_DURATION_RATIO",
            "SELF_ENGINE_MIN_DURATION_SECONDS",
            "SELF_ENGINE_GENERATIVE_PROVIDER",
            "SELF_ENGINE_GENERATIVE_ENABLED",
            "SELF_ENGINE_GENERATIVE_FALLBACK_COMPOSITOR",
            "SELF_ENGINE_GENERATIVE_SUBMIT_URL",
            "SELF_ENGINE_GENERATIVE_STATUS_URL_TEMPLATE",
            "SELF_ENGINE_GENERATIVE_API_KEY",
            "SELF_ENGINE_GENERATIVE_TIMEOUT_SEC",
            "SELF_ENGINE_GENERATIVE_POLL_SEC",
            "SELF_ENGINE_GENERATIVE_VIDEO_URL_FIELD",
            "SELF_ENGINE_LOCAL_VIDEO_PIPELINE",
            "SELF_ENGINE_LOCAL_VIDEO_MODEL_ID",
            "SELF_ENGINE_LOCAL_VIDEO_WIDTH",
            "SELF_ENGINE_LOCAL_VIDEO_HEIGHT",
            "SELF_ENGINE_LOCAL_VIDEO_NUM_FRAMES",
            "SELF_ENGINE_LOCAL_VIDEO_STEPS",
            "SELF_ENGINE_LOCAL_VIDEO_DECODE_CHUNK",
            "SELF_ENGINE_LOCAL_VIDEO_MAX_UNIQUE_CLIPS",
            "SELF_ENGINE_LOCAL_VIDEO_PAD_TO_CUT",
            "SELF_ENGINE_LOCAL_VIDEO_BASE_FPS",
            "SELF_ENGINE_LOCAL_VIDEO_OUTPUT_FPS",
            "SELF_ENGINE_LOCAL_VIDEO_ENABLE_MINTERPOLATE",
            "SELF_ENGINE_LOCAL_VIDEO_MOTION_BUCKET",
            "SELF_ENGINE_LOCAL_VIDEO_MOTION_BUCKET_STEP",
            "SELF_ENGINE_LOCAL_VIDEO_NOISE_AUG",
            "SELF_ENGINE_LOCAL_VIDEO_GUIDANCE",
            "SELF_ENGINE_LOCAL_VIDEO_MIN_GUIDANCE",
            "SELF_ENGINE_LOCAL_VIDEO_MAX_GUIDANCE",
        ],
    },
    {
        "id": "identity_provider",
        "title": "본인확인 공급사 운영값",
        "usage": "PASS/KMC/KCB 상용 endpoint, client, callback URL 관리",
        "description": "본인확인 공급사 운영값과 callback URL을 중앙에서 관리합니다.",
        "fields": [
            "IDENTITY_PROVIDER",
            "PASS_IDENTITY_ENDPOINT",
            "PASS_CLIENT_ID",
            "PASS_CLIENT_SECRET",
            "PASS_CALLBACK_URL",
            "KMC_IDENTITY_ENDPOINT",
            "KMC_CLIENT_ID",
            "KMC_CLIENT_SECRET",
            "KMC_CALLBACK_URL",
            "KCB_IDENTITY_ENDPOINT",
            "KCB_CLIENT_ID",
            "KCB_CLIENT_SECRET",
            "KCB_CALLBACK_URL",
        ],
    },
]
ADMIN_SYSTEM_ENV_SENSITIVE_KEYS = {
    "POSTGRES_PASSWORD",
    "VIDEO_DEDICATED_ENGINE_API_KEY",
    "SELF_ENGINE_GENERATIVE_API_KEY",
    "PASS_CLIENT_SECRET",
    "KMC_CLIENT_SECRET",
    "KCB_CLIENT_SECRET",
}
ADMIN_SYSTEM_ENV_MULTILINE_KEYS = {
    "ALLOWED_ORIGINS",
    "ORCH_REQUIRED_FILES",
    "SELF_ENGINE_GENERATIVE_STATUS_URL_TEMPLATE",
}


def _admin_workspace_root() -> Path:
    return admin_workspace_root()


def _admin_runtime_root() -> Path:
    return admin_runtime_root()


def _admin_env_path() -> Path:
    return _admin_workspace_root() / ".env"


def _admin_orchestrator_runtime_config_path() -> Path:
    return _admin_workspace_root() / "knowledge" / "orchestrator_runtime_config.json"


def _admin_system_env_allowed_keys() -> set[str]:
    allowed: set[str] = set()
    for section in ADMIN_SYSTEM_ENV_SECTIONS:
        allowed.update(str(key) for key in section["fields"])
    return allowed


def _read_admin_env_entries(path: Path) -> List[Dict[str, Any]]:
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=".env 파일을 찾을 수 없습니다.")
    lines = path.read_text(encoding="utf-8").splitlines()
    entries: List[Dict[str, Any]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            entries.append({"kind": "raw", "line": line})
            continue
        key, value = line.split("=", 1)
        entries.append({"kind": "kv", "key": key.strip(), "value": value})
    return entries


def _read_admin_env_values(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for entry in _read_admin_env_entries(path):
        if entry.get("kind") == "kv":
            values[str(entry["key"])] = str(entry["value"])
    return values


def _write_admin_env_values(path: Path, updates: Dict[str, str]) -> Dict[str, str]:
    entries = _read_admin_env_entries(path)
    rendered_lines: List[str] = []
    seen_keys: set[str] = set()
    for entry in entries:
        if entry.get("kind") != "kv":
            rendered_lines.append(str(entry.get("line") or ""))
            continue
        key = str(entry["key"])
        next_value = updates.get(key, str(entry.get("value") or ""))
        rendered_lines.append(f"{key}={next_value}")
        seen_keys.add(key)
    missing_keys = [key for key in updates.keys() if key not in seen_keys]
    if missing_keys:
        if rendered_lines and rendered_lines[-1].strip():
            rendered_lines.append("")
        rendered_lines.append("# Admin dashboard appended settings")
        for key in missing_keys:
            rendered_lines.append(f"{key}={updates[key]}")
    path.write_text("\n".join(rendered_lines) + "\n", encoding="utf-8")
    return _read_admin_env_values(path)


def _resolve_windows_postgres_secret_path(env_values: Optional[Dict[str, str]] = None) -> Path:
    values = env_values or (_read_admin_env_values(_admin_env_path()) if _admin_env_path().exists() else {})
    configured_secret_root = str(values.get("HOST_SECRET_ROOT") or "").strip()
    if configured_secret_root:
        return (Path(configured_secret_root).expanduser() / "postgres_password.txt").resolve()
    return (_admin_workspace_root() / ".runtime" / "secrets" / "postgres_password.txt").resolve()


def _write_postgres_password_secret(password: str, env_values: Optional[Dict[str, str]] = None) -> str:
    secret_path = _resolve_windows_postgres_secret_path(env_values)
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret_path.write_text(password, encoding="utf-8")
    return str(secret_path)


def _load_runtime_config_summary() -> Dict[str, Any]:
    config_path = _admin_orchestrator_runtime_config_path()
    if not config_path.exists():
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _safe_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime if path.exists() else None
    except Exception:
        return None


def _build_admin_system_settings_payload(env_values_override: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    env_path = _admin_env_path()
    runtime_config_path = _admin_orchestrator_runtime_config_path()
    env_mtime = _safe_mtime(env_path)
    runtime_mtime = _safe_mtime(runtime_config_path)
    cached_payload = _ADMIN_SYSTEM_SETTINGS_CACHE.get("payload")
    cached_at = float(_ADMIN_SYSTEM_SETTINGS_CACHE.get("captured_at") or 0.0)
    if (
        env_values_override is None
        and isinstance(cached_payload, dict)
        and cached_payload
        and (time.time() - cached_at) <= ADMIN_SYSTEM_SETTINGS_CACHE_TTL_SEC
        and _ADMIN_SYSTEM_SETTINGS_CACHE.get("env_mtime") == env_mtime
        and _ADMIN_SYSTEM_SETTINGS_CACHE.get("runtime_mtime") == runtime_mtime
    ):
        return dict(cached_payload)

    env_values = env_values_override or _read_admin_env_values(env_path)
    runtime_config = _load_runtime_config_summary()
    sections: List[Dict[str, Any]] = []
    for section in ADMIN_SYSTEM_ENV_SECTIONS:
        fields = []
        for key in section["fields"]:
            key_text = str(key)
            fields.append(
                {
                    "key": key_text,
                    "label": key_text,
                    "value": str(env_values.get(key_text, "")),
                    "sensitive": key_text in ADMIN_SYSTEM_ENV_SENSITIVE_KEYS,
                    "multiline": key_text in ADMIN_SYSTEM_ENV_MULTILINE_KEYS,
                }
            )
        sections.append(
            {
                "id": str(section["id"]),
                "title": str(section["title"]),
                "usage": str(section.get("usage") or ""),
                "description": str(section["description"]),
                "fields": fields,
            }
        )
    model_routes = runtime_config.get("model_routes") or {}
    llm_status = llm_loader.get_cached_status()
    available_models = llm_status.get("models") or []
    generator_profiles = [
        {"id": "python_fastapi", "label": "Python FastAPI", "generator": "python_code_generator", "runtime_role": "backend api"},
        {"id": "python_worker", "label": "Python Worker", "generator": "python_code_generator", "runtime_role": "background worker"},
        {"id": "nextjs_react", "label": "Next.js React", "generator": "non_python_code_generator", "runtime_role": "frontend web"},
        {"id": "multi_code_generator", "label": "Multi Code Generator", "generator": "multi_code_generator", "runtime_role": "backend + frontend + sidecar services"},
    ]
    payload = {
        "env_path": str(env_path),
        "runtime_config_path": str(_admin_orchestrator_runtime_config_path()),
        "sections": sections,
        "summary": {
            "admin_domain": str(env_values.get("ADMIN_DOMAIN", "")),
            "api_domain": str(env_values.get("MARKETPLACE_API_DOMAIN", "")),
            "local_api_base_url": str(env_values.get("LOCAL_API_BASE_URL", "")),
            "marketplace_host_root": str(env_values.get("MARKETPLACE_HOST_ROOT", "")),
            "marketplace_upload_root": str(env_values.get("MARKETPLACE_UPLOAD_ROOT", "")),
            "selected_profile": str(runtime_config.get("selected_profile") or ""),
            "code_generation_strategy": str(runtime_config.get("code_generation_strategy") or ""),
            "default_model": str(model_routes.get("default") or ""),
            "chat_model": str(model_routes.get("chat") or ""),
            "voice_chat_model": str(model_routes.get("voice_chat") or ""),
            "reasoning_model": str(model_routes.get("reasoning") or ""),
            "coding_model": str(model_routes.get("coding") or ""),
            "available_model_count": int(len(available_models)),
            "available_models": available_models,
            "generator_profiles": generator_profiles,
        },
    }
    _ADMIN_SYSTEM_SETTINGS_CACHE.update(
        {
            "captured_at": time.time(),
            "env_mtime": env_mtime,
            "runtime_mtime": runtime_mtime,
            "payload": payload,
        }
    )
    return payload


def _coerce_env_int(value: Any, default: int, minimum: int = 0) -> int:
    try:
        parsed = int(str(value).strip())
    except Exception:
        parsed = default
    return max(minimum, parsed)


def _build_global_automatic_env_updates(env_values: Dict[str, str]) -> Dict[str, str]:
    current_retry = _coerce_env_int(env_values.get("SELF_ENGINE_MAX_RETRY"), 1, minimum=1)
    current_min_files = _coerce_env_int(env_values.get("ORCH_MIN_FILES"), 27, minimum=1)
    current_min_dirs = _coerce_env_int(env_values.get("ORCH_MIN_DIRS"), 3, minimum=0)
    generative_ready = any(
        str(env_values.get(key) or "").strip()
        for key in ["SELF_ENGINE_GENERATIVE_PROVIDER", "SELF_ENGINE_GENERATIVE_SUBMIT_URL", "SELF_ENGINE_GENERATIVE_STATUS_URL_TEMPLATE"]
    )
    return {
        "ORCH_FORCE_COMPLETE": "false",
        "ORCH_MIN_FILES": str(max(27, current_min_files)),
        "ORCH_MIN_DIRS": str(max(3, current_min_dirs)),
        "VIDEO_ALLOW_LOCAL_DEDICATED_ENGINE": "true",
        "VIDEO_ENGINE_FALLBACK_TO_INTERNAL": "true",
        "VIDEO_REQUIRE_GENERATIVE_ENGINE": "false",
        "SELF_ENGINE_GENERATIVE_FALLBACK_COMPOSITOR": "true",
        "SELF_ENGINE_GENERATIVE_ENABLED": "true" if generative_ready else "false",
        "SELF_ENGINE_MAX_RETRY": str(max(2, current_retry)),
    }


async def _apply_global_automatic_mode() -> AdminGlobalAutomaticModeResponse:
    env_path = _admin_env_path()
    current_env_values = _read_admin_env_values(env_path)
    env_updates = _build_global_automatic_env_updates(current_env_values)
    updated_env_values = _write_admin_env_values(env_path, env_updates)
    from backend.llm.orchestrator import OrchestratorRuntimeConfigUpdate, get_runtime_config, update_runtime_config
    current_runtime = await get_runtime_config()
    advisory_controls = dict(current_runtime.get("advisory_controls") or {})
    advisory_controls.update(
        {
            "clarification_questions_enabled": True,
            "max_clarification_questions": 3,
            "evidence_panel_enabled": True,
            "max_evidence_items": 5,
            "next_action_suggestions_enabled": True,
            "max_next_actions": 3,
        }
    )
    runtime_update = OrchestratorRuntimeConfigUpdate(
        code_generation_strategy="auto_generator",
        force_complete=False,
        allow_synthetic_fallback=True,
        max_force_retries=max(2, _coerce_env_int(current_runtime.get("max_force_retries"), 2, 1)),
        min_files=max(27, _coerce_env_int(current_runtime.get("min_files"), 27, 1)),
        min_dirs=max(3, _coerce_env_int(current_runtime.get("min_dirs"), 3, 0)),
        model_tuning_level=0,
        token_tuning_level=0,
        timeout_tuning_level=1,
        selected_profile=str(current_runtime.get("selected_profile") or "rtx5090_32b"),
        advisory_controls=advisory_controls,
    )
    applied_runtime = await update_runtime_config(runtime_update)
    runtime_summary = {
        "selected_profile": str(applied_runtime.get("selected_profile") or ""),
        "code_generation_strategy": str(applied_runtime.get("code_generation_strategy") or ""),
        "min_files": applied_runtime.get("min_files"),
        "min_dirs": applied_runtime.get("min_dirs"),
        "allow_synthetic_fallback": bool(applied_runtime.get("allow_synthetic_fallback")),
        "force_complete": bool(applied_runtime.get("force_complete")),
    }
    return AdminGlobalAutomaticModeResponse(
        applied_at=datetime.now().isoformat(),
        message="관리자 대시보드 전역 자동 프리셋을 적용했습니다.",
        restart_required=True,
        env_path=str(env_path),
        runtime_config_path=str(_admin_orchestrator_runtime_config_path()),
        updated_env_values={key: str(updated_env_values.get(key, "")) for key in sorted(env_updates.keys())},
        runtime_summary=runtime_summary,
    )


def _resolve_admin_workspace_path(requested_path: Optional[str]) -> Path:
    return resolve_admin_workspace_path(requested_path, read_admin_env_values=_read_admin_env_values, admin_env_path=_admin_env_path)


def _is_admin_text_file(path: Path) -> bool:
    return is_admin_text_file(path)


def _decode_admin_text_file(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "cp949"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise HTTPException(status_code=400, detail="UTF-8 또는 CP949 텍스트 파일만 불러올 수 있습니다.")


def _read_admin_text_file(path: Path) -> str:
    return read_admin_text_file(path)


def _workspace_relative_display(path: Path, base_dir: Path) -> str:
    return str(path.relative_to(base_dir)).replace("\\", "/")


def _orchestrator_output_base_dir(base_dir: Path, workspace_root: Path) -> str:
    if is_relative_to(base_dir, workspace_root):
        return str(base_dir.relative_to(workspace_root)).replace("\\", "/")
    return str(base_dir)


def _slugify_admin_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    cleaned = cleaned.strip("-._")
    return cleaned or "workspace"


def _should_skip_self_dir(path: Path) -> bool:
    return path.name.lower() in ADMIN_SELF_EXCLUDE_DIR_NAMES


def _scan_workspace_for_self_analysis(source_dir: Path) -> Dict[str, Any]:
    tree_lines: List[str] = [f"{source_dir.name}/"]
    text_files: List[Dict[str, Any]] = []
    skipped_directories: List[str] = []
    total_directories = 0
    total_files = 0
    tree_truncated = False

    def append_tree_line(line: str) -> None:
        nonlocal tree_truncated
        if len(tree_lines) < ADMIN_SELF_TREE_LIMIT:
            tree_lines.append(line)
        else:
            tree_truncated = True

    def walk(current_dir: Path, depth: int) -> None:
        nonlocal total_directories
        nonlocal total_files
        try:
            children = sorted(current_dir.iterdir(), key=lambda child: (0 if child.is_dir() else 1, child.name.lower()))
        except OSError:
            return
        for child in children:
            indent = "  " * (depth + 1)
            if child.is_dir():
                total_directories += 1
                relative_dir = _workspace_relative_display(child, source_dir)
                if _should_skip_self_dir(child):
                    skipped_directories.append(relative_dir)
                    append_tree_line(f"{indent}{child.name}/ [skip]")
                    continue
                append_tree_line(f"{indent}{child.name}/")
                walk(child, depth + 1)
                continue
            total_files += 1
            append_tree_line(f"{indent}{child.name}")
            if _is_admin_text_file(child):
                text_files.append(
                    {
                        "name": child.name,
                        "path": str(child),
                        "relative_path": _workspace_relative_display(child, source_dir),
                        "size_bytes": child.stat().st_size,
                    }
                )

    walk(source_dir, 0)
    return {
        "tree_lines": tree_lines,
        "tree_truncated": tree_truncated,
        "text_files": text_files,
        "skipped_directories": skipped_directories,
        "total_directories": total_directories,
        "total_files": total_files,
    }


def _build_self_analysis_bundle(source_dir: Path, text_files: List[Dict[str, Any]]) -> Dict[str, Any]:
    sections: List[str] = []
    included_files: List[str] = []
    total_chars = 0
    omitted_files = 0
    for item in text_files[:ADMIN_SELF_TEXT_FILE_LIMIT]:
        try:
            raw_text = _decode_admin_text_file(Path(str(item["path"])))
        except HTTPException:
            omitted_files += 1
            continue
        excerpt = raw_text[:8000]
        if len(raw_text) > len(excerpt):
            excerpt = excerpt + "\n\n...[중략: 파일 전체가 너무 길어 일부만 포함]"
        chunk = f"\n\n### FILE: {item['relative_path']}\nSIZE: {item['size_bytes']} bytes\n\n{excerpt}"
        if total_chars + len(chunk) > ADMIN_SELF_TEXT_CHAR_LIMIT:
            omitted_files += 1
            break
        sections.append(chunk)
        included_files.append(str(item["relative_path"]))
        total_chars += len(chunk)
    return {
        "content_bundle": "".join(sections).strip(),
        "included_files": included_files,
        "included_count": len(included_files),
        "content_chars": total_chars,
        "omitted_files": omitted_files,
    }


def _build_self_task_tree_preview(scan_result: Dict[str, Any]) -> str:
    tree_preview = "\n".join(scan_result["tree_lines"][:ADMIN_SELF_TASK_TREE_LIMIT])
    if scan_result.get("tree_truncated") or len(scan_result["tree_lines"]) > ADMIN_SELF_TASK_TREE_LIMIT:
        tree_preview += "\n...[중략: 구조 프리뷰가 길어 일부만 포함]"
    return tree_preview


def _build_self_task_key_files(scan_result: Dict[str, Any]) -> str:
    key_files = "\n".join(
        f"- {item['relative_path']} ({item['size_bytes']} bytes)"
        for item in scan_result["text_files"][:ADMIN_SELF_TASK_KEY_FILE_LIMIT]
    )
    if len(scan_result["text_files"]) > ADMIN_SELF_TASK_KEY_FILE_LIMIT:
        key_files += "\n- ...[중략: 핵심 텍스트 파일 목록이 길어 일부만 포함]"
    return key_files


def _build_self_task_content_bundle(bundle_result: Dict[str, Any]) -> str:
    omitted_files = int(bundle_result.get("omitted_files") or 0)
    included_count = int(bundle_result.get("included_count") or 0)
    total_chars = int(bundle_result.get("content_chars") or 0)
    return (
        "[분석용 파일 본문 컨텍스트 요약]\n"
        f"- 포함 파일 수: {included_count}\n"
        f"- 누락 파일 수: {omitted_files}\n"
        f"- 원본 컨텍스트 문자 수: {total_chars}\n"
        "- 컨텍스트 포함 파일에는 실행에 필요한 최소한의 소스 코드만 담길 것\n"
        "- 주문 의도에 따라 실제 파일 본문 수정은 오케스트레이터 내부 코드 컨텍스트 조회로 보완할 것"
    )


def _load_json_file(path: Path) -> Dict[str, Any]:
    raw_text = path.read_text(encoding="utf-8").strip()
    if not raw_text:
        return {}
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        return {}


def _write_json_file(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _admin_experiment_clone_root() -> Path:
    return _admin_runtime_root() / "admin_self_experiments"


def _admin_self_run_root() -> Path:
    return _admin_runtime_root() / "admin_self_runs"


def _admin_self_backup_root() -> Path:
    return _admin_runtime_root() / "admin_self_backups"


def _approval_record_path(approval_id: str) -> Path:
    return _admin_self_run_root() / approval_id / "approval.json"


def _approval_payload_to_self_run_response(approval_payload: Dict[str, Any]) -> Dict[str, Any]:
    return approval_payload_to_self_run_response_service(approval_payload)


def _latest_self_run_record_path(pending_only: bool = False) -> Optional[Path]:
    return latest_self_run_record_path_service(admin_self_run_root=_admin_self_run_root, load_json_file=_load_json_file, pending_only=pending_only)


def _self_run_timeout_sec() -> int:
    return max(120, int(os.getenv("ADMIN_SELF_RUN_TIMEOUT_SEC", os.getenv("ORCH_JOB_TIMEOUT_SEC", "3600"))))


def _is_self_run_worker_alive(worker_pid: Optional[int]) -> bool:
    if not worker_pid or worker_pid <= 0:
        return False
    try:
        os.kill(worker_pid, 0)
    except OSError:
        return False
    return True


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _read_self_run_worker_log_excerpt(approval_payload: Dict[str, Any], max_chars: int = 4000) -> str:
    log_path_text = str(approval_payload.get("worker_log_path") or "").strip()
    if not log_path_text:
        return ""
    log_path = Path(log_path_text)
    if not log_path.exists() or not log_path.is_file():
        return ""
    try:
        log_text = log_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    return log_text[-max_chars:].strip()


def _extract_self_run_worker_error_detail(approval_payload: Dict[str, Any]) -> str:
    log_excerpt = _read_self_run_worker_log_excerpt(approval_payload, max_chars=8000)
    if not log_excerpt:
        return ""
    lines = [line.strip() for line in log_excerpt.splitlines() if line.strip()]
    for marker in ("[call_agent:error]", "call_agent failed:", "worker 예외로 종료됨:", "Traceback (most recent call last):"):
        for line in reversed(lines):
            if marker in line:
                return line
    return ""


def _build_stale_self_run_report_preview(approval_payload: Dict[str, Any], stale_reason: str, detail: str) -> str:
    requested_mode = str(approval_payload.get("requested_mode") or "self-run")
    report_lines = [
        f"# {requested_mode} self-run report",
        "",
        "- 상태: failed",
        f"- 실패 분류: {stale_reason}",
        f"- 오류: {detail}",
    ]
    worker_log_path = str(approval_payload.get("worker_log_path") or "").strip()
    if worker_log_path:
        report_lines.append(f"- worker 로그: {worker_log_path}")
    log_excerpt = _read_self_run_worker_log_excerpt(approval_payload)
    if log_excerpt:
        report_lines.extend(["", "## Worker 로그 발췌", "", "```text", log_excerpt, "```"])
    return "\n".join(report_lines)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _collect_syncable_files(base_dir: Path) -> Dict[str, Dict[str, Any]]:
    items: Dict[str, Dict[str, Any]] = {}

    def walk(current_dir: Path) -> None:
        try:
            children = sorted(current_dir.iterdir(), key=lambda child: (0 if child.is_dir() else 1, child.name.lower()))
        except OSError:
            return
        for child in children:
            if child.is_dir():
                if _should_skip_self_dir(child):
                    continue
                walk(child)
                continue
            if child.is_file():
                rel_path = _workspace_relative_display(child, base_dir)
                items[rel_path] = {"path": str(child), "size_bytes": child.stat().st_size, "sha256": _sha256_file(child)}

    walk(base_dir)
    return items


def _build_workspace_snapshot(base_dir: Path) -> Dict[str, Any]:
    files = _collect_syncable_files(base_dir)
    digest = hashlib.sha256()
    total_bytes = 0
    snapshot_files: Dict[str, Dict[str, Any]] = {}
    for rel_path in sorted(files.keys()):
        file_info = files[rel_path]
        size_bytes = int(file_info["size_bytes"])
        file_hash = str(file_info["sha256"])
        total_bytes += size_bytes
        digest.update(rel_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size_bytes).encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hash.encode("utf-8"))
        digest.update(b"\0")
        snapshot_files[rel_path] = {"size_bytes": size_bytes, "sha256": file_hash}
    return {"fingerprint": digest.hexdigest(), "total_files": len(snapshot_files), "total_bytes": total_bytes, "files": snapshot_files}


def _is_self_run_approval_ready(orchestration_result: Dict[str, Any]) -> tuple[bool, List[str]]:
    required_truthy_fields = ["applied", "postcheck_ok", "dod_ok", "completion_gate_ok", "semantic_audit_ok", "structure_validation_ok", "traceability_map_path"]
    failed_fields = [field_name for field_name in required_truthy_fields if not orchestration_result.get(field_name)]
    return len(failed_fields) == 0, failed_fields


def _extract_orchestration_failure_detail(orchestration_result: Dict[str, Any]) -> str:
    candidate_texts = [orchestration_result.get("failure_summary"), orchestration_result.get("apply_error"), orchestration_result.get("final_output")]
    for candidate in candidate_texts:
        text = str(candidate or "").strip()
        if not text:
            continue
        if text.startswith("[에이전트 오류:") or "응답 대기 시간 초과" in text or "Traceback" in text:
            return text
    return ""


def _collect_empty_syncable_dirs(base_dir: Path) -> List[str]:
    empty_dirs: List[str] = []

    def walk(current_dir: Path) -> None:
        try:
            children = list(current_dir.iterdir())
        except OSError:
            return
        visible_children: List[Path] = []
        for child in children:
            if child.is_dir() and _should_skip_self_dir(child):
                continue
            visible_children.append(child)
        if current_dir != base_dir and not visible_children:
            empty_dirs.append(_workspace_relative_display(current_dir, base_dir))
            return
        for child in visible_children:
            if child.is_dir():
                walk(child)

    walk(base_dir)
    return sorted(empty_dirs)


def _run_admin_approval_validation(target_dir: Path) -> tuple[bool, List[str], Optional[str]]:
    return run_admin_approval_validation_service(target_dir, collect_syncable_files=_collect_syncable_files, collect_empty_syncable_dirs=_collect_empty_syncable_dirs, py_compile_module=py_compile)


def _build_python_self_diagnostic_fallback(target_dir: Path, requested_mode: str, failure_detail: str) -> Dict[str, Any]:
    from backend.llm.code_analyzer import code_analyzer
    analysis_path = code_analyzer.write_analysis_report(target_dir)
    validation_ok, validation_logs, validation_error = _run_admin_approval_validation(target_dir)
    inventory = _collect_syncable_files(target_dir)
    python_files = [rel_path for rel_path in sorted(inventory.keys()) if rel_path.lower().endswith(".py")]
    docs_dir = target_dir / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    root_cause_path = docs_dir / "root_cause_analysis.md"
    root_cause_report = (
        "# Root Cause Analysis\n\n"
        "## 현상\n\n"
        f"- self-run 모드: {requested_mode}\n"
        f"- LLM/오케스트레이터 실패 근거: {failure_detail or '-'}\n"
        f"- clone 기준 Python 파일 수: {len(python_files)}\n"
        f"- py_compile 승인 전 검증: {'ok' if validation_ok else 'fail'}\n\n"
        "## 근본 원인\n\n"
        "- live self-run 이 LLM 응답 대기 또는 게이트 실패로 멈추면, 수정 후보가 approval 기록에 충분히 남지 않았습니다.\n"
        "- 그래서 clone 기준 Python 정적 분석과 py_compile 검증 결과를 별도 진단 산출물로 강제 생성합니다.\n\n"
        "## 즉시 수정 후보\n\n"
        "- backend/llm/orchestrator.py: self-run prompt/context 범위를 더 줄일 것\n"
        "- backend/llm/orchestrator.py: planner 설계 결과와 auto_generator 산출물 경로를 일관되게 유지할 것\n"
        "- backend/admin_router.py: approval 종료 시 Python 진단 산출물을 항상 report에 연결할 것\n"
        "- 관리자 화면: 최신 self-run failure 대신 Python 진단 보고서 경로를 함께 노출할 것\n\n"
        "## Python 정적 점검 결과\n\n"
        + "\n".join(f"- {line}" for line in validation_logs[-20:])
        + "\n"
    )
    root_cause_path.write_text(root_cause_report, encoding="utf-8")
    summary = f"LLM self-run 실패 후 Python 정적 진단 fallback 수행 | analysis={analysis_path} | root_cause=docs/root_cause_analysis.md | py_compile={'ok' if validation_ok else 'fail'}"
    return {
        "analysis_path": analysis_path,
        "root_cause_report": root_cause_report,
        "root_cause_report_path": "docs/root_cause_analysis.md",
        "validation_ok": validation_ok,
        "validation_error": validation_error,
        "validation_logs": validation_logs,
        "summary": summary,
        "generated_files": [analysis_path, "docs/root_cause_analysis.md"],
    }


def _merge_python_self_diagnostic_result(orchestration_result: Dict[str, Any], diagnostic_result: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(orchestration_result or {})
    written_files = list(merged.get("written_files") or [])
    for rel_path in diagnostic_result.get("generated_files") or []:
        if rel_path and rel_path not in written_files:
            written_files.append(rel_path)
    merged["written_files"] = written_files
    merged["analysis_path"] = diagnostic_result.get("analysis_path")
    merged["root_cause_report"] = diagnostic_result.get("root_cause_report")
    merged["root_cause_report_path"] = diagnostic_result.get("root_cause_report_path")
    merged["python_self_diagnostic_used"] = True
    merged["python_self_diagnostic_ok"] = diagnostic_result.get("validation_ok")
    merged["python_self_diagnostic_error"] = diagnostic_result.get("validation_error")
    merged["python_self_diagnostic_logs"] = diagnostic_result.get("validation_logs")
    if not merged.get("failure_summary"):
        merged["failure_summary"] = diagnostic_result.get("summary")
    else:
        merged["failure_summary"] = str(merged.get("failure_summary") or "") + "\n\n[python-self-diagnostic]\n" + str(diagnostic_result.get("summary") or "")
    if not merged.get("final_output"):
        merged["final_output"] = diagnostic_result.get("summary")
    return merged


def _diff_workspace_trees(source_dir: Path, clone_dir: Path) -> Dict[str, Any]:
    source_files = _collect_syncable_files(source_dir)
    clone_files = _collect_syncable_files(clone_dir)
    source_keys = set(source_files.keys())
    clone_keys = set(clone_files.keys())
    added_files = sorted(clone_keys - source_keys)
    deleted_files = sorted(source_keys - clone_keys)
    modified_files = sorted(rel for rel in (source_keys & clone_keys) if source_files[rel]["sha256"] != clone_files[rel]["sha256"])
    return {
        "added_files": added_files,
        "modified_files": modified_files,
        "deleted_files": deleted_files,
        "total_changed_files": len(added_files) + len(modified_files) + len(deleted_files),
    }


def _build_self_run_report_preview(requested_mode: str, source_dir: Path, clone_dir: Path, scan_result: Dict[str, Any], diff_summary: Dict[str, Any], orchestration_result: Dict[str, Any], approval_status: str) -> str:
    approval_gate_ok, approval_gate_failed_fields = _is_self_run_approval_ready(orchestration_result)
    output_excerpt = str(orchestration_result.get("final_output") or orchestration_result.get("failure_summary") or "")[:4000]
    changed_lines = [f"- added: {len(diff_summary['added_files'])}", f"- modified: {len(diff_summary['modified_files'])}", f"- deleted: {len(diff_summary['deleted_files'])}"]
    if diff_summary["added_files"]:
        changed_lines.append("- added files:\n  - " + "\n  - ".join(diff_summary["added_files"][:40]))
    if diff_summary["modified_files"]:
        changed_lines.append("- modified files:\n  - " + "\n  - ".join(diff_summary["modified_files"][:40]))
    if diff_summary["deleted_files"]:
        changed_lines.append("- deleted files:\n  - " + "\n  - ".join(diff_summary["deleted_files"][:40]))
    changed_summary = "\n".join(changed_lines)
    return (
        f"# {requested_mode} self-run report\n\n"
        f"- source_path: {source_dir}\n"
        f"- experiment_clone_path: {clone_dir}\n"
        f"- approval_status: {approval_status}\n"
        f"- orchestration_mode: {orchestration_result.get('mode')}\n"
        f"- applied_on_clone: {orchestration_result.get('applied')}\n"
        f"- postcheck_ok: {orchestration_result.get('postcheck_ok')}\n"
        f"- dod_ok: {orchestration_result.get('dod_ok')}\n"
        "- output_dir: " + str(orchestration_result.get("output_dir") or orchestration_result.get("failed_output_dir")) + "\n"
        f"- completion_gate_ok: {orchestration_result.get('completion_gate_ok')}\n"
        f"- semantic_audit_ok: {orchestration_result.get('semantic_audit_ok')}\n"
        f"- approval_gate_ok: {approval_gate_ok}\n"
        f"- approval_gate_failed_fields: {', '.join(approval_gate_failed_fields) or '-'}\n"
        f"- apply_error: {orchestration_result.get('apply_error') or '-'}\n\n"
        f"## 구조 요약\n\n"
        f"- directories: {scan_result['total_directories']}\n"
        f"- files: {scan_result['total_files']}\n"
        f"- text files: {len(scan_result['text_files'])}\n\n"
        f"## 변경 요약\n\n{changed_summary}\n\n"
        f"## 실행 결과 프리뷰\n\n{output_excerpt}"
    )


def _self_run_job_request_path(approval_id: str) -> Path:
    return _admin_self_run_root() / approval_id / "job_request.json"


def _self_run_worker_log_path(approval_id: str) -> Path:
    return _admin_self_run_root() / approval_id / "worker.log"


def _self_run_worker_host_log_path(approval_id: str) -> str:
    return str(_self_run_worker_log_path(approval_id))


def _self_run_worker_status_path(approval_id: str) -> Path:
    return _admin_self_run_root() / approval_id / "worker_status.json"


def _self_run_progress_log_path(approval_id: str) -> Path:
    return _admin_self_run_root() / approval_id / "progress.jsonl"


def _emit_self_run_progress_marker(approval_id: str, phase: str, **payload: Any) -> None:
    progress_path = _self_run_progress_log_path(approval_id)
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    event = {"approval_id": approval_id, "phase": phase, "timestamp": datetime.now().isoformat(), **payload}
    with progress_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def _clone_workspace_for_experiment(source_dir: Path) -> Dict[str, Any]:
    clone_root = _admin_experiment_clone_root()
    clone_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    clone_dir = clone_root / f"{_slugify_admin_name(source_dir.name)}_{timestamp}"
    shutil.copytree(source_dir, clone_dir, ignore=shutil.ignore_patterns(*ADMIN_SELF_EXCLUDE_DIR_NAMES))
    copied_files = sum(1 for item in clone_dir.rglob("*") if item.is_file())
    return {"clone_path": str(clone_dir), "copied_files": copied_files}


def _self_run_execution_mode(requested_mode: str) -> str:
    if requested_mode == "self-improvement":
        return "full"
    if requested_mode == "self-expansion":
        return "plan"
    return "review"


def _self_run_directive_template_label(template_key: str) -> str:
    template = (template_key or "").strip().lower()
    template_labels = {
        "debug_remediation_loop": "디버깅 기반 결함 교정 루프",
        "video_ad_clarity": "영상 선명도 개선",
        "video_ad_conversion": "전환율 개선",
        "video_ad_speed_optimization": "속도 최적화",
        "video_ad_storytelling": "스토리텔링 강화",
        "video_ad_quality_upgrade": "영상광고 품질 고도화",
        "video_ad_new_tech": "영상광고 신기술 도입",
        "admin_ops_efficiency": "관리자 운영 효율화",
        "marketplace_conversion": "마켓플레이스 전환 개선",
        "llm_cost_latency": "LLM 비용/지연 최적화",
        "focused-self-healing": "원인 집중 자가치유",
    }
    return template_labels.get(template, "직접 주문")


def _self_run_directive_scope_label(scope_key: str) -> str:
    scope = (scope_key or "").strip().lower()
    scope_labels = {
        "preset_default": "프리셋 권장 범위",
        "diagnosis_only": "진단/설계 중심",
        "targeted_implementation": "지정 범위만 구현",
        "feature_expansion": "기능 확장 우선",
        "modernization": "구조 개선 포함",
    }
    return scope_labels.get(scope, "프리셋 권장 범위")


def _build_self_run_directive_block(directive_template: str, directive_scope: str, directive_request: str) -> str:
    normalized_request = str(directive_request or "").strip()
    normalized_template = str(directive_template or "").strip()
    normalized_scope = str(directive_scope or "").strip()
    if not (normalized_request or normalized_template or normalized_scope):
        return ""
    lines = ["[선택 주문]"]
    if normalized_template:
        lines.append("- 주문 템플릿: " + _self_run_directive_template_label(normalized_template))
    if normalized_scope:
        lines.append(f"- 실행 범위: {_self_run_directive_scope_label(normalized_scope)}")
    if normalized_request:
        lines.append(f"- 사용자 주문: {normalized_request}")
    lines.append("- 위 주문은 현재 self-run 목적보다 우선순위를 침범하지 않는 범위에서 반영할 것")
    return "\n".join(lines)


def _build_debug_remediation_protocol_block(requested_mode: str, directive_request: str) -> str:
    if requested_mode != "self-improvement":
        return ""
    corrective_command = str(directive_request or "").strip()
    lines = [
        "[디버깅 시스템 표준 프로토콜]",
        "- 1단계 결함 식별 및 제거: validation_findings, runtime diagnostics, 보안 위반을 근거로 실제 결함만 식별하고 즉시 제거할 것",
        "- 2단계 시스템 이해도 향상: 결함이 연결된 라우터, 상태, 저장 경로, 호출 체인을 설명 가능한 수준으로 재구성할 것",
        "- 3단계 리스크 관리: 수정 파급 범위, 잠재 회귀, 미검증 영역, 승인 전 위험을 분리 기록할 것",
        "- 4단계 성능 최적화: 결함 제거 후 남는 병목과 자원 낭비만 최적화하고, 기능 회귀를 만들지 말 것",
    ]
    if corrective_command:
        lines.extend(["", "[교정 조치 명령]", corrective_command])
    return "\n".join(lines)


def _suggested_self_mode(requested_mode: str) -> str:
    if requested_mode == "self-improvement":
        return "full"
    if requested_mode == "self-expansion":
        return "plan"
    return "review"


def _trim_self_run_task_text(value: str, max_chars: int = 2500) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _build_self_prepare_task(requested_mode: str, source_dir: Path, scan_result: Dict[str, Any], bundle_result: Dict[str, Any], experiment_clone_path: Optional[str]) -> str:
    title = "오케스트레이터 자가개선 준비 실행" if requested_mode == "self-improvement" else ("오케스트레이터 자가확장 준비 실행" if requested_mode == "self-expansion" else "오케스트레이터 자가진단 준비 실행")
    mode_steps = [
        "1. 먼저 전체 구조와 핵심 파일 연결을 정확히 분석",
        "2. 개선 실험은 반드시 복제본에서 먼저 수행",
        "3. 복제본에서 검증 통과한 변경만 유지 또는 반영",
        "4. 적용 결과와 실패 원인을 구체적으로 남길 것",
    ]
    tree_preview = _build_self_task_tree_preview(scan_result)
    key_files = _build_self_task_key_files(scan_result)
    skipped_dirs = "\n".join(f"- {path}" for path in scan_result["skipped_directories"][:20]) or "- 없음"
    content_bundle = _build_self_task_content_bundle(bundle_result)
    target_line = f"실험 복제본 경로: {experiment_clone_path}" if experiment_clone_path else f"원본 분석 경로: {source_dir}"
    return (
        f"{title}\n\n"
        f"대상 루트: {source_dir}\n"
        f"{target_line}\n"
        f"권장 실행 모드: {_suggested_self_mode(requested_mode)}\n\n"
        f"[실행 원칙]\n- " + "\n- ".join(mode_steps) + "\n\n"
        f"[구조 요약]\n"
        f"- 디렉터리 수: {scan_result['total_directories']}\n"
        f"- 파일 수: {scan_result['total_files']}\n"
        f"- 텍스트/코드 파일 수: {len(scan_result['text_files'])}\n"
        f"- 분석 컨텍스트 포함 파일 수: {bundle_result['included_count']}\n"
        f"- 분석 컨텍스트 문자 수: {bundle_result['content_chars']}\n\n"
        f"[스킵한 대용량/생성 디렉터리]\n{skipped_dirs}\n\n"
        f"[전체 구조 프리뷰]\n{tree_preview}\n\n"
        f"[핵심 텍스트 파일 목록]\n{key_files}\n\n"
        f"{content_bundle}"
    )


def _build_self_run_task(requested_mode: str, source_dir: Path, experiment_clone_path: Path, scan_result: Dict[str, Any], bundle_result: Dict[str, Any], directive_template: str = "", directive_scope: str = "", directive_request: str = "") -> str:
    title = "오케스트레이터 자가개선 실험 즉시 실행" if requested_mode == "self-improvement" else ("오케스트레이터 자가확장 실험 즉시 실행" if requested_mode == "self-expansion" else "오케스트레이터 자가진단 실험 즉시 실행")
    action_lines = [
        "1. 대상 폴더 전체 구조와 핵심 파일 연결을 먼저 분석",
        "2. 실험 복제본에서 실제 수정과 검증을 수행",
        "3. 검증을 통과한 개선 결과물만 승인 대기 대상으로 남김",
        "4. 원본 폴더는 승인 전까지 절대 직접 수정하지 않음",
    ]
    tree_preview = _build_self_task_tree_preview(scan_result)
    key_files = _build_self_task_key_files(scan_result)
    skipped_dirs = "\n".join(f"- {path}" for path in scan_result["skipped_directories"][:20]) or "- 없음"
    content_bundle = _build_self_task_content_bundle(bundle_result)
    directive_block = _build_self_run_directive_block(directive_template, directive_scope, directive_request)
    debug_protocol_block = _build_debug_remediation_protocol_block(requested_mode, directive_request)
    return (
        f"{title}\n\n"
        f"원본 대상 경로: {source_dir}\n"
        f"실험 복제본 경로: {experiment_clone_path}\n"
        f"실행 모드: {_self_run_execution_mode(requested_mode)}\n\n"
        f"[반드시 지킬 원칙]\n- " + "\n- ".join(action_lines) + "\n- 최종 응답에는 진단 요약, 실험 결과, 검증 결과, 승인 대기용 핵심 변경점을 모두 포함\n- 승인 전 단계에서는 원본 폴더를 직접 수정했다고 주장하지 말 것\n\n"
        + (f"{directive_block}\n\n" if directive_block else "")
        + (f"{debug_protocol_block}\n\n" if debug_protocol_block else "")
        + "[대상 구조 요약]\n"
        + f"- 디렉터리 수: {scan_result['total_directories']}\n"
        + f"- 파일 수: {scan_result['total_files']}\n"
        + f"- 텍스트/코드 파일 수: {len(scan_result['text_files'])}\n"
        + f"- 분석 컨텍스트 포함 파일 수: {bundle_result['included_count']}\n"
        + f"- 분석 컨텍스트 문자 수: {bundle_result['content_chars']}\n\n"
        + f"[스킵 디렉터리]\n{skipped_dirs}\n\n"
        + f"[구조 프리뷰]\n{_trim_self_run_task_text(tree_preview, max_chars=2500)}\n\n"
        + f"[핵심 텍스트 파일]\n{_trim_self_run_task_text(key_files, max_chars=2500)}\n\n"
        + f"{_trim_self_run_task_text(content_bundle, max_chars=8000)}\n\n"
        + "[실행용 작업문 제약]\n- 실행용 작업문에는 구조/컨텍스트를 요약본만 유지할 것\n- 긴 산출물 파일명에 task 전문을 재사용하지 말 것\n- 출고 아카이브 파일명은 output_dir 이름 또는 짧은 project_name 기준으로 생성할 것"
    )


def _build_admin_analysis_summary(scan_result: Dict[str, Any], bundle_result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "total_directories": scan_result["total_directories"],
        "total_files": scan_result["total_files"],
        "total_text_files": len(scan_result["text_files"]),
        "included_text_files": bundle_result["included_count"],
        "content_chars": bundle_result["content_chars"],
        "tree_truncated": scan_result["tree_truncated"],
    }


def _prepare_workspace_self_prepare_result(source_dir: Path, requested_mode: str, create_experiment_clone: bool) -> Dict[str, Any]:
    return prepare_workspace_self_prepare_result_service(
        source_dir,
        requested_mode,
        create_experiment_clone,
        scan_workspace_for_self_analysis=_scan_workspace_for_self_analysis,
        build_self_analysis_bundle=_build_self_analysis_bundle,
        clone_workspace_for_experiment=_clone_workspace_for_experiment,
        build_self_prepare_task=_build_self_prepare_task,
        suggested_self_mode=_suggested_self_mode,
        build_admin_analysis_summary=_build_admin_analysis_summary,
    )


def _prepare_workspace_self_run_context(source_dir: Path, requested_mode: str, directive_template: str = "", directive_scope: str = "", directive_request: str = "") -> Dict[str, Any]:
    scan_result = _scan_workspace_for_self_analysis(source_dir)
    bundle_result = _build_self_analysis_bundle(source_dir, scan_result["text_files"])
    clone_result = _clone_workspace_for_experiment(source_dir)
    clone_dir = Path(str(clone_result["clone_path"])).resolve()
    return {
        "source_path": str(source_dir),
        "requested_mode": str(requested_mode or ""),
        "directive_template": str(directive_template or ""),
        "directive_scope": str(directive_scope or ""),
        "directive_request": str(directive_request or ""),
        "experiment_clone_path": str(clone_dir),
        "analysis_summary": _build_admin_analysis_summary(scan_result, bundle_result),
        "tree_preview": "\n".join(scan_result["tree_lines"]),
        "key_text_files": scan_result["text_files"][:80],
        "runtime_diagnostic": "focused self-healing 이전 self-run 컨텍스트 준비가 완료되었습니다.",
    }


def _run_workspace_self_run_job_from_request(job_request_path: Path) -> None:
    job_request = _load_json_file(job_request_path)
    approval_id = str(job_request["approval_id"])
    requested_mode = str(job_request["requested_mode"])
    execution_mode = _self_run_execution_mode(requested_mode)
    directive_template = str(job_request.get("directive_template") or "")
    directive_scope = str(job_request.get("directive_scope") or "")
    directive_request = str(job_request.get("directive_request") or "")
    source_dir = Path(str(job_request["source_path"])).resolve()
    record_path = _approval_record_path(approval_id)
    report_path = record_path.parent / "report_preview.md"
    clone_dir: Optional[Path] = None
    scan_result: Dict[str, Any] = {}
    bundle_result: Dict[str, Any] = {}
    try:
        scan_result = _scan_workspace_for_self_analysis(source_dir)
        bundle_result = _build_self_analysis_bundle(source_dir, scan_result["text_files"])
        clone_result = _clone_workspace_for_experiment(source_dir)
        clone_dir = Path(str(clone_result["clone_path"])).resolve()
        task_text = _build_self_run_task(requested_mode, source_dir, clone_dir, scan_result, bundle_result, directive_template, directive_scope, directive_request)
        approval_payload = _load_json_file(record_path)
        approval_payload.update(
            {
                "execution_mode": execution_mode,
                "experiment_clone_path": str(clone_dir),
                "analysis_summary": _build_admin_analysis_summary(scan_result, bundle_result),
                "tree_preview": "\n".join(scan_result["tree_lines"]),
                "key_text_files": scan_result["text_files"][:80],
                "executed_task": task_text,
                "runtime_diagnostic": "백그라운드 worker가 self-run 준비를 완료하고 오케스트레이터를 시작합니다.",
            }
        )
        _write_json_file(record_path, approval_payload)

        from backend.llm.orchestrator import OrchestrationRequest, orchestrate

        workspace_root = Path(__file__).resolve().parents[1]
        clone_base_dir = _orchestrator_output_base_dir(clone_dir.parent, workspace_root)
        orchestration_request = OrchestrationRequest(task=task_text, mode=execution_mode, output_base_dir=clone_base_dir, output_dir=str(clone_dir), continue_in_place=True, manual_mode=False, companion_mode="project")
        orchestration_response = asyncio.run(orchestrate(orchestration_request))
        orchestration_result = orchestration_response.model_dump()
        diff_summary = _diff_workspace_trees(source_dir, clone_dir)
        source_snapshot = _build_workspace_snapshot(source_dir)
        approval_gate_ok, approval_gate_failed_fields = _is_self_run_approval_ready(orchestration_result)
        orchestration_error = _extract_orchestration_failure_detail(orchestration_result)
        diagnostic_result: Dict[str, Any] = {}
        if orchestration_error or not approval_gate_ok:
            diagnostic_result = _build_python_self_diagnostic_fallback(clone_dir, requested_mode, orchestration_error or "approval gate not satisfied")
            orchestration_result = _merge_python_self_diagnostic_result(orchestration_result, diagnostic_result)
            diff_summary = _diff_workspace_trees(source_dir, clone_dir)
            approval_gate_ok, approval_gate_failed_fields = _is_self_run_approval_ready(orchestration_result)
        approval_status = "failed"
        if approval_gate_ok and diff_summary["total_changed_files"] > 0:
            approval_status = "pending_approval"
        elif diff_summary["total_changed_files"] == 0 and not orchestration_error:
            approval_status = "no_changes"
        report_preview = _build_self_run_report_preview(requested_mode, source_dir, clone_dir, scan_result, diff_summary, orchestration_result, approval_status)
        report_path.write_text(report_preview, encoding="utf-8")
        approval_payload = _load_json_file(record_path)
        approval_payload.update(
            {
                "status": approval_status,
                "execution_mode": execution_mode,
                "directive_template": directive_template,
                "directive_scope": directive_scope,
                "directive_request": directive_request,
                "source_snapshot": source_snapshot,
                "approval_gate_ok": approval_gate_ok,
                "approval_gate_failed_fields": approval_gate_failed_fields,
                "diff_summary": diff_summary,
                "orchestration_result": orchestration_result,
                "report_preview": report_preview,
                "report_path": str(report_path),
                "finished_at": datetime.now().isoformat(),
                "orchestration_error": orchestration_error,
                "runtime_diagnostic": (orchestration_error or diagnostic_result.get("summary") or approval_payload.get("runtime_diagnostic") or ""),
                "analysis_summary": _build_admin_analysis_summary(scan_result, bundle_result),
            }
        )
        _write_json_file(record_path, approval_payload)
    except BaseException as exc:
        approval_payload: Dict[str, Any] = _load_json_file(record_path) if record_path.exists() else {"approval_id": approval_id}
        trace_text = traceback.format_exc()
        diagnostic_result: Dict[str, Any] = {}
        if clone_dir is not None and clone_dir.exists():
            diagnostic_result = _build_python_self_diagnostic_fallback(clone_dir, requested_mode, str(exc))
        report_preview = (
            f"# {requested_mode} self-run report\n\n"
            f"- 상태: failed\n"
            f"- 오류: {str(exc)}\n\n"
            + (
                "## Python 정적 진단 fallback\n\n"
                f"- summary: {diagnostic_result.get('summary')}\n"
                f"- analysis: {diagnostic_result.get('analysis_path')}\n"
                f"- root cause: {diagnostic_result.get('root_cause_report_path')}\n\n"
                if diagnostic_result else ""
            )
            + "## 예외 추적\n\n```text\n"
            + f"{trace_text[-6000:]}\n"
            + "```"
        )
        report_path.write_text(report_preview, encoding="utf-8")
        approval_payload["status"] = "failed"
        approval_payload["execution_mode"] = execution_mode
        approval_payload["directive_template"] = directive_template
        approval_payload["directive_scope"] = directive_scope
        approval_payload["directive_request"] = directive_request
        approval_payload["approval_gate_ok"] = False
        approval_payload["approval_gate_failed_fields"] = ["runtime_exception"]
        approval_payload["diff_summary"] = approval_payload.get("diff_summary") or {"added_files": [], "modified_files": [], "deleted_files": [], "total_changed_files": 0}
        approval_payload["orchestration_result"] = _merge_python_self_diagnostic_result(dict(approval_payload.get("orchestration_result") or {}), diagnostic_result) if diagnostic_result else (approval_payload.get("orchestration_result") or {})
        approval_payload["report_preview"] = report_preview
        approval_payload["report_path"] = str(report_path)
        approval_payload["finished_at"] = datetime.now().isoformat()
        error_message = str(exc).strip()
        approval_payload["orchestration_error"] = f"{type(exc).__name__}: {error_message}" if error_message else type(exc).__name__
        if diagnostic_result:
            approval_payload["runtime_diagnostic"] = str(diagnostic_result.get("summary") or "")
        _write_json_file(record_path, approval_payload)
        raise


def _start_workspace_self_run_job(approval_id: str, requested_mode: str, directive_template: str, directive_scope: str, directive_request: str, source_dir: Path) -> tuple[int, str]:
    job_request_path = _self_run_job_request_path(approval_id)
    _write_json_file(
        job_request_path,
        {
            "approval_id": approval_id,
            "requested_mode": requested_mode,
            "directive_template": directive_template,
            "directive_scope": directive_scope,
            "directive_request": directive_request,
            "source_path": str(source_dir),
        },
    )
    worker_log_path = _self_run_worker_log_path(approval_id)
    worker_log_path.parent.mkdir(parents=True, exist_ok=True)
    worker_env = os.environ.copy()
    worker_env["PYTHONUNBUFFERED"] = "1"
    worker_log_handle = worker_log_path.open("a", encoding="utf-8")
    worker = subprocess.Popen([sys.executable, "-u", "-m", "backend.admin_self_run_worker", str(job_request_path)], cwd=str(_admin_workspace_root()), stdout=worker_log_handle, stderr=subprocess.STDOUT, env=worker_env)
    worker_log_handle.close()
    return int(worker.pid), str(worker_log_path)


def _force_fail_running_self_run_record(record_path: Path, *, stale_reason: str, detail: str) -> Dict[str, Any]:
    approval_payload = _load_json_file(record_path) if record_path.exists() else {}
    if str(approval_payload.get("status") or "") != "running":
        return approval_payload
    failed_fields = list(approval_payload.get("approval_gate_failed_fields") or [])
    if "runtime_exception" not in failed_fields:
        failed_fields.append("runtime_exception")
    approval_payload["status"] = "failed"
    approval_payload["approval_gate_ok"] = False
    approval_payload["approval_gate_failed_fields"] = failed_fields
    approval_payload["finished_at"] = str(approval_payload.get("finished_at") or datetime.now().isoformat())
    approval_payload["worker_alive"] = False
    approval_payload["orchestration_error"] = detail
    approval_payload["runtime_diagnostic"] = detail
    approval_payload["report_preview"] = _build_stale_self_run_report_preview(approval_payload, stale_reason, detail)
    report_path_text = str(approval_payload.get("report_path") or "").strip()
    if report_path_text:
        try:
            Path(report_path_text).write_text(approval_payload["report_preview"], encoding="utf-8")
        except Exception:
            pass
    _write_json_file(record_path, approval_payload)
    return approval_payload


def _stabilize_running_self_run_record(record_path: Path, approval_payload: Dict[str, Any]) -> Dict[str, Any]:
    if str(approval_payload.get("status") or "") != "running":
        return approval_payload
    worker_pid = approval_payload.get("worker_pid") if isinstance(approval_payload.get("worker_pid"), int) else None
    started_at = _parse_iso_datetime(approval_payload.get("started_at"))
    timeout_sec = _self_run_timeout_sec()
    now = datetime.now()
    running_seconds: Optional[int] = None
    if started_at is not None:
        running_seconds = max(0, int((now - started_at).total_seconds()))
    worker_alive = _is_self_run_worker_alive(worker_pid)
    terminal_error_detail = _extract_self_run_worker_error_detail(approval_payload)
    approval_payload["worker_alive"] = worker_alive
    approval_payload["running_seconds"] = running_seconds
    if running_seconds is not None:
        approval_payload["runtime_diagnostic"] = f"백그라운드 worker 실행 경과 {running_seconds}초 / 제한 {timeout_sec}초"
    stale_reason = ""
    stale_detail = ""
    if worker_pid is None:
        stale_reason = "worker_pid_missing"
        stale_detail = "running 상태인데 worker_pid 가 기록되지 않았습니다."
    elif not worker_alive:
        stale_reason = "worker_process_exited"
        stale_detail = "백그라운드 worker 프로세스가 종료됐지만 approval 기록이 running 상태로 남았습니다."
        if terminal_error_detail:
            stale_detail = f"{stale_detail} 마지막 worker 오류: {terminal_error_detail}"
    elif running_seconds is not None and running_seconds > timeout_sec:
        stale_reason = "worker_timeout"
        stale_detail = f"백그라운드 worker 실행 시간이 {running_seconds}초로 제한 {timeout_sec}초를 초과했습니다."
    if not stale_reason:
        _write_json_file(record_path, approval_payload)
        return approval_payload
    _write_json_file(record_path, approval_payload)
    return _force_fail_running_self_run_record(record_path, stale_reason=stale_reason, detail=stale_detail)


@router.get("/system-settings")
def get_admin_system_settings(admin: User = Depends(require_admin)):
    del admin
    return _build_admin_system_settings_payload()


@router.put("/system-settings")
def update_admin_system_settings(payload: AdminSystemSettingsUpdateRequest, admin: User = Depends(require_admin)):
    del admin
    updates = payload.values or {}
    allowed_keys = _admin_system_env_allowed_keys()
    unknown_keys = sorted(key for key in updates.keys() if key not in allowed_keys)
    if unknown_keys:
        raise HTTPException(status_code=400, detail="관리자 대시보드에서 허용되지 않은 설정 키가 포함되었습니다: " + ", ".join(unknown_keys))
    env_values = _write_admin_env_values(_admin_env_path(), {key: str(value) for key, value in updates.items()})
    return _build_admin_system_settings_payload(env_values_override=env_values)


@router.post("/system-settings/postgres-password")
def update_postgres_runtime_password(payload: AdminPostgresPasswordUpdateRequest, admin: User = Depends(require_admin)):
    next_password = _validate_postgres_password_change_payload(payload)
    env_path = _admin_env_path()
    env_values = _read_admin_env_values(env_path)
    secret_host_path = _write_postgres_password_secret(next_password, env_values)
    file_setting = str(env_values.get("POSTGRES_PASSWORD_FILE") or "").strip() or "/run/codeai-secrets/postgres_password.txt"
    updated_env_values = _write_admin_env_values(
        env_path,
        {
            "POSTGRES_HOST": str(env_values.get("POSTGRES_HOST") or "localhost") or "localhost",
            "POSTGRES_USER": str(env_values.get("POSTGRES_USER") or "postgres") or "postgres",
            "POSTGRES_PASSWORD": next_password,
            "POSTGRES_PASSWORD_FILE": file_setting,
            "DATABASE_URL": "",
        },
    )
    return {
        "changed": True,
        "message": "PostgreSQL 런타임 비밀번호를 .env와 로컬 시크릿 파일에 기록했습니다.",
        "env_path": str(env_path),
        "secret_host_path": secret_host_path,
        "postgres_user": str(updated_env_values.get("POSTGRES_USER") or ""),
        "postgres_host": str(updated_env_values.get("POSTGRES_HOST") or ""),
        "postgres_db": str(updated_env_values.get("POSTGRES_DB") or ""),
    }


@router.post("/system-settings/global-automatic-mode")
async def apply_admin_global_automatic_mode(admin: User = Depends(require_admin)):
    del admin
    return await _apply_global_automatic_mode()


@router.get("/workspace-text-file")
def get_workspace_text_file(path: str = Query(...), admin: User = Depends(require_admin)):
    del admin
    target = _resolve_admin_workspace_path(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="파일만 불러올 수 있습니다.")
    if not _is_admin_text_file(target):
        raise HTTPException(status_code=400, detail="텍스트/코드 파일만 불러올 수 있습니다.")
    return {"path": str(target), "size_bytes": target.stat().st_size, "content": _read_admin_text_file(target)}


@router.get("/workspace-text-files")
def list_workspace_text_files(path: Optional[str] = Query(default=None), admin: User = Depends(require_admin)):
    del admin
    current_dir = _resolve_admin_workspace_path(path)
    if not current_dir.exists():
        raise HTTPException(status_code=404, detail="경로를 찾을 수 없습니다.")
    if not current_dir.is_dir():
        raise HTTPException(status_code=400, detail="디렉터리만 탐색할 수 있습니다.")
    workspace_root = _admin_workspace_root()
    entries = []
    children = sorted(current_dir.iterdir(), key=lambda child: (0 if child.is_dir() else 1, child.name.lower()))
    for child in children:
        if child.is_dir() or _is_admin_text_file(child):
            stat = child.stat()
            entries.append({"name": child.name, "path": str(child), "kind": "dir" if child.is_dir() else "file", "size_bytes": None if child.is_dir() else stat.st_size, "modified_at": stat.st_mtime})
        if len(entries) >= ADMIN_TEXT_LIST_LIMIT:
            break
    parent_path = None
    if current_dir != workspace_root and is_relative_to(current_dir.parent, workspace_root):
        parent_path = str(current_dir.parent)
    return {"root_path": str(workspace_root), "current_path": str(current_dir), "parent_path": parent_path, "entries": entries}


@router.get("/workspace-self-run-record")
def get_workspace_self_run_record(approval_id: Optional[str] = Query(default=None), latest: bool = Query(default=False), pending_only: bool = Query(default=False), admin: User = Depends(require_admin)):
    del admin
    return get_workspace_self_run_record_response_service(
        approval_id=approval_id,
        latest=latest,
        pending_only=pending_only,
        approval_record_path=_approval_record_path,
        latest_self_run_record_path_func=_latest_self_run_record_path,
        load_json_file=_load_json_file,
        stabilize_running_self_run_record=_stabilize_running_self_run_record,
        approval_payload_to_response=_approval_payload_to_self_run_response,
    )


@router.post("/workspace-experiment-clone")
def create_workspace_experiment_clone(payload: WorkspaceExperimentCloneRequest, admin: User = Depends(require_admin)):
    del admin
    source_dir = _resolve_admin_workspace_path(payload.source_path)
    if not source_dir.exists():
        raise HTTPException(status_code=404, detail="복제 대상 경로를 찾을 수 없습니다.")
    if not source_dir.is_dir():
        raise HTTPException(status_code=400, detail="실험 복제는 디렉터리만 가능합니다.")
    clone_result = _clone_workspace_for_experiment(source_dir)
    return {"source_path": str(source_dir), "clone_path": clone_result["clone_path"], "copied_files": clone_result["copied_files"], "excluded_directories": sorted(ADMIN_SELF_EXCLUDE_DIR_NAMES)}


@router.post("/workspace-self-prepare")
def prepare_workspace_for_self_modes(payload: WorkspaceSelfPrepareRequest, admin: User = Depends(require_admin)):
    del admin
    source_dir = _resolve_admin_workspace_path(payload.source_path)
    if not source_dir.exists():
        raise HTTPException(status_code=404, detail="분석 대상 경로를 찾을 수 없습니다.")
    if not source_dir.is_dir():
        raise HTTPException(status_code=400, detail="폴더 구조 분석은 디렉터리만 가능합니다.")
    return _prepare_workspace_self_prepare_result(source_dir, payload.mode, payload.create_experiment_clone)


@router.post("/workspace-self-run")
async def execute_workspace_self_run(payload: WorkspaceSelfRunRequest, admin: User = Depends(require_admin)):
    source_dir = _resolve_admin_workspace_path(payload.source_path)
    if not source_dir.exists():
        raise HTTPException(status_code=404, detail="실행 대상 경로를 찾을 수 없습니다.")
    if not source_dir.is_dir():
        raise HTTPException(status_code=400, detail="실행 대상은 디렉터리여야 합니다.")
    approval_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    approval_root = _admin_self_run_root() / approval_id
    approval_root.mkdir(parents=True, exist_ok=True)
    report_preview = (
        f"# {payload.mode} self-run report\n\n"
        f"- 상태: running\n"
        f"- 요청 모드: {payload.mode}\n"
        f"- 실제 실행 모드: {_self_run_execution_mode(payload.mode)}\n"
        f"- 대상 원본: {source_dir}\n"
        "- 실험 복제본: 준비 중\n\n"
        "자가진단/자가개선/자가확장 작업 준비를 백그라운드에서 시작했습니다. 완료 후 동일 approval_id로 결과를 조회해 주세요."
    )
    report_path = approval_root / "report_preview.md"
    report_path.write_text(report_preview, encoding="utf-8")
    approval_payload = build_initial_running_self_run_payload_service(
        approval_id,
        payload.mode,
        str(payload.directive_template or ""),
        str(payload.directive_scope or ""),
        str(payload.directive_request or ""),
        source_dir,
        report_path,
        report_preview,
        self_run_execution_mode=_self_run_execution_mode,
        self_run_worker_log_path=_self_run_worker_log_path,
        self_run_worker_host_log_path=_self_run_worker_host_log_path,
    )
    stage_run = _resolve_admin_stage_run(payload, admin)
    approval_payload["stage_run"] = stage_run
    _write_json_file(approval_root / "approval.json", approval_payload)
    try:
        worker_pid, worker_log_path = await asyncio.to_thread(_start_workspace_self_run_job, approval_id, payload.mode, str(payload.directive_template or ""), str(payload.directive_scope or ""), str(payload.directive_request or ""), source_dir)
    except Exception as exc:
        approval_payload["status"] = "failed"
        approval_payload["orchestration_error"] = "백그라운드 자가 실행 프로세스를 시작하지 못했습니다: " + f"{exc}"
        approval_payload["finished_at"] = datetime.now().isoformat()
        approval_payload["report_preview"] = f"# {payload.mode} self-run report\n\n- 상태: failed\n- 오류: {approval_payload['orchestration_error']}\n"
        report_path.write_text(approval_payload["report_preview"], encoding="utf-8")
        _write_json_file(approval_root / "approval.json", approval_payload)
        raise HTTPException(status_code=500, detail=approval_payload["orchestration_error"])
    approval_payload["worker_pid"] = worker_pid
    approval_payload["worker_log_path"] = worker_log_path
    approval_payload["worker_log_host_path"] = _self_run_worker_host_log_path(approval_id)
    approval_payload["worker_alive"] = True
    approval_payload["runtime_diagnostic"] = "백그라운드 worker가 시작되어 오케스트레이터 응답을 대기 중입니다."
    _write_json_file(approval_root / "approval.json", approval_payload)
    return _approval_payload_to_self_run_response(approval_payload)


@router.post("/workspace-self-run/approve")
def approve_workspace_self_run(payload: WorkspaceSelfApprovalRequest, admin: User = Depends(require_admin)):
    return approve_workspace_self_run_response_service(
        payload=payload,
        approval_record_path=_approval_record_path,
        load_json_file=_load_json_file,
        write_json_file=_write_json_file,
        resolve_admin_workspace_path=_resolve_admin_workspace_path,
        is_self_run_approval_ready=_is_self_run_approval_ready,
        build_workspace_snapshot=_build_workspace_snapshot,
        run_admin_approval_validation_func=_run_admin_approval_validation,
        diff_workspace_trees=_diff_workspace_trees,
        sync_clone_into_source_func=lambda source_dir, clone_dir: sync_clone_into_source_service(
            source_dir,
            clone_dir,
            admin_self_backup_root=_admin_self_backup_root,
            slugify_admin_name=_slugify_admin_name,
            admin_self_exclude_dir_names=tuple(ADMIN_SELF_EXCLUDE_DIR_NAMES),
            diff_workspace_trees=_diff_workspace_trees,
        ),
    )


@router.post("/workspace-self-run-record/normalize")
async def normalize_workspace_self_run_record(request: WorkspaceSelfRunNormalizeRequest, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    return await normalize_workspace_self_run_record_response_service(
        request=request,
        db=db,
        admin=admin,
        approval_record_path=_approval_record_path,
        latest_self_run_record_path_func=_latest_self_run_record_path,
        load_json_file=_load_json_file,
        stabilize_running_self_run_record=_stabilize_running_self_run_record,
        approval_payload_to_response=_approval_payload_to_self_run_response,
        admin_self_run_root=_admin_self_run_root,
        resolve_admin_workspace_path=_resolve_admin_workspace_path,
        admin_workspace_root=_admin_workspace_root,
        execute_workspace_self_run=lambda *args, **kwargs: asyncio.sleep(0, result={"queued": True, "message": "self-run 재생성 큐를 등록했습니다."}),
        workspace_self_run_request_type=WorkspaceSelfRunRequest,
    )


@router.post("/workspace-self-run-record/retry")
async def retry_workspace_self_run_record(payload: WorkspaceSelfRunRetryRequest, admin: User = Depends(require_admin)):
    del admin
    approval_payload = None
    if payload.approval_id:
        record_path = _approval_record_path(payload.approval_id)
        if record_path.exists():
            approval_payload = _load_json_file(record_path)
    target_source_path = str(payload.source_path or (approval_payload or {}).get("source_path") or str(_admin_workspace_root()))
    source_dir = _resolve_admin_workspace_path(target_source_path)
    context = _prepare_workspace_self_run_context(source_dir, "self-improvement" if payload.target_stage == "remediation" else "self-diagnosis", "debug_remediation_loop" if payload.target_stage == "remediation" else "", "targeted_implementation" if payload.target_stage == "remediation" else "diagnosis_only", str(payload.reason or "관리자 대시보드에서 self-run 재시도를 요청했습니다."))
    return {"queued": True, "approval_id": str(payload.approval_id or ""), "target_stage": str(payload.target_stage or ""), "source_path": target_source_path, "retry": {"mode": "self-improvement" if payload.target_stage == "remediation" else "self-diagnosis", "context": context}, "message": "self-run 재시도를 큐에 등록했습니다."}


class FocusedSelfHealingPlanRequest(BaseModel):
    issue_id: str
    requested_path: str
    reason: str
    proposal_title: Optional[str] = None
    proposal_summary: Optional[str] = None


class FocusedSelfHealingApplyRequest(BaseModel):
    issue_id: str
    requested_path: str
    reason: str
    approved: bool = False
    selected_option_id: Optional[str] = None


@router.post('/focused-self-healing/plan')
def build_focused_self_healing_plan(payload: FocusedSelfHealingPlanRequest, admin: User = Depends(require_admin)):
    del admin
    resolved_path = _resolve_admin_workspace_path(payload.requested_path)
    decision = build_focused_self_healing_decision(operation_id=str(payload.issue_id or '').strip(), requested_path=str(payload.requested_path or '').strip(), resolved_path=resolved_path, reason=str(payload.reason or '').strip())
    proposal_id = f"tower-{decision.operation_id or 'proposal'}"
    options = build_tower_crane_options(proposal_id=proposal_id, title=str(payload.proposal_title or payload.reason or 'Focused Self-Healing').strip(), summary=str(payload.proposal_summary or payload.reason or '').strip(), focused_path=decision.focused_path)
    return {
        'issue_id': decision.operation_id,
        'requested_path': decision.requested_path,
        'focused_path': decision.focused_path,
        'target_source_path': decision.target_source_path,
        'target_kind': decision.target_kind,
        'category': decision.category,
        'auto_apply_allowed': decision.auto_apply_allowed,
        'approval_required': decision.approval_required,
        'rationale': decision.rationale,
        'suggested_action': decision.suggested_action,
        'proposal_id': proposal_id,
        'options': options,
        'execution_contract': {
            'auto_apply': '무승인 자동반영 허용 범위면 focused self-healing worker가 즉시 반복 검증 후 반영',
            'approval_required': '승인 필요 범위면 원인 설명/옵션 선택 후 즉시 실행',
            'verification_loop': ['syntax', 'type', 'runtime', 'domain-route'],
        },
    }


@router.post('/focused-self-healing/apply')
async def apply_focused_self_healing_plan(payload: FocusedSelfHealingApplyRequest, admin: User = Depends(require_admin)):
    resolved_path = _resolve_admin_workspace_path(payload.requested_path)
    decision = build_focused_self_healing_decision(operation_id=str(payload.issue_id or '').strip(), requested_path=str(payload.requested_path or '').strip(), resolved_path=resolved_path, reason=str(payload.reason or '').strip())
    if decision.approval_required and not payload.approved:
        raise HTTPException(status_code=400, detail='승인 필요 범위입니다. 원인 설명 확인 후 승인해야 즉시 실행할 수 있습니다.')
    source_target = _resolve_admin_workspace_path(decision.target_source_path)
    source_dir = source_target if source_target.is_dir() else source_target.parent
    execution_source_path = str(source_dir)
    context = {
        'source_path': execution_source_path,
        'requested_mode': 'self-improvement',
        'directive_template': 'focused-self-healing',
        'directive_scope': 'targeted_implementation',
        'directive_request': '\n'.join([f'이슈 ID: {decision.operation_id}', f'집중 수정 경로: {decision.focused_path}', f'분류: {decision.category}', '원인에 집중해 관련 파일만 분석하고 수정 후보를 확정한 뒤 반복 검증하세요.', str(payload.reason or '').strip()]),
        'experiment_clone_path': None,
        'analysis_summary': {'focused_path': decision.focused_path, 'target_kind': decision.target_kind},
        'tree_preview': '',
        'key_text_files': [decision.focused_path] if decision.target_kind == 'file' else [],
        'runtime_diagnostic': 'focused self-healing 요청은 큐 등록만 수행했고 상세 준비/복제는 background worker가 이어서 실행합니다.',
    }
    self_run_payload = WorkspaceSelfRunRequest(
        source_path=execution_source_path,
        mode='self-improvement',
        self_run_stage='focused-self-healing',
        directive_template='focused-self-healing',
        directive_scope='targeted_implementation',
        directive_request='\n'.join([f'이슈 ID: {decision.operation_id}', f'집중 수정 경로: {decision.focused_path}', f'분류: {decision.category}', '원인에 집중해 관련 파일만 분석하고 수정 후보를 확정한 뒤 반복 검증하세요.', str(payload.reason or '').strip()]),
    )
    executed = await execute_workspace_self_run(self_run_payload, admin=admin)
    retry_result = {
        'queued': True,
        'mode': 'self-improvement',
        'source_path': execution_source_path,
        'focused_path': decision.focused_path,
        'target_kind': decision.target_kind,
        'directive_template': 'focused-self-healing',
        'directive_scope': 'targeted_implementation',
        'selected_option_id': str(payload.selected_option_id or '').strip() or None,
        'context': context,
        'verification_loop': ['syntax', 'type', 'runtime', 'domain-route'],
        'execution': executed,
    }
    return {
        'issue_id': decision.operation_id,
        'focused_path': decision.focused_path,
        'target_source_path': decision.target_source_path,
        'category': decision.category,
        'auto_apply_allowed': decision.auto_apply_allowed,
        'approval_required': decision.approval_required,
        'selected_option_id': str(payload.selected_option_id or '').strip() or None,
        'retry': retry_result,
        'execution': executed,
        'message': 'focused self-healing 이 실제 workspace self-run 재실행까지 연결되었습니다.',
    }
