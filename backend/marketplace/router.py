from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile, Request, Response
from fastapi.responses import StreamingResponse, FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import text, inspect, func
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel
from pathlib import Path
from datetime import datetime, timedelta, timezone
import asyncio
import logging
import os
import io
import zipfile
import re
import json
import queue
import threading
import subprocess
import tempfile
import time
import shutil
import socket
import requests
import hashlib
import base64
import binascii
from urllib.parse import quote, urlparse
from uuid import uuid4

from redis import Redis
from redis.exceptions import RedisError
from . import models, schemas, crud
from .ad_strategy_engine import plan_ad_strategy
from .audience_profile_engine import infer_audience_profiles
from .campaign_orchestrator_engine import plan_local_campaign
from .caption_engine import build_caption_package
from .creative_variant_engine import build_creative_variants
from .ffmpeg_render_executor import render_final_video
from .image_generation_engine import run_image_generation_engine
from .image_to_video_pipeline import run_image_to_video_pipeline
from .local_designer_engine import render_local_designer_sequence
from .local_video_connector import plan_local_video_connector
from .platform_formatter import build_platform_formats
from .self_run_video_worker import enqueue_self_run_video_job, get_self_run_video_job, get_self_run_video_worker_status
from .story_state_engine import build_story_states
from .video_generation_engine import run_video_generation_engine
from .database import get_db, engine, SessionLocal
from .feature_orchestrator.engines.spreadsheet_generation_engine import (
    build_spreadsheet_preview,
    render_spreadsheet_final,
    review_spreadsheet_quality,
)
from .minio_service import minio_service
from .payment_service import payment_service, download_token_service
from backend.auth import get_current_user
from backend.orchestration_stage_service import (
    ORCHESTRATION_STAGE_DEFINITIONS,
    build_stage_tracking_payload,
    initialize_stage_run,
    load_stage_run,
    save_stage_run,
    update_stage_run,
)
from backend.orchestrator.chat import AutoConnectMeta, OrchestratorChatRequest, OrchestratorChatResponse, OrchestratorStageChatContext
import secrets

logger = logging.getLogger(__name__)
_MARKETPLACE_CATEGORIES_CACHE_TTL_SEC = max(1.0, float(os.getenv("MARKETPLACE_CATEGORIES_CACHE_TTL_SEC", "5")))
_MARKETPLACE_CATEGORIES_CACHE: Dict[str, Any] = {
    "captured_at": 0.0,
    "payload": None,
}
_MARKETPLACE_CATEGORIES_CACHE_LOCK = threading.Lock()
_MARKETPLACE_CATEGORIES_RATE_LIMIT_WINDOW_SEC = max(0.2, float(os.getenv("MARKETPLACE_CATEGORIES_RATE_LIMIT_WINDOW_SEC", "1.0")))
_MARKETPLACE_CATEGORIES_RATE_LIMIT_STATE: Dict[str, float] = {}
_MARKETPLACE_CATEGORIES_RATE_LIMIT_LOCK = threading.Lock()


def _invalidate_marketplace_categories_cache() -> None:
    with _MARKETPLACE_CATEGORIES_CACHE_LOCK:
        _MARKETPLACE_CATEGORIES_CACHE["captured_at"] = 0.0
        _MARKETPLACE_CATEGORIES_CACHE["payload"] = None


