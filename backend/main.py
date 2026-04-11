import ctypes
import importlib.util
import json
import logging
import os
import re
import sys
import threading
import time
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
_ad_worker_thread: Optional[threading.Thread] = None
_self_run_video_worker_thread: Optional[threading.Thread] = None
_cpu_sample_lock = threading.Lock()
_cpu_sample_state: Dict[str, float] = {}
_stale_frontend_guard_lock = threading.Lock()
_stale_frontend_guard_state: Dict[str, float] = {}
_bootstrap_status: Dict[str, Any] = {
    "scheduled_at": None,
    "started_at": None,
    "completed_at": None,
    "duration_ms": None,
}
_BOOTSTRAP_STAGE_DEPENDENCIES: Dict[str, List[str]] = {
    "startup": [],
    "post_startup_bootstrap": ["startup"],
    "capability_warmup": ["post_startup_bootstrap"],
    "runtime_recovery": ["post_startup_bootstrap"],
}
_bootstrap_stage_registry: Dict[str, Dict[str, Any]] = {
    stage_id: {
        "stage_id": stage_id,
        "scheduled_at": None,
        "started_at": None,
        "completed_at": None,
        "duration_ms": None,
        "blocking_dependencies": list(dependencies),
        "state": "pending",
        "notes": [],
    }
    for stage_id, dependencies in _BOOTSTRAP_STAGE_DEPENDENCIES.items()
}


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _build_dependency_install_hint() -> str:
    requirements_path = _project_root() / "requirements.txt"
    return f'"{sys.executable}" -m pip install -r "{requirements_path}"'


def _build_container_run_hint() -> str:
    return ".\\run_profiler_backend_container.ps1 -ContainerName <container_name>"


def _ensure_required_module(module_name: str, *, package_name: Optional[str] = None) -> None:
    if importlib.util.find_spec(module_name) is not None:
        return

    project_root = _project_root()
    package_label = package_name or module_name
    environment_label = "가상환경" if os.environ.get("VIRTUAL_ENV") else "현재 인터프리터"
    raise RuntimeError(
        f"필수 Python 패키지 '{package_label}'를 {environment_label}에서 찾지 못했습니다. "
        f"현재 인터프리터: {sys.executable}. "
        f"프로젝트 루트 '{project_root}'에서 의존성 설치가 누락된 상태입니다. "
        f"다음 명령으로 의존성을 설치한 뒤 같은 인터프리터로 다시 실행하세요: {_build_dependency_install_hint()}"
    )


def _ensure_supported_python_runtime() -> None:
    supported_min = (3, 12)
    supported_max_exclusive = (3, 14)
    current = sys.version_info
    if (current.major, current.minor) < supported_min:
        raise RuntimeError(
            "Python 3.12 이상이 필요합니다. "
            f"현재 실행 버전: {current.major}.{current.minor}.{current.micro}. "
            f"컨테이너 Python 3.13을 사용 중이라면 호스트 python 대신 {_build_container_run_hint()} 로 실행하세요."
        )
    if (current.major, current.minor) >= supported_max_exclusive:
        raise RuntimeError(
            "현재 백엔드는 Python 3.14 이상에서 안정 지원되지 않습니다. "
            "Python 3.13 인터프리터와 새 가상환경으로 실행해 주세요. "
            f"현재 실행 버전: {current.major}.{current.minor}.{current.micro}"
        )


_ensure_supported_python_runtime()
_ensure_required_module("fastapi")
_ensure_required_module("annotated_doc", package_name="annotated-doc")
_ensure_required_module("uvicorn")

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from backend.database import check_database_availability, ensure_traceability_schema, ensure_user_role_columns, SessionLocal
from backend.models import User
from backend.auth import get_password_hash, is_weak_secret_key, verify_password
from backend.llm.model_config import (
    SAFE_COMPUTE_USAGE_LIMIT_PERCENT,
    SAFE_MEMORY_OCCUPANCY_LIMIT_PERCENT,
    get_gpu_runtime_info,
)


def _enable_qdrant_rest_only_mode() -> None:
    os.environ.setdefault("QDRANT_REST_ONLY", "true")
    os.environ.setdefault("QDRANT_PREFER_GRPC", "false")
    os.environ.setdefault("QDRANT_GRPC_DISABLED", "true")
    if os.getenv("QDRANT_REST_ONLY", "true").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    if "grpc" in sys.modules:
        return

    class _GrpcStatusCode:
        OK = 0
        CANCELLED = 1
        UNKNOWN = 2
        INVALID_ARGUMENT = 3
        DEADLINE_EXCEEDED = 4
        NOT_FOUND = 5
        ALREADY_EXISTS = 6
        PERMISSION_DENIED = 7
        RESOURCE_EXHAUSTED = 8
        FAILED_PRECONDITION = 9
        ABORTED = 10
        OUT_OF_RANGE = 11
        UNIMPLEMENTED = 12
        INTERNAL = 13
        UNAVAILABLE = 14
        DATA_LOSS = 15
        UNAUTHENTICATED = 16

    class _GrpcCompression:
        NoCompression = 0
        Deflate = 1
        Gzip = 2

    grpc_stub = types.ModuleType("grpc")
    grpc_stub.__dict__["__doc__"] = "REST-only grpc stub for local Qdrant runtime"
    grpc_stub.__dict__["aio"] = types.ModuleType("grpc.aio")
    grpc_stub.__dict__["StatusCode"] = _GrpcStatusCode
    grpc_stub.__dict__["RpcError"] = RuntimeError
    grpc_stub.__dict__["Compression"] = _GrpcCompression

    def _grpc_disabled(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("gRPC is disabled in REST-only Qdrant mode.")

    def _grpc_getattr(name: str) -> Any:
        if name == "Compression":
            return grpc_stub.__dict__["Compression"]
        if name == "StatusCode":
            return grpc_stub.__dict__["StatusCode"]
        if name.endswith("Error"):
            return RuntimeError
        if name and name[0].isupper():
            return type(name, (), {})
        return _grpc_disabled

    grpc_stub.__dict__["insecure_channel"] = _grpc_disabled
    grpc_stub.__dict__["secure_channel"] = _grpc_disabled
    grpc_stub.__dict__["ssl_channel_credentials"] = _grpc_disabled
    grpc_stub.__dict__["metadata_call_credentials"] = _grpc_disabled
    grpc_stub.__dict__["composite_channel_credentials"] = _grpc_disabled
    grpc_stub.__dict__["access_token_call_credentials"] = _grpc_disabled
    grpc_stub.__dict__["__getattr__"] = _grpc_getattr
    grpc_stub.aio.__dict__["__getattr__"] = lambda name: _grpc_getattr(name)
    sys.modules["grpc"] = grpc_stub
    sys.modules["grpc.aio"] = grpc_stub.aio


_enable_qdrant_rest_only_mode()


def _append_bootstrap_stage_note(stage_id: str, note: str) -> None:
    if not note:
        return
    stage_state = _bootstrap_stage_registry.setdefault(stage_id, {"notes": []})
    notes = stage_state.setdefault("notes", [])
    if note not in notes:
        notes.append(note)


def _schedule_bootstrap_stage(stage_id: str, note: str = "") -> None:
    stage_state = _bootstrap_stage_registry.setdefault(stage_id, {})
    if not stage_state.get("scheduled_at"):
        stage_state["scheduled_at"] = datetime.now(timezone.utc).isoformat()
    stage_state["state"] = "scheduled"
    if "blocking_dependencies" not in stage_state:
        stage_state["blocking_dependencies"] = list(_BOOTSTRAP_STAGE_DEPENDENCIES.get(stage_id, []))
    _append_bootstrap_stage_note(stage_id, note)


def _mark_bootstrap_stage_started(stage_id: str, note: str = "") -> float:
    stage_state = _bootstrap_stage_registry.setdefault(stage_id, {})
    if not stage_state.get("scheduled_at"):
        stage_state["scheduled_at"] = datetime.now(timezone.utc).isoformat()
    stage_state["started_at"] = datetime.now(timezone.utc).isoformat()
    stage_state["state"] = "running"
    if "blocking_dependencies" not in stage_state:
        stage_state["blocking_dependencies"] = list(_BOOTSTRAP_STAGE_DEPENDENCIES.get(stage_id, []))
    _append_bootstrap_stage_note(stage_id, note)
    return time.perf_counter()


def _mark_bootstrap_stage_completed(stage_id: str, started_at_perf: float, note: str = "") -> None:
    stage_state = _bootstrap_stage_registry.setdefault(stage_id, {})
    stage_state["completed_at"] = datetime.now(timezone.utc).isoformat()
    stage_state["duration_ms"] = round((time.perf_counter() - started_at_perf) * 1000, 1)
    stage_state["state"] = "completed"
    _append_bootstrap_stage_note(stage_id, note)


def _mark_bootstrap_stage_failed(stage_id: str, started_at_perf: float, note: str) -> None:
    stage_state = _bootstrap_stage_registry.setdefault(stage_id, {})
    stage_state["completed_at"] = datetime.now(timezone.utc).isoformat()
    stage_state["duration_ms"] = round((time.perf_counter() - started_at_perf) * 1000, 1)
    stage_state["state"] = "failed"
    _append_bootstrap_stage_note(stage_id, note)


def _bootstrap_status_payload() -> Dict[str, Any]:
    return {
        **_bootstrap_status,
        "stages": {stage_id: dict(stage_state) for stage_id, stage_state in _bootstrap_stage_registry.items()},
    }


def _start_ad_order_worker_thread() -> None:
    global _ad_worker_thread

    enable_worker = os.getenv(
        "ENABLE_AD_ORDER_WORKER_BOOTSTRAP",
        "true",
    ).strip().lower() in {"1", "true", "yes", "on"}
    if not enable_worker:
        logger.info("[INFO] ad order worker bootstrap disabled")
        return

    if _ad_worker_thread and _ad_worker_thread.is_alive():
        return

    try:
        from backend.marketplace.router import run_ad_order_worker
    except Exception as exc:
        logger.warning(f"[WARN] ad order worker import failed: {exc}")
        return

    worker_thread = threading.Thread(
        target=run_ad_order_worker,
        name="ad-render-worker-001",
        daemon=True,
    )
    worker_thread.start()
    _ad_worker_thread = worker_thread
    logger.info("[OK] ad order worker bootstrap started")


def _start_self_run_video_worker_thread() -> None:
    global _self_run_video_worker_thread

    enable_worker = os.getenv(
        "ENABLE_SELF_RUN_VIDEO_WORKER_BOOTSTRAP",
        "true",
    ).strip().lower() in {"1", "true", "yes", "on"}
    if not enable_worker:
        logger.info("[INFO] self-run video worker bootstrap disabled")
        return

    if _self_run_video_worker_thread and _self_run_video_worker_thread.is_alive():
        return

    try:
        from backend.marketplace.self_run_video_worker import run_self_run_video_worker
    except Exception as exc:
        logger.warning(f"[WARN] self-run video worker import failed: {exc}")
        return

    worker_thread = threading.Thread(
        target=run_self_run_video_worker,
        name="self-run-video-worker-001",
        daemon=True,
    )
    worker_thread.start()
    _self_run_video_worker_thread = worker_thread
    logger.info("[OK] self-run video worker bootstrap started")


def _start_ad_order_runtime_recovery_thread(db_available: bool) -> None:
    _schedule_bootstrap_stage(
        "runtime_recovery",
        "marketplace ad queue recovery and worker bootstrap scheduled after post-startup bootstrap",
    )
    enable_runtime_recovery = os.getenv(
        "ENABLE_AD_ORDER_RUNTIME_RECOVERY_BOOTSTRAP",
        "true",
    ).strip().lower() in {"1", "true", "yes", "on"}
    if not enable_runtime_recovery:
        logger.info("[INFO] ad order runtime recovery bootstrap disabled")
        _append_bootstrap_stage_note("runtime_recovery", "runtime recovery disabled by environment")
        return
    if not db_available:
        logger.warning("[WARN] ad video queue bootstrap skipped: database unavailable")
        _append_bootstrap_stage_note("runtime_recovery", "database unavailable -> recovery skipped")
        return

    def _recover_runtime() -> None:
        stage_started_at = _mark_bootstrap_stage_started(
            "runtime_recovery",
            "runtime recovery started; heavy runtime import allowed only in runtime_recovery stage",
        )
        try:
            from backend.marketplace.router import ensure_ad_order_runtime_ready

            recovered = ensure_ad_order_runtime_ready()
            if recovered:
                logger.info(
                    "[OK] ad video queue recovered "
                    f"{recovered} interrupted order(s)"
                )
            _mark_bootstrap_stage_completed(
                "runtime_recovery",
                stage_started_at,
                f"runtime recovery completed; recovered={recovered}",
            )
        except Exception as exc:
            logger.warning(f"[WARN] ad video queue bootstrap failed: {exc}")
            _mark_bootstrap_stage_failed(
                "runtime_recovery",
                stage_started_at,
                f"runtime recovery failed: {exc}",
            )

    threading.Thread(
        target=_recover_runtime,
        name="ad-order-runtime-recovery",
        daemon=True,
    ).start()


def _start_admin_capability_warmup_thread() -> None:
    _schedule_bootstrap_stage(
        "capability_warmup",
        "admin capability cache warmup scheduled after post-startup bootstrap",
    )
    def _warmup() -> None:
        stage_started_at = _mark_bootstrap_stage_started(
            "capability_warmup",
            "capability warmup started; cache fill only, no runtime recovery work allowed",
        )
        try:
            from backend.llm.admin_capabilities import _build_capability_map

            _build_capability_map()
            logger.info("[OK] admin capability cache warmup completed via capability map prebuild")
            _mark_bootstrap_stage_completed(
                "capability_warmup",
                stage_started_at,
                "admin capability cache warmup completed via capability map prebuild",
            )
        except Exception as exc:
            logger.warning(f"[WARN] admin capability cache warmup failed: {exc}")
            _mark_bootstrap_stage_failed(
                "capability_warmup",
                stage_started_at,
                f"admin capability cache warmup failed: {exc}",
            )

    threading.Thread(
        target=_warmup,
        name="admin-capability-cache-warmup",
        daemon=True,
    ).start()


def _run_post_startup_bootstrap() -> None:
    started_at = _mark_bootstrap_stage_started(
        "post_startup_bootstrap",
        "database schema checks and fixed admin bootstrap only",
    )
    _bootstrap_status["started_at"] = datetime.now(timezone.utc).isoformat()
    db_available, db_reason = check_database_availability()
    if not db_available:
        logger.warning(f"[WARN] database unavailable at startup: {db_reason}")
        _append_bootstrap_stage_note("post_startup_bootstrap", f"database unavailable: {db_reason}")

    def _run_database_schema_bootstrap() -> None:
        _append_bootstrap_stage_note(
            "post_startup_bootstrap",
            "heavy DB schema correction stays in post_startup_bootstrap stage",
        )
        try:
            if db_available:
                ensure_user_role_columns()
                logger.info("[OK] user role columns verified")
            else:
                logger.warning("[WARN] user role columns check skipped: database unavailable")
        except Exception as e:
            logger.warning(f"[WARN] user role columns check failed: {e}")

        try:
            if db_available:
                ensure_traceability_schema()
                logger.info("[OK] traceability schema verified")
            else:
                logger.warning("[WARN] traceability schema check skipped: database unavailable")
        except Exception as e:
            logger.warning(f"[WARN] traceability schema check failed: {e}")

        try:
            if db_available:
                from backend.marketplace.router import ensure_marketplace_runtime_schema

                ensure_marketplace_runtime_schema()
                logger.info("[OK] marketplace runtime schema verified")
            else:
                logger.warning("[WARN] marketplace runtime schema check skipped: database unavailable")
        except Exception as e:
            logger.warning(f"[WARN] marketplace runtime schema check failed: {e}")

    def _run_fixed_admin_bootstrap() -> None:
        _append_bootstrap_stage_note(
            "post_startup_bootstrap",
            "fixed admin account bootstrap runs only after schema bootstrap",
        )
        app_env = os.getenv("APP_ENV", "dev").strip().lower()
        enable_fixed_admin = os.getenv(
            "ENABLE_FIXED_ADMIN_BOOTSTRAP", "true"
        ).lower() in {"1", "true", "yes", "on"}
        if not enable_fixed_admin:
            return

        fixed_admin_email = os.getenv(
            "FIXED_ADMIN_EMAIL", "119cash@naver.com"
        ).strip()
        fixed_admin_password = str(os.getenv("FIXED_ADMIN_PASSWORD") or "space0215@").strip()
        if not fixed_admin_email or not fixed_admin_password:
            if app_env in {"prod", "production", "stage", "staging"}:
                logger.warning("[WARN] fixed admin bootstrap skipped: missing email/password in protected env")
            else:
                logger.warning("[WARN] fixed admin bootstrap skipped: missing email/password")
            return
        if not db_available:
            logger.warning("[WARN] fixed admin bootstrap skipped: database unavailable")
            return

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == fixed_admin_email).first()
            should_commit = False
            if user is None:
                user = User(
                    email=fixed_admin_email,
                    username=fixed_admin_email,
                    hashed_password=get_password_hash(fixed_admin_password),
                    is_active=True,
                    is_admin=True,
                    is_superuser=True,
                )
                db.add(user)
                action = "created"
                should_commit = True
            else:
                password_matches = bool(user.hashed_password) and verify_password(
                    fixed_admin_password,
                    user.hashed_password,
                )
                needs_update = any(
                    [
                        getattr(user, "username", None) != fixed_admin_email,
                        not password_matches,
                        not getattr(user, "is_active", False),
                        not getattr(user, "is_admin", False),
                        not getattr(user, "is_superuser", False),
                    ]
                )
                if needs_update:
                    setattr(user, "username", fixed_admin_email)
                    if not password_matches:
                        setattr(user, "hashed_password", get_password_hash(fixed_admin_password))
                    setattr(user, "is_active", True)
                    setattr(user, "is_admin", True)
                    setattr(user, "is_superuser", True)
                    action = "updated"
                    should_commit = True
                else:
                    action = "verified"

            if should_commit:
                db.commit()
            logger.info(f"[OK] fixed admin account {action}: {fixed_admin_email}")
        except Exception as e:
            db.rollback()
            logger.warning(f"[WARN] fixed admin bootstrap failed: {e}")
        finally:
            db.close()

    try:
        _run_database_schema_bootstrap()
        _run_fixed_admin_bootstrap()
    except Exception as exc:
        _mark_bootstrap_stage_failed(
            "post_startup_bootstrap",
            started_at,
            f"post-startup bootstrap failed: {exc}",
        )
        raise

    _start_ad_order_runtime_recovery_thread(db_available)
    _start_ad_order_worker_thread()
    _start_self_run_video_worker_thread()
    _start_admin_capability_warmup_thread()
    _bootstrap_status["completed_at"] = datetime.now(timezone.utc).isoformat()
    _bootstrap_status["duration_ms"] = round((time.perf_counter() - started_at) * 1000, 1)
    _mark_bootstrap_stage_completed(
        "post_startup_bootstrap",
        started_at,
        "post-startup bootstrap completed; runtime_recovery and capability_warmup dispatched independently",
    )
    logger.info(
        "[OK] post-startup bootstrap completed in %.1fms",
        _bootstrap_status["duration_ms"],
    )