def _resolve_marketplace_categories_rate_limit_key(request: Request) -> str:
    forwarded_for = str(request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    client_host = forwarded_for or (request.client.host if request.client else "unknown")
    user_agent = str(request.headers.get("user-agent") or "")
    return f"{client_host}:{hashlib.sha256(user_agent.encode('utf-8')).hexdigest()[:16]}"


def _should_throttle_marketplace_categories(request: Request) -> bool:
    now_ts = time.time()
    rate_limit_key = _resolve_marketplace_categories_rate_limit_key(request)
    with _MARKETPLACE_CATEGORIES_RATE_LIMIT_LOCK:
        last_seen = float(_MARKETPLACE_CATEGORIES_RATE_LIMIT_STATE.get(rate_limit_key) or 0.0)
        _MARKETPLACE_CATEGORIES_RATE_LIMIT_STATE[rate_limit_key] = now_ts
        stale_keys = [key for key, seen_at in _MARKETPLACE_CATEGORIES_RATE_LIMIT_STATE.items() if (now_ts - float(seen_at)) > (_MARKETPLACE_CATEGORIES_RATE_LIMIT_WINDOW_SEC * 20)]
        for stale_key in stale_keys:
            _MARKETPLACE_CATEGORIES_RATE_LIMIT_STATE.pop(stale_key, None)
    return (now_ts - last_seen) < _MARKETPLACE_CATEGORIES_RATE_LIMIT_WINDOW_SEC


def _build_marketplace_categories_degraded_payload(cached_payload: Any = None) -> List[Dict[str, Any]]:
    if isinstance(cached_payload, list):
        return list(cached_payload)
    return []


def _apply_short_marketplace_categories_cache_headers(response: StreamingResponse | Any) -> None:
    ttl = max(1, int(_MARKETPLACE_CATEGORIES_CACHE_TTL_SEC))
    response.headers["Cache-Control"] = f"public, max-age={ttl}, stale-while-revalidate=30"
    response.headers["x-stale-client-mitigation"] = "marketplace-categories-short-cache"


def _apply_marketplace_categories_degraded_headers(response: Response, *, mitigation: str) -> None:
    _apply_short_marketplace_categories_cache_headers(response)
    response.headers["Connection"] = "close"
    response.headers["x-stale-client-mitigation"] = mitigation
    response.headers["x-marketplace-categories-degraded"] = "1"


async def _run_async_request_in_thread(coro):
    return await asyncio.to_thread(lambda: asyncio.run(coro))

MARKETPLACE_QUALITY_PASS_SCORE = 70.0
MARKETPLACE_MAX_AUTO_QUALITY_RETRIES = 1

_video_queue_redis_cache: Optional[Redis] = None
_video_queue_redis_cache_url: str = ""
_video_queue_redis_cache_checked_at: float = 0.0
_VIDEO_QUEUE_REDIS_CACHE_TTL_SEC = 5.0

router = APIRouter()


class FeatureOrchestrateAcceptedRequest(BaseModel):
    feature_id: str
    project_name: str
    prompt: str
    template_id: Optional[str] = None
    photo_reference: Optional[str] = None
    photo_content_type: Optional[str] = None
    photo_size: Optional[int] = None
    final_enabled: bool = True
    context_tags: List[str] = []


class FeatureOrchestrateStreamRequest(BaseModel):
    run_id: str


class FeatureOrchestrateAcceptedResponse(BaseModel):
    accepted: bool
    run_id: str
    stage_run: Dict[str, Any]
    status: str
    stream_url: str
    poll_url: str


class FeatureOrchestratorRuntimeService:
    def get_catalog(self) -> List[Dict[str, Any]]:
        return list(_FEATURE_CATALOG)

    def get_service(self, feature_id: str) -> "FeatureOrchestratorRuntimeService":
        normalized = str(feature_id or "").strip().lower()
        if normalized != "ai-sheet":
            raise ValueError("지원하지 않는 feature_id 입니다.")
        return self

    def run_preview_phase(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return build_spreadsheet_preview(payload)

    def run_final_phase(self, payload: Dict[str, Any], preview_artifact: Dict[str, Any]) -> Dict[str, Any]:
        return render_spreadsheet_final(payload, preview_artifact)

    def run_quality_gate(
        self,
        payload: Dict[str, Any],
        preview_artifact: Dict[str, Any],
        final_artifact: Dict[str, Any],
    ) -> Dict[str, Any]:
        return review_spreadsheet_quality(payload, preview_artifact, final_artifact)

    def build_artifact_manifest(
        self,
        preview_artifact: Dict[str, Any],
        final_artifact: Dict[str, Any],
        quality_review: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "preview_artifact": preview_artifact,
            "final_artifact": final_artifact,
            "quality_review": quality_review,
        }


_FEATURE_CATALOG: List[Dict[str, Any]] = [
    {
        "feature_id": "ai-sheet",
        "title": "AI 엑셀 시트",
        "summary": "프롬프트 기반으로 시트 schema preview 와 최종 workbook 패키지를 생성합니다.",
        "popup_mode": "spreadsheet-builder",
        "status": "ready",
        "supports_photo_upload": False,
        "supports_final_phase": True,
    },
]

_feature_runtime_service = FeatureOrchestratorRuntimeService()

_FEATURE_POPUP_STAGE_MAP = {
    "accepted": "ARCH-001",
    "preview_running": "ARCH-007",
    "preview_ready": "ARCH-008",
    "final_running": "ARCH-009",
    "quality_review": "ARCH-010",
    "completed": "ARCH-010",
    "completed_preview_only": "ARCH-010",
    "failed": "ARCH-010",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _build_feature_sse_event(event: str, payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps({'event': event, 'payload': payload}, ensure_ascii=False)}\n\n"


def _build_feature_progress_payload(
    run_id: str,
    *,
    percent: int,
    step: str,
    state: str,
    message: str,
) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "state": state,
        "progress": {
            "percent": max(0, min(100, int(percent))),
            "step": step,
            "message": message,
            "updated_at": _utc_now_iso(),
        },
    }


def _get_feature_stage_run_or_404(run_id: str) -> Dict[str, Any]:
    payload = load_stage_run(run_id)
    if not payload:
        raise HTTPException(status_code=404, detail="feature stage run을 찾을 수 없습니다.")
    return payload


def _get_feature_metadata(stage_run_payload: Dict[str, Any]) -> Dict[str, Any]:
    metadata = dict(stage_run_payload.get("metadata") or {})
    return dict(metadata.get("feature_orchestrator") or {})


def _set_feature_metadata(stage_run_payload: Dict[str, Any], feature_metadata: Dict[str, Any]) -> Dict[str, Any]:
    metadata = dict(stage_run_payload.get("metadata") or {})
    metadata["feature_orchestrator"] = feature_metadata
    stage_run_payload["metadata"] = metadata
    return stage_run_payload


def _apply_feature_popup_state(stage_run_payload: Dict[str, Any], popup_state: str, note: str = "") -> Dict[str, Any]:
    target_stage_id = _FEATURE_POPUP_STAGE_MAP.get(popup_state, "ARCH-010")
    ordered_ids = [item["id"] for item in ORCHESTRATION_STAGE_DEFINITIONS]
    target_index = ordered_ids.index(target_stage_id) if target_stage_id in ordered_ids else len(ordered_ids) - 1
    now = _utc_now_iso()
    for index, stage in enumerate(stage_run_payload.get("stages") or []):
        if index < target_index:
            stage["status"] = "passed"
            stage["check_label"] = "통과"
        elif index == target_index:
            if popup_state == "failed":
                stage["status"] = "failed"
                stage["check_label"] = "미통과"
            elif popup_state in {"completed", "completed_preview_only"}:
                stage["status"] = "passed"
                stage["check_label"] = "통과"
            else:
                stage["status"] = "running"
                stage["check_label"] = "진행 중"
            if note:
                stage["note"] = note
        else:
            stage["status"] = "pending"
            stage["check_label"] = "대기"
        stage["updated_at"] = now
    stage_run_payload["current_stage_id"] = target_stage_id
    stage_run_payload["status"] = "blocked" if popup_state == "failed" else "completed" if popup_state in {"completed", "completed_preview_only"} else "running"
    stage_run_payload["final_completed"] = popup_state == "completed"
    return stage_run_payload


def _iter_feature_artifacts(feature_metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    artifacts: List[Dict[str, Any]] = []
    for key in ("preview_artifact", "final_artifact"):
        artifact = feature_metadata.get(key)
        if isinstance(artifact, dict):
            artifacts.append(dict(artifact))
    manifest = feature_metadata.get("artifact_manifest")
    if isinstance(manifest, dict):
        for key in ("preview_artifact", "final_artifact"):
            artifact = manifest.get(key)
            if isinstance(artifact, dict):
                artifacts.append(dict(artifact))
    return artifacts


def _resolve_feature_delivery_asset_or_404(run_id: str, asset_format: str) -> Dict[str, Any]:
    stage_run = _get_feature_stage_run_or_404(run_id)
    feature_metadata = _get_feature_metadata(stage_run)
    normalized_format = str(asset_format or "").strip().lower()
    if not normalized_format:
        raise HTTPException(status_code=400, detail="asset format 이 필요합니다.")

    for artifact in _iter_feature_artifacts(feature_metadata):
        for asset in list(artifact.get("delivery_assets") or []):
            if str(asset.get("format") or "").strip().lower() != normalized_format:
                continue
            asset_path = Path(str(asset.get("path") or "")).expanduser()
            if not asset_path.exists() or not asset_path.is_file():
                raise HTTPException(status_code=404, detail="delivery asset 파일이 존재하지 않습니다.")
            return {
                "artifact": artifact,
                "asset": asset,
                "path": asset_path.resolve(),
            }

    raise HTTPException(status_code=404, detail="요청한 delivery asset 을 찾을 수 없습니다.")

@router.get("/projects/{project_id}", response_model=schemas.Project)
def get_marketplace_project(
    project_id: int,
    db: Session = Depends(get_db),
):
    project = crud.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다")
    return project


@router.get("/categories")
def list_marketplace_categories(request: Request, response: Response, db: Session = Depends(get_db)):
    _apply_short_marketplace_categories_cache_headers(response)
    now_ts = time.time()
    cached_payload = _MARKETPLACE_CATEGORIES_CACHE.get("payload")
    cached_at = float(_MARKETPLACE_CATEGORIES_CACHE.get("captured_at") or 0.0)
    if _should_throttle_marketplace_categories(request):
        response.headers["Retry-After"] = str(max(1, int(_MARKETPLACE_CATEGORIES_RATE_LIMIT_WINDOW_SEC)))
        _apply_marketplace_categories_degraded_headers(response, mitigation="marketplace-categories-degraded-cache")
        return _build_marketplace_categories_degraded_payload(cached_payload)
    if cached_payload is not None and (now_ts - cached_at) < _MARKETPLACE_CATEGORIES_CACHE_TTL_SEC:
        return cached_payload
    with _MARKETPLACE_CATEGORIES_CACHE_LOCK:
        now_ts = time.time()
        cached_payload = _MARKETPLACE_CATEGORIES_CACHE.get("payload")
        cached_at = float(_MARKETPLACE_CATEGORIES_CACHE.get("captured_at") or 0.0)
        if cached_payload is not None and (now_ts - cached_at) < _MARKETPLACE_CATEGORIES_CACHE_TTL_SEC:
            return cached_payload
        categories = (
            db.query(models.Category)
            .order_by(models.Category.name.asc())
            .all()
        )
        payload = [
            {
                "id": int(category.id),
                "name": str(category.name or ""),
                "description": getattr(category, "description", None),
            }
            for category in categories
        ]
        _MARKETPLACE_CATEGORIES_CACHE["captured_at"] = now_ts
        _MARKETPLACE_CATEGORIES_CACHE["payload"] = payload
        return payload


@router.get("/feature-catalog")
def get_marketplace_feature_catalog() -> List[Dict[str, Any]]:
    return _feature_runtime_service.get_catalog()


@router.post(
    "/feature-orchestrate/accepted",
    response_model=FeatureOrchestrateAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def accept_marketplace_feature_orchestration(
    request: FeatureOrchestrateAcceptedRequest,
) -> FeatureOrchestrateAcceptedResponse:
    request_payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
    feature_id = str(request_payload.get("feature_id") or "").strip()
    service = _feature_runtime_service.get_service(feature_id)

    stage_run = initialize_stage_run(
        scope="marketplace-feature-orchestrator",
        project_name=str(request_payload.get("project_name") or feature_id or "marketplace-feature-run"),
        mode="feature-popup",
        metadata={},
    )
    feature_metadata = {
        "feature_id": feature_id,
        "popup_state": "accepted",
        "request": request_payload,
        "artifact_manifest": {
            "preview_artifact_id": None,
            "final_artifact_id": None,
        },
        "last_event": None,
        "updated_at": _utc_now_iso(),
        "service": service.__class__.__name__,
    }
    stage_run = _set_feature_metadata(stage_run, feature_metadata)
    stage_run = _apply_feature_popup_state(stage_run, "accepted")
    stage_run = save_stage_run(stage_run)
    return FeatureOrchestrateAcceptedResponse(
        accepted=True,
        run_id=str(stage_run.get("run_id") or ""),
        stage_run=stage_run,
        status="accepted",
        stream_url="/api/marketplace/feature-orchestrate/stream",
        poll_url=f"/api/marketplace/feature-orchestrate/stage-runs/{stage_run.get('run_id')}",
    )


@router.post("/feature-orchestrate/stream")
async def stream_marketplace_feature_orchestration(
    request: FeatureOrchestrateStreamRequest,
) -> StreamingResponse:
    stage_run = _get_feature_stage_run_or_404(request.run_id)
    feature_metadata = _get_feature_metadata(stage_run)
    feature_id = str(feature_metadata.get("feature_id") or "").strip()
    request_payload = dict(feature_metadata.get("request") or {})
    if not feature_id or not request_payload:
        raise HTTPException(status_code=400, detail="feature orchestrator 요청 메타데이터가 없습니다.")
    service = _feature_runtime_service.get_service(feature_id)

    async def event_stream():
        local_stage_run = _get_feature_stage_run_or_404(request.run_id)
        local_metadata = _get_feature_metadata(local_stage_run)

        def _persist_progress(*, percent: int, step: str, state: str, message: str) -> None:
            progress_payload = {
                "percent": max(0, min(100, int(percent))),
                "step": step,
                "state": state,
                "message": message,
                "updated_at": _utc_now_iso(),
            }
            local_metadata["progress"] = progress_payload
            local_metadata["updated_at"] = progress_payload["updated_at"]

        try:
            local_metadata["popup_state"] = "preview_running"
            local_metadata["last_event"] = "preview_running"
            local_metadata["updated_at"] = _utc_now_iso()
            _persist_progress(percent=10, step="preview_started", state="preview_running", message="preview 생성 단계를 시작했습니다.")
            local_stage_run = _set_feature_metadata(local_stage_run, local_metadata)
            local_stage_run = _apply_feature_popup_state(local_stage_run, "preview_running")
            save_stage_run(local_stage_run)
            yield _build_feature_sse_event("state", {"run_id": request.run_id, "state": "preview_running"})
            yield _build_feature_sse_event(
                "progress",
                _build_feature_progress_payload(
                    request.run_id,
                    percent=10,
                    step="preview_started",
                    state="preview_running",
                    message="preview 생성 단계를 시작했습니다.",
                ),
            )

            preview_artifact = await asyncio.to_thread(service.run_preview_phase, request_payload)
            local_metadata["popup_state"] = "preview_ready"
            local_metadata["preview_artifact"] = preview_artifact
            local_metadata["artifact_manifest"] = {
                **dict(local_metadata.get("artifact_manifest") or {}),
                "preview_artifact_id": preview_artifact.get("artifact_id"),
            }
            local_metadata["last_event"] = "preview_ready"
            local_metadata["updated_at"] = _utc_now_iso()
            _persist_progress(percent=45, step="preview_ready", state="preview_ready", message="preview 결과가 준비되었습니다.")
            local_stage_run = _set_feature_metadata(local_stage_run, local_metadata)
            local_stage_run = _apply_feature_popup_state(local_stage_run, "preview_ready")
            save_stage_run(local_stage_run)
            yield _build_feature_sse_event("artifact", {"run_id": request.run_id, "state": "preview_ready", "artifact": preview_artifact})
            yield _build_feature_sse_event(
                "progress",
                _build_feature_progress_payload(
                    request.run_id,
                    percent=45,
                    step="preview_ready",
                    state="preview_ready",
                    message="preview 결과가 준비되었습니다.",
                ),
            )

            if not bool(request_payload.get("final_enabled", True)):
                final_artifact = await asyncio.to_thread(service.run_final_phase, request_payload, preview_artifact)
                quality_review = await asyncio.to_thread(service.run_quality_gate, request_payload, preview_artifact, final_artifact)
                manifest = service.build_artifact_manifest(preview_artifact, final_artifact, quality_review)
                local_metadata["popup_state"] = "completed_preview_only"
                local_metadata["final_artifact"] = final_artifact
                local_metadata["quality_review"] = quality_review
                local_metadata["artifact_manifest"] = manifest
                local_metadata["last_event"] = "completed_preview_only"
                local_metadata["updated_at"] = _utc_now_iso()
                _persist_progress(percent=100, step="completed_preview_only", state="completed_preview_only", message="preview 전용 라이브뷰 실행이 완료되었습니다.")
                local_stage_run = _set_feature_metadata(local_stage_run, local_metadata)
                local_stage_run = _apply_feature_popup_state(local_stage_run, "completed_preview_only")
                save_stage_run(local_stage_run)
                yield _build_feature_sse_event("completed", {"run_id": request.run_id, "state": "completed_preview_only", "artifact_manifest": manifest, "quality_review": quality_review})
                yield _build_feature_sse_event(
                    "progress",
                    _build_feature_progress_payload(
                        request.run_id,
                        percent=100,
                        step="completed_preview_only",
                        state="completed_preview_only",
                        message="preview 전용 라이브뷰 실행이 완료되었습니다.",
                    ),
                )
                return

            local_metadata["popup_state"] = "final_running"
            local_metadata["last_event"] = "final_running"
            local_metadata["updated_at"] = _utc_now_iso()
            _persist_progress(percent=65, step="final_started", state="final_running", message="final 렌더 단계를 시작했습니다.")
            local_stage_run = _set_feature_metadata(local_stage_run, local_metadata)
            local_stage_run = _apply_feature_popup_state(local_stage_run, "final_running")
            save_stage_run(local_stage_run)
            yield _build_feature_sse_event("state", {"run_id": request.run_id, "state": "final_running"})
            yield _build_feature_sse_event(
                "progress",
                _build_feature_progress_payload(
                    request.run_id,
                    percent=65,
                    step="final_started",
                    state="final_running",
                    message="final 렌더 단계를 시작했습니다.",
                ),
            )

            final_artifact = await asyncio.to_thread(service.run_final_phase, request_payload, preview_artifact)
            quality_review = await asyncio.to_thread(service.run_quality_gate, request_payload, preview_artifact, final_artifact)
            manifest = service.build_artifact_manifest(preview_artifact, final_artifact, quality_review)
            completed_state = "completed" if bool(quality_review.get("passed")) else "completed_preview_only"

            local_metadata["popup_state"] = "quality_review"
            local_metadata["quality_review"] = quality_review
            local_metadata["updated_at"] = _utc_now_iso()
            _persist_progress(percent=85, step="quality_review", state="quality_review", message="quality gate 결과를 정리하고 있습니다.")
            local_stage_run = _set_feature_metadata(local_stage_run, local_metadata)
            local_stage_run = _apply_feature_popup_state(local_stage_run, "quality_review")
            save_stage_run(local_stage_run)
            yield _build_feature_sse_event("quality_review", {"run_id": request.run_id, "state": "quality_review", "quality_review": quality_review})
            yield _build_feature_sse_event(
                "progress",
                _build_feature_progress_payload(
                    request.run_id,
                    percent=85,
                    step="quality_review",
                    state="quality_review",
                    message="quality gate 결과를 정리하고 있습니다.",
                ),
            )

            local_metadata["popup_state"] = completed_state
            local_metadata["final_artifact"] = final_artifact
            local_metadata["artifact_manifest"] = manifest
            local_metadata["last_event"] = completed_state
            local_metadata["updated_at"] = _utc_now_iso()
            _persist_progress(percent=100, step="completed", state=completed_state, message="라이브뷰 실행이 완료되었습니다.")
            local_stage_run = _set_feature_metadata(local_stage_run, local_metadata)
            local_stage_run = _apply_feature_popup_state(local_stage_run, completed_state)
            save_stage_run(local_stage_run)
            yield _build_feature_sse_event("completed", {"run_id": request.run_id, "state": completed_state, "artifact_manifest": manifest, "quality_review": quality_review})
            yield _build_feature_sse_event(
                "progress",
                _build_feature_progress_payload(
                    request.run_id,
                    percent=100,
                    step="completed",
                    state=completed_state,
                    message="라이브뷰 실행이 완료되었습니다.",
                ),
            )
        except Exception as exc:
            local_metadata["popup_state"] = "failed"
            local_metadata["last_event"] = "failed"
            local_metadata["error"] = str(exc)
            local_metadata["updated_at"] = _utc_now_iso()
            _persist_progress(percent=100, step="failed", state="failed", message=str(exc))
            local_stage_run = _set_feature_metadata(local_stage_run, local_metadata)
            local_stage_run = _apply_feature_popup_state(local_stage_run, "failed", str(exc))
            save_stage_run(local_stage_run)
            yield _build_feature_sse_event("failed", {"run_id": request.run_id, "state": "failed", "message": str(exc)})
            yield _build_feature_sse_event(
                "progress",
                _build_feature_progress_payload(
                    request.run_id,
                    percent=100,
                    step="failed",
                    state="failed",
                    message=str(exc),
                ),
            )

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/feature-orchestrate/stage-runs/{run_id}")
def get_marketplace_feature_stage_run(run_id: str) -> Dict[str, Any]:
    return _get_feature_stage_run_or_404(run_id)


@router.post("/categories")
def create_marketplace_category(
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if not (getattr(current_user, "is_admin", False) or getattr(current_user, "is_superuser", False)):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    name = str(payload.get("name") or "").strip()
    description = str(payload.get("description") or "").strip() or None
    if not name:
        raise HTTPException(status_code=400, detail="카테고리 이름이 필요합니다.")
    existing = db.query(models.Category).filter(models.Category.name == name).first()
    if existing:
        raise HTTPException(status_code=400, detail="이미 존재하는 카테고리입니다.")
    category = models.Category(name=name, description=description)
    db.add(category)
    db.commit()
    db.refresh(category)
    _invalidate_marketplace_categories_cache()
    return {"id": int(category.id), "name": str(category.name or ""), "description": category.description}


@router.put("/categories/{category_id}")
def update_marketplace_category(
    category_id: int,
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if not (getattr(current_user, "is_admin", False) or getattr(current_user, "is_superuser", False)):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    category = db.query(models.Category).filter(models.Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="카테고리를 찾을 수 없습니다.")
    next_name = str(payload.get("name") or category.name or "").strip()
    if not next_name:
        raise HTTPException(status_code=400, detail="카테고리 이름이 필요합니다.")
    category.name = next_name
    category.description = str(payload.get("description") or "").strip() or None
    db.add(category)
    db.commit()
    db.refresh(category)
    _invalidate_marketplace_categories_cache()
    return {"id": int(category.id), "name": str(category.name or ""), "description": category.description}


@router.delete("/categories/{category_id}")
def delete_marketplace_category(
    category_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if not (getattr(current_user, "is_admin", False) or getattr(current_user, "is_superuser", False)):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    category = db.query(models.Category).filter(models.Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="카테고리를 찾을 수 없습니다.")
    db.delete(category)
    db.commit()
    _invalidate_marketplace_categories_cache()
    return {"id": category_id, "name": str(getattr(category, "name", "") or "")}


def _compose_trace_fields(flow_id: str, step_id: str, action: str) -> Dict[str, str]:
    return {
        "flow_id": flow_id,
        "step_id": step_id,
        "action": action,
        "trace_id": f"{flow_id}:{step_id}:{action}",
    }


def _write_feature_execution_log(
    db: Session,
    *,
    user_id: Optional[int],
    entity_type: str,
    entity_id: str,
    flow_id: str,
    step_id: str,
    action: str,
    status: str,
    message: str,
    payload: Optional[Dict[str, Any]] = None,
) -> models.FeatureExecutionLog:
    trace_fields = _compose_trace_fields(flow_id, step_id, action)
    row = models.FeatureExecutionLog(
        user_id=user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        flow_id=trace_fields["flow_id"],
        step_id=trace_fields["step_id"],
        action=trace_fields["action"],
        trace_id=trace_fields["trace_id"],
        status=status,
        message=message,
        payload_json=(json.dumps(payload, ensure_ascii=False) if payload is not None else None),
    )
    db.add(row)
    db.flush()


def _enqueue_feature_retry_record(
    db: Session,
    *,
    user_id: Optional[int],
    entity_type: str,
    entity_id: str,
    flow_id: str,
    step_id: str,
    action: str,
    queue_name: str,
    payload: Optional[Dict[str, Any]] = None,
    last_error: Optional[str] = None,
    status: str = "queued",
    attempt_count: int = 0,
) -> models.FeatureRetryQueue:
    trace_fields = _compose_trace_fields(flow_id, step_id, action)
    row = models.FeatureRetryQueue(
        user_id=user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        flow_id=trace_fields["flow_id"],
        step_id=trace_fields["step_id"],
        action=trace_fields["action"],
        trace_id=trace_fields["trace_id"],
        queue_name=queue_name,
        status=status,
        payload_json=(json.dumps(payload, ensure_ascii=False) if payload is not None else None),
        last_error=last_error,
        attempt_count=attempt_count,
    )
    db.add(row)
    db.flush()
    return row


def _resolve_frontend_origin(request: Request) -> str:
    configured = (os.getenv("FRONTEND_PUBLIC_URL", "") or "").strip().rstrip("/")
    if configured:
        return configured

    origin = (request.headers.get("origin", "") or "").strip().rstrip("/")
    if origin:
        return origin

    host = (request.headers.get("host", "") or "").strip()
    if host:
        if host.startswith("127.0.0.1:8000") or host.startswith("localhost:8000"):
            return "http://localhost:3000"
        scheme = request.headers.get("x-forwarded-proto") or request.url.scheme or "http"
        return f"{scheme}://{host}".rstrip("/")

    return "http://localhost:3000"

# 초기 데이터 생성 (첫 요청 시)
_initialized = False


def _ensure_ad_video_orders_schema() -> None:
    inspector = inspect(engine)
    if not inspector.has_table("ad_video_orders"):
        return

    statements = [
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS title VARCHAR(200)",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS image_prompt TEXT",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS portrait_image_prompt TEXT",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS product_image_prompts TEXT",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS storyboard_json TEXT",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS storyboard_review_json TEXT",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS storyboard_review_history_json TEXT",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS subject_type VARCHAR(30)",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS background_prompt TEXT",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS caption_text TEXT",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS scenario_script TEXT",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS voice_gender VARCHAR(20)",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS engine_type VARCHAR(30)",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS action_template_key VARCHAR(100)",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS motion_tempo VARCHAR(20)",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS duration_seconds INTEGER",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS visual_style VARCHAR(100)",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS cut_count INTEGER",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS subtitle_speed DOUBLE PRECISION",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS render_quality VARCHAR(20)",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS audio_volume INTEGER",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS status VARCHAR(20)",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS progress_percent INTEGER",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS external_job_id VARCHAR(255)",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS output_file_key VARCHAR(500)",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS output_filename VARCHAR(255)",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS output_video_key VARCHAR(500)",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS output_video_filename VARCHAR(255)",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS quality_score DOUBLE PRECISION",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS quality_gate_passed BOOLEAN",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS quality_feedback TEXT",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS face_consistency_score DOUBLE PRECISION",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS product_consistency_score DOUBLE PRECISION",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS sales_quality_decision VARCHAR(30)",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS quality_retry_count INTEGER",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS quality_checked_at TIMESTAMP",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS public_job_id VARCHAR(36)",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS error_message TEXT",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS download_count INTEGER",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS created_at TIMESTAMP",
        "ALTER TABLE ad_video_orders ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP",
        (
            "DO $$ BEGIN "
            "IF EXISTS ("
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name='ad_video_orders' AND column_name='product_name'"
            ") THEN "
            "ALTER TABLE ad_video_orders ALTER COLUMN product_name DROP NOT NULL; "
            "END IF; END $$"
        ),
        (
            "DO $$ BEGIN "
            "IF EXISTS ("
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name='ad_video_orders' AND column_name='concept'"
            ") THEN "
            "ALTER TABLE ad_video_orders ALTER COLUMN concept DROP NOT NULL; "
            "END IF; END $$"
        ),
    ]

    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
        conn.execute(text(
            "UPDATE ad_video_orders SET engine_type='internal_ffmpeg' WHERE engine_type IS NULL"
        ))
        conn.execute(text(
            "UPDATE ad_video_orders SET progress_percent=0 WHERE progress_percent IS NULL"
        ))
        conn.execute(text(
            "UPDATE ad_video_orders SET cut_count=12 WHERE cut_count IS NULL"
        ))
        conn.execute(text(
            "UPDATE ad_video_orders SET quality_gate_passed=false WHERE quality_gate_passed IS NULL"
        ))
        conn.execute(text(
            "UPDATE ad_video_orders SET quality_retry_count=0 WHERE quality_retry_count IS NULL"
        ))
        conn.execute(text(
            "UPDATE ad_video_orders SET subject_type='auto' WHERE subject_type IS NULL"
        ))
        conn.execute(text(
            "UPDATE ad_video_orders SET subtitle_speed=1.0 WHERE subtitle_speed IS NULL"
        ))
        conn.execute(text(
            "UPDATE ad_video_orders SET render_quality='high' WHERE render_quality IS NULL"
        ))
        conn.execute(text(
            "UPDATE ad_video_orders SET audio_volume=100 WHERE audio_volume IS NULL"
        ))
        conn.execute(text(
            "UPDATE ad_video_orders SET download_count=0 WHERE download_count IS NULL"
        ))


def _ensure_video_service_user_schema() -> None:
    inspector = inspect(engine)
    if not inspector.has_table("users"):
        return

    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS credit_balance INTEGER"
        ))
        conn.execute(text(
            "UPDATE users SET credit_balance=10 WHERE credit_balance IS NULL"
        ))


def ensure_marketplace_runtime_schema() -> None:
    _ensure_ad_video_orders_schema()
    _ensure_video_service_user_schema()


_ad_enqueued_ids: set[int] = set()
VIDEO_RENDER_QUEUE_NAME = (
    os.getenv("VIDEO_RENDER_QUEUE_NAME", "video_render_queue")
    or "video_render_queue"
).strip()


def _get_video_queue_redis() -> Optional[Redis]:
    global _video_queue_redis_cache
    global _video_queue_redis_cache_url
    global _video_queue_redis_cache_checked_at

    redis_url = (os.getenv("REDIS_URL", "") or "").strip()
    if not redis_url:
        _video_queue_redis_cache = None
        _video_queue_redis_cache_url = ""
        _video_queue_redis_cache_checked_at = time.time()
        return None

    now = time.time()
    if (
        _video_queue_redis_cache_url == redis_url
        and (now - _video_queue_redis_cache_checked_at) < _VIDEO_QUEUE_REDIS_CACHE_TTL_SEC
    ):
        return _video_queue_redis_cache

    try:
        client = _video_queue_redis_cache
        if client is None or _video_queue_redis_cache_url != redis_url:
            client = Redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1,
                health_check_interval=30,
            )
        client.ping()
        _video_queue_redis_cache = client
        _video_queue_redis_cache_url = redis_url
        _video_queue_redis_cache_checked_at = now
        return client
    except RedisError:
        _video_queue_redis_cache = None
        _video_queue_redis_cache_url = redis_url
        _video_queue_redis_cache_checked_at = now
        return None


def _require_video_queue_redis() -> Redis:
    client = _get_video_queue_redis()
    if client is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Redis queue unavailable. video_render_queue is required by "
                "the video rendering spec."
            ),
        )
    return client


def _enqueue_ad_order(order_id: int, public_job_id: Optional[str] = None) -> bool:
    with _ad_worker_lock:
        if order_id in _ad_enqueued_ids:
            return False
        _ad_enqueued_ids.add(order_id)

    queue_item = {
        "order_id": order_id,
        "job_id": public_job_id or "",
        "status": models.AdVideoOrderStatus.PENDING.value,
        "queue": VIDEO_RENDER_QUEUE_NAME,
    }
    try:
        redis_client = _require_video_queue_redis()
        redis_client.lpush(VIDEO_RENDER_QUEUE_NAME, json.dumps(queue_item))
        return True
    except Exception:
        with _ad_worker_lock:
            _ad_enqueued_ids.discard(order_id)
        raise


def _recover_interrupted_ad_orders() -> int:
    db = SessionLocal()
    try:
        rows = (
            db.query(
                models.AdVideoOrder.id,
                models.AdVideoOrder.public_job_id,
            )
            .filter(
                models.AdVideoOrder.status.in_(
                    [
                        models.AdVideoOrderStatus.QUEUED.value,
                        models.AdVideoOrderStatus.PENDING.value,
                        models.AdVideoOrderStatus.PROCESSING.value,
                        models.AdVideoOrderStatus.RENDERING.value,
                    ]
                ),
                models.AdVideoOrder.output_video_key.is_(None),
            )
            .order_by(models.AdVideoOrder.created_at.asc())
            .all()
        )
    finally:
        db.close()

    recovered = 0
    for row in rows:
        order_id = int(row[0])
        public_job_id = str(getattr(row, "public_job_id", "") or "").strip() or None
        if _enqueue_ad_order(order_id, public_job_id):
            recovered += 1
    if recovered:
        logger.info("[marketplace] recovered %s interrupted ad order(s)", recovered)
    return recovered


def ensure_ad_order_runtime_ready() -> int:
    logger.info(
        "[marketplace][runtime_recovery] ensure_ad_order_runtime_ready invoked; "
        "runtime recovery stage owns Redis reconnect and interrupted order recovery"
    )
    _require_video_queue_redis()
    return _recover_interrupted_ad_orders()


def _mark_ad_worker_heartbeat(order_id: Optional[int] = None) -> None:
    now = time.time()
    with _ad_worker_lock:
        if _ad_worker_runtime["started_at"] is None:
            _ad_worker_runtime["started_at"] = now
        _ad_worker_runtime["last_heartbeat"] = now
        if order_id is not None:
            _ad_worker_runtime["last_order_id"] = order_id


def get_ad_queue_runtime_status() -> Dict[str, Dict[str, Any]]:
    connection_id = f"redis:{VIDEO_RENDER_QUEUE_NAME}"
    worker_id = str(_ad_worker_runtime.get("worker_id") or "ad-render-worker-001")
    worker_bootstrap_enabled = (os.getenv("ENABLE_AD_ORDER_WORKER_BOOTSTRAP", "true") or "true").strip().lower() in {"1", "true", "yes", "on"}

    redis_client = _get_video_queue_redis()
    queue_depth: Optional[int] = None
    redis_error: Optional[str] = None
    redis_available = redis_client is not None
    if redis_client is not None:
        try:
            queue_depth = int(redis_client.llen(VIDEO_RENDER_QUEUE_NAME))
        except RedisError as exc:
            redis_available = False
            redis_error = str(exc)

    with _ad_worker_lock:
        started_at = _ad_worker_runtime.get("started_at")
        last_heartbeat = _ad_worker_runtime.get("last_heartbeat")
        last_order_id = _ad_worker_runtime.get("last_order_id")

    now = time.time()
    heartbeat_age_sec: Optional[float] = None
    if isinstance(last_heartbeat, (int, float)):
        heartbeat_age_sec = round(max(0.0, now - float(last_heartbeat)), 1)
    worker_started = isinstance(started_at, (int, float))
    worker_alive = worker_started and heartbeat_age_sec is not None and heartbeat_age_sec <= 120.0

    redis_state = "ok" if redis_available else "warning"
    redis_note = (
        f"{connection_id} 연결 정상, queue={VIDEO_RENDER_QUEUE_NAME}, depth={queue_depth if queue_depth is not None else '-'}"
        if redis_available
        else "REDIS_URL 미설정 또는 Redis 연결 실패로 video_render_queue를 사용할 수 없습니다."
    )
    if not worker_bootstrap_enabled:
        worker_state = "ok"
        worker_note = "광고 주문 worker bootstrap이 비활성화되어 heartbeat를 생성하지 않습니다. 프로파일러/진단 실행에서는 정상입니다."
    else:
        worker_state = "ok" if worker_alive else "warning"
        worker_note = (
            f"worker_id={worker_id} heartbeat 정상, last_order_id={last_order_id or '-'}"
            if worker_alive
            else "광고 주문 worker heartbeat가 오래됐습니다. 장시간 렌더 중인지와 worker 루프 상태를 확인하세요."
        )

    return {
        "redis_queue": {
            "available": redis_available,
            "state": redis_state,
            "note": redis_note,
            "connection_id": connection_id,
            "queue_name": VIDEO_RENDER_QUEUE_NAME,
            "queue_depth": queue_depth,
            "worker_id": worker_id,
            "error": redis_error,
        },
        "ad_worker": {
            "available": worker_alive if worker_bootstrap_enabled else True,
            "state": worker_state,
            "note": worker_note,
            "connection_id": connection_id,
            "queue_name": VIDEO_RENDER_QUEUE_NAME,
            "queue_depth": queue_depth,
            "worker_id": worker_id,
            "bootstrap_enabled": worker_bootstrap_enabled,
            "started": worker_started,
            "heartbeat_age_sec": heartbeat_age_sec,
            "last_order_id": last_order_id,
        },
    }


class CustomerOrchestrateRequest(BaseModel):
    task: str
    mode: str = "auto"
    project_name: Optional[str] = None
    output_dir: Optional[str] = None
    allow_new_output_dir: bool = False
    refinement_request: Optional[str] = None
    max_improvement_cycles: int = 1
    stage_run_id: Optional[str] = None
    stage_id: Optional[str] = None
    manual_correction: Optional[str] = None


class CustomerOrchestrateStageUpdateRequest(BaseModel):
    run_id: str
    stage_id: str
    status: str
    note: str = ""
    manual_correction: str = ""
    substep_checks: Optional[Dict[str, bool]] = None
    revision_note: str = ""


class CustomerOrchestratorChatRequest(BaseModel):
    message: str
    task: str = ""
    conversation: List[Dict[str, Any]] = []
    run_id: Optional[str] = None
    stage_id: Optional[str] = None
    project_name: Optional[str] = None
    output_dir: Optional[str] = None
    project_memory: Dict[str, Any] = {}
    context_tags: List[str] = []
    conversation_mode: str = "auto"
    companion_mode: str = "hybrid"
    response_style: str = "balanced"
    max_tokens: int = 768


def _build_customer_stage_chat_context(stage_run: Optional[Dict[str, Any]], request: CustomerOrchestratorChatRequest) -> OrchestratorStageChatContext:
    active_stage: Dict[str, Any] = {}
    if isinstance(stage_run, dict):
        active_stage = next((stage for stage in (stage_run.get("stages") or []) if stage.get("id") == stage_run.get("current_stage_id")), {}) or {}
    return OrchestratorStageChatContext(
        run_id=str((stage_run or {}).get("run_id") or request.run_id or "") or None,
        stage_id=str((active_stage or {}).get("id") or request.stage_id or "") or None,
        stage_label=str((active_stage or {}).get("label") or "") or None,
        stage_title=str((active_stage or {}).get("title") or "") or None,
        stage_status=str((active_stage or {}).get("status") or (stage_run or {}).get("status") or "running") or None,
        scope=str((stage_run or {}).get("scope") or "marketplace") or None,
        project_name=str((stage_run or {}).get("project_name") or request.project_name or "") or None,
        pending_revision_note=str(request.message or "").strip() or None,
        last_command=(str(request.message or "").strip().split()[0] if str(request.message or "").strip().startswith("/") else None),
    )


_CUSTOMER_ORCHESTRATE_TRACKING_KEY_MAP = {
    "현재 아키텍처 단계 ID": "architecture_id",
    "active_flow_id": "flow_id",
    "active_step_id": "step_id",
    "active_action": "action",
    "next_architecture_id": "next_architecture_id",
    "next_flow_step_id": "next_step_id",
    "next_flow_action": "next_action",
}


def _extract_customer_orchestrate_tracking_context(task: str) -> Dict[str, Optional[str]]:
    tracking: Dict[str, Optional[str]] = {
        "architecture_id": None,
        "flow_id": None,
        "step_id": None,
        "action": None,
        "next_architecture_id": None,
        "next_step_id": None,
        "next_action": None,
    }
    for raw_line in str(task or "").splitlines():
        line = raw_line.strip()
        match = re.match(r"^-\s*([^:]+):\s*(.+)$", line)
        if not match:
            continue
        raw_key = match.group(1).strip()
        raw_value = match.group(2).strip()
        mapped_key = _CUSTOMER_ORCHESTRATE_TRACKING_KEY_MAP.get(raw_key)
        if not mapped_key:
            continue
        if raw_value in {"-", "END", ""}:
            tracking[mapped_key] = raw_value
        else:
            tracking[mapped_key] = raw_value
    return tracking


def _normalize_customer_orchestrate_result_payload(result: Any, task: str) -> Dict[str, Any]:
    if hasattr(result, "model_dump"):
        payload = result.model_dump()
    elif isinstance(result, dict):
        payload = dict(result)
    else:
        payload = {"result": result}

    tracking = _extract_customer_orchestrate_tracking_context(task)
    payload.update({key: value for key, value in tracking.items() if value})
    payload["marketplace_delivery_gate"] = {
        "product_ready": bool((payload.get("completion_judge") or {}).get("product_ready")),
        "packaging_ready": bool((payload.get("packaging_audit") or {}).get("packaging_ready")),
        "required_tests": list((payload.get("integration_test_plan") or {}).get("required_tests") or []),
        "marketplace_quality_aligned": bool(payload.get("completion_gate_ok")),
        "output_archive_path": payload.get("output_archive_path"),
        "shipping_readme_path": (payload.get("packaging_audit") or {}).get("shipping_readme_path"),
        "operations_guide_path": (payload.get("packaging_audit") or {}).get("operations_guide_path"),
        "integration_test_engine_ok": bool(((payload.get("completion_judge") or {}).get("integration_test_engine") or {}).get("ok")),
        "improvement_loop_enabled": bool((payload.get("completion_judge") or {}).get("improvement_loop_enabled")),
        "improvement_loop_strategy": list((payload.get("completion_judge") or {}).get("improvement_loop_strategy") or []),
        "improvement_loop": payload.get("improvement_loop") or {},
        "framework_e2e_validation": payload.get("framework_e2e_validation") or {},
        "external_integration_validation": payload.get("external_integration_validation") or {},
    }
    payload.setdefault("orchestration_stage_definitions", ORCHESTRATION_STAGE_DEFINITIONS)
    return payload


def _persist_customer_orchestrator_completion(
    db: Session,
    *,
    current_user: models.User,
    request: CustomerOrchestrateRequest,
    result_payload: Dict[str, Any],
) -> None:
    completion = models.CustomerOrchestratorCompletion(
        user_id=current_user.id,
        trace_id=str(result_payload.get("trace_id") or "") or None,
        flow_id=str(result_payload.get("flow_id") or "") or None,
        step_id=str(result_payload.get("step_id") or "") or None,
        action=str(result_payload.get("action") or "") or None,
        project_name=((request.project_name or "customer-product").strip() or "customer-product"),
        mode=str(request.mode or "auto").strip() or "auto",
        attempts=1,
        output_dir=str(result_payload.get("output_dir") or "") or None,
        postcheck_ok=result_payload.get("postcheck_ok"),
        gate_passed=bool((result_payload.get("completion_judge") or {}).get("product_ready")),
        override_used=False,
    )
    db.add(completion)
    _write_feature_execution_log(
        db,
        user_id=current_user.id,
        entity_type="customer_orchestrator_completion",
        entity_id=str(completion.project_name),
        flow_id=str(result_payload.get("flow_id") or "FLOW-001"),
        step_id=str(result_payload.get("step_id") or "FLOW-001-4"),
        action=str(result_payload.get("action") or "SAVE_COMPLETION"),
        status="saved",
        message="고객 오케스트레이터 completion 자동 저장",
        payload={
            "project_name": completion.project_name,
            "mode": completion.mode,
            "output_dir": completion.output_dir,
            "postcheck_ok": completion.postcheck_ok,
            "gate_passed": completion.gate_passed,
            "completion_gate_ok": result_payload.get("completion_gate_ok"),
            "failed_reasons": list((result_payload.get("completion_judge") or {}).get("failed_reasons") or []),
        },
    )


@router.post("/customer-orchestrate/chat", response_model=OrchestratorChatResponse)
async def customer_orchestrator_chat(
    request_context: Request,
    request: CustomerOrchestratorChatRequest,
    current_user=Depends(get_current_user),
):
    stage_run_payload = None
    if request.run_id:
        stage_run_payload = load_stage_run(request.run_id)

    from backend.llm.orchestrator import answer_orchestrator_chat as answer_orchestrator_chat_handler

    project_name = str(request.project_name or (stage_run_payload or {}).get("project_name") or "customer-product").strip() or "customer-product"
    auto_connect = AutoConnectMeta(
        connection_id=request.run_id or uuid4().hex,
        flow_id="FLOW-CUST-CHAT",
        step_id=str((stage_run_payload or {}).get("current_stage_id") or request.stage_id or "ARCH-001"),
        action="CUSTOMER_CHAT",
        route_id="ROUTE-CUSTOMER-ORCH-CHAT",
        panel_id="PANEL-CUSTOMER-ORCHESTRATOR",
        capability_id="customer-orchestrator-chat",
    )
    chat_response = await answer_orchestrator_chat_handler(
        request_context=request_context,
        request=OrchestratorChatRequest(
            task=str(request.task or "").strip() or project_name,
            message=str(request.message or "").strip(),
            agent_key="customer_orchestrator",
            mode="manual_10step",
            manual_mode=True,
            companion_mode=str(request.companion_mode or "hybrid").strip() or "hybrid",
            conversation_mode=str(request.conversation_mode or "auto").strip() or "auto",
            output_dir=request.output_dir,
            run_id=request.run_id,
            max_tokens=int(request.max_tokens or 768),
            lightweight=False,
            multi_turn_enabled=True,
            response_style=str(request.response_style or "balanced").strip() or "balanced",
            conversation=list(request.conversation or []),
            context_tags=list(request.context_tags or []) + ["customer", "stage-run", "manual-10step"],
            project_root=request.output_dir,
            project_memory=dict(request.project_memory or {}),
            auto_connect=auto_connect,
        ),
        agent_key="customer_orchestrator",
    )
    chat_response.stage_chat = _build_customer_stage_chat_context(stage_run_payload, request)
    chat_response.diagnostics = {
        **dict(chat_response.diagnostics or {}),
        "customer_user_id": getattr(current_user, "id", None),
        "customer_project_name": project_name,
        "stage_run_connected": bool(stage_run_payload),
    }
    return chat_response


def _resolve_stage_run_for_request(
    request: CustomerOrchestrateRequest,
    current_user: models.User,
) -> Optional[Dict[str, Any]]:
    if request.stage_run_id:
        return load_stage_run(request.stage_run_id)
    project_name = (request.project_name or "customer-product").strip() or "customer-product"
    return initialize_stage_run(
        scope="marketplace",
        project_name=project_name,
        mode=request.mode,
        requested_by={
            "id": current_user.id,
            "email": getattr(current_user, "email", ""),
        },
        metadata={
            "task": request.task,
        },
    )


def _merge_stage_tracking_into_task(task: str, stage_id: str, manual_correction: Optional[str] = None) -> str:
    tracking_payload = build_stage_tracking_payload(stage_id)
    if not tracking_payload:
        return task
    lines = [str(task or "").strip(), "", "[9단계 Stage Tracking]"]
    for key, value in tracking_payload.items():
        if key == "architecture_id":
            lines.append(f"- 현재 아키텍처 단계 ID: {value}")
        elif key == "flow_id":
            lines.append(f"- active_flow_id: {value}")
        elif key == "step_id":
            lines.append(f"- active_step_id: {value}")
        elif key == "action":
            lines.append(f"- active_action: {value}")
        elif key == "next_architecture_id":
            lines.append(f"- next_architecture_id: {value}")
        elif key == "next_step_id":
            lines.append(f"- next_flow_step_id: {value}")
        elif key == "next_action":
            lines.append(f"- next_flow_action: {value}")
    if str(manual_correction or "").strip():
        lines.extend(["", "[수동 보정 메모]", str(manual_correction or "").strip()])
    return "\n".join(lines).strip()


def _sync_stage_run_after_result(
    *,
    stage_run_id: Optional[str],
    stage_id: Optional[str],
    result_payload: Dict[str, Any],
    error_message: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not stage_run_id or not stage_id:
        return None
    normalized_stage_id = str(stage_id or "").strip().upper()
    if not normalized_stage_id:
        return None

    completion_judge = result_payload.get("completion_judge") or {}
    product_ready = bool(completion_judge.get("product_ready"))
    apply_error = str(result_payload.get("apply_error") or "").strip()
    postcheck_error = str(result_payload.get("postcheck_error") or "").strip()
    completion_gate_error = str(result_payload.get("completion_gate_error") or "").strip()
    failure_summary = str(result_payload.get("failure_summary") or "").strip()
    combined_error = error_message or apply_error or postcheck_error or completion_gate_error or failure_summary

    scaffold_only = bool(completion_judge.get("scaffold_only"))
    next_status = "passed" if product_ready and not combined_error and not scaffold_only else "manual_correction"
    note_parts = [
        str(result_payload.get("completion_summary") or "").strip(),
        combined_error,
        "; ".join(list(completion_judge.get("failed_reasons") or [])[:8]),
    ]
    note = "\n".join([part for part in note_parts if part])

    return update_stage_run(
        run_id=stage_run_id,
        stage_id=normalized_stage_id,
        status=next_status,
        note=note,
        manual_correction=combined_error if next_status != "passed" else "",
    )


@router.post("/customer-orchestrate/stage-runs")
def create_customer_orchestrate_stage_run(
    request: CustomerOrchestrateRequest,
    current_user=Depends(get_current_user),
):
    project_name = (request.project_name or "customer-product").strip() or "customer-product"
    payload = initialize_stage_run(
        scope="marketplace",
        project_name=project_name,
        mode=request.mode,
        requested_by={
            "id": current_user.id,
            "email": getattr(current_user, "email", ""),
        },
        metadata={
            "task": request.task,
        },
    )
    return payload


@router.get("/customer-orchestrate/stage-runs/{run_id}")
def get_customer_orchestrate_stage_run(
    run_id: str,
    current_user=Depends(get_current_user),
):
    del current_user
    payload = load_stage_run(run_id)
    if not payload:
        raise HTTPException(status_code=404, detail="stage run을 찾을 수 없습니다.")
    return payload


@router.post("/customer-orchestrate/stage-runs/update")
def update_customer_orchestrate_stage_run(
    payload: CustomerOrchestrateStageUpdateRequest,
    current_user=Depends(get_current_user),
):
    del current_user
    try:
        return update_stage_run(
            run_id=payload.run_id,
            stage_id=payload.stage_id,
            status=payload.status,
            note=payload.note,
            manual_correction=payload.manual_correction,
            substep_checks=payload.substep_checks,
            revision_note=payload.revision_note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _build_customer_orchestrate_log_event(
    message: str,
    level: str,
    tracking: Optional[Dict[str, Optional[str]]] = None,
) -> Dict[str, Any]:
    event: Dict[str, Any] = {
        "event": "log",
        "level": level,
        "message": message,
    }
    if tracking:
        if tracking.get("flow_id"):
            event["flow_id"] = tracking["flow_id"]
        if tracking.get("step_id"):
            event["step_id"] = tracking["step_id"]
        if tracking.get("action"):
            event["action"] = tracking["action"]
    return event


class CustomerPublishRequest(BaseModel):
    output_dir: str
    title: str
    description: str
    price: float = 99000
    category_id: Optional[int] = None
    image_url: Optional[str] = None
    demo_url: Optional[str] = None
    github_url: Optional[str] = None
    tags: Optional[List[str]] = None


class CustomerGeneratedProgramSummary(BaseModel):
    output_dir: Optional[str] = None
    output_archive_path: Optional[str] = None
    delivery_gate_blocked: bool = False
    delivery_gate_message: Optional[str] = None
    publish_ready: bool = False
    publish_targets: List[str] = []
    shipping_zip_ok: bool = False
    validation_profile: Optional[str] = None
    required_tests: List[str] = []
    priority_average_score: int = 0
    priority_peak_score: int = 0
    priority_latest_score: int = 0
    priority_previous_score: Optional[int] = None
    priority_momentum: int = 0
    priority_cumulative_score: int = 0
    approval_history_count: int = 0
    stage_run_status: Optional[str] = None
    hard_gate_failed_stages: List[str] = []


def _customer_follow_up_history_path() -> Path:
    runtime_root = Path(os.getenv("ADMIN_RUNTIME_ROOT", "")).expanduser().resolve() if os.getenv("ADMIN_RUNTIME_ROOT", "").strip() else (Path(tempfile.gettempdir()) / "codeai_admin_runtime").resolve()
    return runtime_root / "capability_cache" / "customer_follow_up_history.json"


def _read_customer_follow_up_history() -> Dict[str, List[Dict[str, Any]]]:
    path = _customer_follow_up_history_path()
    try:
        if not path.exists() or not path.is_file():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_customer_follow_up_history(payload: Dict[str, List[Dict[str, Any]]]) -> None:
    path = _customer_follow_up_history_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        return


def _append_customer_follow_up_history(*, history_id: str, score: int, limit: int = 24) -> Dict[str, Any]:
    payload = _read_customer_follow_up_history()
    entries = list(payload.get(history_id) or [])
    normalized_score = max(0, min(100, int(score)))
    if not entries or int(entries[-1].get("score") or -1) != normalized_score:
        entries.append({
            "recorded_at": datetime.utcnow().isoformat() + "Z",
            "score": normalized_score,
        })
    entries = entries[-max(2, limit):]
    payload[history_id] = entries
    _write_customer_follow_up_history(payload)
    scores = [max(0, min(100, int(item.get("score") or 0))) for item in entries]
    average_score = round(sum(scores) / len(scores)) if scores else normalized_score
    peak_score = max(scores) if scores else normalized_score
    previous_score = scores[-2] if len(scores) > 1 else None
    momentum = normalized_score - previous_score if previous_score is not None else 0
    cumulative_score = max(0, min(100, round((normalized_score * 0.45) + (average_score * 0.3) + (peak_score * 0.15) + (max(0, momentum) * 0.1))))
    return {
        "average_score": average_score,
        "peak_score": peak_score,
        "latest_score": normalized_score,
        "previous_score": previous_score,
        "momentum": momentum,
        "cumulative_score": cumulative_score,
    }


@router.get("/customer-orchestrate/generated-programs/latest", response_model=CustomerGeneratedProgramSummary)
def get_latest_customer_generated_program_summary(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    latest = (
        db.query(models.CustomerOrchestratorCompletion)
        .filter(models.CustomerOrchestratorCompletion.user_id == current_user.id)
        .order_by(models.CustomerOrchestratorCompletion.created_at.desc())
        .first()
    )
    if latest is None:
        raise HTTPException(status_code=404, detail="최근 생성 결과가 없습니다.")

    output_dir = str(getattr(latest, "output_dir", "") or "").strip()
    if not output_dir:
        return CustomerGeneratedProgramSummary()

    output_path = _validate_customer_generated_output_dir(Path(output_dir), current_user.id)
    validation_result_path = output_path / "docs" / "automatic_validation_result.json"
    shipping_readme_path = output_path / "docs" / "shipping_readme.md"
    payload: Dict[str, Any] = {}
    if validation_result_path.exists():
        try:
            payload = json.loads(validation_result_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}

    validation_engines = payload.get("validation_engines") or {}
    publish_payload = (((payload.get("completion_judge") or {}).get("packaging_audit") or {}).get("publish_payload")) if isinstance(payload.get("completion_judge"), dict) else None
    if not isinstance(publish_payload, dict):
        publish_payload = {}
    retry_queue_count = (
        db.query(models.FeatureRetryQueue)
        .filter(models.FeatureRetryQueue.user_id == current_user.id)
        .count()
    )
    completion_count = (
        db.query(models.CustomerOrchestratorCompletion)
        .filter(models.CustomerOrchestratorCompletion.user_id == current_user.id)
        .count()
    )
    log_count = (
        db.query(models.FeatureExecutionLog)
        .filter(models.FeatureExecutionLog.user_id == current_user.id)
        .count()
    )
    approval_history_count = (
        db.query(models.FeatureExecutionLog)
        .filter(
            models.FeatureExecutionLog.user_id == current_user.id,
            models.FeatureExecutionLog.entity_type == "customer_orchestrator_completion",
        )
        .count()
    )
    stage_run_status = str((payload.get("stage_run") or {}).get("status") or "") or None if isinstance(payload.get("stage_run"), dict) else None
    hard_gate_failed_stages = [
        str(item)
        for item in ((((payload.get("completion_judge") or {}).get("product_readiness_hard_gate") or {}).get("failed_stages") or []))
        if str(item).strip()
    ] if isinstance(payload.get("completion_judge"), dict) else []
    runtime_score = 0
    runtime_score += 35 if str(payload.get("status") or "") != "passed" else 0
    runtime_score += 25 if not (bool(publish_payload.get("ready")) or shipping_readme_path.exists()) else 0
    runtime_score += min(20, retry_queue_count * 5)
    runtime_score += min(10, len([item for item in (((validation_engines.get("integration_test_engine") or {}).get("required_tests") or [])) if str(item).strip()]) * 2)
    runtime_score += min(10, approval_history_count * 2)
    runtime_score += min(10, len(hard_gate_failed_stages) * 3)
    if stage_run_status in {"failed", "manual_correction"}:
        runtime_score += 10
    priority_history = _append_customer_follow_up_history(
        history_id=f"customer:{current_user.id}:{output_path.name}",
        score=runtime_score,
    )

    return CustomerGeneratedProgramSummary(
        output_dir=str(output_path),
        output_archive_path=str(payload.get("output_archive_path") or "") or None,
        delivery_gate_blocked=str(payload.get("status") or "") != "passed",
        delivery_gate_message="; ".join(list(payload.get("failed_reasons") or [])[:8]) or None,
        publish_ready=bool(publish_payload.get("ready")) or shipping_readme_path.exists(),
        publish_targets=[str(item) for item in (publish_payload.get("publish_targets") or []) if str(item).strip()],
        shipping_zip_ok=bool((validation_engines.get("shipping_zip_validation") or {}).get("ok")),
        validation_profile=str(payload.get("validation_profile") or "") or None,
        required_tests=[str(item) for item in (((validation_engines.get("integration_test_engine") or {}).get("required_tests") or [])) if str(item).strip()],
        priority_average_score=int(priority_history.get("average_score") or 0),
        priority_peak_score=int(priority_history.get("peak_score") or 0),
        priority_latest_score=int(priority_history.get("latest_score") or 0),
        priority_previous_score=priority_history.get("previous_score"),
        priority_momentum=int(priority_history.get("momentum") or 0),
        priority_cumulative_score=int(priority_history.get("cumulative_score") or 0),
        approval_history_count=approval_history_count,
        stage_run_status=stage_run_status,
        hard_gate_failed_stages=hard_gate_failed_stages,
    )


def _customer_orchestrate_connection_id(trace_id: Optional[str], flow_id: Optional[str], step_id: Optional[str], action: Optional[str]) -> Optional[str]:
    if flow_id and step_id and action:
        return f"{flow_id}:{step_id}:{action}"
    return trace_id


@router.get("/customer-orchestrate/completions/my")
def list_my_customer_orchestrate_completions(
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    safe_limit = max(1, min(int(limit or 20), 50))
    rows = (
        db.query(models.CustomerOrchestratorCompletion)
        .filter(models.CustomerOrchestratorCompletion.user_id == current_user.id)
        .order_by(models.CustomerOrchestratorCompletion.created_at.desc())
        .limit(safe_limit)
        .all()
    )
    items = [
        {
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
            "connection_id": _customer_orchestrate_connection_id(
                getattr(item, "trace_id", None),
                getattr(item, "flow_id", None),
                getattr(item, "step_id", None),
                getattr(item, "action", None),
            ),
        }
        for item in rows
    ]
    return {"items": items, "count": len(items), "limit": safe_limit}


@router.get("/customer-orchestrate/logs/my")
def list_my_customer_orchestrate_logs(
    limit: int = 30,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    safe_limit = max(1, min(int(limit or 30), 100))
    rows = (
        db.query(models.FeatureExecutionLog)
        .filter(models.FeatureExecutionLog.user_id == current_user.id)
        .order_by(models.FeatureExecutionLog.created_at.desc())
        .limit(safe_limit)
        .all()
    )
    items = [
        {
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
            "connection_id": _customer_orchestrate_connection_id(
                getattr(item, "trace_id", None),
                getattr(item, "flow_id", None),
                getattr(item, "step_id", None),
                getattr(item, "action", None),
            ),
        }
        for item in rows
    ]
    return {"items": items, "count": len(items), "limit": safe_limit}


@router.get("/customer-orchestrate/retry-queue/my")
def list_my_customer_orchestrate_retry_queue(
    limit: int = 30,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    safe_limit = max(1, min(int(limit or 30), 100))
    rows = (
        db.query(models.FeatureRetryQueue)
        .filter(models.FeatureRetryQueue.user_id == current_user.id)
        .order_by(models.FeatureRetryQueue.updated_at.desc(), models.FeatureRetryQueue.created_at.desc())
        .limit(safe_limit)
        .all()
    )
    items = [
        {
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
            "connection_id": _customer_orchestrate_connection_id(
                getattr(item, "trace_id", None),
                getattr(item, "flow_id", None),
                getattr(item, "step_id", None),
                getattr(item, "action", None),
            ),
        }
        for item in rows
    ]
    return {"items": items, "count": len(items), "limit": safe_limit}


def _build_customer_orchestrate_request(
    request: CustomerOrchestrateRequest,
    user_id: int,
):
    from backend.llm.orchestrator import OrchestrationRequest

    safe_mode = request.mode if request.mode in {"auto", "code", "design", "plan", "review", "full", "program_5step"} else "auto"
    _maybe_run_marketplace_storage_cleanup()
    user_dir_path = _resolve_customer_orchestrator_run_root(user_id)
    user_dir = str(user_dir_path)
    requested_output_dir = str(request.output_dir or "").strip() or None
    validated_output_dir: Optional[str] = None
    if requested_output_dir:
        if bool(request.allow_new_output_dir):
            requested_path = Path(requested_output_dir)
            requested_path.mkdir(parents=True, exist_ok=True)
            validated_output_dir = str(requested_path.resolve())
        else:
            # 고객 재실행은 기존 생성 결과 폴더 내부에서만 이어서 수정되도록 경로를 강제 검증한다.
            validated_output_dir = str(
                _validate_customer_generated_output_dir(
                    Path(requested_output_dir),
                    user_id,
                )
            )
    else:
        # 고객 신규 실행도 시작 시점에 단일 작업 폴더를 선할당해
        # 내부 강제 재시도가 retry_* 폴더를 연쇄 생성하지 않도록 고정한다.
        validated_output_dir = str(
            _allocate_customer_orchestrator_output_dir(
                user_dir_path,
                request.project_name,
            )
        )

    return OrchestrationRequest(
        task=_merge_stage_tracking_into_task(
            (request.task or "").strip(),
            request.stage_id or "ARCH-001",
            request.manual_correction,
        ),
        mode=safe_mode,
        project_name=request.project_name,
        output_base_dir=user_dir,
        output_dir=validated_output_dir,
        continue_in_place=bool(requested_output_dir),
        auto_apply=True,
        run_postcheck=True,
        retry_on_postcheck_fail=True,
        forensic_on_fail=True,
        refinement_request=request.refinement_request,
        max_improvement_cycles=request.max_improvement_cycles,
    )


def _slugify_text(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9가-힣_-]", "-", (value or "project").strip())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "project"


def _has_mojibake(value: Optional[str]) -> bool:
    if value is None:
        return False
    text = str(value)
    if re.search(r"\?{3,}", text):
        return True
    if "�" in text:
        return True
    if re.search(r"[\u0080-\u009F]", text):
        return True
    if re.search(r"(?:Ã.|Â.|ì.|ë.|ê.|í.){2,}", text):
        return True
    return False


def _validate_text_fields(fields: List[tuple[str, Optional[str]]]):
    broken = [name for name, value in fields if _has_mojibake(value)]
    if broken:
        raise HTTPException(
            status_code=400,
            detail=f"문자 인코딩이 깨진 텍스트가 감지되었습니다: {', '.join(broken)}",
        )


def _resolve_marketplace_upload_root() -> Path:
    configured_root = (os.getenv("MARKETPLACE_UPLOAD_ROOT", "") or "").strip()
    if configured_root:
        if os.name != "nt" and re.match(r"^[A-Za-z]:[\\/]", configured_root):
            mounted_upload_root = Path("/app/uploads")
            if mounted_upload_root.exists():
                return mounted_upload_root.resolve()
        return Path(configured_root).expanduser().resolve()
    workspace_root = Path(__file__).resolve().parents[2]
    return (workspace_root / "uploads").resolve()


def _resolve_marketplace_temp_root() -> Path:
    temp_root = (_resolve_marketplace_upload_root() / "tmp").resolve()
    temp_root.mkdir(parents=True, exist_ok=True)
    return temp_root


def _resolve_customer_orchestrator_run_root(user_id: int) -> Path:
    run_root = (
        _resolve_marketplace_upload_root()
        / "projects"
        / f"customer_{user_id}"
        / "runs"
    ).resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    return run_root


def _allocate_customer_orchestrator_output_dir(
    run_root: Path,
    project_name: Optional[str],
) -> Path:
    slug = _slugify_text(project_name or "project")
    candidate = (run_root / f"{slug}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}").resolve()
    if not str(candidate).startswith(str(run_root)):
        raise HTTPException(status_code=500, detail="출력 경로 계산 실패")
    suffix = 1
    while candidate.exists():
        candidate = (run_root / f"{slug}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{suffix:02d}").resolve()
        suffix += 1
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def _validate_customer_generated_output_dir(
    output_dir: Path,
    current_user_id: int,
) -> Path:
    base_allowed = _resolve_customer_orchestrator_run_root(current_user_id)
    resolved_output = output_dir.resolve()

    if not str(resolved_output).startswith(str(base_allowed)):
        raise HTTPException(
            status_code=403,
            detail="허용되지 않은 출력 경로입니다.",
        )
    if resolved_output == base_allowed:
        raise HTTPException(
            status_code=400,
            detail="실행 루트 전체가 아닌 개별 결과 폴더를 선택해야 합니다.",
        )
    if resolved_output.parent != base_allowed:
        raise HTTPException(
            status_code=400,
            detail="개별 실행 결과 폴더만 게시할 수 있습니다.",
        )
    if str(resolved_output.name).startswith("_archive"):
        raise HTTPException(
            status_code=400,
            detail="보관 폴더는 게시 대상으로 사용할 수 없습니다.",
        )
    return resolved_output


def _ensure_customer_publish_deploy_handoff(
    output_dir: Path,
    request: "CustomerPublishRequest",
    current_user_id: int,
) -> None:
    handoff_path = (output_dir / "deploy_handoff.json").resolve()
    if handoff_path.parent != output_dir.resolve():
        raise HTTPException(status_code=500, detail="배포 인계 파일 경로 계산 실패")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "user_id": current_user_id,
        "output_dir": str(output_dir),
        "publish": {
            "title": request.title.strip(),
            "description": request.description.strip(),
            "price": float(request.price),
            "category_id": request.category_id,
            "image_url": request.image_url,
            "demo_url": request.demo_url,
            "github_url": request.github_url,
            "tags": [tag.strip() for tag in (request.tags or []) if str(tag).strip()],
        },
    }
    handoff_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _read_file_from_storage(file_key: str) -> Optional[bytes]:
    if not file_key:
        return None

    if file_key.startswith("local:"):
        rel = file_key[len("local:"):].lstrip("/").replace("\\", "/")
        local_base = (_resolve_marketplace_upload_root() / "marketplace_local").resolve()
        local_path = (local_base / rel).resolve()
        if not str(local_path).startswith(str(local_base)):
            return None
        if not local_path.exists() or not local_path.is_file():
            return None
        return local_path.read_bytes()

    return minio_service.download_file(file_key)


def _store_bytes_with_fallback(file_bytes: bytes, object_key: str, content_type: str) -> str:
    uploaded = minio_service.upload_file(file_bytes, object_key, content_type)
    if uploaded:
        return object_key

    local_base = (_resolve_marketplace_upload_root() / "marketplace_local").resolve()
    local_target = (local_base / object_key).resolve()
    if not str(local_target).startswith(str(local_base)):
        raise HTTPException(status_code=500, detail="로컬 저장 경로 계산 실패")
    local_target.parent.mkdir(parents=True, exist_ok=True)
    local_target.write_bytes(file_bytes)
    return f"local:{object_key}"


_ad_order_queue: "queue.Queue[dict[str, Any]]" = queue.Queue()
_ad_worker_lock = threading.Lock()
_cleanup_lock = threading.Lock()
_last_cleanup_epoch_sec = 0.0
_ad_worker_runtime: Dict[str, Any] = {
    "worker_id": "ad-render-worker-001",
    "connection_id": "redis:video_render_queue",
    "started_at": None,
    "last_heartbeat": None,
    "last_order_id": None,
}

def _get_int_env(name: str, default: int, min_value: int, max_value: int) -> int:
    raw = (os.getenv(name, str(default)) or str(default)).strip()
    try:
        value = int(raw)
    except Exception:
        value = default
    return max(min_value, min(max_value, value))


AD_TOTAL_SECONDS = _get_int_env("AD_TOTAL_SECONDS", 60, 15, 180)
AD_FRAME_HINTS_PER_SECOND = 8
AD_TOTAL_FRAME_HINT = AD_TOTAL_SECONDS * AD_FRAME_HINTS_PER_SECOND
AD_CUT_COUNT = _get_int_env("AD_CUT_COUNT", 12, 1, 180)
AD_CUT_SECONDS = max(1, AD_TOTAL_SECONDS // AD_CUT_COUNT)
MARKETPLACE_AD_QUALITY_CRITERIA = [
    "첫 3초 안에 브랜드/상품 훅이 보여야 한다.",
    f"{AD_TOTAL_SECONDS}초 본편은 1초당 {AD_FRAME_HINTS_PER_SECOND}장, 총 {AD_TOTAL_FRAME_HINT}장 고정 규격을 유지한다.",
    "상품은 대부분의 컷에서 보이거나 명시적으로 참조되어야 한다.",
    "후반부에는 사용 장면, 신뢰 포인트, CTA가 순서대로 정리되어야 한다.",
    "자막/내레이션/장면 메시지는 서로 충돌하지 않아야 한다.",
]

DEDICATED_STATUS_ACCEPTED = {"accepted", "queued", "processing", "running", "completed", "failed", "error"}
DEDICATED_STATUS_COMPLETED = {"completed", "success", "done"}
DEDICATED_STATUS_FAILED = {"failed", "error"}


def _order_duration_seconds(order: models.AdVideoOrder) -> int:
    try:
        value = int(getattr(order, "duration_seconds", AD_TOTAL_SECONDS) or AD_TOTAL_SECONDS)
    except Exception:
        value = AD_TOTAL_SECONDS
    return max(15, min(180, value))


def _recommended_cut_count(duration_seconds: int) -> int:
    return max(1, int(round(max(1, duration_seconds) / max(1, AD_CUT_SECONDS))))


def _cut_count_bounds(duration_seconds: int) -> tuple[int, int]:
    recommended = _recommended_cut_count(duration_seconds)
    return recommended, recommended


def _minimum_product_image_count_for_duration(duration_seconds: int) -> int:
    if duration_seconds >= 60:
        return 12
    if duration_seconds >= 30:
        return 6
    return 3


def _marketplace_ad_quality_brief(duration_seconds: int) -> str:
    return (
        f"Marketplace {duration_seconds}초 광고 기준: "
        + " / ".join(MARKETPLACE_AD_QUALITY_CRITERIA)
    )


def _ad_variation_seed(order: models.AdVideoOrder, index: int) -> int:
    public_job_id = str(getattr(order, "public_job_id", "") or "")
    material = f"{order.id}:{public_job_id}:{index}:marketplace-ad"
    return int(hashlib.sha256(material.encode("utf-8")).hexdigest()[:8], 16)


def _order_cut_count(order: models.AdVideoOrder) -> int:
    duration = _order_duration_seconds(order)
    minimum, maximum = _cut_count_bounds(duration)
    recommended = _recommended_cut_count(duration)
    try:
        value = int(
            getattr(order, "cut_count", recommended) or recommended
        )
    except Exception:
        value = recommended
    return max(minimum, min(maximum, value))


def _order_cut_seconds(order: models.AdVideoOrder) -> int:
    duration = _order_duration_seconds(order)
    cut_count = _order_cut_count(order)
    return max(1, int(round(duration / max(1, cut_count))))


def _order_subtitle_speed(order: models.AdVideoOrder) -> float:
    try:
        value = float(getattr(order, "subtitle_speed", 1.0) or 1.0)
    except Exception:
        value = 1.0
    value = max(0.5, min(2.0, value))
    return round(value, 1)


def _order_audio_volume(order: models.AdVideoOrder) -> int:
    try:
        value = int(getattr(order, "audio_volume", 100) or 100)
    except Exception:
        value = 100
    value = max(0, min(200, value))
    return int(round(value / 5.0) * 5)


def _order_bgm_enabled(order: models.AdVideoOrder) -> bool:
    raw = (os.getenv("MARKETPLACE_AD_BGM_ENABLED", "true") or "true")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _order_bgm_volume(order: models.AdVideoOrder) -> int:
    raw = (os.getenv("MARKETPLACE_AD_BGM_VOLUME", "38") or "38").strip()
    try:
        value = int(raw)
    except Exception:
        value = 38
    return max(0, min(100, value))


def _infer_bgm_mood(*values: Optional[str]) -> str:
    text = " ".join(str(value or "").lower() for value in values)
    if any(keyword in text for keyword in [
        "premium", "luxury", "studio", "elegant", "high-end", "gold",
    ]):
        return "premium"
    if any(keyword in text for keyword in [
        "sale", "launch", "dynamic", "sport", "active", "energy",
        "energetic", "boost",
    ]):
        return "upbeat"
    if any(keyword in text for keyword in [
        "beauty", "calm", "natural", "wellness", "soft", "relax",
        "clean", "serene",
    ]):
        return "calm"
    if any(keyword in text for keyword in [
        "tech", "digital", "future", "futuristic", "smart", "ai",
        "modern", "neon",
    ]):
        return "tech"
    return "corporate"


def _order_bgm_mood(order: models.AdVideoOrder) -> str:
    return _infer_bgm_mood(
        getattr(order, "title", ""),
        getattr(order, "background_prompt", ""),
        getattr(order, "caption_text", ""),
    )


def _bgm_profile(mood: str) -> Dict[str, Any]:
    profiles: Dict[str, Dict[str, Any]] = {
        "premium": {
            "expr": "0.20*sin(2*PI*196*t)+0.12*sin(2*PI*246.94*t)+0.08*sin(2*PI*329.63*t)+0.04*sin(2*PI*98*t)",
            "lowpass": 3400,
        },
        "upbeat": {
            "expr": "0.18*sin(2*PI*220*t)+0.12*sin(2*PI*330*t)+0.08*sin(2*PI*440*t)+0.05*sin(2*PI*660*t)",
            "lowpass": 4200,
        },
        "calm": {
            "expr": "0.18*sin(2*PI*174.61*t)+0.10*sin(2*PI*220*t)+0.07*sin(2*PI*261.63*t)+0.03*sin(2*PI*87.31*t)",
            "lowpass": 3000,
        },
        "tech": {
            "expr": "0.16*sin(2*PI*207.65*t)+0.11*sin(2*PI*311.13*t)+0.07*sin(2*PI*415.3*t)+0.05*sin(2*PI*622.25*t)",
            "lowpass": 4600,
        },
        "corporate": {
            "expr": "0.19*sin(2*PI*196*t)+0.11*sin(2*PI*293.66*t)+0.07*sin(2*PI*392*t)+0.04*sin(2*PI*98*t)",
            "lowpass": 3600,
        },
    }
    return profiles.get(mood, profiles["corporate"])


def _bgm_lavfi_source(duration_seconds: int, mood: str) -> str:
    profile = _bgm_profile(mood)
    return (
        "aevalsrc="
        f"exprs='{profile['expr']}|{profile['expr']}':"
        f"s=44100:d={max(1, int(duration_seconds))}"
    )


def _order_render_quality(order: models.AdVideoOrder) -> str:
    value = str(getattr(order, "render_quality", "high") or "high").strip().lower()
    if value not in {"standard", "high", "ultra"}:
        return "high"
    return value


def _stability_profile_for_order(order: models.AdVideoOrder) -> str:
    duration = _order_duration_seconds(order)
    cut_count = _order_cut_count(order)
    quality = _order_render_quality(order)
    motion_profile = _motion_profile_for_order(order)
    if quality == "ultra" or motion_profile == "youtube_web" or duration >= 30 or cut_count > 12:
        return "stable_90"
    return "default"


def _effective_cut_count_for_order(order: models.AdVideoOrder) -> int:
    cut_count = _order_cut_count(order)
    raw_storyboard = getattr(order, "storyboard_json", None)
    if raw_storyboard:
        try:
            parsed = json.loads(raw_storyboard)
            if isinstance(parsed, list) and parsed:
                return max(1, len(parsed))
        except Exception:
            pass
    return cut_count


def _effective_cut_seconds_for_order(order: models.AdVideoOrder) -> int:
    duration = _order_duration_seconds(order)
    cut_count = _effective_cut_count_for_order(order)
    return max(1, int(round(duration / max(1, cut_count))))


def _get_marketplace_retention_days() -> int:
    raw = (os.getenv("MARKETPLACE_RETENTION_DAYS", "30") or "30").strip()
    try:
        days = int(raw)
    except Exception:
        days = 30
    return max(1, days)


def _get_marketplace_temp_retention_days() -> int:
    raw = (os.getenv("MARKETPLACE_TEMP_RETENTION_DAYS", "7") or "7").strip()
    try:
        days = int(raw)
    except Exception:
        days = 7
    return max(1, days)


def _get_marketplace_cleanup_interval_sec() -> int:
    raw = (os.getenv("MARKETPLACE_CLEANUP_INTERVAL_SEC", "3600") or "3600").strip()
    try:
        value = int(raw)
    except Exception:
        value = 3600
    return max(60, value)


def _get_ad_download_min_notice_minutes() -> int:
    raw = (os.getenv("AD_DOWNLOAD_MIN_NOTICE_MINUTES", "60") or "60").strip()
    try:
        value = int(raw)
    except Exception:
        value = 60
    return max(0, value)


def _get_ad_download_window_days() -> int:
    raw = (os.getenv("AD_DOWNLOAD_WINDOW_DAYS", "30") or "30").strip()
    try:
        value = int(raw)
    except Exception:
        value = 30
    return max(1, value)


def _get_ad_download_max_count() -> int:
    raw = (os.getenv("AD_DOWNLOAD_MAX_COUNT", "2") or "2").strip()
    try:
        value = int(raw)
    except Exception:
        value = 2
    return max(1, value)


def _to_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _cleanup_expired_paths(root: Path, cutoff_epoch_sec: float) -> None:
    if not root.exists() or not root.is_dir():
        return

    for child in root.iterdir():
        try:
            child_mtime = child.stat().st_mtime
            if child_mtime >= cutoff_epoch_sec:
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        except Exception:
            continue


def _maybe_run_marketplace_storage_cleanup(force: bool = False) -> None:
    global _last_cleanup_epoch_sec

    now = time.time()
    interval_sec = _get_marketplace_cleanup_interval_sec()
    if not force and (now - _last_cleanup_epoch_sec) < interval_sec:
        return

    with _cleanup_lock:
        now = time.time()
        if not force and (now - _last_cleanup_epoch_sec) < interval_sec:
            return
        _last_cleanup_epoch_sec = now

        retention_days = _get_marketplace_retention_days()
        temp_retention_days = _get_marketplace_temp_retention_days()
        asset_cutoff = now - (retention_days * 86400)
        temp_cutoff = now - (temp_retention_days * 86400)
        upload_root = _resolve_marketplace_upload_root()

        temp_targets = [
            upload_root / "tmp",
        ]
        asset_targets = [
            upload_root / "projects",
            upload_root / "marketplace_local" / "projects",
            upload_root / "marketplace_local" / "ad-orders",
        ]
        for target in temp_targets:
            _cleanup_expired_paths(target, temp_cutoff)
        for target in asset_targets:
            _cleanup_expired_paths(target, asset_cutoff)


def _engine_headers(api_key_env_name: str) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = read_secret_env(api_key_env_name)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _safe_json(response: requests.Response, source_name: str) -> Dict[str, Any]:
    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError(f"{source_name} returned non-json response") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"{source_name} returned invalid json shape")
    return data


def _parse_progress_percent(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    try:
        progress = int(raw)
    except Exception:
        return None
    return max(0, min(100, progress))


def _safe_filename(value: str) -> str:
    name = _slugify_text(value)
    return name[:80] if len(name) > 80 else name


def _split_caption_into_cuts(caption: str, cut_count: int) -> List[str]:
    text = (caption or "").strip()
    if not text:
        text = "광고 메시지"

    safe_cut_count = max(1, cut_count)
    chunk_size = max(1, len(text) // safe_cut_count)
    chunks: List[str] = []
    cursor = 0
    for index in range(safe_cut_count):
        if index == safe_cut_count - 1:
            part = text[cursor:]
        else:
            part = text[cursor:cursor + chunk_size]
        cursor += chunk_size
        part = part.strip() or text[:20]
        chunks.append(part)
    return chunks


def _load_ad_image_from_prompt(
    image_prompt: Optional[str],
    tmpdir: str,
    file_stem: str = "source",
) -> Optional[Path]:
    text = (image_prompt or "").strip()
    if not text:
        return None

    try:
        if text.startswith("data:image/"):
            header, encoded = text.split(",", 1)
            mime = header.split(";", 1)[0].split(":", 1)[1].lower()
            ext_map = {
                "image/jpeg": ".jpg",
                "image/jpg": ".jpg",
                "image/png": ".png",
                "image/webp": ".webp",
                "image/gif": ".gif",
                "image/bmp": ".bmp",
            }
            ext = ext_map.get(mime, ".img")
            data = base64.b64decode(encoded, validate=True)
            if not data:
                return None
            image_path = Path(tmpdir) / f"{file_stem}{ext}"
            image_path.write_bytes(data)
            return image_path

        if text.startswith("http://") or text.startswith("https://"):
            response = requests.get(text, timeout=30)
            response.raise_for_status()
            content_type = str(response.headers.get("content-type") or "").lower()
            if not content_type.startswith("image/"):
                return None
            ext = ".jpg"
            if "png" in content_type:
                ext = ".png"
            elif "webp" in content_type:
                ext = ".webp"
            elif "gif" in content_type:
                ext = ".gif"
            image_path = Path(tmpdir) / f"{file_stem}{ext}"
            image_path.write_bytes(response.content)
            return image_path
    except (ValueError, binascii.Error, requests.RequestException):
        return None
    except Exception:
        return None

    return None


def _normalize_string_list(values: Any) -> List[str]:
    if values is None:
        return []

    items: List[Any]
    if isinstance(values, list):
        items = values
    elif isinstance(values, str):
        text_value = values.strip()
        if not text_value:
            return []
        if text_value.startswith("["):
            try:
                parsed = json.loads(text_value)
                items = parsed if isinstance(parsed, list) else [text_value]
            except Exception:
                items = [text_value]
        else:
            items = [text_value]
    else:
        items = [values]

    normalized: List[str] = []
    for item in items:
        text_item = str(item or "").strip()
        if text_item:
            normalized.append(text_item)
    return normalized


def _normalize_image_reference(value: Optional[str]) -> Optional[str]:
    text = (value or "").strip()
    return text or None


def _get_product_image_prompts(order: models.AdVideoOrder) -> List[str]:
    values = _normalize_string_list(getattr(order, "product_image_prompts", None))
    if values:
        return values
    legacy_value = _normalize_image_reference(getattr(order, "image_prompt", None))
    return [legacy_value] if legacy_value else []


def _get_primary_image_prompt(
    image_prompt: Optional[str],
    product_image_prompts: Optional[Any] = None,
) -> str:
    normalized_products = _normalize_string_list(product_image_prompts)
    if normalized_products:
        return normalized_products[0]
    return (image_prompt or "").strip()


def _get_reference_image_prompt(order: models.AdVideoOrder) -> Optional[str]:
    portrait_prompt = _normalize_image_reference(getattr(order, "portrait_image_prompt", None))
    if portrait_prompt:
        return portrait_prompt
    primary_prompt = _get_primary_image_prompt(order.image_prompt, getattr(order, "product_image_prompts", None))
    return primary_prompt or None


def _serialize_ad_video_order(order: models.AdVideoOrder) -> Dict[str, Any]:
    expose_output_metadata = str(getattr(order, "status", "") or "") == models.AdVideoOrderStatus.COMPLETED.value
    return {
        "id": order.id,
        "public_job_id": getattr(order, "public_job_id", None),
        "trace_id": getattr(order, "trace_id", None),
        "flow_id": getattr(order, "flow_id", None),
        "step_id": getattr(order, "step_id", None),
        "action": getattr(order, "action", None),
        "user_id": order.user_id,
        "title": order.title,
        "image_prompt": _get_primary_image_prompt(order.image_prompt, getattr(order, "product_image_prompts", None)),
        "portrait_image_prompt": _normalize_image_reference(getattr(order, "portrait_image_prompt", None)),
        "product_image_prompts": _get_product_image_prompts(order),
        "storyboard": _compose_storyboard(order),
        "storyboard_review": _compose_storyboard_review(order),
        "subject_type": str(getattr(order, "subject_type", "auto") or "auto"),
        "background_prompt": order.background_prompt,
        "caption_text": order.caption_text,
        "voice_gender": order.voice_gender,
        "engine_type": order.engine_type,
        "duration_seconds": order.duration_seconds,
        "visual_style": order.visual_style,
        "cut_count": order.cut_count,
        "subtitle_speed": order.subtitle_speed,
        "render_quality": order.render_quality,
        "audio_volume": order.audio_volume,
        "status": order.status,
        "progress_percent": order.progress_percent,
        "external_job_id": order.external_job_id,
        "output_file_key": order.output_file_key if expose_output_metadata else None,
        "output_filename": order.output_filename if expose_output_metadata else None,
        "output_video_key": order.output_video_key if expose_output_metadata else None,
        "output_video_filename": order.output_video_filename if expose_output_metadata else None,
        "quality_score": getattr(order, "quality_score", None),
        "quality_gate_passed": bool(getattr(order, "quality_gate_passed", False)),
        "quality_feedback": getattr(order, "quality_feedback", None),
        "quality_retry_count": int(getattr(order, "quality_retry_count", 0) or 0),
        "quality_checked_at": getattr(order, "quality_checked_at", None),
        "download_count": order.download_count,
        "error_message": order.error_message,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
    }


def _contains_cta_signal(text: str) -> bool:
    normalized = (text or "").lower()
    keywords = [
        "지금",
        "바로",
        "구매",
        "주문",
        "신청",
        "문의",
        "예약",
        "체험",
        "상담",
        "shop",
        "buy",
        "cta",
        "order now",
        "learn more",
        "call now",
        "sign up",
    ]
    return any(keyword in normalized for keyword in keywords)


def _estimate_min_video_bytes(order: models.AdVideoOrder) -> int:
    duration = max(1, _order_duration_seconds(order))
    quality = str(getattr(order, "render_quality", "high") or "high").lower()
    engine_type = str(getattr(order, "engine_type", "") or "").lower()

    if engine_type == "internal_ffmpeg":
        per_second = 15000
        if quality == "high":
            per_second = 18000
        elif quality == "ultra":
            per_second = 22000
        return duration * per_second

    per_second = 90000
    if quality == "high":
        per_second = 120000
    elif quality == "ultra":
        per_second = 150000
    if engine_type == "dedicated_engine":
        per_second += 15000
    return duration * per_second


def _evaluate_ad_order_quality(
    order: models.AdVideoOrder,
    video_bytes: bytes,
) -> Dict[str, Any]:
    duration = _order_duration_seconds(order)
    cut_count = _order_cut_count(order)
    minimum, maximum = _cut_count_bounds(duration)
    recommended = _recommended_cut_count(duration)
    caption = str(getattr(order, "caption_text", "") or "").strip()
    title = str(getattr(order, "title", "") or "").strip()
    image_prompt = str(getattr(order, "image_prompt", "") or "").strip()
    product_prompts = _get_product_image_prompts(order)
    feedback: List[str] = []
    hard_failures: List[str] = []
    score = 0.0
    face_consistency = 0.0
    product_consistency = 0.0
    visual_decision = "review_required"

    if minimum <= cut_count <= maximum:
        pacing_score = max(18.0, 30.0 - abs(cut_count - recommended) * 1.2)
        score += pacing_score
    else:
        hard_failures.append(
            f"컷 수가 {duration}초 기준 권장 범위({minimum}~{maximum})를 벗어났습니다. 현재 {cut_count}컷입니다."
        )

    if len(caption) >= 24:
        score += 10.0
    else:
        feedback.append("카피가 너무 짧아서 중반부 효익/증거 전개가 약할 가능성이 큽니다.")

    if _contains_cta_signal(f"{title} {caption}"):
        score += 12.0
    else:
        feedback.append("최종 CTA 신호가 약합니다. 구매/문의/신청 같은 행동 유도가 필요합니다.")

    if _has_ad_image_reference(image_prompt):
        score += 10.0
    elif product_prompts:
        score += 8.0
    else:
        hard_failures.append("실상품 기준 이미지 참조가 약해서 제품 인지 유지 가능성이 낮습니다.")

    if title and caption and title.lower() not in caption.lower():
        score += 8.0
    else:
        feedback.append("타이틀과 본문 카피가 거의 동일해서 훅과 본문 메시지 분리가 약합니다.")

    if len(product_prompts) >= 2:
        score += 5.0
    elif product_prompts:
        score += 3.0
    else:
        feedback.append("상품/장면 참조 수가 적어 반복 프레임 위험이 있습니다.")

    face_consistency = 85.0 if _normalize_image_reference(getattr(order, "portrait_image_prompt", None)) else 0.0
    if _normalize_image_reference(getattr(order, "portrait_image_prompt", None)):
        try:
            from backend.movie_studio.quality.arcface_adapter import build_face_recognition_adapter

            adapter, adapter_status = build_face_recognition_adapter()
            if adapter.is_available():
                face_consistency = 92.0 if adapter_status.get("available") else 85.0
                score += 10.0
            else:
                feedback.append("얼굴 일관성 엔진이 비활성 상태여서 fallback 점수로 판정했습니다.")
        except Exception as exc:
            feedback.append(f"얼굴 일관성 실검증 fallback 적용: {exc}")
        if face_consistency < 80.0:
            hard_failures.append("얼굴 일관성 점수가 기준 미만입니다.")

    if len(product_prompts) >= 3:
        product_consistency = 94.0
        score += 10.0
    elif len(product_prompts) >= 2:
        product_consistency = 84.0
        score += 6.0
    elif len(product_prompts) == 1:
        product_consistency = 72.0
        feedback.append("제품 참조 이미지가 1개뿐이라 컷 간 제품 일관성 검증 신뢰도가 낮습니다.")
    else:
        product_consistency = 0.0
        hard_failures.append("제품 일관성 실검증을 위한 참조 이미지가 부족합니다.")

    if product_consistency and product_consistency < 75.0:
        hard_failures.append("제품 일관성 점수가 기준 미만입니다.")

    actual_bytes = len(video_bytes or b"")
    min_video_bytes = _estimate_min_video_bytes(order)
    if actual_bytes >= min_video_bytes:
        size_ratio = min(1.0, actual_bytes / max(1, min_video_bytes * 1.25))
        score += 25.0 * size_ratio
    else:
        hard_failures.append(
            f"산출 영상 용량이 낮습니다. {actual_bytes} bytes, 최소 기대치 {min_video_bytes} bytes."
        )

    if duration == AD_TOTAL_SECONDS and cut_count != AD_CUT_COUNT:
        hard_failures.append(f"{AD_TOTAL_SECONDS}초 광고는 {AD_CUT_COUNT}컷 x {AD_CUT_SECONDS}초 규격을 유지해야 합니다.")

    score = round(min(100.0, score), 1)
    quality_gate_passed = not hard_failures and score >= MARKETPLACE_QUALITY_PASS_SCORE
    if quality_gate_passed and face_consistency >= 80.0 and product_consistency >= 80.0:
        visual_decision = "sale_ready"
    elif quality_gate_passed:
        visual_decision = "review_required"
    else:
        visual_decision = "blocked"

    if not quality_gate_passed:
        feedback = hard_failures + feedback
    elif not feedback:
        feedback = ["시장형 60초 광고 기준을 충족했습니다."]

    return {
        "score": score,
        "passed": quality_gate_passed,
        "feedback": " ".join(feedback).strip(),
        "face_consistency_score": round(face_consistency, 1),
        "product_consistency_score": round(product_consistency, 1),
        "sales_quality_decision": visual_decision,
    }


def _build_engine_image_payload(image_prompt: Optional[str]) -> Dict[str, str]:
    text = (image_prompt or "").strip()
    if not text:
        return {}

    if text.startswith("data:image/"):
        try:
            header, encoded = text.split(",", 1)
            mime = header.split(";", 1)[0].split(":", 1)[1].lower()
            return {
                "image_mime_type": mime,
                "image_data_base64": encoded,
            }
        except Exception:
            return {"image_prompt": text}

    if text.startswith("http://") or text.startswith("https://"):
        return {"image_source_url": text}

    return {"image_prompt": text}


def _build_engine_media_payload(order: models.AdVideoOrder) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    primary_prompt = _get_primary_image_prompt(order.image_prompt, getattr(order, "product_image_prompts", None))
    payload.update(_build_engine_image_payload(primary_prompt))

    portrait_prompt = _normalize_image_reference(getattr(order, "portrait_image_prompt", None))
    if portrait_prompt:
        payload["portrait_image_prompt"] = portrait_prompt

    product_prompts = _get_product_image_prompts(order)
    if product_prompts:
        payload["product_image_prompts"] = product_prompts

    return payload


def _has_ad_image_reference(image_prompt: Optional[str]) -> bool:
    text = (image_prompt or "").strip()
    if not text:
        return False
    return text.startswith("data:image/") or text.startswith("http://") or text.startswith("https://")


def _ffmpeg_text(value: Optional[str], limit: int = 90) -> str:
    text = (value or "").strip().replace("\n", " ")
    if len(text) > limit:
        text = text[:limit]
    return (
        text
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace(",", "\\,")
        .replace("%", "\\%")
    )


def _compose_cut_scripts(order: models.AdVideoOrder) -> List[str]:
    title = (order.title or "신규 제품 광고").strip()
    bg = (order.background_prompt or "").strip()
    caption = (order.caption_text or "").strip()

    bg_parts = [p.strip() for p in re.split(r"[\n,\.\!\?]+", bg) if p.strip()]
    caption_parts = [p.strip() for p in re.split(r"[\n,\.\!\?]+", caption) if p.strip()]

    cut_count = _order_cut_count(order)
    hook_pool = [
        f"첫 장면 훅: {title}의 핵심 이미지를 즉시 보여준다",
        f"스크롤을 멈추게 하는 시작 장면으로 {title}를 바로 인지시킨다",
        bg_parts[0] if bg_parts else f"{title}의 분위기와 브랜드 톤을 첫 컷에서 고정한다",
    ]
    benefit_pool = [
        caption_parts[0] if caption_parts else "핵심 가치 전달",
        caption_parts[1] if len(caption_parts) > 1 else "문제 해결 제안",
        caption_parts[2] if len(caption_parts) > 2 else "실사용 만족 포인트",
    ]
    proof_pool = [
        bg_parts[1] if len(bg_parts) > 1 else "사용 장면으로 신뢰도를 증명한다",
        "상품 디테일과 사용 맥락을 교차 편집해 설득력을 높인다",
        "리뷰 컷처럼 기능과 결과를 짧게 증명한다",
    ]
    cta_pool = [
        "지금 바로 문의하고 시작하세요",
        f"마지막 컷에서 {title}와 행동 유도를 동시에 고정한다",
        "혜택 요약 뒤 즉시 행동 유도로 마무리한다",
    ]

    scripts: List[str] = []
    for index in range(cut_count):
        ratio = (index + 1) / max(1, cut_count)
        if ratio <= 0.12:
            pool = hook_pool
        elif ratio <= 0.45:
            pool = benefit_pool
        elif ratio <= 0.78:
            pool = proof_pool
        else:
            pool = cta_pool

        selected = pool[index % len(pool)].strip()
        if ratio > 0.12 and ratio <= 0.78:
            selected = f"{selected}. 상품이 화면 안에서 계속 보이도록 유지한다"
        if ratio > 0.78:
            selected = f"{selected}. 브랜드명과 CTA를 동시에 보여준다"
        scripts.append(selected)

    return scripts


def _compose_scene_prompt(order: models.AdVideoOrder) -> str:
    title = (order.title or "광고").strip()
    bg = (order.background_prompt or "").strip()
    caption = (order.caption_text or "").strip()
    style = (order.visual_style or "photorealistic").strip()
    duration = _order_duration_seconds(order)
    cut_count = _order_cut_count(order)
    profile = _subject_profile(order)
    subject_label = str(profile["subject_label"])
    motion_phrase = str(profile["motion_phrase"])
    gesture_phrase = str(profile["gesture_phrase"])

    return (
        f"Create a cinematic {duration}-second scene-driven commercial video. "
        f"Use {cut_count} short sequential micro-cuts with a clear motion beat in every cut. "
        f"Product title: {title}. "
        f"Primary subject: {subject_label}. "
        f"Background: {bg}. "
        f"Required actions and story: {caption}. "
        f"Visual style: {style}. "
        f"Motion rule: {motion_phrase}. Gesture rule: {gesture_phrase}. "
        f"Marketplace quality gate: {_marketplace_ad_quality_brief(duration)}. "
        "Editing rule: build many short editorially stitchable clips instead of relying on a single long take. "
        "Clear product beauty shots, readable movement, continuous scene transitions, ad-quality composition, subtitles must remain readable, no black frames, no static slideshow."
    )


def _split_ad_copy(text_value: Optional[str]) -> List[str]:
    return [
        part.strip()
        for part in re.split(r"[\n\.\!\?]+", (text_value or "").strip())
        if part.strip()
    ]


def _order_subject_type(order: models.AdVideoOrder) -> str:
    explicit = str(getattr(order, "subject_type", "auto") or "auto").strip().lower()
    allowed = {"auto", "human", "robot", "character", "product"}
    if explicit in allowed and explicit != "auto":
        return explicit

    combined_text = " ".join(
        value
        for value in [
            str(getattr(order, "title", "") or ""),
            str(getattr(order, "caption_text", "") or ""),
            str(getattr(order, "background_prompt", "") or ""),
            str(getattr(order, "visual_style", "") or ""),
        ]
        if value
    ).lower()

    robot_keywords = ["robot", "android", "cyborg", "mecha", "로봇", "안드로이드", "메카"]
    character_keywords = ["character", "mascot", "avatar", "creature", "캐릭터", "마스코트", "아바타"]

    if any(keyword in combined_text for keyword in robot_keywords):
        return "robot"
    if any(keyword in combined_text for keyword in character_keywords):
        return "character"
    if _normalize_image_reference(getattr(order, "portrait_image_prompt", None)):
        return "human"
    return "product"


def _subject_profile(order: models.AdVideoOrder) -> Dict[str, Any]:
    subject_type = _order_subject_type(order)
    portrait_prompt = _normalize_image_reference(getattr(order, "portrait_image_prompt", None))
    has_portrait = bool(portrait_prompt)
    has_product = bool(_get_product_image_prompts(order))

    profiles: Dict[str, Dict[str, Any]] = {
        "human": {
            "subject_label": "human spokesperson",
            "identity_phrase": "same person identity cues from the reference, newly staged for a commercial",
            "motion_phrase": "natural human gestures, confident product presentation, controlled body movement",
            "gesture_phrase": "eye contact, hand gestures, pointing, holding, presenting",
            "requires_realistic_human": has_portrait,
        },
        "robot": {
            "subject_label": "robot spokesperson",
            "identity_phrase": "same robot identity cues from the reference, newly staged for a commercial",
            "motion_phrase": "articulated robotic movement, clear arm gestures, stable torso turns",
            "gesture_phrase": "robot arm pointing, product display pose, deliberate head turn",
            "requires_realistic_human": False,
        },
        "character": {
            "subject_label": "animated mascot spokesperson",
            "identity_phrase": "same mascot identity cues from the reference, newly staged for a commercial",
            "motion_phrase": "expressive mascot motion, readable silhouette, clear body acting",
            "gesture_phrase": "big readable gestures, product presentation, inviting call to action",
            "requires_realistic_human": False,
        },
        "product": {
            "subject_label": "product hero object",
            "identity_phrase": "same product identity from the reference, newly staged for a premium commercial",
            "motion_phrase": "camera-led motion, object reveal, kinetic framing, premium detail emphasis",
            "gesture_phrase": "object reveal, rotation, close-up transitions, premium hero emphasis",
            "requires_realistic_human": False,
        },
    }

    profile = profiles.get(subject_type, profiles["product"]).copy()
    profile["subject_type"] = subject_type
    profile["has_portrait"] = has_portrait
    profile["has_product"] = has_product
    return profile


def _prefer_global_scene_basis(order: models.AdVideoOrder) -> bool:
    scene_basis = str(os.getenv("VIDEO_SCENE_BASIS", "global") or "global").strip().lower()
    if scene_basis in {"subject", "local"}:
        return False
    return True


def _motion_profile_for_order(order: models.AdVideoOrder) -> str:
    style = str(getattr(order, "visual_style", "") or "").strip().lower()
    quality = str(getattr(order, "render_quality", "high") or "high").strip().lower()
    if quality == "ultra" or "youtube_web" in style or "web" in style or "youtube" in style:
        return "youtube_web"
    return "general"


def _effective_motion_profile_for_order(order: models.AdVideoOrder) -> str:
    if _stability_profile_for_order(order) == "stable_90":
        return "general"
    return _motion_profile_for_order(order)


def _effective_render_quality_for_order(order: models.AdVideoOrder) -> str:
    if _stability_profile_for_order(order) == "stable_90":
        return "high"
    return _order_render_quality(order)


def _target_output_fps_for_order(order: models.AdVideoOrder) -> int:
    duration = _order_duration_seconds(order)
    if duration == AD_TOTAL_SECONDS:
        return 8
    if _stability_profile_for_order(order) == "stable_90":
        return 30
    return 60 if _motion_profile_for_order(order) == "youtube_web" else 30


def _target_output_frame_hint_for_order(order: models.AdVideoOrder) -> int:
    duration = _order_duration_seconds(order)
    if duration == AD_TOTAL_SECONDS:
        return AD_TOTAL_FRAME_HINT
    return max(AD_FRAME_HINTS_PER_SECOND, int(max(1, duration) * AD_FRAME_HINTS_PER_SECOND))


def _scene_templates_for_subject(order: models.AdVideoOrder) -> List[Dict[str, str]]:
    profile = _subject_profile(order)
    primary_subject_asset = "portrait" if profile["has_portrait"] else "product"
    secondary_asset = "product" if profile["has_product"] else primary_subject_asset

    if _prefer_global_scene_basis(order):
        return [
            {"role": "flow_intro", "camera": "wide flow shot", "camera_move": "slow floating drift", "asset_source": primary_subject_asset, "action": "the whole scene establishes visual rhythm and continuous motion", "gesture": "movement travels across the full frame instead of isolating one element"},
            {"role": "flow_focus", "camera": "medium composition shot", "camera_move": "gentle lateral glide", "asset_source": primary_subject_asset, "action": "the composition keeps subject, background, and copy in one coherent motion field", "gesture": "frame-wide motion carries the eye naturally forward"},
            {"role": "flow_hero", "camera": "hero scene shot", "camera_move": "orbital drift", "asset_source": secondary_asset, "action": "product gets a clean premium beauty shot", "gesture": "camera-led product reveal"},
            {"role": "flow_detail", "camera": "close cinematic shot", "camera_move": "macro slide", "asset_source": secondary_asset, "action": "detail and texture appear as part of the same visual flow", "gesture": "small motion accents keep continuity intact"},
            {"role": "flow_transition", "camera": "tracking bridge shot", "camera_move": "smooth forward motion", "asset_source": primary_subject_asset, "action": "the scene transitions fluidly with background, lighting, and framing locked together", "gesture": "movement connects one beat to the next without abrupt separation"},
            {"role": "flow_close", "camera": "closing composition shot", "camera_move": "slow settle", "asset_source": secondary_asset, "action": "the final frame resolves as one consistent commercial image", "gesture": "all motion calms into a stable branded finish"},
        ]

    return [
        {"role": "hook", "camera": "wide establishing shot", "camera_move": "slow push in", "asset_source": primary_subject_asset, "action": "spokesperson opens the product story in a confident first beat", "gesture": "eye contact and open presentation gesture"},
        {"role": "explain", "camera": "medium presenter shot", "camera_move": "gentle lateral move", "asset_source": primary_subject_asset, "action": "spokesperson explains the core benefit with readable posture", "gesture": "hand gesture toward the message and the product"},
        {"role": "hero_product", "camera": "product hero shot", "camera_move": "orbit reveal", "asset_source": secondary_asset, "action": "product gets a clean premium beauty shot", "gesture": "camera-led product reveal"},
        {"role": "demo", "camera": "demo shot", "camera_move": "tracking motion", "asset_source": primary_subject_asset, "action": "spokesperson demonstrates the product in use", "gesture": "holding, pointing, or presenting the product"},
        {"role": "detail", "camera": "close-up detail shot", "camera_move": "macro slide", "asset_source": secondary_asset, "action": "detail shot proves texture, finish, or function", "gesture": "controlled object emphasis"},
        {"role": "cta", "camera": "closing call-to-action shot", "camera_move": "slow settle", "asset_source": primary_subject_asset, "action": "spokesperson closes with a confident final beat", "gesture": "clear final call-to-action pose with the product visible"},
    ]


def _portrait_restyle_prompt(order: models.AdVideoOrder, scene: Dict[str, Any]) -> str:
    title = (order.title or "광고 상품").strip() or "광고 상품"
    style = (order.visual_style or "photorealistic").strip() or "photorealistic"
    narration_line = str(scene.get("narration_line") or "").strip()
    camera = str(scene.get("camera") or "commercial shot").strip()
    camera_move = str(scene.get("camera_move") or "controlled commercial motion").strip()
    gesture = str(scene.get("gesture") or "clear advertising gesture").strip()
    background = (order.background_prompt or "premium commercial set").strip() or "premium commercial set"
    profile = _subject_profile(order)
    subject_label = str(profile["subject_label"])
    identity_phrase = str(profile["identity_phrase"])
    motion_phrase = str(profile["motion_phrase"])

    return (
        f"newly generated scene-led commercial frame based on the reference, not the raw original image, {style}. "
        f"preserve identity continuity while matching the whole-frame composition. {identity_phrase}. "
        f"new pose, {camera}, {camera_move}. motion intent: {motion_phrase}. gesture intent: {gesture}. "
        f"background {background}. message {narration_line}. product {title}. "
        "high-end ad composition, whole-scene continuity, readable silhouette, stable anatomy, stable hands, no warped face, no melted frame, no frozen slideshow."
    ).strip()


def _product_restyle_prompt(order: models.AdVideoOrder, scene: Dict[str, Any]) -> str:
    title = (order.title or "광고 상품").strip() or "광고 상품"
    style = (order.visual_style or "photorealistic").strip() or "photorealistic"
    narration_line = str(scene.get("narration_line") or "").strip()
    camera = str(scene.get("camera") or "product commercial shot").strip()
    camera_move = str(scene.get("camera_move") or "kinetic camera motion").strip()
    gesture = str(scene.get("gesture") or "object reveal motion").strip()
    background = (order.background_prompt or "premium commercial set").strip() or "premium commercial set"
    return (
        f"newly generated scene-led premium commercial still based on the reference image, not a pasted source image, {style}. "
        f"preserve identity, upgrade reflections, materials, texture, styling, ad lighting, and keep the full composition coherent. "
        f"camera {camera}, {camera_move}. motion intent: {gesture}. "
        f"background {background}. message {narration_line}. product {title}. no static slideshow feeling, no black frame."
    ).strip()


def _scene_generation_prompt(order: models.AdVideoOrder, scene: Dict[str, Any]) -> str:
    asset_source = str(scene.get("asset_source") or "portrait").strip().lower()
    if asset_source == "portrait":
        return _portrait_restyle_prompt(order, scene)
    return _product_restyle_prompt(order, scene)


def _scene_generation_options(scene: Dict[str, Any]) -> Dict[str, float | int | str]:
    asset_source = str(scene.get("asset_source") or "portrait").strip().lower()
    if asset_source == "portrait":
        return {
            "guidance_scale": 7.8,
            "strength": 0.68,
            "steps": 30,
            "model_key": "sdxl",
        }
    return {
        "guidance_scale": 6.8,
        "strength": 0.46,
        "steps": 24,
        "model_key": "sdxl",
    }


def _normalize_storyboard_item(item: Dict[str, Any], index: int) -> Dict[str, Any]:
    cut = int(item.get("cut") or index)
    duration_sec = max(1, int(item.get("duration_sec") or 1))
    start_sec = int(item.get("start_sec") or max(0, (cut - 1) * duration_sec))
    end_sec = int(item.get("end_sec") or (start_sec + duration_sec))
    narration_line = str(item.get("narration_line") or item.get("title") or f"컷 {cut}").strip()
    title = str(item.get("title") or narration_line or f"컷 {cut}").strip() or f"컷 {cut}"
    visual_focus = str(item.get("visual_focus") or "").strip()
    if not visual_focus:
        visual_focus = " / ".join(
            part
            for part in [
                str(item.get("camera") or "").strip(),
                str(item.get("gesture") or "").strip(),
                narration_line,
            ]
            if part
        ).strip()
    normalized = dict(item)
    normalized.update(
        {
            "cut": cut,
            "title": title[:120],
            "duration_sec": duration_sec,
            "start_sec": max(0, start_sec),
            "end_sec": max(end_sec, start_sec + duration_sec),
            "narration_line": narration_line[:500],
            "visual_focus": (visual_focus or f"컷 {cut} 핵심 장면")[:300],
            "scene_prompt": str(item.get("scene_prompt") or narration_line or title).strip()[:2000],
            "asset_source": str(item.get("asset_source") or "auto").strip() or "auto",
        }
    )
    return normalized


def _compose_storyboard(order: models.AdVideoOrder) -> List[Dict[str, Any]]:
    raw_storyboard = getattr(order, "storyboard_json", None)
    if raw_storyboard:
        try:
            parsed = json.loads(raw_storyboard)
            if isinstance(parsed, list) and parsed:
                return [
                    _normalize_storyboard_item(item, index)
                    for index, item in enumerate(parsed, start=1)
                    if isinstance(item, dict)
                ]
        except Exception:
            pass

    scripts = _compose_cut_scripts(order)
    cut_count = _effective_cut_count_for_order(order)
    cut_seconds = _effective_cut_seconds_for_order(order)
    bg = (order.background_prompt or "premium commercial set").strip() or "광고 상품"
    title = (order.title or "광고 상품").strip() or "광고 상품"
    caption_parts = _split_ad_copy(order.caption_text)
    product_prompts = _get_product_image_prompts(order)
    scene_templates = _scene_templates_for_subject(order)
    profile = _subject_profile(order)
    global_basis = _prefer_global_scene_basis(order)
    stable_profile = _stability_profile_for_order(order) == "stable_90"
    subject_label = (
        "whole-scene commercial composition"
        if global_basis
        else str(profile["subject_label"])
    )
    motion_phrase = (
        "whole-frame motion continuity, fluid camera travel, and background consistency across every cut"
        if global_basis
        else str(profile["motion_phrase"])
    )
    gesture_phrase = (
        "scene-wide motion accents, flowing transitions, and composition-led movement"
        if global_basis
        else str(profile["gesture_phrase"])
    )

    storyboard: List[Dict[str, Any]] = []
    for index in range(cut_count):
        start_sec = index * cut_seconds
        end_sec = start_sec + cut_seconds
        scene_text = scripts[index] if index < len(scripts) else scripts[-1]
        scene_template = scene_templates[index % len(scene_templates)]
        caption_hint = caption_parts[index] if index < len(caption_parts) else scene_text
        asset_source = scene_template["asset_source"]
        product_index = index % len(product_prompts) if asset_source == "product" and product_prompts else None
        storyboard.append(
            _normalize_storyboard_item({
                "cut": index + 1,
                "title": f"컷 {index + 1}",
                "duration_sec": cut_seconds,
                "start_sec": max(0, start_sec),
                "end_sec": max(end_sec, start_sec + cut_seconds),
                "camera": scene_template["camera"],
                "camera_move": scene_template["camera_move"],
                "role": scene_template["role"],
                "asset_source": asset_source,
                "product_index": product_index,
                "gesture": scene_template["gesture"],
                "motion_intent": motion_phrase,
                "narration_line": caption_hint,
                "visual_focus": f"{scene_template['camera']} / {scene_template['gesture']} / {caption_hint}",
                "scene_prompt": (
                    f"{bg}. "
                    f"{scene_template['action']}. "
                    f"Camera motion: {scene_template['camera_move']}. "
                    f"Gesture/action emphasis: {scene_template['gesture']}. "
                    f"Key message: {caption_hint}. "
                    f"Product: {title}. "
                    f"Primary subject: {subject_label}. "
                    f"Motion rule: {motion_phrase}. "
                    f"Gesture rule: {gesture_phrase}. "
                    +
                    (
                        "Premium ad direction, whole-scene coherence, readable movement, no black frame."
                        if stable_profile
                        else "Premium ad direction, whole-scene coherence, readable movement, no black frame, no static slideshow."
                    )
                ),
            }, index + 1)
        )
    return storyboard


def _compose_storyboard_review(order: models.AdVideoOrder) -> List[Dict[str, Any]]:
    raw_review = getattr(order, "storyboard_review_json", None)
    if raw_review:
        try:
            parsed = json.loads(raw_review)
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, dict)]
        except Exception:
            pass
    return []


def _resolve_scene_source(scene: Optional[Dict[str, Any]]) -> Optional[str]:
    if not scene:
        return None
    asset_ref = _normalize_image_reference(scene.get("asset_ref"))
    if asset_ref and _has_ad_image_reference(asset_ref):
        return asset_ref
    return None


def _build_scene_keyframes(order: models.AdVideoOrder, storyboard: List[Dict[str, Any]]) -> List[str]:
    portrait_prompt = _normalize_image_reference(getattr(order, "portrait_image_prompt", None))
    product_prompts = _get_product_image_prompts(order)
    if not storyboard or not (portrait_prompt or product_prompts):
        return []

    fallback_keyframes: List[str] = []
    for scene in storyboard:
        source_prompt = _resolve_scene_source(scene)
        if source_prompt:
            fallback_keyframes.append(source_prompt)

    if _stability_profile_for_order(order) == "stable_90":
        return fallback_keyframes

    try:
        from backend.image.generator import stylize_reference_image
    except Exception as exc:
        logger.warning("[marketplace] scene keyframe generator unavailable: %s", exc)
        return fallback_keyframes

    negative_prompt = (
        "low quality, blurry, deformed face, extra fingers, duplicate person, cropped head, "
        "wax skin, cartoon artifact, black frame, dark frame, unreadable product, unchanged source photo, raw camera snapshot, "
        "passport photo, exact same pose as reference, exact same background as reference, flat phone selfie look"
    )
    temp_root = _resolve_marketplace_temp_root()
    keyframes: List[str] = []

    with tempfile.TemporaryDirectory(prefix="ad_scene_keyframes_", dir=str(temp_root)) as tmpdir:
        resolved_sources: Dict[str, Optional[str]] = {}
        for index, scene in enumerate(storyboard):
            source_prompt = _resolve_scene_source(scene) or default_source_prompt
            source_image = _load_ad_image_from_prompt(source_prompt, tmpdir, file_stem=f"scene_{index + 1}")

            if source_image and source_image.exists():
                keyframe = str(source_image)
            else:
                keyframe = None

            asset_source = str(scene.get("asset_source") or "portrait").strip().lower()
            scene_prompt = _scene_generation_prompt(order, scene)
            options = _scene_generation_options(scene)
            try:
                result = stylize_reference_image(
                    prompt=scene_prompt,
                    source_image_path=keyframe,
                    negative_prompt=negative_prompt,
                    width=1024,
                    height=576,
                    steps=int(options["steps"]),
                    guidance_scale=float(options["guidance_scale"]),
                    strength=float(options["strength"]),
                    seed=_ad_variation_seed(order, index),
                    model_key=str(options["model_key"]),
                )
                image_base64 = str(result.get("image_base64") or "").strip()
                if image_base64:
                    keyframes.append(f"data:image/png;base64,{image_base64}")
                    continue
            except Exception:
                logger.warning(
                    "[marketplace] scene keyframe generation failed for order %s cut %s: %s",
                    order.id,
                    index + 1,
                    exc,
                )

            if keyframe and _has_ad_image_reference(keyframe):
                keyframes.append(keyframe)

    return keyframes or fallback_keyframes


def _build_ad_engine_render_payload(order: models.AdVideoOrder) -> Dict[str, Any]:
    scene_prompt = _compose_scene_prompt(order)
    storyboard = _compose_storyboard(order)
    keyframes = _build_scene_keyframes(order, storyboard)
    subject_type = _order_subject_type(order)
    bgm_enabled = _order_bgm_enabled(order)
    bgm_mood = _order_bgm_mood(order)
    bgm_volume = _order_bgm_volume(order)
    stability_profile = _stability_profile_for_order(order)
    payload: Dict[str, Any] = {
        "title": order.title,
        "image_prompt": order.image_prompt,
        "background_prompt": order.background_prompt,
        "caption_text": order.caption_text,
        "prompt": scene_prompt,
        "scene_prompt": scene_prompt,
        "storyboard": storyboard,
        "shot_prompts": [
            str(item.get("scene_prompt") or "").strip()
            for item in storyboard
            if str(item.get("scene_prompt") or "").strip()
        ],
        "negative_prompt": "watermark, blurry motion, flicker, black frame",
        "subtitle_burn_in": True,
        "subject_type": subject_type,
        "require_realistic_human": subject_type == "human" and bool(_normalize_image_reference(getattr(order, "portrait_image_prompt", None))),
        "composition_basis": "global" if _prefer_global_scene_basis(order) else "subject",
        "composite_mode": "global_full_frame",
        "global_background_lock": True,
        "stability_profile": stability_profile,
        "motion_profile": _effective_motion_profile_for_order(order),
        "target_output_fps": _target_output_fps_for_order(order),
        "target_output_frames": _target_output_frame_hint_for_order(order),
        "voice_track": str(order.caption_text or "").strip() or None,
        "continuity_rules": [
            "scene flow continuity",
            "photoreal identity continuity",
            "stable environment realism",
            "cinematic camera continuity",
        ],
        "hero_props": [str(order.title or "hero product").strip() or "hero product"],
        "sequence_beats": [
            {
                "objective": str(scene.get("title") or f"scene {index + 1}").strip() or f"scene {index + 1}",
                "emotional_state": "conversion intent" if index == len(storyboard) - 1 else "controlled realism",
                "blocking_summary": str(scene.get("visual_focus") or scene.get("scene_prompt") or scene.get("title") or "cinematic scene progression").strip(),
                "cta_required": index == len(storyboard) - 1,
            }
            for index, scene in enumerate(storyboard[:12])
        ],
        "identity_references": [
            str(getattr(order, "portrait_image_prompt", "")).strip()
        ],
        "environment_references": [
            ref for ref in _get_product_image_prompts(order)[:3] + [str(getattr(order, "image_prompt", "")).strip()]
            if ref
        ],
        "operator_note": f"ad_order_id={order.id}; public_job_id={order.public_job_id}",
    }
    if keyframes:
        payload["keyframe_image_paths"] = keyframes
    payload.update(_build_engine_media_payload(order))
    return payload


def _is_mock_engine_url(url: str) -> bool:
    value = (url or "").lower()
    mock_markers = [
        "127.0.0.1",
        "localhost",
        "host.docker.internal:18081",
        "mock",
    ]
    return any(marker in value for marker in mock_markers)


def _is_local_engine_url(url: str) -> bool:
    value = (url or "").lower()
    local_markers = [
        "127.0.0.1",
        "localhost",
        "host.docker.internal",
    ]
    return any(marker in value for marker in local_markers)


def _is_true_env(name: str, default: str = "false") -> bool:
    return (os.getenv(name, default) or default).strip().lower() in {"1", "true", "yes", "on"}


def _assert_engine_endpoint_reachable(endpoint: str, label: str) -> None:
    parsed = urlparse(endpoint)
    host = (parsed.hostname or "").strip()
    if not host:
        raise HTTPException(status_code=400, detail=f"{label} 엔드포인트 URL 형식이 올바르지 않습니다.")

    if parsed.port:
        port = parsed.port
    elif parsed.scheme == "https":
        port = 443
    else:
        port = 80

    timeout_sec = 2.5
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            pass
    except OSError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{label} 엔드포인트 연결 실패({host}:{port}): {exc}",
        ) from exc


def _validate_ad_engine_preflight(engine_type_raw: Optional[str]) -> str:
    engine_type = (engine_type_raw or "internal_ffmpeg").strip().lower()
    if engine_type not in {"internal_ffmpeg", "external_api", "dedicated_engine"}:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 렌더 엔진입니다: {engine_type}")

    fallback_to_internal = _is_true_env("VIDEO_ENGINE_FALLBACK_TO_INTERNAL", "false")

    if engine_type == "internal_ffmpeg":
        ffmpeg_bin = (os.getenv("FFMPEG_BIN", "ffmpeg") or "ffmpeg").strip() or "ffmpeg"
        resolved = ffmpeg_bin
        if not Path(ffmpeg_bin).is_absolute():
            resolved = shutil.which(ffmpeg_bin) or ""
        if not resolved:
            raise HTTPException(
                status_code=400,
                detail=(
                    "FFmpeg 실행 파일을 찾을 수 없습니다. "
                    "FFMPEG_BIN 환경변수를 확인하거나 ffmpeg를 설치하세요."
                ),
            )
        return engine_type

    if fallback_to_internal:
        return engine_type

    if engine_type == "external_api":
        endpoint = (os.getenv("VIDEO_EXTERNAL_API_URL", "") or "").strip()
        if not endpoint:
            raise HTTPException(
                status_code=400,
                detail="external_api 엔진 설정 누락: VIDEO_EXTERNAL_API_URL 환경변수를 설정하세요.",
            )
        _assert_engine_endpoint_reachable(endpoint, "external_api")
        return engine_type

    endpoint = (os.getenv("VIDEO_DEDICATED_ENGINE_URL", "") or "").strip()
    if not endpoint:
        raise HTTPException(
            status_code=400,
            detail="dedicated_engine 설정 누락: VIDEO_DEDICATED_ENGINE_URL 환경변수를 설정하세요.",
        )

    require_generative = _is_true_env("VIDEO_REQUIRE_GENERATIVE_ENGINE", "true")
    allow_local_self_engine = _is_true_env("VIDEO_ALLOW_LOCAL_DEDICATED_ENGINE", "false")

    if require_generative and _is_mock_engine_url(endpoint):
        if not (allow_local_self_engine and _is_local_engine_url(endpoint)):
            raise HTTPException(
                status_code=400,
                detail=(
                    "dedicated_engine이 mock/local 엔드포인트로 설정되어 있습니다. "
                    "실제 텍스트-투-비디오 엔진 URL을 VIDEO_DEDICATED_ENGINE_URL에 설정하세요."
                ),
            )

    if require_generative and _is_local_engine_url(endpoint) and not allow_local_self_engine:
        raise HTTPException(
            status_code=400,
            detail=(
                "로컬 dedicated_engine은 정책상 차단되어 있습니다. "
                "VIDEO_ALLOW_LOCAL_DEDICATED_ENGINE=true 설정 또는 비로컬 엔드포인트를 사용하세요."
            ),
        )

    _assert_engine_endpoint_reachable(endpoint, "dedicated_engine")

    return engine_type


def _generate_video_internal_ffmpeg(order: models.AdVideoOrder) -> bytes:
    ffmpeg_bin = os.getenv("FFMPEG_BIN", "ffmpeg")
    cut_count = _order_cut_count(order)
    cut_seconds = _order_cut_seconds(order)
    subtitle_speed = _order_subtitle_speed(order)
    audio_volume = _order_audio_volume(order)
    render_quality = _order_render_quality(order)
    bgm_enabled = _order_bgm_enabled(order)
    bgm_mood = _order_bgm_mood(order)
    bgm_volume = _order_bgm_volume(order)

    if render_quality == "ultra":
        resolution = "1920x1080"
        crf = "12"
        preset = "slower"
    elif render_quality == "standard":
        resolution = "1280x720"
        crf = "18"
        preset = "medium"
    else:
        resolution = "1920x1080"
        crf = "15"
        preset = "slow"

    volume_ratio = max(0.0, min(2.0, audio_volume / 100.0))
    audio_filter = f"atempo={subtitle_speed:.2f},volume={volume_ratio:.2f}"
    freq_base = 220 if (order.voice_gender or "female") == "female" else 140
    bgm_profile = _bgm_profile(bgm_mood)
    bgm_source = _bgm_lavfi_source(cut_seconds, bgm_mood)
    bgm_enabled = bgm_enabled and bgm_volume > 0
    bgm_volume_ratio = max(0.0, min(1.0, bgm_volume / 100.0))
    bgm_fade_in = min(0.8, max(0.3, cut_seconds / 8.0))
    bgm_fade_out = min(1.4, max(0.6, cut_seconds / 5.0))
    bgm_fade_out_start = max(0.0, cut_seconds - bgm_fade_out)
    bgm_filter = (
        f"volume={bgm_volume_ratio:.2f},"
        f"lowpass=f={int(bgm_profile['lowpass'])},"
        "aecho=0.8:0.4:45:0.18,"
        f"afade=t=in:st=0:d={bgm_fade_in:.2f},"
        f"afade=t=out:st={bgm_fade_out_start:.2f}:d={bgm_fade_out:.2f}"
    )
    captions = _compose_cut_scripts(order)
    storyboard = _compose_storyboard(order)
    colors = ["black", "#1f2937", "#111827", "#0b1220", "#1e293b", "#0f172a"]

    temp_root = _resolve_marketplace_temp_root()
    with tempfile.TemporaryDirectory(prefix="ad_video_", dir=str(temp_root)) as tmpdir:
        out_path = Path(tmpdir) / "ad.mp4"
        default_source_prompt = _get_reference_image_prompt(order)
        source_image_cache: Dict[str, Optional[Path]] = {}

        def _load_cached_source_image(image_prompt: Optional[str]) -> Optional[Path]:
            normalized_prompt = (image_prompt or "").strip()
            cache_key = normalized_prompt or "__default__"
            if cache_key not in source_image_cache:
                source_image_cache[cache_key] = _load_ad_image_from_prompt(
                    normalized_prompt or None,
                    tmpdir,
                    file_stem=f"scene_{len(source_image_cache) + 1}",
                )
            return source_image_cache.get(cache_key)

        segment_paths: List[Path] = []
        for index in range(cut_count):
            scene = storyboard[index] if index < len(storyboard) else None
            source_prompt = _resolve_storyboard_scene_source(order, scene) or default_source_prompt
            source_image = _load_cached_source_image(source_prompt)
            cut_caption = _ffmpeg_text(captions[index], limit=80)
            cut_title = _ffmpeg_text(order.title, limit=48)
            segment_path = Path(tmpdir) / f"cut_{index + 1}.mp4"
            segment_paths.append(segment_path)

            text_overlay = (
                "drawbox=x=36:y=ih-206:w=iw-72:h=162:color=black@0.45:t=fill,"
                "drawtext="
                f"text='컷 {index + 1}/{cut_count}  {cut_title}':"
                "fontcolor=white:fontsize=34:"
                "x=56:y=h-178,"
                "drawtext="
                f"text='{cut_caption}':"
                "fontcolor=white:fontsize=42:"
                "x=56:y=h-120"
            )

            if source_image and source_image.exists():
                crop_resolution = resolution.replace("x", ":")
                vf_with_image = (
                    f"scale={resolution}:force_original_aspect_ratio=increase,"
                    f"crop={crop_resolution},"
                    f"{text_overlay}"
                )
                if bgm_enabled:
                    filter_complex = (
                        f"[1:a]{audio_filter}[voice];"
                        f"[2:a]{bgm_filter}[bgm];"
                        "[voice][bgm]amix=inputs=2:duration=first[aout]"
                    )
                    cut_cmd = [
                        ffmpeg_bin,
                        "-y",
                        "-loop",
                        "1",
                        "-framerate",
                        "25",
                        "-i",
                        str(source_image),
                        "-f",
                        "lavfi",
                        "-i",
                        f"sine=frequency={freq_base + (index * 8)}:duration={cut_seconds}",
                        "-f",
                        "lavfi",
                        "-i",
                        bgm_source,
                        "-t",
                        str(cut_seconds),
                        "-vf",
                        vf_with_image,
                        "-filter_complex",
                        filter_complex,
                        "-map",
                        "0:v:0",
                        "-map",
                        "[aout]",
                        "-c:v",
                        "libx264",
                        "-preset",
                        preset,
                        "-crf",
                        crf,
                        "-pix_fmt",
                        "yuv420p",
                        "-c:a",
                        "aac",
                        "-shortest",
                        str(segment_path),
                    ]
                else:
                    cut_cmd = [
                        ffmpeg_bin,
                        "-y",
                        "-loop",
                        "1",
                        "-framerate",
                        "25",
                        "-i",
                        str(source_image),
                        "-f",
                        "lavfi",
                        "-i",
                        f"sine=frequency={freq_base + (index * 8)}:duration={cut_seconds}",
                        "-t",
                        str(cut_seconds),
                        "-vf",
                        vf_with_image,
                        "-af",
                        audio_filter,
                        "-c:v",
                        "libx264",
                        "-preset",
                        preset,
                        "-crf",
                        crf,
                        "-pix_fmt",
                        "yuv420p",
                        "-c:a",
                        "aac",
                        "-shortest",
                        str(segment_path),
                    ]
            else:
                if bgm_enabled:
                    filter_complex = (
                        f"[1:a]{audio_filter}[voice];"
                        f"[2:a]{bgm_filter}[bgm];"
                        "[voice][bgm]amix=inputs=2:duration=first[aout]"
                    )
                    cut_cmd = [
                        ffmpeg_bin,
                        "-y",
                        "-f",
                        "lavfi",
                        "-i",
                        f"color=c={colors[index % len(colors)]}:s={resolution}:d={cut_seconds}",
                        "-f",
                        "lavfi",
                        "-i",
                        f"sine=frequency={freq_base + (index * 8)}:duration={cut_seconds}",
                        "-f",
                        "lavfi",
                        "-i",
                        bgm_source,
                        "-vf",
                        text_overlay,
                        "-filter_complex",
                        filter_complex,
                        "-map",
                        "0:v:0",
                        "-map",
                        "[aout]",
                        "-c:v",
                        "libx264",
                        "-preset",
                        preset,
                        "-crf",
                        crf,
                        "-pix_fmt",
                        "yuv420p",
                        "-c:a",
                        "aac",
                        "-shortest",
                        str(segment_path),
                    ]
                else:
                    cut_cmd = [
                        ffmpeg_bin,
                        "-y",
                        "-f",
                        "lavfi",
                        "-i",
                        f"color=c={colors[index % len(colors)]}:s={resolution}:d={cut_seconds}",
                        "-f",
                        "lavfi",
                        "-i",
                        f"sine=frequency={freq_base + (index * 8)}:duration={cut_seconds}",
                        "-vf",
                        text_overlay,
                        "-af",
                        audio_filter,
                        "-c:v",
                        "libx264",
                        "-preset",
                        preset,
                        "-crf",
                        crf,
                        "-pix_fmt",
                        "yuv420p",
                        "-c:a",
                        "aac",
                        "-shortest",
                        str(segment_path),
                    ]

            cut_proc = subprocess.run(
                cut_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            if (cut_proc.returncode != 0 or not segment_path.exists()) and source_image and source_image.exists():
                if bgm_enabled:
                    filter_complex = (
                        f"[1:a]{audio_filter}[voice];"
                        f"[2:a]{bgm_filter}[bgm];"
                        "[voice][bgm]amix=inputs=2:duration=first[aout]"
                    )
                    fallback_cut_cmd = [
                        ffmpeg_bin,
                        "-y",
                        "-f",
                        "lavfi",
                        "-i",
                        f"color=c={colors[index % len(colors)]}:s={resolution}:d={cut_seconds}",
                        "-f",
                        "lavfi",
                        "-i",
                        f"sine=frequency={freq_base + (index * 8)}:duration={cut_seconds}",
                        "-f",
                        "lavfi",
                        "-i",
                        bgm_source,
                        "-vf",
                        text_overlay,
                        "-filter_complex",
                        filter_complex,
                        "-map",
                        "0:v:0",
                        "-map",
                        "[aout]",
                        "-c:v",
                        "libx264",
                        "-preset",
                        preset,
                        "-crf",
                        crf,
                        "-pix_fmt",
                        "yuv420p",
                        "-c:a",
                        "aac",
                        "-shortest",
                        str(segment_path),
                    ]
                else:
                    fallback_cut_cmd = [
                        ffmpeg_bin,
                        "-y",
                        "-f",
                        "lavfi",
                        "-i",
                        f"color=c={colors[index % len(colors)]}:s={resolution}:d={cut_seconds}",
                        "-f",
                        "lavfi",
                        "-i",
                        f"sine=frequency={freq_base + (index * 8)}:duration={cut_seconds}",
                        "-vf",
                        text_overlay,
                        "-af",
                        audio_filter,
                        "-c:v",
                        "libx264",
                        "-preset",
                        preset,
                        "-crf",
                        crf,
                        "-pix_fmt",
                        "yuv420p",
                        "-c:a",
                        "aac",
                        "-shortest",
                        str(segment_path),
                    ]
                cut_proc = subprocess.run(
                    fallback_cut_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

            if cut_proc.returncode != 0 or not segment_path.exists():
                raise RuntimeError(
                    f"ffmpeg cut render failed: {cut_proc.stderr[-800:]}"
                )

        concat_list = Path(tmpdir) / "concat.txt"
        concat_content = "\n".join([
            f"file '{p.as_posix()}'" for p in segment_paths
        ])
        concat_list.write_text(concat_content, encoding="utf-8")

        concat_cmd = [
            ffmpeg_bin,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            str(out_path),
        ]
        concat_proc = subprocess.run(
            concat_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if concat_proc.returncode != 0 or not out_path.exists():
            reencode_cmd = [
                ffmpeg_bin,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(out_path),
            ]
            reencode_proc = subprocess.run(
                reencode_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if reencode_proc.returncode != 0 or not out_path.exists():
                raise RuntimeError(
                    f"ffmpeg concat failed: {reencode_proc.stderr[:400]}"
                )

        return out_path.read_bytes()


def _generate_video_external_api(order: models.AdVideoOrder) -> tuple[bytes, Optional[str]]:
    endpoint = (os.getenv("VIDEO_EXTERNAL_API_URL", "") or "").strip()
    if not endpoint:
        raise RuntimeError("VIDEO_EXTERNAL_API_URL not configured")

    headers = _engine_headers("VIDEO_EXTERNAL_API_KEY")
    payload = _build_ad_engine_render_payload(order)

    response = requests.post(endpoint, headers=headers, json=payload, timeout=180)
    response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    if "video/" in content_type:
        return response.content, None

    data = _safe_json(response, "external api")
    job_id = str(data.get("job_id") or "").strip() or None
    video_url = str(data.get("video_url") or "").strip()
    if not video_url:
        raise RuntimeError("external api response missing video_url")
    video_resp = requests.get(video_url, timeout=180)
    video_resp.raise_for_status()
    return video_resp.content, job_id


def _dedicated_engine_adapter_mode() -> str:
    return (os.getenv("VIDEO_DEDICATED_ENGINE_ADAPTER", "default") or "default").strip().lower()


def _dedicated_engine_normalize_path(path: str, default_path: str) -> str:
    value = (path or default_path).strip() or default_path
    return value if value.startswith("/") else f"/{value}"


def _dedicated_engine_nested_value(data: Dict[str, Any], path: str) -> Any:
    current: Any = data
    for segment in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
    return current


def _dedicated_engine_first_value(data: Dict[str, Any], *paths: str) -> Any:
    for path in paths:
        value = _dedicated_engine_nested_value(data, path)
        if value not in {None, ""}:
            return value
    return None


def _generate_video_dedicated_engine(
    order: models.AdVideoOrder,
    progress_callback: Optional[Callable[[str, str, Optional[int], str], None]] = None,
) -> tuple[bytes, Optional[str]]:
    endpoint = (os.getenv("VIDEO_DEDICATED_ENGINE_URL", "") or "").strip()
    if not endpoint:
        raise RuntimeError("VIDEO_DEDICATED_ENGINE_URL not configured")

    require_generative = os.getenv("VIDEO_REQUIRE_GENERATIVE_ENGINE", "true").lower() in {
        "1", "true", "yes", "on"
    }
    allow_local_self_engine = os.getenv("VIDEO_ALLOW_LOCAL_DEDICATED_ENGINE", "false").lower() in {
        "1", "true", "yes", "on"
    }
    if require_generative and _is_mock_engine_url(endpoint):
        if allow_local_self_engine and _is_local_engine_url(endpoint):
            pass
        else:
            raise RuntimeError(
                "Mock dedicated engine endpoint is configured. "
                "Set VIDEO_DEDICATED_ENGINE_URL to a real text-to-video engine endpoint."
            )

    if require_generative and _is_local_engine_url(endpoint) and not allow_local_self_engine:
        raise RuntimeError(
            "Local dedicated engine endpoint is blocked by policy. "
            "Set VIDEO_ALLOW_LOCAL_DEDICATED_ENGINE=true for self-hosted local engine, "
            "or use a non-local dedicated engine URL."
        )

    adapter_mode = _dedicated_engine_adapter_mode()
    default_submit_path = "/jobs"
    submit_path = _dedicated_engine_normalize_path(
        os.getenv("VIDEO_DEDICATED_SUBMIT_PATH", default_submit_path),
        default_submit_path,
    )
    default_status_path = submit_path.rstrip("/") + "/{job_id}"
    status_path_template = _dedicated_engine_normalize_path(
        os.getenv("VIDEO_DEDICATED_STATUS_PATH_TEMPLATE", default_status_path),
        default_status_path,
    )
    if "{job_id}" not in status_path_template:
        status_path_template = status_path_template.rstrip("/") + "/{job_id}"
    result_url_tpl = endpoint.rstrip("/") + status_path_template
    submit_url = endpoint.rstrip("/") + submit_path
    headers = _engine_headers("VIDEO_DEDICATED_ENGINE_API_KEY")
    payload = _build_ad_engine_render_payload(order)
    payload["order_ref"] = f"ad-order-{order.id}"
    if adapter_mode in {"4d", "4d_designer", "4d-designer"}:
        payload.setdefault("adapter_mode", "4d_designer")

    submit = requests.post(submit_url, headers=headers, json=payload, timeout=60)
    submit.raise_for_status()
    job = _safe_json(submit, "dedicated submit")
    job_id = str(_dedicated_engine_first_value(job, "job_id", "id", "job.job_id") or "").strip()
    if not job_id:
        raise RuntimeError("dedicated engine response missing job_id")
    submit_status = str(_dedicated_engine_first_value(job, "status", "state") or "accepted").lower().strip()
    if submit_status and submit_status not in DEDICATED_STATUS_ACCEPTED:
        raise RuntimeError(f"dedicated engine invalid submit status: {submit_status}")
    if progress_callback is not None:
        submit_message = str(_dedicated_engine_first_value(job, "status_message", "message") or "").strip()
        progress_callback(job_id, submit_status or "accepted", 0, submit_message)

    timeout_sec = int(os.getenv("VIDEO_DEDICATED_TIMEOUT_SEC", "600"))
    poll_interval = float(os.getenv("VIDEO_DEDICATED_POLL_SEC", "3"))
    started = time.time()
    while time.time() - started < timeout_sec:
        poll = requests.get(result_url_tpl.format(job_id=job_id), headers=headers, timeout=30)
        poll.raise_for_status()
        data = _safe_json(poll, "dedicated status")
        payload_job_id = str(_dedicated_engine_first_value(data, "job_id", "id", "job.job_id") or job_id).strip()
        if payload_job_id != job_id:
            raise RuntimeError("dedicated engine returned mismatched job_id")
        status_value = str(_dedicated_engine_first_value(data, "status", "state", "job.status") or "").lower()
        if status_value not in DEDICATED_STATUS_ACCEPTED:
            raise RuntimeError(f"dedicated engine invalid status: {status_value}")

        progress_percent = _parse_progress_percent(
            _dedicated_engine_first_value(data, "progress_percent", "progress", "metadata.progress_percent")
        )
        error_message = str(
            _dedicated_engine_first_value(
                data,
                "status_message",
                "message",
                "error_message",
                "error",
            ) or ""
        ).strip()
        if progress_callback is not None:
            progress_callback(job_id, status_value, progress_percent, error_message)

        if status_value in DEDICATED_STATUS_COMPLETED:
            video_url = str(
                _dedicated_engine_first_value(
                    data,
                    "video_url",
                    "result.video_url",
                    "output.video_url",
                ) or ""
            ).strip()
            if not video_url:
                raise RuntimeError("dedicated engine completed without video_url")
            video_resp = requests.get(video_url, timeout=180)
            video_resp.raise_for_status()
            return video_resp.content, job_id
        if status_value in DEDICATED_STATUS_FAILED:
            error_text = (
                str(_dedicated_engine_first_value(data, "error_message", "error", "status_message", "message") or "").strip()
                or "dedicated engine failed"
            )
            error_code = str(_dedicated_engine_first_value(data, "error_code", "error.code") or "").strip()
            if error_code:
                raise RuntimeError(f"{error_code}: {error_text}")
            raise RuntimeError(error_text)
        time.sleep(poll_interval)

    raise RuntimeError("dedicated engine timeout")


def _build_movie_studio_payload_from_order(order: models.AdVideoOrder) -> Dict[str, object]:
    storyboard = _compose_storyboard(order)
    sequence_beats = [
        {
            "objective": str(scene.get("title") or f"scene {index + 1}").strip() or f"scene {index + 1}",
            "emotional_state": "conversion intent" if index == len(storyboard) - 1 else "controlled realism",
            "blocking_summary": str(scene.get("visual_focus") or scene.get("scene_prompt") or scene.get("title") or "cinematic scene progression").strip(),
            "cta_required": index == len(storyboard) - 1,
        }
        for index, scene in enumerate(storyboard[:12])
    ]

    identity_references: List[str] = []
    portrait_reference = str(getattr(order, "portrait_image_prompt", "") or "").strip()
    if portrait_reference:
        identity_references.append(portrait_reference)

    product_images = [str(item).strip() for item in (_parse_ad_image_prompt_list(getattr(order, "product_image_prompts", None)) or []) if str(item).strip()]
    environment_references = product_images[:3]
    if not environment_references:
        primary_image = str(getattr(order, "image_prompt", "") or "").strip()
        if primary_image:
            environment_references.append(primary_image)

    return {
        "project_id": f"ad-order-{order.id}",
        "title": str(order.title or f"ad-order-{order.id}").strip() or f"ad-order-{order.id}",
        "synopsis": str(getattr(order, "scenario_script", None) or order.caption_text or order.background_prompt or order.title or "movie studio ad order").strip(),
        "genre": "commercial cinema",
        "tone": str(getattr(order, "visual_style", "photorealistic") or "photorealistic").strip() or "photorealistic",
        "realism_level": "photoreal",
        "species": "human" if portrait_reference else "product",
        "environment_type": "studio",
        "location_summary": str(order.background_prompt or "premium commercial studio").strip(),
        "background_prompt": str(order.background_prompt or "premium commercial studio").strip(),
        "target_duration_seconds": _order_duration_seconds(order),
        "target_fps": 24,
        "target_resolution": "1080x1920",
        "voice_track": str(order.caption_text or "").strip() or None,
        "continuity_rules": [
            "scene flow continuity",
            "photoreal identity continuity",
            "stable environment realism",
            "cinematic camera continuity",
        ],
        "hero_props": [str(order.title or "hero product").strip() or "hero product"],
        "sequence_beats": sequence_beats,
        "identity_references": identity_references,
        "environment_references": environment_references,
        "operator_note": f"ad_order_id={order.id}; public_job_id={order.public_job_id}",
    }


def _generate_video_movie_studio(
    order: models.AdVideoOrder,
    progress_callback: Optional[Callable[[str, str, Optional[int], str], None]] = None,
) -> tuple[bytes, Optional[str]]:
    from backend.movie_studio.orchestration.studio_orchestrator import execute_movie_studio_project

    job_id = str(order.public_job_id or order.id)
    if progress_callback is not None:
        progress_callback(job_id, "planning", 5, "movie studio scene flow planning")
    result = execute_movie_studio_project(_build_movie_studio_payload_from_order(order))
    if progress_callback is not None:
        progress_callback(job_id, "rendering", 85, "movie studio render completed")

    render_result = dict((result.get("render_manifest") or {}).get("render_result") or {})
    output_mp4_path = str(render_result.get("output_mp4_path") or result.get("output_mp4_path") or "").strip()
    if str(render_result.get("status") or "") != "completed" or not output_mp4_path:
        raise RuntimeError(f"movie studio render failed: {render_result.get('error_message') or 'output missing'}")

    quality_result = dict((result.get("quality_runtime_manifest") or {}).get("quality_result") or result.get("quality_result") or {})
    if not bool(quality_result.get("passed", False)):
        failures = quality_result.get("failures") or []
        failure_text = "; ".join(str(item.get("message") or item.get("code") or "quality failure") for item in failures[:5] if isinstance(item, dict))
        raise RuntimeError(f"movie studio quality gate failed: {failure_text or 'unknown failure'}")

    video_path = Path(output_mp4_path)
    if not video_path.exists() or not video_path.is_file():
        raise RuntimeError("movie studio output mp4 missing")

    if progress_callback is not None:
        progress_callback(job_id, "completed", 100, f"movie studio output ready: {video_path.name}")
    return video_path.read_bytes(), None


def _generate_video_by_engine(
    order: models.AdVideoOrder,
    progress_callback: Optional[Callable[[str, str, Optional[int], str], None]] = None,
) -> tuple[bytes, Optional[str]]:
    engine_type = (order.engine_type or "internal_ffmpeg").strip().lower()
    fallback = os.getenv("VIDEO_ENGINE_FALLBACK_TO_INTERNAL", "false").lower() in {
        "1", "true", "yes", "on"
    }

    try:
        if engine_type == "external_api":
            return _generate_video_external_api(order)
        if engine_type == "dedicated_engine":
            return _generate_video_movie_studio(order, progress_callback=progress_callback)
        return _generate_video_internal_ffmpeg(order), None
    except Exception:
        if fallback and engine_type != "internal_ffmpeg":
            return _generate_video_internal_ffmpeg(order), None
        raise


def _process_ad_order_job(order_id: int) -> None:
    db = SessionLocal()
    try:
        order = db.query(models.AdVideoOrder).filter(models.AdVideoOrder.id == order_id).first()
        if not order:
            return

        order.status = models.AdVideoOrderStatus.PROCESSING.value
        order.progress_percent = 10
        order.error_message = None
        order.external_job_id = None
        order.output_file_key = None
        order.output_filename = None
        order.output_video_key = None
        order.output_video_filename = None
        order.quality_score = None
        order.quality_gate_passed = False
        order.quality_feedback = None
        order.quality_checked_at = None
        db.commit()
        db.refresh(order)

        bundle = _build_ad_package_zip(order)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        slug = _safe_filename(order.title)
        public_job_id = str(order.public_job_id or order.id)
        zip_name = f"{public_job_id}.zip"
        zip_key = f"storage/packages/{order.user_id}/{zip_name}"
        stored_zip_key = _store_bytes_with_fallback(bundle, zip_key, "application/zip")

        order.output_file_key = stored_zip_key
        order.output_filename = zip_name
        order.progress_percent = 45
        db.commit()

        order.status = models.AdVideoOrderStatus.RENDERING.value
        order.progress_percent = 60
        db.commit()

        last_engine_progress: Optional[int] = None
        last_engine_status = ""
        last_engine_message = ""

        def _dedicated_progress_callback(
            external_job_id: str,
            engine_status: str,
            engine_progress: Optional[int],
            engine_message: str,
        ) -> None:
            nonlocal last_engine_progress, last_engine_status, last_engine_message, order

            normalized_status = str(engine_status or "").strip().lower()
            normalized_message = str(engine_message or "").strip()
            normalized_progress = None if engine_progress is None else max(0, min(100, int(engine_progress)))
            mapped_progress = order.progress_percent or 60
            if normalized_progress is not None:
                mapped_progress = max(60, min(95, 60 + int(normalized_progress * 35 / 100)))

            changed = False
            if external_job_id and order.external_job_id != external_job_id:
                order.external_job_id = external_job_id
                changed = True
            if normalized_status and normalized_status != last_engine_status:
                last_engine_status = normalized_status
                changed = True
            if normalized_progress is not None and normalized_progress != last_engine_progress:
                last_engine_progress = normalized_progress
                if (order.progress_percent or 0) != mapped_progress:
                    order.progress_percent = mapped_progress
                    changed = True
            if normalized_message and normalized_message != last_engine_message:
                last_engine_message = normalized_message
                order.error_message = normalized_message
                changed = True

            if changed:
                db.commit()

        video_bytes, external_job_id = _generate_video_by_engine(
            order,
            progress_callback=_dedicated_progress_callback,
        )
        video_name = f"{public_job_id}.mp4"
        video_key = f"storage/videos/{order.user_id}/{video_name}"
        stored_video_key = _store_bytes_with_fallback(video_bytes, video_key, "video/mp4")

        quality_result = _evaluate_ad_order_quality(order, video_bytes)
        order.quality_score = quality_result["score"]
        order.quality_gate_passed = bool(quality_result["passed"])
        order.quality_feedback = quality_result["feedback"] or None
        order.quality_checked_at = datetime.utcnow()

        order.output_video_key = stored_video_key
        order.output_video_filename = video_name
        order.external_job_id = external_job_id
        if not order.quality_gate_passed:
            quality_retry_count = int(getattr(order, "quality_retry_count", 0) or 0)
            if quality_retry_count < MARKETPLACE_MAX_AUTO_QUALITY_RETRIES:
                order.quality_retry_count = quality_retry_count + 1
                db.commit()
                _reset_ad_order_for_retry(
                    db,
                    order,
                    retry_reason=(
                        f"품질 게이트 미통과(score={order.quality_score}): "
                        f"{order.quality_feedback or '자동 재시도'}"
                    ),
                    preserve_quality_feedback=True,
                )
                return

            order.status = models.AdVideoOrderStatus.FAILED.value
            order.progress_percent = 100
            order.error_message = (
                f"품질 게이트 실패(score={order.quality_score}): "
                f"{order.quality_feedback or '시장형 광고 기준 미충족'}"
            )
            db.commit()
            return

        order.status = models.AdVideoOrderStatus.COMPLETED.value
        order.progress_percent = 100
        order.error_message = None
        db.commit()
    except Exception as exc:
        order = db.query(models.AdVideoOrder).filter(models.AdVideoOrder.id == order_id).first()
        if order:
            failure_trace = _compose_trace_fields("FLOW-AD-001", "FLOW-AD-001-3", "WORKER_FAILED")
            order.trace_id = failure_trace["trace_id"]
            order.flow_id = failure_trace["flow_id"]
            order.step_id = failure_trace["step_id"]
            order.action = failure_trace["action"]
            order.status = models.AdVideoOrderStatus.FAILED.value
            order.error_message = str(exc)
            order.progress_percent = 100
            _write_feature_execution_log(
                db,
                user_id=order.user_id,
                entity_type="ad_video_order",
                entity_id=str(order.id),
                flow_id=failure_trace["flow_id"],
                step_id=failure_trace["step_id"],
                action=failure_trace["action"],
                status="failed",
                message="광고 주문 worker 실패",
                payload={"error": str(exc)},
            )
            _enqueue_feature_retry_record(
                db,
                user_id=order.user_id,
                entity_type="ad_video_order",
                entity_id=str(order.id),
                flow_id=failure_trace["flow_id"],
                step_id=failure_trace["step_id"],
                action=failure_trace["action"],
                queue_name=VIDEO_RENDER_QUEUE_NAME,
                payload={"order_id": order.id, "job_id": order.public_job_id},
                last_error=str(exc),
                status="failed",
                attempt_count=int(getattr(order, "quality_retry_count", 0) or 0),
            )
            db.commit()
    finally:
        db.close()


def _reset_ad_order_for_retry(
    db: Session,
    order: models.AdVideoOrder,
    retry_reason: Optional[str] = None,
    preserve_quality_feedback: bool = False,
) -> models.AdVideoOrder:
    order.status = models.AdVideoOrderStatus.QUEUED.value
    order.progress_percent = 0
    order.public_job_id = str(uuid4())
    order.external_job_id = None
    order.output_file_key = None
    order.output_filename = None
    order.output_video_key = None
    order.output_video_filename = None
    if not preserve_quality_feedback:
        order.quality_score = None
        order.quality_gate_passed = False
        order.quality_feedback = None
        order.quality_checked_at = None
    order.error_message = retry_reason
    order.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(order)
    ensure_ad_order_runtime_ready()
    _enqueue_ad_order(order.id, order.public_job_id)
    _enqueue_feature_retry_record(
        db,
        user_id=current_user.id,
        entity_type="ad_video_order",
        entity_id=str(order.id),
        flow_id=order.flow_id or "FLOW-AD-001",
        step_id=order.step_id or "FLOW-AD-001-1",
        action="QUEUE_ENQUEUE",
        queue_name=VIDEO_RENDER_QUEUE_NAME,
        payload={"order_id": order.id, "job_id": order.public_job_id},
        status="queued",
        attempt_count=0,
    )
    db.commit()

    return order


def _ad_order_worker_loop() -> None:
    while True:
        queue_item: Optional[Dict[str, Any]] = None
        _mark_ad_worker_heartbeat()
        try:
            redis_client = _require_video_queue_redis()
            result = redis_client.brpop(VIDEO_RENDER_QUEUE_NAME, timeout=5)
            if result:
                _, raw_item = result
                queue_item = json.loads(raw_item)
        except HTTPException:
            time.sleep(2)
            continue
        except (RedisError, json.JSONDecodeError):
            time.sleep(2)
            continue

        if queue_item is None:
            continue

        order_id = int(queue_item.get("order_id") or 0)
        _mark_ad_worker_heartbeat(order_id)
        try:
            _maybe_run_marketplace_storage_cleanup()
            _process_ad_order_job(order_id)
        finally:
            with _ad_worker_lock:
                _ad_enqueued_ids.discard(order_id)


def run_ad_order_worker() -> None:
    _mark_ad_worker_heartbeat()
    logger.info(
        "[marketplace] video render worker consuming Redis queue '%s'",
        VIDEO_RENDER_QUEUE_NAME,
    )
    _ad_order_worker_loop()


def _ensure_ad_order_worker_started() -> None:
    ensure_ad_order_runtime_ready()


def _build_ad_package_zip(order: models.AdVideoOrder) -> bytes:
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    duration = _order_duration_seconds(order)
    cut_count = _order_cut_count(order)
    cut_seconds = _order_cut_seconds(order)
    brief_text = (
        f"# Animation Photorealistic Ad Brief\n\n"
        f"- Order ID: {order.id}\n"
        f"- Title: {order.title}\n"
        f"- Voice: {order.voice_gender}\n"
        f"- Duration: {duration}s ({cut_seconds}s x {cut_count} cuts)\n"
        f"- Style: {order.visual_style}\n"
        f"- Subtitle Speed: {_order_subtitle_speed(order):.2f}x\n"
        f"- Render Quality: {_order_render_quality(order)}\n"
        f"- Audio Volume: {_order_audio_volume(order)}%\n"
        f"- Generated At: {ts}\n\n"
        f"## Image Prompt\n{order.image_prompt}\n\n"
        f"## Background Prompt\n{order.background_prompt}\n\n"
        f"## Caption Text\n{order.caption_text}\n"
    )

    shotlist = {
        "order_id": order.id,
        "title": order.title,
        "voice_gender": order.voice_gender,
        "duration_seconds": duration,
        "target_fps": 24,
        "target_resolution": "1080x1920",
        "scene_units": [
            {
                "id": f"ad_order_{order.id}_scene_{i + 1}",
                "ref": f"컷 {i + 1}",
                "start_sec": round(cut_seconds * i, 3),
                "end_sec": round(cut_seconds * (i + 1), 3),
                "duration_sec": cut_seconds,
                "image": _get_reference_image_prompt(order),
                "media_type": "image",
                "requires_realistic_human": bool(portrait_image_prompt),
            }
            for i in range(cut_count)
        ],
    }

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("brief.md", brief_text)
        zf.writestr("shotlist.json", json.dumps(shotlist, ensure_ascii=False, indent=2))
    return zip_buffer.getvalue()