def _start_post_startup_bootstrap_thread() -> None:
    _bootstrap_status["scheduled_at"] = datetime.now(timezone.utc).isoformat()
    _schedule_bootstrap_stage(
        "post_startup_bootstrap",
        "baseline readiness complete -> post_startup_bootstrap scheduled",
    )
    threading.Thread(
        target=_run_post_startup_bootstrap,
        name="post-startup-bootstrap",
        daemon=True,
    ).start()
    logger.info("[OK] post-startup bootstrap scheduled")

app = FastAPI(
    title="DevAnalysis114 API",
    version="2.2.0",
    openapi_version="3.1.0",
)


def _stale_frontend_guard_key(request: Request) -> str:
    forwarded_for = str(request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    client_host = forwarded_for or (request.client.host if request.client else "unknown")
    user_agent = str(request.headers.get("user-agent") or "")
    return f"{client_host}:{user_agent[:120]}"


def _mark_and_check_stale_frontend_burst(request: Request, *, window_sec: float) -> bool:
    now_ts = time.time()
    key = _stale_frontend_guard_key(request)
    with _stale_frontend_guard_lock:
        previous = float(_stale_frontend_guard_state.get(key) or 0.0)
        _stale_frontend_guard_state[key] = now_ts
        stale_keys = [
            state_key
            for state_key, seen_at in _stale_frontend_guard_state.items()
            if (now_ts - float(seen_at)) > (window_sec * 20)
        ]
        for stale_key in stale_keys:
            _stale_frontend_guard_state.pop(stale_key, None)
    return (now_ts - previous) < window_sec


def _legacy_admin_projects_payload() -> bytes:
    return json.dumps(
        {
            "items": [],
            "projects": [],
            "total": 0,
            "skip": 0,
            "limit": 500,
            "requested_limit": 5000,
            "applied_limit": 500,
            "degraded": True,
        },
        ensure_ascii=False,
    ).encode("utf-8")


def _stale_frontend_guard_response(*, body: bytes, content_type: str, headers: Dict[str, str]) -> Response:
    response = Response(content=body, media_type=content_type, status_code=200)
    for key, value in headers.items():
        response.headers[key] = value
    response.headers["Connection"] = "close"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@app.middleware("http")
async def stale_frontend_burst_guard(request: Request, call_next):
    path = request.url.path
    if request.method == "GET" and path == "/api/admin/projects":
        limit_value = str(request.query_params.get("limit") or "").strip()
        if limit_value.isdigit() and int(limit_value) >= 5000:
            return _stale_frontend_guard_response(
                body=_legacy_admin_projects_payload(),
                content_type="application/json",
                headers={
                    "x-stale-client-mitigation": "global-admin-projects-legacy-cutoff",
                    "x-admin-projects-degraded": "1",
                    "x-admin-projects-applied-limit": "500",
                },
            )

    if request.method == "GET" and path == "/api/marketplace/categories":
        if _mark_and_check_stale_frontend_burst(request, window_sec=0.8):
            return _stale_frontend_guard_response(
                body=b"[]",
                content_type="application/json",
                headers={
                    "x-stale-client-mitigation": "global-marketplace-categories-burst-cutoff",
                    "x-marketplace-categories-degraded": "1",
                },
            )

    return await call_next(request)


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _build_cors_origin_regex() -> str:
    patterns = [
        r"https?://localhost(:\d+)?$",
        r"https?://127\.0\.0\.1(:\d+)?$",
        r"https?://\[::1\](:\d+)?$",
        r"https?://host\.docker\.internal(:\d+)?$",
        r"https?://10\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?$",
        r"https?://172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}(:\d+)?$",
        r"https?://192\.168\.\d{1,3}\.\d{1,3}(:\d+)?$",
        r"https?://[a-z0-9-]+\.local(:\d+)?$",
    ]
    domains = [
        os.getenv("DOMAIN_NAME", "xn--114-2p7l635dz3bh5j.com").strip(),
        os.getenv("ADMIN_DOMAIN", "metanova1004.com").strip(),
        os.getenv(
            "MARKETPLACE_API_DOMAIN",
            "api.xn--114-2p7l635dz3bh5j.com",
        ).strip(),
        os.getenv("COMPUTERNAME", "").strip(),
        os.getenv("HOSTNAME", "").strip(),
    ]
    for domain_name in domains:
        if domain_name:
            escaped_domain = re.escape(domain_name)
            patterns.append(
                rf"https?://([a-z0-9-]+\.)?{escaped_domain}(:\d+)?$"
            )
    return "|".join(patterns)


def _build_cors_origins() -> List[str]:
    configured_origins = (
        os.getenv("CORS_ORIGINS")
        or os.getenv("ALLOWED_ORIGINS")
        or ""
    )
    origins = [o.strip() for o in configured_origins.split(",") if o.strip()]
    default_origins = [
        "http://localhost:3000",
        "http://localhost:3005",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3005",
        "https://localhost",
        "https://localhost:443",
        "https://localhost:8443",
        "https://127.0.0.1",
        "https://127.0.0.1:443",
        "https://127.0.0.1:8443",
        "http://host.docker.internal:3000",
        "http://host.docker.internal:3005",
    ]
    for host in filter(None, [
        os.getenv("DOMAIN_NAME", "").strip(),
        os.getenv("ADMIN_DOMAIN", "").strip(),
        os.getenv("MARKETPLACE_API_DOMAIN", "").strip(),
        os.getenv("COMPUTERNAME", "").strip(),
        os.getenv("HOSTNAME", "").strip(),
    ]):
        default_origins.extend([
            f"http://{host}",
            f"https://{host}",
            f"http://{host}:3000",
            f"http://{host}:3005",
            f"https://{host}:443",
            f"https://{host}:8443",
        ])

    deduped: List[str] = []
    for origin in [*origins, *default_origins]:
        if origin and origin not in deduped:
            deduped.append(origin)
    return deduped


def _relative_percent(numerator: float, denominator: float) -> Optional[float]:
    if denominator <= 0:
        return None
    return round((numerator / denominator) * 100, 1)


def _linux_memory_snapshot() -> Optional[Dict[str, Any]]:
    meminfo_path = "/proc/meminfo"
    if not os.path.exists(meminfo_path):
        return None

    values: Dict[str, int] = {}
    try:
        with open(meminfo_path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                if ":" not in raw_line:
                    continue
                key, raw_value = raw_line.split(":", 1)
                number = raw_value.strip().split()[0]
                if number.isdigit():
                    values[key.strip()] = int(number)
    except Exception as exc:
        return {"error": str(exc)}

    total_kb = values.get("MemTotal", 0)
    available_kb = values.get("MemAvailable", values.get("MemFree", 0))
    used_kb = max(total_kb - available_kb, 0)
    usage_percent = _relative_percent(used_kb, total_kb)
    return {
        "total_mb": round(total_kb / 1024, 1),
        "used_mb": round(used_kb / 1024, 1),
        "available_mb": round(available_kb / 1024, 1),
        "usage_percent": usage_percent,
    }


def _windows_memory_snapshot() -> Optional[Dict[str, Any]]:
    try:
        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        kernel32 = ctypes.windll.kernel32
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
    except Exception as exc:
        return {"error": str(exc)}

    total_bytes = int(status.ullTotalPhys)
    available_bytes = int(status.ullAvailPhys)
    used_bytes = max(total_bytes - available_bytes, 0)
    return {
        "total_mb": round(total_bytes / (1024 * 1024), 1),
        "used_mb": round(used_bytes / (1024 * 1024), 1),
        "available_mb": round(available_bytes / (1024 * 1024), 1),
        "usage_percent": round(float(status.dwMemoryLoad), 1),
    }


def _memory_snapshot() -> Dict[str, Any]:
    snapshot = _linux_memory_snapshot()
    if snapshot is None and os.name == "nt":
        snapshot = _windows_memory_snapshot()
    if not snapshot:
        return {
            "available": False,
            "state": "warning",
            "note": "메모리 사용량을 수집하지 못했습니다.",
        }

    usage_percent = snapshot.get("usage_percent")
    critical_percent = max(
        SAFE_COMPUTE_USAGE_LIMIT_PERCENT,
        int(os.getenv("RUNTIME_MEMORY_CRITICAL_PERCENT", str(SAFE_COMPUTE_USAGE_LIMIT_PERCENT)) or SAFE_COMPUTE_USAGE_LIMIT_PERCENT),
    )
    warning_percent = min(
        critical_percent,
        int(os.getenv("RUNTIME_MEMORY_WARNING_PERCENT", str(SAFE_MEMORY_OCCUPANCY_LIMIT_PERCENT)) or SAFE_MEMORY_OCCUPANCY_LIMIT_PERCENT),
    )
    if isinstance(usage_percent, (int, float)) and usage_percent >= critical_percent:
        state = "critical"
        note = "메모리 사용량이 위험 수위입니다. 불필요한 프로세스를 줄이고 컨테이너 메모리 한도를 점검하세요."
    elif isinstance(usage_percent, (int, float)) and usage_percent >= warning_percent:
        state = "warning"
        note = "메모리 사용량이 높습니다. 캐시, 워커 수, 동시 실행 작업을 점검하세요."
    else:
        state = "ok"
        note = "메모리 사용량이 정상 범위입니다."

    return {
        "available": True,
        "state": state,
        "note": note,
        **snapshot,
    }


def _read_linux_cpu_totals() -> Optional[Dict[str, float]]:
    stat_path = "/proc/stat"
    if not os.path.exists(stat_path):
        return None
    try:
        with open(stat_path, "r", encoding="utf-8") as handle:
            first_line = handle.readline().strip()
    except Exception:
        return None
    parts = first_line.split()
    if len(parts) < 5 or parts[0] != "cpu":
        return None
    try:
        values = [float(item) for item in parts[1:8]]
    except ValueError:
        return None
    total = float(sum(values))
    idle = float(values[3] + values[4])
    return {
        "total": total,
        "idle": idle,
    }


def _linux_cpu_usage_percent() -> Optional[float]:
    with _cpu_sample_lock:
        current = _read_linux_cpu_totals()
        if not current:
            return None

        previous_total = _cpu_sample_state.get("total")
        previous_idle = _cpu_sample_state.get("idle")
        if previous_total is None or previous_idle is None:
            time.sleep(0.15)
            current = _read_linux_cpu_totals()
            if not current:
                return None
            previous_total = _cpu_sample_state.get("total", current["total"])
            previous_idle = _cpu_sample_state.get("idle", current["idle"])

        _cpu_sample_state.update(current)
        total_delta = current["total"] - previous_total
        idle_delta = current["idle"] - previous_idle
        if total_delta <= 0:
            return None
        busy_delta = max(total_delta - idle_delta, 0.0)
        return round((busy_delta / total_delta) * 100, 1)


def _cpu_snapshot() -> Dict[str, Any]:
    cpu_count = os.cpu_count() or 0
    load_1m: Optional[float] = None
    load_ratio_percent: Optional[float] = None
    usage_percent: Optional[float] = None
    note = "CPU 부하가 정상 범위입니다."
    state = "ok"
    error_message = ""
    warning_percent = min(
        SAFE_COMPUTE_USAGE_LIMIT_PERCENT,
        int(os.getenv("RUNTIME_CPU_WARNING_PERCENT", str(SAFE_MEMORY_OCCUPANCY_LIMIT_PERCENT)) or SAFE_MEMORY_OCCUPANCY_LIMIT_PERCENT),
    )
    critical_percent = max(
        warning_percent,
        int(os.getenv("RUNTIME_CPU_CRITICAL_PERCENT", str(SAFE_COMPUTE_USAGE_LIMIT_PERCENT)) or SAFE_COMPUTE_USAGE_LIMIT_PERCENT),
    )

    if hasattr(os, "getloadavg"):
        try:
            getloadavg = cast(Any, getattr(os, "getloadavg"))
            load_1m = round(float(getloadavg()[0]), 2)
        except Exception as exc:
            error_message = str(exc)

    if load_1m is not None and cpu_count > 0:
        load_ratio_percent = _relative_percent(load_1m, cpu_count)

    usage_percent = _linux_cpu_usage_percent() or load_ratio_percent

    if usage_percent is None:
        state = "warning"
        note = "CPU 부하를 정밀 수집하지 못했습니다. 컨테이너/호스트 런타임 정보를 확인하세요."
    elif usage_percent >= critical_percent:
        state = "critical"
        note = "CPU 부하가 과도합니다. 동시 작업 수와 백그라운드 연산을 줄이세요."
    elif usage_percent >= warning_percent:
        state = "warning"
        note = "CPU 부하가 높습니다. 워커 수, 큐 적체, CPU fallback 실행 여부를 점검하세요."
    elif isinstance(load_ratio_percent, (int, float)) and load_ratio_percent >= critical_percent:
        note = "load average 는 높지만 실제 CPU 사용률은 기준 미만입니다. 컨테이너 외부 부하 또는 대기 작업까지 포함된 상태로 해석합니다."

    payload: Dict[str, Any] = {
        "available": usage_percent is not None,
        "state": state,
        "note": note,
        "cpu_count": cpu_count,
        "load_1m": load_1m,
        "load_ratio_percent": load_ratio_percent,
        "usage_percent": usage_percent,
    }
    if error_message:
        payload["error"] = error_message
    return payload


def _gpu_snapshot() -> Dict[str, Any]:
    gpu_runtime = get_gpu_runtime_info()
    devices = (
        gpu_runtime.get("devices", [])
        if isinstance(gpu_runtime, dict)
        else []
    )
    if not gpu_runtime.get("available"):
        return {
            "available": False,
            "state": "warning",
            "note": "GPU 런타임이 감지되지 않았습니다. CPU fallback 또는 드라이버 상태를 확인하세요.",
            "devices": [],
            "error": (
                gpu_runtime.get("error")
                if isinstance(gpu_runtime, dict)
                else None
            ),
        }

    peak_usage = 0.0
    peak_util = 0.0
    normalized_devices: List[Dict[str, Any]] = []
    for device in devices:
        memory_used = float(device.get("memory_used_mb", 0) or 0)
        memory_total = float(device.get("memory_total_mb", 0) or 0)
        memory_percent = _relative_percent(memory_used, memory_total) or 0.0
        util = float(device.get("utilization_gpu", 0) or 0)
        peak_usage = max(peak_usage, memory_percent)
        peak_util = max(peak_util, util)
        normalized_devices.append(
            {
                **device,
                "memory_usage_percent": round(memory_percent, 1),
            }
        )

    vram_warning_percent = min(
        SAFE_COMPUTE_USAGE_LIMIT_PERCENT,
        int(os.getenv("RUNTIME_VRAM_WARNING_PERCENT", str(SAFE_MEMORY_OCCUPANCY_LIMIT_PERCENT)) or SAFE_MEMORY_OCCUPANCY_LIMIT_PERCENT),
    )
    gpu_warning_percent = min(
        SAFE_COMPUTE_USAGE_LIMIT_PERCENT,
        int(os.getenv("RUNTIME_GPU_WARNING_PERCENT", str(SAFE_MEMORY_OCCUPANCY_LIMIT_PERCENT)) or SAFE_MEMORY_OCCUPANCY_LIMIT_PERCENT),
    )
    gpu_critical_percent = max(
        gpu_warning_percent,
        int(os.getenv("RUNTIME_GPU_CRITICAL_PERCENT", str(SAFE_COMPUTE_USAGE_LIMIT_PERCENT)) or SAFE_COMPUTE_USAGE_LIMIT_PERCENT),
    )

    if peak_usage >= vram_warning_percent and peak_util < 10:
        state = "ok"
        note = "VRAM 상주 비중은 높지만 실제 GPU 연산 부하는 매우 낮습니다. 로드된 모델 또는 캐시 상주 상태로 해석합니다."
    elif peak_util >= gpu_critical_percent or (peak_usage >= gpu_critical_percent and peak_util >= 35) or (peak_usage >= vram_warning_percent and peak_util >= gpu_critical_percent):
        state = "critical"
        note = "GPU 사용률 또는 VRAM 점유율이 위험 수위입니다. 모델 프로필과 동시 추론 수를 즉시 낮추세요."
    elif peak_util >= gpu_warning_percent or (peak_usage >= vram_warning_percent and peak_util >= 35):
        state = "warning"
        note = "GPU 사용량이 높습니다. 대형 모델 동시 실행과 레이어 오프로드 설정을 점검하세요."
    elif peak_usage >= vram_warning_percent and peak_util < 20:
        state = "ok"
        note = "VRAM 상주 비중은 높지만 실제 GPU 연산 부하는 낮습니다. 로드된 모델/캐시 상태로 해석합니다."
    else:
        state = "ok"
        note = "GPU 사용량이 정상 범위입니다."

    return {
        "available": True,
        "state": state,
        "note": note,
        "devices": normalized_devices,
        "device_count": len(normalized_devices),
        "peak_memory_usage_percent": round(peak_usage, 1),
        "peak_utilization_percent": round(peak_util, 1),
    }


def _append_alert(
    alerts: List[Dict[str, str]],
    modules: Dict[str, str],
    alert_id: str,
    severity: str,
    title: str,
    message: str,
    action: str,
    source_path: str,
    diagnostic_detail: str,
    root_cause: Optional[str] = None,
    metrics: Optional[Dict[str, Any]] = None,
) -> None:
    payload: Dict[str, Any] = {
        "id": alert_id,
        "severity": severity,
        "title": title,
        "message": message,
        "action": action,
        "source_path": source_path,
        "diagnostic_detail": diagnostic_detail,
    }
    if root_cause:
        payload["root_cause"] = root_cause
    if metrics:
        payload["metrics"] = metrics
    alerts.append(cast(Dict[str, str], payload))
    modules[alert_id] = severity


def _runtime_health_payload() -> Dict[str, Any]:
    memory = _memory_snapshot()
    cpu = _cpu_snapshot()
    gpu = _gpu_snapshot()
    try:
        from backend.marketplace.router import get_ad_queue_runtime_status
        queue_runtime = get_ad_queue_runtime_status()
    except Exception as exc:
        queue_runtime = {
            "redis_queue": {
                "available": False,
                "state": "warning",
                "note": "Redis queue 진단을 로드하지 못했습니다.",
                "connection_id": "redis:video_render_queue",
                "queue_name": "video_render_queue",
                "error": str(exc),
            },
            "ad_worker": {
                "available": False,
                "state": "warning",
                "note": "광고 주문 worker 진단을 로드하지 못했습니다.",
                "connection_id": "redis:video_render_queue",
                "queue_name": "video_render_queue",
                "worker_id": "ad-render-worker-001",
                "error": str(exc),
            },
        }
    redis_queue = queue_runtime.get("redis_queue", {})
    ad_worker = queue_runtime.get("ad_worker", {})

    alerts: List[Dict[str, str]] = []
    modules: Dict[str, str] = {
        "api": "ok",
        "memory": memory.get("state", "warning"),
        "cpu": cpu.get("state", "warning"),
        "gpu": gpu.get("state", "warning"),
        "redis_queue": redis_queue.get("state", "warning"),
        "ad_worker": ad_worker.get("state", "warning"),
    }

    if memory.get("state") in {"warning", "critical"}:
        memory_metrics = {
            "usage_percent": memory.get("usage_percent"),
            "available_mb": memory.get("available_mb"),
            "total_mb": memory.get("total_mb"),
        }
        _append_alert(
            alerts,
            modules,
            "memory",
            str(memory.get("state")),
            "메모리 경고",
            f"메모리 사용률 {memory.get('usage_percent', 'unknown')}% 상태입니다.",
            str(memory.get("note") or "메모리 캐시와 동시 작업 수를 점검하세요."),
            "backend/main.py::_runtime_health_payload",
            f"usage_percent={memory.get('usage_percent')} available_mb={memory.get('available_mb')} total_mb={memory.get('total_mb')}",
            (
                "가용 메모리가 빠르게 줄어드는 상태입니다. "
                "캐시, 동시 워커, 대용량 프로세스 사용량을 먼저 줄여야 합니다."
            ),
            memory_metrics,
        )
    if cpu.get("state") in {"warning", "critical"}:
        usage_label = cpu.get("usage_percent")
        message = (
            f"CPU 부하 추정치 {usage_label}% 상태입니다."
            if usage_label is not None
            else "CPU 부하를 정밀 수집하지 못했습니다."
        )
        cpu_metrics = {
            "usage_percent": cpu.get("usage_percent"),
            "load_1m": cpu.get("load_1m"),
            "cpu_count": cpu.get("cpu_count"),
        }
        cpu_root_cause = (
            "호스트 load average 또는 코어 수를 읽지 못해 CPU 상태를 신뢰성 있게 계산하지 못했습니다."
            if usage_label is None
            else "코어 수 대비 1분 평균 부하가 높아 동시 작업 또는 백그라운드 연산이 몰린 상태입니다."
        )
        _append_alert(
            alerts,
            modules,
            "cpu",
            str(cpu.get("state")),
            "CPU 경고",
            message,
            str(cpu.get("note") or "동시 워커 수와 백그라운드 연산을 점검하세요."),
            "backend/main.py::_runtime_health_payload",
            f"usage_percent={cpu.get('usage_percent')} load_1m={cpu.get('load_1m')} cpu_count={cpu.get('cpu_count')}",
            cpu_root_cause,
            cpu_metrics,
        )
    if gpu.get("state") in {"warning", "critical"}:
        gpu_metrics = {
            "available": gpu.get("available"),
            "peak_utilization_percent": gpu.get("peak_utilization_percent"),
            "peak_memory_usage_percent": gpu.get("peak_memory_usage_percent"),
            "device_count": gpu.get("device_count"),
        }
        if gpu.get("available"):
            peak_util = gpu.get("peak_utilization_percent", 0)
            peak_vram = gpu.get("peak_memory_usage_percent", 0)
            message = (
                f"GPU 최대 사용률 {peak_util}%, "
                f"VRAM 점유율 {peak_vram}% 상태입니다."
            )
            if float(peak_vram or 0) >= 90 and float(peak_util or 0) < 15:
                gpu_root_cause = (
                    "VRAM 점유는 높지만 실제 연산률이 낮습니다. "
                    "로드된 모델 또는 캐시가 메모리에 상주해 경고처럼 보일 가능성이 큽니다."
                )
            else:
                gpu_root_cause = (
                    "실제 GPU 연산 또는 VRAM 점유가 높아 대형 모델 동시 실행이나 "
                    "과도한 레이어 오프로딩이 발생한 상태로 해석됩니다."
                )
        else:
            message = "GPU 런타임이 감지되지 않았습니다."
            gpu_root_cause = "GPU 런타임, 드라이버 또는 컨테이너 장치 바인딩을 확인해야 합니다."
        _append_alert(
            alerts,
            modules,
            "gpu",
            str(gpu.get("state")),
            "GPU 경고",
            message,
            str(gpu.get("note") or "GPU 드라이버와 모델 프로필을 점검하세요."),
            "backend/main.py::_runtime_health_payload",
            f"available={gpu.get('available')} peak_utilization_percent={gpu.get('peak_utilization_percent')} peak_memory_usage_percent={gpu.get('peak_memory_usage_percent')}",
            gpu_root_cause,
            gpu_metrics,
        )
    if redis_queue.get("state") in {"warning", "critical"}:
        redis_metrics = {
            "connection_id": redis_queue.get("connection_id"),
            "queue_name": redis_queue.get("queue_name"),
            "queue_depth": redis_queue.get("queue_depth"),
            "error": redis_queue.get("error"),
        }
        _append_alert(
            alerts,
            modules,
            "redis_queue",
            str(redis_queue.get("state")),
            "Redis queue 경고",
            str(redis_queue.get("note") or "Redis queue 상태를 확인하세요."),
            "REDIS_URL과 queue 연결 상태를 점검하세요.",
            "backend.marketplace.router:get_ad_queue_runtime_status",
            f"connection_id={redis_queue.get('connection_id')} queue_name={redis_queue.get('queue_name')} queue_depth={redis_queue.get('queue_depth')} error={redis_queue.get('error')}",
            "큐 연결 상태 또는 적체량에 문제가 있어 작업 전달이 지연되고 있습니다.",
            redis_metrics,
        )
    if ad_worker.get("state") in {"warning", "critical"}:
        worker_metrics = {
            "worker_id": ad_worker.get("worker_id"),
            "connection_id": ad_worker.get("connection_id"),
            "heartbeat_age_sec": ad_worker.get("heartbeat_age_sec"),
            "last_order_id": ad_worker.get("last_order_id"),
        }
        _append_alert(
            alerts,
            modules,
            "ad_worker",
            str(ad_worker.get("state")),
            "광고 주문 worker 경고",
            str(ad_worker.get("note") or "광고 주문 worker 상태를 확인하세요."),
            "worker 실행 진입점과 heartbeat를 점검하세요.",
            "backend.marketplace.router:get_ad_queue_runtime_status",
            f"worker_id={ad_worker.get('worker_id')} connection_id={ad_worker.get('connection_id')} heartbeat_age_sec={ad_worker.get('heartbeat_age_sec')} last_order_id={ad_worker.get('last_order_id')}",
            "worker heartbeat가 멈췄거나 주문 소비가 지연돼 실행 파이프라인이 정상 순환하지 못하고 있습니다.",
            worker_metrics,
        )

    overall_status = "ok"
    if any(alert["severity"] == "critical" for alert in alerts):
        overall_status = "critical"
    elif alerts:
        overall_status = "warning"

    return {
        "status": overall_status,
        "version": app.version,
        "modules": modules,
        "runtime": {
            "bootstrap": _bootstrap_status_payload(),
        },
        "diagnostics": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "resources": {
                "memory": memory,
                "cpu": cpu,
                "gpu": gpu,
                "redis_queue": redis_queue,
                "ad_worker": ad_worker,
            },
            "alerts": alerts,
        },
    }


# 관리자/개발 프런트가 LAN IP 또는 로컬 도메인으로 열려도 동일 백엔드를 호출할 수 있게 허용 출처를 확장한다.
# 레거시 3001 포트는 운영 경로에서 제외했으므로 CORS 허용 목록에서도 제거한다.
cors_origins = _build_cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_origin_regex=_build_cors_origin_regex(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger.info("[OK] cors origins loaded: %s", cors_origins)


@app.on_event("startup")
def startup_schema_guard():
    startup_started_at = _mark_bootstrap_stage_started(
        "startup",
        "startup stage only performs baseline guards and schedules post_startup_bootstrap",
    )
    app_env = os.getenv("APP_ENV", "dev").strip().lower()
    if is_weak_secret_key():
        if app_env in {"prod", "production", "stage", "staging"}:
            raise RuntimeError(
                "SECRET_KEY must be overridden outside local development"
            )
        logger.warning("[WARN] weak default SECRET_KEY is in use")
    _start_post_startup_bootstrap_thread()
    _mark_bootstrap_stage_completed(
        "startup",
        startup_started_at,
        "startup guard completed and post_startup_bootstrap scheduled",
    )


@app.get("/health")
async def health():
    return _runtime_health_payload()


@app.head("/health")
async def health_head():
    return None


@app.get("/api/health")
async def api_health():
    return _runtime_health_payload()


@app.head("/api/health")
async def api_health_head():
    return None


# ── Auth ──
try:
    from backend.auth_router import router as auth_router
    app.include_router(auth_router, prefix="/api/auth")
    logger.info("[OK] auth router loaded")
except Exception as e:
    logger.warning(f"[WARN] auth router skipped: {e}")

try:
    from backend.auth_identity_router import router as auth_identity_router
    app.include_router(auth_identity_router, prefix="/api/auth/identity")
    logger.info("[OK] auth identity router loaded")
except Exception as e:
    logger.warning(f"[WARN] auth identity router skipped: {e}")

# ── Admin ──
try:
    from backend.admin_router import router as admin_router
    app.include_router(admin_router)
    logger.info("[OK] admin router loaded")
except Exception as e:
    logger.warning(f"[WARN] admin router skipped: {e}")

# ── LLM ──
try:
    from backend.llm.router import router as llm_router
    app.include_router(llm_router)
    logger.info("[OK] llm router loaded")
except Exception as e:
    logger.warning(f"[WARN] llm router skipped: {e}")

# ── LLM Smart Router ──
try:
    from backend.llm.smart_router import router as smart_router
    app.include_router(smart_router)
    logger.info("[OK] smart router loaded")
except Exception as e:
    logger.warning(f"[WARN] smart router skipped: {e}")

# ── LLM Orchestrator ──
try:
    from backend.llm.orchestrator import router as orchestrator_router
    app.include_router(orchestrator_router)
    logger.info("[OK] orchestrator router loaded")
except Exception as e:
    logger.warning(f"[WARN] orchestrator router skipped: {e}")

# ── Admin Orchestrator Capabilities ──
try:
    from backend.llm.admin_capabilities import (
        router as admin_orchestrator_capability_router,
    )
    app.include_router(admin_orchestrator_capability_router)
    logger.info("[OK] admin orchestrator capability router loaded")
except Exception as e:
    logger.warning(f"[WARN] admin orchestrator capability router skipped: {e}")

# ── Voice Gateway ──
try:
    from backend.llm.voice_gateway import router as voice_router
    app.include_router(voice_router)
    logger.info("[OK] voice router loaded")
except Exception as e:
    logger.warning(f"[WARN] voice router skipped: {e}")

# ── Marketplace ──
try:
    from backend.marketplace.router import router as marketplace_router
    app.include_router(marketplace_router, prefix="/api/marketplace")
    logger.info("[OK] marketplace router loaded")
except Exception as e:
    logger.warning(f"[WARN] marketplace router skipped: {e}")

# ── Video API Compatibility ──
try:
    from backend.video_api_router import router as video_api_router
    app.include_router(video_api_router)
    logger.info("[OK] video api compatibility router loaded")
except Exception as e:
    logger.warning(f"[WARN] video api compatibility router skipped: {e}")

# ── Movie Studio ──
try:
    from backend.movie_studio.api.router import router as movie_studio_router
    app.include_router(movie_studio_router)
    logger.info("[OK] movie studio router loaded")
except Exception as e:
    logger.warning(f"[WARN] movie studio router skipped: {e}")

# ── Marketplace Stats ──
try:
    from backend.marketplace.stats_router import router as stats_router
    app.include_router(stats_router, prefix="/api/marketplace")
    logger.info("[OK] stats router loaded")
except Exception as e:
    logger.warning(f"[WARN] stats router skipped: {e}")

# ── Image ──
try:
    from backend.image.router import router as image_router
    app.include_router(image_router)
    logger.info("[OK] image router loaded")
except Exception as e:
    logger.warning(f"[WARN] image router skipped: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
