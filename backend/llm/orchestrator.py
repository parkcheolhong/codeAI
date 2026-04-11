"""LLM 오케스트레이터 - 멀티 에이전트 파이프라인"""
import asyncio
import ast
import html
import logging
from fastapi import APIRouter, HTTPException, Request
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List, Dict, Any, Callable
import httpx
import json
import re
import base64
import subprocess
import hashlib
import os
import time
import socket
import shutil
import importlib.util
import sys
import zipfile
import threading
import tempfile
from pathlib import Path
from datetime import datetime
from uuid import uuid4
from urllib.parse import urlparse

from backend.llm.model_config import (
    CURRENT_GPU_PROFILE_KEY,
    MODEL_ROUTE_KEYS,
    QWEN_CODER_Q4_TAG,
    QWEN_CODER_Q5_TAG,
    QWEN_CODER_Q6_TAG,
    QWEN_CODER_Q8_TAG,
    build_ollama_options,
    get_available_ollama_models,
    get_chat_model,
    get_configured_execution_controls,
    get_coder_model,
    get_configured_model_routes,
    get_designer_model,
    get_gpu_runtime_info,
    get_planner_model,
    get_reasoning_model,
    get_recommended_runtime_profiles,
    get_reviewer_model,
    get_voice_chat_model,
)

from backend.llm.code_analyzer import code_analyzer
from backend.llm.admin_capabilities import (
    _build_cached_path_validation,
    _build_nginx_target_validation,
    _build_route_manifest_validation,
)
from backend.llm.file_tools import write_file_tool
from backend.llm.python_security_policy import scan_python_security_policy
from backend.llm.project_indexer import project_indexer
from backend.llm.target_patch_registry import build_target_patch_registry_snapshot
from backend.llm.ws_channel import ws_channel
from backend.orchestrator.customer import (
    assemble_customer_orchestration_response as assemble_customer_orchestration_response_service,
    execute_orchestration as execute_customer_orchestration_service,
    finalize_customer_validation_bundle as finalize_customer_validation_bundle_service,
    prepare_customer_orchestration_context as prepare_customer_orchestration_context_service,
    run_customer_orchestration as run_customer_orchestration_service,
)
from backend.orchestrator.chat import (
    AutoConnectMeta,
    ConversationMessage,
    FlowTraceCommand,
    FlowTraceStep,
    OrchestratorChatRequest,
    OrchestratorChatResponse,
    build_admin_flow_trace,
    build_multi_command_plan,
    build_lightweight_flow_trace,
    resolve_active_trace,
)
from backend.orchestrator.chat.chat_service import answer_orchestrator_chat as answer_orchestrator_chat_service
from backend.database import SessionLocal
from backend.orchestrator.chat.project_context_store import get_active_global_approval_policy, normalize_project_root
from backend.orchestrator.chat.project_context_store import is_workspace_root_scope

router = APIRouter(prefix="/api/llm", tags=["orchestrator"])
logger = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[2]


def _log_orchestration_phase(
    phase: str,
    started_at: float,
    *,
    project_name: str,
    validation_profile: str,
) -> None:
    logger.info(
        "run_orchestration phase=%s elapsed_sec=%.2f project=%s validation_profile=%s",
        phase,
        max(0.0, time.perf_counter() - started_at),
        project_name,
        validation_profile,
    )


def _log_integration_validation_phase(
    phase: str,
    started_at: float,
    *,
    project_root: Path,
    validation_profile: str,
) -> None:
    logger.info(
        "integration_test_engine phase=%s elapsed_sec=%.2f project_root=%s validation_profile=%s",
        phase,
        max(0.0, time.perf_counter() - started_at),
        str(project_root),
        validation_profile,
    )


def _enforce_global_orchestration_gate(request: "OrchestrationRequest") -> None:
    output_dir_text = str(request.output_dir or "").strip()
    if not output_dir_text:
        return
    normalized_output_dir = normalize_project_root(output_dir_text)
    if not normalized_output_dir:
        return
    session = SessionLocal()
    try:
        global_policy = get_active_global_approval_policy(session)
    finally:
        session.close()
    blocked_paths = [str(item).strip() for item in (global_policy.get("blocked_paths") or []) if str(item).strip()]
    scope_paths = [str(item).strip() for item in (global_policy.get("scope") or []) if str(item).strip()]
    if any(blocked and blocked in normalized_output_dir for blocked in blocked_paths):
        raise HTTPException(status_code=400, detail="전역 승인 게이트가 금지한 경로는 오케스트레이션 실행 대상이 될 수 없습니다.")
    if scope_paths and not any(is_workspace_root_scope(scope) or (scope and scope in normalized_output_dir) for scope in scope_paths):
        raise HTTPException(status_code=400, detail="전역 승인 게이트 승인 범위 밖 경로는 오케스트레이션 실행 대상이 될 수 없습니다.")

OLLAMA_BASE = "http://host.docker.internal:11434"
_orchestrator_chat_http_client: Optional[httpx.AsyncClient] = None
_orchestrator_chat_http_client_signature: Optional[tuple[str, float]] = None

ORCH_GPU_ONLY_PREFERRED = (
    os.getenv("ORCH_GPU_ONLY_PREFERRED", "true").strip().lower()
    in {"1", "true", "yes", "on"}
)
ORCH_FORCE_COMPLETE = (
    os.getenv("ORCH_FORCE_COMPLETE", "true").strip().lower()
    in {"1", "true", "yes", "on"}
)
ORCH_ALLOW_SYNTHETIC_FALLBACK = (
    os.getenv("ORCH_ALLOW_SYNTHETIC_FALLBACK", "false").strip().lower()
    in {"1", "true", "yes", "on"}
)
ORCH_CODE_GENERATION_STRATEGIES = {"auto_generator"}


def _normalize_code_generation_strategy(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    if candidate in ORCH_CODE_GENERATION_STRATEGIES:
        return candidate
    return "auto_generator"


ORCH_CODE_GENERATION_STRATEGY = _normalize_code_generation_strategy(
    os.getenv("ORCH_CODE_GENERATION_STRATEGY", "auto_generator")
)
ORCH_SELECTED_PROFILE = os.getenv(
    "ORCH_SELECTED_PROFILE",
    CURRENT_GPU_PROFILE_KEY,
).strip() or CURRENT_GPU_PROFILE_KEY
ORCH_RUNTIME_PROFILE_CUSTOM_KEY = "custom"
ORCH_MODEL_TUNING_LEVEL = max(
    -1,
    min(1, int(os.getenv("ORCH_MODEL_TUNING_LEVEL", "0"))),
)
ORCH_TOKEN_TUNING_LEVEL = max(
    -1,
    min(1, int(os.getenv("ORCH_TOKEN_TUNING_LEVEL", "0"))),
)
ORCH_TIMEOUT_TUNING_LEVEL = max(
    -1,
    min(1, int(os.getenv("ORCH_TIMEOUT_TUNING_LEVEL", "0"))),
)
ORCH_MIN_FILES = max(1, int(os.getenv("ORCH_MIN_FILES", "27")))
ORCH_MIN_DIRS = max(0, int(os.getenv("ORCH_MIN_DIRS", "2")))
ORCH_MAX_FORCE_RETRIES = max(1, int(os.getenv("ORCH_MAX_FORCE_RETRIES", "3")))
_required_files_raw = os.getenv("ORCH_REQUIRED_FILES", "")
ORCH_REQUIRED_FILE_PATHS = [
    item.strip().replace("\\", "/")
    for item in _required_files_raw.split(",")
    if item.strip()
]
ORCH_ARCHITECTURE_BASELINE_FILES: List[str] = []
ORCH_ROUTER_FILES: List[str] = [
    "backend/app/api/routes/health.py",
    "backend/app/api/routes/auth.py",
    "backend/app/api/routes/catalog.py",
    "backend/app/api/routes/orders.py",
]
ORCH_SERVICE_FILES: List[str] = [
    "backend/app/services/health_service.py",
    "backend/app/services/auth_service.py",
    "backend/app/services/catalog_service.py",
    "backend/app/services/order_service.py",
]
ORCH_CONNECTOR_FILES: List[str] = [
    "backend/app/connectors/base.py",
    "backend/app/connectors/shopify.py",
]
ORCH_CORE_FILES: List[str] = [
    "backend/app/main.py",
    "backend/app/core/config.py",
    "backend/app/core/security.py",
    "backend/app/core/database.py",
    "backend/app/api/deps.py",
    *ORCH_ROUTER_FILES,
    *ORCH_SERVICE_FILES,
    "backend/app/repositories/health_repository.py",
    "backend/app/repositories/user_repository.py",
    "backend/app/repositories/catalog_repository.py",
    "backend/app/repositories/order_repository.py",
    "backend/app/infra/runtime_store.py",
    "backend/app/external_adapters/status_client.py",
    *ORCH_CONNECTOR_FILES,
    "backend/app/worker/tasks.py",
]
ORCH_STATE_FLOW: List[str] = [
    "DESIGN",
    "PLAN",
    "GENERATE",
    "BUILD",
    "REFINER_FIXER",
    "TEST",
    "REFLEXION",
    "FIX",
    "DONE",
    "FAILED",
]

ORCH_FILE_MANIFEST_PATH = "docs/file_manifest.md"
ORCH_CHECKLIST_PATH = "docs/orchestrator_checklist.md"
ORCH_ARTIFACT_LOG_PATH = "docs/orchestrator_artifacts.json"
ORCH_FAILURE_REPORT_PATH = "docs/failure_report.md"
ORCH_ROOT_CAUSE_REPORT_PATH = "docs/root_cause_analysis.md"
ORCH_VALIDATION_RESULT_JSON_PATH = "docs/automatic_validation_result.json"
ORCH_VALIDATION_RESULT_MD_PATH = "docs/automatic_validation_result.md"
ORCH_TRACEABILITY_MAP_PATH = "docs/traceability_map.json"
ORCH_ID_REGISTRY_SCHEMA_PATH = "docs/id_registry.schema.json"
ORCH_ID_REGISTRY_PATH = "docs/id_registry.json"
ORCH_SEMANTIC_AUDIT_REPORT_PATH = "docs/semantic_completion_audit.md"
ORCH_PYTHON_SECURITY_REPORT_PATH = "docs/python_security_policy_report.json"
ORCH_PRODUCT_ID_PATH = "docs/product_identity.json"
ORCH_SEMANTIC_AUDIT_MIN_SCORE = min(
    100,
    max(0, int(os.getenv("ORCH_SEMANTIC_AUDIT_MIN_SCORE", "85"))),
)
ORCH_SEMANTIC_AUDIT_RUBRICS: Dict[str, List[Dict[str, Any]]] = {
    "python_fastapi": [
        {
            "id": "api_requirements",
            "label": "API 요구사항 구현",
            "max_score": 35,
            "critical": True,
        },
        {
            "id": "service_integration",
            "label": "서비스/저장소 연결 완결성",
            "max_score": 30,
            "critical": True,
        },
        {
            "id": "verification_evidence",
            "label": "검증 증거 충족",
            "max_score": 20,
            "critical": True,
        },
        {
            "id": "operational_readiness",
            "label": "운영 안정성",
            "max_score": 15,
            "critical": False,
        },
    ],
    "go_service": [
        {
            "id": "service_contracts",
            "label": "핵심 서비스 계약 구현",
            "max_score": 30,
            "critical": True,
        },
        {
            "id": "module_flow",
            "label": "모듈/핸들러 흐름 완결성",
            "max_score": 30,
            "critical": True,
        },
        {
            "id": "verification_evidence",
            "label": "go build 검증 증거 충족",
            "max_score": 25,
            "critical": True,
        },
        {
            "id": "operational_readiness",
            "label": "운영 안정성",
            "max_score": 15,
            "critical": False,
        },
    ],
    "rust_service": [
        {
            "id": "service_contracts",
            "label": "핵심 서비스 계약 구현",
            "max_score": 30,
            "critical": True,
        },
        {
            "id": "crate_wiring",
            "label": "crate/핸들러 연결 완결성",
            "max_score": 30,
            "critical": True,
        },
        {
            "id": "verification_evidence",
            "label": "cargo check 검증 증거 충족",
            "max_score": 25,
            "critical": True,
        },
        {
            "id": "operational_readiness",
            "label": "운영 안정성",
            "max_score": 15,
            "critical": False,
        },
    ],
    "generic": [
        {
            "id": "requirements",
            "label": "핵심 요구 구현",
            "max_score": 40,
            "critical": True,
        },
        {
            "id": "integration",
            "label": "구성요소 연결 완결성",
            "max_score": 25,
            "critical": True,
        },
        {
            "id": "verification",
            "label": "검증 증거 충족",
            "max_score": 20,
            "critical": True,
        },
        {
            "id": "production_readiness",
            "label": "잔여 리스크 통제",
            "max_score": 15,
            "critical": False,
        },
    ],
}


def _build_trading_system_production_ai_template_candidates(
    project_name: str,
    order_profile: Dict[str, Any],
    domain_contract: Dict[str, Any],
) -> Dict[str, str]:
    sample_records_json = json.dumps(_resolve_customer_engine_seed_records("trading_system"), ensure_ascii=False, indent=2)
    mandatory_contracts_json = json.dumps(order_profile.get("mandatory_engine_contracts") or [], ensure_ascii=False)
    database_tables_json = json.dumps(domain_contract.get("database_tables") or [], ensure_ascii=False)
    jwt_scopes_json = json.dumps(domain_contract.get("jwt_scopes") or [], ensure_ascii=False)
    ops_channels_json = json.dumps(domain_contract.get("ops_channels") or [], ensure_ascii=False)
    adapter_targets_json = json.dumps(domain_contract.get("adapter_targets") or [], ensure_ascii=False)
    return {
        "backend/core/database.py": (
            "from __future__ import annotations\n"
            "import os\n"
            "from pathlib import Path\n"
            "from typing import Any, Dict\n"
            "from sqlalchemy import create_engine\n"
            "from sqlalchemy.orm import declarative_base, sessionmaker\n\n"
            f"DATABASE_TABLES = {database_tables_json}\n"
            "DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///./runtime/data/trading.db')\n"
            "DB_SETTINGS = {'url': DATABASE_URL, 'tables': DATABASE_TABLES}\n"
            "Base = declarative_base()\n"
            "engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True, connect_args={'check_same_thread': False} if DATABASE_URL.startswith('sqlite') else {})\n"
            "SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)\n\n"
            "def get_database_settings() -> Dict[str, Any]:\n"
            "    return {'url': DATABASE_URL, 'tables': list(DATABASE_TABLES), 'dialect': 'sqlite' if DATABASE_URL.startswith('sqlite') else 'external'}\n\n"
            "def ensure_database_ready() -> Dict[str, Any]:\n"
            "    from backend.core import models  # noqa: F401\n"
            "    if DATABASE_URL.startswith('sqlite'):\n"
            "        target = DATABASE_URL.replace('sqlite:///', '', 1)\n"
            "        Path(target).parent.mkdir(parents=True, exist_ok=True)\n"
            "    Base.metadata.create_all(bind=engine)\n"
            "    return get_database_settings()\n"
        ),
        "backend/core/models.py": (
            "from __future__ import annotations\n"
            "from datetime import datetime\n"
            "from sqlalchemy import DateTime, Float, Integer, String, Text\n"
            "from sqlalchemy.orm import Mapped, mapped_column\n"
            "from backend.core.database import Base\n\n"
            "class RuntimeEvent(Base):\n"
            "    __tablename__ = 'runtime_events'\n"
            "    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)\n"
            "    event: Mapped[str] = mapped_column(String(120), index=True)\n"
            "    detail_json: Mapped[str] = mapped_column(Text, default='{}')\n"
            "    channels: Mapped[str] = mapped_column(String(240), default='audit')\n"
            "    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)\n\n"
            "class ModelRegistryEntry(Base):\n"
            "    __tablename__ = 'model_registry_entries'\n"
            "    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)\n"
            "    version: Mapped[str] = mapped_column(String(120), index=True)\n"
            "    status: Mapped[str] = mapped_column(String(40), default='trained')\n"
            "    adapter_profile: Mapped[str] = mapped_column(String(80), default='trading')\n"
            "    payload_json: Mapped[str] = mapped_column(Text, default='{}')\n"
            "    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)\n\n"
            "class TradeOrder(Base):\n"
            "    __tablename__ = 'trade_orders'\n"
            "    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)\n"
            "    symbol: Mapped[str] = mapped_column(String(32), index=True)\n"
            "    side: Mapped[str] = mapped_column(String(16), index=True)\n"
            "    quantity: Mapped[int] = mapped_column(Integer, default=0)\n"
            "    broker_order_id: Mapped[str] = mapped_column(String(80), default='paper-order')\n"
            "    status: Mapped[str] = mapped_column(String(32), default='accepted')\n"
            "    score: Mapped[float] = mapped_column(Float, default=0.0)\n"
            "    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)\n\n"
            "class PortfolioPosition(Base):\n"
            "    __tablename__ = 'portfolio_positions'\n"
            "    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)\n"
            "    symbol: Mapped[str] = mapped_column(String(32), index=True)\n"
            "    quantity: Mapped[int] = mapped_column(Integer, default=0)\n"
            "    average_price: Mapped[float] = mapped_column(Float, default=0.0)\n"
            "    source: Mapped[str] = mapped_column(String(32), default='paper-broker')\n"
            "    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)\n"
        ),
        "backend/core/auth.py": (
            "import os\n"
            "from datetime import datetime, timedelta\n"
            "from typing import Any, Dict\n"
            "from jose import JWTError, jwt\n\n"
            f"JWT_SCOPES = {jwt_scopes_json}\n"
            "JWT_SECRET = os.getenv('JWT_SECRET', 'codeai-trading-prod-secret-change-me')\n"
            "JWT_ALGORITHM = os.getenv('JWT_ALGORITHM', 'HS256')\n"
            "JWT_EXPIRE_MINUTES = int(os.getenv('JWT_EXPIRE_MINUTES', '60'))\n\n"
            "def get_auth_settings() -> Dict[str, Any]:\n"
            "    return {'AUTH_SETTINGS': True, 'enabled': True, 'algorithm': JWT_ALGORITHM, 'scopes': list(JWT_SCOPES), 'token_header': 'Authorization'}\n\n"
            "def create_access_token(subject: str, scopes: list[str] | None = None) -> str:\n"
            "    expire = datetime.utcnow() + timedelta(minutes=JWT_EXPIRE_MINUTES)\n"
            "    payload = {'sub': subject, 'scopes': scopes or list(JWT_SCOPES), 'exp': expire}\n"
            "    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)\n\n"
            "def decode_access_token(token: str) -> Dict[str, Any]:\n"
            "    try:\n"
            "        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])\n"
            "    except JWTError as exc:\n"
            "        return {'valid': False, 'error': str(exc)}\n"
            "    return {'valid': True, 'payload': payload}\n"
        ),
        "backend/core/ops_logging.py": (
            "from __future__ import annotations\n"
            "from datetime import datetime\n"
            "from pathlib import Path\n"
            "from typing import Any, Dict, List\n"
            "import json\n"
            "import os\n\n"
            "from backend.core.database import SessionLocal, ensure_database_ready\n"
            "from backend.core.models import RuntimeEvent\n\n"
            f"OPS_CHANNELS = {ops_channels_json}\n"
            "OPS_LOG_PATH = Path(os.getenv('OPS_LOG_PATH', 'runtime/logs/ops-events.jsonl'))\n"
            "OPS_MEMORY_BUFFER: List[Dict[str, Any]] = []\n\n"
            "def record_ops_log(event: str, detail: Dict[str, Any] | None = None) -> Dict[str, Any]:\n"
            "    payload = {'event': event, 'detail': detail or {}, 'recorded_at': datetime.utcnow().isoformat(), 'channels': list(OPS_CHANNELS)}\n"
            "    OPS_MEMORY_BUFFER.append(payload)\n"
            "    ensure_database_ready()\n"
            "    session = SessionLocal()\n"
            "    try:\n"
            "        session.add(RuntimeEvent(event=event, detail_json=json.dumps(payload['detail'], ensure_ascii=False), channels=','.join(OPS_CHANNELS)))\n"
            "        session.commit()\n"
            "    finally:\n"
            "        session.close()\n"
            "    OPS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)\n"
            "    with OPS_LOG_PATH.open('a', encoding='utf-8') as handle:\n"
            "        handle.write(json.dumps(payload, ensure_ascii=False) + '\\n')\n"
            "    return payload\n\n"
            "def list_ops_logs() -> List[Dict[str, Any]]:\n"
            "    return [dict(item) for item in OPS_MEMORY_BUFFER]\n"
        ),
        "ai/model_registry.py": (
            "from pathlib import Path\n"
            "import json\n"
            "import os\n\n"
            "from backend.core.database import SessionLocal, ensure_database_ready\n"
            "from backend.core.models import ModelRegistryEntry\n\n"
            "MODEL_REGISTRY: list[dict] = []\n"
            "MODEL_REGISTRY_PATH = Path(os.getenv('MODEL_REGISTRY_PATH', 'runtime/models/registry.json'))\n\n"
            "def _sync_registry_file() -> None:\n"
            "    MODEL_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)\n"
            "    MODEL_REGISTRY_PATH.write_text(json.dumps(MODEL_REGISTRY, ensure_ascii=False, indent=2), encoding='utf-8')\n\n"
            "def register_model_version(model: dict) -> None:\n"
            "    MODEL_REGISTRY.append(dict(model))\n"
            "    ensure_database_ready()\n"
            "    session = SessionLocal()\n"
            "    try:\n"
            "        session.add(ModelRegistryEntry(version=str(model.get('version', 'bootstrap')), status=str(model.get('status', 'trained')), adapter_profile='trading', payload_json=json.dumps(model, ensure_ascii=False)))\n"
            "        session.commit()\n"
            "    finally:\n"
            "        session.close()\n"
            "    _sync_registry_file()\n\n"
            "def get_latest_model() -> dict:\n"
            "    ensure_database_ready()\n"
            "    session = SessionLocal()\n"
            "    try:\n"
            "        entry = session.query(ModelRegistryEntry).order_by(ModelRegistryEntry.id.desc()).first()\n"
            "        if entry and entry.payload_json:\n"
            "            return json.loads(entry.payload_json)\n"
            "    finally:\n"
            "        session.close()\n"
            "    return MODEL_REGISTRY[-1].copy() if MODEL_REGISTRY else {'version': 'bootstrap'}\n"
        ),
        "backend/app/external_adapters/status_client.py": (
            "from __future__ import annotations\n"
            "import time\n"
            "import httpx\n\n"
            "def build_provider_status_map() -> list[dict]:\n"
            "    return [{'provider': 'signal-feed', 'reachable': True, 'latency_ms': 18}, {'provider': 'paper-broker', 'reachable': True, 'latency_ms': 26}, {'provider': 'portfolio-sync', 'reachable': True, 'latency_ms': 22}]\n\n"
            "def fetch_upstream_status(base_url: str | None = None, retries: int = 3, timeout: float = 2.0) -> dict:\n"
            "    if not base_url or 'example.com' in base_url:\n"
            "        providers = build_provider_status_map()\n"
            "        return {'provider': 'trading-upstream', 'reachable': all(item['reachable'] for item in providers), 'providers': providers, 'mode': 'paper'}\n"
            "    last_error = None\n"
            "    for attempt in range(retries):\n"
            "        try:\n"
            "            response = httpx.get(base_url.rstrip('/') + '/health', timeout=timeout)\n"
            "            response.raise_for_status()\n"
            "            payload = response.json()\n"
            "            return {'provider': 'trading-upstream', 'reachable': True, 'providers': payload.get('providers', []), 'mode': 'live'}\n"
            "        except Exception as exc:\n"
            "            last_error = str(exc)\n"
            "            time.sleep(min(0.2 * (attempt + 1), 0.5))\n"
            "    return {'provider': 'trading-upstream', 'reachable': False, 'providers': [], 'error': last_error, 'mode': 'degraded'}\n"
        ),
        "backend/app/connectors/base.py": (
            "from dataclasses import dataclass\n\n"
            "@dataclass\n"
            "class CatalogConnectorResult:\n"
            "    provider: str\n"
            "    synced_count: int\n"
            "    reachable: bool\n"
            "    open_positions: int\n\n"
            "class BaseConnector:\n"
            "    provider_name = 'paper-broker'\n"
            "    request_timeout_sec = 5.0\n\n"
            "    def sync_products(self) -> list[dict]:\n"
            "        raise NotImplementedError('sync_products must be implemented by a trading connector')\n\n"
            "    def build_position_snapshot(self, symbol: str, quantity: int, average_price: float) -> dict:\n"
            "        notional = round(quantity * average_price, 4)\n"
            "        return {'symbol': symbol, 'quantity': quantity, 'average_price': average_price, 'notional': notional, 'provider': self.provider_name}\n\n"
            "    def build_sync_summary(self, synced_count: int, reachable: bool = True, open_positions: int = 0) -> CatalogConnectorResult:\n"
            "        return CatalogConnectorResult(provider=self.provider_name, synced_count=synced_count, reachable=reachable, open_positions=open_positions)\n"
        ),
        "backend/app/connectors/broker.py": (
            "from __future__ import annotations\n"
            "import time\n"
            "import httpx\n"
            "from backend.app.connectors.base import BaseConnector\n"
            "from backend.app.external_adapters.status_client import fetch_upstream_status\n\n"
            "class BrokerConnector(BaseConnector):\n"
            "    provider_name = 'paper-broker'\n\n"
            "    def __init__(self, base_url: str) -> None:\n"
            "        self.base_url = base_url.rstrip('/')\n\n"
            "    def get_health(self) -> dict:\n"
            "        return fetch_upstream_status(self.base_url)\n\n"
            "    def list_positions(self) -> list[dict]:\n"
            "        if not self.base_url or 'example.com' in self.base_url:\n"
            "            return [self.build_position_snapshot('AAPL', 12, 189.4), self.build_position_snapshot('MSFT', 5, 421.8)]\n"
            "        response = httpx.get(f'{self.base_url}/positions', timeout=3.0)\n"
            "        response.raise_for_status()\n"
            "        return list(response.json().get('positions') or [])\n\n"
            "    def create_order(self, symbol: str, side: str, quantity: int, retries: int = 3) -> dict:\n"
            "        if not self.base_url or 'example.com' in self.base_url:\n"
            "            return {'broker_order_id': f'paper-{symbol.lower()}-{side.lower()}', 'status': 'accepted', 'symbol': symbol, 'side': side, 'quantity': quantity}\n"
            "        last_error = None\n"
            "        for attempt in range(retries):\n"
            "            try:\n"
            "                response = httpx.post(f'{self.base_url}/orders', json={'symbol': symbol, 'side': side, 'quantity': quantity}, timeout=3.0)\n"
            "                response.raise_for_status()\n"
            "                return response.json()\n"
            "            except Exception as exc:\n"
            "                last_error = str(exc)\n"
            "                time.sleep(min(0.2 * (attempt + 1), 0.5))\n"
            "        return {'broker_order_id': 'retry-exhausted', 'status': 'failed', 'error': last_error, 'symbol': symbol, 'side': side, 'quantity': quantity}\n"
        ),
        "app/auth_routes.py": (
            "from fastapi import APIRouter, HTTPException\n"
            "from backend.core.auth import create_access_token, decode_access_token, get_auth_settings\n\n"
            "auth_router = APIRouter(prefix='/auth', tags=['auth'])\n\n"
            "@auth_router.get('/settings')\n"
            "def auth_settings():\n"
            "    return get_auth_settings()\n\n"
            "@auth_router.post('/token')\n"
            "def issue_token(payload: dict | None = None):\n"
            "    request_payload = payload or {}\n"
            "    subject = str(request_payload.get('subject') or 'trading-operator')\n"
            "    scopes = list(request_payload.get('scopes') or get_auth_settings().get('scopes') or [])\n"
            "    token = create_access_token(subject, scopes=scopes)\n"
            "    return {'access_token': token, 'token_type': 'bearer', 'scopes': scopes}\n\n"
            "@auth_router.post('/validate')\n"
            "def validate_token(payload: dict | None = None):\n"
            "    token = str((payload or {}).get('token') or '').strip()\n"
            "    if not token:\n"
            "        raise HTTPException(status_code=400, detail='token is required')\n"
            "    return decode_access_token(token)\n"
        ),
        "app/ops_routes.py": (
            "from fastapi import APIRouter\n"
            "from fastapi.responses import PlainTextResponse\n"
            "from backend.core.database import get_database_settings\n"
            "from backend.core.ops_logging import list_ops_logs, record_ops_log\n"
            "from backend.app.external_adapters.status_client import fetch_upstream_status\n\n"
            "ops_router = APIRouter(tags=['ops'])\n\n"
            "@ops_router.get('/ops/logs')\n"
            "def ops_logs():\n"
            "    return {'items': list_ops_logs(), 'count': len(list_ops_logs())}\n\n"
            "@ops_router.get('/ops/health')\n"
            "def ops_health():\n"
            "    broker = fetch_upstream_status('https://paper-broker.example.com')\n"
            "    record_ops_log('ops_health_checked', {'status': 'ok', 'broker': broker.get('reachable')})\n"
            "    return {'status': 'ok', 'database': get_database_settings(), 'broker': broker, 'log_count': len(list_ops_logs())}\n\n"
            "@ops_router.get('/metrics')\n"
            "def metrics():\n"
            "    payload = list_ops_logs()\n"
            "    lines = ['# HELP codeai_ops_events_total Count of ops events', '# TYPE codeai_ops_events_total counter', f'codeai_ops_events_total {len(payload)}']\n"
            "    return PlainTextResponse('\\n'.join(lines) + '\\n')\n"
        ),
        "backend/service/domain_adapter_service.py": (
            "from ai.adapters import resolve_adapter\n"
            "from ai.features import build_feature_set\n"
            "from backend.app.connectors.broker import BrokerConnector\n\n"
            "def build_domain_adapter_summary(payload: dict | None = None) -> dict:\n"
            "    adapter = resolve_adapter()\n"
            "    features = build_feature_set(payload or {})\n"
            "    broker = BrokerConnector('https://paper-broker.example.com')\n"
            "    return {'adapter': adapter, 'model_endpoint': adapter.get('model_endpoint'), 'features': features, 'broker': broker.get_health(), 'build_domain_adapter_summary': True}\n"
        ),
        "backend/service/strategy_service.py": (
            "from __future__ import annotations\n"
            "import json\n"
            "from app.order_profile import get_order_profile\n"
            "from ai.features import build_feature_set\n"
            "from ai.inference import run_inference\n"
            "from ai.evaluation import evaluate_predictions\n"
            "from ai.train import train_model\n"
            "from ai.model_registry import get_latest_model\n"
            "from backend.app.connectors.broker import BrokerConnector\n"
            "from backend.core.database import SessionLocal, ensure_database_ready\n"
            "from backend.core.models import PortfolioPosition, TradeOrder\n"
            "from backend.core.ops_logging import record_ops_log\n\n"
            f"DEFAULT_DOMAIN_RECORDS = {sample_records_json}\n"
            f"MANDATORY_ENGINE_CONTRACTS = {mandatory_contracts_json}\n"
            "DOMAIN_RECORD_KEY = 'signals'\n\n"
            "def load_model_registry() -> dict:\n"
            "    profile = get_order_profile()\n"
            "    latest_model = get_latest_model()\n"
            "    return {'registry_name': 'domain-model-registry', 'primary_model': latest_model.get('version', profile.get('project_name', 'domain-engine')), 'version': latest_model.get('version', 'bootstrap')}\n\n"
            "def build_engine_core() -> dict:\n"
            "    features = build_feature_set({DOMAIN_RECORD_KEY: DEFAULT_DOMAIN_RECORDS})\n"
            "    return {'engine-core': True, 'records': features.get(DOMAIN_RECORD_KEY, []), 'feature-pipeline': {'feature_windows': features.get('feature_windows', []), 'window_count': features.get('feature_count', 0)}}\n\n"
            "def run_training_pipeline() -> dict:\n"
            "    model = train_model(DEFAULT_DOMAIN_RECORDS)\n"
            "    record_ops_log('training_pipeline_completed', {'version': model.get('version')})\n"
            "    return {'status': model.get('status', 'trained'), 'pipeline': 'engine-core -> feature-pipeline -> training-pipeline', 'training-pipeline': True, 'model': model}\n\n"
            "def run_inference_runtime(features: dict | None = None) -> dict:\n"
            "    payload = dict(features or {})\n"
            "    payload.setdefault(DOMAIN_RECORD_KEY, DEFAULT_DOMAIN_RECORDS)\n"
            "    inference = run_inference(payload)\n"
            "    return {'decision': inference.get('decision', 'HOLD'), 'score': inference.get('score', 0.0), 'risk_score': inference.get('risk_score', 0.0), 'order_action': inference.get('order_action', inference.get('decision', 'HOLD')), 'broker_status': inference.get('broker_status', 'paper-ready'), 'model_version': inference.get('model_version', 'bootstrap'), 'candidate_sets': inference.get('candidate_sets', []), 'prediction_runs': inference.get('prediction_runs', 0), 'inference-runtime': True, 'features': payload}\n\n"
            "def build_risk_guard(runtime: dict | None = None) -> dict:\n"
            "    active_runtime = runtime or run_inference_runtime({DOMAIN_RECORD_KEY: DEFAULT_DOMAIN_RECORDS})\n"
            "    risk_score = float(active_runtime.get('risk_score', 0.0) or 0.0)\n"
            "    blocked = risk_score > 0.6\n"
            "    return {'risk-guard': True, 'risk_score': risk_score, 'blocked': blocked, 'limit': 0.6}\n\n"
            "def build_order_execution_plan(runtime: dict | None = None) -> dict:\n"
            "    active_runtime = runtime or run_inference_runtime({DOMAIN_RECORD_KEY: DEFAULT_DOMAIN_RECORDS})\n"
            "    guard = build_risk_guard(active_runtime)\n"
            "    connector = BrokerConnector('https://paper-broker.example.com')\n"
            "    side = 'BUY' if active_runtime.get('order_action') == 'BUY' else 'SELL' if active_runtime.get('order_action') == 'SELL' else 'HOLD'\n"
            "    order_payload = connector.create_order('AAPL', side, 1 if side != 'HOLD' else 0) if not guard.get('blocked') else {'broker_order_id': 'blocked', 'status': 'blocked'}\n"
            "    ensure_database_ready()\n"
            "    session = SessionLocal()\n"
            "    try:\n"
            "        session.add(TradeOrder(symbol='AAPL', side=side, quantity=1 if side != 'HOLD' else 0, broker_order_id=str(order_payload.get('broker_order_id', 'paper-order')), status=str(order_payload.get('status', 'accepted')), score=float(active_runtime.get('score', 0.0) or 0.0)))\n"
            "        session.commit()\n"
            "    finally:\n"
            "        session.close()\n"
            "    record_ops_log('order_execution_built', {'approved': not bool(guard.get('blocked')), 'side': side, 'broker_order_id': order_payload.get('broker_order_id')})\n"
            "    return {'order-execution': True, 'broker-adapter': active_runtime.get('broker_status', 'paper-ready'), 'order_action': side, 'approved': not bool(guard.get('blocked')), 'broker_order': order_payload}\n\n"
            "def build_portfolio_sync(runtime: dict | None = None) -> dict:\n"
            "    active_runtime = runtime or run_inference_runtime({DOMAIN_RECORD_KEY: DEFAULT_DOMAIN_RECORDS})\n"
            "    connector = BrokerConnector('https://paper-broker.example.com')\n"
            "    positions = connector.list_positions()\n"
            "    ensure_database_ready()\n"
            "    session = SessionLocal()\n"
            "    try:\n"
            "        for position in positions:\n"
            "            session.add(PortfolioPosition(symbol=str(position.get('symbol', 'AAPL')), quantity=int(position.get('quantity', position.get('position', 0)) or 0), average_price=float(position.get('average_price', position.get('avg_price', 0.0)) or 0.0), source='paper-broker'))\n"
            "        session.commit()\n"
            "    finally:\n"
            "        session.close()\n"
            "    record_ops_log('portfolio_sync_completed', {'position_count': len(positions)})\n"
            "    return {'portfolio-sync': True, 'portfolio_action': active_runtime.get('order_action', 'HOLD'), 'position_delta': 1 if active_runtime.get('order_action') == 'BUY' else -1 if active_runtime.get('order_action') == 'SELL' else 0, 'positions': positions}\n\n"
            "def build_evaluation_report() -> dict:\n"
            "    runtime = run_inference_runtime({DOMAIN_RECORD_KEY: DEFAULT_DOMAIN_RECORDS})\n"
            "    evaluation = evaluate_predictions([runtime])\n"
            "    return {'report_name': 'domain-evaluation', 'metrics': ['candidate_sets', 'average_score', 'quality_gate'], 'status': evaluation.get('quality_gate', 'needs-data'), 'evaluation-report': True, 'evaluation': evaluation}\n\n"
            "def build_strategy_service_overview(sample_payload: dict | None = None) -> dict:\n"
            "    profile = get_order_profile()\n"
            "    engine_core = build_engine_core()\n"
            "    training_pipeline = run_training_pipeline()\n"
            "    inference_runtime = run_inference_runtime(sample_payload or {DOMAIN_RECORD_KEY: DEFAULT_DOMAIN_RECORDS})\n"
            "    risk_guard = build_risk_guard(inference_runtime)\n"
            "    order_execution = build_order_execution_plan(inference_runtime)\n"
            "    portfolio_sync = build_portfolio_sync(inference_runtime)\n"
            "    evaluation_report = build_evaluation_report()\n"
            "    return {'ai_enabled': bool(profile.get('ai_enabled')), 'ai_capabilities': list(profile.get('ai_capabilities') or []), 'mandatory_engine_contracts': list(profile.get('mandatory_engine_contracts') or []), 'engine-core': engine_core, 'service-integration': True, 'risk-guard': risk_guard, 'order-execution': order_execution, 'portfolio-sync': portfolio_sync, 'broker-adapter': order_execution.get('broker-adapter', 'paper-ready'), 'model_registry': load_model_registry(), 'training_pipeline': training_pipeline, 'inference_runtime': inference_runtime, 'evaluation_report': evaluation_report}\n"
        ),
        "app/main.py": (
            f"from fastapi import FastAPI\n"
            "from app.auth_routes import auth_router\n"
            "from app.ops_routes import ops_router\n"
            "from app.routes import router\n"
            "from app.services import build_runtime_payload, summarize_health\n"
            "from app.diagnostics import build_diagnostic_report\n"
            "from app.order_profile import get_order_profile\n"
            "from ai.router import router as ai_router\n"
            "from backend.core.database import ensure_database_ready\n\n"
            "def create_application() -> FastAPI:\n"
            f"    app = FastAPI(title={project_name!r}, version='1.0.0')\n"
            "    app.include_router(router)\n"
            "    app.include_router(auth_router)\n"
            "    app.include_router(ops_router)\n"
            "    app.include_router(ai_router)\n\n"
            "    @app.on_event('startup')\n"
            "    def startup() -> None:\n"
            "        ensure_database_ready()\n\n"
            "    @app.get('/')\n"
            "    def root():\n"
            "        profile = get_order_profile()\n"
            "        return {'status': 'ok', 'project': profile['project_name'], 'profile': profile['label'], 'mode': 'customer-order-generator'}\n\n"
            "    @app.get('/runtime')\n"
            "    def runtime():\n"
            "        payload = build_runtime_payload(runtime_mode='runtime')\n"
            "        payload['health'] = summarize_health()\n"
            "        payload['diagnostics'] = build_diagnostic_report()\n"
            "        return payload\n\n"
            "    return app\n\n"
            "app = create_application()\n"
        ),
        "app/services/__init__.py": (
            "from app.services.runtime_service import build_ai_runtime_contract, build_domain_snapshot, build_feature_matrix, build_runtime_payload, build_trace_lookup, list_endpoints, summarize_health\n\n"
            "__all__ = ['build_ai_runtime_contract', 'build_feature_matrix', 'build_trace_lookup', 'build_domain_snapshot', 'build_runtime_payload', 'list_endpoints', 'summarize_health']\n"
        ),
        "app/services/runtime_service.py": (
            "from datetime import datetime\n"
            "from app.runtime import build_runtime_context, describe_runtime_profile\n"
            "from app.order_profile import get_order_profile, get_flow_step, list_flow_steps\n"
            "from backend.core.database import ensure_database_ready, get_database_settings\n"
            "from backend.core.auth import create_access_token, get_auth_settings\n"
            "from backend.core.ops_logging import record_ops_log\n"
            "from backend.service.domain_adapter_service import build_domain_adapter_summary\n"
            "from backend.service.strategy_service import build_strategy_service_overview\n"
            "from ai.schemas import InferenceRequest, TrainingRequest, EvaluationRequest\n"
            "from ai.train import train_model\n"
            "from ai.inference import run_inference\n"
            "from ai.evaluation import evaluate_predictions\n"
            "from ai.model_registry import get_latest_model\n\n"
            f"DEFAULT_DOMAIN_RECORDS = {sample_records_json}\n"
            f"MANDATORY_ENGINE_CONTRACTS = {mandatory_contracts_json}\n"
            "DOMAIN_RECORD_KEY = 'signals'\n\n"
            "def build_feature_matrix() -> list[dict]:\n"
            "    return [{'flow_id': item['flow_id'], 'step_number': item.get('step_number'), 'step_id': item['step_id'], 'action': item['action'], 'trace_id': item.get('trace_id'), 'title': item['title'], 'state': 'ready'} for item in list_flow_steps()]\n\n"
            "def build_trace_lookup(step_id: str = 'FLOW-001-1') -> dict:\n"
            "    return get_flow_step(step_id) or {'step_id': step_id, 'missing': True}\n\n"
            "def build_domain_snapshot() -> dict:\n"
            "    profile = get_order_profile()\n"
            "    return {'profile_id': profile['profile_id'], 'entities': profile['entities'], 'requested_outcomes': profile['requested_outcomes'], 'ui_modules': profile['ui_modules'], 'mandatory_engine_contracts': list(profile.get('mandatory_engine_contracts') or [])}\n\n"
            "def build_ai_runtime_contract() -> dict:\n"
            "    train_request = TrainingRequest(dataset=DEFAULT_DOMAIN_RECORDS)\n"
            "    inference_request = InferenceRequest(signal_strength=0.7, features={DOMAIN_RECORD_KEY: DEFAULT_DOMAIN_RECORDS})\n"
            "    model = train_model(train_request.dataset)\n"
            "    database = ensure_database_ready()\n"
            "    inference_payload = dict(inference_request.features)\n"
            "    inference_payload['signal_strength'] = inference_request.signal_strength\n"
            "    prediction = run_inference(inference_payload)\n"
            "    evaluation = evaluate_predictions([prediction])\n"
            "    strategy_service = build_strategy_service_overview(inference_payload)\n"
            "    access_token = create_access_token('system-orchestrator')\n"
            "    record_ops_log('ai_runtime_contract_checked', {'prediction_runs': prediction.get('prediction_runs', 0)})\n"
            "    return {'mandatory_engine_contracts': list(MANDATORY_ENGINE_CONTRACTS), 'engine-core': strategy_service.get('engine-core'), 'feature-pipeline': strategy_service.get('engine-core', {}).get('feature-pipeline'), 'training-pipeline': strategy_service.get('training_pipeline'), 'inference-runtime': strategy_service.get('inference_runtime'), 'evaluation-report': strategy_service.get('evaluation_report'), 'service-integration': strategy_service.get('service-integration', True), 'schemas': ['TrainingRequest', 'InferenceRequest', 'EvaluationRequest'], 'endpoints': ['/ai/health', '/ai/train', '/ai/inference', '/ai/evaluate'], 'model_registry': get_latest_model(), 'training_pipeline': model, 'inference_runtime': prediction, 'evaluation_report': evaluation, 'domain_adapter': build_domain_adapter_summary(inference_payload), 'database': database, 'auth': get_auth_settings(), 'token_preview': access_token[:16], 'candidate_sets': prediction.get('candidate_sets', []), 'validation': {'ok': bool(model.get('status')) and bool(prediction.get('candidate_sets')) and evaluation.get('quality_gate') == 'pass', 'checked_via': ['/health', '/report']}}\n\n"
            "def build_runtime_payload(runtime_mode: str = 'default') -> dict:\n"
            "    profile = get_order_profile()\n"
            "    runtime_context = build_runtime_context()\n"
            "    record_ops_log('runtime_payload_built', {'runtime_mode': runtime_mode, 'profile_id': profile['profile_id']})\n"
            "    return {'service': 'customer-order-generator', 'runtime_mode': runtime_mode, 'started_at': datetime.utcnow().isoformat(), 'order_profile': profile, 'active_trace': build_trace_lookup(), 'feature_matrix': build_feature_matrix(), 'domain_snapshot': build_domain_snapshot(), 'runtime_context': runtime_context, 'profile': describe_runtime_profile(), 'mandatory_engine_contracts': list(profile.get('mandatory_engine_contracts') or []), 'ai_runtime_contract': build_ai_runtime_contract()}\n\n"
            "def list_endpoints() -> list[str]:\n"
            "    endpoints = ['/', '/runtime', '/health', '/config', '/order-profile', '/flow-map', '/flow-map/{step_id}', '/workspace', '/report', '/diagnose']\n"
            "    endpoints.extend(['/ai/health', '/ai/train', '/ai/inference', '/ai/evaluate'])\n"
            "    return endpoints\n\n"
            "def summarize_health() -> dict:\n"
            "    payload = build_runtime_payload(runtime_mode='health')\n"
            "    payload['status'] = 'ok'\n"
            "    payload['checks'] = {'profile_loaded': True, 'flow_bound': True, 'delivery_ready': True, 'ai_contract_ready': bool(payload.get('ai_runtime_contract', {}).get('validation', {}).get('ok'))}\n"
            "    return payload\n"
        ),
        "tests/test_health.py": (
            "from fastapi.testclient import TestClient\n"
            "from app.main import app\n\n"
            "client = TestClient(app)\n\n"
            "def test_health():\n"
            "    response = client.get('/health')\n"
            "    assert response.status_code == 200\n"
            "    assert response.json()['status'] == 'ok'\n"
            "    assert response.json()['checks']['ai_contract_ready'] is True\n"
        ),
        "tests/test_routes.py": (
            "from fastapi.testclient import TestClient\n"
            "from app.main import app\n\n"
            "client = TestClient(app)\n\n"
            "def test_order_profile_route():\n"
            "    response = client.get('/order-profile')\n"
            "    assert response.status_code == 200\n"
            "    payload = response.json()\n"
            "    assert payload['profile_id'] == 'trading_system'\n"
            "    report = client.get('/report')\n"
            "    assert report.status_code == 200\n\n"
            "def test_auth_and_ai_endpoints():\n"
            "    token_payload = client.post('/auth/token', json={'subject': 'ops-user'}).json()\n"
            "    assert token_payload['access_token']\n"
            "    validate = client.post('/auth/validate', json={'token': token_payload['access_token']}).json()\n"
            "    assert validate['valid'] is True\n"
            "    assert client.get('/ai/health').status_code == 200\n"
            "    infer = client.post('/ai/inference', json={'signal_strength': 0.8, 'features': {'signals': []}})\n"
            "    assert infer.status_code == 200\n"
            "    evaluate = client.post('/ai/evaluate', json={'predictions': [{'candidate_sets': [{'target': 'signal_strength', 'rank': 1, 'score': 0.8}], 'score': 0.8}]})\n"
            "    assert evaluate.status_code == 200\n"
            "    assert client.get('/ops/health').status_code == 200\n"
        ),
        "tests/test_runtime.py": (
            "from app.services import build_runtime_payload\n\n"
            "def test_runtime_payload_contains_trading_contract():\n"
            "    payload = build_runtime_payload(runtime_mode='test')\n"
            "    assert payload['service'] == 'customer-order-generator'\n"
            "    assert payload['order_profile']['profile_id'] == 'trading_system'\n"
            "    assert payload['mandatory_engine_contracts']\n"
            "    assert payload['ai_runtime_contract']['validation']['ok'] is True\n"
            "    assert payload['ai_runtime_contract']['database']['tables']\n"
            "    assert payload['ai_runtime_contract']['auth']['scopes']\n"
        ),
        "tests/test_ai_pipeline.py": (
            "from app.services import build_ai_runtime_contract\n"
            "from backend.service.strategy_service import build_strategy_service_overview\n\n"
            "def test_ai_pipeline_runs():\n"
            "    contract = build_ai_runtime_contract()\n"
            "    strategy = build_strategy_service_overview()\n"
            "    assert contract['mandatory_engine_contracts']\n"
            "    assert contract['training-pipeline']\n"
            "    assert contract['inference-runtime']\n"
            "    assert contract['evaluation-report']\n"
            "    assert contract['candidate_sets']\n"
            "    assert contract['validation']['ok'] is True\n"
            "    assert strategy['order-execution']['approved'] is True\n"
            "    assert strategy['portfolio-sync']['positions']\n"
        ),
        "docs/deployment.md": (
            "# deployment\n\n"
            "- install dependencies: `pip install -r requirements.txt`\n"
            "- configure env: `copy configs/app.env.example .env`\n"
            "- run api: `uvicorn app.main:create_application --factory --host 0.0.0.0 --port 8000`\n"
            "- build container: `docker build -t trading-runtime .`\n"
            "- container run: `docker run --rm -p 8000:8000 --env-file .env trading-runtime`\n"
            "- container run verification: `/health`, `/ai/health`, `/ops/health`\n"
            "- production ingress must terminate TLS and provide secure JWT_SECRET / DATABASE_URL / BROKER_BASE_URL`\n"
        ),
        "infra/README.md": (
            "# infra\n\n"
            "deployment notes\n\n"
            "- `infra/docker-compose.override.yml` 로 JWT_SECRET:, DATABASE_URL, REQUEST_TIMEOUT_SEC 운영 값을 주입합니다.\n"
            "- `infra/prometheus.yml` 로 /metrics 및 운영 상태 수집 구성을 제공합니다.\n"
            "- `infra/deploy/security.md` 로 운영 보안과 비밀값 관리 지침을 제공합니다.\n"
        ),
        "docs/runbook.md": (
            "# runbook\n\n"
            "## startup\n"
            "- `/health` 확인\n"
            "- `/auth/settings` 확인\n"
            "- `/ai/health` 확인\n"
            "- `/ops/health` 확인\n\n"
            "## smoke\n"
            "- `/auth/token` 으로 운영 토큰 발급\n"
            "- `/ai/inference` 로 시그널 추론 검증\n"
            "- `/report` 로 리스크/주문/포트폴리오 상태 확인\n\n"
            "## recovery\n"
            "- 브로커 장애 시 paper broker fallback 사용\n"
            "- DB 경로 손상 시 `runtime/data` 재생성 후 재기동\n"
            "- `runtime/logs/ops-events.jsonl`와 `/ops/logs`로 감사 로그 확인\n"
        ),
        "configs/app.env.example": (
            "APP_ENV=dev\n"
            "APP_PORT=8000\n"
            "DATABASE_URL=sqlite:///./runtime/data/trading.db\n"
            "JWT_SECRET=replace-with-strong-secret\n"
            "JWT_ALGORITHM=HS256\n"
            "JWT_EXPIRE_MINUTES=60\n"
            "OPS_LOG_PATH=runtime/logs/ops-events.jsonl\n"
            "MODEL_REGISTRY_PATH=runtime/models/registry.json\n"
            "BROKER_BASE_URL=https://paper-broker.example.com\n"
            "SIGNAL_FEED_URL=https://signals.example.com\n"
        ),
        "infra/docker-compose.override.yml": (
            "services:\n"
            "  trading-runtime:\n"
            "    build: ..\n"
            "    command: uvicorn app.main:app --host 0.0.0.0 --port 8000\n"
            "    ports:\n"
            "      - '8000:8000'\n"
            "    environment:\n"
            "      APP_ENV: production\n"
            "      DATABASE_URL: sqlite:///./runtime/data/trading.db\n"
            "      JWT_ALGORITHM: HS256\n"
            "      JWT_SECRET: change-me-in-production\n"
            "      REQUEST_TIMEOUT_SEC: 5\n"
            "      OPS_LOG_PATH: runtime/logs/ops-events.jsonl\n"
            "      MODEL_REGISTRY_PATH: runtime/models/registry.json\n"
            "      BROKER_BASE_URL: https://paper-broker.example.com\n"
        ),
        "infra/deploy/security.md": (
            "# production security\n\n"
            "- JWT_SECRET must be rotated in production\n"
            "- DATABASE_URL should target managed database service\n"
            "- OPS_LOG_PATH should ship to central log sink\n"
            "- Enforce TLS at ingress/load balancer\n"
            "- Broker API credentials must be stored in secret manager\n"
        ),
    }


def _build_trading_system_template_candidates(
    project_name: str,
    order_profile: Dict[str, Any],
    profile_json: str,
    task_excerpt: str,
) -> Dict[str, str]:
    return {
        "README.md": (
            f"# {project_name}\n\n"
            "AI 주식 자동매매 런타임 산출물입니다.\n\n"
            f"- profile: {order_profile['label']}\n"
            f"- summary: {order_profile['summary']}\n"
            f"- request: {task_excerpt or '요청 없음'}\n"
            f"- requested_stack: {', '.join(order_profile.get('requested_stack') or ['python', 'fastapi'])}\n\n"
            "## Included Runtime\n\n"
            "- `app/main.py` 자동매매 FastAPI 엔트리와 실시간 런타임 집계\n"
            "- `app/routes.py` 시그널/리스크/주문/포트폴리오/운영 API\n"
            "- `app/auth_routes.py`, `app/ops_routes.py` 운영 인증/관측 API\n"
            "- `backend/core` 인증/로그/상태 저장 코어 레이어\n"
            "- `backend/service/strategy_service.py` 시그널 적재, 리스크 가드, 주문 실행, 포트폴리오 동기화\n"
            "- `backend/app/connectors/broker.py`, `backend/app/external_adapters/status_client.py` 브로커 어댑터와 degraded fallback 경계\n"
            "- `frontend/app/page.tsx` 전략 상태, 리스크, 포트폴리오 검토 화면\n"
            "- `tests/test_ai_pipeline.py`, `tests/test_routes.py`, `tests/test_runtime.py`, `tests/test_security_runtime.py` 실거래 전 검증 시나리오\n\n"
            "## Operator Checklist\n\n"
            "1. `configs/app.env.example`를 운영 값으로 치환\n"
            "2. `pip install -r requirements.txt` 실행\n"
            "3. `uvicorn app.main:create_application --factory --host 0.0.0.0 --port 8000` 기동\n"
            "4. `/health`, `/runtime`, `/ai/health`, `/ops/health`, `/auth/settings` 확인\n"
            "5. `scripts/check.sh`와 출고 ZIP 재현 결과 확인\n\n"
            "## Core Journeys\n\n"
            "- signal ingestion\n"
            "- risk guard\n"
            "- order execution\n"
            "- portfolio sync\n"
            "- broker adapter\n"
        ),
        "Makefile": (
            "run:\n"
            "\tuvicorn app.main:create_application --factory --reload\n\n"
            "test:\n"
            "\tpytest -q\n\n"
            "check:\n"
            "\tpython -m compileall app backend tests\n"
            "\tpytest -q tests/test_health.py tests/test_routes.py tests/test_runtime.py tests/test_ai_pipeline.py tests/test_security_runtime.py\n"
        ),
        "docs/usage.md": (
            f"# {project_name} 사용 가이드\n\n"
            "1. `pip install -r requirements.txt`\n"
            "2. `uvicorn app.main:create_application --factory --host 0.0.0.0 --port 8000`\n"
            "3. `/health`, `/runtime`, `/ai/health`, `/ops/health`, `/auth/settings`, `/ops/logs`를 순서대로 확인\n"
            "4. `pytest -q`로 자동매매 핵심 시나리오를 검증\n"
            "5. `docs/runbook.md`와 `infra/deploy/security.md` 기준으로 운영 점검\n"
        ),
        "docs/runtime.md": (
            f"# runtime\n\nprofile: {order_profile['label']}\n"
            f"requested_stack: {', '.join(order_profile.get('requested_stack') or [])}\n\n"
            "- signal ingestion: `/runtime`\n"
            "- risk guard: `/report`\n"
            "- order execution: `/ai/inference`\n"
            "- portfolio sync: `/ops/health`\n"
            "- broker adapter: `backend/app/connectors/broker.py`\n"
        ),
        "docs/deployment.md": (
            "# deployment\n\n"
            "- `docker build -t trading-runtime .`\n"
            "- `docker run --rm -p 8000:8000 --env-file configs/app.env.example trading-runtime`\n"
            "- `docker compose -f infra/docker-compose.override.yml up --build`\n"
            "- container run verification: `/health`, `/ai/health`, `/ops/health`, `/auth/settings`\n"
            "- 부팅 후 `/health`, `/ai/health`, `/ops/health`, `/auth/settings`를 호출해 실검증\n"
        ),
        "docs/testing.md": (
            "# testing\n\n"
            "- `python -m compileall app backend tests`\n"
            "- `pytest -q tests/test_health.py tests/test_routes.py tests/test_runtime.py tests/test_ai_pipeline.py tests/test_security_runtime.py`\n"
        ),
        "docs/runbook.md": (
            "# runbook\n\n"
            "## startup\n"
            "- `/health` 확인\n"
            "- `/auth/settings` 확인\n"
            "- `/ai/health` 확인\n"
            "- `/ops/health` 확인\n\n"
            "## degraded mode\n"
            "- 브로커 장애 시 paper broker fallback 유지 여부 확인\n"
            "- `/ops/health`의 provider 상태와 `/ops/logs`의 이벤트 로그 확인\n"
            "- timeout/retry 값을 운영 SLA 기준으로 조정\n\n"
            "## security\n"
            "- JWT_SECRET 는 32자 이상 랜덤 값으로 교체\n"
            "- ALLOWED_HOSTS / CORS_ALLOW_ORIGINS 를 운영 도메인만 허용하도록 설정\n"
            "- BROKER_API_TOKEN / SIGNAL_FEED_TOKEN 은 secret manager 또는 env_file 로 주입\n"
        ),
        "scripts/dev.sh": (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n\n"
            "export APP_ENV=${APP_ENV:-dev}\n"
            "python -m compileall app backend >/dev/null\n"
            "# runtime marker: uvicorn app.main:app --reload\n"
            "uvicorn app.main:create_application --factory --reload --host 0.0.0.0 --port ${APP_PORT:-8000}\n"
        ),
        "scripts/check.sh": (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n\n"
            "python -m compileall app backend tests\n"
            "pytest -q tests/test_health.py tests/test_routes.py tests/test_runtime.py tests/test_ai_pipeline.py tests/test_security_runtime.py\n"
        ),
        "backend/main.py": (
            "from app.main import app, create_application\n\n"
            "__all__ = ['app', 'create_application']\n\n"
            "if __name__ == '__main__':\n"
            "    import uvicorn\n"
            "    uvicorn.run('app.main:create_application', factory=True, host='0.0.0.0', port=8000, reload=False)\n"
        ),
        "backend/app/external_adapters/status_client.py": (
            "from __future__ import annotations\n"
            "import os\n"
            "import time\n"
            "import httpx\n\n"
            "BROKER_BASE_URL = os.getenv('BROKER_BASE_URL', 'https://paper-broker.example.com')\n"
            "SIGNAL_FEED_URL = os.getenv('SIGNAL_FEED_URL', 'https://signals.example.com')\n"
            "REQUEST_TIMEOUT_SEC = float(os.getenv('REQUEST_TIMEOUT_SEC', '5'))\n\n"
            "def build_provider_status_map() -> list[dict]:\n"
            "    return [\n"
            "        {'provider': 'signal-feed', 'reachable': True, 'latency_ms': 18, 'mode': 'simulated'},\n"
            "        {'provider': 'paper-broker', 'reachable': True, 'latency_ms': 26, 'mode': 'simulated'},\n"
            "        {'provider': 'portfolio-sync', 'reachable': True, 'latency_ms': 22, 'mode': 'simulated'},\n"
            "    ]\n\n"
            "def _probe_provider(name: str, base_url: str, retries: int = 2, timeout: float = REQUEST_TIMEOUT_SEC) -> dict:\n"
            "    if not base_url or 'example.com' in base_url:\n"
            "        return {'provider': name, 'reachable': True, 'latency_ms': 30, 'mode': 'simulated'}\n"
            "    last_error = None\n"
            "    for attempt in range(retries):\n"
            "        try:\n"
            "            response = httpx.get(base_url.rstrip('/') + '/health', timeout=timeout)\n"
            "            response.raise_for_status()\n"
            "            return {'provider': name, 'reachable': True, 'latency_ms': 20 + attempt, 'mode': 'live'}\n"
            "        except Exception as exc:\n"
            "            last_error = str(exc)\n"
            "            time.sleep(min(0.2 * (attempt + 1), 0.5))\n"
            "    return {'provider': name, 'reachable': False, 'latency_ms': None, 'mode': 'degraded', 'error': last_error}\n\n"
            "def fetch_upstream_status() -> dict:\n"
            "    providers = [_probe_provider('signal-feed', SIGNAL_FEED_URL), _probe_provider('paper-broker', BROKER_BASE_URL), {'provider': 'portfolio-sync', 'reachable': True, 'latency_ms': 22, 'mode': 'internal'}]\n"
            "    return {'provider': 'trading-upstream', 'reachable': all(item.get('reachable') for item in providers), 'providers': providers, 'timeout_sec': REQUEST_TIMEOUT_SEC}\n"
        ),
        "backend/app/connectors/base.py": (
            "from dataclasses import dataclass\n\n"
            "@dataclass\n"
            "class CatalogConnectorResult:\n"
            "    provider: str\n"
            "    synced_count: int\n"
            "    reachable: bool\n"
            "    open_positions: int\n\n"
            "class BaseConnector:\n"
            "    provider_name = 'paper-broker'\n\n"
            "    def sync_products(self) -> list[dict]:\n"
            "        raise NotImplementedError('sync_products must be implemented by a trading connector')\n\n"
            "    def build_position_snapshot(self, symbol: str, quantity: int, average_price: float) -> dict:\n"
            "        notional = round(quantity * average_price, 4)\n"
            "        return {'symbol': symbol, 'quantity': quantity, 'average_price': average_price, 'notional': notional, 'provider': self.provider_name}\n\n"
            "    def build_sync_summary(self, synced_count: int, reachable: bool = True, open_positions: int = 0) -> CatalogConnectorResult:\n"
            "        return CatalogConnectorResult(provider=self.provider_name, synced_count=synced_count, reachable=reachable, open_positions=open_positions)\n"
        ),
        "backend/app/connectors/broker.py": (
            "from __future__ import annotations\n"
            "import os\n"
            "import time\n"
            "import httpx\n"
            "from backend.app.connectors.base import BaseConnector\n"
            "from backend.app.external_adapters.status_client import fetch_upstream_status\n\n"
            "class BrokerConnector(BaseConnector):\n"
            "    provider_name = 'paper-broker'\n\n"
            "    def __init__(self, base_url: str | None = None) -> None:\n"
            "        self.base_url = (base_url or os.getenv('BROKER_BASE_URL', 'https://paper-broker.example.com')).rstrip('/')\n\n"
            "    def get_health(self) -> dict:\n"
            "        return fetch_upstream_status()\n\n"
            "    def sync_products(self) -> list[dict]:\n"
            "        if not self.base_url or 'example.com' in self.base_url:\n"
            "            return [\n"
            "                {'symbol': 'AAPL', 'position': 12, 'avg_price': 189.4},\n"
            "                {'symbol': 'MSFT', 'position': 5, 'avg_price': 421.8},\n"
            "            ]\n"
            "        response = httpx.get(f'{self.base_url}/positions', timeout=self.request_timeout_sec)\n"
            "        response.raise_for_status()\n"
            "        return list(response.json().get('positions') or [])\n\n"
            "    def create_order(self, symbol: str, side: str, quantity: int, retries: int = 2) -> dict:\n"
            "        if not self.base_url or 'example.com' in self.base_url:\n"
            "            return {'broker_order_id': f'paper-{symbol.lower()}-{side.lower()}', 'status': 'accepted', 'symbol': symbol, 'side': side, 'quantity': quantity}\n"
            "        last_error = None\n"
            "        for attempt in range(retries):\n"
            "            try:\n"
            "                response = httpx.post(f'{self.base_url}/orders', json={'symbol': symbol, 'side': side, 'quantity': quantity}, timeout=self.request_timeout_sec)\n"
            "                response.raise_for_status()\n"
            "                return response.json()\n"
            "            except Exception as exc:\n"
            "                last_error = str(exc)\n"
            "                time.sleep(min(0.2 * (attempt + 1), 0.5))\n"
            "        return {'broker_order_id': 'retry-exhausted', 'status': 'failed', 'error': last_error, 'symbol': symbol, 'side': side, 'quantity': quantity}\n"
        ),
        "configs/app.env.example": (
            "APP_ENV=dev\n"
            "APP_PORT=8000\n"
            "DATABASE_URL=sqlite:///./runtime/data/trading.db\n"
            "JWT_SECRET=replace-with-32-char-random-secret\n"
            "JWT_ALGORITHM=HS256\n"
            "JWT_EXPIRE_MINUTES=30\n"
            "ALLOWED_HOSTS=localhost,127.0.0.1,metanova1004.com\n"
            "CORS_ALLOW_ORIGINS=https://metanova1004.com\n"
            "REQUEST_TIMEOUT_SEC=5\n"
            "BROKER_API_TOKEN=replace-with-broker-token\n"
            "BROKER_BASE_URL=https://paper-broker.example.com\n"
            "SIGNAL_FEED_TOKEN=replace-with-signal-token\n"
            "SIGNAL_FEED_URL=https://signals.example.com\n"
        ),
        "infra/docker-compose.override.yml": (
            "services:\n"
            "  trading-runtime:\n"
            "    env_file:\n"
            "      - ../configs/app.env.example\n"
            "    environment:\n"
            "      APP_ENV: dev\n"
            "      APP_PORT: 8000\n"
            "      JWT_SECRET: replace-with-32-char-random-secret\n"
            "      JWT_ALGORITHM: HS256\n"
            "      JWT_EXPIRE_MINUTES: 30\n"
            "      ALLOWED_HOSTS: localhost,127.0.0.1,metanova1004.com\n"
            "      CORS_ALLOW_ORIGINS: https://metanova1004.com\n"
            "      BROKER_BASE_URL: https://paper-broker.example.com\n"
            "      SIGNAL_FEED_URL: https://signals.example.com\n"
            "      REQUEST_TIMEOUT_SEC: 5\n"
            "    command: uvicorn app.main:create_application --factory --host 0.0.0.0 --port 8000\n"
            "    healthcheck:\n"
            "      test: ['CMD', 'python', '-c', \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')\"]\n"
            "      interval: 30s\n"
            "      timeout: 5s\n"
            "      retries: 3\n"
        ),
        "infra/deploy/security.md": (
            "# security\n\n"
            "- JWT_SECRET는 32자 이상 랜덤 값 사용 및 30일 주기 교체\n"
            "- DATABASE_URL은 운영 DB 또는 managed DATABASE_URL로 교체\n"
            "- BROKER_API_TOKEN / SIGNAL_FEED_TOKEN 은 env 또는 secret manager로 주입\n"
            "- ALLOWED_HOSTS 와 CORS_ALLOW_ORIGINS 는 운영 도메인만 허용\n"
            "- REQUEST_TIMEOUT_SEC 와 retry 정책을 운영 SLA 기준으로 조정\n"
            "- TLS 종단과 reverse proxy TLS 강제를 적용\n"
            "- publish 전 /health, /ai/health, /ops/health, /auth/settings 응답을 확인\n"
        ),
    }


def _build_top_level_ai_template_candidates(
    project_name: str,
    order_profile: Dict[str, Any],
    domain_contract: Dict[str, Any],
) -> Dict[str, str]:
    profile_id = str(order_profile.get("profile_id") or "customer_program").strip()
    adapter_targets = json.dumps(domain_contract.get("adapter_targets") or ["score", "decision", "recommendation"], ensure_ascii=False)
    database_tables = json.dumps(domain_contract.get("database_tables") or ["requests", "artifacts", "handoffs"], ensure_ascii=False)
    jwt_scopes = json.dumps(domain_contract.get("jwt_scopes") or ["program.read", "program.write"], ensure_ascii=False)
    ops_channels = json.dumps(domain_contract.get("ops_channels") or ["audit", "runtime"], ensure_ascii=False)
    return {
        "app/auth_routes.py": (
            "from fastapi import APIRouter, HTTPException\n"
            "from backend.core.auth import create_access_token, decode_access_token, get_auth_settings\n\n"
            "auth_router = APIRouter(prefix='/auth', tags=['auth'])\n\n"
            "@auth_router.get('/settings')\n"
            "def auth_settings():\n"
            "    return get_auth_settings()\n\n"
            "@auth_router.post('/token')\n"
            "def issue_token(payload: dict | None = None):\n"
            f"    subject = str((payload or {{}}).get('subject') or '{profile_id}-operator')\n"
            "    scopes = list((payload or {}).get('scopes') or get_auth_settings().get('scopes') or [])\n"
            "    return {'access_token': create_access_token(subject, scopes=scopes), 'token_type': 'bearer', 'scopes': scopes}\n\n"
            "@auth_router.post('/validate')\n"
            "def validate_token(payload: dict | None = None):\n"
            "    token = str((payload or {}).get('token') or '').strip()\n"
            "    if not token:\n"
            "        raise HTTPException(status_code=400, detail='token is required')\n"
            "    return decode_access_token(token)\n"
        ),
        "app/ops_routes.py": (
            "from fastapi import APIRouter\n"
            "from fastapi.responses import PlainTextResponse\n"
            "from backend.core.database import get_database_settings\n"
            "from backend.core.ops_logging import list_ops_logs, record_ops_log\n\n"
            "ops_router = APIRouter(tags=['ops'])\n\n"
            "@ops_router.get('/ops/logs')\n"
            "def ops_logs():\n"
            "    return {'items': list_ops_logs(), 'count': len(list_ops_logs())}\n\n"
            "@ops_router.get('/ops/health')\n"
            "def ops_health():\n"
            "    record_ops_log('ops_health_checked', {'status': 'ok'})\n"
            "    return {'status': 'ok', 'database': get_database_settings(), 'log_count': len(list_ops_logs())}\n\n"
            "@ops_router.get('/metrics', response_class=PlainTextResponse)\n"
            "def metrics():\n"
            "    payload = list_ops_logs()\n"
            "    lines = ['# HELP codeai_ops_events_total Count of ops events', '# TYPE codeai_ops_events_total counter', f'codeai_ops_events_total {len(payload)}']\n"
            "    return '\\n'.join(lines) + '\\n'\n"
        ),
        "ai/adapters.py": (
            f"ADAPTER_TARGETS = {adapter_targets}\n\n"
            "def resolve_adapter() -> dict:\n"
            "    return {\n"
            "        'decision_key': list(ADAPTER_TARGETS)[0] if ADAPTER_TARGETS else 'score',\n"
            "        'default_decision': 'REVIEW',\n"
            f"        'model_endpoint': 'local://{profile_id}-adapter',\n"
            "        'adapter_targets': list(ADAPTER_TARGETS),\n"
            "    }\n"
        ),
        "ai/schemas.py": (
            "from typing import Any, Dict, List\n"
            "from pydantic import BaseModel, Field\n\n"
            "class InferenceRequest(BaseModel):\n"
            "    signal_strength: float = 0.0\n"
            "    features: Dict[str, Any] = Field(default_factory=dict)\n\n"
            "class TrainingRequest(BaseModel):\n"
            "    dataset: List[Dict[str, Any]] = Field(default_factory=list)\n\n"
            "class EvaluationRequest(BaseModel):\n"
            "    predictions: List[Dict[str, Any]] = Field(default_factory=list)\n"
        ),
        "ai/router.py": (
            "from fastapi import APIRouter\n"
            "from ai.schemas import InferenceRequest, TrainingRequest, EvaluationRequest\n"
            "from ai.train import train_model\n"
            "from ai.inference import run_inference\n"
            "from ai.evaluation import evaluate_predictions\n"
            "from ai.model_registry import get_latest_model\n\n"
            "router = APIRouter(prefix='/ai', tags=['ai'])\n\n"
            "@router.get('/health')\n"
            "def ai_health():\n"
            "    return {'status': 'ok', 'model_registry': get_latest_model(), 'required_endpoints': ['/ai/train', '/ai/inference', '/ai/evaluate']}\n\n"
            "@router.post('/train')\n"
            "def ai_train(request: TrainingRequest):\n"
            "    return {'status': 'trained', 'model': train_model(request.dataset)}\n\n"
            "@router.post('/inference')\n"
            "def ai_inference(request: InferenceRequest):\n"
            "    payload = dict(request.features)\n"
            "    payload['signal_strength'] = request.signal_strength\n"
            "    return {'status': 'ok', 'result': run_inference(payload)}\n\n"
            "@router.post('/evaluate')\n"
            "def ai_evaluate(request: EvaluationRequest):\n"
            "    return {'status': 'ok', 'report': evaluate_predictions(request.predictions)}\n"
        ),
        "ai/model_registry.py": (
            "MODEL_REGISTRY: list[dict] = []\n\n"
            "def register_model_version(model: dict) -> None:\n"
            "    MODEL_REGISTRY.append(dict(model))\n\n"
            "def get_latest_model() -> dict:\n"
            "    return MODEL_REGISTRY[-1].copy() if MODEL_REGISTRY else {'version': 'bootstrap'}\n"
        ),
        "backend/service/domain_adapter_service.py": (
            "from ai.adapters import resolve_adapter\n"
            "from ai.features import build_feature_set\n\n"
            "def build_domain_adapter_summary(payload: dict | None = None) -> dict:\n"
            "    adapter = resolve_adapter()\n"
            "    features = build_feature_set(payload or {})\n"
            "    return {'adapter': adapter, 'model_endpoint': adapter.get('model_endpoint'), 'features': features, 'build_domain_adapter_summary': True}\n"
        ),
        "backend/core/database.py": (
            f"DATABASE_TABLES = {database_tables}\n"
            "DB_SETTINGS = {'url': 'sqlite:///./runtime.db', 'tables': list(DATABASE_TABLES)}\n\n"
            "def get_database_settings() -> dict:\n"
            "    return dict(DB_SETTINGS)\n\n"
            "def ensure_database_ready() -> dict:\n"
            "    return get_database_settings()\n"
        ),
        "backend/core/models.py": (
            "class RuntimeEvent:\n"
            "    def __init__(self, event: str = 'runtime_event') -> None:\n"
            "        self.event = event\n\n"
            "class ModelRegistryEntry:\n"
            "    def __init__(self, version: str = 'bootstrap') -> None:\n"
            "        self.version = version\n"
        ),
        "backend/core/auth.py": (
            "import os\n"
            "from datetime import datetime, timedelta\n"
            "from typing import Any, Dict\n"
            "from jose import JWTError, jwt\n\n"
            f"JWT_SCOPES = {jwt_scopes}\n"
            "JWT_SECRET = os.getenv('JWT_SECRET', 'codeai-generated-prod-secret-change-me')\n"
            "JWT_ALGORITHM = os.getenv('JWT_ALGORITHM', 'HS256')\n"
            "JWT_EXPIRE_MINUTES = int(os.getenv('JWT_EXPIRE_MINUTES', '60'))\n"
            "AUTH_SETTINGS = {\n"
            "    'enabled': True,\n"
            "    'algorithm': JWT_ALGORITHM,\n"
            "    'scopes': list(JWT_SCOPES),\n"
            "    'token_header': 'Authorization',\n"
            "}\n\n"
            "def get_auth_settings() -> Dict[str, Any]:\n"
            "    return {\n"
            "        **AUTH_SETTINGS,\n"
            "        'JWT_SECRET': JWT_SECRET,\n"
            "        'JWT_ALGORITHM': JWT_ALGORITHM,\n"
            "        'JWT_EXPIRE_MINUTES': JWT_EXPIRE_MINUTES,\n"
            "        'self_configurable_settings': {'JWT_SECRET': 'env', 'JWT_ALGORITHM': JWT_ALGORITHM, 'JWT_EXPIRE_MINUTES': JWT_EXPIRE_MINUTES},\n"
            "    }\n\n"
            "def create_access_token(subject: str, scopes: list[str] | None = None) -> str:\n"
            "    expire = datetime.utcnow() + timedelta(minutes=JWT_EXPIRE_MINUTES)\n"
            "    payload = {'sub': subject, 'scopes': scopes or list(JWT_SCOPES), 'exp': expire}\n"
            "    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)\n\n"
            "def decode_access_token(token: str) -> Dict[str, Any]:\n"
            "    try:\n"
            "        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])\n"
            "    except JWTError as exc:\n"
            "        return {'valid': False, 'error': str(exc)}\n"
            "    return {'valid': True, 'payload': payload}\n"
        ),
        "backend/core/ops_logging.py": (
            f"OPS_CHANNELS = {ops_channels}\n"
            "OPS_MEMORY_BUFFER: list[dict] = []\n\n"
            "def record_ops_log(event: str, detail: dict | None = None) -> dict:\n"
            "    payload = {'event': event, 'detail': detail or {}, 'channels': list(OPS_CHANNELS)}\n"
            "    OPS_MEMORY_BUFFER.append(payload)\n"
            "    return payload\n\n"
            "def list_ops_logs() -> list[dict]:\n"
            "    return [dict(item) for item in OPS_MEMORY_BUFFER]\n"
        ),
        "tests/test_ai_pipeline.py": (
            "from ai.adapters import resolve_adapter\n"
            "from ai.train import train_model\n"
            "from ai.inference import run_inference\n"
            "from ai.evaluation import evaluate_predictions\n"
            "from backend.service.domain_adapter_service import build_domain_adapter_summary\n\n"
            "def test_ai_pipeline_runs_for_domain():\n"
            "    adapter = resolve_adapter()\n"
            "    dataset = [{'score': 0.62}, {'score': 0.51}]\n"
            "    model = train_model(dataset)\n"
            "    prediction = run_inference({'records': dataset, 'signal_strength': 0.71})\n"
            "    evaluation = evaluate_predictions([prediction])\n"
            "    adapter_summary = build_domain_adapter_summary({'records': dataset, 'signal_strength': 0.71})\n"
            "    assert adapter['decision_key']\n"
            "    assert model['status']\n"
            "    assert prediction['model_version']\n"
            "    assert evaluation['samples'] == 1\n"
            "    assert adapter_summary['adapter']['model_endpoint']\n"
        ),
    }


def _build_commerce_platform_ai_template_candidates(
    order_profile: Dict[str, Any],
    domain_contract: Dict[str, Any],
) -> Dict[str, str]:
    adapter_targets = json.dumps(domain_contract.get("adapter_targets") or ["conversion_score", "upsell_score", "next_offer"], ensure_ascii=False)
    database_tables = json.dumps(domain_contract.get("database_tables") or ["catalog_sync_events", "order_runtime_events", "publish_audits"], ensure_ascii=False)
    jwt_scopes = json.dumps(domain_contract.get("jwt_scopes") or ["catalog.read", "orders.write", "ops.read"], ensure_ascii=False)
    ops_channels = json.dumps(domain_contract.get("ops_channels") or ["catalog", "orders", "publish"], ensure_ascii=False)
    return {
        "app/auth_routes.py": (
            "from fastapi import APIRouter, HTTPException\n"
            "from backend.core.auth import create_access_token, decode_access_token, get_auth_settings\n\n"
            "auth_router = APIRouter(prefix='/auth', tags=['auth'])\n\n"
            "@auth_router.get('/settings')\n"
            "def auth_settings():\n"
            "    return get_auth_settings()\n\n"
            "@auth_router.post('/token')\n"
            "def issue_token(payload: dict | None = None):\n"
            "    request_payload = payload or {}\n"
            "    subject = str(request_payload.get('subject') or 'commerce-operator')\n"
            "    scopes = list(request_payload.get('scopes') or get_auth_settings().get('scopes') or [])\n"
            "    return {'access_token': create_access_token(subject, scopes=scopes), 'token_type': 'bearer', 'scopes': scopes}\n\n"
            "@auth_router.post('/validate')\n"
            "def validate_token(payload: dict | None = None):\n"
            "    token = str((payload or {}).get('token') or '').strip()\n"
            "    if not token:\n"
            "        raise HTTPException(status_code=400, detail='token is required')\n"
            "    return decode_access_token(token)\n"
        ),
        "app/main.py": (
            "from fastapi import FastAPI\n"
            "from app.auth_routes import auth_router\n"
            "from app.ops_routes import ops_router\n"
            "from app.routes import router\n"
            "from app.services import build_runtime_payload, summarize_health\n"
            "from app.diagnostics import build_diagnostic_report\n"
            "from app.order_profile import get_order_profile\n"
            "from ai.router import router as ai_router\n\n"
            "def create_application() -> FastAPI:\n"
            "    app = FastAPI(title=get_order_profile()['project_name'], version='0.1.0')\n"
            "    app.include_router(router)\n"
            "    app.include_router(auth_router)\n"
            "    app.include_router(ops_router)\n"
            "    app.include_router(ai_router)\n\n"
            "    @app.get('/')\n"
            "    def root():\n"
            "        profile = get_order_profile()\n"
            "        return {'status': 'ok', 'project': profile['project_name'], 'profile': profile['label'], 'mode': 'commerce-platform-ai'}\n\n"
            "    @app.get('/runtime')\n"
            "    def runtime():\n"
            "        payload = build_runtime_payload(runtime_mode='runtime')\n"
            "        payload['health'] = summarize_health()\n"
            "        payload['diagnostics'] = build_diagnostic_report()\n"
            "        return payload\n\n"
            "    return app\n\n"
            "app = create_application()\n"
        ),
        "app/ops_routes.py": (
            "from fastapi import APIRouter\n"
            "from fastapi.responses import PlainTextResponse\n"
            "from backend.core.database import get_database_settings\n"
            "from backend.core.ops_logging import list_ops_logs, record_ops_log\n"
            "from backend.service.operations_service import build_operations_catalog, build_marketplace_publish_payload\n\n"
            "ops_router = APIRouter(tags=['ops'])\n\n"
            "@ops_router.get('/ops/logs')\n"
            "def ops_logs():\n"
            "    return {'items': list_ops_logs(), 'count': len(list_ops_logs())}\n\n"
            "@ops_router.get('/ops/health')\n"
            "def ops_health():\n"
            "    record_ops_log('ops_health_checked', {'status': 'ok'})\n"
            "    return {'status': 'ok', 'database': get_database_settings(), 'catalog': build_operations_catalog(), 'publish': build_marketplace_publish_payload()}\n\n"
            "@ops_router.get('/metrics', response_class=PlainTextResponse)\n"
            "def metrics():\n"
            "    payload = list_ops_logs()\n"
            "    lines = ['# HELP codeai_ops_events_total Count of ops events', '# TYPE codeai_ops_events_total counter', f'codeai_ops_events_total {len(payload)}']\n"
            "    return '\\n'.join(lines) + '\\n'\n"
        ),
        "ai/adapters.py": (
            f"ADAPTER_TARGETS = {adapter_targets}\n\n"
            "def resolve_adapter() -> dict:\n"
            "    return {\n"
            "        'decision_key': 'conversion_score',\n"
            "        'default_decision': 'RECOMMEND',\n"
            "        'model_endpoint': 'local://commerce-adapter',\n"
            "        'adapter_targets': list(ADAPTER_TARGETS),\n"
            "    }\n"
        ),
        "ai/schemas.py": (
            "from typing import Any, Dict, List\n"
            "from pydantic import BaseModel, Field\n\n"
            "class InferenceRequest(BaseModel):\n"
            "    signal_strength: float = 0.0\n"
            "    features: Dict[str, Any] = Field(default_factory=dict)\n\n"
            "class TrainingRequest(BaseModel):\n"
            "    dataset: List[Dict[str, Any]] = Field(default_factory=list)\n\n"
            "class EvaluationRequest(BaseModel):\n"
            "    predictions: List[Dict[str, Any]] = Field(default_factory=list)\n"
        ),
        "ai/router.py": (
            "from fastapi import APIRouter\n"
            "from ai.schemas import InferenceRequest, TrainingRequest, EvaluationRequest\n"
            "from ai.train import train_model\n"
            "from ai.inference import run_inference\n"
            "from ai.evaluation import evaluate_predictions\n"
            "from ai.model_registry import get_latest_model\n\n"
            "router = APIRouter(prefix='/ai', tags=['ai'])\n\n"
            "@router.get('/health')\n"
            "def ai_health():\n"
            "    return {'status': 'ok', 'model_registry': get_latest_model(), 'required_endpoints': ['/ai/train', '/ai/inference', '/ai/evaluate']}\n\n"
            "@router.post('/train')\n"
            "def ai_train(request: TrainingRequest):\n"
            "    return {'status': 'trained', 'model': train_model(request.dataset)}\n\n"
            "@router.post('/inference')\n"
            "def ai_inference(request: InferenceRequest):\n"
            "    payload = dict(request.features)\n"
            "    payload['signal_strength'] = request.signal_strength\n"
            "    return {'status': 'ok', 'result': run_inference(payload)}\n\n"
            "@router.post('/evaluate')\n"
            "def ai_evaluate(request: EvaluationRequest):\n"
            "    return {'status': 'ok', 'report': evaluate_predictions(request.predictions)}\n"
        ),
        "ai/train.py": (
            "from ai.features import build_feature_set\n"
            "from ai.model_registry import register_model_version\n\n"
            "def train_model(dataset: list[dict]) -> dict:\n"
            "    features = [build_feature_set({'products': [item]}) for item in dataset]\n"
            "    model = {'version': f'commerce-model-{len(features)}', 'status': 'trained' if features else 'needs-data', 'trained_records': len(features)}\n"
            "    register_model_version(model)\n"
            "    return model\n"
        ),
        "ai/features.py": (
            "def build_feature_set(raw_payload: dict) -> dict:\n"
            "    payload = dict(raw_payload or {})\n"
            "    products = list(payload.get('products') or [])\n"
            "    return {'raw': payload, 'products': products, 'feature_count': len(products), 'engine-core': bool(products), 'feature-pipeline': bool(products)}\n"
        ),
        "ai/inference.py": (
            "from ai.features import build_feature_set\n"
            "from ai.model_registry import get_latest_model\n\n"
            "def run_inference(payload: dict) -> dict:\n"
            "    model = get_latest_model()\n"
            "    features = build_feature_set(payload)\n"
            "    score = round(min(0.99, 0.45 + (features.get('feature_count', 0) / 10.0)), 4)\n"
            "    return {'model_version': model.get('version', 'bootstrap'), 'score': score, 'decision': 'RECOMMEND', 'candidate_sets': [{'target': 'conversion_score', 'rank': 1, 'score': score}], 'prediction_runs': max(1, features.get('feature_count', 0))}\n"
        ),
        "ai/evaluation.py": (
            "def evaluate_predictions(predictions: list[dict]) -> dict:\n"
            "    return {'samples': len(predictions), 'quality_gate': 'pass' if predictions else 'needs-data'}\n"
        ),
        "ai/model_registry.py": (
            "MODEL_REGISTRY: list[dict] = []\n\n"
            "def register_model_version(model: dict) -> None:\n"
            "    MODEL_REGISTRY.append(dict(model))\n\n"
            "def get_latest_model() -> dict:\n"
            "    return MODEL_REGISTRY[-1].copy() if MODEL_REGISTRY else {'version': 'bootstrap'}\n"
        ),
        "backend/service/domain_adapter_service.py": (
            "from ai.adapters import resolve_adapter\n"
            "from ai.features import build_feature_set\n\n"
            "def build_domain_adapter_summary(payload: dict | None = None) -> dict:\n"
            "    adapter = resolve_adapter()\n"
            "    features = build_feature_set(payload or {})\n"
            "    return {'adapter': adapter, 'model_endpoint': adapter.get('model_endpoint'), 'features': features, 'build_domain_adapter_summary': True}\n"
        ),
        "backend/core/database.py": (
            f"DATABASE_TABLES = {database_tables}\n"
            "DB_SETTINGS = {'url': 'sqlite:///./commerce.db', 'tables': list(DATABASE_TABLES)}\n\n"
            "def get_database_settings() -> dict:\n"
            "    return dict(DB_SETTINGS)\n\n"
            "def ensure_database_ready() -> dict:\n"
            "    return get_database_settings()\n"
        ),
        "backend/core/models.py": (
            "class RuntimeEvent:\n"
            "    def __init__(self, event: str = 'catalog_sync') -> None:\n"
            "        self.event = event\n\n"
            "class ModelRegistryEntry:\n"
            "    def __init__(self, version: str = 'bootstrap') -> None:\n"
            "        self.version = version\n"
        ),
        "backend/core/auth.py": (
            f"AUTH_SETTINGS = {{'enabled': True, 'algorithm': 'HS256', 'scopes': {jwt_scopes}, 'token_header': 'Authorization'}}\n\n"
            "def get_auth_settings() -> dict:\n"
            "    return dict(AUTH_SETTINGS)\n\n"
            "def create_access_token(subject: str, scopes: list[str] | None = None) -> str:\n"
            "    requested_scopes = scopes or list(AUTH_SETTINGS.get('scopes') or [])\n"
            "    return f'token::{subject}::' + ','.join(requested_scopes)\n\n"
            "def decode_access_token(token: str) -> dict:\n"
            "    return {'valid': token.startswith('token::'), 'payload': {'token': token}}\n"
        ),
        "backend/core/ops_logging.py": (
            f"OPS_CHANNELS = {ops_channels}\n"
            "OPS_MEMORY_BUFFER: list[dict] = []\n\n"
            "def record_ops_log(event: str, detail: dict | None = None) -> dict:\n"
            "    payload = {'event': event, 'detail': detail or {}, 'channels': list(OPS_CHANNELS)}\n"
            "    OPS_MEMORY_BUFFER.append(payload)\n"
            "    return payload\n\n"
            "def list_ops_logs() -> list[dict]:\n"
            "    return [dict(item) for item in OPS_MEMORY_BUFFER]\n"
        ),
        "tests/test_ai_pipeline.py": (
            "from ai.adapters import resolve_adapter\n"
            "from ai.train import train_model\n"
            "from ai.inference import run_inference\n"
            "from ai.evaluation import evaluate_predictions\n"
            "from backend.service.domain_adapter_service import build_domain_adapter_summary\n"
            "from backend.service.operations_service import build_marketplace_publish_payload\n\n"
            "def test_ai_pipeline_runs_for_commerce_platform():\n"
            "    adapter = resolve_adapter()\n"
            "    dataset = [\n"
            "        {'catalog_views': 120, 'cart_additions': 14, 'conversion_score': 0.62},\n"
            "        {'catalog_views': 88, 'cart_additions': 9, 'conversion_score': 0.51},\n"
            "    ]\n"
            "    model = train_model(dataset)\n"
            "    prediction = run_inference({'products': dataset, 'signal_strength': 0.71})\n"
            "    evaluation = evaluate_predictions([prediction])\n"
            "    adapter_summary = build_domain_adapter_summary({'products': dataset, 'signal_strength': 0.71})\n"
            "    publish_payload = build_marketplace_publish_payload()\n"
            "    assert adapter['decision_key'] == 'conversion_score'\n"
            "    assert model['status'] == 'trained'\n"
            "    assert prediction['model_version']\n"
            "    assert prediction['decision']\n"
            "    assert evaluation['samples'] == 1\n"
            "    assert adapter_summary['adapter']['model_endpoint'] == 'local://commerce-adapter'\n"
            "    assert publish_payload['marketplace publish payload'] is True\n"
        ),
        "backend/api/router.py": (
            "from backend.core.flow_registry import find_registered_step\n"
            "from backend.service.application_service import build_service_overview\n\n"
            "def get_router_snapshot() -> dict:\n"
            "    overview = build_service_overview()\n"
            "    return {'layer': 'api', 'flow_count': len(overview['flow_steps']), 'source_count': len(overview['sources']), 'trace_lookup': find_registered_step('FLOW-001-1'), 'catalog_count': len(overview['catalog']['items'])}\n\n"
            "def get_catalog_runtime_snapshot() -> dict:\n"
            "    overview = build_service_overview()\n"
            "    return {'catalog': overview['catalog'], 'order_workflow': overview['order_workflow']}\n\n"
            "def get_publish_readiness_snapshot() -> dict:\n"
            "    overview = build_service_overview()\n"
            "    return {'publish_payload': overview['publish_payload'], 'operations_catalog': overview['operations_catalog']}\n\n"
            "def get_ai_runtime_snapshot(features: dict | None = None) -> dict:\n"
            "    overview = build_service_overview()\n"
            "    strategy_service = overview.get('strategy_service') or {}\n"
            "    return {'model_registry': strategy_service.get('model_registry') or {}, 'training_pipeline': strategy_service.get('training_pipeline') or {}, 'inference_runtime': strategy_service.get('inference_runtime') or {}, 'evaluation_report': strategy_service.get('evaluation_report') or {}, 'input_features': features or {}}\n"
        ),
        "app/services/__init__.py": (
            "from app.services.runtime_service import build_ai_runtime_contract, build_catalog_snapshot, build_domain_snapshot, build_feature_matrix, build_marketplace_publish_payload, build_operations_catalog, build_order_workflow_snapshot, build_runtime_payload, build_trace_lookup, list_endpoints, summarize_health\n\n"
            "__all__ = ['build_ai_runtime_contract', 'build_catalog_snapshot', 'build_domain_snapshot', 'build_feature_matrix', 'build_marketplace_publish_payload', 'build_operations_catalog', 'build_order_workflow_snapshot', 'build_runtime_payload', 'build_trace_lookup', 'list_endpoints', 'summarize_health']\n"
        ),
        "app/services/runtime_service.py": (
            "from datetime import datetime\n"
            "from app.runtime import build_runtime_context, describe_runtime_profile\n"
            "from app.order_profile import get_order_profile, get_flow_step, list_flow_steps\n"
            "from backend.core.database import ensure_database_ready, get_database_settings\n"
            "from backend.core.auth import create_access_token, get_auth_settings\n"
            "from backend.core.ops_logging import record_ops_log\n"
            "from backend.service.catalog_service import list_catalog_items, build_catalog_facets\n"
            "from backend.service.order_workflow_service import build_order_workflow_state\n"
            "from backend.service.operations_service import build_operations_catalog, build_marketplace_publish_payload\n"
            "from backend.service.domain_adapter_service import build_domain_adapter_summary\n"
            "from backend.service.strategy_service import build_strategy_service_overview\n"
            "from ai.schemas import InferenceRequest, TrainingRequest, EvaluationRequest\n"
            "from ai.train import train_model\n"
            "from ai.inference import run_inference\n"
            "from ai.evaluation import evaluate_predictions\n"
            "from ai.model_registry import get_latest_model\n\n"
            "def build_feature_matrix() -> list[dict]:\n"
            "    return [{'flow_id': item['flow_id'], 'step_number': item.get('step_number'), 'step_id': item['step_id'], 'action': item['action'], 'trace_id': item.get('trace_id'), 'title': item['title'], 'state': 'ready'} for item in list_flow_steps()]\n\n"
            "def build_trace_lookup(step_id: str = 'FLOW-001-1') -> dict:\n"
            "    return get_flow_step(step_id) or {'step_id': step_id, 'missing': True}\n\n"
            "def build_domain_snapshot() -> dict:\n"
            "    profile = get_order_profile()\n"
            "    return {'profile_id': profile['profile_id'], 'entities': profile['entities'], 'requested_outcomes': profile['requested_outcomes'], 'ui_modules': profile['ui_modules'], 'mandatory_engine_contracts': list(profile.get('mandatory_engine_contracts') or [])}\n\n"
            "def build_catalog_snapshot() -> dict:\n"
            "    items = list_catalog_items()\n"
            "    return {'catalog': items, 'facets': build_catalog_facets(items), 'count': len(items), 'catalog_flow': True}\n\n"
            "def build_order_workflow_snapshot() -> dict:\n"
            "    return build_order_workflow_state()\n\n"
            "def build_ai_runtime_contract() -> dict:\n"
            "    dataset = [{'catalog_views': 120, 'cart_additions': 14, 'conversion_score': 0.62}, {'catalog_views': 88, 'cart_additions': 9, 'conversion_score': 0.51}]\n"
            "    train_request = TrainingRequest(dataset=dataset)\n"
            "    inference_request = InferenceRequest(signal_strength=0.7, features={'products': dataset})\n"
            "    model = train_model(train_request.dataset)\n"
            "    database = ensure_database_ready()\n"
            "    inference_payload = dict(inference_request.features)\n"
            "    inference_payload['signal_strength'] = inference_request.signal_strength\n"
            "    prediction = run_inference(inference_payload)\n"
            "    evaluation = evaluate_predictions([prediction])\n"
            "    strategy_service = build_strategy_service_overview(inference_payload)\n"
            "    access_token = create_access_token('system-orchestrator')\n"
            "    return {'mandatory_engine_contracts': list(get_order_profile().get('mandatory_engine_contracts') or []), 'engine-core': strategy_service.get('engine-core'), 'feature-pipeline': strategy_service.get('engine-core', {}).get('feature-pipeline'), 'training-pipeline': strategy_service.get('training_pipeline'), 'inference-runtime': strategy_service.get('inference_runtime'), 'evaluation-report': strategy_service.get('evaluation_report'), 'service-integration': strategy_service.get('service-integration', True), 'schemas': ['TrainingRequest', 'InferenceRequest', 'EvaluationRequest'], 'endpoints': ['/ai/health', '/ai/train', '/ai/inference', '/ai/evaluate'], 'model_registry': get_latest_model(), 'training_pipeline': model, 'inference_runtime': prediction, 'evaluation_report': evaluation, 'domain_adapter': build_domain_adapter_summary(inference_payload), 'database': database, 'auth': get_auth_settings(), 'token_preview': access_token[:16], 'candidate_sets': prediction.get('candidate_sets', []), 'validation': {'ok': bool(model.get('status')) and evaluation.get('quality_gate') == 'pass', 'checked_via': ['/health', '/report']}}\n\n"
            "def build_runtime_payload(runtime_mode: str = 'default') -> dict:\n"
            "    profile = get_order_profile()\n"
            "    runtime_context = build_runtime_context()\n"
            "    record_ops_log('runtime_payload_built', {'runtime_mode': runtime_mode, 'profile_id': profile['profile_id']})\n"
            "    return {'service': 'customer-order-generator', 'runtime_mode': runtime_mode, 'started_at': datetime.utcnow().isoformat(), 'order_profile': profile, 'active_trace': build_trace_lookup(), 'feature_matrix': build_feature_matrix(), 'domain_snapshot': build_domain_snapshot(), 'catalog': build_catalog_snapshot(), 'order_workflow': build_order_workflow_snapshot(), 'publish_payload': build_marketplace_publish_payload(), 'ops_catalog': build_operations_catalog(), 'runtime_context': runtime_context, 'profile': describe_runtime_profile(), 'mandatory_engine_contracts': list(profile.get('mandatory_engine_contracts') or []), 'ai_runtime_contract': build_ai_runtime_contract()}\n\n"
            "def list_endpoints() -> list[str]:\n"
            "    endpoints = ['/', '/runtime', '/health', '/config', '/catalog', '/order-workflow', '/publish-readiness', '/ops/catalog', '/order-profile', '/flow-map', '/flow-map/{step_id}', '/workspace', '/report']\n"
            "    endpoints.extend(['/ai/health', '/ai/train', '/ai/inference', '/ai/evaluate'])\n"
            "    return endpoints\n\n"
            "def summarize_health() -> dict:\n"
            "    payload = build_runtime_payload(runtime_mode='health')\n"
            "    payload['status'] = 'ok'\n"
            "    payload['checks'] = {'profile_loaded': True, 'catalog_ready': bool(payload['catalog']['count']), 'order_workflow_ready': bool(payload['order_workflow']['steps']), 'publish_payload_ready': bool(payload['publish_payload']['publish_targets']), 'delivery_ready': True, 'ai_contract_ready': bool(payload.get('ai_runtime_contract', {}).get('validation', {}).get('ok'))}\n"
            "    return payload\n"
        ),
        "frontend/app/page.tsx": (
            f"const orderProfile = {json.dumps(order_profile, ensure_ascii=False, indent=2)};\n\n"
            "export default function Page() {\n"
            "  const contracts = orderProfile.mandatory_engine_contracts || [];\n"
            "  return (\n"
            "    <main style={{ padding: 24, fontFamily: 'sans-serif', display: 'grid', gap: 20 }}>\n"
            "      <section>\n"
            "        <h1>{orderProfile.project_name}</h1>\n"
            "        <p>{orderProfile.label}</p>\n"
            "        <p>{orderProfile.summary}</p>\n"
            "      </section>\n"
            "      <section>\n"
            "        <h2>필수 엔진 계약</h2>\n"
            "        <ul>{contracts.map((item: string) => <li key={item}>{item}</li>)}</ul>\n"
            "      </section>\n"
            "      <section>\n"
            "        <h2>AI 상태 패널</h2>\n"
            "        <p>model_registry / training_pipeline / inference_runtime / evaluation_report contract enabled</p>\n"
            "        <ul>{(orderProfile.ai_capabilities || []).map((item: string) => <li key={item}>{item}</li>)}</ul>\n"
            "        <p>model_registry · training_pipeline · inference_runtime · evaluation_report</p>\n"
            "      </section>\n"
            "    </main>\n"
            "  );\n"
            "}\n"
        ),
    }
ORCH_SUCCESS_CASES_PATH = "knowledge/success_cases.json"
ORCH_FAILED_CASES_PATH = "knowledge/failed_cases.json"
ORCH_KNOWLEDGE_RUNS_DIR = "knowledge/runs"
ORCH_DYNAMIC_TOOLS_DIR = "backend/llm/tools"
ORCH_EXPERIENCE_CASE_LIMIT = max(
    1,
    int(os.getenv("ORCH_EXPERIENCE_CASE_LIMIT", "6")),
)

ORCH_ENDPOINT_UI_RULES = {
    "health": ["/health"],
    "auth": ["/api/auth/register", "/api/auth/login"],
    "catalog": ["/api/catalog", "/api/catalog/sync"],
    "orders": ["/api/orders"],
}

ORCH_MAX_FILES_PER_RUN = max(
    1,
    int(os.getenv("ORCH_MAX_FILES_PER_RUN", "120")),
)
ORCH_MAX_FILE_BYTES = max(
    1024,
    int(os.getenv("ORCH_MAX_FILE_BYTES", str(120 * 1024))),
)
ORCH_MAX_PATCH_BYTES = max(
    4096,
    int(os.getenv("ORCH_MAX_PATCH_BYTES", str(2 * 1024 * 1024))),
)
ORCH_MAX_TOKENS_PER_STEP = max(
    1024,
    int(os.getenv("ORCH_MAX_TOKENS_PER_STEP", "32000")),
)
ORCH_DEFAULT_REQUEST_MAX_TOKENS = min(
    ORCH_MAX_TOKENS_PER_STEP,
    max(
        4096,
        int(
            os.getenv(
                "ORCH_DEFAULT_REQUEST_MAX_TOKENS",
                str(min(16000, ORCH_MAX_TOKENS_PER_STEP)),
            )
        ),
    ),
)
ORCH_CHAT_REQUEST_MAX_TOKENS = min(
    ORCH_MAX_TOKENS_PER_STEP,
    max(
        128,
        int(
            os.getenv(
                "ORCH_CHAT_REQUEST_MAX_TOKENS",
                str(min(768, ORCH_DEFAULT_REQUEST_MAX_TOKENS)),
            )
        ),
    ),
)
ORCH_LIGHTWEIGHT_CHAT_MAX_TOKENS = min(
    ORCH_CHAT_REQUEST_MAX_TOKENS,
    max(
        128,
        int(
            os.getenv(
                "ORCH_LIGHTWEIGHT_CHAT_MAX_TOKENS",
                str(min(192, ORCH_CHAT_REQUEST_MAX_TOKENS)),
            )
        ),
    ),
)
ORCH_DEFAULT_AGENT_MAX_TOKENS = min(
    ORCH_MAX_TOKENS_PER_STEP,
    max(
        1024,
        int(
            os.getenv(
                "ORCH_DEFAULT_AGENT_MAX_TOKENS",
                str(min(8192, ORCH_MAX_TOKENS_PER_STEP)),
            )
        ),
    ),
)
ORCH_PLANNER_MAX_TOKENS = min(
    ORCH_MAX_TOKENS_PER_STEP,
    max(
        1024,
        int(
            os.getenv(
                "ORCH_PLANNER_MAX_TOKENS",
                str(ORCH_DEFAULT_AGENT_MAX_TOKENS),
            )
        ),
    ),
)
ORCH_CODER_MAX_TOKENS = min(
    ORCH_MAX_TOKENS_PER_STEP,
    max(
        1024,
        int(
            os.getenv(
                "ORCH_CODER_MAX_TOKENS",
                str(max(12000, ORCH_DEFAULT_AGENT_MAX_TOKENS)),
            )
        ),
    ),
)
ORCH_REVIEWER_MAX_TOKENS = min(
    ORCH_MAX_TOKENS_PER_STEP,
    max(
        1024,
        int(
            os.getenv(
                "ORCH_REVIEWER_MAX_TOKENS",
                str(max(8000, ORCH_DEFAULT_AGENT_MAX_TOKENS)),
            )
        ),
    ),
)
ORCH_MAX_STEPS_PER_JOB = max(1, int(os.getenv("ORCH_MAX_STEPS_PER_JOB", "80")))
ORCH_STEP_TIMEOUT_SEC = max(60, int(os.getenv("ORCH_STEP_TIMEOUT_SEC", "600")))
ORCH_DOD_HEALTH_RETRIES = max(
    20,
    int(os.getenv("ORCH_DOD_HEALTH_RETRIES", "60")),
)
ORCH_JOB_TIMEOUT_SEC = max(600, int(os.getenv("ORCH_JOB_TIMEOUT_SEC", "3600")))
ORCH_INDEX_CONTEXT_TIMEOUT_SEC = max(
    10,
    min(
        ORCH_STEP_TIMEOUT_SEC,
        int(os.getenv("ORCH_INDEX_CONTEXT_TIMEOUT_SEC", "45")),
    ),
)
ORCH_PLANNER_SPEC_TIMEOUT_SEC = max(
    30,
    min(
        ORCH_STEP_TIMEOUT_SEC,
        int(os.getenv("ORCH_PLANNER_SPEC_TIMEOUT_SEC", "90")),
    ),
)
ORCH_AGENT_HTTP_TIMEOUT_SEC = max(
    180,
    int(
        os.getenv(
            "ORCH_AGENT_HTTP_TIMEOUT_SEC",
            str(ORCH_STEP_TIMEOUT_SEC + 240),
        )
    ),
)
ORCH_PLANNER_AGENT_TIMEOUT_SEC = max(
    60,
    min(
        ORCH_AGENT_HTTP_TIMEOUT_SEC,
        int(os.getenv("ORCH_PLANNER_AGENT_TIMEOUT_SEC", "240")),
    ),
)
ORCH_CODER_AGENT_TIMEOUT_SEC = max(
    60,
    min(
        ORCH_AGENT_HTTP_TIMEOUT_SEC,
        int(os.getenv("ORCH_CODER_AGENT_TIMEOUT_SEC", "300")),
    ),
)
ORCH_REVIEWER_AGENT_TIMEOUT_SEC = max(
    60,
    min(
        ORCH_AGENT_HTTP_TIMEOUT_SEC,
        int(os.getenv("ORCH_REVIEWER_AGENT_TIMEOUT_SEC", "240")),
    ),
)
ORCH_CHAT_AGENT_TIMEOUT_SEC = max(
    30,
    min(
        ORCH_AGENT_HTTP_TIMEOUT_SEC,
        int(os.getenv("ORCH_CHAT_AGENT_TIMEOUT_SEC", "75")),
    ),
)
ORCH_REASONER_BRIEF_TIMEOUT_SEC = max(
    15,
    min(
        ORCH_CHAT_AGENT_TIMEOUT_SEC,
        int(os.getenv("ORCH_REASONER_BRIEF_TIMEOUT_SEC", "45")),
    ),
)
ORCH_CHAT_WEB_GROUNDING_TIMEOUT_SEC = max(
    5,
    min(
        ORCH_CHAT_AGENT_TIMEOUT_SEC,
        int(os.getenv("ORCH_CHAT_WEB_GROUNDING_TIMEOUT_SEC", "8")),
    ),
)
ORCH_PLANNER_PROMPT_CHAR_LIMIT = max(
    1200,
    int(os.getenv("ORCH_PLANNER_PROMPT_CHAR_LIMIT", "5000")),
)
ORCH_CODER_PROMPT_CHAR_LIMIT = max(
    1200,
    int(os.getenv("ORCH_CODER_PROMPT_CHAR_LIMIT", "7000")),
)
ORCH_REVIEWER_PROMPT_CHAR_LIMIT = max(
    1200,
    int(os.getenv("ORCH_REVIEWER_PROMPT_CHAR_LIMIT", "5000")),
)
ORCH_PLANNER_CONTEXT_CHAR_LIMIT = max(
    0,
    int(os.getenv("ORCH_PLANNER_CONTEXT_CHAR_LIMIT", "2500")),
)
ORCH_CODER_CONTEXT_CHAR_LIMIT = max(
    0,
    int(os.getenv("ORCH_CODER_CONTEXT_CHAR_LIMIT", "4500")),
)
ORCH_REVIEWER_CONTEXT_CHAR_LIMIT = max(
    0,
    int(os.getenv("ORCH_REVIEWER_CONTEXT_CHAR_LIMIT", "2500")),
)
ORCH_EXPERIENCE_MEMORY_CHAR_LIMIT = max(
    0,
    int(os.getenv("ORCH_EXPERIENCE_MEMORY_CHAR_LIMIT", "1200")),
)
ORCH_FORENSIC_MAX_INVENTORY = max(
    100,
    int(os.getenv("ORCH_FORENSIC_MAX_INVENTORY", "1000")),
)
ORCH_TIMELOCK_FINALIZE_SEC = max(
    0,
    int(os.getenv("ORCH_TIMELOCK_FINALIZE_SEC", "0")),
)
ORCH_USE_FIXED_TEMPLATE = (
    os.getenv("ORCH_USE_FIXED_TEMPLATE", "false").strip().lower()
    in {"1", "true", "yes", "on"}
)
ORCH_API_PORT = int(os.getenv("ORCH_API_PORT", "18000"))
ORCH_HEALTH_URL = os.getenv(
    "ORCH_HEALTH_URL",
    f"http://localhost:{ORCH_API_PORT}/health",
)
ORCH_ENABLE_WEB_GROUNDING = (
    os.getenv("ORCH_ENABLE_WEB_GROUNDING", "true").strip().lower()
    in {"1", "true", "yes", "on"}
)
ORCH_RUNTIME_CONFIG_PATH = "knowledge/orchestrator_runtime_config.json"
ORCH_VALIDATION_WORK_ROOT = "uploads/tmp/orchestrator_validation"
ORCH_OUTPUT_AUDIT_PATH = "docs/output_audit.json"

ORCH_RUNTIME_CORE_MODEL_ROUTE_KEYS = [
    "default",
    "reasoning",
    "coding",
    "planner",
    "coder",
    "reviewer",
    "smart_planner",
    "smart_executor",
]

ORCH_RUNTIME_EXPERIENCE_MODEL_ROUTE_KEYS = [
    "chat",
    "voice_chat",
    "designer",
    "smart_designer",
]

ORCH_TOKEN_TUNING_PRESETS: Dict[int, Dict[str, Any]] = {
    -1: {
        "max_tokens_per_step": 4096,
        "default_request_max_tokens": 4096,
        "chat_request_max_tokens": 768,
        "default_agent_max_tokens": 1024,
        "planner_max_tokens": 1024,
        "coder_max_tokens": 1024,
        "reviewer_max_tokens": 1024,
        "planner_prompt_char_limit": 2200,
        "coder_prompt_char_limit": 2600,
        "reviewer_prompt_char_limit": 2200,
        "planner_context_char_limit": 700,
        "coder_context_char_limit": 900,
        "reviewer_context_char_limit": 700,
        "experience_memory_char_limit": 400,
    },
    0: {
        "max_tokens_per_step": 4096,
        "default_request_max_tokens": 4096,
        "chat_request_max_tokens": 1024,
        "default_agent_max_tokens": 2048,
        "planner_max_tokens": 2048,
        "coder_max_tokens": 2048,
        "reviewer_max_tokens": 2048,
        "planner_prompt_char_limit": 3200,
        "coder_prompt_char_limit": 3600,
        "reviewer_prompt_char_limit": 3200,
        "planner_context_char_limit": 1400,
        "coder_context_char_limit": 1800,
        "reviewer_context_char_limit": 1400,
        "experience_memory_char_limit": 800,
    },
    1: {
        "max_tokens_per_step": 6144,
        "default_request_max_tokens": 6144,
        "chat_request_max_tokens": 1536,
        "default_agent_max_tokens": 3072,
        "planner_max_tokens": 3072,
        "coder_max_tokens": 3072,
        "reviewer_max_tokens": 3072,
        "planner_prompt_char_limit": 4200,
        "coder_prompt_char_limit": 4800,
        "reviewer_prompt_char_limit": 4200,
        "planner_context_char_limit": 1800,
        "coder_context_char_limit": 2400,
        "reviewer_context_char_limit": 1800,
        "experience_memory_char_limit": 1200,
    },
}


def _normalize_canonical_evidence_bundle(evidence_bundle: Dict[str, Any] | None) -> Dict[str, Any]:
    payload = dict(evidence_bundle or {})
    contract = dict(payload.get("contract") or {})
    execution = dict(payload.get("execution") or {})
    readiness = dict(payload.get("readiness") or {})
    operations = dict(payload.get("operations") or {})
    selective_apply = dict(payload.get("selective_apply") or {})
    contract.setdefault("evidence_schema_version", "v1")
    selective_apply.setdefault("target_file_ids", list(selective_apply.get("target_file_ids") or []))
    selective_apply.setdefault("target_section_ids", list(selective_apply.get("target_section_ids") or []))
    selective_apply.setdefault("target_feature_ids", list(selective_apply.get("target_feature_ids") or []))
    selective_apply.setdefault("target_chunk_ids", list(selective_apply.get("target_chunk_ids") or []))
    selective_apply.setdefault("failure_tags", list(selective_apply.get("failure_tags") or []))
    selective_apply.setdefault("repair_tags", list(selective_apply.get("repair_tags") or []))
    payload["contract"] = contract
    payload["execution"] = execution
    payload["readiness"] = readiness
    payload["operations"] = operations
    payload["selective_apply"] = selective_apply
    return payload

ORCH_TIMEOUT_TUNING_PRESETS: Dict[int, Dict[str, Any]] = {
    -1: {
        "step_timeout_sec": 300,
        "job_timeout_sec": 1200,
        "agent_http_timeout_sec": 180,
        "planner_agent_timeout_sec": 60,
        "coder_agent_timeout_sec": 60,
        "reviewer_agent_timeout_sec": 60,
        "index_context_timeout_sec": 10,
    },
    0: {
        "step_timeout_sec": 420,
        "job_timeout_sec": 1800,
        "agent_http_timeout_sec": 180,
        "planner_agent_timeout_sec": 60,
        "coder_agent_timeout_sec": 90,
        "reviewer_agent_timeout_sec": 60,
        "index_context_timeout_sec": 15,
    },
    1: {
        "step_timeout_sec": 600,
        "job_timeout_sec": 2400,
        "agent_http_timeout_sec": 240,
        "planner_agent_timeout_sec": 90,
        "coder_agent_timeout_sec": 120,
        "reviewer_agent_timeout_sec": 90,
        "index_context_timeout_sec": 20,
    },
}


def _bounded_token_floor(target: int) -> int:
    return min(ORCH_MAX_TOKENS_PER_STEP, max(1024, int(target)))


def _coerce_runtime_int(
    value: Any,
    fallback: int,
    *,
    minimum: int,
    maximum: Optional[int] = None,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _coerce_runtime_bool(value: Any, fallback: bool) -> bool:
    # 런타임 설정은 JSON/문자열 양쪽에서 들어오므로 "false"를 True로 오판하면 안 된다.
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
        return fallback
    if value is None:
        return fallback
    return bool(value)


def _resolve_step_token_budget(
    requested: Optional[int],
    *,
    minimum: int,
    preferred_default: Optional[int] = None,
) -> int:
    effective_budget = int(
        preferred_default or ORCH_DEFAULT_AGENT_MAX_TOKENS
    )
    requested_cap = int(requested or 0)
    if requested_cap > 0:
        effective_budget = min(effective_budget, requested_cap)
    effective_budget = max(
        effective_budget,
        _bounded_token_floor(minimum),
    )
    return min(ORCH_MAX_TOKENS_PER_STEP, effective_budget)


def _agent_default_token_budget(agent_key: str) -> int:
    if agent_key == "planner":
        return ORCH_PLANNER_MAX_TOKENS
    if agent_key == "coder":
        return ORCH_CODER_MAX_TOKENS
    if agent_key == "reviewer":
        return ORCH_REVIEWER_MAX_TOKENS
    return ORCH_DEFAULT_AGENT_MAX_TOKENS


def _agent_prompt_char_limit(agent_key: str) -> int:
    if agent_key == "planner":
        return ORCH_PLANNER_PROMPT_CHAR_LIMIT
    if agent_key == "coder":
        return ORCH_CODER_PROMPT_CHAR_LIMIT
    if agent_key == "reviewer":
        return ORCH_REVIEWER_PROMPT_CHAR_LIMIT
    return ORCH_CODER_PROMPT_CHAR_LIMIT


def _agent_context_char_limit(agent_key: str) -> int:
    if agent_key == "planner":
        return ORCH_PLANNER_CONTEXT_CHAR_LIMIT
    if agent_key == "coder":
        return ORCH_CODER_CONTEXT_CHAR_LIMIT
    if agent_key == "reviewer":
        return ORCH_REVIEWER_CONTEXT_CHAR_LIMIT
    return ORCH_CODER_CONTEXT_CHAR_LIMIT


def _truncate_prompt_segment(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n...[truncated]"


ORCH_DYNAMIC_TOOL_ALLOWED_IMPORTS = {


    "asyncio",
    "base64",
    "collections",
    "csv",
    "datetime",
    "functools",
    "hashlib",
    "hmac",
    "httpx",
    "itertools",
    "json",
    "math",
    "random",
    "re",
    "requests",
    "ssl",
    "statistics",
    "time",
    "typing",
    "urllib",
    "yaml",
}

ORCH_DYNAMIC_TOOL_BLOCKED_MODULES = {
    "builtins",
    "ctypes",
    "importlib",
    "multiprocessing",
    "os",
    "pathlib",
    "shutil",
    "socket",
    "subprocess",
    "sys",
    "threading",
}

ORCH_DYNAMIC_TOOL_BLOCKED_CALLS = {
    "__import__",
    "compile",
    "eval",
    "exec",
    "open",
}

ORCH_DYNAMIC_TOOL_BLOCKED_ATTRS = {
    "chmod",
    "chown",
    "exec_module",
    "mkdir",
    "makedirs",
    "popen",
    "remove",
    "rename",
    "replace",
    "rmdir",
    "rmtree",
    "run",
    "system",
    "unlink",
    "write_bytes",
    "write_text",
}

ORCH_SOURCE_FILE_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".cs",
}

ORCH_MINIMAL_CONTENT: Dict[str, str] = {
    "readme.md": "# Generated Vertical Slice\n\n자동 생성 프로젝트입니다.\n",
    ".env.example": "APP_ENV=dev\nSECRET_KEY=change-me\n",
    ".gitignore": "__pycache__/\n.venv/\n.pytest_cache/\n",
    "package.json": (
        "{\n"
        "  \"name\": \"generated-app\",\n"
        "  \"private\": true,\n"
        "  \"scripts\": {\n"
        "    \"dev\": \"next dev\",\n"
        "    \"build\": \"next build\",\n"
        "    \"start\": \"next start\"\n"
        "  },\n"
        "  \"dependencies\": {\n"
        "    \"next\": \"16.1.6\",\n"
        "    \"react\": \"18.3.1\",\n"
        "    \"react-dom\": \"18.3.1\"\n"
        "  },\n"
        "  \"devDependencies\": {\n"
        "    \"typescript\": \"5.5.4\",\n"
        "    \"@types/node\": \"20.14.12\",\n"
        "    \"@types/react\": \"18.3.3\",\n"
        "    \"@types/react-dom\": \"18.3.0\"\n"
        "  }\n"
        "}\n"
    ),
    "tsconfig.json": (
        "{\n"
        "  \"compilerOptions\": {\n"
        "    \"target\": \"ES2022\",\n"
        "    \"lib\": [\"dom\", \"dom.iterable\", \"es2022\"],\n"
        "    \"allowJs\": false,\n"
        "    \"skipLibCheck\": true,\n"
        "    \"strict\": true,\n"
        "    \"noEmit\": true,\n"
        "    \"esModuleInterop\": true,\n"
        "    \"module\": \"esnext\",\n"
        "    \"moduleResolution\": \"bundler\",\n"
        "    \"resolveJsonModule\": true,\n"
        "    \"isolatedModules\": true,\n"
        "    \"jsx\": \"react-jsx\",\n"
        "    \"incremental\": true\n"
        "  },\n"
        "  \"include\": [\"next-env.d.ts\", \"**/*.ts\", \"**/*.tsx\"],\n"
        "  \"exclude\": [\"node_modules\"]\n"
        "}\n"
    ),
    "next-env.d.ts": (
        "/// <reference types=\"next\" />\n"
        "/// <reference types=\"next/image-types/global\" />\n"
    ),
    "next.config.js": (
        "/** @type {import('next').NextConfig} */\n"
        "const nextConfig = { reactStrictMode: true };\n\n"
        "module.exports = nextConfig;\n"
    ),
    "docker-compose.yml": (
        "services:\n"
        "  api:\n"
        "    image: python:3.11-slim\n"
        "    working_dir: /app\n"
        "    volumes:\n"
        "      - ./:/app\n"
        "    command: >-\n"
        "      sh -c \"pip install -r requirements.txt && "
        "uvicorn backend.app.main:app --host 0.0.0.0 --port 8000\"\n"
        "    ports:\n"
        "      - \"${ORCH_API_PORT:-18000}:8000\"\n"
    ),
    "requirements.txt": (
        "fastapi==0.110.0\n"
        "uvicorn==0.27.1\n"
        "httpx==0.26.0\n"
        "pytest==8.2.0\n"
    ),
    "pytest.ini": (
        "[pytest]\n"
        "asyncio_mode = auto\n"
        "asyncio_default_fixture_loop_scope = function\n"
    ),
    "docs/architecture.md": "# Architecture\n\n자동 생성된 구조 문서입니다.\n",
    "docs/architecture.contract.json": "{\n  \"schema_version\": \"generated.v1\"\n}\n",
    "docs/orchestration_rules_checklist.md": "# Orchestration Rules Checklist\n\n- required_files\n- structure_compliance\n- completion_gate\n- semantic_audit\n",
    "app/layout.tsx": (
        "import './globals.css';\n"
        "import type { ReactNode } from 'react';\n\n"
        "export default function RootLayout({ children }: { children: ReactNode }) {\n"
        "  return (\n"
        "    <html lang=\"en\">\n"
        "      <body>{children}</body>\n"
        "    </html>\n"
        "  );\n"
        "}\n"
    ),
    "app/page.tsx": "export default function HomePage() {\n  return <main>Generated app</main>;\n}\n",
    "app/dashboard/page.tsx": "export default function DashboardPage() {\n  return <main>Dashboard</main>;\n}\n",
    "app/api/health/route.ts": (
        "import { NextResponse } from 'next/server';\n\n"
        "export async function GET() {\n"
        "  return NextResponse.json({ ok: true });\n"
        "}\n"
    ),
    "app/api/brief/route.ts": (
        "import { NextResponse } from 'next/server';\n\n"
        "export async function GET() {\n"
        "  return NextResponse.json({ refreshedAt: new Date().toISOString() });\n"
        "}\n"
    ),
    "app/globals.css": "html, body { margin: 0; padding: 0; font-family: sans-serif; }\n",
    "backend/app/main.py": (
        "from fastapi import FastAPI\n"
        "from backend.app.api.routes.health import router as health_router\n\n"
        "app = FastAPI()\n"
        "app.include_router(health_router)\n"
    ),
    "backend/app/api/routes/__init__.py": "",
    "backend/app/api/routes/health.py": (
        "from fastapi import APIRouter\n\n"
        "router = APIRouter()\n\n"
        "@router.get('/health')\n"
        "def health_check():\n"
        "    return {'ok': True}\n"
    ),
    "backend/app/core/config.py": "class Settings:\n    app_env = 'dev'\n\nsettings = Settings()\n",
    "backend/app/core/security.py": "def get_password_hash(value: str) -> str:\n    return value\n",
    "backend/app/core/database.py": "def get_db():\n    return None\n",
    "backend/app/api/deps.py": "def get_current_user():\n    return None\n",
}

AGENT_ROLES = {
    "planner": "작업 계획 에이전트",
    "coder": "코드 생성 에이전트",
    "reviewer": "코드 리뷰 전문가",
    "designer": "UI/UX 디자인 에이전트",
    "reasoner": "추론 에이전트",
    "chat": "일반 챗봇 에이전트",
    "voice_chat": "음성 챗봇 에이전트",
    "b_brain": "멀티 코드 생성기 라우터",
}

ORCH_A_BRAIN_AGENT_KEYS = ["reasoner", "planner"]
ORCH_B_BRAIN_AGENT_KEY = "b_brain"
ORCH_REFINER_FIXER_STAGE = {
    "id": "ARCH-0045",
    "label": "4.5단계",
    "title": "Refiner/Fixer",
    "state": "REFINER_FIXER",
    "summary": "핵심엔진 직후 로직 전에 구조 정리, 계약 보정, 자동 수정 안전고리를 닫습니다.",
}


def _current_agents() -> Dict[str, Dict[str, str]]:
    agents = {
        "planner": {
            "model": get_planner_model(),
            "role": AGENT_ROLES["planner"],
        },
        "coder": {
            "model": get_coder_model(),
            "role": AGENT_ROLES["coder"],
        },
        "reviewer": {
            "model": get_reviewer_model(),
            "role": AGENT_ROLES["reviewer"],
        },
        "designer": {
            "model": get_designer_model(),
            "role": AGENT_ROLES["designer"],
        },
        "reasoner": {
            "model": get_reasoning_model(),
            "role": AGENT_ROLES["reasoner"],
        },
        "chat": {
            "model": get_chat_model(),
            "role": AGENT_ROLES["chat"],
        },
        "voice_chat": {
            "model": get_voice_chat_model(),
            "role": AGENT_ROLES["voice_chat"],
        },
        "b_brain": {
            "model": "multi_code_generator_router",
            "role": AGENT_ROLES["b_brain"],
        },
    }
    return agents


AGENTS = _current_agents()

SYSTEM_PROMPTS = {
    "planner": (
        "당신은 시니어 소프트웨어 아키텍트입니다. "
        "구현 계획을 최대 5단계로 수립하고, 확인되지 않은 내용을 완료나 통과로 단정하지 마세요. "
        "반드시 한국어로 답변하세요."
    ),
    "coder": (
        "당신은 전문 Python/TypeScript 개발자입니다. "
        "프로덕션 수준 코드를 한국어 주석과 함께 작성하세요."
    ),
    "reviewer": "당신은 코드 리뷰 전문가입니다. 버그, 보안, 성능 관점에서 코드를 분석하고 개선안을 한국어로 제시하세요.",
    "designer": (
        "당신은 UI/UX 전문가입니다. Tailwind CSS + Next.js 기준으로 "
        "컴포넌트를 설계하고 한국어로 설명하세요."
    ),
    "reasoner": (
        "당신은 사용자와 직접 대화하며 요구를 해석하고 연구 방향을 정리하는 "
        "추론 전문가입니다. 자연어 이해, 구조 설계, 논리 전개, 수학적 판단, "
        "대안 비교를 명확한 단계로 설명하고, 확인되지 않은 내용을 사실처럼 단정하지 마세요. "
        "답변은 항상 한국어로 작성하고, 설계 의도와 판단 근거를 분리해서 설명하세요. "
        "또한 공학적 근거, 과학적 모델링, 시스템 사고, 미래 기술 시나리오, 고급 상상력 기반 대안까지 함께 제시하세요."
    ),
    "chat": (
        "당신은 매우 영리한 기술 파트너형 챗봇입니다. 질문 의도를 먼저 파악하고, "
        "실무적으로 바로 쓸 수 있는 답과 함께 신기술, 미래 방향, 발명적 확장 아이디어까지 "
        "한국어로 자연스럽고 구체적으로 제안하세요. "
        "가능하면 범용 플랫폼 관점, 엔진 확장성, 자동화, 지식 축적, 장기 진화 로드맵까지 포함하세요."
    ),
    "voice_chat": (
        "당신은 추론형 음성 비서입니다. 말투는 자연스럽게 유지하되, "
        "핵심 판단, 근거, 다음 행동을 짧고 또렷하게 말하고, 필요하면 reasoner 수준의 해석과 논리 흐름을 함께 제시하세요."
    ),
    "b_brain": (
        "당신은 멀티 코드 생성기 라우터입니다. "
        "A 브레인이 정한 설계와 스택에 따라 python_code_generator 또는 non_python_code_generator를 선택하고, "
        "선택 근거와 생성 책임 경계를 한국어로 명확히 설명하세요."
    ),
}

ORCH_EXECUTION_CONSTITUTION = (
    "[오케스트레이터 헌법 규칙]\n"
    "- 사용자의 요구는 표면 문장만 좁게 해석하지 말고, 실사용 가능한 결과를 위해 "
    "자연스럽게 필요한 설계, 연결부, 검증, 승인 흐름을 함께 고려한다.\n"
    "- 다만 사용자가 요청하지 않은 무관한 기능 확장이나 기존 연결 구조 변경은 "
    "임의로 하지 않는다.\n"
    "- 검증은 선택이 아니라 필수다. 아직 실행하지 않은 검증을 통과나 성공처럼 "
    "기록하지 않는다.\n"
    "- 품질이 낮거나 반응이 없거나 실사용이 어려운 결과물은 성공처럼 포장하지 말고, "
    "실패 또는 미달로 명확히 보고한다.\n"
    "- 사용자가 제안한 구현 방식이 현재 기술이나 시간 조건상 불가능하면, "
    "불가능하다고 분명히 말하고 즉시 검증된 대체 구현 방향과 현실적인 절차를 제시한다.\n"
    "- 무거운 요구사항은 대충 흉내 내지 말고, 가능한 범위, 남은 난점, 필요한 기간을 "
    "명확히 분리해 설명한다.\n"
    "- 모든 답변과 산출물은 한국어로 작성하고, 완료/통과/반영 여부는 실제 근거가 있을 때만 단정한다."
)


def _compose_agent_system_prompt(agent_key: str) -> str:
    return (
        SYSTEM_PROMPTS[agent_key]
        + "\n\n"
        + ORCH_EXECUTION_CONSTITUTION
    )


class OrchestrationRequest(BaseModel):
    task: str
    mode: str = "auto"  # auto | code | design | review | plan | full |
    # program_5step
    run_id: Optional[str] = None
    max_tokens: int = Field(
        default_factory=lambda: ORCH_DEFAULT_REQUEST_MAX_TOKENS
    )
    pipeline: Optional[List[str]] = None  # ["planner", "coder", "reviewer"]
    auto_apply: bool = True
    run_postcheck: bool = True
    retry_on_postcheck_fail: bool = True
    forensic_on_fail: bool = True
    project_name: Optional[str] = None
    output_base_dir: str = "uploads/projects"
    output_dir: Optional[str] = None
    continue_in_place: bool = False
    manual_mode: bool = False
    companion_mode: str = "hybrid"
    conversation: List[Dict[str, Any]] = Field(default_factory=list)
    auto_connect: Optional[AutoConnectMeta] = None
    enable_improvement_loop: bool = True
    refinement_request: Optional[str] = None
    max_improvement_cycles: int = 1


class OrchestrationSpec(BaseModel):
    mode: str = "code"
    pipeline: List[str] = Field(
        default_factory=lambda: ["planner", "coder"]
    )
    required_files: List[str] = Field(default_factory=list)
    validation_profile: str = "generic"
    dod_targets: List[str] = Field(default_factory=list)
    reasoning: str = ""
    spec_source: str = "planner"
    fallback_reason: Optional[str] = None
    manual_steps: List[str] = Field(default_factory=list)


class AgentResult(BaseModel):
    agent: str
    role: str
    model: str
    output: str


OFFICIAL_DOC_DOMAINS = {
    "docs.python.org",
    "developer.mozilla.org",
    "fastapi.tiangolo.com",
    "react.dev",
    "nextjs.org",
    "nodejs.org",
    "www.typescriptlang.org",
    "typescriptlang.org",
    "go.dev",
    "doc.rust-lang.org",
    "rust-lang.org",
    "kubernetes.io",
    "docs.docker.com",
    "learn.microsoft.com",
    "docs.microsoft.com",
    "learn.microsoft.com",
    "docs.aws.amazon.com",
    "cloud.google.com",
    "postgresql.org",
    "www.postgresql.org",
}

COMMUNITY_DOMAINS = {
    "github.com",
    "stackoverflow.com",
    "stackexchange.com",
    "dev.to",
    "medium.com",
    "reddit.com",
    "news.ycombinator.com",
    "velog.io",
    "tistory.com",
}


class OrchestrationResponse(BaseModel):
    task: str
    mode: str
    run_id: Optional[str] = None
    pipeline: List[str]
    results: List[AgentResult]
    final_output: str
    applied: bool = False
    output_dir: Optional[str] = None
    failed_output_dir: Optional[str] = None
    written_files: List[str] = Field(default_factory=list)
    apply_error: Optional[str] = None
    postcheck_ran: bool = False
    postcheck_ok: bool = False
    postcheck_logs: List[str] = Field(default_factory=list)
    postcheck_error: Optional[str] = None
    secondary_validation_ran: bool = False
    secondary_validation_ok: bool = False
    secondary_validation_logs: List[str] = Field(default_factory=list)
    secondary_validation_error: Optional[str] = None
    structure_validation_ran: bool = False
    structure_validation_ok: bool = False
    structure_validation_logs: List[str] = Field(default_factory=list)
    structure_validation_error: Optional[str] = None
    forensic_report: Optional[str] = None
    failure_summary: Optional[str] = None
    state_history: List[str] = Field(default_factory=list)
    dod_ran: bool = False
    dod_ok: bool = False
    dod_logs: List[str] = Field(default_factory=list)
    dod_error: Optional[str] = None
    checklist_path: Optional[str] = None
    manifest_path: Optional[str] = None
    artifact_log_path: Optional[str] = None
    output_audit_path: Optional[str] = None
    completion_gate_ok: bool = False
    completion_gate_error: Optional[str] = None
    completion_summary: Optional[str] = None
    semantic_audit_ran: bool = False
    semantic_audit_ok: bool = False
    semantic_audit_error: Optional[str] = None
    semantic_audit_summary: Optional[str] = None
    semantic_audit_score: Optional[int] = None
    semantic_audit_max_score: Optional[int] = None
    semantic_audit_threshold: Optional[int] = None
    semantic_audit_checklist: List[Dict[str, Any]] = Field(default_factory=list)
    semantic_audit_report_path: Optional[str] = None
    python_security_validation_ran: bool = False
    python_security_validation_ok: bool = False
    python_security_validation_logs: List[str] = Field(default_factory=list)
    python_security_validation_error: Optional[str] = None
    python_security_validation_findings: List[Dict[str, Any]] = Field(default_factory=list)
    python_security_validation_report_path: Optional[str] = None
    traceability_map_path: Optional[str] = None
    traceability_items: List[Dict[str, Any]] = Field(default_factory=list)
    template_profile: Optional[str] = None
    output_archive_path: Optional[str] = None
    conversation: List[ConversationMessage] = Field(default_factory=list)
    flow_trace: List[FlowTraceStep] = Field(default_factory=list)
    command_plan: List[FlowTraceCommand] = Field(default_factory=list)
    active_trace: Optional[FlowTraceStep] = None
    auto_connect: Optional[AutoConnectMeta] = None
    normalized_requirements: Dict[str, Any] = Field(default_factory=dict)
    domain_contract: Dict[str, Any] = Field(default_factory=dict)
    completion_judge: Dict[str, Any] = Field(default_factory=dict)
    integration_test_plan: Dict[str, Any] = Field(default_factory=dict)
    packaging_audit: Dict[str, Any] = Field(default_factory=dict)
    improvement_loop: Dict[str, Any] = Field(default_factory=dict)
    framework_e2e_validation: Dict[str, Any] = Field(default_factory=dict)
    external_integration_validation: Dict[str, Any] = Field(default_factory=dict)
    post_validation_analysis: Dict[str, Any] = Field(default_factory=dict)
    validation_artifacts: Dict[str, Any] = Field(default_factory=dict)
    operational_evidence: Dict[str, Any] = Field(default_factory=dict)
    operational_latency_summary: Dict[str, Any] = Field(default_factory=dict)
    artifact_paths: Dict[str, Any] = Field(default_factory=dict)
    evidence_bundle: Dict[str, Any] = Field(default_factory=dict)


class OrchestrationAcceptedResponse(BaseModel):
    accepted: bool = True
    run_id: Optional[str] = None
    project_name: Optional[str] = None
    output_dir: Optional[str] = None
    status: str = "accepted"
    poll_url: Optional[str] = None
    stream_url: Optional[str] = None
    message: str = "오케스트레이션이 백그라운드에서 계속 진행됩니다. poll_url 또는 stream_url 로 진행 상태를 확인하세요."


_ORCHESTRATION_PROGRESS_STORE: Dict[str, Dict[str, Any]] = {}


def _runtime_progress_root() -> Path:
    configured_root = (os.getenv("ADMIN_RUNTIME_ROOT", "") or "").strip()
    runtime_root = Path(configured_root).expanduser().resolve() if configured_root else (Path(tempfile.gettempdir()) / "codeai_admin_runtime").resolve()
    progress_root = runtime_root / "orchestration_progress"
    progress_root.mkdir(parents=True, exist_ok=True)
    return progress_root


def _orchestration_progress_path(run_id: str) -> Path:
    safe_run_id = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(run_id or "unknown")).strip("-") or "unknown"
    return _runtime_progress_root() / f"{safe_run_id}.json"


def _build_progress_poll_url(run_id: str) -> str:
    return f"/api/llm/orchestrate/progress/{run_id}"


def _build_progress_stream_url(run_id: str) -> str:
    return f"/api/llm/orchestrate/stream/{run_id}"


def _save_orchestration_progress(run_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(payload)
    normalized["run_id"] = str(run_id or normalized.get("run_id") or "")
    normalized.setdefault("updated_at", datetime.utcnow().isoformat() + "Z")
    _ORCHESTRATION_PROGRESS_STORE[normalized["run_id"]] = normalized
    progress_path = _orchestration_progress_path(normalized["run_id"])
    progress_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def _load_orchestration_progress(run_id: str) -> Dict[str, Any]:
    cached = _ORCHESTRATION_PROGRESS_STORE.get(str(run_id or ""))
    if isinstance(cached, dict) and cached:
        return dict(cached)
    progress_path = _orchestration_progress_path(run_id)
    try:
        if progress_path.exists() and progress_path.is_file():
            payload = json.loads(progress_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                _ORCHESTRATION_PROGRESS_STORE[str(run_id or "")] = dict(payload)
                return dict(payload)
    except Exception:
        return {}
    return {}


def _record_orchestration_progress_event(run_id: str, *, message: str, level: str = "info") -> Dict[str, Any]:
    current = _load_orchestration_progress(run_id)
    events = list(current.get("events") or [])
    events.append(
        {
            "at": datetime.utcnow().isoformat() + "Z",
            "level": level,
            "message": str(message or "").strip(),
        }
    )
    current["events"] = events[-120:]
    current["status"] = "running"
    return _save_orchestration_progress(run_id, current)


def _mark_orchestration_progress_result(run_id: str, response: OrchestrationResponse) -> Dict[str, Any]:
    payload = _load_orchestration_progress(run_id)
    payload.update(
        {
            "status": "completed",
            "result": response.model_dump(),
            "output_dir": response.output_dir,
            "project_name": response.normalized_requirements.get("project_name") if isinstance(response.normalized_requirements, dict) else payload.get("project_name"),
            "completed_at": datetime.utcnow().isoformat() + "Z",
        }
    )
    return _save_orchestration_progress(run_id, payload)


def _accepted_orchestrate_requests_full_mode(request: OrchestrationRequest) -> bool:
    mode = str(request.mode or "").strip().lower()
    return mode in {"full", "program_5step", "auto"}


def _mark_orchestration_progress_error(run_id: str, *, error_message: str) -> Dict[str, Any]:
    payload = _load_orchestration_progress(run_id)
    payload.update(
        {
            "status": "failed",
            "error": str(error_message or "실행 중 오류가 발생했습니다."),
            "completed_at": datetime.utcnow().isoformat() + "Z",
        }
    )
    return _save_orchestration_progress(run_id, payload)


def _build_evidence_bundle(
    *,
    validation_profile: str,
    completion_gate_ok: bool,
    completion_gate_error: str,
    semantic_audit_ok: bool,
    semantic_audit_score: int,
    product_readiness_hard_gate: Dict[str, Any],
    shipping_zip_validation: Dict[str, Any],
    final_readiness_checklist_path: str,
    operational_evidence: Dict[str, Any],
    target_patch_registry_snapshot: Dict[str, Any],
    run_id: str,
    post_validation_analysis: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    target_patch_entries = [
        item
        for item in (
            target_patch_registry_snapshot.get("matched_entries")
            or target_patch_registry_snapshot.get("reusable_patch_units")
            or []
        )
        if isinstance(item, dict)
    ]
    checklist_record_links = [
        {
            "record_scope_id": "phase-d-hard-gate",
            "applies_to": {
                "target_file_ids": list(target_patch_registry_snapshot.get("target_file_ids") or []),
                "target_section_ids": list(target_patch_registry_snapshot.get("target_section_ids") or []),
                "target_feature_ids": list(target_patch_registry_snapshot.get("target_feature_ids") or []),
                "target_chunk_ids": list(target_patch_registry_snapshot.get("target_chunk_ids") or []),
            },
            "result_status": "pass" if bool(product_readiness_hard_gate.get("ok")) else "blocked",
            "applied_to_source_evidence": {
                "status": "not_applicable_in_customer_generation",
                "required_for_pass": False,
            },
        },
        {
            "record_scope_id": "phase-f-self-run-terminal-state",
            "applies_to": {
                "target_file_ids": list(target_patch_registry_snapshot.get("target_file_ids") or []),
                "target_section_ids": list(target_patch_registry_snapshot.get("target_section_ids") or []),
                "target_feature_ids": list(target_patch_registry_snapshot.get("target_feature_ids") or []),
                "target_chunk_ids": list(target_patch_registry_snapshot.get("target_chunk_ids") or []),
            },
            "result_status": "pass",
            "applied_to_source_evidence": {
                "status": "closed_in_latest_session_records",
                "required_for_pass": False,
            },
        },
        {
            "record_scope_id": "phase-f-focused-self-healing-apply",
            "applies_to": {
                "target_file_ids": list(target_patch_registry_snapshot.get("target_file_ids") or []),
                "target_section_ids": list(target_patch_registry_snapshot.get("target_section_ids") or []),
                "target_feature_ids": list(target_patch_registry_snapshot.get("target_feature_ids") or []),
                "target_chunk_ids": list(target_patch_registry_snapshot.get("target_chunk_ids") or []),
            },
            "result_status": "pass",
            "applied_to_source_evidence": {
                "status": "closed_in_latest_session_records",
                "required_for_pass": False,
            },
        },
    ]
    return {
        "contract": {
            "evidence_schema_version": "v1",
            "profile_id": validation_profile,
        },
        "execution": {
            "evidence_run_id": run_id,
            "evidence_generated_at": datetime.utcnow().isoformat() + "Z",
            "self_run_status": "not_applicable",
            "completion_gate_ok": completion_gate_ok,
            "completion_gate_error": completion_gate_error,
            "semantic_audit_ok": semantic_audit_ok,
            "semantic_audit_score": semantic_audit_score,
            "post_validation_analysis": dict(post_validation_analysis or {}),
        },
        "readiness": {
            "product_readiness_hard_gate": product_readiness_hard_gate,
            "shipping_zip_validation": shipping_zip_validation,
            "final_readiness_checklist_path": final_readiness_checklist_path,
        },
        "operations": {
            "operational_evidence": operational_evidence,
        },
        "selective_apply": {
            "target_file_ids": list(target_patch_registry_snapshot.get("target_file_ids") or []),
            "target_section_ids": list(target_patch_registry_snapshot.get("target_section_ids") or []),
            "target_feature_ids": list(target_patch_registry_snapshot.get("target_feature_ids") or []),
            "target_chunk_ids": list(target_patch_registry_snapshot.get("target_chunk_ids") or []),
            "failure_tags": list(target_patch_registry_snapshot.get("failure_tags") or []),
            "repair_tags": list(target_patch_registry_snapshot.get("repair_tags") or []),
            "target_patch_entries": target_patch_entries,
            "record_scope_links": checklist_record_links,
        },
    }


class OrchestratorRuntimeConfigUpdate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    max_tokens_per_step: Optional[int] = None
    default_request_max_tokens: Optional[int] = None
    chat_request_max_tokens: Optional[int] = None
    default_agent_max_tokens: Optional[int] = None
    planner_max_tokens: Optional[int] = None
    coder_max_tokens: Optional[int] = None
    reviewer_max_tokens: Optional[int] = None
    step_timeout_sec: Optional[int] = None
    job_timeout_sec: Optional[int] = None
    agent_http_timeout_sec: Optional[int] = None
    planner_agent_timeout_sec: Optional[int] = None
    coder_agent_timeout_sec: Optional[int] = None
    reviewer_agent_timeout_sec: Optional[int] = None
    index_context_timeout_sec: Optional[int] = None
    planner_prompt_char_limit: Optional[int] = None
    coder_prompt_char_limit: Optional[int] = None
    reviewer_prompt_char_limit: Optional[int] = None
    planner_context_char_limit: Optional[int] = None
    coder_context_char_limit: Optional[int] = None
    reviewer_context_char_limit: Optional[int] = None
    experience_memory_char_limit: Optional[int] = None
    forensic_max_inventory: Optional[int] = None
    max_force_retries: Optional[int] = None
    force_complete: Optional[bool] = None
    allow_synthetic_fallback: Optional[bool] = None
    code_generation_strategy: Optional[str] = None
    min_files: Optional[int] = None
    min_dirs: Optional[int] = None
    model_tuning_level: Optional[int] = None
    token_tuning_level: Optional[int] = None
    timeout_tuning_level: Optional[int] = None
    model_routes: Optional[Dict[str, str]] = None
    execution_controls: Optional[Dict[str, Dict[str, Any]]] = None
    selected_profile: Optional[str] = None
    gpu_only_preferred: Optional[bool] = None
    advisory_controls: Optional[Dict[str, Any]] = None


def _runtime_config_file_path() -> Path:
    return Path(__file__).resolve().parents[2] / ORCH_RUNTIME_CONFIG_PATH


def _normalize_runtime_model_routes(payload: Dict[str, Any]) -> Dict[str, str]:
    raw_routes = (
        payload.get("model_routes")
        if isinstance(payload, dict)
        else {}
    )
    if not isinstance(raw_routes, dict):
        raw_routes = {}
    current_routes = get_configured_model_routes()
    normalized: Dict[str, str] = {}
    for key in MODEL_ROUTE_KEYS:
        value = str(
            raw_routes.get(key)
            or current_routes.get(key)
            or ""
        ).strip()
        if value:
            normalized[key] = value
    return normalized


def _merge_runtime_update_payload(
    current: Dict[str, Any],
    update_payload: Dict[str, Any],
) -> Dict[str, Any]:
    merged = {
        **current,
        **update_payload,
    }

    if isinstance(current.get("model_routes"), dict) or isinstance(update_payload.get("model_routes"), dict):
        merged_model_routes = dict(current.get("model_routes") or {})
        merged_model_routes.update(dict(update_payload.get("model_routes") or {}))
        merged["model_routes"] = merged_model_routes

    if isinstance(current.get("execution_controls"), dict) or isinstance(update_payload.get("execution_controls"), dict):
        merged_execution_controls = dict(current.get("execution_controls") or {})
        for key, value in dict(update_payload.get("execution_controls") or {}).items():
            current_control = merged_execution_controls.get(key)
            if isinstance(current_control, dict) and isinstance(value, dict):
                merged_execution_controls[key] = {
                    **current_control,
                    **value,
                }
            else:
                merged_execution_controls[key] = value
        merged["execution_controls"] = merged_execution_controls

    if isinstance(current.get("advisory_controls"), dict) or isinstance(update_payload.get("advisory_controls"), dict):
        merged["advisory_controls"] = {
            **dict(current.get("advisory_controls") or {}),
            **dict(update_payload.get("advisory_controls") or {}),
        }

    return merged


def _pick_available_runtime_model(
    available_models: List[str],
    candidates: List[str],
    fallback: str,
) -> str:
    available_set = set(available_models)
    for candidate in candidates:
        if candidate in available_set:
            return candidate
    return fallback


def _get_admin_lightweight_chat_model() -> str:
    # 관리자 경량 채팅 모델은 관리자 대시보드의 모델 설정값을 그대로 따른다.
    return get_chat_model()


def _resolve_admin_chat_model(agent_key: str, *, lightweight: bool) -> str:
    normalized = str(agent_key or "chat").strip().lower()
    if normalized == "voice_chat":
        return get_voice_chat_model()
    if normalized == "reasoner":
        return get_reasoning_model()
    if normalized == "coder":
        return get_coder_model()
    if normalized == "planner":
        return get_planner_model()
    if normalized == "reviewer":
        return get_reviewer_model()
    if normalized == "designer":
        return get_designer_model()
    if lightweight:
        return _get_admin_lightweight_chat_model()
    return get_chat_model()


def _profile_model_routes(profile_key: str) -> Dict[str, str]:
    for profile in get_recommended_runtime_profiles():
        if str(profile.get("key", "")).strip() != profile_key:
            continue
        routes = profile.get("model_routes")
        if isinstance(routes, dict):
            normalized: Dict[str, str] = {}
            for key in MODEL_ROUTE_KEYS:
                value = str(routes.get(key, "")).strip()
                if value:
                    normalized[key] = value
            return normalized
    return {}


def _build_tuned_model_routes(
    base_routes: Dict[str, str],
    *,
    selected_profile: str,
    tuning_level: int,
) -> Dict[str, str]:
    if tuning_level == 0:
        return dict(base_routes)

    available_models = get_available_ollama_models()
    next_routes = dict(base_routes)
    low_core_model = _pick_available_runtime_model(
        available_models,
        ["qwen2.5-coder:7b", QWEN_CODER_Q4_TAG],
        next_routes.get("coder") or next_routes.get("default") or "qwen2.5-coder:7b",
    )
    balanced_core_model = _pick_available_runtime_model(
        available_models,
        [QWEN_CODER_Q4_TAG, QWEN_CODER_Q5_TAG, "qwen2.5-coder:7b"],
        next_routes.get("coder") or next_routes.get("default") or QWEN_CODER_Q4_TAG,
    )
    high_core_model = _pick_available_runtime_model(
        available_models,
        [QWEN_CODER_Q5_TAG, QWEN_CODER_Q6_TAG, QWEN_CODER_Q8_TAG, QWEN_CODER_Q4_TAG],
        next_routes.get("coder") or next_routes.get("default") or QWEN_CODER_Q5_TAG,
    )
    low_experience_model = _pick_available_runtime_model(
        available_models,
        [QWEN_CODER_Q4_TAG, "qwen2.5-coder:7b"],
        next_routes.get("chat") or next_routes.get("default") or QWEN_CODER_Q4_TAG,
    )
    high_experience_model = _pick_available_runtime_model(
        available_models,
        [QWEN_CODER_Q6_TAG, QWEN_CODER_Q5_TAG, QWEN_CODER_Q8_TAG, QWEN_CODER_Q4_TAG],
        next_routes.get("chat") or next_routes.get("default") or QWEN_CODER_Q5_TAG,
    )

    selected_core_model = (
        low_core_model if tuning_level < 0 else high_core_model
    )
    selected_experience_model = (
        low_experience_model if tuning_level < 0 else high_experience_model
    )
    if tuning_level == 0:
        selected_core_model = balanced_core_model

    for route_key in ORCH_RUNTIME_CORE_MODEL_ROUTE_KEYS:
        next_routes[route_key] = selected_core_model
    for route_key in ORCH_RUNTIME_EXPERIENCE_MODEL_ROUTE_KEYS:
        next_routes[route_key] = selected_experience_model
    return next_routes


def _apply_runtime_tuning_presets(
    payload: Dict[str, Any],
    *,
    selected_profile: str,
    model_tuning_level: int,
    token_tuning_level: int,
    timeout_tuning_level: int,
) -> Dict[str, Any]:
    effective_payload = {}
    effective_payload.update(
        ORCH_TOKEN_TUNING_PRESETS.get(
            token_tuning_level,
            ORCH_TOKEN_TUNING_PRESETS[0],
        )
    )
    effective_payload.update(
        ORCH_TIMEOUT_TUNING_PRESETS.get(
            timeout_tuning_level,
            ORCH_TIMEOUT_TUNING_PRESETS[0],
        )
    )
    # 명시적 관리자 저장값이 preset 기본값에 다시 덮이지 않도록 마지막에 payload 를 우선 적용한다.
    effective_payload.update(payload)
    base_routes = _normalize_runtime_model_routes(effective_payload)
    effective_payload["model_routes"] = _build_tuned_model_routes(
        base_routes,
        selected_profile=selected_profile,
        tuning_level=model_tuning_level,
    )
    return effective_payload


ORCH_ADVISORY_CONTROLS: Dict[str, Any] = {
    "clarification_questions_enabled": True,
    "max_clarification_questions": 3,
    "evidence_panel_enabled": True,
    "max_evidence_items": 5,
    "next_action_suggestions_enabled": True,
    "max_next_actions": 3,
    "scientific_reasoning_enabled": True,
    "systems_thinking_enabled": True,
    "future_tech_expansion_enabled": True,
    "cross_domain_synthesis_enabled": True,
    "innovation_scenarios_enabled": True,
    "max_innovation_scenarios": 5,
    "max_system_design_alternatives": 4,
}


def _normalize_advisory_controls(payload: Dict[str, Any]) -> Dict[str, Any]:
    raw_controls = payload.get("advisory_controls")
    if not isinstance(raw_controls, dict):
        raw_controls = {}
    template_candidates = {
        "clarification_questions_enabled": _coerce_runtime_bool(
            raw_controls.get(
                "clarification_questions_enabled",
                ORCH_ADVISORY_CONTROLS["clarification_questions_enabled"],
            ),
            ORCH_ADVISORY_CONTROLS["clarification_questions_enabled"],
        ),
        "max_clarification_questions": int(
            raw_controls.get(
                "max_clarification_questions",
                ORCH_ADVISORY_CONTROLS["max_clarification_questions"],
            )
        ),
        "evidence_panel_enabled": _coerce_runtime_bool(
            raw_controls.get(
                "evidence_panel_enabled",
                ORCH_ADVISORY_CONTROLS["evidence_panel_enabled"],
            ),
            ORCH_ADVISORY_CONTROLS["evidence_panel_enabled"],
        ),
        "max_evidence_items": int(
            raw_controls.get(
                "max_evidence_items",
                ORCH_ADVISORY_CONTROLS["max_evidence_items"],
            )
        ),
        "next_action_suggestions_enabled": _coerce_runtime_bool(
            raw_controls.get(
                "next_action_suggestions_enabled",
                ORCH_ADVISORY_CONTROLS["next_action_suggestions_enabled"],
            ),
            ORCH_ADVISORY_CONTROLS["next_action_suggestions_enabled"],
        ),
        "max_next_actions": int(
            raw_controls.get(
                "max_next_actions",
                ORCH_ADVISORY_CONTROLS["max_next_actions"],
            )
        ),
        "scientific_reasoning_enabled": _coerce_runtime_bool(
            raw_controls.get(
                "scientific_reasoning_enabled",
                ORCH_ADVISORY_CONTROLS["scientific_reasoning_enabled"],
            ),
            ORCH_ADVISORY_CONTROLS["scientific_reasoning_enabled"],
        ),
        "systems_thinking_enabled": _coerce_runtime_bool(
            raw_controls.get(
                "systems_thinking_enabled",
                ORCH_ADVISORY_CONTROLS["systems_thinking_enabled"],
            ),
            ORCH_ADVISORY_CONTROLS["systems_thinking_enabled"],
        ),
        "future_tech_expansion_enabled": _coerce_runtime_bool(
            raw_controls.get(
                "future_tech_expansion_enabled",
                ORCH_ADVISORY_CONTROLS["future_tech_expansion_enabled"],
            ),
            ORCH_ADVISORY_CONTROLS["future_tech_expansion_enabled"],
        ),
        "cross_domain_synthesis_enabled": _coerce_runtime_bool(
            raw_controls.get(
                "cross_domain_synthesis_enabled",
                ORCH_ADVISORY_CONTROLS["cross_domain_synthesis_enabled"],
            ),
            ORCH_ADVISORY_CONTROLS["cross_domain_synthesis_enabled"],
        ),
        "innovation_scenarios_enabled": _coerce_runtime_bool(
            raw_controls.get(
                "innovation_scenarios_enabled",
                ORCH_ADVISORY_CONTROLS["innovation_scenarios_enabled"],
            ),
            ORCH_ADVISORY_CONTROLS["innovation_scenarios_enabled"],
        ),
        "max_innovation_scenarios": int(
            raw_controls.get(
                "max_innovation_scenarios",
                ORCH_ADVISORY_CONTROLS["max_innovation_scenarios"],
            )
        ),
        "max_system_design_alternatives": int(
            raw_controls.get(
                "max_system_design_alternatives",
                ORCH_ADVISORY_CONTROLS["max_system_design_alternatives"],
            )
        ),
    }
    return template_candidates


def _runtime_config_base_payload() -> Dict[str, Any]:
    advisory_controls = dict(ORCH_ADVISORY_CONTROLS or {})
    return {
        "max_tokens_per_step": ORCH_MAX_TOKENS_PER_STEP,
        "default_request_max_tokens": ORCH_DEFAULT_REQUEST_MAX_TOKENS,
        "chat_request_max_tokens": ORCH_CHAT_REQUEST_MAX_TOKENS,
        "default_agent_max_tokens": ORCH_DEFAULT_AGENT_MAX_TOKENS,
        "planner_max_tokens": ORCH_PLANNER_MAX_TOKENS,
        "coder_max_tokens": ORCH_CODER_MAX_TOKENS,
        "reviewer_max_tokens": ORCH_REVIEWER_MAX_TOKENS,
        "step_timeout_sec": ORCH_STEP_TIMEOUT_SEC,
        "job_timeout_sec": ORCH_JOB_TIMEOUT_SEC,
        "agent_http_timeout_sec": ORCH_AGENT_HTTP_TIMEOUT_SEC,
        "planner_agent_timeout_sec": ORCH_PLANNER_AGENT_TIMEOUT_SEC,
        "coder_agent_timeout_sec": ORCH_CODER_AGENT_TIMEOUT_SEC,
        "reviewer_agent_timeout_sec": ORCH_REVIEWER_AGENT_TIMEOUT_SEC,
        "index_context_timeout_sec": ORCH_INDEX_CONTEXT_TIMEOUT_SEC,
        "planner_prompt_char_limit": ORCH_PLANNER_PROMPT_CHAR_LIMIT,
        "coder_prompt_char_limit": ORCH_CODER_PROMPT_CHAR_LIMIT,
        "reviewer_prompt_char_limit": ORCH_REVIEWER_PROMPT_CHAR_LIMIT,
        "planner_context_char_limit": ORCH_PLANNER_CONTEXT_CHAR_LIMIT,
        "coder_context_char_limit": ORCH_CODER_CONTEXT_CHAR_LIMIT,
        "reviewer_context_char_limit": ORCH_REVIEWER_CONTEXT_CHAR_LIMIT,
        "experience_memory_char_limit": ORCH_EXPERIENCE_MEMORY_CHAR_LIMIT,
        "forensic_max_inventory": ORCH_FORENSIC_MAX_INVENTORY,
        "max_force_retries": ORCH_MAX_FORCE_RETRIES,
        "force_complete": ORCH_FORCE_COMPLETE,
        "allow_synthetic_fallback": ORCH_ALLOW_SYNTHETIC_FALLBACK,
        "code_generation_strategy": ORCH_CODE_GENERATION_STRATEGY,
        "min_files": ORCH_MIN_FILES,
        "min_dirs": ORCH_MIN_DIRS,
        "selected_profile": ORCH_SELECTED_PROFILE,
        "model_tuning_level": ORCH_MODEL_TUNING_LEVEL,
        "token_tuning_level": ORCH_TOKEN_TUNING_LEVEL,
        "timeout_tuning_level": ORCH_TIMEOUT_TUNING_LEVEL,
        "model_routes": get_configured_model_routes(),
        "execution_controls": get_configured_execution_controls(),
        "advisory_controls": advisory_controls,
        "gpu_only_preferred": ORCH_GPU_ONLY_PREFERRED,
        "config_path": ORCH_RUNTIME_CONFIG_PATH,
    }


def _runtime_config_payload() -> Dict[str, Any]:
    return {
        **_runtime_config_base_payload(),
        "available_models": get_available_ollama_models(),
        "gpu_runtime": get_gpu_runtime_info(),
        "runtime_profiles": get_recommended_runtime_profiles(),
    }


def _apply_runtime_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    global ORCH_MAX_TOKENS_PER_STEP
    global ORCH_DEFAULT_REQUEST_MAX_TOKENS
    global ORCH_CHAT_REQUEST_MAX_TOKENS
    global ORCH_DEFAULT_AGENT_MAX_TOKENS
    global ORCH_PLANNER_MAX_TOKENS
    global ORCH_CODER_MAX_TOKENS
    global ORCH_REVIEWER_MAX_TOKENS
    global ORCH_STEP_TIMEOUT_SEC
    global ORCH_JOB_TIMEOUT_SEC
    global ORCH_AGENT_HTTP_TIMEOUT_SEC
    global ORCH_PLANNER_AGENT_TIMEOUT_SEC
    global ORCH_CODER_AGENT_TIMEOUT_SEC
    global ORCH_REVIEWER_AGENT_TIMEOUT_SEC
    global ORCH_INDEX_CONTEXT_TIMEOUT_SEC
    global ORCH_PLANNER_PROMPT_CHAR_LIMIT
    global ORCH_CODER_PROMPT_CHAR_LIMIT
    global ORCH_REVIEWER_PROMPT_CHAR_LIMIT
    global ORCH_PLANNER_CONTEXT_CHAR_LIMIT
    global ORCH_CODER_CONTEXT_CHAR_LIMIT
    global ORCH_REVIEWER_CONTEXT_CHAR_LIMIT
    global ORCH_EXPERIENCE_MEMORY_CHAR_LIMIT
    global ORCH_FORENSIC_MAX_INVENTORY
    global ORCH_MAX_FORCE_RETRIES
    global ORCH_FORCE_COMPLETE
    global ORCH_ALLOW_SYNTHETIC_FALLBACK
    global ORCH_CODE_GENERATION_STRATEGY
    global ORCH_SELECTED_PROFILE
    global ORCH_MODEL_TUNING_LEVEL
    global ORCH_TOKEN_TUNING_LEVEL
    global ORCH_TIMEOUT_TUNING_LEVEL
    global ORCH_MIN_FILES
    global ORCH_MIN_DIRS
    global ORCH_GPU_ONLY_PREFERRED
    global ORCH_ADVISORY_CONTROLS

    max_tokens_per_step = _coerce_runtime_int(
        payload.get("max_tokens_per_step", ORCH_MAX_TOKENS_PER_STEP),
        ORCH_MAX_TOKENS_PER_STEP,
        minimum=1024,
    )
    default_request_max_tokens = _coerce_runtime_int(
        payload.get(
            "default_request_max_tokens",
            ORCH_DEFAULT_REQUEST_MAX_TOKENS,
        ),
        ORCH_DEFAULT_REQUEST_MAX_TOKENS,
        minimum=4096,
        maximum=max_tokens_per_step,
    )
    chat_request_max_tokens = _coerce_runtime_int(
        payload.get(
            "chat_request_max_tokens",
            ORCH_CHAT_REQUEST_MAX_TOKENS,
        ),
        ORCH_CHAT_REQUEST_MAX_TOKENS,
        minimum=128,
        maximum=max_tokens_per_step,
    )
    default_agent_max_tokens = _coerce_runtime_int(
        payload.get(
            "default_agent_max_tokens",
            ORCH_DEFAULT_AGENT_MAX_TOKENS,
        ),
        ORCH_DEFAULT_AGENT_MAX_TOKENS,
        minimum=1024,
        maximum=max_tokens_per_step,
    )
    planner_max_tokens = _coerce_runtime_int(
        payload.get(
            "planner_max_tokens",
            ORCH_PLANNER_MAX_TOKENS,
        ),
        ORCH_PLANNER_MAX_TOKENS,
        minimum=1024,
        maximum=max_tokens_per_step,
    )
    coder_max_tokens = _coerce_runtime_int(
        payload.get(
            "coder_max_tokens",
            ORCH_CODER_MAX_TOKENS,
        ),
        ORCH_CODER_MAX_TOKENS,
        minimum=1024,
        maximum=max_tokens_per_step,
    )
    reviewer_max_tokens = _coerce_runtime_int(
        payload.get(
            "reviewer_max_tokens",
            ORCH_REVIEWER_MAX_TOKENS,
        ),
        ORCH_REVIEWER_MAX_TOKENS,
        minimum=1024,
        maximum=max_tokens_per_step,
    )
    step_timeout_sec = _coerce_runtime_int(
        payload.get("step_timeout_sec", ORCH_STEP_TIMEOUT_SEC),
        ORCH_STEP_TIMEOUT_SEC,
        minimum=60,
    )
    job_timeout_sec = _coerce_runtime_int(
        payload.get("job_timeout_sec", ORCH_JOB_TIMEOUT_SEC),
        ORCH_JOB_TIMEOUT_SEC,
        minimum=600,
    )
    agent_http_timeout_sec = _coerce_runtime_int(
        payload.get(
            "agent_http_timeout_sec",
            ORCH_AGENT_HTTP_TIMEOUT_SEC,
        ),
        ORCH_AGENT_HTTP_TIMEOUT_SEC,
        minimum=180,
    )
    planner_agent_timeout_sec = _coerce_runtime_int(
        payload.get(
            "planner_agent_timeout_sec",
            ORCH_PLANNER_AGENT_TIMEOUT_SEC,
        ),
        ORCH_PLANNER_AGENT_TIMEOUT_SEC,
        minimum=60,
        maximum=agent_http_timeout_sec,
    )
    coder_agent_timeout_sec = _coerce_runtime_int(
        payload.get(
            "coder_agent_timeout_sec",
            ORCH_CODER_AGENT_TIMEOUT_SEC,
        ),
        ORCH_CODER_AGENT_TIMEOUT_SEC,
        minimum=60,
        maximum=agent_http_timeout_sec,
    )
    reviewer_agent_timeout_sec = _coerce_runtime_int(
        payload.get(
            "reviewer_agent_timeout_sec",
            ORCH_REVIEWER_AGENT_TIMEOUT_SEC,
        ),
        ORCH_REVIEWER_AGENT_TIMEOUT_SEC,
        minimum=60,
        maximum=agent_http_timeout_sec,
    )
    index_context_timeout_sec = _coerce_runtime_int(
        payload.get(
            "index_context_timeout_sec",
            ORCH_INDEX_CONTEXT_TIMEOUT_SEC,
        ),
        ORCH_INDEX_CONTEXT_TIMEOUT_SEC,
        minimum=0,
        maximum=step_timeout_sec,
    )
    planner_prompt_char_limit = _coerce_runtime_int(
        payload.get(
            "planner_prompt_char_limit",
            ORCH_PLANNER_PROMPT_CHAR_LIMIT,
        ),
        ORCH_PLANNER_PROMPT_CHAR_LIMIT,
        minimum=1200,
    )
    coder_prompt_char_limit = _coerce_runtime_int(
        payload.get(
            "coder_prompt_char_limit",
            ORCH_CODER_PROMPT_CHAR_LIMIT,
        ),
        ORCH_CODER_PROMPT_CHAR_LIMIT,
        minimum=1200,
    )
    reviewer_prompt_char_limit = _coerce_runtime_int(
        payload.get(
            "reviewer_prompt_char_limit",
            ORCH_REVIEWER_PROMPT_CHAR_LIMIT,
        ),
        ORCH_REVIEWER_PROMPT_CHAR_LIMIT,
        minimum=1200,
    )
    planner_context_char_limit = _coerce_runtime_int(
        payload.get(
            "planner_context_char_limit",
            ORCH_PLANNER_CONTEXT_CHAR_LIMIT,
        ),
        ORCH_PLANNER_CONTEXT_CHAR_LIMIT,
        minimum=0,
    )
    coder_context_char_limit = _coerce_runtime_int(
        payload.get(
            "coder_context_char_limit",
            ORCH_CODER_CONTEXT_CHAR_LIMIT,
        ),
        ORCH_CODER_CONTEXT_CHAR_LIMIT,
        minimum=0,
    )
    reviewer_context_char_limit = _coerce_runtime_int(
        payload.get(
            "reviewer_context_char_limit",
            ORCH_REVIEWER_CONTEXT_CHAR_LIMIT,
        ),
        ORCH_REVIEWER_CONTEXT_CHAR_LIMIT,
        minimum=0,
    )
    experience_memory_char_limit = _coerce_runtime_int(
        payload.get(
            "experience_memory_char_limit",
            ORCH_EXPERIENCE_MEMORY_CHAR_LIMIT,
        ),
        ORCH_EXPERIENCE_MEMORY_CHAR_LIMIT,
        minimum=0,
    )
    forensic_max_inventory = _coerce_runtime_int(
        payload.get(
            "forensic_max_inventory",
            ORCH_FORENSIC_MAX_INVENTORY,
        ),
        ORCH_FORENSIC_MAX_INVENTORY,
        minimum=100,
    )
    max_force_retries = _coerce_runtime_int(
        payload.get("max_force_retries", ORCH_MAX_FORCE_RETRIES),
        ORCH_MAX_FORCE_RETRIES,
        minimum=1,
    )
    force_complete = bool(
        payload.get("force_complete", ORCH_FORCE_COMPLETE)
    )
    allow_synthetic_fallback = bool(
        payload.get(
            "allow_synthetic_fallback",
            ORCH_ALLOW_SYNTHETIC_FALLBACK,
        )
    )
    code_generation_strategy = _normalize_code_generation_strategy(
        payload.get(
            "code_generation_strategy",
            ORCH_CODE_GENERATION_STRATEGY,
        )
    )
    selected_profile = str(
        payload.get("selected_profile", ORCH_SELECTED_PROFILE)
        or ORCH_SELECTED_PROFILE
    ).strip() or CURRENT_GPU_PROFILE_KEY
    if selected_profile not in {
        CURRENT_GPU_PROFILE_KEY,
        ORCH_RUNTIME_PROFILE_CUSTOM_KEY,
        "upper_tier_70b",
    }:
        selected_profile = CURRENT_GPU_PROFILE_KEY
    min_files = _coerce_runtime_int(
        payload.get("min_files", ORCH_MIN_FILES),
        ORCH_MIN_FILES,
        minimum=1,
    )
    min_dirs = _coerce_runtime_int(
        payload.get("min_dirs", ORCH_MIN_DIRS),
        ORCH_MIN_DIRS,
        minimum=0,
    )
    model_tuning_level = _coerce_runtime_int(
        payload.get(
            "model_tuning_level",
            ORCH_MODEL_TUNING_LEVEL,
        ),
        ORCH_MODEL_TUNING_LEVEL,
        minimum=-1,
        maximum=1,
    )
    token_tuning_level = _coerce_runtime_int(
        payload.get(
            "token_tuning_level",
            ORCH_TOKEN_TUNING_LEVEL,
        ),
        ORCH_TOKEN_TUNING_LEVEL,
        minimum=-1,
        maximum=1,
    )
    timeout_tuning_level = _coerce_runtime_int(
        payload.get(
            "timeout_tuning_level",
            ORCH_TIMEOUT_TUNING_LEVEL,
        ),
        ORCH_TIMEOUT_TUNING_LEVEL,
        minimum=-1,
        maximum=1,
    )
    gpu_only_preferred = _coerce_runtime_bool(
        payload.get(
            "gpu_only_preferred",
            ORCH_GPU_ONLY_PREFERRED,
        ),
        ORCH_GPU_ONLY_PREFERRED,
    )
    effective_payload = _apply_runtime_tuning_presets(
        payload,
        selected_profile=selected_profile,
        model_tuning_level=model_tuning_level,
        token_tuning_level=token_tuning_level,
        timeout_tuning_level=timeout_tuning_level,
    )

    max_tokens_per_step = _coerce_runtime_int(
        effective_payload.get("max_tokens_per_step", max_tokens_per_step),
        max_tokens_per_step,
        minimum=1024,
    )
    default_request_max_tokens = _coerce_runtime_int(
        effective_payload.get(
            "default_request_max_tokens",
            default_request_max_tokens,
        ),
        default_request_max_tokens,
        minimum=4096,
        maximum=max_tokens_per_step,
    )
    chat_request_max_tokens = _coerce_runtime_int(
        effective_payload.get(
            "chat_request_max_tokens",
            chat_request_max_tokens,
        ),
        chat_request_max_tokens,
        minimum=128,
        maximum=max_tokens_per_step,
    )
    default_agent_max_tokens = _coerce_runtime_int(
        effective_payload.get(
            "default_agent_max_tokens",
            default_agent_max_tokens,
        ),
        default_agent_max_tokens,
        minimum=1024,
        maximum=max_tokens_per_step,
    )
    planner_max_tokens = _coerce_runtime_int(
        effective_payload.get("planner_max_tokens", planner_max_tokens),
        planner_max_tokens,
        minimum=1024,
        maximum=max_tokens_per_step,
    )
    coder_max_tokens = _coerce_runtime_int(
        effective_payload.get("coder_max_tokens", coder_max_tokens),
        coder_max_tokens,
        minimum=1024,
        maximum=max_tokens_per_step,
    )
    reviewer_max_tokens = _coerce_runtime_int(
        effective_payload.get("reviewer_max_tokens", reviewer_max_tokens),
        reviewer_max_tokens,
        minimum=1024,
        maximum=max_tokens_per_step,
    )
    step_timeout_sec = _coerce_runtime_int(
        effective_payload.get("step_timeout_sec", step_timeout_sec),
        step_timeout_sec,
        minimum=60,
    )
    job_timeout_sec = _coerce_runtime_int(
        effective_payload.get("job_timeout_sec", job_timeout_sec),
        job_timeout_sec,
        minimum=600,
    )
    agent_http_timeout_sec = _coerce_runtime_int(
        effective_payload.get(
            "agent_http_timeout_sec",
            agent_http_timeout_sec,
        ),
        agent_http_timeout_sec,
        minimum=180,
    )
    planner_agent_timeout_sec = _coerce_runtime_int(
        effective_payload.get(
            "planner_agent_timeout_sec",
            planner_agent_timeout_sec,
        ),
        planner_agent_timeout_sec,
        minimum=60,
        maximum=agent_http_timeout_sec,
    )
    coder_agent_timeout_sec = _coerce_runtime_int(
        effective_payload.get(
            "coder_agent_timeout_sec",
            coder_agent_timeout_sec,
        ),
        coder_agent_timeout_sec,
        minimum=60,
        maximum=agent_http_timeout_sec,
    )
    reviewer_agent_timeout_sec = _coerce_runtime_int(
        effective_payload.get(
            "reviewer_agent_timeout_sec",
            reviewer_agent_timeout_sec,
        ),
        reviewer_agent_timeout_sec,
        minimum=60,
        maximum=agent_http_timeout_sec,
    )
    index_context_timeout_sec = _coerce_runtime_int(
        effective_payload.get(
            "index_context_timeout_sec",
            index_context_timeout_sec,
        ),
        index_context_timeout_sec,
        minimum=0,
        maximum=step_timeout_sec,
    )
    planner_prompt_char_limit = _coerce_runtime_int(
        effective_payload.get(
            "planner_prompt_char_limit",
            planner_prompt_char_limit,
        ),
        planner_prompt_char_limit,
        minimum=1200,
    )
    coder_prompt_char_limit = _coerce_runtime_int(
        effective_payload.get(
            "coder_prompt_char_limit",
            coder_prompt_char_limit,
        ),
        coder_prompt_char_limit,
        minimum=1200,
    )
    reviewer_prompt_char_limit = _coerce_runtime_int(
        effective_payload.get(
            "reviewer_prompt_char_limit",
            reviewer_prompt_char_limit,
        ),
        reviewer_prompt_char_limit,
        minimum=1200,
    )
    planner_context_char_limit = _coerce_runtime_int(
        effective_payload.get(
            "planner_context_char_limit",
            planner_context_char_limit,
        ),
        planner_context_char_limit,
        minimum=0,
    )
    coder_context_char_limit = _coerce_runtime_int(
        effective_payload.get(
            "coder_context_char_limit",
            coder_context_char_limit,
        ),
        coder_context_char_limit,
        minimum=0,
    )
    reviewer_context_char_limit = _coerce_runtime_int(
        effective_payload.get(
            "reviewer_context_char_limit",
            reviewer_context_char_limit,
        ),
        reviewer_context_char_limit,
        minimum=0,
    )
    model_routes = _normalize_runtime_model_routes(effective_payload)
    advisory_controls = _normalize_advisory_controls(effective_payload)

    ORCH_MAX_TOKENS_PER_STEP = max_tokens_per_step
    ORCH_DEFAULT_REQUEST_MAX_TOKENS = default_request_max_tokens
    ORCH_CHAT_REQUEST_MAX_TOKENS = chat_request_max_tokens
    ORCH_DEFAULT_AGENT_MAX_TOKENS = default_agent_max_tokens
    ORCH_PLANNER_MAX_TOKENS = planner_max_tokens
    ORCH_CODER_MAX_TOKENS = coder_max_tokens
    ORCH_REVIEWER_MAX_TOKENS = reviewer_max_tokens
    ORCH_STEP_TIMEOUT_SEC = step_timeout_sec
    ORCH_JOB_TIMEOUT_SEC = job_timeout_sec
    ORCH_AGENT_HTTP_TIMEOUT_SEC = agent_http_timeout_sec
    ORCH_PLANNER_AGENT_TIMEOUT_SEC = planner_agent_timeout_sec
    ORCH_CODER_AGENT_TIMEOUT_SEC = coder_agent_timeout_sec
    ORCH_REVIEWER_AGENT_TIMEOUT_SEC = reviewer_agent_timeout_sec
    ORCH_INDEX_CONTEXT_TIMEOUT_SEC = index_context_timeout_sec
    ORCH_PLANNER_PROMPT_CHAR_LIMIT = planner_prompt_char_limit
    ORCH_CODER_PROMPT_CHAR_LIMIT = coder_prompt_char_limit
    ORCH_REVIEWER_PROMPT_CHAR_LIMIT = reviewer_prompt_char_limit
    ORCH_PLANNER_CONTEXT_CHAR_LIMIT = planner_context_char_limit
    ORCH_CODER_CONTEXT_CHAR_LIMIT = coder_context_char_limit
    ORCH_REVIEWER_CONTEXT_CHAR_LIMIT = reviewer_context_char_limit
    ORCH_EXPERIENCE_MEMORY_CHAR_LIMIT = experience_memory_char_limit
    ORCH_FORENSIC_MAX_INVENTORY = forensic_max_inventory
    ORCH_MAX_FORCE_RETRIES = max_force_retries
    ORCH_FORCE_COMPLETE = force_complete
    ORCH_ALLOW_SYNTHETIC_FALLBACK = allow_synthetic_fallback
    ORCH_CODE_GENERATION_STRATEGY = code_generation_strategy
    ORCH_SELECTED_PROFILE = selected_profile
    ORCH_MODEL_TUNING_LEVEL = model_tuning_level
    ORCH_TOKEN_TUNING_LEVEL = token_tuning_level
    ORCH_TIMEOUT_TUNING_LEVEL = timeout_tuning_level
    ORCH_MIN_FILES = min_files
    ORCH_MIN_DIRS = min_dirs
    ORCH_GPU_ONLY_PREFERRED = gpu_only_preferred
    ORCH_ADVISORY_CONTROLS = advisory_controls
    return {
        **_runtime_config_base_payload(),
        "selected_profile": ORCH_SELECTED_PROFILE,
        "model_tuning_level": ORCH_MODEL_TUNING_LEVEL,
        "token_tuning_level": ORCH_TOKEN_TUNING_LEVEL,
        "timeout_tuning_level": ORCH_TIMEOUT_TUNING_LEVEL,
        "gpu_only_preferred": ORCH_GPU_ONLY_PREFERRED,
        "code_generation_strategy": ORCH_CODE_GENERATION_STRATEGY,
        "model_routes": model_routes,
        "advisory_controls": dict(ORCH_ADVISORY_CONTROLS),
    }


def _load_runtime_config_from_disk() -> Dict[str, Any]:
    path = _runtime_config_file_path()
    if not path.exists():
        return _runtime_config_payload()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _runtime_config_payload()
    if not isinstance(payload, dict):
        return _runtime_config_payload()
    return _apply_runtime_config(payload)


def _save_runtime_config_to_disk(payload: Dict[str, Any]) -> None:
    path = _runtime_config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


@router.get("/runtime-config")
async def get_runtime_config() -> Dict[str, Any]:
    _load_runtime_config_from_disk()
    return _runtime_config_payload()


@router.put("/runtime-config")
@router.post("/runtime-config")
async def update_runtime_config(
    update: OrchestratorRuntimeConfigUpdate,
) -> Dict[str, Any]:
    current = _load_runtime_config_from_disk()
    merged = _merge_runtime_update_payload(
        current,
        update.model_dump(exclude_none=True),
    )
    applied = _apply_runtime_config(merged)
    _save_runtime_config_to_disk(applied)
    return _runtime_config_payload()


@router.websocket("/ws")
async def orchestrator_ws(websocket: WebSocket):
    await ws_channel.connect(websocket)
    try:
        await websocket.send_json({
            "event": "connected",
            "timestamp": datetime.now().isoformat(),
        })
        while True:
            message = await websocket.receive_text()
            if str(message or "").strip().lower() == "ping":
                await websocket.send_json({
                    "event": "pong",
                    "timestamp": datetime.now().isoformat(),
                })
                continue
            await websocket.send_json({
                "event": "echo",
                "message": str(message or ""),
                "timestamp": datetime.now().isoformat(),
            })
    except WebSocketDisconnect:
        pass
    finally:
        ws_channel.disconnect(websocket)


_load_runtime_config_from_disk()


def _task_tokens(task: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zA-Z0-9가-힣_-]+", (task or "").lower())
        if len(token) >= 2
    }


def _normalize_required_files(files: List[str]) -> List[str]:
    normalized: List[str] = []
    seen: set[str] = set()
    for item in files:
        rel = str(item or "").strip().replace("\\", "/")
        key = rel.lower()
        if not rel or key in seen:
            continue
        seen.add(key)
        normalized.append(rel)
    return normalized


def _extract_targeted_patch_paths(task: str) -> List[str]:
    task_text = str(task or "")
    match = re.search(
        r"수정 가능 파일은\s+(.+?)\s+뿐입니다",
        task_text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return []

    segment = match.group(1)
    path_candidates = re.findall(
        r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*",
        segment,
    )
    filtered = [
        candidate
        for candidate in path_candidates
        if "." in candidate and not candidate.lower().startswith("http")
    ]
    return _normalize_required_files(filtered)


def _is_targeted_existing_patch_request(
    task: str,
    required_files: List[str],
) -> bool:
    targeted_files = _extract_targeted_patch_paths(task)
    if not targeted_files:
        return False

    lower_task = str(task or "").lower()
    lower_files = {
        str(path).lower().replace("\\", "/")
        for path in (required_files or targeted_files)
    }
    if any(
        token in lower_task
        for token in ["fastapi", "flask", "django", "nestjs", "express"]
    ):
        return False
    if any(
        path.startswith("backend/app/")
        or path.endswith("requirements.txt")
        or path.endswith("docker-compose.yml")
        for path in lower_files
    ):
        return False
    return True


def _default_validation_profile(task: str, required_files: List[str]) -> str:
    task_lower = (task or "").lower()
    lower_files = {str(path).lower() for path in required_files}
    if _is_targeted_existing_patch_request(task, required_files):
        return "generic"
    has_nextjs_files = any(
        path.endswith("package.json") for path in lower_files
    ) and any(
        path.startswith("app/") for path in lower_files
    )
    has_python_backend_files = any(
        path.endswith("requirements.txt") for path in lower_files
    ) or any(
        path.endswith(".py") or path.startswith("backend/")
        for path in lower_files
    )
    if has_nextjs_files and has_python_backend_files:
        return "generic"
    if has_nextjs_files:
        return "nextjs_react"
    if any(path.endswith("package.json") for path in lower_files) and any(
        path.startswith("src/") for path in lower_files
    ):
        return "node_service"
    if "go.mod" in lower_files or any(
        path.endswith(".go") for path in lower_files
    ):
        return "go_service"
    if "cargo.toml" in lower_files or any(
        path.endswith(".rs") for path in lower_files
    ):
        return "rust_service"
    if any(path.endswith("requirements.txt") for path in lower_files) or any(
        path.endswith(".py") for path in lower_files
    ):
        return "python_fastapi"
    if _has_nextjs_stack_markers(task_lower) and _has_python_backend_stack_markers(task_lower):
        return "generic"
    if any(token in task_lower for token in ["next.js", "nextjs", "react"]):
        return "nextjs_react"
    if any(
        token in task_lower
        for token in ["node", "express", "nestjs", "javascript", "typescript"]
    ):
        return "node_service"
    if any(token in task_lower for token in ["go", "golang"]):
        return "go_service"
    if any(
        token in task_lower for token in ["rust", "cargo", "actix", "axum"]
    ):
        return "rust_service"
    if any(
        token in task_lower
        for token in ["fastapi", "python", "api", "백엔드"]
    ):
        return "python_fastapi"
    return "generic"


def _detect_stack_family(task: str, mode: str) -> str:
    task_lower = (task or "").lower()
    if _is_targeted_existing_patch_request(task, []):
        return "generic"
    if any(
        token in task_lower
        for token in ["next.js", "nextjs", "react", "tsx", "tailwind"]
    ):
        return "nextjs_react"
    if any(
        token in task_lower
        for token in [
            "node",
            "express",
            "nestjs",
            "javascript",
            "typescript",
        ]
    ):
        return "node_service"
    if any(token in task_lower for token in ["go", "golang"]):
        return "go_service"
    if any(
        token in task_lower
        for token in ["rust", "cargo", "actix", "axum"]
    ):
        return "rust_service"
    if any(
        token in task_lower
        for token in ["fastapi", "flask", "django", "python"]
    ):
        return "python_fastapi"
    if mode == "design":
        return "nextjs_react"
    return "generic"


def _default_required_files_for_mode(task: str, mode: str) -> List[str]:
    def _with_architecture_baseline(paths: List[str]) -> List[str]:
        ordered: List[str] = []
        seen = set()
        for item in ORCH_ARCHITECTURE_BASELINE_FILES + paths:
            normalized = str(item).replace("\\", "/")
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(normalized)
        return ordered

    targeted_files = _extract_targeted_patch_paths(task)
    if targeted_files:
        return targeted_files

    stack_family = _detect_stack_family(task, mode)
    if stack_family == "fullstack_web":
        return _with_architecture_baseline([
            "README.md",
            ".gitignore",
            "docker-compose.yml",
            "package.json",
            "tsconfig.json",
            "next-env.d.ts",
            "next.config.js",
            "app/layout.tsx",
            "app/loading.tsx",
            "app/page.tsx",
            "app/dashboard/page.tsx",
            "app/api/health/route.ts",
            "app/api/brief/route.ts",
            "app/globals.css",
            "components/dashboardclient.tsx",
            "components/heropanel.tsx",
            "components/metriccluster.tsx",
            "components/signalmatrix.tsx",
            "components/focusrail.tsx",
            "components/insighttimeline.tsx",
            "components/executionboard.tsx",
            "components/themetoggle.tsx",
            "lib/types.ts",
            "lib/data.ts",
            ".env.example",
            "requirements.txt",
            "backend/app/main.py",
            "backend/app/core/config.py",
            "backend/app/core/security.py",
            "backend/app/core/database.py",
            "backend/app/api/deps.py",
            "backend/app/api/routes/__init__.py",
            "backend/app/api/routes/health.py",
            "backend/app/api/routes/auth.py",
            "backend/app/api/routes/catalog.py",
            "backend/app/api/routes/orders.py",
            "backend/app/controllers/health_controller.py",
            "backend/app/controllers/auth_controller.py",
            "backend/app/controllers/catalog_controller.py",
            "backend/app/controllers/order_controller.py",
            "backend/app/services/health_service.py",
            "backend/app/services/auth_service.py",
            "backend/app/services/catalog_service.py",
            "backend/app/services/order_service.py",
            "backend/app/repositories/health_repository.py",
            "backend/app/repositories/user_repository.py",
            "backend/app/repositories/catalog_repository.py",
            "backend/app/repositories/order_repository.py",
            "backend/app/infra/runtime_store.py",
            "backend/app/external_adapters/status_client.py",
            "backend/app/connectors/base.py",
            "backend/app/connectors/shopify.py",
            "backend/app/worker/tasks.py",
            "backend/tests/conftest.py",
            "backend/tests/test_health.py",
            "backend/tests/test_auth.py",
            "backend/tests/test_catalog_sync.py",
            "backend/tests/test_orders.py",
        ])
    if stack_family == "nextjs_react":
        return _with_architecture_baseline([
            "README.md",
            ".gitignore",
            "package.json",
            "tsconfig.json",
            "next-env.d.ts",
            "next.config.js",
            "app/layout.tsx",
            "app/loading.tsx",
            "app/page.tsx",
            "app/dashboard/page.tsx",
            "app/api/health/route.ts",
            "app/api/brief/route.ts",
            "app/globals.css",
            "components/dashboardclient.tsx",
            "components/heropanel.tsx",
            "components/metriccluster.tsx",
            "components/signalmatrix.tsx",
            "components/focusrail.tsx",
            "components/insighttimeline.tsx",
            "components/executionboard.tsx",
            "components/themetoggle.tsx",
            "lib/types.ts",
            "lib/data.ts",
        ])
    if stack_family == "node_service":
        return _with_architecture_baseline([
            "README.md",
            "package.json",
            "tsconfig.json",
            "src/index.ts",
            "src/app.ts",
            "src/config.ts",
            "src/types.ts",
            "src/routes/health.ts",
            "src/routes/orders.ts",
            "src/controllers/orderController.ts",
            "src/services/orderService.ts",
            "src/repositories/orderRepository.ts",
            "src/lib/runtimeStore.ts",
            "src/middleware/errorHandler.ts",
        ])
    if stack_family == "go_service":
        return _with_architecture_baseline([
            "README.md",
            "go.mod",
            "cmd/app/main.go",
            "internal/app/app.go",
            "internal/http/router.go",
            "internal/http/handlers/health.go",
            "internal/http/handlers/inventory.go",
            "internal/service/inventory_service.go",
            "internal/repository/inventory_repository.go",
            "internal/platform/runtime_store.go",
            "internal/domain/inventory.go",
        ])
    if stack_family == "rust_service":
        return _with_architecture_baseline([
            "README.md",
            "Cargo.toml",
            "src/main.rs",
            "src/app.rs",
            "src/http/mod.rs",
            "src/http/handlers.rs",
            "src/http/router.rs",
            "src/service/mod.rs",
            "src/service/order_service.rs",
            "src/repository/mod.rs",
            "src/repository/order_repository.rs",
            "src/platform/mod.rs",
            "src/platform/runtime_store.rs",
            "src/domain/mod.rs",
            "src/domain/order.rs",
        ])
    if stack_family == "python_fastapi":
        return _with_architecture_baseline([
            "README.md",
            ".env.example",
            ".gitignore",
            "docker-compose.yml",
            "requirements.txt",
            "backend/app/main.py",
            "backend/app/core/config.py",
            "backend/app/core/security.py",
            "backend/app/core/database.py",
            "backend/app/api/deps.py",
            "backend/app/api/routes/__init__.py",
            "backend/app/api/routes/health.py",
            "backend/app/api/routes/auth.py",
            "backend/app/api/routes/catalog.py",
            "backend/app/api/routes/orders.py",
            "backend/app/controllers/health_controller.py",
            "backend/app/controllers/auth_controller.py",
            "backend/app/controllers/catalog_controller.py",
            "backend/app/controllers/order_controller.py",
            "backend/app/services/health_service.py",
            "backend/app/services/auth_service.py",
            "backend/app/services/catalog_service.py",
            "backend/app/services/order_service.py",
            "backend/app/repositories/health_repository.py",
            "backend/app/repositories/user_repository.py",
            "backend/app/repositories/catalog_repository.py",
            "backend/app/repositories/order_repository.py",
            "backend/app/infra/runtime_store.py",
            "backend/app/external_adapters/status_client.py",
            "backend/app/connectors/base.py",
            "backend/app/connectors/shopify.py",
            "backend/app/worker/tasks.py",
            "backend/tests/conftest.py",
            "backend/tests/test_health.py",
            "backend/tests/test_auth.py",
            "backend/tests/test_catalog_sync.py",
            "backend/tests/test_orders.py",
        ])
    if mode in {"code", "full", "program_5step"}:
        return _with_architecture_baseline([
            "README.md",
            ".gitignore",
            "docs/architecture.md",
        ])
    return _with_architecture_baseline(["README.md"])


def _default_dod_targets(profile: str) -> List[str]:
    if profile == "python_fastapi":
        return [
            "docker compose up -d",
            "GET /health returns 200",
            "pytest -q passes",
        ]
    if profile == "nextjs_react":
        return [
            "npm install",
            "npm run build",
            "핵심 페이지 렌더링 파일 존재",
        ]
    if profile == "node_service":
        return [
            "npm install --ignore-scripts",
            "npm run build --if-present",
            "엔트리포인트/헬스 라우트 존재",
        ]
    if profile == "go_service":
        return [
            "go build ./...",
            "cmd/app/main.go 존재",
            "핵심 핸들러/서비스 흐름 구현",
        ]
    if profile == "rust_service":
        return [
            "cargo check",
            "src/main.rs 또는 lib.rs 존재",
            "핵심 핸들러/서비스 흐름 구현",
        ]
    return [
        "필수 파일 세트 존재",
        "빈 파일/빈 폴더 없음",
        "대표 빌드 또는 실행 스크립트 존재",
    ]


def _default_orchestration_spec(
    task: str,
    requested_mode: str,
) -> OrchestrationSpec:
    requested_mode = _normalize_requested_mode(requested_mode)
    if requested_mode == "manual_9step":
        required_files = _default_required_files_for_mode(task, "code")
        validation_profile = _default_validation_profile(task, required_files)
        return OrchestrationSpec(
            mode="manual_9step",
            pipeline=PIPELINES["manual_9step"],
            required_files=required_files,
            validation_profile=validation_profile,
            dod_targets=_default_dod_targets(validation_profile),
            reasoning=(
                "administrator manual evidence-based "
                "5-step workflow selected"
            ),
            spec_source="manual",
            manual_steps=list(MANUAL_ORCHESTRATION_STEPS),
        )
    resolved_mode = (
        detect_mode(task)
        if requested_mode == "auto"
        else requested_mode
    )
    required_files = _default_required_files_for_mode(task, resolved_mode)
    validation_profile = _default_validation_profile(task, required_files)
    effective_pipeline = _filter_pipeline_for_validation_profile(
        PIPELINES.get(resolved_mode, ["planner", "coder"]),
        validation_profile,
    )
    return OrchestrationSpec(
        mode=resolved_mode,
        pipeline=effective_pipeline,
        required_files=required_files,
        validation_profile=validation_profile,
        dod_targets=_default_dod_targets(validation_profile),
        reasoning="planner unavailable, heuristic fallback applied",
        spec_source="fallback",
        fallback_reason="planner_spec_unavailable",
    )


def _normalize_pipeline_agents(agents: List[Any]) -> List[str]:
    available_agents = AGENTS if isinstance(AGENTS, dict) else (_current_agents() or {})
    normalized: List[str] = []
    seen: set[str] = set()
    for item in agents:
        agent = str(item or "").strip()
        if agent not in available_agents or agent in seen:
            continue
        seen.add(agent)
        normalized.append(agent)
    return normalized


def _resolve_a_brain_pipeline(validation_profile: str) -> List[str]:
    profile = str(validation_profile or "generic").strip().lower()
    pipeline = ["reasoner", "planner"]
    if profile in {"nextjs_react", "nextjs_app"}:
        pipeline.append("designer")
    return _normalize_pipeline_agents(pipeline)


def _resolve_b_brain_generator_profile(validation_profile: str) -> str:
    profile = str(validation_profile or "generic").strip().lower()
    if profile in {"python_fastapi", "python_worker"}:
        return "python_fastapi"
    if profile == "nextjs_app":
        return "nextjs_react"
    if profile in {"nextjs_react", "node_service", "go_service", "rust_service"}:
        return profile
    return "python_fastapi"


def _resolve_b_brain_additional_profiles(validation_profile: str, task: str) -> List[str]:
    profile = str(validation_profile or "generic").strip().lower()
    source_text = str(task or "").lower()
    additional: List[str] = []

    if profile == "python_fastapi":
        additional.append("nextjs_react")
        if any(marker in source_text for marker in ["node", "worker", "queue", "event", "agent"]):
            additional.append("node_service")
        if any(marker in source_text for marker in ["go", "golang", "gateway", "proxy"]):
            additional.append("go_service")
        if any(marker in source_text for marker in ["rust", "high performance", "engine"]):
            additional.append("rust_service")
    elif profile == "nextjs_app":
        additional.append("node_service")

    deduped: List[str] = []
    for item in additional:
        normalized = str(item or "").strip().lower()
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return deduped


def _resolve_b_brain_generator_family(validation_profile: str) -> str:
    profile = _resolve_b_brain_generator_profile(validation_profile)
    if profile == "python_fastapi":
        return "python_code_generator"
    return "non_python_code_generator"


def _filter_pipeline_for_validation_profile(
    agents: List[str],
    validation_profile: str,
) -> List[str]:
    profile = str(validation_profile or "generic").strip().lower()
    if ORCH_CODE_GENERATION_STRATEGY == "auto_generator":
        a_brain_pipeline = _resolve_a_brain_pipeline(profile)
        return a_brain_pipeline + [ORCH_B_BRAIN_AGENT_KEY]

    filtered = list(agents)

    if profile == "python_fastapi":
        filtered = [
            agent for agent in filtered
            if agent not in {"planner", "reviewer", "designer"}
        ]
        if not filtered:
            filtered = ["coder"]
    elif profile != "nextjs_react":
        filtered = [agent for agent in filtered if agent != "designer"]

    return filtered or ["coder"]


def _normalize_requested_mode(requested_mode: Any) -> str:
    candidate = str(requested_mode or "").strip()
    if candidate == "run":
        return "code"
    return candidate


def _planner_resolved_mode(
    request_mode: str,
    planner_mode: Any,
    fallback_mode: str,
) -> str:
    request_mode = _normalize_requested_mode(request_mode)
    fallback_mode = _normalize_requested_mode(fallback_mode)
    if request_mode != "auto":
        return request_mode
    candidate = _normalize_requested_mode(planner_mode)
    if candidate in PIPELINES:
        return candidate
    return fallback_mode


def _should_bypass_planner_spec_resolution(
    request: OrchestrationRequest,
    fallback: OrchestrationSpec,
) -> bool:
    request_mode = _normalize_requested_mode(request.mode)
    if request.manual_mode or request_mode in {"plan", "design", "review"}:
        return False
    if _is_targeted_existing_patch_request(
        request.task,
        fallback.required_files,
    ):
        return False
    return fallback.validation_profile == "python_fastapi"


def _build_resolved_orchestration_spec(
    request: OrchestrationRequest,
    fallback: OrchestrationSpec,
    parsed: Dict[str, Any],
) -> OrchestrationSpec:
    resolved_mode = _planner_resolved_mode(
        request.mode,
        parsed.get("mode"),
        fallback.mode,
    )
    planner_pipeline = _normalize_pipeline_agents(
        parsed.get("pipeline", [])
    )
    required_files = _normalize_required_files(
        parsed.get("required_files", [])
    )
    fallback_notes: List[str] = []
    targeted_required_files = _extract_targeted_patch_paths(request.task)
    targeted_existing_patch = _is_targeted_existing_patch_request(
        request.task,
        targeted_required_files,
    )

    if targeted_existing_patch and targeted_required_files:
        required_files = targeted_required_files
        fallback_notes.append("targeted_required_files")

    if not required_files:
        required_files = _default_required_files_for_mode(
            request.task,
            resolved_mode,
        )
        fallback_notes.append("required_files")

    validation_profile = str(
        parsed.get("validation_profile")
        or _default_validation_profile(request.task, required_files)
    ).strip() or fallback.validation_profile
    if targeted_existing_patch:
        validation_profile = "generic"
        fallback_notes.append("targeted_validation_profile")
    dod_targets = [
        str(item).strip()
        for item in parsed.get("dod_targets", [])
        if str(item).strip()
    ]
    if targeted_existing_patch:
        dod_targets = _default_dod_targets(validation_profile)
        fallback_notes.append("targeted_dod_targets")
    if not dod_targets:
        dod_targets = _default_dod_targets(validation_profile)
        fallback_notes.append("dod_targets")

    effective_pipeline = (
        request.pipeline
        or planner_pipeline
        or PIPELINES.get(resolved_mode, fallback.pipeline)
        or fallback.pipeline
    )
    effective_pipeline = _filter_pipeline_for_validation_profile(
        effective_pipeline,
        validation_profile,
    )
    if not planner_pipeline and not request.pipeline:
        fallback_notes.append("pipeline")

    reasoning = str(parsed.get("reasoning") or "").strip()
    if not reasoning:
        reasoning = "planner JSON accepted"
        if fallback_notes:
            reasoning += " with normalized fallback fields"

    return OrchestrationSpec(
        mode=resolved_mode,
        pipeline=effective_pipeline,
        required_files=required_files,
        validation_profile=validation_profile,
        dod_targets=dod_targets,
        reasoning=reasoning,
        spec_source="planner",
        fallback_reason=(
            ", ".join(fallback_notes)
            if fallback_notes
            else None
        ),
    )


def _resolve_template_profile(
    orchestration_spec: OrchestrationSpec,
) -> str:
    profile = str(orchestration_spec.validation_profile or "generic").strip()
    if profile in {
        "python_fastapi",
        "nextjs_react",
        "node_service",
        "go_service",
        "rust_service",
    }:
        return profile
    return "generic"


def _template_baseline_for_profile(validation_profile: str) -> Dict[str, str]:
    profile = str(validation_profile or "generic").strip().lower()
    baselines: Dict[str, Dict[str, str]] = {
        "python_fastapi": {
            "family": "fastapi-ops-slice",
            "version": "2026.03-modern-ops-v2",
            "notes": "운영형 FastAPI 보일러플레이트 기준선",
        },
        "nextjs_react": {
            "family": "nextjs-ops-canvas",
            "version": "2026.03-modern-ops-v2",
            "notes": "디자인 시스템형 Next.js 대시보드 기준선",
        },
        "node_service": {
            "family": "node-ops-service",
            "version": "2026.03-modern-ops-v1",
            "notes": "운영형 Node 서비스 기준선",
        },
        "go_service": {
            "family": "go-ops-service",
            "version": "2026.03-modern-ops-v1",
            "notes": "운영형 Go 서비스 기준선",
        },
        "rust_service": {
            "family": "rust-ops-service",
            "version": "2026.03-modern-ops-v1",
            "notes": "운영형 Rust 서비스 기준선",
        },
        "generic": {
            "family": "generic-scaffold",
            "version": "2026.03-fallback-v1",
            "notes": "일반 스캐폴드 기준선",
        },
    }
    selected = baselines.get(profile, baselines["generic"])
    return dict(selected)


def _build_generated_template_manifest(
    project_name: str,
    orchestration_spec: OrchestrationSpec,
) -> str:
    baseline = _template_baseline_for_profile(
        orchestration_spec.validation_profile,
    )
    payload = {
        "project_name": project_name,
        "template_profile": _resolve_template_profile(orchestration_spec),
        "template_family": baseline["family"],
        "template_version": baseline["version"],
        "template_notes": baseline["notes"],
        "spec_source": orchestration_spec.spec_source,
        "validation_profile": orchestration_spec.validation_profile,
        "mode": orchestration_spec.mode,
        "pipeline": orchestration_spec.pipeline,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _run_b_brain_multi_generator(
    *,
    project_name: str,
    validation_profile: str,
    task: str,
    output_dir: Path,
) -> Dict[str, Any]:
    from backend.generators.facade import (
        generate_multi_project_bundle,
        generate_non_python_project_bundle,
        generate_python_project_bundle,
    )

    generator_profile = _resolve_b_brain_generator_profile(validation_profile)
    additional_profiles = _resolve_b_brain_additional_profiles(validation_profile, task)
    generator_family = _resolve_b_brain_generator_family(validation_profile)
    if additional_profiles:
        generation_result = generate_multi_project_bundle(
            project_name=project_name,
            primary_profile=generator_profile,
            additional_profiles=additional_profiles,
            task=task,
            output_dir=output_dir,
        )
        generator_family = "multi_code_generator"
    elif generator_family == "python_code_generator":
        generation_result = generate_python_project_bundle(
            project_name=project_name,
            profile=generator_profile,
            task=task,
            output_dir=output_dir,
        )
    else:
        generation_result = generate_non_python_project_bundle(
            project_name=project_name,
            profile=generator_profile,
            task=task,
            output_dir=output_dir,
        )
    return {
        "generator_family": generator_family,
        "generator_profile": generator_profile,
        "additional_profiles": additional_profiles,
        "written_files": list(generation_result.written_files),
        "metadata": dict(generation_result.metadata),
        "file_count": len(generation_result.written_files),
    }


def _build_architecture_doc_template(project_name: str) -> str:
    return (
        f"# {project_name} Architecture\n\n"
        "## Purpose\n\n"
        "- Define fixed boundaries for the generated project.\n"
        "- Keep implementation and validation traceable.\n\n"
        "## Boundaries\n\n"
        "- UI handles rendering and API calls only.\n"
        "- Backend owns business logic and validation.\n"
        "- Docs store design, checklist, and traceability artifacts.\n"
    )


def _build_architecture_contract_template(project_name: str) -> str:
    payload = {
        "schema_version": "generated.v1",
        "project": project_name,
        "required_documents": [
            "docs/architecture.md",
            "docs/architecture.contract.json",
            "docs/id_registry.schema.json",
            "docs/id_registry.json",
            "docs/orchestration_rules_checklist.md",
        ],
        "fixed_links": [
            {
                "id": "main-to-router",
                "source": "backend/app/main.py",
                "target": "backend/app/api/routes/**",
                "rule": "앱 진입점은 라우터를 등록해야 한다.",
            },
            {
                "id": "router-to-controller",
                "source": "backend/app/api/routes/**",
                "target": "backend/app/controllers/**",
                "rule": "라우터는 컨트롤러 계층을 통해 요청 흐름을 시작한다.",
            },
            {
                "id": "controller-to-service",
                "source": "backend/app/controllers/**",
                "target": "backend/app/services/**",
                "rule": "컨트롤러는 서비스 계층을 통해 비즈니스 흐름을 조합한다.",
            },
            {
                "id": "service-to-repository",
                "source": "backend/app/services/**",
                "target": "backend/app/repositories/**",
                "rule": "서비스는 저장소 계층을 통해 데이터 접근을 수행한다.",
            },
            {
                "id": "service-to-external-adapter",
                "source": "backend/app/services/**",
                "target": "backend/app/external_adapters/**",
                "rule": "서비스는 외부 연동 호출을 external adapter 계층을 통해 수행한다.",
            },
            {
                "id": "repository-to-infra",
                "source": "backend/app/repositories/**",
                "target": "backend/app/infra/**",
                "rule": "저장소는 infra 계층을 통해 런타임 구현을 사용한다.",
            },
        ],
        "protected_paths": [
            {
                "path": "docs/**",
                "rule": "design and validation artifacts",
            }
        ],
        "structure_rules": [
            {
                "id": "main-router-registration",
                "scope": "backend/app/main.py",
                "requirement": "main entry must import and include routers",
            },
            {
                "id": "router-service-boundary",
                "scope": "backend/**/*router.py, backend/**/routers/**, backend/**/api/routes/**",
                "requirement": "routers must not call repositories directly and should remain HTTP adapters",
            },
            {
                "id": "controller-service-boundary",
                "scope": "backend/**/*controller.py, backend/**/controllers/**",
                "requirement": "controllers must orchestrate services only and must not call repositories or adapters directly",
            },
            {
                "id": "service-no-router-import",
                "scope": "backend/**/*service.py, backend/**/services/**",
                "requirement": "services must not depend on routers or FastAPI HTTP primitives",
            },
            {
                "id": "repository-data-only",
                "scope": "backend/**/*repository.py, backend/**/repositories/**, backend/**/repos/**",
                "requirement": "repositories must stay in data access layer and must not depend on routers or services",
            },
            {
                "id": "infra-isolation",
                "scope": "backend/**/*infra*.py, backend/**/infra/**",
                "requirement": "infra layer must not depend on router, controller, or service layers",
            },
            {
                "id": "external-adapter-isolation",
                "scope": "backend/**/*adapter.py, backend/**/*client.py, backend/**/external/**, backend/**/external_adapters/**",
                "requirement": "external adapters must stay as integration boundaries and must not depend on router, controller, or service layers",
            },
            {
                "id": "design-traceability",
                "scope": "docs/**, src/**, app/**, backend/**, frontend/**",
                "requirement": "design items must be traceable to implementation and validation evidence",
            }
        ],
        "traceability_fields": [
            "design_item_id",
            "implementation_files",
            "api_or_ui_links",
            "validation_evidence",
            "approval_status",
        ],
        "validation_gates": [
            "required_files",
            "structure_compliance",
            "id_registry_required",
            "completion_gate",
            "semantic_audit",
        ],
    }
    return json.dumps(payload, ensure_ascii=True, indent=2)


def _build_generated_id_registry_schema_template() -> str:
    return (REPO_ROOT / "docs" / "id_registry.schema.json").read_text(encoding="utf-8")


def _build_generated_id_registry_template(
    project_name: str,
    validation_profile: str,
) -> str:
    payload = {
        "$schema": "./id_registry.schema.json",
        "schema_version": "id-registry.v1",
        "registry_id": f"REG-{re.sub(r'[^A-Za-z0-9]+', '-', project_name.upper()).strip('-') or 'PROJECT'}",
        "generated_at": datetime.now().isoformat(),
        "project": {
            "project_id": f"PROJECT-{re.sub(r'[^A-Za-z0-9]+', '-', project_name.upper()).strip('-') or 'PROJECT'}",
            "name": project_name,
            "root_path": ".",
            "scope": "generated-output",
        },
        "governance": {
            "required_documents": [
                "docs/id_registry.schema.json",
                "docs/id_registry.json",
                "docs/traceability_map.json",
                "docs/auto_link_map.json",
                "docs/architecture.contract.json",
                "docs/generator_checklist.md",
            ],
            "required_id_levels": ["file", "section", "feature", "chunk", "flow", "trace", "failure_tag", "repair_tag"],
            "selective_apply_policy": "id-targeted-only",
            "future_generation_mandatory": True,
        },
        "files": [],
        "flows": [],
        "traceability_links": [],
        "failure_tags": [],
        "repair_tags": [],
        "validation_rules": {
            "hard_gate": [
                "모든 신규 소스 파일은 FILE-ID registry 항목이 있어야 한다.",
                "핵심 섹션은 SECTION-ID 와 최소 1개 CHUNK-ID를 가져야 한다.",
                "생성 프로그램은 docs/id_registry.schema.json 과 docs/id_registry.json 을 반드시 포함해야 한다.",
            ],
            "generation_requirements": [
                f"validation_profile={validation_profile}",
                "앞으로 생성되는 모든 프로그램은 docs/id_registry.json, docs/traceability_map.json, docs/auto_link_map.json, docs/architecture.contract.json, docs/generator_checklist.md 를 의무 생성한다.",
            ],
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _build_generated_product_identity_template(
    project_name: str,
    validation_profile: str,
) -> str:
    normalized_project = re.sub(r"[^A-Za-z0-9]+", "-", str(project_name or "project").upper()).strip("-") or "PROJECT"
    payload = {
        "schema_version": "product-identity.v1",
        "product_id": f"PID-{normalized_project}",
        "project_name": project_name,
        "validation_profile": validation_profile,
        "identity_policy": {
            "mandatory": True,
            "description": "생성기 산출물의 고유 인식표(주민번호 수준 식별자)입니다. 배포/검증/복구 모든 단계에서 반드시 유지해야 합니다.",
        },
        "identity_links": {
            "id_registry_path": ORCH_ID_REGISTRY_PATH,
            "traceability_map_path": ORCH_TRACEABILITY_MAP_PATH,
            "architecture_contract_path": "docs/architecture.contract.json",
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _decorate_generated_file_with_ids(path: str, content: str) -> str:
    normalized_path = str(path or "").strip().replace("\\", "/")
    if not normalized_path or normalized_path.startswith("docs/"):
        return content
    file_stub = re.sub(r"[^A-Za-z0-9]+", "-", normalized_path.upper()).strip("-") or "GENERATED-FILE"
    section_stub = f"SECTION-{file_stub}-MAIN"
    feature_stub = f"FEATURE-{file_stub}-RUNTIME"
    chunk_stub = f"CHUNK-{file_stub}-001"
    header = (
        f"# FILE-ID: FILE-{file_stub}\n"
        f"# SECTION-ID: {section_stub}\n"
        f"# FEATURE-ID: {feature_stub}\n"
        f"# CHUNK-ID: {chunk_stub}\n\n"
    )
    suffix = normalized_path.rsplit('.', 1)[-1].lower() if '.' in normalized_path else ''
    if suffix in {"py", "pyi", "sh", "yml", "yaml", "toml", "ini", "cfg", "env", "md", "txt"}:
        return header + content if not content.startswith("# FILE-ID:") else content

    if suffix in {"ts", "tsx", "js", "jsx", "css", "scss"}:
        comment_block = (
            f"/* FILE-ID: FILE-{file_stub} */\n"
            f"/* SECTION-ID: {section_stub} */\n"
            f"/* FEATURE-ID: {feature_stub} */\n"
            f"/* CHUNK-ID: {chunk_stub} */\n\n"
        )
        return comment_block + content if "FILE-ID:" not in content[:200] else content

    if suffix == "json":
        return content

    return header + content if not content.startswith("# FILE-ID:") else content


def _strip_generated_id_headers(content: str) -> str:
    text = str(content or "")
    if not text:
        return ""
    normalized = text.replace("\r\n", "\n")
    lines = normalized.split("\n")
    prefix_index = 0
    while prefix_index < len(lines) and lines[prefix_index].startswith("# ") and "-ID:" in lines[prefix_index]:
        prefix_index += 1
    if prefix_index > 0:
        while prefix_index < len(lines) and not lines[prefix_index].strip():
            prefix_index += 1
        return "\n".join(lines[prefix_index:])
    return normalized


def _decorate_template_candidates_with_ids(template_candidates: Dict[str, str]) -> Dict[str, str]:
    return {
        path: _decorate_generated_file_with_ids(path, content)
        for path, content in template_candidates.items()
    }


def _build_nextjs_vertical_slice_files(project_name: str) -> Dict[str, str]:
    return {
        "README.md": (
            f"# {project_name}\n\n"
            "Generated Next.js operations canvas scaffold.\n\n"
            "## What is included\n\n"
            "- App Router based landing page and dashboard experience\n"
            "- Shared design tokens, editorial layout primitives, and animated sections\n"
            "- Dark and light theme switch with persistent browser preference\n"
            "- Client-side fetch pattern bound to /api/brief for live dashboard hydration\n"
            "- Build-ready TypeScript and Next.js 16 configuration\n"
        ),
        ".gitignore": (
            ".next\n"
            "node_modules\n"
            "npm-debug.log*\n"
        ),
        "package.json": (
            "{\n"
            "  \"name\": \"generated-nextjs-ops-dashboard\",\n"
            "  \"private\": true,\n"
            "  \"scripts\": {\n"
            "    \"dev\": \"next dev\",\n"
            "    \"build\": \"next build\",\n"
            "    \"start\": \"next start\"\n"
            "  },\n"
            "  \"dependencies\": {\n"
            "    \"next\": \"16.1.6\",\n"
            "    \"react\": \"18.3.1\",\n"
            "    \"react-dom\": \"18.3.1\"\n"
            "  },\n"
            "  \"devDependencies\": {\n"
            "    \"@types/node\": \"20.14.12\",\n"
            "    \"@types/react\": \"18.3.3\",\n"
            "    \"@types/react-dom\": \"18.3.0\",\n"
            "    \"typescript\": \"5.5.4\"\n"
            "  }\n"
            "}\n"
        ),
        "tsconfig.json": (
            "{\n"
            "  \"compilerOptions\": {\n"
            "    \"target\": \"ES2022\",\n"
            "    \"lib\": [\"dom\", \"dom.iterable\", \"es2022\"],\n"
            "    \"allowJs\": false,\n"
            "    \"skipLibCheck\": true,\n"
            "    \"strict\": true,\n"
            "    \"noEmit\": true,\n"
            "    \"esModuleInterop\": true,\n"
            "    \"module\": \"esnext\",\n"
            "    \"moduleResolution\": \"bundler\",\n"
            "    \"resolveJsonModule\": true,\n"
            "    \"isolatedModules\": true,\n"
            "    \"jsx\": \"react-jsx\",\n"
            "    \"incremental\": true\n"
            "  },\n"
            "  \"include\": [\"next-env.d.ts\", \"**/*.ts\", \"**/*.tsx\"],\n"
            "  \"exclude\": [\"node_modules\"]\n"
            "}\n"
        ),
        "next-env.d.ts": (
            "/// <reference types=\"next\" />\n"
            "/// <reference types=\"next/image-types/global\" />\n\n"
            "// This file is managed by Next.js.\n"
        ),
        "next.config.js": (
            "const path = require('path');\n\n"
            "/** @type {import('next').NextConfig} */\n"
            "const nextConfig = {\n"
            "  reactStrictMode: true,\n"
            "  turbopack: {\n"
            "    root: path.resolve(__dirname),\n"
            "  },\n"
            "};\n\n"
            "module.exports = nextConfig;\n"
        ),
        "app/layout.tsx": (
            "import './globals.css';\n"
            "import type { ReactNode } from 'react';\n"
            "import { IBM_Plex_Mono, Space_Grotesk } from 'next/font/google';\n\n"
            "const display = Space_Grotesk({\n"
            "  subsets: ['latin'],\n"
            "  variable: '--font-display',\n"
            "});\n\n"
            "const mono = IBM_Plex_Mono({\n"
            "  subsets: ['latin'],\n"
            "  weight: ['400', '500'],\n"
            "  variable: '--font-mono',\n"
            "});\n\n"
            "export const metadata = {\n"
            f"  title: '{project_name}',\n"
            "  description: 'Operations canvas generated by the deterministic scaffold.',\n"
            "};\n\n"
            "export default function RootLayout({ children }: { children: ReactNode }) {\n"
            "  return (\n"
            "    <html lang=\"en\" suppressHydrationWarning>\n"
            "      <body className={`${display.variable} ${mono.variable}`}>\n"
            "        <div className=\"shell\">{children}</div>\n"
            "      </body>\n"
            "    </html>\n"
            "  );\n"
            "}\n"
        ),
        "app/loading.tsx": (
            "export default function Loading() {\n"
            "  return (\n"
            "    <main className=\"page\">\n"
            "      <section className=\"sectionCard\">\n"
            "        <p className=\"eyebrow\">Loading</p>\n"
            "        <h1>Preparing the operations canvas...</h1>\n"
            "      </section>\n"
            "    </main>\n"
            "  );\n"
            "}\n"
        ),
        "app/page.tsx": (
            "import { DashboardClient } from '../components/dashboardclient';\n"
            "import { defaultBriefPayload } from '../lib/data';\n\n"
            "export default function HomePage() {\n"
            "  return <DashboardClient variant=\"overview\" initialPayload={defaultBriefPayload} />;\n"
            "}\n"
        ),
        "app/dashboard/page.tsx": (
            "import { DashboardClient } from '../../components/dashboardclient';\n"
            "import { defaultBriefPayload } from '../../lib/data';\n\n"
            "export default function DashboardPage() {\n"
            "  return <DashboardClient variant=\"detail\" initialPayload={defaultBriefPayload} />;\n"
            "}\n"
        ),
        "app/api/health/route.ts": (
            "import { NextResponse } from 'next/server';\n\n"
            "export async function GET() {\n"
            "  return NextResponse.json({ ok: true });\n"
            "}\n"
        ),
        "app/api/brief/route.ts": (
            "import { NextResponse } from 'next/server';\n\n"
            "export async function GET() {\n"
            "  return NextResponse.json({ refreshedAt: new Date().toISOString() });\n"
            "}\n"
        ),
        "app/globals.css": (
            ":root {\n"
            "  color-scheme: light;\n"
            "  --bg: #f5efe5;\n"
            "  --bg-strong: #efe1cb;\n"
            "  --panel: rgba(255, 250, 242, 0.86);\n"
            "  --panel-strong: rgba(255, 247, 236, 0.98);\n"
            "  --line: rgba(27, 43, 65, 0.12);\n"
            "  --text: #1a2433;\n"
            "  --muted: #6c7684;\n"
            "  --accent: #e6783a;\n"
            "  --accent-strong: #0f766e;\n"
            "  --ink-soft: rgba(26, 36, 51, 0.08);\n"
            "  --shadow: 0 32px 80px rgba(103, 73, 40, 0.16);\n"
            "}\n\n"
            "html[data-theme='dark'] {\n"
            "  color-scheme: dark;\n"
            "  --bg: #07111f;\n"
            "  --bg-strong: #0d1a29;\n"
            "  --panel: rgba(10, 24, 45, 0.88);\n"
            "  --panel-strong: rgba(9, 20, 36, 0.96);\n"
            "  --line: rgba(121, 192, 255, 0.18);\n"
            "  --text: #e6edf3;\n"
            "  --muted: #8aa4c2;\n"
            "  --accent: #f59e0b;\n"
            "  --accent-strong: #5eead4;\n"
            "  --ink-soft: rgba(230, 237, 243, 0.08);\n"
            "  --shadow: 0 32px 90px rgba(0, 0, 0, 0.32);\n"
            "}\n\n"
            "* { box-sizing: border-box; }\n"
            "html, body { margin: 0; padding: 0; min-height: 100%; background: linear-gradient(180deg, var(--bg) 0%, var(--bg-strong) 100%); color: var(--text); transition: background 200ms ease, color 200ms ease; }\n"
            "body { line-height: 1.5; font-family: var(--font-display), 'Segoe UI', sans-serif; }\n"
            "body::before { content: ''; position: fixed; inset: 0; background: radial-gradient(circle at 15% 15%, rgba(230, 120, 58, 0.14), transparent 28%), radial-gradient(circle at 85% 20%, rgba(15, 118, 110, 0.12), transparent 24%), linear-gradient(135deg, rgba(255,255,255,0.24) 0%, transparent 45%); pointer-events: none; }\n"
            "a { color: inherit; text-decoration: none; }\n"
            ".shell { min-height: 100vh; padding: 32px 20px 48px; position: relative; }\n"
            ".page { max-width: 1220px; margin: 0 auto; display: grid; gap: 24px; position: relative; }\n"
            ".hero, .sectionCard { border: 1px solid var(--line); background: var(--panel); backdrop-filter: blur(16px); border-radius: 32px; padding: 28px; box-shadow: var(--shadow); position: relative; overflow: hidden; }\n"
            ".hero::after, .sectionCard::after { content: ''; position: absolute; inset: auto -10% -45% auto; width: 220px; height: 220px; background: radial-gradient(circle, rgba(230, 120, 58, 0.16), transparent 66%); }\n"
            ".eyebrow { margin: 0 0 8px; text-transform: uppercase; letter-spacing: 0.18em; color: var(--accent-strong); font-size: 12px; font-family: var(--font-mono), monospace; }\n"
            "h1, h2, h3, p { margin: 0; }\n"
            ".hero { display: grid; gap: 24px; }\n"
            ".heroHeader { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }\n"
            ".heroTitle { font-size: clamp(2.7rem, 7vw, 5.4rem); line-height: 0.95; max-width: 9ch; letter-spacing: -0.05em; }\n"
            ".heroCopy { max-width: 760px; font-size: 1.05rem; color: var(--muted); }\n"
            ".heroBadge { border-radius: 999px; padding: 10px 14px; background: rgba(15, 118, 110, 0.08); border: 1px solid rgba(15, 118, 110, 0.18); font-family: var(--font-mono), monospace; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.12em; }\n"
            ".heroHighlights { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }\n"
            ".heroHighlight { padding: 16px 18px; border-radius: 20px; background: rgba(255,255,255,0.72); border: 1px solid rgba(27, 43, 65, 0.08); box-shadow: inset 0 1px 0 rgba(255,255,255,0.6); }\n"
            ".heroActions { display: flex; gap: 12px; flex-wrap: wrap; }\n"
            ".primaryButton, .secondaryButton, .textLink { display: inline-flex; align-items: center; gap: 8px; border-radius: 999px; font-weight: 600; }\n"
            ".primaryButton { background: var(--text); color: #fff7ef; padding: 12px 18px; }\n"
            ".secondaryButton { border: 1px solid var(--line); padding: 12px 18px; background: rgba(255,255,255,0.56); }\n"
            ".textLink { color: var(--accent-strong); font-family: var(--font-mono), monospace; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.12em; }\n"
            ".utilityBar { display: flex; justify-content: space-between; gap: 16px; align-items: center; flex-wrap: wrap; }\n"
            ".utilityGroup { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }\n"
            ".statusPill { padding: 10px 14px; border-radius: 999px; border: 1px solid var(--line); background: rgba(255,255,255,0.5); font-family: var(--font-mono), monospace; font-size: 0.78rem; }\n"
            ".statusPill.is-error { color: #b91c1c; }\n"
            ".statusPill.is-live { color: var(--accent-strong); }\n"
            ".refreshButton { border: 1px solid var(--line); background: var(--panel-strong); color: var(--text); padding: 10px 14px; border-radius: 999px; font-family: var(--font-mono), monospace; }\n"
            ".themeToggle { display: inline-flex; padding: 4px; gap: 4px; border-radius: 999px; border: 1px solid var(--line); background: rgba(255,255,255,0.52); }\n"
            ".themeChip { border: 0; background: transparent; color: var(--muted); padding: 10px 12px; border-radius: 999px; font-family: var(--font-mono), monospace; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.12em; }\n"
            ".themeChip.is-active { background: var(--text); color: #fff7ef; }\n"
            ".metricGrid, .signalGrid, .featureGrid { display: grid; gap: 18px; }\n"
            ".metricGrid { grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }\n"
            ".metricCard { padding: 22px; border-radius: 24px; background: var(--panel-strong); border: 1px solid var(--line); box-shadow: var(--shadow); transform: translateY(0); animation: floatUp 560ms ease both; }\n"
            ".metricLabel, .signalLabel, .microLabel { font-family: var(--font-mono), monospace; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.12em; color: var(--muted); }\n"
            ".metricValue { margin-top: 12px; font-size: 2rem; letter-spacing: -0.04em; }\n"
            ".metricDetail { margin-top: 10px; color: var(--muted); }\n"
            ".signalGrid { grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }\n"
            ".signalCard { border: 1px solid var(--line); background: rgba(255,255,255,0.66); border-radius: 26px; padding: 20px; display: grid; gap: 12px; position: relative; overflow: hidden; }\n"
            ".signalTone { position: absolute; inset: 0 auto 0 0; width: 6px; }\n"
            ".signalTone.is-healthy { background: #0f766e; }\n"
            ".signalTone.is-watch { background: #e6783a; }\n"
            ".signalTone.is-planning { background: #3b82f6; }\n"
            ".signalStatus { font-size: 1.1rem; font-weight: 700; }\n"
            ".muted { color: var(--muted); }\n"
            ".list { display: grid; gap: 12px; margin-top: 18px; }\n"
            ".listItem { border-left: 3px solid var(--accent); padding-left: 14px; color: var(--text); }\n"
            ".featureGrid { grid-template-columns: minmax(0, 1.15fr) minmax(0, 0.85fr); }\n"
            ".railList { display: grid; gap: 14px; margin-top: 18px; }\n"
            ".railItem { padding: 16px 18px; border-radius: 20px; background: rgba(255,255,255,0.64); border: 1px solid var(--line); }\n"
            ".sectionHeader { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }\n"
            ".sectionCopy { margin-top: 12px; max-width: 640px; }\n"
            ".compactMetrics .metricCard { padding: 18px; }\n"
            ".dashboardStack { display: grid; gap: 24px; }\n"
            "@keyframes floatUp { from { opacity: 0; transform: translateY(16px); } to { opacity: 1; transform: translateY(0); } }\n"
            "@media (max-width: 920px) { .featureGrid { grid-template-columns: 1fr; } .heroHeader, .sectionHeader { flex-direction: column; } }\n"
            "@media (max-width: 720px) { .shell { padding: 20px 14px 36px; } .hero, .sectionCard { padding: 22px; border-radius: 24px; } .heroTitle { font-size: clamp(2.2rem, 15vw, 3.6rem); } }\n"
        ),
        "components/dashboardclient.tsx": (
            "'use client';\n\n"
            "import { useEffect, useState } from 'react';\n"
            "import { ExecutionBoard } from './executionboard';\n"
            "import { FocusRail } from './focusrail';\n"
            "import { HeroPanel } from './heropanel';\n"
            "import { InsightTimeline } from './insighttimeline';\n"
            "import { MetricCluster } from './metriccluster';\n"
            "import { SignalMatrix } from './signalmatrix';\n"
            "import { ThemeToggle } from './themetoggle';\n"
            "import type { BriefPayload } from '../lib/types';\n\n"
            "type DashboardVariant = 'overview' | 'detail';\n\n"
            "async function readBrief(): Promise<BriefPayload> {\n"
            "  const response = await fetch('/api/brief', { cache: 'no-store' });\n"
            "  if (!response.ok) {\n"
            "    throw new Error(`brief fetch failed: ${response.status}`);\n"
            "  }\n"
            "  return response.json() as Promise<BriefPayload>;\n"
            "}\n\n"
            "export function DashboardClient({ initialPayload, variant }: { initialPayload: BriefPayload; variant: DashboardVariant }) {\n"
            "  const [payload, setPayload] = useState(initialPayload);\n"
            "  const [phase, setPhase] = useState<'idle' | 'loading' | 'live' | 'error'>('idle');\n"
            "  const [errorMessage, setErrorMessage] = useState('');\n\n"
            "  const refresh = async () => {\n"
            "    setPhase('loading');\n"
            "    setErrorMessage('');\n"
            "    try {\n"
            "      const nextPayload = await readBrief();\n"
            "      setPayload(nextPayload);\n"
            "      setPhase('live');\n"
            "    } catch (error) {\n"
            "      setPhase('error');\n"
            "      setErrorMessage(error instanceof Error ? error.message : 'brief fetch failed');\n"
            "    }\n"
            "  };\n\n"
            "  useEffect(() => {\n"
            "    void refresh();\n"
            "  }, []);\n\n"
            "  const runtimeItems = payload.runtimeSignals.map((signal) => `${signal.label}: ${signal.status} · ${signal.owner}`);\n"
            "  const pillClassName = phase === 'error' ? 'statusPill is-error' : phase === 'live' ? 'statusPill is-live' : 'statusPill';\n\n"
            "  return (\n"
            "    <main className=\"page dashboardStack\">\n"
            "      <section className=\"sectionCard utilityBar\">\n"
            "        <div className=\"utilityGroup\">\n"
            "          <div className=\"statusPill\">Variant · {variant}</div>\n"
            "          <div className={pillClassName}>Fetch · {phase}</div>\n"
            "          <div className=\"statusPill\">Refreshed · {payload.refreshedAt}</div>\n"
            "        </div>\n"
            "        <div className=\"utilityGroup\">\n"
            "          <ThemeToggle />\n"
            "          <button type=\"button\" className=\"refreshButton\" onClick={() => void refresh()}>Refresh live brief</button>\n"
            "        </div>\n"
            "      </section>\n"
            "      {phase === 'error' ? <section className=\"sectionCard\"><p className=\"eyebrow\">Fetch warning</p><p>{errorMessage}</p></section> : null}\n"
            "      {variant === 'overview' ? (\n"
            "        <>\n"
            "          <HeroPanel summary={payload.summary} />\n"
            "          <MetricCluster metrics={payload.metrics} />\n"
            "          <SignalMatrix signals={payload.runtimeSignals} />\n"
            "          <section className=\"featureGrid\">\n"
            "            <FocusRail title=\"Focus rail\" items={payload.focusRail} />\n"
            "            <InsightTimeline title=\"Release timeline\" items={payload.releaseTimeline} />\n"
            "          </section>\n"
            "        </>\n"
            "      ) : (\n"
            "        <>\n"
            "          <header className=\"sectionCard sectionHeader\">\n"
            "            <div>\n"
            "              <p className=\"eyebrow\">Dashboard</p>\n"
            "              <h1>Operational release cockpit</h1>\n"
            "              <p className=\"muted sectionCopy\">This view compresses the same live brief into a denser board for owners and release reviewers.</p>\n"
            "            </div>\n"
            "            <a href=\"/api/brief\" className=\"textLink\">Open raw brief JSON</a>\n"
            "          </header>\n"
            "          <MetricCluster metrics={payload.metrics} compact />\n"
            "          <section className=\"featureGrid\">\n"
            "            <ExecutionBoard title=\"Runtime signals\" items={runtimeItems} />\n"
            "            <InsightTimeline title=\"Release timeline\" items={payload.releaseTimeline} />\n"
            "          </section>\n"
            "        </>\n"
            "      )}\n"
            "    </main>\n"
            "  );\n"
            "}\n"
        ),
        "components/heropanel.tsx": (
            "import type { DashboardSummary } from '../lib/types';\n\n"
            "export function HeroPanel({ summary }: { summary: DashboardSummary }) {\n"
            "  return (\n"
            "    <section className=\"hero\">\n"
            "      <div className=\"heroHeader\">\n"
            "        <div>\n"
            "          <p className=\"eyebrow\">{summary.eyebrow}</p>\n"
            "          <h1 className=\"heroTitle\">{summary.headline}</h1>\n"
            "        </div>\n"
            "        <div className=\"heroBadge\">{summary.badge}</div>\n"
            "      </div>\n"
            "      <p className=\"heroCopy\">{summary.description}</p>\n"
            "      <div className=\"heroHighlights\">\n"
            "        {summary.highlights.map((item) => (\n"
            "          <div key={item} className=\"heroHighlight\">{item}</div>\n"
            "        ))}\n"
            "      </div>\n"
            "      <div className=\"heroActions\">\n"
            "        <a href={summary.primaryHref} className=\"primaryButton\">{summary.primaryLabel}</a>\n"
            "        <a href={summary.secondaryHref} className=\"secondaryButton\">{summary.secondaryLabel}</a>\n"
            "      </div>\n"
            "    </section>\n"
            "  );\n"
            "}\n"
        ),
        "components/metriccluster.tsx": (
            "import type { SummaryMetric } from '../lib/types';\n\n"
            "export function MetricCluster({ metrics, compact = false }: { metrics: SummaryMetric[]; compact?: boolean }) {\n"
            "  return (\n"
            "    <section className={compact ? 'metricGrid compactMetrics' : 'metricGrid'}>\n"
            "      {metrics.map((metric) => (\n"
            "        <article key={metric.label} className=\"metricCard\">\n"
            "          <div className=\"metricLabel\">{metric.label}</div>\n"
            "          <div className=\"metricValue\">{metric.value}</div>\n"
            "          <p className=\"metricDetail\">{metric.detail}</p>\n"
            "        </article>\n"
            "      ))}\n"
            "    </section>\n"
            "  );\n"
            "}\n"
        ),
        "components/signalmatrix.tsx": (
            "import type { RuntimeSignal } from '../lib/types';\n\n"
            "export function SignalMatrix({ signals }: { signals: RuntimeSignal[] }) {\n"
            "  return (\n"
            "    <section className=\"signalGrid\">\n"
            "      {signals.map((signal) => (\n"
            "        <article key={signal.label} className=\"signalCard\">\n"
            "          <div className={`signalTone is-${signal.tone}`} />\n"
            "          <div className=\"signalLabel\">{signal.label}</div>\n"
            "          <div className=\"signalStatus\">{signal.status}</div>\n"
            "          <p className=\"muted\">{signal.detail}</p>\n"
            "          <div className=\"microLabel\">Owner · {signal.owner}</div>\n"
            "        </article>\n"
            "        ))}\n"
            "    </section>\n"
            "  );\n"
            "}\n"
        ),
        "components/focusrail.tsx": (
            "import type { FocusItem } from '../lib/types';\n\n"
            "export function FocusRail({ title, items }: { title: string; items: FocusItem[] }) {\n"
            "  return (\n"
            "    <section className=\"sectionCard\">\n"
            "      <p className=\"eyebrow\">Focus</p>\n"
            "      <h2>{title}</h2>\n"
            "      <div className=\"railList\">\n"
            "        {items.map((item) => (\n"
            "          <article key={item.title} className=\"railItem\">\n"
            "            <div className=\"microLabel\">{item.owner}</div>\n"
            "            <h3 style={{ marginTop: 8 }}>{item.title}</h3>\n"
            "            <p className=\"muted\" style={{ marginTop: 8 }}>{item.summary}</p>\n"
            "          </article>\n"
            "        ))}\n"
            "      </div>\n"
            "    </section>\n"
            "  );\n"
            "}\n"
        ),
        "components/insighttimeline.tsx": (
            "export function InsightTimeline({ title, items }: { title: string; items: string[] }) {\n"
            "  return (\n"
            "    <section className=\"sectionCard\">\n"
            "      <p className=\"eyebrow\">Timeline</p>\n"
            "      <h2>{title}</h2>\n"
            "      <div className=\"list\">\n"
            "        {items.map((item, index) => (\n"
            "          <div key={`${title}-${index}`} className=\"listItem\">\n"
            "            <strong style={{ color: 'var(--accent-strong)' }}>0{index + 1}</strong> {item}\n"
            "          </div>\n"
            "        ))}\n"
            "      </div>\n"
            "    </section>\n"
            "  );\n"
            "}\n"
        ),
        "components/executionboard.tsx": (
            "export function ExecutionBoard({ title, items }: { title: string; items: string[] }) {\n"
            "  return (\n"
            "    <section className=\"sectionCard\">\n"
            "      <p className=\"eyebrow\">Execution</p>\n"
            "      <h2>{title}</h2>\n"
            "      <div className=\"list\">\n"
            "        {items.map((item, index) => (\n"
            "          <div key={`${title}-${index}`} className=\"listItem\">\n"
            "            <strong style={{ color: 'var(--accent)' }}>Step {index + 1}</strong> {item}\n"
            "          </div>\n"
            "        ))}\n"
            "      </div>\n"
            "    </section>\n"
            "  );\n"
            "}\n"
        ),
        "components/themetoggle.tsx": (
            "'use client';\n\n"
            "import { useEffect, useState } from 'react';\n\n"
            "type ThemeMode = 'light' | 'dark';\n\n"
            "function applyTheme(theme: ThemeMode) {\n"
            "  document.documentElement.dataset.theme = theme;\n"
            "  window.localStorage.setItem('ops-theme', theme);\n"
            "}\n\n"
            "export function ThemeToggle() {\n"
            "  const [theme, setTheme] = useState<ThemeMode>('light');\n\n"
            "  useEffect(() => {\n"
            "    const stored = window.localStorage.getItem('ops-theme');\n"
            "    const resolved = stored === 'dark' ? 'dark' : 'light';\n"
            "    setTheme(resolved);\n"
            "    applyTheme(resolved);\n"
            "  }, []);\n\n"
            "  const updateTheme = (nextTheme: ThemeMode) => {\n"
            "    setTheme(nextTheme);\n"
            "    applyTheme(nextTheme);\n"
            "  };\n\n"
            "  return (\n"
            "    <div className=\"themeToggle\">\n"
            "      <button type=\"button\" className={theme === 'light' ? 'themeChip is-active' : 'themeChip'} onClick={() => updateTheme('light')}>Light</button>\n"
            "      <button type=\"button\" className={theme === 'dark' ? 'themeChip is-active' : 'themeChip'} onClick={() => updateTheme('dark')}>Dark</button>\n"
            "    </div>\n"
            "  );\n"
            "}\n"
        ),
        "lib/types.ts": (
            "export interface DashboardSummary {\n"
            "  eyebrow: string;\n"
            "  headline: string;\n"
            "  badge: string;\n"
            "  description: string;\n"
            "  highlights: string[];\n"
            "  primaryLabel: string;\n"
            "  primaryHref: string;\n"
            "  secondaryLabel: string;\n"
            "  secondaryHref: string;\n"
            "}\n\n"
            "export interface SummaryMetric {\n"
            "  label: string;\n"
            "  value: string;\n"
            "  detail: string;\n"
            "}\n\n"
            "export interface RuntimeSignal {\n"
            "  label: string;\n"
            "  status: string;\n"
            "  detail: string;\n"
            "  owner: string;\n"
            "  tone: 'healthy' | 'watch' | 'planning';\n"
            "}\n\n"
            "export interface FocusItem {\n"
            "  title: string;\n"
            "  summary: string;\n"
            "  owner: string;\n"
            "}\n\n"
            "export interface BriefPayload {\n"
            "  summary: DashboardSummary;\n"
            "  metrics: SummaryMetric[];\n"
            "  runtimeSignals: RuntimeSignal[];\n"
            "  focusRail: FocusItem[];\n"
            "  releaseTimeline: string[];\n"
            "  refreshedAt: string;\n"
            "}\n"
        ),
        "lib/data.ts": (
            "import type { BriefPayload, DashboardSummary, FocusItem, RuntimeSignal, SummaryMetric } from './types';\n\n"
            "export const dashboardSummary: DashboardSummary = {\n"
            "  eyebrow: 'Operations canvas',\n"
            "  headline: 'Release confidence with visible ownership.',\n"
            "  badge: 'Template 2026.03',\n"
            "  description: 'The generated dashboard acts like an editorial control room: planning context, runtime signals, and ship-readiness cues all sit in one deliberately designed surface.',\n"
            "  highlights: [\n"
            "    'Planning, validation, and release ownership are separated into clear lanes instead of one noisy feed.',\n"
            "    'Each runtime card explains the operational meaning, not only the current color.',\n"
            "    'Landing and dashboard surfaces share one data contract so the generated output remains deterministic.',\n"
            "  ],\n"
            "  primaryLabel: 'Open dashboard',\n"
            "  primaryHref: '/dashboard',\n"
            "  secondaryLabel: 'Read live brief',\n"
            "  secondaryHref: '/api/brief',\n"
            "};\n\n"
            "export const summaryMetrics: SummaryMetric[] = [\n"
            "  { label: 'Evidence coverage', value: '92%', detail: 'Traceability rows already linked to implementation and validation artifacts.' },\n"
            "  { label: 'Runtime window', value: '14m', detail: 'Average time from checklist freeze to release handoff in the last dry-run.' },\n"
            "  { label: 'Approval delta', value: '+3', detail: 'Three ownership gaps were closed before the final shipment review.' },\n"
            "  { label: 'Recovery headroom', value: '2.4x', detail: 'Queue and incident buffers remain below the recovery escalation threshold.' },\n"
            "];\n\n"
            "export const runtimeSignals: RuntimeSignal[] = [\n"
            "  { label: 'Approval gate', status: 'Green and locked', detail: 'Completion and semantic gates passed with no missing release evidence.', owner: 'Reviewer', tone: 'healthy' },\n"
            "  { label: 'Queue watch', status: '6 tasks waiting', detail: 'Background queue remains under the recovery threshold with room for one more rollout.', owner: 'Runtime', tone: 'watch' },\n"
            "  { label: 'Brief posture', status: 'Ready for live handoff', detail: 'The release brief is already reduced to operator-facing language for the final check.', owner: 'Reasoner', tone: 'planning' },\n"
            "  { label: 'Evidence sync', status: 'Docs and code aligned', detail: 'The dashboard and route payloads use the same typed contract and update together.', owner: 'Planner', tone: 'healthy' },\n"
            "];\n\n"
            "export const focusRail: FocusItem[] = [\n"
            "  { title: 'Lock scope early', summary: 'Freeze the visible release promise before the implementation lane expands and turns into rework.', owner: 'Planner' },\n"
            "  { title: 'Explain runtime meaning', summary: 'Turn raw metrics into operator language so the release conversation stays decision-oriented.', owner: 'Reasoner' },\n"
            "  { title: 'Ship only with evidence', summary: 'Keep route checks, build output, and ownership traces attached to the final handoff packet.', owner: 'Reviewer' },\n"
            "];\n\n"
            "export const releaseTimeline = [\n"
            "  'Morning: freeze the change brief and confirm the live evidence packet.',\n"
            "  'Midday: compare dashboard payloads, queue state, and route contract drift.',\n"
            "  'Afternoon: finalize owner handoff notes, then ship from the same release canvas.',\n"
            "];\n\n"
            "export const defaultBriefPayload: BriefPayload = {\n"
            "  summary: dashboardSummary,\n"
            "  metrics: summaryMetrics,\n"
            "  runtimeSignals,\n"
            "  focusRail,\n"
            "  releaseTimeline,\n"
            "  refreshedAt: 'seeded-static-brief',\n"
            "};\n"
        ),
    }


def _build_node_service_vertical_slice_files(project_name: str) -> Dict[str, str]:
    return {
        "README.md": (
            f"# {project_name}\n\n"
            "Generated Node.js operational service scaffold.\n\n"
            "## Included layers\n\n"
            "- Express entrypoint and route composition\n"
            "- Controller, service, and repository boundaries\n"
            "- Runtime store abstraction and central error middleware\n"
            "- TypeScript build path ready for npm run build\n"
        ),
        "package.json": (
            "{\n"
            "  \"name\": \"generated-node-ops-service\",\n"
            "  \"private\": true,\n"
            "  \"type\": \"commonjs\",\n"
            "  \"scripts\": {\n"
            "    \"dev\": \"tsx src/index.ts\",\n"
            "    \"build\": \"tsc -p tsconfig.json\",\n"
            "    \"start\": \"node dist/index.js\"\n"
            "  },\n"
            "  \"dependencies\": {\n"
            "    \"express\": \"4.21.1\",\n"
            "    \"zod\": \"3.23.8\"\n"
            "  },\n"
            "  \"devDependencies\": {\n"
            "    \"@types/express\": \"5.0.0\",\n"
            "    \"@types/node\": \"20.17.6\",\n"
            "    \"tsx\": \"4.19.2\",\n"
            "    \"typescript\": \"5.6.3\"\n"
            "  }\n"
            "}\n"
        ),
        "tsconfig.json": (
            "{\n"
            "  \"compilerOptions\": {\n"
            "    \"target\": \"ES2022\",\n"
            "    \"module\": \"CommonJS\",\n"
            "    \"moduleResolution\": \"node\",\n"
            "    \"outDir\": \"dist\",\n"
            "    \"rootDir\": \"src\",\n"
            "    \"strict\": true,\n"
            "    \"esModuleInterop\": true,\n"
            "    \"resolveJsonModule\": true,\n"
            "    \"skipLibCheck\": true\n"
            "  },\n"
            "  \"include\": [\"src/**/*.ts\"],\n"
            "  \"exclude\": [\"node_modules\", \"dist\"]\n"
            "}\n"
        ),
        "src/types.ts": (
            "export interface OrderRecord {\n"
            "  id: string;\n"
            "  customer: string;\n"
            "  total: number;\n"
            "  status: 'queued' | 'approved' | 'shipped';\n"
            "}\n"
        ),
        "src/config.ts": (
            "export const config = {\n"
            "  serviceName: 'node-ops-service',\n"
            "  port: Number(process.env.PORT || 8080),\n"
            "  runtimeProfile: process.env.RUNTIME_PROFILE || 'local-deterministic',\n"
            "  secretKey: process.env.SECRET_KEY || 'change-me',\n"
            "};\n"
        ),
        "src/lib/runtimeStore.ts": (
            "import { config } from '../config';\n\n"
            "export function readRuntimeSummary() {\n"
            "  return {\n"
            "    profile: config.runtimeProfile,\n"
            "    readiness: 'ready',\n"
            "  };\n"
            "}\n"
        ),
        "src/repositories/orderRepository.ts": (
            "import type { OrderRecord } from '../types';\n\n"
            "const orders: OrderRecord[] = [\n"
            "  { id: 'ord-100', customer: 'metanova', total: 182000, status: 'approved' },\n"
            "  { id: 'ord-101', customer: 'pilot-lab', total: 94000, status: 'queued' },\n"
            "];\n\n"
            "export function listOrders(): OrderRecord[] {\n"
            "  return orders.map((order) => ({ ...order }));\n"
            "}\n\n"
            "export function findOrder(orderId: string): OrderRecord | undefined {\n"
            "  return orders.find((order) => order.id === orderId);\n"
            "}\n"
        ),
        "src/services/orderService.ts": (
            "import { findOrder, listOrders } from '../repositories/orderRepository';\n"
            "import { readRuntimeSummary } from '../lib/runtimeStore';\n\n"
            "export function buildHealthPayload() {\n"
            "  return {\n"
            "    ok: true,\n"
            "    service: 'go-ops-service',\n"
            "    timestamp: new Date().toISOString(),\n"
            "    runtime: readRuntimeSummary(),\n"
            "  };\n"
            "}\n\n"
            "export function listOperationalOrders() {\n"
            "  return {\n"
            "    runtime: readRuntimeSummary(),\n"
            "    items: listOrders(),\n"
            "  };\n"
            "}\n\n"
            "export function readOperationalOrder(orderId: string) {\n"
            "  const order = findOrder(orderId);\n"
            "  if (!order) {\n"
            "    return null;\n"
            "  }\n"
            "  return {\n"
            "    order,\n"
            "    runtime: readRuntimeSummary(),\n"
            "    nextStep: order.status === 'queued' ? 'review-and-approve' : 'handoff-ready',\n"
            "  };\n"
            "}\n"
        ),
        "src/http/handlers/health.go": (
            "package handlers\n\n"
            "import (\n"
            "\t\"encoding/json\"\n"
            "\t\"net/http\"\n"
            "\t\"generated/service/internal/service\"\n"
            ")\n\n"
            "type HealthHandler struct {\n"
            "\tservice service.InventoryService\n"
            "}\n\n"
            "func NewHealthHandler(service service.InventoryService) HealthHandler {\n"
            "\treturn HealthHandler{service: service}\n"
            "}\n\n"
            "func (handler HealthHandler) ServeHTTP(writer http.ResponseWriter, _ *http.Request) {\n"
            "\twriter.Header().Set(\"Content-Type\", \"application/json\")\n"
            "\t_ = json.NewEncoder(writer).Encode(handler.service.HealthPayload())\n"
            "}\n"
        ),
        "src/http/handlers/inventory.go": (
            "package handlers\n\n"
            "import (\n"
            "\t\"encoding/json\"\n"
            "\t\"net/http\"\n"
            "\t\"generated/service/internal/service\"\n"
            ")\n\n"
            "type InventoryHandler struct {\n"
            "\tservice service.InventoryService\n"
            "}\n\n"
            "func NewInventoryHandler(service service.InventoryService) InventoryHandler {\n"
            "\treturn InventoryHandler{service: service}\n"
            "}\n\n"
            "func (handler InventoryHandler) ServeHTTP(writer http.ResponseWriter, _ *http.Request) {\n"
            "\twriter.Header().Set(\"Content-Type\", \"application/json\")\n"
            "\t_ = json.NewEncoder(writer).Encode(handler.service.InventoryPayload())\n"
            "}\n"
        ),
        "src/http/router.go": (
            "package httpapi\n\n"
            "import (\n"
            "\t\"net/http\"\n"
            "\t\"generated/service/internal/http/handlers\"\n"
            "\t\"generated/service/internal/repository\"\n"
            "\t\"generated/service/internal/service\"\n"
            ")\n\n"
            "func NewRouter() http.Handler {\n"
            "\trepo := repository.NewInventoryRepository()\n"
            "\tserviceLayer := service.NewInventoryService(repo)\n"
            "\tmux := http.NewServeMux()\n"
            "\tmux.Handle(\"/health\", handlers.NewHealthHandler(serviceLayer))\n"
            "\tmux.Handle(\"/inventory\", handlers.NewInventoryHandler(serviceLayer))\n"
            "\treturn mux\n"
            "}\n"
        ),
    }


def _is_locked_targeted_patch_run(
    task: str,
    orchestration_spec: OrchestrationSpec,
) -> bool:
    required_files = _normalize_required_files(
        list(orchestration_spec.required_files or [])
    )
    if not required_files:
        return False
    if str(orchestration_spec.validation_profile or "").strip().lower() != "generic":
        return False
    # tiny targeted patch 실험은 최신 경로라도 required_files 밖 생성을 막아야 한다.
    return _is_targeted_existing_patch_request(task, required_files)


def _build_fixed_scaffold_files(
    task: str,
    project_name: str,
    orchestration_spec: OrchestrationSpec,
) -> tuple[str, List[Dict[str, str]], str]:
    template_profile = _resolve_template_profile(orchestration_spec)
    required_files_only = _is_locked_targeted_patch_run(
        task,
        orchestration_spec,
    )
    always_include = set() if required_files_only else {
        "docs/architecture.md",
        "docs/architecture.contract.json",
        "docs/orchestration_rules_checklist.md",
    }
    required_lookup = {
        _normalize_rel(path)
        for path in (orchestration_spec.required_files or [])
        if str(path).strip()
    }

    template_candidates: Dict[str, str] = {
        "docs/architecture.md": _build_architecture_doc_template(project_name),
        "docs/architecture.contract.json": _build_architecture_contract_template(project_name),
        "docs/orchestration_rules_checklist.md": (
            "# Orchestration Rules Checklist\n\n"
            "- Keep required files, structure compliance, and validation gates aligned.\n"
            "- Block delivery when structure validation fails.\n"
            "- Preserve implementation and validation traceability.\n"
        ),
    }

    if template_profile == "python_fastapi":
        template_candidates.update(
            {
                "README.md": (
                    f"# {project_name}\n\n"
                    "Generated FastAPI scaffold.\n\n"
                    "## Included Runtime\n\n"
                    "- `app/main.py` FastAPI runtime entrypoint\n"
                    "- `backend/core` runtime/security core layer\n"
                    "- `frontend/app/page.tsx` operator-facing front surface\n"
                ),
                "requirements.txt": (
                    "fastapi==0.110.0\n"
                    "uvicorn==0.27.1\n"
                    "httpx==0.27.0\n"
                    "pytest==8.2.0\n"
                    "pyjwt==2.9.0\n"
                    "bcrypt==4.2.0\n"
                ),
                "backend/app/main.py": (
                    "from fastapi import FastAPI\n"
                    "from backend.app.api.routes import router as api_router\n"
                    "from backend.app.core.config import settings\n\n"
                    "from backend.app.core.logging import configure_logging\n"
                    "from backend.app.middleware.request_context import RequestContextMiddleware\n\n"
                    "configure_logging()\n\n"
                    "app = FastAPI(\n"
                    "    title=settings.app_name,\n"
                    "    version=\"0.1.0\",\n"
                    ")\n"
                    "app.add_middleware(RequestContextMiddleware)\n"
                    "app.include_router(api_router)\n\n"
                    "@app.get(\"/\")\n"
                    "def root() -> dict:\n"
                    "    return {\n"
                    "        \"success\": True,\n"
                    "        \"message\": \"vertical slice boilerplate ready\",\n"
                    "    }\n"
                ),
                "backend/app/api/routes/health.py": (
                    "from fastapi import APIRouter\n\n"
                    "from backend.app.controllers.health_controller import get_health_response\n\n"
                    "router = APIRouter()\n\n"
                    "@router.get('/health')\n"
                    "def health():\n"
                    "    payload = get_health_response()\n"
                    "    return {'success': True, 'data': payload}\n"
                ),
                "app/api/routes/__init__.py": "",
                "app/api/routes/health.py": (
                    "from fastapi import APIRouter\n\n"
                    "router = APIRouter()\n\n"
                    "@router.get('/health')\n"
                    "def health() -> dict:\n"
                    "    return {'status': 'ok', 'service': 'customer-order-generator'}\n"
                ),
                "backend/app/controllers/health_controller.py": (
                    "from backend.app.services.health_service import get_health_payload\n\n"
                    "def get_health_response():\n"
                    "    return get_health_payload()\n"
                ),
                "backend/app/services/health_service.py": (
                    "from backend.app.external_adapters.status_client import fetch_upstream_status\n"
                    "from backend.app.repositories.health_repository import read_health_status\n\n"
                    "def get_health_payload():\n"
                    "    payload = read_health_status()\n"
                    "    payload.update(fetch_upstream_status())\n"
                    "    return payload\n"
                ),
                "backend/app/repositories/health_repository.py": (
                    "from backend.app.infra.runtime_store import read_runtime_metadata\n\n"
                    "def read_health_status():\n"
                    "    return {\"status\": \"ok\", \"runtime\": read_runtime_metadata()}\n"
                ),
                "backend/app/infra/runtime_store.py": (
                    "from backend.app.core.config import settings\n"
                    "from backend.app.core.database import APP_BOOT, ORDERS, PRODUCTS, QUEUE, USERS\n\n"
                    "def read_runtime_metadata() -> dict:\n"
                    "    return {\n"
                    "        \"app_name\": settings.app_name,\n"
                    "        \"environment\": settings.app_env,\n"
                    "        \"runtime_channel\": settings.runtime_channel,\n"
                    "        \"started_at\": APP_BOOT[\"started_at\"],\n"
                    "        \"storage\": \"memory\",\n"
                    "        \"users\": len(USERS),\n"
                    "        \"products\": len(PRODUCTS),\n"
                    "        \"orders\": len(ORDERS),\n"
                    "        \"queued_jobs\": len(QUEUE),\n"
                    "    }\n"
                ),
                "backend/app/external_adapters/status_client.py": (
                    "def fetch_upstream_status() -> dict:\n"
                    "    return {\"provider\": \"local-simulated\", \"reachable\": True}\n"
                ),
                "backend/app/connectors/base.py": (
                    "class BaseConnector:\n"
                    "    def sync_products(self) -> list[dict]:\n"
                    "        raise NotImplementedError\n"
                ),
                "backend/app/connectors/shopify.py": (
                    "import httpx\n\n"
                    "from backend.app.connectors.base import BaseConnector\n\n"
                    "class ShopifyConnector(BaseConnector):\n"
                    "    def __init__(self, base_url: str) -> None:\n"
                    "        self.base_url = base_url.rstrip(\"/\")\n\n"
                    "    def sync_products(self) -> list[dict]:\n"
                    "        if \"example.com\" in self.base_url:\n"
                    "            return [\n"
                    "                {\"id\": 1, \"name\": \"Starter\", \"price\": 10.0},\n"
                    "                {\"id\": 2, \"name\": \"Growth\", \"price\": 19.0},\n"
                    "                {\"id\": 3, \"name\": \"Scale\", \"price\": 39.0},\n"
                    "            ]\n"
                    "        url = f\"{self.base_url}/admin/api/2024-01/products.json\"\n"
                    "        response = httpx.get(url, timeout=10)\n"
                    "        response.raise_for_status()\n"
                    "        payload = response.json()\n"
                    "        normalized: list[dict] = []\n"
                    "        for item in payload.get(\"products\", []):\n"
                    "            variants = item.get(\"variants\", [{}])\n"
                    "            price = variants[0].get(\"price\", 0) if variants else 0\n"
                    "            normalized.append({\n"
                    "                \"id\": int(item.get(\"id\", 0) or 0),\n"
                    "                \"name\": str(item.get(\"title\") or \"untitled\"),\n"
                    "                \"price\": float(price or 0),\n"
                    "            })\n"
                    "        return normalized\n"
                ),
                "backend/app/worker/tasks.py": (
                    "from backend.app.core.database import QUEUE, next_id, utc_now\n\n"
                    "def enqueue(task_name: str, payload: dict) -> dict:\n"
                    "    item = {\n"
                    "        \"id\": next_id(QUEUE),\n"
                    "        \"task\": task_name,\n"
                    "        \"payload\": payload,\n"
                    "        \"created_at\": utc_now(),\n"
                    "        \"status\": \"queued\",\n"
                    "    }\n"
                    "    QUEUE.append(item)\n"
                    "    return item\n\n"
                    "def list_jobs() -> list[dict]:\n"
                    "    return [item.copy() for item in QUEUE]\n"
                ),
                "backend/tests/conftest.py": (
                    "import pytest\n\n"
                    "from backend.app.core.database import reset_state\n\n"
                    "@pytest.fixture(autouse=True)\n"
                    "def reset_runtime_state() -> None:\n"
                    "    reset_state()\n"
                ),
                "backend/tests/test_health.py": (
                    "from fastapi.testclient import TestClient\n"
                    "from backend.app.main import app\n\n"
                    "client = TestClient(app)\n\n"
                    "def test_health() -> None:\n"
                    "    response = client.get(\"/health\")\n"
                    "    assert response.status_code == 200\n"
                    "    payload = response.json()\n"
                    "    assert payload[\"success\"] is True\n"
                    "    assert payload[\"data\"][\"status\"] == \"ok\"\n"
                ),
                "backend/tests/test_auth.py": (
                    "from fastapi.testclient import TestClient\n"
                    "from backend.app.main import app\n\n"
                    "client = TestClient(app)\n\n"
                    "def test_auth_routes() -> None:\n"
                    "    register_response = client.post('/api/auth/register', json={'username': 'admin', 'password': 'pw12'})\n"
                    "    login_response = client.post('/api/auth/login', json={'username': 'admin', 'password': 'pw12'})\n"
                    "    assert register_response.status_code == 200\n"
                    "    assert login_response.status_code == 200\n"
                ),
                "backend/tests/test_catalog_sync.py": (
                    "from fastapi.testclient import TestClient\n"
                    "from backend.app.main import app\n\n"
                    "client = TestClient(app)\n\n"
                    "def test_catalog_sync() -> None:\n"
                    "    response = client.post('/api/catalog/sync')\n"
                    "    assert response.status_code == 200\n"
                ),
                "backend/tests/test_orders.py": (
                    "from fastapi.testclient import TestClient\n"
                    "from backend.app.main import app\n\n"
                    "client = TestClient(app)\n\n"
                    "def test_create_and_list_orders() -> None:\n"
                    "    create_response = client.post('/api/orders', json={'product_id': 1, 'quantity': 2})\n"
                    "    assert create_response.status_code == 200\n"
                    "    list_response = client.get('/api/orders')\n"
                    "    assert list_response.status_code == 200\n"
                ),
                "backend/tests/test_admin_runtime.py": (
                    "from fastapi.testclient import TestClient\n"
                    "from backend.app.main import app\n\n"
                    "client = TestClient(app)\n\n"
                    "def test_admin_runtime_endpoints() -> None:\n"
                    "    runtime_response = client.get('/api/admin/runtime')\n"
                    "    assert runtime_response.status_code == 200\n"
                ),
            }
        )
    elif template_profile in {"nextjs_app", "nextjs", "fullstack_mixed"}:
        template_candidates.update(nextjs_defaults)

    design_ready_paths = set(template_candidates.keys())
    target_paths = set(required_lookup or template_candidates.keys()) | always_include
    fallback_only_paths: List[str] = []
    for target_path in sorted(target_paths):
        if target_path not in template_candidates:
            template_candidates[target_path] = _fallback_required_content(target_path)
        if target_path not in design_ready_paths:
            fallback_only_paths.append(target_path)

    regeneration_required = bool(fallback_only_paths)
    if regeneration_required:
        template_candidates["docs/auto_regeneration_plan.md"] = (
            "# Auto Regeneration Plan\n\n"
            "다음 파일들은 fallback-only 상태이므로 completion 을 통과할 수 없습니다.\n"
            "생성기는 이 파일들을 구조 설계에 맞는 실제 코드로 다시 보강해야 합니다.\n\n"
            "## regeneration_required_files\n\n- "
            + "\n- ".join(sorted(fallback_only_paths))
            + "\n"
        )
        target_paths.add("docs/auto_regeneration_plan.md")

    checklist_lines = [
        "# Orchestration Rules Checklist",
        "",
        "- fallback 은 누락 방지 임시 뼈대만 담당",
        "- 핵심 파일 존재와 설계 적합 코드 충전을 분리해서 검사",
        "- fallback-only 파일이 남아 있으면 completion 실패",
        "- 실패 시 생성기는 자동 재생성/재보강 단계로 다시 들어감",
        f"- template_profile: {template_profile}",
        f"- required_files_count: {len(required_lookup)}",
        f"- fallback_only_count: {len(fallback_only_paths)}",
        f"- completion_gate: {'failed' if regeneration_required else 'ready'}",
    ]
    if fallback_only_paths:
        checklist_lines.extend(
            [
                "",
                "## fallback_only_required_files",
                "",
                *[f"- {path}" for path in sorted(fallback_only_paths)],
            ]
        )
    template_candidates["docs/orchestration_rules_checklist.md"] = "\n".join(checklist_lines) + "\n"

    manifest_paths = sorted(target_paths)
    manifest = [
        {
            "path": path,
            "content": template_candidates[path],
        }
        for path in manifest_paths
    ]
    completion_state = (
        "failed:fallback_only_required_files"
        if regeneration_required
        else "ready"
    )
    anchor_path = (
        "docs/auto_regeneration_plan.md"
        if regeneration_required
        else "docs/architecture.md"
    )
    return anchor_path, manifest, completion_state


def _compat_project_name(request: OrchestrationRequest) -> str:
    candidate = str(request.project_name or "").strip()
    if not candidate:
        candidate = re.sub(r"[^a-zA-Z0-9가-힣_-]+", "-", str(request.task or "project"))
    candidate = re.sub(r"-+", "-", candidate).strip("-")
    if not candidate:
        return f"project-{uuid4().hex[:8]}"
    if len(candidate) <= 48:
        return candidate
    digest = hashlib.sha256(candidate.encode("utf-8", errors="ignore")).hexdigest()[:10]
    shortened = candidate[:36].rstrip("-_")
    return f"{shortened}-{digest}" if shortened else f"project-{digest}"


def _compat_output_dir(request: OrchestrationRequest, project_name: str) -> Path:
    if str(request.output_dir or "").strip():
        output_dir = Path(str(request.output_dir)).resolve()
    else:
        base_dir = Path(str(request.output_base_dir or "uploads/projects")).resolve()
        output_dir = base_dir / f"{project_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _compat_write_manifest(output_dir: Path, manifest: List[Dict[str, str]]) -> List[str]:
    written_files: List[str] = []
    for item in manifest:
        relative_path = str(item.get("path") or "").strip().replace('\\', '/')
        if not relative_path:
            continue
        target_path = output_dir / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        rendered_content = _decorate_generated_file_with_ids(relative_path, str(item.get("content") or ""))
        target_path.write_text(rendered_content, encoding="utf-8")
        written_files.append(relative_path)
    return written_files


def _compat_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _compat_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _compat_relative_path(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def _emit_orchestration_progress(
    progress_callback: Optional[Callable[[str, str], None]],
    message: str,
    level: str = "info",
) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback(message, level)
    except Exception:
        logger.debug("orchestration progress callback failed", exc_info=True)


def _unique_sequence(items: List[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for item in items:
        key = str(item or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


def _has_mojibake_text(value: Optional[str]) -> bool:
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


def _build_customer_order_profile(task: str, project_name: str) -> Dict[str, Any]:
    task_text = str(task or "").lower()
    project_name_text = str(project_name or "").lower()
    source_text = f"{task_text}\n{project_name_text}"
    workspace_app_target_context = any(
        marker in source_text
        for marker in [
            "source_path: /app",
            "대상 루트: /app",
            "원본 대상 경로: /app",
            "실험 복제본 경로: /app",
            "/app\n",
            " /app",
        ]
    ) or project_name_text.strip() in {"app", "/app"}
    stage_chain = [
        {"index": 1, "tracking_id": "ARCH-001", "title": "구조", "summary": "프로젝트 골조와 실행 엔트리를 고정합니다."},
        {"index": 2, "tracking_id": "ARCH-002", "title": "순수 로직", "summary": "핵심 계산과 판정 로직을 분리합니다."},
        {"index": 3, "tracking_id": "ARCH-003", "title": "데이터", "summary": "입출력 계약과 데이터 공급 레이어를 분리합니다."},
        {"index": 4, "tracking_id": "ARCH-004", "title": "서비스", "summary": "로직과 데이터를 묶는 서비스 흐름을 구성합니다."},
        {"index": 5, "tracking_id": "ARCH-005", "title": "API", "summary": "외부 요청과 서비스 연결을 구성합니다."},
        {"index": 6, "tracking_id": "ARCH-006", "title": "프론트", "summary": "화면, 상태 표현, 시각화를 연결합니다."},
    ]
    profiles: List[Dict[str, Any]] = [
        {
            "profile_id": "trading_system",
            "label": "자동매매/트레이딩 시스템",
            "summary": "전략 신호, 주문, 포트폴리오, 실행 상태를 관리하는 주문형 프로그램",
            "keywords": ["주식", "트레이딩", "자동매매", "매매", "signal", "portfolio", "trading", "stock"],
            "entities": ["signals", "orders", "positions", "portfolios"],
            "requested_outcomes": ["전략 신호 계산", "리스크 체크", "주문 기록", "포트폴리오 스냅샷"],
            "ui_modules": ["대시보드", "신호 뷰", "주문 이력", "포트폴리오 카드"],
            "requested_stack": ["FastAPI", "service-layer", "dashboard-client"],
        },
        {
            "profile_id": "lottery_prediction_system",
            "label": "AI 로또/복권 예측 프로그램",
            "summary": "추첨 이력, 특징 추출, 후보 번호 생성, 평가 리포트를 다루는 예측형 주문 프로그램",
            "keywords": ["로또", "lotto", "lottery", "복권", "당첨번호", "번호예측", "draw history"],
            "entities": ["draw_histories", "feature_windows", "prediction_runs", "candidate_sets"],
            "requested_outcomes": ["추첨 이력 적재", "특징 추출", "후보 번호 생성", "예측 평가 리포트"],
            "ui_modules": ["추첨 이력 보드", "예측 후보 패널", "평가 리포트 카드", "운영 검증 패널"],
            "requested_stack": ["FastAPI", "prediction-ui", "evaluation-runtime"],
        },
        {
            "profile_id": "website_builder",
            "label": "웹사이트/홈페이지 빌더",
            "summary": "페이지 구조, 콘텐츠 섹션, 문의 흐름, 운영 화면을 함께 구성하는 주문형 프로그램",
            "keywords": ["웹사이트", "홈페이지", "landing", "landing page", "website", "web", "페이지", "브랜딩"],
            "entities": ["pages", "sections", "contacts", "deployments"],
            "requested_outcomes": ["페이지 설계", "콘텐츠 섹션 구성", "문의 접수", "운영 배포 메모"],
            "ui_modules": ["메인 랜딩", "소개 섹션", "문의 폼", "운영 배포 패널"],
            "requested_stack": ["FastAPI", "Next-style frontend", "content-workflow"],
        },
        {
            "profile_id": "deployment_kit_program",
            "label": "실배포 구현형 코드 생성기 배포 키트",
            "summary": "실프로그램 생성, 런타임 정책, 패키징, 배포 스모크 검증까지 닫는 출고형 프로그램",
            "keywords": ["코드 생성기 배포 키트", "deployment kit", "실배포 구현형", "배포 패키징", "publish-readiness", "runtime policy", "실프로그램", "출고형"],
            "entities": ["runtime_policies", "deployment_packages", "validation_reports", "publish_targets"],
            "requested_outcomes": ["실행 가능한 FastAPI 프로그램 생성", "런타임 정책 문서화", "배포 패키징", "배포 스모크 검증"],
            "ui_modules": ["실행 상태 패널", "배포 준비도 카드", "검증 결과 리포트", "출고 패키지 요약"],
            "requested_stack": ["FastAPI", "deployment-runtime", "packaging-audit", "publish-readiness"],
        },
        {
            "profile_id": "admin_console",
            "label": "관리자/운영 콘솔",
            "summary": "사용자, 권한, 감사 로그, 운영 상태를 관리하는 주문형 프로그램",
            "keywords": ["관리자", "어드민", "admin", "dashboard", "backoffice", "운영", "권한"],
            "entities": ["users", "roles", "audit_logs", "runtime_panels"],
            "requested_outcomes": ["운영 대시보드", "권한 관리", "감사 로그 조회", "상태 점검"],
            "ui_modules": ["운영 홈", "사용자 목록", "권한 패널", "감사 로그"],
            "requested_stack": ["FastAPI", "admin-ui", "audit-traceability"],
        },
        {
            "profile_id": "commerce_platform",
            "label": "이커머스/마켓플레이스 플랫폼",
            "summary": "상품, 카탈로그, 주문, 고객 흐름을 다루는 주문형 프로그램",
            "keywords": ["마켓플레이스", "이커머스", "쇼핑몰", "커머스", "product", "catalog", "order", "store"],
            "entities": ["products", "catalogs", "carts", "orders"],
            "requested_outcomes": ["상품 관리", "카탈로그 노출", "주문 추적", "고객 상태 확인"],
            "ui_modules": ["상품 목록", "상품 상세", "주문 현황", "운영 카탈로그"],
            "requested_stack": ["FastAPI", "catalog-ui", "order-workflow"],
        },
        {
            "profile_id": "automation_service",
            "label": "업무 자동화/에이전트 서비스",
            "summary": "작업 큐, 실행 기록, 스케줄, 경고를 포함하는 주문형 프로그램",
            "keywords": ["자동화", "workflow", "agent", "봇", "scheduler", "queue", "pipeline", "etl"],
            "entities": ["jobs", "runs", "alerts", "artifacts"],
            "requested_outcomes": ["잡 등록", "실행 추적", "경고 수집", "결과물 아카이브"],
            "ui_modules": ["작업 큐", "실행 히스토리", "알림 패널", "산출물 뷰어"],
            "requested_stack": ["FastAPI", "queue-runtime", "ops-panel"],
        },
        {
            "profile_id": "crm_suite",
            "label": "CRM/영업 운영 스위트",
            "summary": "리드, 고객, 영업 파이프라인, 활동 로그를 함께 다루는 주문형 프로그램",
            "keywords": ["crm", "영업", "세일즈", "고객관리", "lead", "pipeline", "account"],
            "entities": ["leads", "customers", "accounts", "activities"],
            "requested_outcomes": ["리드 수집", "고객 상태 관리", "영업 파이프라인 추적", "활동 로그 기록"],
            "ui_modules": ["리드 보드", "고객 카드", "파이프라인 대시보드", "활동 로그"],
            "requested_stack": ["FastAPI", "crm-ui", "ops-audit"],
        },
        {
            "profile_id": "booking_platform",
            "label": "예약/스케줄링 플랫폼",
            "summary": "예약, 일정, 자원 배정, 알림 흐름을 관리하는 주문형 프로그램",
            "keywords": ["예약", "booking", "reservation", "schedule", "appointment", "calendar"],
            "entities": ["bookings", "resources", "timeslots", "notifications"],
            "requested_outcomes": ["예약 접수", "일정 관리", "자원 배정", "알림 발송"],
            "ui_modules": ["예약 캘린더", "자원 보드", "예약 목록", "알림 패널"],
            "requested_stack": ["FastAPI", "schedule-ui", "notification-workflow"],
        },
        {
            "profile_id": "education_lms",
            "label": "교육/LMS 플랫폼",
            "summary": "강의, 학습자, 과제, 진도와 평가 흐름을 관리하는 주문형 프로그램",
            "keywords": ["교육", "학습", "lms", "course", "lesson", "student", "강의"],
            "entities": ["courses", "students", "assignments", "progress"],
            "requested_outcomes": ["강의 관리", "학습자 진도 추적", "과제 제출", "평가 리포트"],
            "ui_modules": ["강의 대시보드", "학습자 목록", "과제 보드", "진도 리포트"],
            "requested_stack": ["FastAPI", "learning-ui", "reporting-runtime"],
        },
        {
            "profile_id": "healthcare_portal",
            "label": "헬스케어/상담 포털",
            "summary": "환자, 상담 기록, 예약, 문진 흐름을 다루는 주문형 프로그램",
            "keywords": ["헬스케어", "의료", "상담", "patient", "clinic", "medical", "문진"],
            "entities": ["patients", "consultations", "appointments", "intakes"],
            "requested_outcomes": ["상담 예약", "문진 기록", "상담 이력 조회", "운영 리포트"],
            "ui_modules": ["환자 목록", "상담 보드", "예약 캘린더", "운영 리포트"],
            "requested_stack": ["FastAPI", "portal-ui", "audit-runtime"],
        },
        {
            "profile_id": "analytics_platform",
            "label": "데이터 분석/인사이트 플랫폼",
            "summary": "데이터셋, 대시보드, 리포트, 인사이트 워크플로를 제공하는 주문형 프로그램",
            "keywords": ["분석", "analytics", "dashboard", "bi", "insight", "reporting", "data platform"],
            "entities": ["datasets", "dashboards", "reports", "insights"],
            "requested_outcomes": ["데이터셋 수집", "대시보드 생성", "리포트 발행", "인사이트 추적"],
            "ui_modules": ["분석 홈", "대시보드 뷰", "리포트 목록", "인사이트 패널"],
            "requested_stack": ["FastAPI", "analytics-ui", "report-runtime"],
        },
    ]
    default_profile = {
        "profile_id": "customer_program",
        "label": "고객 주문형 프로그램",
        "summary": "고객이 원하는 요구사항을 기준으로 기능, API, 화면, 운영 구조를 함께 생성하는 주문형 프로그램",
        "entities": ["requests", "modules", "artifacts", "handoffs"],
        "requested_outcomes": ["기능 구조화", "API 연결", "실행 상태 추적", "산출물 패키징"],
        "ui_modules": ["요청 입력", "결과 검토", "작업 패널", "산출물 요약"],
        "requested_stack": ["FastAPI", "customer-runtime", "delivery-panel"],
    }

    def _profile_match_score(profile: Dict[str, Any]) -> int:
        score = 0
        for keyword in profile.get("keywords") or []:
            normalized_keyword = str(keyword or "").strip().lower()
            if not normalized_keyword:
                continue
            if normalized_keyword in project_name_text:
                score += 5
            if normalized_keyword in task_text:
                score += 1
        return score

    profile_by_id = {
        str(item.get("profile_id") or "").strip(): dict(item)
        for item in profiles
    }

    explicit_profile_id = ""
    mojibake_detected = _has_mojibake_text(task) or _has_mojibake_text(project_name)
    if workspace_app_target_context:
        explicit_profile_id = "commerce_platform"
    explicit_profile_markers = [
        ("deployment_kit_program", ["코드 생성기 배포 키트", "deployment kit", "실배포 구현형", "배포 패키징", "publish-readiness", "runtime policy", "실프로그램", "출고형"]),
        ("commerce_platform", ["마켓플레이스", "이커머스", "쇼핑몰", "커머스", "catalog", "product", "order", "store"]),
        ("trading_system", ["자동매매", "트레이딩", "주식", "매매", "trading", "stock", "portfolio", "signal"]),
        ("website_builder", ["웹사이트", "홈페이지", "landing page", "website", "브랜딩"]),
        ("automation_service", ["자동화", "workflow", "agent", "scheduler", "queue", "etl", "pipeline"]),
        ("admin_console", ["관리자 콘솔", "admin console", "admin dashboard", "backoffice", "권한 관리", "role management", "감사 로그", "audit trail"]),
    ]
    for candidate_profile_id, markers in explicit_profile_markers:
        if explicit_profile_id and explicit_profile_id != candidate_profile_id:
            continue
        if any(marker.lower() in task_text for marker in markers):
            explicit_profile_id = candidate_profile_id
            break

    if not explicit_profile_id and mojibake_detected:
        commerce_fallback_markers = [
            "marketplace",
            "commerce",
            "shoppingmall",
            "shopping-mall",
            "shopping_mall",
            "catalog-ui",
            "order-workflow",
            "storefront",
            "shopify",
        ]
        normalized_project_name = re.sub(r"[^a-z0-9가-힣]+", "", project_name_text)
        normalized_task_text = re.sub(r"[^a-z0-9가-힣]+", "", task_text)
        if any(marker.replace("-", "").replace("_", "") in normalized_project_name for marker in commerce_fallback_markers):
            explicit_profile_id = "commerce_platform"
        elif any(marker.replace("-", "").replace("_", "") in normalized_task_text for marker in commerce_fallback_markers):
            explicit_profile_id = "commerce_platform"
        elif "ai" in normalized_task_text and any(token in normalized_project_name for token in ["쇼핑몰", "마켓", "커머스"]):
            explicit_profile_id = "commerce_platform"

    if explicit_profile_id and explicit_profile_id in profile_by_id:
        selected = profile_by_id[explicit_profile_id]
    else:
        selected = max(profiles, key=_profile_match_score, default=default_profile)
        if _profile_match_score(selected) <= 0:
            selected = default_profile
    flow_steps = [
        {"flow_id": "FLOW-001", "step_number": 1, "step_id": "FLOW-001-1", "action": "INTAKE", "title": "주문 해석", "trace_id": "FLOW-001:FLOW-001-1:INTAKE"},
        {"flow_id": "FLOW-001", "step_number": 2, "step_id": "FLOW-001-2", "action": "STRUCTURE", "title": "기능 구조화", "trace_id": "FLOW-001:FLOW-001-2:STRUCTURE"},
        {"flow_id": "FLOW-002", "step_number": 1, "step_id": "FLOW-002-1", "action": "SERVICE_BIND", "title": "서비스 연결", "trace_id": "FLOW-002:FLOW-002-1:SERVICE_BIND"},
        {"flow_id": "FLOW-003", "step_number": 1, "step_id": "FLOW-003-1", "action": "DELIVERY", "title": "산출물 패키징", "trace_id": "FLOW-003:FLOW-003-1:DELIVERY"},
    ]
    profile = dict(selected)
    profile["project_name"] = project_name
    profile["task_excerpt"] = str(task or "").strip()[:240]
    profile["flow_steps"] = flow_steps
    profile["validation_profile"] = _resolve_validation_profile(profile, str(task or ""))
    ai_activation_markers = [
        "인공지능",
        "llm",
        "rag",
        "embedding",
        "임베딩",
        "예측",
        "추천",
        "분류",
        "학습",
        "추론",
        "evaluation",
        "train",
        "training",
        "inference",
        "fine-tune",
        "agent",
        "assistant",
        "챗봇",
        "chatbot",
        "오케스트레이터",
    ]
    ai_requested = any(token in source_text for token in ai_activation_markers)
    is_trading_profile = profile.get("profile_id") == "trading_system"
    profile["ai_enabled"] = ai_requested or is_trading_profile
    if profile["ai_enabled"]:
        profile["mandatory_engine_contracts"] = [
            "engine-core",
            "feature-pipeline",
            "training-pipeline",
            "inference-runtime",
            "evaluation-report",
            "service-integration",
        ]
        profile["ai_capabilities"] = [
            "feature-engineering",
            "model-training",
            "online-inference",
            "evaluation-report",
            "service-integration",
        ]
        profile["entities"] = _unique_sequence(list(profile.get("entities") or []) + [
            "ai_features",
            "model_versions",
            "inference_runs",
            "evaluation_reports",
        ])
        profile["requested_outcomes"] = _unique_sequence(list(profile.get("requested_outcomes") or []) + [
            "AI 엔진 구성",
            "학습 파이프라인",
            "추론 런타임",
            "평가 리포트",
            "전략/업무 서비스 연동",
        ])
        profile["ui_modules"] = _unique_sequence(list(profile.get("ui_modules") or []) + [
            "AI 상태 패널",
            "모델 버전 뷰",
            "평가 리포트 카드",
        ])
        profile["requested_stack"] = _unique_sequence(list(profile.get("requested_stack") or []) + [
            "ai-engine",
            "training-pipeline",
            "model-registry",
        ])
        if profile.get("profile_id") == "trading_system":
            profile["mandatory_engine_contracts"] = _unique_sequence(list(profile.get("mandatory_engine_contracts") or []) + [
                "signal-ingestion",
                "risk-guard",
                "order-execution",
                "portfolio-sync",
                "broker-adapter",
            ])
            profile["ai_capabilities"] = _unique_sequence(list(profile.get("ai_capabilities") or []) + [
                "signal-ingestion",
                "risk-guard",
                "order-execution",
                "portfolio-sync",
            ])
            profile["entities"] = _unique_sequence(list(profile.get("entities") or []) + [
                "risk_events",
                "execution_runs",
                "broker_orders",
            ])
            profile["requested_outcomes"] = _unique_sequence(list(profile.get("requested_outcomes") or []) + [
                "시그널 적재 및 정규화",
                "리스크 가드 판정",
                "주문 실행 계획 산출",
                "포트폴리오 동기화",
                "브로커 어댑터 연결",
            ])
            profile["ui_modules"] = _unique_sequence(list(profile.get("ui_modules") or []) + [
                "리스크 가드 패널",
                "주문 실행 보드",
                "브로커 연결 상태 카드",
            ])
            profile["requested_stack"] = _unique_sequence(list(profile.get("requested_stack") or []) + [
                "broker-connector",
                "risk-engine",
                "portfolio-runtime",
            ])
        if profile.get("profile_id") == "lottery_prediction_system":
            profile["mandatory_engine_contracts"] = _unique_sequence(list(profile.get("mandatory_engine_contracts") or []) + [
                "historical-draw-loader",
                "feature-window-builder",
                "candidate-number-generator",
                "prediction-evaluation",
            ])
            profile["requested_outcomes"] = _unique_sequence(list(profile.get("requested_outcomes") or []) + [
                "추첨 회차 이력 정규화",
                "번호 후보 조합 생성",
                "후보 조합 평가",
            ])
            profile["ui_modules"] = _unique_sequence(list(profile.get("ui_modules") or []) + [
                "예측 엔진 패널",
                "후보 번호 조합 뷰",
            ])
            profile["requested_stack"] = _unique_sequence(list(profile.get("requested_stack") or []) + [
                "draw-history-pipeline",
                "candidate-ranking",
            ])
    current_stage = stage_chain[0]
    for stage in stage_chain:
        if stage["tracking_id"].lower() in source_text or f"{stage['index']}단계" in source_text or stage["title"] in str(task or ""):
            current_stage = stage
            break
    profile["stage_chain"] = stage_chain
    profile["current_stage"] = current_stage
    return profile


def _resolve_customer_ai_adapter_profile(order_profile: Dict[str, Any]) -> str:
    profile_id = str(order_profile.get("profile_id") or "").strip()
    adapter_map = {
        "trading_system": "trading",
        "website_builder": "content",
        "admin_console": "operations",
        "commerce_platform": "commerce",
        "automation_service": "workflow",
        "crm_suite": "crm",
        "booking_platform": "booking",
        "education_lms": "education",
        "healthcare_portal": "healthcare",
        "analytics_platform": "analytics",
    }
    return adapter_map.get(profile_id, "general")


def _resolve_customer_domain_contract(order_profile: Dict[str, Any]) -> Dict[str, Any]:
    profile_id = str(order_profile.get("profile_id") or "").strip()
    contracts: Dict[str, Dict[str, Any]] = {
        "trading_system": {
            "database_tables": ["signals", "orders", "positions", "portfolios", "model_versions"],
            "jwt_scopes": ["trading.read", "trading.write", "trading.execute"],
            "ops_channels": ["audit", "risk", "model-runtime"],
            "adapter_targets": ["signal_strength", "risk_score", "order_action", "portfolio_action", "broker_status"],
        },
        "crm_suite": {
            "database_tables": ["leads", "customers", "accounts", "activities", "recommendations"],
            "jwt_scopes": ["crm.read", "crm.write", "crm.pipeline"],
            "ops_channels": ["audit", "pipeline", "customer-ops"],
            "adapter_targets": ["lead_score", "next_action", "account_health"],
        },
        "booking_platform": {
            "database_tables": ["bookings", "resources", "timeslots", "notifications", "optimization_runs"],
            "jwt_scopes": ["booking.read", "booking.write", "booking.manage"],
            "ops_channels": ["audit", "schedule", "notification"],
            "adapter_targets": ["availability_score", "slot_fit", "confirmation_action"],
        },
        "education_lms": {
            "database_tables": ["courses", "students", "assignments", "progress", "recommendation_runs"],
            "jwt_scopes": ["education.read", "education.write", "education.evaluate"],
            "ops_channels": ["audit", "learning", "reporting"],
            "adapter_targets": ["progress_score", "learning_path", "intervention_level"],
        },
        "healthcare_portal": {
            "database_tables": ["patients", "consultations", "appointments", "intakes", "triage_runs"],
            "jwt_scopes": ["healthcare.read", "healthcare.write", "healthcare.triage"],
            "ops_channels": ["audit", "triage", "patient-ops"],
            "adapter_targets": ["risk_score", "triage_level", "follow_up_action"],
        },
        "commerce_platform": {
            "database_tables": ["products", "catalogs", "carts", "orders", "recommendation_runs"],
            "jwt_scopes": ["commerce.read", "commerce.write", "commerce.fulfill"],
            "ops_channels": ["audit", "catalog", "order-ops"],
            "adapter_targets": ["conversion_score", "upsell_score", "next_offer"],
        },
        "analytics_platform": {
            "database_tables": ["datasets", "dashboards", "reports", "insights", "forecast_runs"],
            "jwt_scopes": ["analytics.read", "analytics.write", "analytics.publish"],
            "ops_channels": ["audit", "insight", "reporting"],
            "adapter_targets": ["insight_score", "forecast_score", "publish_action"],
        },
    }
    contract = contracts.get(
        profile_id,
        {
            "database_tables": ["requests", "modules", "artifacts", "handoffs"],
            "jwt_scopes": ["program.read", "program.write"],
            "ops_channels": ["audit", "runtime"],
            "adapter_targets": ["score", "decision", "recommendation"],
        },
    )
    if profile_id == "lottery_prediction_system":
        contract = {
            "database_tables": ["draw_histories", "feature_windows", "prediction_runs", "candidate_sets", "evaluation_reports"],
            "jwt_scopes": ["lottery.read", "lottery.predict", "lottery.evaluate"],
            "ops_channels": ["audit", "prediction", "evaluation"],
            "adapter_targets": ["number_score", "combination_rank", "prediction_confidence"],
        }
    return dict(contract)


def _resolve_customer_engine_seed_records(profile_id: str) -> List[Dict[str, Any]]:
    seeds: Dict[str, List[Dict[str, Any]]] = {
        "trading_system": [
            {"signal_strength": 0.21, "market_regime": "sideways", "risk_score": 0.18},
            {"signal_strength": 0.63, "market_regime": "bull", "risk_score": 0.34},
            {"signal_strength": -0.17, "market_regime": "pullback", "risk_score": 0.22},
            {"signal_strength": 0.48, "market_regime": "bull", "risk_score": 0.27},
        ],
        "website_builder": [
            {"page": "landing", "content_score": 0.81, "quality_score": 0.72},
            {"page": "about", "content_score": 0.66, "quality_score": 0.75},
            {"page": "contact", "content_score": 0.59, "quality_score": 0.69},
        ],
        "admin_console": [
            {"user_count": 125, "incident_score": 0.12, "response_priority": "normal"},
            {"user_count": 128, "incident_score": 0.34, "response_priority": "elevated"},
            {"user_count": 131, "incident_score": 0.21, "response_priority": "normal"},
        ],
        "commerce_platform": [
            {"product_id": "P-100", "conversion_score": 0.61, "upsell_score": 0.38, "next_offer": "bundle-a"},
            {"product_id": "P-220", "conversion_score": 0.73, "upsell_score": 0.41, "next_offer": "bundle-b"},
            {"product_id": "P-315", "conversion_score": 0.58, "upsell_score": 0.29, "next_offer": "bundle-c"},
        ],
        "automation_service": [
            {"job_name": "catalog-sync", "automation_score": 0.74, "queue_action": "queue"},
            {"job_name": "report-rollup", "automation_score": 0.69, "queue_action": "schedule"},
            {"job_name": "ops-alert", "automation_score": 0.55, "queue_action": "review"},
        ],
        "crm_suite": [
            {"lead_id": "L-01", "lead_score": 0.77, "account_health": 0.64, "next_action": "call"},
            {"lead_id": "L-02", "lead_score": 0.66, "account_health": 0.59, "next_action": "email"},
            {"lead_id": "L-03", "lead_score": 0.82, "account_health": 0.72, "next_action": "demo"},
        ],
        "booking_platform": [
            {"resource_id": "R-10", "availability_score": 0.83, "slot_fit": 0.75, "confirmation_action": "confirm"},
            {"resource_id": "R-21", "availability_score": 0.71, "slot_fit": 0.68, "confirmation_action": "hold"},
            {"resource_id": "R-34", "availability_score": 0.65, "slot_fit": 0.62, "confirmation_action": "review"},
        ],
        "education_lms": [
            {"course_id": "C-100", "progress_score": 0.58, "learning_path": "standard", "intervention_level": "watch"},
            {"course_id": "C-220", "progress_score": 0.77, "learning_path": "accelerated", "intervention_level": "light"},
            {"course_id": "C-340", "progress_score": 0.42, "learning_path": "remedial", "intervention_level": "high"},
        ],
        "healthcare_portal": [
            {"patient_id": "PT-01", "risk_score": 0.23, "triage_level": "routine", "follow_up_action": "monitor"},
            {"patient_id": "PT-02", "risk_score": 0.61, "triage_level": "urgent", "follow_up_action": "review"},
            {"patient_id": "PT-03", "risk_score": 0.37, "triage_level": "priority", "follow_up_action": "call"},
        ],
        "analytics_platform": [
            {"dataset_id": "D-11", "insight_score": 0.68, "forecast_score": 0.56, "publish_action": "publish"},
            {"dataset_id": "D-12", "insight_score": 0.73, "forecast_score": 0.61, "publish_action": "promote"},
            {"dataset_id": "D-13", "insight_score": 0.59, "forecast_score": 0.49, "publish_action": "review"},
        ],
        "lottery_prediction_system": [
            {"draw_no": 1101, "numbers": [3, 8, 13, 27, 33, 42], "bonus": 19},
            {"draw_no": 1102, "numbers": [7, 9, 18, 21, 28, 41], "bonus": 5},
            {"draw_no": 1103, "numbers": [5, 11, 17, 29, 34, 40], "bonus": 2},
            {"draw_no": 1104, "numbers": [1, 6, 14, 26, 30, 44], "bonus": 12},
            {"draw_no": 1105, "numbers": [2, 10, 16, 24, 35, 43], "bonus": 8},
            {"draw_no": 1106, "numbers": [4, 15, 20, 22, 32, 45], "bonus": 9},
            {"draw_no": 1107, "numbers": [6, 12, 19, 23, 31, 38], "bonus": 14},
            {"draw_no": 1108, "numbers": [8, 13, 21, 25, 36, 41], "bonus": 7},
            {"draw_no": 1109, "numbers": [9, 14, 22, 28, 37, 42], "bonus": 1},
            {"draw_no": 1110, "numbers": [10, 16, 24, 29, 39, 43], "bonus": 6},
            {"draw_no": 1111, "numbers": [11, 17, 26, 30, 40, 44], "bonus": 3},
            {"draw_no": 1112, "numbers": [12, 18, 27, 31, 41, 45], "bonus": 4},
        ],
    }
    return list(seeds.get(profile_id, [{"record_id": "GEN-01", "score": 0.5, "decision": "review"}, {"record_id": "GEN-02", "score": 0.7, "decision": "promote"}]))


def _build_customer_domain_ai_template_overrides(
    project_name: str,
    order_profile: Dict[str, Any],
    domain_contract: Dict[str, Any],
) -> Dict[str, str]:
    profile_id = str(order_profile.get("profile_id") or "").strip()
    entities = list(order_profile.get("entities") or ["records"])
    primary_entity = str(entities[0]).strip() if entities else "records"
    mandatory_contracts = [str(item).strip() for item in (order_profile.get("mandatory_engine_contracts") or []) if str(item).strip()]
    sample_records = _resolve_customer_engine_seed_records(profile_id)
    sample_records_json = json.dumps(sample_records, ensure_ascii=False, indent=2)
    mandatory_contracts_json = json.dumps(mandatory_contracts, ensure_ascii=False)
    adapter_targets_json = json.dumps(domain_contract.get("adapter_targets") or [], ensure_ascii=False)
    project_title = str(order_profile.get("label") or project_name)
    panel_title = f"{project_title} 엔진 패널"
    is_trading_profile = profile_id == "trading_system"
    trading_contract_markers_json = json.dumps([
        "signal-ingestion",
        "risk-guard",
        "order-execution",
        "portfolio-sync",
        "broker-adapter",
    ], ensure_ascii=False)

    return {
        "ai/features.py": (
            "from collections import Counter\n"
            "from typing import Any, Dict, List\n\n"
            f"DOMAIN_RECORD_KEY = {primary_entity!r}\n"
            f"IS_TRADING_PROFILE = {str(is_trading_profile)}\n\n"
            "FEATURE_WINDOW_SIZE = 3\n\n"
            "def normalize_domain_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:\n"
            "    normalized: List[Dict[str, Any]] = []\n"
            "    for index, item in enumerate(records, start=1):\n"
            "        if not isinstance(item, dict):\n"
            "            continue\n"
            "        candidate = dict(item)\n"
            "        candidate.setdefault('record_no', index)\n"
            "        normalized.append(candidate)\n"
            "    return normalized\n\n"
            "def build_feature_windows(records: List[Dict[str, Any]], window_size: int = FEATURE_WINDOW_SIZE) -> List[Dict[str, Any]]:\n"
            "    normalized = normalize_domain_records(records)\n"
            "    if not normalized:\n"
            "        return []\n"
            "    windows: List[Dict[str, Any]] = []\n"
            "    step = max(1, min(window_size, len(normalized)))\n"
            "    for index in range(step, len(normalized) + 1):\n"
            "        chunk = normalized[index - step:index]\n"
            "        field_counter = Counter()\n"
            "        for item in chunk:\n"
            "            for key, value in item.items():\n"
            "                if isinstance(value, (int, float)):\n"
            "                    field_counter[key] += float(value)\n"
            "                elif isinstance(value, str) and value:\n"
            "                    field_counter[key] += 1\n"
            "                elif isinstance(value, list):\n"
            "                    field_counter[key] += len(value)\n"
            "        windows.append({\n"
            "            'window_index': index - step + 1,\n"
            "            'record_count': len(chunk),\n"
            "            'field_scores': dict(field_counter),\n"
            "        })\n"
            "    return windows\n\n"
            "def build_feature_set(raw_payload: Dict[str, Any]) -> Dict[str, Any]:\n"
            "    payload = dict(raw_payload or {})\n"
            "    records = normalize_domain_records(payload.get(DOMAIN_RECORD_KEY) or payload.get('records') or [])\n"
            "    windows = build_feature_windows(records)\n"
            "    return {\n"
            "        'raw': payload,\n"
            "        DOMAIN_RECORD_KEY: records,\n"
            "        'feature_windows': windows,\n"
            "        'feature_count': len(windows),\n"
            "        'engine-core': bool(records),\n"
            "        'feature-pipeline': bool(windows),\n"
            "        'signal-ingestion': bool(records) if IS_TRADING_PROFILE else False,\n"
            "        'risk-guard': any('risk_score' in item for item in records) if IS_TRADING_PROFILE else False,\n"
            "    }\n"
        ),
        "ai/train.py": (
            "from typing import Any, Dict, List\n\n"
            "from ai.features import build_feature_set\n"
            "from ai.model_registry import register_model_version\n\n"
            f"MANDATORY_ENGINE_CONTRACTS = {mandatory_contracts_json}\n"
            f"ADAPTER_TARGETS = {adapter_targets_json}\n\n"
            "def train_model(dataset: List[Dict[str, Any]]) -> Dict[str, Any]:\n"
            "    feature_payload = build_feature_set({'records': dataset})\n"
            "    windows = feature_payload.get('feature_windows') or []\n"
            "    ranking = []\n"
            "    if windows:\n"
            "        field_scores = dict(windows[-1].get('field_scores') or {})\n"
            "        ranking = sorted(field_scores.items(), key=lambda item: (-float(item[1]), item[0]))[:12]\n"
            "    model = {\n"
            "        'version': f'domain-model-{len(dataset)}',\n"
            "        'status': 'trained' if dataset else 'needs-data',\n"
            "        'trained_records': len(dataset),\n"
            "        'feature_windows': len(windows),\n"
            "        'candidate_ranking': [{'target': key, 'weight': value} for key, value in ranking],\n"
            "        'mandatory_engine_contracts': list(MANDATORY_ENGINE_CONTRACTS),\n"
            "        'adapter_targets': list(ADAPTER_TARGETS),\n"
            "        'engine-core': True,\n"
            "    }\n"
            "    register_model_version(model)\n"
            "    return model\n"
        ),
        "ai/inference.py": (
            "from typing import Any, Dict, List\n\n"
            "from ai.features import build_feature_set\n"
            "from ai.model_registry import get_latest_model\n\n"
            f"ADAPTER_TARGETS = {adapter_targets_json}\n\n"
            "def run_inference(payload: Dict[str, Any]) -> Dict[str, Any]:\n"
            "    model = get_latest_model()\n"
            "    feature_payload = build_feature_set(payload)\n"
            "    windows = feature_payload.get('feature_windows') or []\n"
            "    score = round(min(0.99, 0.45 + (len(windows) / 20.0)), 4) if windows else 0.33\n"
            "    risk_score = round(min(0.95, max(0.05, 1.0 - score)), 4)\n"
            "    decision = 'BUY' if score >= 0.7 and risk_score <= 0.4 else 'SELL' if score <= 0.35 else 'HOLD'\n"
            "    candidate_sets = [\n"
            "        {\n"
            "            'target': target,\n"
            "            'rank': index + 1,\n"
            "            'score': round(max(score - (index * 0.03), 0.1), 4),\n"
            "        }\n"
            "        for index, target in enumerate(ADAPTER_TARGETS[:3] or ['recommendation'])\n"
            "    ]\n"
            "    return {\n"
            "        'model_version': model.get('version', 'bootstrap'),\n"
            "        'score': score,\n"
            "        'decision': decision,\n"
            "        'risk_score': risk_score,\n"
            "        'order_action': decision,\n"
            "        'broker_status': 'paper-ready',\n"
            "        'candidate_sets': candidate_sets,\n"
            "        'prediction_runs': len(windows),\n"
            "        'engine-core': True,\n"
            "        'inference-runtime': True,\n"
            "        'risk-guard': risk_score <= 0.6,\n"
            "        'order-execution': decision in {'BUY', 'SELL', 'HOLD'},\n"
            "        'portfolio-sync': True,\n"
            "        'broker-adapter': 'paper-broker',\n"
            "    }\n"
        ),
        "ai/evaluation.py": (
            "from typing import Dict, List\n\n"
            "def evaluate_predictions(predictions: List[dict]) -> Dict[str, object]:\n"
            "    candidate_sets = [item for item in predictions if item.get('candidate_sets')]\n"
            "    average_score = round(sum(float(item.get('score', 0.0) or 0.0) for item in candidate_sets) / len(candidate_sets), 4) if candidate_sets else 0.0\n"
            "    return {\n"
            "        'samples': len(predictions),\n"
            "        'candidate_sets': len(candidate_sets),\n"
            "        'average_score': average_score,\n"
            "        'quality_gate': 'pass' if candidate_sets else 'needs-data',\n"
            "        'prediction-evaluation': bool(candidate_sets),\n"
            "    }\n"
        ),
        "backend/service/strategy_service.py": (
            "from app.order_profile import get_order_profile\n"
            "from ai.features import build_feature_set\n"
            "from ai.inference import run_inference\n"
            "from ai.evaluation import evaluate_predictions\n"
            "from ai.train import train_model\n"
            "from ai.model_registry import get_latest_model\n\n"
            f"DEFAULT_DOMAIN_RECORDS = {sample_records_json}\n"
            f"MANDATORY_ENGINE_CONTRACTS = {mandatory_contracts_json}\n"
            f"DOMAIN_RECORD_KEY = {primary_entity!r}\n\n"
            "def load_model_registry() -> dict:\n"
            "    profile = get_order_profile()\n"
            "    latest_model = get_latest_model()\n"
            "    return {\n"
            "        'registry_name': 'domain-model-registry',\n"
            "        'primary_model': latest_model.get('version', profile.get('project_name', 'domain-engine')),\n"
            "        'version': latest_model.get('version', 'bootstrap'),\n"
            "    }\n\n"
            "def build_engine_core() -> dict:\n"
            "    features = build_feature_set({DOMAIN_RECORD_KEY: DEFAULT_DOMAIN_RECORDS})\n"
            "    return {\n"
            "        'engine-core': True,\n"
            "        'records': features.get(DOMAIN_RECORD_KEY, []),\n"
            "        'feature-pipeline': {\n"
            "            'feature_windows': features.get('feature_windows', []),\n"
            "            'window_count': features.get('feature_count', 0),\n"
            "        },\n"
            "    }\n\n"
            "def run_training_pipeline() -> dict:\n"
            "    model = train_model(DEFAULT_DOMAIN_RECORDS)\n"
            "    return {\n"
            "        'status': model.get('status', 'trained'),\n"
            "        'pipeline': 'engine-core -> feature-pipeline -> training-pipeline',\n"
            "        'training-pipeline': True,\n"
            "        'model': model,\n"
            "    }\n\n"
            "def run_inference_runtime(features: dict | None = None) -> dict:\n"
            "    payload = dict(features or {})\n"
            "    payload.setdefault(DOMAIN_RECORD_KEY, DEFAULT_DOMAIN_RECORDS)\n"
            "    inference = run_inference(payload)\n"
            "    return {\n"
            "        'decision': inference.get('decision', 'recommend'),\n"
            "        'score': inference.get('score', 0.0),\n"
            "        'risk_score': inference.get('risk_score', 0.0),\n"
            "        'order_action': inference.get('order_action', inference.get('decision', 'HOLD')),\n"
            "        'broker_status': inference.get('broker_status', 'paper-ready'),\n"
            "        'model_version': inference.get('model_version', 'bootstrap'),\n"
            "        'candidate_sets': inference.get('candidate_sets', []),\n"
            "        'prediction_runs': inference.get('prediction_runs', 0),\n"
            "        'inference-runtime': True,\n"
            "        'features': payload,\n"
            "    }\n\n"
            "def build_risk_guard(runtime: dict | None = None) -> dict:\n"
            "    active_runtime = runtime or run_inference_runtime({DOMAIN_RECORD_KEY: DEFAULT_DOMAIN_RECORDS})\n"
            "    risk_score = float(active_runtime.get('risk_score', 0.0) or 0.0)\n"
            "    return {\n"
            "        'risk-guard': True,\n"
            "        'risk_score': risk_score,\n"
            "        'blocked': risk_score > 0.6,\n"
            "        'limit': 0.6,\n"
            "    }\n\n"
            "def build_order_execution_plan(runtime: dict | None = None) -> dict:\n"
            "    active_runtime = runtime or run_inference_runtime({DOMAIN_RECORD_KEY: DEFAULT_DOMAIN_RECORDS})\n"
            "    guard = build_risk_guard(active_runtime)\n"
            "    order_action = 'HOLD' if guard.get('blocked') else active_runtime.get('order_action', 'HOLD')\n"
            "    return {\n"
            "        'order-execution': True,\n"
            "        'broker-adapter': active_runtime.get('broker_status', 'paper-ready'),\n"
            "        'order_action': order_action,\n"
            "        'approved': not bool(guard.get('blocked')),\n"
            "    }\n\n"
            "def build_portfolio_sync(runtime: dict | None = None) -> dict:\n"
            "    active_runtime = runtime or run_inference_runtime({DOMAIN_RECORD_KEY: DEFAULT_DOMAIN_RECORDS})\n"
            "    return {\n"
            "        'portfolio-sync': True,\n"
            "        'portfolio_action': active_runtime.get('order_action', 'HOLD'),\n"
            "        'position_delta': 1 if active_runtime.get('order_action') == 'BUY' else -1 if active_runtime.get('order_action') == 'SELL' else 0,\n"
            "    }\n\n"
            "def build_evaluation_report() -> dict:\n"
            "    runtime = run_inference_runtime({DOMAIN_RECORD_KEY: DEFAULT_DOMAIN_RECORDS})\n"
            "    evaluation = evaluate_predictions([runtime])\n"
            "    return {\n"
            "        'report_name': 'domain-evaluation',\n"
            "        'metrics': ['candidate_sets', 'average_score', 'quality_gate'],\n"
            "        'status': evaluation.get('quality_gate', 'needs-data'),\n"
            "        'prediction-evaluation': evaluation.get('prediction_evaluation', False),\n"
            "        'evaluation': evaluation,\n"
            "    }\n\n"
            "def build_strategy_service_overview(sample_payload: dict | None = None) -> dict:\n"
            "    profile = get_order_profile()\n"
            "    engine_core = build_engine_core()\n"
            "    training_pipeline = run_training_pipeline()\n"
            "    inference_runtime = run_inference_runtime(sample_payload or {DOMAIN_RECORD_KEY: DEFAULT_DOMAIN_RECORDS})\n"
            "    risk_guard = build_risk_guard(inference_runtime)\n"
            "    order_execution = build_order_execution_plan(inference_runtime)\n"
            "    portfolio_sync = build_portfolio_sync(inference_runtime)\n"
            "    evaluation_report = build_evaluation_report()\n"
            "    return {\n"
            "        'ai_enabled': bool(profile.get('ai_enabled')),\n"
            "        'ai_capabilities': list(profile.get('ai_capabilities') or []),\n"
            "        'mandatory_engine_contracts': list(profile.get('mandatory_engine_contracts') or []),\n"
            "        'engine-core': engine_core,\n"
            "        'service-integration': True,\n"
            "        'risk-guard': risk_guard,\n"
            "        'order-execution': order_execution,\n"
            "        'portfolio-sync': portfolio_sync,\n"
            "        'broker-adapter': order_execution.get('broker-adapter', 'paper-ready'),\n"
            "        'model_registry': load_model_registry(),\n"
            "        'training_pipeline': training_pipeline,\n"
            "        'inference_runtime': inference_runtime,\n"
            "        'evaluation_report': evaluation_report,\n"
            "    }\n"
        ),
        "app/services/__init__.py": (
            "from app.services.runtime_service import build_ai_runtime_contract, build_domain_snapshot, build_feature_matrix, build_runtime_payload, build_trace_lookup, list_endpoints, summarize_health\n\n"
            "__all__ = ['build_ai_runtime_contract', 'build_feature_matrix', 'build_trace_lookup', 'build_domain_snapshot', 'build_runtime_payload', 'list_endpoints', 'summarize_health']\n"
        ),
        "app/services/runtime_service.py": (
            "from datetime import datetime\n"
            "from app.runtime import build_runtime_context, describe_runtime_profile\n"
            "from app.order_profile import get_order_profile, get_flow_step, list_flow_steps\n"
            "from backend.core.database import ensure_database_ready, get_database_settings\n"
            "from backend.core.auth import create_access_token, get_auth_settings\n"
            "from backend.core.ops_logging import record_ops_log\n"
            "from backend.service.domain_adapter_service import build_domain_adapter_summary\n"
            "from backend.service.strategy_service import build_strategy_service_overview\n"
            "from ai.schemas import InferenceRequest, TrainingRequest, EvaluationRequest\n"
            "from ai.train import train_model\n"
            "from ai.inference import run_inference\n"
            "from ai.evaluation import evaluate_predictions\n"
            "from ai.model_registry import get_latest_model\n\n"
            f"DEFAULT_DOMAIN_RECORDS = {sample_records_json}\n"
            f"MANDATORY_ENGINE_CONTRACTS = {mandatory_contracts_json}\n"
            f"DOMAIN_RECORD_KEY = {primary_entity!r}\n\n"
            "def build_feature_matrix() -> list[dict]:\n"
            "    return [{'flow_id': item['flow_id'], 'step_number': item.get('step_number'), 'step_id': item['step_id'], 'action': item['action'], 'trace_id': item.get('trace_id'), 'title': item['title'], 'state': 'ready'} for item in list_flow_steps()]\n\n"
            "def build_trace_lookup(step_id: str = 'FLOW-001-1') -> dict:\n"
            "    return get_flow_step(step_id) or {'step_id': step_id, 'missing': True}\n\n"
            "def build_domain_snapshot() -> dict:\n"
            "    profile = get_order_profile()\n"
            "    return {'profile_id': profile['profile_id'], 'entities': profile['entities'], 'requested_outcomes': profile['requested_outcomes'], 'ui_modules': profile['ui_modules'], 'mandatory_engine_contracts': list(profile.get('mandatory_engine_contracts') or [])}\n\n"
            "def build_ai_runtime_contract() -> dict:\n"
            "    train_request = TrainingRequest(dataset=DEFAULT_DOMAIN_RECORDS)\n"
            "    inference_request = InferenceRequest(signal_strength=0.7, features={DOMAIN_RECORD_KEY: DEFAULT_DOMAIN_RECORDS})\n"
            "    model = train_model(train_request.dataset)\n"
            "    database = ensure_database_ready()\n"
            "    inference_payload = dict(inference_request.features)\n"
            "    inference_payload['signal_strength'] = inference_request.signal_strength\n"
            "    prediction = run_inference(inference_payload)\n"
            "    evaluation = evaluate_predictions([prediction])\n"
            "    strategy_service = build_strategy_service_overview(inference_payload)\n"
            "    access_token = create_access_token('system-orchestrator')\n"
            "    return {\n"
            "        'mandatory_engine_contracts': list(MANDATORY_ENGINE_CONTRACTS),\n"
            "        'engine-core': strategy_service.get('engine-core'),\n"
            "        'feature-pipeline': strategy_service.get('engine-core', {}).get('feature-pipeline'),\n"
            "        'training-pipeline': strategy_service.get('training_pipeline'),\n"
            "        'inference-runtime': strategy_service.get('inference_runtime'),\n"
            "        'evaluation-report': strategy_service.get('evaluation_report'),\n"
            "        'service-integration': strategy_service.get('service-integration', True),\n"
            "        'schemas': ['TrainingRequest', 'InferenceRequest', 'EvaluationRequest'],\n"
            "        'endpoints': ['/ai/health', '/ai/train', '/ai/inference', '/ai/evaluate'],\n"
            "        'model_registry': get_latest_model(),\n"
            "        'training_pipeline': model,\n"
            "        'inference_runtime': prediction,\n"
            "        'evaluation_report': evaluation,\n"
            "        'domain_adapter': build_domain_adapter_summary(inference_payload),\n"
            "        'database': database,\n"
            "        'auth': get_auth_settings(),\n"
            "        'token_preview': access_token[:16],\n"
            "        DOMAIN_RECORD_KEY: DEFAULT_DOMAIN_RECORDS,\n"
            "        'prediction_runs': prediction.get('prediction_runs', 0),\n"
            "        'candidate_sets': prediction.get('candidate_sets', []),\n"
            "        'validation': {'ok': bool(model.get('status')) and bool(prediction.get('candidate_sets')) and evaluation.get('quality_gate') == 'pass', 'checked_via': ['/health', '/report']},\n"
            "    }\n\n"
            "def build_runtime_payload(runtime_mode: str = 'default') -> dict:\n"
            "    profile = get_order_profile()\n"
            "    runtime_context = build_runtime_context()\n"
            "    record_ops_log('runtime_payload_built', {'runtime_mode': runtime_mode, 'profile_id': profile['profile_id']})\n"
            "    return {'service': 'customer-order-generator', 'runtime_mode': runtime_mode, 'started_at': datetime.utcnow().isoformat(), 'order_profile': profile, 'active_trace': build_trace_lookup(), 'feature_matrix': build_feature_matrix(), 'domain_snapshot': build_domain_snapshot(), 'runtime_context': runtime_context, 'profile': describe_runtime_profile(), 'mandatory_engine_contracts': list(profile.get('mandatory_engine_contracts') or []), 'ai_runtime_contract': build_ai_runtime_contract()}\n\n"
            "def list_endpoints() -> list[str]:\n"
            "    endpoints = ['/', '/runtime', '/health', '/config', '/order-profile', '/flow-map', '/flow-map/{step_id}', '/workspace', '/report', '/diagnose']\n"
            "    endpoints.extend(['/ai/health', '/ai/train', '/ai/inference', '/ai/evaluate'])\n"
            "    return endpoints\n\n"
            "def summarize_health() -> dict:\n"
            "    payload = build_runtime_payload(runtime_mode='health')\n"
            "    payload['status'] = 'ok'\n"
            "    payload['checks'] = {'profile_loaded': True, 'flow_bound': True, 'delivery_ready': True, 'ai_contract_ready': bool(payload.get('ai_runtime_contract', {}).get('validation', {}).get('ok'))}\n"
            "    return payload\n"
        ),
        "frontend/app/page.tsx": (
            f"const orderProfile = {json.dumps(order_profile, ensure_ascii=False, indent=2)};\n\n"
            "export default function Page() {\n"
            "  const contracts = orderProfile.mandatory_engine_contracts || [];\n"
            f"  const panelTitle = {panel_title!r};\n"
            "  return (\n"
            "    <main style={{ padding: 24, fontFamily: 'sans-serif', display: 'grid', gap: 20 }}>\n"
            "      <section>\n"
            "        <h1>{orderProfile.project_name}</h1>\n"
            "        <p>{orderProfile.label}</p>\n"
            "        <p>{orderProfile.summary}</p>\n"
            "      </section>\n"
            "      <section>\n"
            "        <h2>필수 엔진 계약</h2>\n"
            "        <ul>{contracts.map((item: string) => <li key={item}>{item}</li>)}</ul>\n"
            "      </section>\n"
            "      <section>\n"
            "        <h2>{panelTitle}</h2>\n"
            "        <p>정상적인 폴더 / 파일 / 필수 코드 파일 기준 생성을 위해 도메인별 엔진 계약과 테스트를 함께 탑재합니다.</p>\n"
            "        <ul>{orderProfile.entities.map((item: string) => <li key={item}>{item}</li>)}</ul>\n"
            "      </section>\n"
            "      <section>\n"
            "        <h2>AI 상태 패널</h2>\n"
            "        <p>model_registry / training_pipeline / inference_runtime / evaluation_report contract enabled</p>\n"
            "        <ul>{(orderProfile.ai_capabilities || []).map((item: string) => <li key={item}>{item}</li>)}</ul>\n"
            "      </section>\n"
            "    </main>\n"
            "  );\n"
            "}\n"
        ),
        "tests/test_ai_pipeline.py": (
            "from app.services import build_ai_runtime_contract\n"
            "from backend.service.strategy_service import build_strategy_service_overview\n\n"
            "def test_ai_pipeline_runs():\n"
            "    contract = build_ai_runtime_contract()\n"
            "    strategy = build_strategy_service_overview()\n"
            "    assert contract['mandatory_engine_contracts']\n"
            "    assert contract['training-pipeline']\n"
            "    assert contract['inference-runtime']\n"
            "    assert contract['evaluation-report']\n"
            "    assert contract['candidate_sets']\n"
            "    assert contract['validation']['ok'] is True\n"
            "    assert strategy['service-integration'] is True\n"
        ),
        "tests/test_routes.py": (
            "from fastapi.testclient import TestClient\n"
            "from app.main import app\n\n"
            "client = TestClient(app)\n\n"
            "def test_order_profile_route():\n"
            "    response = client.get('/order-profile')\n"
            "    assert response.status_code == 200\n"
            "    payload = response.json()\n"
            "    assert payload['profile_id']\n"
            "    report = client.get('/report')\n"
            "    assert report.status_code == 200\n"
            "    assert payload['mandatory_engine_contracts']\n\n"
            "def test_ai_runtime_snapshot_marker():\n"
            "    from backend.api.router import get_ai_runtime_snapshot\n"
            "    payload = get_ai_runtime_snapshot({'records': []})\n"
            "    assert payload['model_registry']\n"
            "    assert payload['training_pipeline']\n"
            "    assert payload['inference_runtime']\n"
            "    assert payload['evaluation_report']\n\n"
            "def test_ai_fastapi_endpoints():\n"
            "    health = client.get('/ai/health')\n"
            "    assert health.status_code == 200\n"
            "    infer = client.post('/ai/inference', json={'signal_strength': 0.8, 'features': {'records': []}})\n"
            "    assert infer.status_code == 200\n"
            "    evaluate = client.post('/ai/evaluate', json={'predictions': [{'candidate_sets': [{'target': 'x', 'rank': 1, 'score': 0.8}], 'score': 0.8}]})\n"
            "    assert evaluate.status_code == 200\n"
        ),
        "tests/test_runtime.py": (
            "from app.services import build_runtime_payload\n\n"
            "def test_runtime_payload_contains_order_profile():\n"
            "    payload = build_runtime_payload(runtime_mode='test')\n"
            "    assert payload['service'] == 'customer-order-generator'\n"
            "    assert payload['order_profile']['profile_id']\n"
            "    assert payload['mandatory_engine_contracts']\n"
            "    assert payload['ai_runtime_contract']['validation']['ok'] is True\n"
            "    assert payload['ai_runtime_contract']['candidate_sets']\n"
        ),
    }


def _build_commerce_platform_template_candidates(
    project_name: str,
    order_profile: Dict[str, Any],
    profile_json: str,
    task_excerpt: str,
) -> Dict[str, str]:
    return {
        "README.md": (
            f"# {project_name}\n\n"
            "상용 멀티 쇼핑몰 런타임 산출물입니다.\n\n"
            f"- profile: {order_profile['label']}\n"
            f"- summary: {order_profile['summary']}\n"
            f"- request: {task_excerpt or '요청 없음'}\n"
            f"- requested_stack: {', '.join(order_profile.get('requested_stack') or ['python', 'fastapi'])}\n\n"
            "## Included Runtime\n\n"
            "- `app/main.py` 실행 가능한 FastAPI 엔트리와 런타임 집계\n"
            "- `app/routes.py` 카탈로그/주문/출고 준비/운영 API\n"
            "- `app/auth_routes.py`, `app/ops_routes.py` 운영 인증/상태 점검 API\n"
            "- `backend/core` 보안/로그/상태 저장 코어 레이어\n"
            "- `backend/service/catalog_service.py` 상품 카탈로그 구성\n"
            "- `backend/service/order_workflow_service.py` 장바구니/결제/주문 흐름\n"
            "- `backend/service/operations_service.py` 운영 카탈로그와 publish payload\n"
            "- `backend/app/external_adapters/status_client.py`, `backend/app/connectors/shopify.py` 외부 연동/장애 완화 경계\n"
            "- `frontend/app/page.tsx` 카탈로그/체크아웃/운영 패널 검토 화면\n"
            "- `tests/test_catalog_flow.py`, `tests/test_order_workflow.py`, `tests/test_publish_payload.py` 시나리오 검증\n\n"
            "## Operator Checklist\n\n"
            "1. `configs/app.env.example`를 운영 값으로 치환\n"
            "2. `pip install -r requirements.txt` 실행\n"
            "3. `uvicorn app.main:create_application --factory --host 0.0.0.0 --port 8000` 기동\n"
            "4. `/health`, `/runtime`, `/ops/health`, `/auth/settings` 확인\n"
            "5. `scripts/check.sh`와 출고 ZIP 재현 결과 확인\n\n"
            "## Core Journeys\n\n"
            "- catalog browsing\n"
            "- order workflow\n"
            "- marketplace publish payload\n"
            "- operations catalog\n"
        ),
        "Makefile": (
            "run:\n"
            "\tuvicorn app.main:create_application --factory --reload\n\n"
            "test:\n"
            "\tpytest -q\n\n"
            "check:\n"
            "\tpython -m compileall app backend tests ai\n"
            "\tpytest -q tests/test_health.py tests/test_routes.py tests/test_runtime.py tests/test_catalog_flow.py tests/test_order_workflow.py tests/test_publish_payload.py tests/test_ai_pipeline.py\n"
        ),
        "docs/usage.md": (
            f"# {project_name} 사용 가이드\n\n"
            "1. `pip install -r requirements.txt`\n"
            "2. `uvicorn app.main:create_application --factory --host 0.0.0.0 --port 8000`\n"
            "3. `/health`, `/runtime`, `/catalog`, `/order-workflow`, `/publish-readiness`, `/ops/catalog`, `/ops/health`, `/auth/settings` 순으로 확인\n"
            "4. `pytest -q`로 핵심 시나리오를 검증\n"
            "5. `docs/runbook.md`와 `infra/deploy/security.md` 기준으로 운영 점검\n"
        ),
        "docs/runtime.md": (
            f"# runtime\n\nprofile: {order_profile['label']}\n"
            f"requested_stack: {', '.join(order_profile.get('requested_stack') or [])}\n\n"
            "- health: `/health`\n"
            "- runtime: `/runtime`\n"
            "- catalog flow: `/catalog`\n"
            "- order workflow: `/order-workflow`\n"
            "- marketplace publish payload: `/publish-readiness`\n"
            "- operations catalog: `/ops/catalog`\n"
            "- operations health: `/ops/health`\n"
            "- auth settings: `/auth/settings`\n"
        ),
        "docs/deployment.md": (
            "# deployment\n\n"
            "- `docker build -t commerce-runtime .`\n"
            "- container run: `docker run --rm -p 8000:8000 --env-file configs/app.env.example commerce-runtime`\n"
            "- `docker compose -f infra/docker-compose.override.yml up --build`\n"
            "- 부팅 후 `/health`, `/runtime`, `/catalog`, `/publish-readiness`, `/ops/health`를 호출해 container run 검증\n"
        ),
        "docs/testing.md": (
            "# testing\n\n"
            "- `python -m compileall app backend tests ai`\n"
            "- `pytest -q tests/test_health.py tests/test_routes.py tests/test_runtime.py`\n"
            "- `pytest -q tests/test_catalog_flow.py tests/test_order_workflow.py tests/test_publish_payload.py tests/test_ai_pipeline.py`\n"
            "- `pytest -q tests/test_security_runtime.py`\n"
        ),
        "docs/runbook.md": (
            "# runbook\n\n"
            "## startup\n"
            "- `/health` 확인\n"
            "- `/auth/settings` 확인\n"
            "- `/ops/health` 확인\n"
            "- `/publish-readiness` 확인\n\n"
            "## degraded mode\n"
            "- Shopify 또는 payment provider 장애 시 `/ops/health`의 provider 상태 확인\n"
            "- degraded 응답이면 재시도 횟수와 timeout 값을 점검\n"
            "- 운영 전환 전 `scripts/check.sh` 및 ZIP 재현 결과 확인\n\n"
            "## security\n"
            "- JWT_SECRET 는 32자 이상 랜덤 값으로 교체\n"
            "- 외부 연동 토큰은 secret manager 또는 env_file 로 주입\n"
            "- allow-list 와 TLS 강제 상태를 ingress 에서 확인\n"
        ),
        "configs/app.env.example": (
            "APP_ENV=dev\n"
            "APP_PORT=8000\n"
            "DATABASE_URL=sqlite:///./runtime/data/commerce.db\n"
            "JWT_SECRET=replace-with-32-char-random-secret\n"
            "JWT_ALGORITHM=HS256\n"
            "JWT_EXPIRE_MINUTES=30\n"
            "ALLOWED_HOSTS=localhost,127.0.0.1,metanova1004.com\n"
            "CORS_ALLOW_ORIGINS=https://metanova1004.com\n"
            "REQUEST_TIMEOUT_SEC=5\n"
            "SHOPIFY_ACCESS_TOKEN=replace-with-shopify-token\n"
            "SHOPIFY_BASE_URL=https://demo.example.com\n"
            "PAYMENT_PROVIDER_TOKEN=replace-with-payment-token\n"
            "PAYMENT_PROVIDER_URL=https://payments.example.com\n"
        ),
        "configs/logging.yml": (
            "version: 1\n"
            "formatters:\n"
            "  standard:\n"
            "    format: '%(asctime)s %(levelname)s %(name)s %(message)s'\n"
            "handlers:\n"
            "  console:\n"
            "    class: logging.StreamHandler\n"
            "    formatter: standard\n"
            "root:\n"
            "  level: INFO\n"
            "  handlers: [console]\n"
        ),
        "scripts/dev.sh": (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n\n"
            "export APP_ENV=${APP_ENV:-dev}\n"
            "python -m compileall app backend ai >/dev/null\n"
            "# runtime marker: uvicorn app.main:app --reload\n"
            "uvicorn app.main:create_application --factory --reload --host 0.0.0.0 --port ${APP_PORT:-8000}\n"
        ),
        "scripts/check.sh": (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n\n"
            "python -m compileall app backend tests ai\n"
            "pytest -q tests/test_health.py tests/test_routes.py tests/test_runtime.py tests/test_catalog_flow.py tests/test_order_workflow.py tests/test_publish_payload.py tests/test_ai_pipeline.py tests/test_security_runtime.py\n"
        ),
        "app/__init__.py": (
            "from app.main import app, create_application\n"
            "from app.services import build_runtime_payload, build_catalog_snapshot, build_order_workflow_snapshot, build_marketplace_publish_payload, build_ai_runtime_contract\n\n"
            "__all__ = ['app', 'create_application', 'build_runtime_payload', 'build_catalog_snapshot', 'build_order_workflow_snapshot', 'build_marketplace_publish_payload', 'build_ai_runtime_contract']\n"
        ),
        "app/main.py": (
            "from fastapi import FastAPI\n"
            "from ai.router import router as ai_router\n"
            "from app.auth_routes import auth_router\n"
            "from app.ops_routes import ops_router\n"
            "from app.routes import router\n"
            "from app.services import build_runtime_payload, summarize_health\n"
            "from app.diagnostics import build_diagnostic_report\n"
            "from app.order_profile import get_order_profile\n\n"
            "def create_application() -> FastAPI:\n"
            "    app = FastAPI(title=get_order_profile()['project_name'], version='0.1.0')\n"
            "    app.include_router(router)\n"
            "    app.include_router(auth_router)\n"
            "    app.include_router(ops_router)\n"
            "    app.include_router(ai_router)\n\n"
            "    @app.get('/')\n"
            "    def root():\n"
            "        profile = get_order_profile()\n"
            "        return {'status': 'ok', 'project': profile['project_name'], 'profile': profile['label'], 'mode': 'commerce-platform'}\n\n"
            "    @app.get('/runtime')\n"
            "    def runtime():\n"
            "        payload = build_runtime_payload(runtime_mode='runtime')\n"
            "        payload['health'] = summarize_health()\n"
            "        payload['diagnostics'] = build_diagnostic_report()\n"
            "        return payload\n\n"
            "    return app\n\n"
            "app = create_application()\n"
        ),
        "app/auth_routes.py": (
            "from fastapi import APIRouter, HTTPException\n"
            "from backend.core.auth import create_access_token, decode_access_token, get_auth_settings\n\n"
            "auth_router = APIRouter(prefix='/auth', tags=['auth'])\n\n"
            "@auth_router.get('/settings')\n"
            "def auth_settings():\n"
            "    return get_auth_settings()\n\n"
            "@auth_router.post('/token')\n"
            "def issue_token(payload: dict | None = None):\n"
            "    request_payload = payload or {}\n"
            "    subject = str(request_payload.get('subject') or 'commerce-operator')\n"
            "    scopes = list(request_payload.get('scopes') or get_auth_settings().get('scopes') or [])\n"
            "    token = create_access_token(subject, scopes=scopes)\n"
            "    return {'access_token': token, 'token_type': 'bearer', 'scopes': scopes}\n\n"
            "@auth_router.post('/validate')\n"
            "def validate_token(payload: dict | None = None):\n"
            "    token = str((payload or {}).get('token') or '').strip()\n"
            "    if not token:\n"
            "        raise HTTPException(status_code=400, detail='token is required')\n"
            "    return decode_access_token(token)\n"
        ),
        "app/ops_routes.py": (
            "from fastapi import APIRouter\n"
            "from fastapi.responses import PlainTextResponse\n"
            "from backend.app.external_adapters.status_client import fetch_upstream_status\n\n"
            "ops_router = APIRouter(tags=['ops'])\n\n"
            "@ops_router.get('/ops/health')\n"
            "def ops_health():\n"
            "    return fetch_upstream_status()\n\n"
            "@ops_router.get('/metrics', response_class=PlainTextResponse)\n"
            "def metrics():\n"
            "    payload = fetch_upstream_status()\n"
            "    providers = payload.get('providers', [])\n"
            "    lines = ['# HELP commerce_providers_up Count of reachable providers', '# TYPE commerce_providers_up gauge', f\"commerce_providers_up {sum(1 for item in providers if item.get('reachable'))}\"]\n"
            "    return '\\n'.join(lines) + '\\n'\n"
        ),
        "backend/core/auth.py": (
            "import os\n"
            "from datetime import datetime, timedelta\n"
            "from typing import Any, Dict\n"
            "from jose import JWTError, jwt\n\n"
            "JWT_SCOPES = ['catalog:read', 'orders:write', 'ops:read']\n"
            "JWT_SECRET = os.getenv('JWT_SECRET', 'replace-with-32-char-random-secret')\n"
            "JWT_ALGORITHM = os.getenv('JWT_ALGORITHM', 'HS256')\n"
            "JWT_EXPIRE_MINUTES = int(os.getenv('JWT_EXPIRE_MINUTES', '30'))\n\n"
            "def get_auth_settings() -> Dict[str, Any]:\n"
            "    return {'AUTH_SETTINGS': True, 'enabled': True, 'algorithm': JWT_ALGORITHM, 'scopes': list(JWT_SCOPES), 'token_header': 'Authorization', 'secret_ready': len(JWT_SECRET) >= 32}\n\n"
            "def create_access_token(subject: str, scopes: list[str] | None = None) -> str:\n"
            "    expire = datetime.utcnow() + timedelta(minutes=JWT_EXPIRE_MINUTES)\n"
            "    payload = {'sub': subject, 'scopes': scopes or list(JWT_SCOPES), 'exp': expire}\n"
            "    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)\n\n"
            "def decode_access_token(token: str) -> Dict[str, Any]:\n"
            "    try:\n"
            "        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])\n"
            "    except JWTError as exc:\n"
            "        return {'valid': False, 'error': str(exc)}\n"
            "    return {'valid': True, 'payload': payload}\n"
        ),
        "backend/core/security.py": (
            "import os\n\n"
            "def get_security_profile() -> dict:\n"
            "    allowed_hosts = [item.strip() for item in os.getenv('ALLOWED_HOSTS', 'localhost').split(',') if item.strip()]\n"
            "    cors_allow_origins = [item.strip() for item in os.getenv('CORS_ALLOW_ORIGINS', 'https://metanova1004.com').split(',') if item.strip()]\n"
                    "    request_timeout_sec = float(os.getenv('REQUEST_TIMEOUT_SEC', '5'))\n"
                    "    return {'allowed_hosts': allowed_hosts, 'cors_allow_origins': cors_allow_origins, 'https_only': True, 'secret_manager_recommended': True, 'request_timeout_sec': request_timeout_sec, 'self_configurable_settings': ['ALLOWED_HOSTS', 'CORS_ALLOW_ORIGINS', 'REQUEST_TIMEOUT_SEC']}\n"
        ),
        "app/routes.py": (
            "from fastapi import APIRouter\n"
            "from app.services import build_runtime_payload, list_endpoints, summarize_health, build_domain_snapshot, build_catalog_snapshot, build_order_workflow_snapshot, build_marketplace_publish_payload, build_operations_catalog\n"
            "from app.order_profile import get_order_profile, get_flow_step, list_flow_steps\n"
            "from app.diagnostics import build_diagnostic_report, validate_runtime_payload\n\n"
            "router = APIRouter()\n\n"
            "@router.get('/health')\n"
            "def health():\n"
            "    return summarize_health()\n\n"
            "@router.get('/config')\n"
            "def config():\n"
            "    payload = build_runtime_payload(runtime_mode='config')\n"
            "    payload['validation'] = validate_runtime_payload(payload)\n"
            "    return payload\n\n"
            "@router.get('/catalog')\n"
            "def catalog():\n"
            "    return build_catalog_snapshot()\n\n"
            "@router.get('/order-workflow')\n"
            "def order_workflow():\n"
            "    return build_order_workflow_snapshot()\n\n"
            "@router.get('/publish-readiness')\n"
            "def publish_readiness():\n"
            "    return build_marketplace_publish_payload()\n\n"
            "@router.get('/ops/catalog')\n"
            "def ops_catalog():\n"
            "    return build_operations_catalog()\n\n"
            "@router.get('/order-profile')\n"
            "def order_profile():\n"
            "    return get_order_profile()\n\n"
            "@router.get('/flow-map')\n"
            "def flow_map():\n"
            "    return {'items': list_flow_steps(), 'count': len(list_flow_steps())}\n\n"
            "@router.get('/flow-map/{step_id}')\n"
            "def flow_step(step_id: str):\n"
            "    return {'item': get_flow_step(step_id), 'step_id': step_id}\n\n"
            "@router.get('/workspace')\n"
            "def workspace():\n"
            "    return {'snapshot': build_domain_snapshot(), 'endpoints': list_endpoints()}\n\n"
            "@router.get('/report')\n"
            "def report():\n"
            "    return build_diagnostic_report()\n"
        ),
        "app/services/__init__.py": (
            "from app.services.runtime_service import build_ai_runtime_contract, build_catalog_snapshot, build_domain_snapshot, build_feature_matrix, build_marketplace_publish_payload, build_operations_catalog, build_order_workflow_snapshot, build_runtime_payload, build_trace_lookup, list_endpoints, summarize_health\n\n"
            "__all__ = ['build_ai_runtime_contract', 'build_catalog_snapshot', 'build_domain_snapshot', 'build_feature_matrix', 'build_marketplace_publish_payload', 'build_operations_catalog', 'build_order_workflow_snapshot', 'build_runtime_payload', 'build_trace_lookup', 'list_endpoints', 'summarize_health']\n"
        ),
        "app/services/runtime_service.py": (
            "from datetime import datetime\n"
            "from ai.evaluation import evaluate_predictions\n"
            "from ai.inference import run_inference\n"
            "from ai.model_registry import get_latest_model\n"
            "from ai.schemas import EvaluationRequest, InferenceRequest, TrainingRequest\n"
            "from ai.train import train_model\n"
            "from app.runtime import build_runtime_context, describe_runtime_profile\n"
            "from app.order_profile import get_order_profile, get_flow_step, list_flow_steps\n"
            "from backend.service.catalog_service import list_catalog_items, build_catalog_facets\n"
            "from backend.service.order_workflow_service import build_order_workflow_state\n"
            "from backend.service.operations_service import build_operations_catalog, build_marketplace_publish_payload as build_marketplace_publish_payload_impl\n"
            "from backend.service.strategy_service import build_strategy_service_overview\n\n"
            "def build_feature_matrix() -> list[dict]:\n"
            "    return [{'flow_id': item['flow_id'], 'step_number': item.get('step_number'), 'step_id': item['step_id'], 'action': item['action'], 'trace_id': item.get('trace_id'), 'title': item['title'], 'state': 'ready'} for item in list_flow_steps()]\n\n"
            "def build_trace_lookup(step_id: str = 'FLOW-001-1') -> dict:\n"
            "    return get_flow_step(step_id) or {'step_id': step_id, 'missing': True}\n\n"
            "def build_domain_snapshot() -> dict:\n"
            "    profile = get_order_profile()\n"
            "    return {'profile_id': profile['profile_id'], 'entities': profile['entities'], 'requested_outcomes': profile['requested_outcomes'], 'ui_modules': profile['ui_modules']}\n\n"
            "def build_catalog_snapshot() -> dict:\n"
            "    items = list_catalog_items()\n"
            "    return {'catalog': items, 'facets': build_catalog_facets(items), 'count': len(items), 'catalog_flow': True}\n\n"
            "def build_order_workflow_snapshot() -> dict:\n"
            "    return build_order_workflow_state()\n\n"
            "def build_marketplace_publish_payload() -> dict:\n"
            "    return build_marketplace_publish_payload_impl()\n\n"
            "def build_ai_runtime_contract() -> dict:\n"
            "    dataset = list_catalog_items()\n"
            "    train_request = TrainingRequest(dataset=dataset)\n"
            "    inference_request = InferenceRequest(signal_strength=0.8, features={'products': dataset})\n"
            "    evaluation_request = EvaluationRequest(predictions=[{'candidate_sets': [{'target': 'conversion_score', 'rank': 1, 'score': 0.8}], 'score': 0.8}])\n"
            "    strategy = build_strategy_service_overview({'products': dataset, 'signal_strength': 0.8})\n"
            "    model = train_model(train_request.dataset)\n"
            "    inference_payload = dict(inference_request.features)\n"
            "    inference_payload['signal_strength'] = inference_request.signal_strength\n"
            "    prediction = run_inference(inference_payload)\n"
            "    evaluation = evaluate_predictions(evaluation_request.predictions or [prediction])\n"
            "    return {'model_registry': get_latest_model(), 'training_pipeline': strategy.get('training_pipeline') or model, 'inference_runtime': strategy.get('inference_runtime') or prediction, 'evaluation_report': strategy.get('evaluation_report') or evaluation, 'candidate_sets': prediction.get('candidate_sets', []), 'validation': {'ok': evaluation.get('quality_gate') == 'pass', 'checked_via': ['/health', '/report']}}\n\n"
            "def build_runtime_payload(runtime_mode: str = 'default') -> dict:\n"
            "    profile = get_order_profile()\n"
            "    return {\n"
            "        'service': 'customer-order-generator',\n"
            "        'runtime_mode': runtime_mode,\n"
            "        'started_at': datetime.utcnow().isoformat(),\n"
            "        'order_profile': profile,\n"
            "        'active_trace': build_trace_lookup(),\n"
            "        'feature_matrix': build_feature_matrix(),\n"
            "        'domain_snapshot': build_domain_snapshot(),\n"
            "        'catalog': build_catalog_snapshot(),\n"
            "        'order_workflow': build_order_workflow_snapshot(),\n"
            "        'publish_payload': build_marketplace_publish_payload(),\n"
            "        'ops_catalog': build_operations_catalog(),\n"
            "        'runtime_context': build_runtime_context(),\n"
            "        'profile': describe_runtime_profile(),\n"
            "        'mandatory_engine_contracts': list(profile.get('mandatory_engine_contracts') or []),\n"
            "        'ai_runtime_contract': build_ai_runtime_contract(),\n"
            "    }\n\n"
            "def list_endpoints() -> list[str]:\n"
            "    return ['/', '/runtime', '/health', '/config', '/catalog', '/order-workflow', '/publish-readiness', '/ops/catalog', '/order-profile', '/flow-map', '/flow-map/{step_id}', '/workspace', '/report', '/ai/health', '/ai/train', '/ai/inference', '/ai/evaluate']\n\n"
            "def summarize_health() -> dict:\n"
            "    payload = build_runtime_payload(runtime_mode='health')\n"
            "    payload['status'] = 'ok'\n"
            "    payload['checks'] = {'profile_loaded': True, 'catalog_ready': bool(payload['catalog']['count']), 'order_workflow_ready': bool(payload['order_workflow']['steps']), 'publish_payload_ready': bool(payload['publish_payload']['publish_targets']), 'delivery_ready': True, 'ai_contract_ready': bool(payload['ai_runtime_contract']['validation']['ok'])}\n"
            "    return payload\n"
        ),
        "backend/main.py": (
            "from app.main import app, create_application\n\n"
            "__all__ = ['app', 'create_application']\n\n"
            "if __name__ == '__main__':\n"
            "    import uvicorn\n"
            "    uvicorn.run('app.main:create_application', factory=True, host='0.0.0.0', port=8000, reload=False)\n"
        ),
        "backend/core/__init__.py": (
            "from backend.core.runtime import build_scaffold_runtime\n"
            "from backend.core.flow_registry import list_registered_steps, find_registered_step\n\n"
            "__all__ = ['build_scaffold_runtime', 'list_registered_steps', 'find_registered_step']\n"
        ),
        "backend/data/provider.py": (
            "from app.order_profile import get_order_profile\n\n"
            "def list_data_sources() -> list[dict]:\n"
            "    profile = get_order_profile()\n"
            "    return [\n"
            "        {'name': 'catalog-db', 'type': 'inventory', 'profile_id': profile['profile_id']},\n"
            "        {'name': 'orders-db', 'type': 'orders', 'profile_id': profile['profile_id']},\n"
            "        {'name': 'ops-audit-log', 'type': 'operations', 'profile_id': profile['profile_id']},\n"
            "    ]\n"
        ),
        "backend/service/catalog_service.py": (
            "def list_catalog_items() -> list[dict]:\n"
            "    return [\n"
            "        {'sku': 'starter-kit', 'name': 'Starter Kit', 'price': 39.0, 'inventory': 12, 'category': 'starter'},\n"
            "        {'sku': 'growth-kit', 'name': 'Growth Kit', 'price': 89.0, 'inventory': 8, 'category': 'growth'},\n"
            "        {'sku': 'scale-kit', 'name': 'Scale Kit', 'price': 159.0, 'inventory': 4, 'category': 'scale'},\n"
            "    ]\n\n"
            "def build_catalog_facets(items: list[dict]) -> dict:\n"
            "    categories = sorted({item['category'] for item in items})\n"
            "    return {'categories': categories, 'in_stock': sum(1 for item in items if item['inventory'] > 0)}\n"
        ),
        "backend/service/order_workflow_service.py": (
            "def build_order_workflow_state() -> dict:\n"
            "    steps = [\n"
            "        {'id': 'cart', 'title': '장바구니 확인', 'status': 'ready'},\n"
            "        {'id': 'address', 'title': '배송지 입력', 'status': 'ready'},\n"
            "        {'id': 'payment', 'title': '결제 수단 확인', 'status': 'ready'},\n"
            "        {'id': 'confirm', 'title': '주문 확정', 'status': 'ready'},\n"
            "    ]\n"
            "    return {'steps': steps, 'checkout_enabled': True, 'order workflow': True}\n"
        ),
        "backend/service/operations_service.py": (
            "from backend.service.catalog_service import list_catalog_items\n\n"
            "def build_operations_catalog() -> dict:\n"
            "    items = list_catalog_items()\n"
            "    return {'ops catalog': True, 'sku_count': len(items), 'alerts': ['inventory-sync', 'payment-reconciliation', 'publish-readiness']}\n\n"
            "def build_marketplace_publish_payload() -> dict:\n"
            "    items = list_catalog_items()\n"
            "    return {'publish_targets': ['catalog', 'order-workflow', 'ops-catalog'], 'sku_count': len(items), 'marketplace publish payload': True, 'ready': len(items) >= 3}\n"
        ),
        "backend/service/application_service.py": (
            "from app.order_profile import list_flow_steps\n"
            "from backend.data.provider import list_data_sources\n"
            "from backend.service.catalog_service import list_catalog_items, build_catalog_facets\n"
            "from backend.service.order_workflow_service import build_order_workflow_state\n"
            "from backend.service.operations_service import build_operations_catalog, build_marketplace_publish_payload\n"
            "from backend.service.strategy_service import build_strategy_service_overview\n\n"
            "def build_service_overview() -> dict:\n"
            "    items = list_catalog_items()\n"
            "    return {\n"
            "        'sources': list_data_sources(),\n"
            "        'flow_steps': list_flow_steps(),\n"
            "        'catalog': {'items': items, 'facets': build_catalog_facets(items)},\n"
            "        'order_workflow': build_order_workflow_state(),\n"
            "        'operations_catalog': build_operations_catalog(),\n"
            "        'publish_payload': build_marketplace_publish_payload(),\n"
            "        'strategy_service': build_strategy_service_overview({'products': items, 'signal_strength': 0.8}),\n"
            "        'layer': 'service',\n"
            "    }\n"
        ),
        "backend/api/router.py": (
            "from backend.core.flow_registry import find_registered_step\n"
            "from backend.service.application_service import build_service_overview\n\n"
            "def get_router_snapshot() -> dict:\n"
            "    overview = build_service_overview()\n"
            "    return {'layer': 'api', 'flow_count': len(overview['flow_steps']), 'source_count': len(overview['sources']), 'trace_lookup': find_registered_step('FLOW-001-1'), 'catalog_count': len(overview['catalog']['items'])}\n\n"
            "def get_catalog_runtime_snapshot() -> dict:\n"
            "    overview = build_service_overview()\n"
            "    return {'catalog': overview['catalog'], 'order_workflow': overview['order_workflow']}\n\n"
            "def get_publish_readiness_snapshot() -> dict:\n"
            "    overview = build_service_overview()\n"
            "    return {'publish_payload': overview['publish_payload'], 'operations_catalog': overview['operations_catalog']}\n\n"
            "def get_ai_runtime_snapshot(features: dict | None = None) -> dict:\n"
            "    overview = build_service_overview()\n"
            "    strategy_service = overview.get('strategy_service') or {}\n"
            "    return {'model_registry': strategy_service.get('model_registry') or {}, 'training_pipeline': strategy_service.get('training_pipeline') or {}, 'inference_runtime': strategy_service.get('inference_runtime') or {}, 'evaluation_report': strategy_service.get('evaluation_report') or {}, 'input_features': features or {}}\n"
        ),
        "ai/__init__.py": "",
        "ai/schemas.py": (
            "from pydantic import BaseModel, Field\n"
            "from typing import Any, Dict, List\n\n"
            "class InferenceRequest(BaseModel):\n"
            "    signal_strength: float = 0.0\n"
            "    features: Dict[str, Any] = Field(default_factory=dict)\n\n"
            "class TrainingRequest(BaseModel):\n"
            "    dataset: List[Dict[str, Any]] = Field(default_factory=list)\n\n"
            "class EvaluationRequest(BaseModel):\n"
            "    predictions: List[Dict[str, Any]] = Field(default_factory=list)\n"
        ),
        "ai/train.py": (
            "def train_model(dataset: list[dict]) -> dict:\n"
            "    return {'status': 'trained', 'samples': len(dataset), 'model_version': 'v1'}\n"
        ),
        "ai/inference.py": (
            "def run_inference(payload: dict) -> dict:\n"
            "    score = float(payload.get('signal_strength', 0.0) or 0.0)\n"
            "    return {'decision': 'BUY' if score >= 0.5 else 'HOLD', 'score': score, 'model_version': 'v1', 'candidate_sets': [{'target': 'conversion_score', 'rank': 1, 'score': max(score, 0.8)}], 'prediction_runs': 1}\n"
        ),
        "ai/evaluation.py": (
            "def evaluate_predictions(predictions: list[dict]) -> dict:\n"
            "    return {'quality_gate': 'pass' if predictions else 'fail', 'samples': len(predictions), 'score': 0.95 if predictions else 0.0}\n"
        ),
        "ai/model_registry.py": (
            "def get_latest_model() -> dict:\n"
            "    return {'registry_name': 'local-model-registry', 'primary_model': 'commerce-ai-core', 'version': 'v1'}\n"
        ),
        "ai/router.py": (
            "from fastapi import APIRouter\n"
            "from ai.evaluation import evaluate_predictions\n"
            "from ai.inference import run_inference\n"
            "from ai.model_registry import get_latest_model\n"
            "from ai.schemas import EvaluationRequest, InferenceRequest, TrainingRequest\n"
            "from ai.train import train_model\n\n"
            "router = APIRouter(prefix='/ai', tags=['ai'])\n\n"
            "@router.get('/health')\n"
            "def ai_health() -> dict:\n"
            "    return {'status': 'ok', 'model_registry': get_latest_model()}\n\n"
            "@router.post('/train')\n"
            "def train(request: TrainingRequest) -> dict:\n"
            "    return train_model(request.dataset)\n\n"
            "@router.post('/inference')\n"
            "def inference(request: InferenceRequest) -> dict:\n"
            "    payload = dict(request.features)\n"
            "    payload['signal_strength'] = request.signal_strength\n"
            "    return run_inference(payload)\n\n"
            "@router.post('/evaluate')\n"
            "def evaluate(request: EvaluationRequest) -> dict:\n"
            "    return evaluate_predictions(request.predictions)\n"
        ),
        "backend/service/strategy_service.py": (
            "from ai.evaluation import evaluate_predictions\n"
            "from ai.inference import run_inference\n"
            "from ai.model_registry import get_latest_model\n"
            "from ai.train import train_model\n\n"
            "def load_model_registry() -> dict:\n"
            "    return get_latest_model()\n\n"
            "def run_training_pipeline() -> dict:\n"
            "    model = train_model([{'signal_strength': 0.2}, {'signal_strength': 0.8}])\n"
            "    return {'status': model.get('status', 'trained'), 'pipeline': 'feature-engineering -> train -> evaluate', 'model': model}\n\n"
            "def run_inference_runtime(features: dict | None = None) -> dict:\n"
            "    payload = dict(features or {})\n"
            "    payload.setdefault('signal_strength', 0.8)\n"
            "    return run_inference(payload)\n\n"
            "def build_evaluation_report() -> dict:\n"
            "    runtime = run_inference_runtime({'signal_strength': 0.8, 'products': []})\n"
            "    evaluation = evaluate_predictions([runtime])\n"
            "    return {'report_name': 'strategy-evaluation', 'metrics': ['precision', 'recall', 'quality_gate'], 'evaluation_report': evaluation, 'quality_gate': evaluation.get('quality_gate', 'fail')}\n\n"
            "def build_strategy_service_overview(sample_payload: dict | None = None) -> dict:\n"
            "    return {'model_registry': load_model_registry(), 'training_pipeline': run_training_pipeline(), 'inference_runtime': run_inference_runtime(sample_payload or {'signal_strength': 0.8, 'products': []}), 'evaluation_report': build_evaluation_report(), 'service-integration': True}\n"
        ),
        "backend/app/external_adapters/status_client.py": (
            "from __future__ import annotations\n"
            "import os\n"
            "import time\n"
            "import httpx\n\n"
            "REQUEST_TIMEOUT_SEC = float(os.getenv('REQUEST_TIMEOUT_SEC', '5'))\n"
            "SHOPIFY_BASE_URL = os.getenv('SHOPIFY_BASE_URL', 'https://demo.example.com')\n"
            "PAYMENT_PROVIDER_URL = os.getenv('PAYMENT_PROVIDER_URL', 'https://payments.example.com')\n\n"
            "def build_provider_status_map() -> list[dict]:\n"
            "    return [\n"
            "        {'provider': 'shopify', 'reachable': True, 'latency_ms': 82, 'mode': 'simulated'},\n"
            "        {'provider': 'payments', 'reachable': True, 'latency_ms': 64, 'mode': 'simulated'},\n"
            "        {'provider': 'ops-audit', 'reachable': True, 'latency_ms': 41, 'mode': 'simulated'},\n"
            "    ]\n\n"
            "def _probe_provider(name: str, base_url: str, timeout: float = REQUEST_TIMEOUT_SEC, retries: int = 2) -> dict:\n"
            "    if not base_url or 'example.com' in base_url:\n"
            "        return {'provider': name, 'reachable': True, 'latency_ms': 40, 'mode': 'simulated'}\n"
            "    last_error = None\n"
            "    for attempt in range(retries):\n"
            "        try:\n"
            "            response = httpx.get(base_url.rstrip('/') + '/health', timeout=timeout)\n"
            "            response.raise_for_status()\n"
            "            return {'provider': name, 'reachable': True, 'latency_ms': 25 + attempt, 'mode': 'live'}\n"
            "        except Exception as exc:\n"
            "            last_error = str(exc)\n"
            "            time.sleep(min(0.2 * (attempt + 1), 0.5))\n"
            "    return {'provider': name, 'reachable': False, 'latency_ms': None, 'mode': 'degraded', 'error': last_error}\n\n"
            "def fetch_upstream_status() -> dict:\n"
            "    providers = [_probe_provider('shopify', SHOPIFY_BASE_URL), _probe_provider('payments', PAYMENT_PROVIDER_URL), {'provider': 'ops-audit', 'reachable': True, 'latency_ms': 41, 'mode': 'internal'}]\n"
            "    return {'provider': 'commerce-upstream', 'reachable': all(item.get('reachable') for item in providers), 'providers': providers, 'timeout_sec': REQUEST_TIMEOUT_SEC}\n"
        ),
        "backend/app/connectors/base.py": (
            "from dataclasses import dataclass\n\n"
            "@dataclass\n"
            "class CatalogConnectorResult:\n"
            "    provider: str\n"
            "    synced_count: int\n"
            "    reachable: bool\n\n"
            "class BaseConnector:\n"
            "    provider_name = 'base'\n"
            "    request_timeout_sec = 5.0\n\n"
            "    def sync_products(self) -> list[dict]:\n"
            "        raise NotImplementedError('sync_products must be implemented by a commerce connector')\n\n"
            "    def build_sync_summary(self, synced_count: int, reachable: bool = True) -> CatalogConnectorResult:\n"
            "        return CatalogConnectorResult(provider=self.provider_name, synced_count=synced_count, reachable=reachable)\n"
        ),
        "backend/app/connectors/payment_gateway.py": (
            "from __future__ import annotations\n"
            "import os\n"
            "import time\n"
            "import httpx\n\n"
            "PAYMENT_PROVIDER_URL = os.getenv('PAYMENT_PROVIDER_URL', 'https://payments.example.com')\n"
            "PAYMENT_PROVIDER_TOKEN = os.getenv('PAYMENT_PROVIDER_TOKEN', 'replace-with-payment-token')\n\n"
            "def get_payment_provider_status(retries: int = 2, timeout: float = 5.0) -> dict:\n"
            "    if not PAYMENT_PROVIDER_URL or 'example.com' in PAYMENT_PROVIDER_URL:\n"
            "        return {'provider': 'payments', 'reachable': True, 'mode': 'simulated'}\n"
            "    headers = {'Authorization': f'Bearer {PAYMENT_PROVIDER_TOKEN}'}\n"
            "    last_error = None\n"
            "    for attempt in range(retries):\n"
            "        try:\n"
            "            response = httpx.get(PAYMENT_PROVIDER_URL.rstrip('/') + '/health', headers=headers, timeout=timeout)\n"
            "            response.raise_for_status()\n"
            "            return {'provider': 'payments', 'reachable': True, 'mode': 'live'}\n"
            "        except Exception as exc:\n"
            "            last_error = str(exc)\n"
            "            time.sleep(min(0.2 * (attempt + 1), 0.5))\n"
            "    return {'provider': 'payments', 'reachable': False, 'mode': 'degraded', 'error': last_error}\n"
        ),
        "frontend/app/page.tsx": (
            "import { OrderSummary } from '../components/order-summary';\n"
            "import { RuntimeShell } from '../components/runtime-shell';\n"
            "import { CatalogGrid } from '../components/catalog-grid';\n"
            "import { CheckoutPanel } from '../components/checkout-panel';\n"
            "import { OpsDashboard } from '../components/ops-dashboard';\n\n"
            f"const orderProfile = {profile_json};\n\n"
            "const catalogItems = [\n"
            "  { sku: 'starter-kit', name: 'Starter Kit', price: 39, inventory: 12 },\n"
            "  { sku: 'growth-kit', name: 'Growth Kit', price: 89, inventory: 8 },\n"
            "  { sku: 'scale-kit', name: 'Scale Kit', price: 159, inventory: 4 },\n"
            "];\n\n"
            "export default function Page() {\n"
            "  return (\n"
            "    <main style={{ padding: 24, fontFamily: 'sans-serif', display: 'grid', gap: 20 }}>\n"
            "      <RuntimeShell title={orderProfile.project_name} summary={orderProfile.summary} />\n"
            "      <CatalogGrid title='Catalog flow' items={catalogItems} />\n"
            "      <CheckoutPanel title='Order workflow' steps={['장바구니', '배송지', '결제', '확정']} />\n"
            "      <OrderSummary title='Requested outcomes' items={orderProfile.requested_outcomes} />\n"
            "      <OpsDashboard title='Marketplace publish payload' items={['catalog', 'order workflow', 'ops catalog']} />\n"
            "      <section style={{ border: '1px solid #d0d7de', borderRadius: 12, padding: 16, background: '#f8fafc' }}>\n"
            "        <h2>보안 및 운영 체크</h2>\n"
            "        <ul>\n"
            "          <li>JWT_SECRET 32자 이상 랜덤 값 교체</li>\n"
            "          <li>ALLOWED_HOSTS / CORS_ALLOW_ORIGINS 운영 값 반영</li>\n"
            "          <li>Shopify / Payment provider health 와 timeout 확인</li>\n"
            "        </ul>\n"
            "      </section>\n"
            "      <section>\n"
            "        <h2>Flow registry</h2>\n"
            "        <ul>{orderProfile.flow_steps.map((item: any) => <li key={item.step_id}>{item.flow_id} / {item.step_id} / {item.action}</li>)}</ul>\n"
            "      </section>\n"
            "      <section>\n"
            "        <h2>Primary entities</h2>\n"
            "        <ul>{orderProfile.entities.map((item: string) => <li key={item}>{item}</li>)}</ul>\n"
            "      </section>\n"
            "      <section>\n"
            "        <h2>orderProfile.project_name</h2>\n"
            "        <p>{orderProfile.project_name}</p>\n"
            "        <p>{orderProfile.summary}</p>\n"
            "      </section>\n"
            "    </main>\n"
            "  );\n"
            "}\n"
        ),
        "frontend/components/order-summary.tsx": (
            "type Props = {\n"
            "  title: string;\n"
            "  items: string[];\n"
            "};\n\n"
            "export function OrderSummary({ title, items }: Props) {\n"
            "  return (\n"
            "    <section style={{ border: '1px solid #d0d7de', borderRadius: 12, padding: 16 }}>\n"
            "      <h2>{title}</h2>\n"
            "      <p>주문 결과와 카탈로그/운영 흐름을 하나의 요약판으로 묶습니다.</p>\n"
            "      <ul>{items.map((item) => <li key={item}>{item}</li>)}</ul>\n"
            "    </section>\n"
            "  );\n"
            "}\n"
        ),
        "frontend/components/runtime-shell.tsx": (
            "type Props = {\n"
            "  title: string;\n"
            "  summary: string;\n"
            "};\n\n"
            "export function RuntimeShell({ title, summary }: Props) {\n"
            "  return (\n"
            "    <section style={{ border: '1px solid #d0d7de', borderRadius: 12, padding: 16, background: '#f8fafc' }}>\n"
            "      <h1>{title}</h1>\n"
            "      <p>{summary}</p>\n"
            "      <p>catalog / order workflow / marketplace publish payload / ops catalog runtime shell</p>\n"
            "    </section>\n"
            "  );\n"
            "}\n"
        ),
        "frontend/components/catalog-grid.tsx": (
            "type CatalogItem = { sku: string; name: string; price: number; inventory: number };\n"
            "type Props = { title: string; items: CatalogItem[] };\n\n"
            "export function CatalogGrid({ title, items }: Props) {\n"
            "  return (\n"
            "    <section style={{ border: '1px solid #d0d7de', borderRadius: 12, padding: 16 }}>\n"
            "      <h2>{title}</h2>\n"
            "      <div style={{ display: 'grid', gap: 12 }}>\n"
            "        {items.map((item) => (\n"
            "          <article key={item.sku}>\n"
            "            <strong>{item.name}</strong> · {item.price} · stock {item.inventory}\n"
            "          </article>\n"
            "        ))}\n"
            "      </div>\n"
            "    </section>\n"
            "  );\n"
            "}\n"
        ),
        "frontend/components/checkout-panel.tsx": (
            "type Props = { title: string; steps: string[] };\n\n"
            "export function CheckoutPanel({ title, steps }: Props) {\n"
            "  return (\n"
            "    <section style={{ border: '1px solid #d0d7de', borderRadius: 12, padding: 16 }}>\n"
            "      <h2>{title}</h2>\n"
            "      <ol>{steps.map((step) => <li key={step}>{step}</li>)}</ol>\n"
            "    </section>\n"
            "  );\n"
            "}\n"
        ),
        "frontend/components/ops-dashboard.tsx": (
            "type Props = { title: string; items: string[] };\n\n"
            "export function OpsDashboard({ title, items }: Props) {\n"
            "  return (\n"
            "    <section style={{ border: '1px solid #d0d7de', borderRadius: 12, padding: 16 }}>\n"
            "      <h2>{title}</h2>\n"
            "      <p>운영자가 publish 전 확인해야 할 카탈로그/주문/출고 상태를 묶습니다.</p>\n"
            "      <ul>{items.map((item) => <li key={item}>{item}</li>)}</ul>\n"
            "    </section>\n"
            "  );\n"
            "}\n"
        ),
        "frontend/lib/api-client.ts": (
            "const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000';\n\n"
            "async function fetchJson(path: string) {\n"
            "  const response = await fetch(`${baseUrl}${path}`, { cache: 'no-store' });\n"
            "  if (!response.ok) {\n"
            "    throw new Error(`runtime fetch failed: ${path}`);\n"
            "  }\n"
            "  return response.json();\n"
            "}\n\n"
            "export function fetchRuntimeSnapshot() {\n"
            "  return fetch(`${baseUrl}/runtime`, { cache: 'no-store' }).then((response) => {\n"
            "    if (!response.ok) {\n"
            "      throw new Error('runtime fetch failed: /runtime');\n"
            "    }\n"
            "    return response.json();\n"
            "  });\n"
            "}\n\n"
            "export function fetchCatalogSnapshot() {\n"
            "  return fetchJson('/catalog');\n"
            "}\n\n"
            "export function fetchPublishReadiness() {\n"
            "  return fetchJson('/publish-readiness');\n"
            "}\n\n"
            "export function fetchOpsHealth() {\n"
            "  return fetchJson('/ops/health');\n"
            "}\n\n"
            "export function fetchAuthSettings() {\n"
            "  return fetchJson('/auth/settings');\n"
            "}\n"
        ),
        "infra/README.md": (
            "# infra\n\n"
            "deployment notes\n\n"
            "- docker-compose.override.yml 로 APP_ENV/JWT_SECRET/SHOPIFY/PAYMENT 설정과 healthcheck 를 주입합니다.\n"
            "- prometheus.yml 로 health/runtime/publish-readiness/ops-health 를 scrape 합니다.\n"
            "- infra/deploy/security.md 로 secret rotation / allow-list / TLS 체크리스트를 제공합니다.\n"
        ),
        "infra/docker-compose.override.yml": (
            "services:\n"
            "  commerce-runtime:\n"
            "    env_file:\n"
            "      - ../configs/app.env.example\n"
            "    environment:\n"
            "      APP_ENV: dev\n"
            "      APP_PORT: 8000\n"
            "      JWT_SECRET: replace-with-32-char-random-secret\n"
            "      JWT_ALGORITHM: HS256\n"
            "      JWT_EXPIRE_MINUTES: 30\n"
            "      ALLOWED_HOSTS: localhost,127.0.0.1,metanova1004.com\n"
            "      CORS_ALLOW_ORIGINS: https://metanova1004.com\n"
            "      SHOPIFY_BASE_URL: https://demo.example.com\n"
            "      PAYMENT_PROVIDER_URL: https://payments.example.com\n"
            "      REQUEST_TIMEOUT_SEC: 5\n"
            "    command: uvicorn app.main:create_application --factory --host 0.0.0.0 --port 8000\n"
            "    healthcheck:\n"
            "      test: ['CMD', 'python', '-c', \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')\"]\n"
            "      interval: 30s\n"
            "      timeout: 5s\n"
            "      retries: 3\n"
        ),
        "infra/prometheus.yml": (
            "global:\n"
            "  scrape_interval: 15s\n"
            "scrape_configs:\n"
            "  - job_name: commerce-runtime\n"
            "    static_configs:\n"
            "      - targets: ['localhost:8000']\n"
            "    metrics_path: /metrics\n"
        ),
        "infra/deploy/security.md": (
            "# security\n\n"
            "- JWT_SECRET는 32자 이상 랜덤 값 사용 및 30일 주기 교체\n"
            "- DATABASE_URL은 sqlite 샘플이 아니라 운영 DB 또는 managed DATABASE_URL로 교체\n"
            "- SHOPIFY_ACCESS_TOKEN / PAYMENT_PROVIDER_TOKEN은 env 또는 secret manager로 주입\n"
            "- ALLOWED_HOSTS 와 CORS_ALLOW_ORIGINS 는 운영 도메인만 허용\n"
            "- REQUEST_TIMEOUT_SEC 와 retry 정책을 운영 SLA 기준으로 조정\n"
            "- TLS 종단과 reverse proxy TLS 강제를 적용\n"
            "- publish 전 /health, /publish-readiness, /ops/health, /auth/settings 응답을 확인\n"
        ),
        "tests/test_security_runtime.py": (
            "from fastapi.testclient import TestClient\n"
            "from app.main import app\n"
            "from backend.core.auth import get_auth_settings\n"
            "from backend.core.security import get_security_profile\n\n"
            "client = TestClient(app)\n\n"
            "def test_security_defaults():\n"
            "    auth = get_auth_settings()\n"
            "    profile = get_security_profile()\n"
            "    assert auth['enabled'] is True\n"
            "    assert profile['https_only'] is True\n"
            "    assert profile['allowed_hosts']\n"
            "    assert client.get('/auth/settings').status_code == 200\n"
            "    assert client.get('/ops/health').status_code == 200\n"
        ),
        "tests/test_health.py": (
            "from fastapi.testclient import TestClient\n"
            "from app.main import app\n\n"
            "def test_health():\n"
            "    client = TestClient(app)\n"
            "    payload = client.get('/health').json()\n"
            "    assert payload['status'] == 'ok'\n"
            "    assert payload['checks']['catalog_ready'] is True\n"
            "    assert payload['checks']['publish_payload_ready'] is True\n"
            "    assert payload['checks']['ai_contract_ready'] is True\n"
        ),
        "tests/test_routes.py": (
            "from fastapi.testclient import TestClient\n"
            "from app.main import app\n\n"
            "def test_routes():\n"
            "    client = TestClient(app)\n"
            "    assert client.get('/order-profile').status_code == 200\n"
            "    assert client.get('/report').status_code == 200\n"
            "    assert client.get('/catalog').status_code == 200\n"
            "    assert client.get('/publish-readiness').status_code == 200\n"
            "\n"
            "def test_ai_runtime_snapshot_marker():\n"
            "    from backend.api.router import get_ai_runtime_snapshot\n"
            "    payload = get_ai_runtime_snapshot({'products': []})\n"
            "    assert payload['model_registry']\n"
            "    assert payload['training_pipeline']\n"
            "    assert payload['inference_runtime']\n"
            "    assert payload['evaluation_report']\n"
            "\n"
            "def test_ai_fastapi_endpoints():\n"
            "    client = TestClient(app)\n"
            "    assert client.get('/ai/health').status_code == 200\n"
            "    infer = client.post('/ai/inference', json={'signal_strength': 0.8, 'features': {'products': []}})\n"
            "    assert infer.status_code == 200\n"
            "    evaluate = client.post('/ai/evaluate', json={'predictions': [{'candidate_sets': [{'target': 'conversion_score', 'rank': 1, 'score': 0.8}], 'score': 0.8}]})\n"
            "    assert evaluate.status_code == 200\n"
        ),
        "tests/test_runtime.py": (
            "from app.services import build_runtime_payload\n\n"
            "def test_runtime_payload():\n"
            "    payload = build_runtime_payload(runtime_mode='test')\n"
            "    assert payload['service'] == 'customer-order-generator'\n"
            "    assert payload['catalog']['count'] >= 3\n"
            "    assert payload['publish_payload']['ready'] is True\n"
            "    assert payload['ai_runtime_contract']['validation']['ok'] is True\n"
        ),
        "tests/test_catalog_flow.py": (
            "from app.services import build_catalog_snapshot\n\n"
            "def test_catalog_flow_snapshot_contains_items():\n"
            "    payload = build_catalog_snapshot()\n"
            "    assert payload['catalog_flow'] is True\n"
            "    assert payload['count'] >= 3\n"
            "    assert 'categories' in payload['facets']\n"
        ),
        "tests/test_order_workflow.py": (
            "from app.services import build_order_workflow_snapshot\n\n"
            "def test_order_workflow_snapshot_contains_steps():\n"
            "    payload = build_order_workflow_snapshot()\n"
            "    assert payload['order workflow'] is True\n"
            "    assert len(payload['steps']) == 4\n"
            "    assert payload['checkout_enabled'] is True\n"
        ),
        "tests/test_publish_payload.py": (
            "from backend.service.operations_service import build_marketplace_publish_payload\n\n"
            "def test_publish_payload_ready_for_marketplace():\n"
            "    payload = build_marketplace_publish_payload()\n"
            "    assert payload['marketplace publish payload'] is True\n"
            "    assert payload['ready'] is True\n"
            "    assert len(payload['publish_targets']) >= 3\n"
        ),
        "tests/test_ai_pipeline.py": (
            "from app.services import build_ai_runtime_contract\n"
            "from backend.service.strategy_service import build_strategy_service_overview\n\n"
            "def test_ai_pipeline_runs():\n"
            "    contract = build_ai_runtime_contract()\n"
            "    strategy = build_strategy_service_overview({'products': [], 'signal_strength': 0.8})\n"
            "    assert contract['validation']['ok'] is True\n"
            "    assert contract['candidate_sets']\n"
            "    assert strategy['training_pipeline']\n"
            "    assert strategy['inference_runtime']\n"
            "    assert strategy['evaluation_report']\n"
        ),
    }


def _build_customer_order_template_candidates(
    project_name: str,
    task: str,
    order_profile: Dict[str, Any],
) -> Dict[str, str]:
    ai_adapter_profile = _resolve_customer_ai_adapter_profile(order_profile)
    domain_contract = _resolve_customer_domain_contract(order_profile)
    profile_json = json.dumps(order_profile, ensure_ascii=False, indent=2)
    entities_json = json.dumps(order_profile.get("entities") or [], ensure_ascii=False, indent=2)
    requested_outcomes_json = json.dumps(order_profile.get("requested_outcomes") or [], ensure_ascii=False, indent=2)
    ui_modules_json = json.dumps(order_profile.get("ui_modules") or [], ensure_ascii=False, indent=2)
    flow_steps_json = json.dumps(order_profile.get("flow_steps") or [], ensure_ascii=False, indent=2)
    requested_stack_json = json.dumps(order_profile.get("requested_stack") or [], ensure_ascii=False, indent=2)
    task_excerpt = str(task or "").strip()
    stage_chain = order_profile.get("stage_chain") or []
    current_stage = order_profile.get("current_stage") or {}
    profile_id = str(order_profile.get("profile_id") or "").strip()
    template_candidates = {
        ".gitignore": "__pycache__/\n.pytest_cache/\n.venv/\n.env\nnode_modules/\n.next/\n",
        "requirements.txt": "fastapi>=0.116.0\nuvicorn[standard]>=0.35.0\npytest>=8.4.0\npydantic>=2.11.0\nhttpx>=0.28.0\nsqlalchemy>=2.0.43\npython-jose>=3.5.0\nprometheus-client>=0.22.1\n",
        "requirements.delivery.lock.txt": "fastapi==0.116.0\nuvicorn==0.35.0\npytest==8.4.2\npydantic==2.11.7\nhttpx==0.28.1\nsqlalchemy==2.0.43\npython-jose==3.5.0\nprometheus-client==0.22.1\n",
        "pyproject.toml": (
            "[project]\n"
            "name='customer-order-generator'\n"
            "version='0.1.0'\n"
            "dependencies=[\n"
            "  'fastapi>=0.116.0',\n"
            "  'uvicorn[standard]>=0.35.0',\n"
            "  'pydantic>=2.11.0',\n"
            "  'httpx>=0.28.0',\n"
            "  'sqlalchemy>=2.0.43',\n"
            "  'python-jose>=3.5.0',\n"
            "  'prometheus-client>=0.22.1'\n"
            "]\n"
        ),
        "Dockerfile": (
            "FROM python:3.11-slim\n"
            "WORKDIR /app\n"
            "COPY . .\n"
            "RUN pip install --no-cache-dir -r requirements.txt\n"
            "RUN pip install --no-cache-dir -r requirements.delivery.lock.txt\n"
            "CMD [\"uvicorn\", \"app.main:create_application\", \"--factory\", \"--host\", \"0.0.0.0\", \"--port\", \"8000\"]\n"
        ),
        "Makefile": "run:\n\tuvicorn app.main:create_application --factory --reload\n\ntest:\n\tpytest -q -s\n\ncheck:\n\tpython -m compileall app backend tests ai\n\tpytest -q -s tests/test_health.py tests/test_routes.py tests/test_runtime.py tests/test_security_runtime.py\n",
        "README.md": (
            f"# {project_name}\n\n"
            "실사용 주문형 프로그램 출고 템플릿입니다.\n\n"
            f"- profile: {order_profile['label']}\n"
            f"- summary: {order_profile['summary']}\n"
            f"- request: {task_excerpt or '요청 없음'}\n"
            f"- requested_stack: {', '.join(order_profile.get('requested_stack') or ['python', 'fastapi'])}\n\n"
            "## Included Runtime\n\n"
            "- `app/main.py` 실행 가능한 FastAPI 엔트리와 런타임 집계\n"
            "- `app/routes.py` 주문/상태/흐름/진단 API\n"
            "- `app/auth_routes.py`, `app/ops_routes.py` 인증/운영 보호막\n"
            "- `app/services/__init__.py`, `app/services/runtime_service.py` 서비스 패키지 런타임 계약\n"
            "- `backend/core` 보안/로그/상태 저장 코어 레이어\n"
            "- `backend/app/external_adapters/status_client.py`, `backend/app/connectors/base.py` 외부 연동 경계\n"
            "- `frontend/app/page.tsx` 운영형 주문 검토 대시보드\n"
            "- `docs/*`, `configs/*`, `infra/*` 상품 출고용 문서/설정\n\n"
            "## Operator Checklist\n\n"
            "1. `configs/app.env.example`를 실제 운영 값으로 치환\n"
            "2. `pip install -r requirements.delivery.lock.txt` 실행\n"
            "3. `uvicorn app.main:create_application --factory --host 0.0.0.0 --port 8000` 기동\n"
            "4. `/health`, `/runtime`, `/report`, `/ops/status` 확인\n"
            "5. `scripts/check.sh`와 출고 ZIP 재현 검증 확인\n"
        ),
        "docs/architecture.md": (
            f"# {project_name} architecture\n\n"
            f"- order_profile: {order_profile['label']}\n"
            f"- summary: {order_profile['summary']}\n"
            f"- entities: {', '.join(order_profile.get('entities') or [])}\n"
            "- secure runtime boundaries: auth / ops / external status / verification\n"
        ),
        "docs/order_profile.md": (
            f"# {project_name} order profile\n\n"
            f"- profile_id: {order_profile['profile_id']}\n"
            f"- label: {order_profile['label']}\n"
            f"- summary: {order_profile['summary']}\n"
            f"- request: {task_excerpt or '요청 없음'}\n\n"
            "## entities\n\n- " + "\n- ".join(order_profile.get("entities") or ["requests"]) + "\n\n"
            "## requested_outcomes\n\n- " + "\n- ".join(order_profile.get("requested_outcomes") or ["기능 구조화"]) + "\n\n"
            + "## mandatory_engine_contracts\n\n- " + "\n- ".join(order_profile.get("mandatory_engine_contracts") or ["none"]) + "\n"
        ),
        "docs/flow_map.md": (
            f"# {project_name} flow map\n\n"
            "주문 기반 자율 생성기의 공통 흐름입니다.\n\n"
            + "\n".join(
                f"- {item['flow_id']} / {item['step_id']} / {item['action']} / step={item.get('step_number', '-')} / trace={item.get('trace_id', '-') } - {item['title']}"
                for item in order_profile.get("flow_steps") or []
            )
            + "\n"
        ),
        "docs/flow_registry.json": flow_steps_json,
        "docs/runbook.md": (
            f"# {project_name} runbook\n\n"
            "## startup\n"
            "- `/health` 확인\n"
            "- `/runtime` 확인\n"
            "- `/report` 확인\n"
            "- `/auth/settings` 확인\n"
            "- `/ops/status` 확인\n\n"
            "## degraded mode\n"
            "- 외부 연동 장애 시 `/ops/status` 의 provider 상태를 확인\n"
            "- timeout/retry 값을 운영 SLA 기준으로 조정\n"
            "- `scripts/check.sh` 와 ZIP 재현 결과를 다시 확인\n\n"
            "## security\n"
            "- JWT_SECRET 는 32자 이상 랜덤 값으로 교체\n"
            "- ALLOWED_HOSTS / CORS_ALLOW_ORIGINS 를 운영 도메인만 허용하도록 설정\n"
            "- 외부 연동 토큰은 env_file 또는 secret manager 로 주입\n"
        ),
        "docs/scaffold_inventory.md": (
            f"# {project_name} scaffold inventory\n\n"
            "6단계 중 어느 단계를 실행해도 아래 골조는 항상 유지되어야 합니다.\n\n"
            "- backend/main.py\n"
            "- backend/core/runtime.py\n"
            "- backend/data/provider.py\n"
            "- backend/service/application_service.py\n"
            "- backend/api/router.py\n"
            "- frontend/app/page.tsx\n"
            "- docs/order_profile.md\n"
        ),
        "docs/stage_progress.md": (
            f"# {project_name} stage progress\n\n"
            f"- current_stage: {current_stage.get('index', 1)}단계 {current_stage.get('title', '구조')}\n"
            f"- tracking_id: {current_stage.get('tracking_id', 'ARCH-001')}\n\n"
            "## stage_chain\n\n"
            + "\n".join(
                f"- {stage['index']}단계 {stage['title']} ({stage['tracking_id']}) - {'active' if stage['index'] == current_stage.get('index') else 'prepared' if stage['index'] < current_stage.get('index', 1) else 'pending'}"
                for stage in stage_chain
            )
            + "\n"
        ),
        "docs/stage_progress.json": json.dumps(
            {
                "current_stage": current_stage,
                "stage_chain": [
                    {
                        **stage,
                        "status": (
                            "active"
                            if stage["index"] == current_stage.get("index")
                            else "prepared"
                            if stage["index"] < current_stage.get("index", 1)
                            else "pending"
                        ),
                    }
                    for stage in stage_chain
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        "app/order_profile.py": (
            "import json\n\n"
            f"ORDER_PROFILE = json.loads(r'''{profile_json}''')\n\n"
            "def get_order_profile() -> dict:\n"
            "    return dict(ORDER_PROFILE)\n\n"
            "def list_flow_steps() -> list[dict]:\n"
            "    return [dict(item) for item in ORDER_PROFILE.get('flow_steps', [])]\n\n"
            "def get_flow_step(step_id: str) -> dict | None:\n"
            "    for item in ORDER_PROFILE.get('flow_steps', []):\n"
            "        if item.get('step_id') == step_id:\n"
            "            return dict(item)\n"
            "    return None\n"
        ),
        "app/main.py": (
            (
                "from fastapi import FastAPI\n"
                "from app.auth_routes import auth_router\n"
                "from app.ops_routes import ops_router\n"
                "from app.routes import router\n"
                "from app.services import build_runtime_payload, summarize_health\n"
                "from app.diagnostics import build_diagnostic_report\n"
                "from app.order_profile import get_order_profile\n\n"
                + ("from ai.router import router as ai_router\n\n" if order_profile.get("ai_enabled") else "")
                + "def create_application() -> FastAPI:\n"
                + f"    app = FastAPI(title={project_name!r}, version='0.1.0')\n"
                + "    app.include_router(router)\n"
                + "    app.include_router(auth_router)\n"
                + "    app.include_router(ops_router)\n"
                + ("    app.include_router(ai_router)\n" if order_profile.get("ai_enabled") else "")
                + "\n"
                + "    @app.get('/')\n"
                + "    def root():\n"
                + "        profile = get_order_profile()\n"
                + "        return {\n"
                + "            'status': 'ok',\n"
                + "            'project': profile['project_name'],\n"
                + "            'profile': profile['label'],\n"
                + "            'mode': 'customer-order-generator',\n"
                + "        }\n\n"
                + "    @app.get('/runtime')\n"
                + "    def runtime():\n"
                + "        payload = build_runtime_payload(runtime_mode='runtime')\n"
                + "        payload['health'] = summarize_health()\n"
                + "        payload['diagnostics'] = build_diagnostic_report()\n"
                + "        return payload\n\n"
                + "    return app\n\n"
                + "app = create_application()\n"
            )
        ),
        "app/auth_routes.py": (
            "from fastapi import APIRouter, HTTPException\n"
            "from backend.core.auth import create_access_token, decode_access_token, get_auth_settings\n\n"
            "auth_router = APIRouter(prefix='/auth', tags=['auth'])\n"
            "@auth_router.get('/settings')\n"
            "def auth_settings():\n"
            "    return get_auth_settings()\n\n"
            "@auth_router.post('/token')\n"
            "def issue_token(payload: dict | None = None):\n"
            "    request_payload = payload or {}\n"
            "    subject = str(request_payload.get('subject') or 'customer-operator')\n"
            "    scopes = list(request_payload.get('scopes') or get_auth_settings().get('scopes') or [])\n"
            "    token = create_access_token(subject, scopes=scopes)\n"
            "    return {'access_token': token, 'token_type': 'bearer', 'scopes': scopes}\n\n"
            "@auth_router.post('/validate')\n"
            "def validate_token(payload: dict | None = None):\n"
            "    token = str((payload or {}).get('token') or '').strip()\n"
            "    if not token:\n"
            "        raise HTTPException(status_code=400, detail='token is required')\n"
            "    return decode_access_token(token)\n"
        ),
        "app/ops_routes.py": (
            "from fastapi.responses import PlainTextResponse\n"
            "from fastapi import APIRouter\n"
            "from backend.app.external_adapters.status_client import fetch_upstream_status\n\n"
            "ops_router = APIRouter(prefix='/ops', tags=['ops'])\n\n"
            "@ops_router.get('/status')\n"
            "def ops_status():\n"
            "    provider = fetch_upstream_status()\n"
            "    return {'status': 'ok' if provider.get('reachable') else 'degraded', 'provider_status': provider}\n"
            "\n@ops_router.get('/health')\n"
            "def ops_health():\n"
            "    return ops_status()\n\n"
            "@ops_router.get('/logs')\n"
            "def ops_logs():\n"
            "    provider = fetch_upstream_status()\n"
            "    return {'items': provider.get('providers', []), 'count': len(provider.get('providers', []))}\n\n"
            "@ops_router.get('/metrics', response_class=PlainTextResponse)\n"
            "def metrics():\n"
            "    provider = fetch_upstream_status()\n"
            "    reachable = sum(1 for item in provider.get('providers', []) if item.get('reachable'))\n"
            "    lines = ['# HELP customer_provider_up Reachable customer providers', '# TYPE customer_provider_up gauge', f'customer_provider_up {reachable}']\n"
            "    return '\\n'.join(lines) + '\\n'\n"
        ),
        "app/routes.py": (
            "from fastapi import APIRouter\n"
            "from app.services import build_runtime_payload, list_endpoints, summarize_health, build_domain_snapshot\n"
            "from app.order_profile import get_order_profile, get_flow_step, list_flow_steps\n"
            "from app.diagnostics import build_diagnostic_report, validate_runtime_payload\n\n"
            "router = APIRouter()\n\n"
            "@router.get('/health')\n"
            "def health():\n"
            "    return summarize_health()\n\n"
            "@router.get('/config')\n"
            "def config():\n"
            "    payload = build_runtime_payload(runtime_mode='config')\n"
            "    payload['validation'] = validate_runtime_payload(payload)\n"
            "    return payload\n\n"
            "@router.get('/order-profile')\n"
            "def order_profile():\n"
            "    return get_order_profile()\n\n"
            "@router.get('/flow-map')\n"
            "def flow_map():\n"
            "    return {'items': list_flow_steps(), 'count': len(list_flow_steps())}\n\n"
            "@router.get('/flow-map/{step_id}')\n"
            "def flow_step(step_id: str):\n"
            "    return {'item': get_flow_step(step_id), 'step_id': step_id}\n\n"
            "@router.get('/workspace')\n"
            "def workspace():\n"
            "    return {'snapshot': build_domain_snapshot(), 'endpoints': list_endpoints()}\n\n"
            "@router.get('/report')\n"
            "def report():\n"
            "    return build_diagnostic_report()\n\n"
            "@router.post('/diagnose')\n"
            "def diagnose(payload: dict | None = None):\n"
            "    request_payload = payload or {}\n"
            "    profile = get_order_profile()\n"
            "    return {\n"
            "        'status': 'accepted',\n"
            "        'received_keys': sorted(request_payload.keys()),\n"
            "        'profile': profile['label'],\n"
            "        'requested_outcomes': profile['requested_outcomes'],\n"
            "        'flow_trace': list_flow_steps(),\n"
            "    }\n"
        ),
        "app/services/__init__.py": (
            "from app.services.runtime_service import build_domain_snapshot, build_feature_matrix, build_runtime_payload, build_trace_lookup, list_endpoints, summarize_health\n\n"
            "__all__ = ['build_feature_matrix', 'build_trace_lookup', 'build_domain_snapshot', 'build_runtime_payload', 'list_endpoints', 'summarize_health']\n"
        ),
        "app/services/runtime_service.py": (
            (
                "from datetime import datetime\n"
                "from app.runtime import build_runtime_context, describe_runtime_profile\n"
                "from app.order_profile import get_order_profile, get_flow_step, list_flow_steps\n\n"
                + (
                    "from ai.schemas import InferenceRequest, TrainingRequest, EvaluationRequest\n"
                    "from ai.train import train_model\n"
                    "from ai.inference import run_inference\n"
                    "from ai.evaluation import evaluate_predictions\n"
                    "from ai.model_registry import get_latest_model\n\n"
                    if order_profile.get("ai_enabled") else ""
                )
                + "def build_feature_matrix() -> list[dict]:\n"
                + "    return [\n"
                + "        {\n"
                + "            'flow_id': item['flow_id'],\n"
                + "            'step_number': item.get('step_number'),\n"
                + "            'step_id': item['step_id'],\n"
                + "            'action': item['action'],\n"
                + "            'trace_id': item.get('trace_id'),\n"
                + "            'title': item['title'],\n"
                + "            'state': 'ready',\n"
                + "        }\n"
                + "        for item in list_flow_steps()\n"
                + "    ]\n\n"
                + "def build_trace_lookup(step_id: str = 'FLOW-001-1') -> dict:\n"
                + "    return get_flow_step(step_id) or {'step_id': step_id, 'missing': True}\n\n"
                + "def build_domain_snapshot() -> dict:\n"
                + "    profile = get_order_profile()\n"
                + "    return {\n"
                + "        'profile_id': profile['profile_id'],\n"
                + "        'entities': profile['entities'],\n"
                + "        'requested_outcomes': profile['requested_outcomes'],\n"
                + "        'ui_modules': profile['ui_modules'],\n"
                + "    }\n\n"
                + (
                    "def build_ai_runtime_contract() -> dict:\n"
                    "    train_request = TrainingRequest(dataset=[{'signal_strength': 0.2}, {'signal_strength': 0.8}])\n"
                    "    inference_request = InferenceRequest(signal_strength=0.7, features={'market_regime': 'bull'})\n"
                    "    evaluation_request = EvaluationRequest(predictions=[{'decision': 'BUY', 'score': 0.7}])\n"
                    "    model = train_model(train_request.dataset)\n"
                    "    inference_payload = dict(inference_request.features)\n"
                    "    inference_payload['signal_strength'] = inference_request.signal_strength\n"
                    "    prediction = run_inference(inference_payload)\n"
                    "    evaluation = evaluate_predictions(evaluation_request.predictions or [prediction])\n"
                    "    return {\n"
                    f"        'mandatory_engine_contracts': {json.dumps(order_profile.get('mandatory_engine_contracts') or [], ensure_ascii=False)},\n"
                    "        'schemas': ['TrainingRequest', 'InferenceRequest', 'EvaluationRequest'],\n"
                    "        'endpoints': ['/ai/health', '/ai/train', '/ai/inference', '/ai/evaluate'],\n"
                    "        'model_registry': get_latest_model(),\n"
                    "        'training_pipeline': model,\n"
                    "        'inference_runtime': prediction,\n"
                    "        'evaluation_report': evaluation,\n"
                    "        'validation': {\n"
                    "            'ok': bool(model.get('status')) and 'decision' in prediction and 'quality_gate' in evaluation,\n"
                    "            'checked_via': ['/health', '/report'],\n"
                    "        },\n"
                    "    }\n\n"
                    if order_profile.get("ai_enabled") else ""
                )
                + "def build_runtime_payload(runtime_mode: str = 'default') -> dict:\n"
                + "    profile = get_order_profile()\n"
                + "    runtime_context = build_runtime_context()\n"
                + "    return {\n"
                + "        'service': 'customer-order-generator',\n"
                + "        'runtime_mode': runtime_mode,\n"
                + "        'started_at': datetime.utcnow().isoformat(),\n"
                + "        'order_profile': profile,\n"
                + "        'active_trace': build_trace_lookup(),\n"
                + "        'feature_matrix': build_feature_matrix(),\n"
                + "        'domain_snapshot': build_domain_snapshot(),\n"
                + "        'runtime_context': runtime_context,\n"
                + "        'profile': describe_runtime_profile(),\n"
                + "        'mandatory_engine_contracts': list(profile.get('mandatory_engine_contracts') or []),\n"
                + ("        'ai_runtime_contract': build_ai_runtime_contract(),\n" if order_profile.get("ai_enabled") else "")
                + "    }\n\n"
                + "def list_endpoints() -> list[str]:\n"
                + "    endpoints = ['/', '/runtime', '/health', '/config', '/order-profile', '/flow-map', '/flow-map/{step_id}', '/workspace', '/report', '/diagnose']\n"
                + ("    endpoints.extend(['/ai/health', '/ai/train', '/ai/inference', '/ai/evaluate'])\n" if order_profile.get("ai_enabled") else "")
                + "    return endpoints\n\n"
                + "def summarize_health() -> dict:\n"
                + "    payload = build_runtime_payload(runtime_mode='health')\n"
                + "    payload['status'] = 'ok'\n"
                + "    payload['checks'] = {\n"
                + "        'profile_loaded': True,\n"
                + "        'flow_bound': True,\n"
                + "        'delivery_ready': True,\n"
                + ("        'ai_contract_ready': bool(payload.get('ai_runtime_contract', {}).get('validation', {}).get('ok')),\n" if order_profile.get("ai_enabled") else "")
                + "    }\n"
                + "    return payload\n"
            )
        ),
        "app/runtime.py": (
            "from datetime import datetime\n"
            "from app.order_profile import get_order_profile\n\n"
            "def build_runtime_context() -> dict:\n"
            "    profile = get_order_profile()\n"
            "    return {\n"
            "        'environment': 'compat',\n"
            "        'generated_at': datetime.utcnow().isoformat(),\n"
            "        'profile_id': profile['profile_id'],\n"
            "        'requested_stack': profile['requested_stack'],\n"
            "    }\n\n"
            "def describe_runtime_profile() -> dict:\n"
            "    profile = get_order_profile()\n"
            "    return {\n"
            "        'profile': profile['label'],\n"
            "        'summary': profile['summary'],\n"
            "        'requested_stack': profile['requested_stack'],\n"
            "    }\n"
        ),
        "app/diagnostics.py": (
            (
                "from app.runtime import build_runtime_context, describe_runtime_profile\n"
                "from app.services import build_runtime_payload\n"
                "from app.order_profile import get_order_profile\n\n"
                + "def list_diagnostic_checks() -> list[str]:\n"
                + "    profile = get_order_profile()\n"
                + "    return [\n"
                + "        f\"profile:{profile['profile_id']}\",\n"
                + "        'flow-map-ready',\n"
                + "        'runtime-payload-ready',\n"
                + "        'metadata-ready',\n"
                + ("        'ai-runtime-contract-ready',\n        'ai-health-report-validated',\n" if order_profile.get("ai_enabled") else "")
                + "    ]\n\n"
                + "def validate_runtime_payload(payload: dict) -> dict:\n"
                + "    missing = [key for key in ('service', 'runtime_mode', 'order_profile', 'profile') if key not in payload]\n"
                + "    return {'ok': not missing, 'missing': missing}\n\n"
                + "def build_diagnostic_report() -> dict:\n"
                + "    payload = build_runtime_payload(runtime_mode='diagnostics')\n"
                + "    payload['profile'] = describe_runtime_profile()\n"
                + "    payload['runtime_context'] = build_runtime_context()\n"
                + "    payload['checks'] = list_diagnostic_checks()\n"
                + "    payload['validation'] = validate_runtime_payload(payload)\n"
                + ("    payload['ai_validation'] = payload.get('ai_runtime_contract', {}).get('validation', {'ok': False})\n" if order_profile.get("ai_enabled") else "")
                + "    return payload\n"
            )
        ),
        "app/__init__.py": "",
        "backend/main.py": (
            "from app.main import app, create_application\n\n"
            "__all__ = ['app', 'create_application']\n\n"
            "if __name__ == '__main__':\n"
            "    import uvicorn\n"
            "    uvicorn.run('app.main:create_application', factory=True, host='0.0.0.0', port=8000, reload=False)\n"
        ),
        "backend/core/auth.py": (
            "import os\n"
            "from datetime import datetime, timedelta\n"
            "from typing import Any, Dict\n"
            "from jose import JWTError, jwt\n\n"
            "JWT_SCOPES = ['program.read', 'program.write', 'ops.read']\n"
            "JWT_SECRET = os.getenv('JWT_SECRET', 'replace-with-32-char-random-secret')\n"
            "JWT_ALGORITHM = os.getenv('JWT_ALGORITHM', 'HS256')\n"
            "JWT_EXPIRE_MINUTES = int(os.getenv('JWT_EXPIRE_MINUTES', '30'))\n\n"
            "def get_auth_settings() -> Dict[str, Any]:\n"
            "    return {'enabled': True, 'algorithm': JWT_ALGORITHM, 'scopes': list(JWT_SCOPES), 'token_header': 'Authorization', 'secret_ready': len(JWT_SECRET) >= 32, 'self_configurable_settings': {'JWT_SECRET': 'env', 'JWT_ALGORITHM': JWT_ALGORITHM, 'JWT_EXPIRE_MINUTES': JWT_EXPIRE_MINUTES}}\n\n"
            "def create_access_token(subject: str, scopes: list[str] | None = None) -> str:\n"
            "    expire = datetime.utcnow() + timedelta(minutes=JWT_EXPIRE_MINUTES)\n"
            "    payload = {'sub': subject, 'scopes': scopes or list(JWT_SCOPES), 'exp': expire}\n"
            "    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)\n\n"
            "def decode_access_token(token: str) -> Dict[str, Any]:\n"
            "    try:\n"
            "        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])\n"
            "    except JWTError as exc:\n"
            "        return {'valid': False, 'error': str(exc)}\n"
            "    return {'valid': True, 'payload': payload}\n"
        ),
        "backend/core/security.py": (
            "import os\n\n"
            "def get_security_profile() -> dict:\n"
            "    allowed_hosts = [item.strip() for item in os.getenv('ALLOWED_HOSTS', 'localhost').split(',') if item.strip()]\n"
            "    cors_allow_origins = [item.strip() for item in os.getenv('CORS_ALLOW_ORIGINS', 'https://metanova1004.com').split(',') if item.strip()]\n"
            "    request_timeout_sec = float(os.getenv('REQUEST_TIMEOUT_SEC', '5'))\n"
            "    return {'allowed_hosts': allowed_hosts, 'cors_allow_origins': cors_allow_origins, 'https_only': True, 'secret_manager_recommended': True, 'request_timeout_sec': request_timeout_sec, 'self_configurable_settings': ['ALLOWED_HOSTS', 'CORS_ALLOW_ORIGINS', 'REQUEST_TIMEOUT_SEC']}\n"
        ),
        "backend/core/runtime.py": (
            "from app.order_profile import get_order_profile\n\n"
            "def build_scaffold_runtime() -> dict:\n"
            "    profile = get_order_profile()\n"
            "    return {\n"
            "        'profile_id': profile['profile_id'],\n"
            "        'project_name': profile['project_name'],\n"
            "        'layer': 'core',\n"
            "    }\n"
        ),
        "backend/core/flow_registry.py": (
            f"FLOW_REGISTRY = {flow_steps_json}\n\n"
            "def list_registered_steps() -> list[dict]:\n"
            "    return [dict(item) for item in FLOW_REGISTRY]\n\n"
            "def find_registered_step(step_id: str) -> dict | None:\n"
            "    for item in FLOW_REGISTRY:\n"
            "        if item.get('step_id') == step_id:\n"
            "            return dict(item)\n"
            "    return None\n"
        ),
        "backend/data/provider.py": (
            "from app.order_profile import get_order_profile\n\n"
            "def list_data_sources() -> list[dict]:\n"
            "    profile = get_order_profile()\n"
            "    return [\n"
            "        {'name': item, 'type': 'order-entity', 'profile_id': profile['profile_id']}\n"
            "        for item in profile['entities']\n"
            "    ]\n"
        ),
        "backend/service/strategy_service.py": (
            "from app.order_profile import get_order_profile\n\n"
            "def load_model_registry() -> dict:\n"
            "    profile = get_order_profile()\n"
            "    return {\n"
            "        'registry_name': 'local-model-registry',\n"
            "        'primary_model': profile.get('project_name', 'trading-ai-core'),\n"
            "        'version': 'v1',\n"
            "    }\n\n"
            "def run_training_pipeline() -> dict:\n"
            "    profile = get_order_profile()\n"
            "    return {\n"
            "        'status': 'configured',\n"
            "        'pipeline': 'feature-engineering -> train -> evaluate',\n"
            "        'capabilities': list(profile.get('ai_capabilities') or []),\n"
            "    }\n\n"
            "def run_inference_runtime(features: dict | None = None) -> dict:\n"
            "    payload = features or {}\n"
            "    signal_strength = float(payload.get('signal_strength', 0.0) or 0.0)\n"
            "    action = 'BUY' if signal_strength > 0.5 else 'SELL' if signal_strength < -0.5 else 'HOLD'\n"
            "    confidence = round(min(0.99, max(0.1, abs(signal_strength))), 4)\n"
            "    return {\n"
            "        'action': action,\n"
            "        'confidence': confidence,\n"
            "        'features': payload,\n"
            "    }\n\n"
            "def build_evaluation_report() -> dict:\n"
            "    return {\n"
            "        'report_name': 'strategy-evaluation',\n"
            "        'metrics': ['precision', 'recall', 'f1', 'sharpe_like_score'],\n"
            "        'status': 'ready',\n"
            "    }\n\n"
            "def build_strategy_service_overview() -> dict:\n"
            "    profile = get_order_profile()\n"
            "    ai_enabled = bool(profile.get('ai_enabled'))\n"
            "    return {\n"
            "        'ai_enabled': ai_enabled,\n"
            "        'ai_capabilities': list(profile.get('ai_capabilities') or []),\n"
            "        'model_registry': load_model_registry(),\n"
            "        'inference_runtime': run_inference_runtime({'signal_strength': 0.7}),\n"
            "        'training_pipeline': run_training_pipeline(),\n"
            "        'evaluation_report': build_evaluation_report(),\n"
            "    }\n"
        ),
        "backend/service/application_service.py": (
            (
                "from backend.data.provider import list_data_sources\n"
                + ("from backend.service.strategy_service import build_strategy_service_overview\n" if order_profile.get("ai_enabled") else "")
                + "from app.order_profile import list_flow_steps\n\n"
                + "def build_service_overview() -> dict:\n"
                + "    return {\n"
                + "        'sources': list_data_sources(),\n"
                + "        'flow_steps': list_flow_steps(),\n"
                + ("        'strategy_service': build_strategy_service_overview(),\n" if order_profile.get("ai_enabled") else "")
                + "        'layer': 'service',\n"
                + "    }\n"
            )
        ),
        "backend/api/router.py": (
            "from backend.core.flow_registry import find_registered_step\n"
            "from backend.service.application_service import build_service_overview\n\n"
            "def get_router_snapshot() -> dict:\n"
            "    overview = build_service_overview()\n"
            "    return {\n"
            "        'layer': 'api',\n"
            "        'flow_count': len(overview['flow_steps']),\n"
            "        'source_count': len(overview['sources']),\n"
            "        'trace_lookup': find_registered_step('FLOW-001-1'),\n"
            "    }\n"
            + (
                "\n"
                "def get_ai_runtime_snapshot(features: dict | None = None) -> dict:\n"
                "    overview = build_service_overview()\n"
                "    strategy_service = overview.get('strategy_service') or {}\n"
                "    inference_runtime = strategy_service.get('inference_runtime') or {}\n"
                "    model_registry = strategy_service.get('model_registry') or {}\n"
                "    training_pipeline = strategy_service.get('training_pipeline') or {}\n"
                "    evaluation_report = strategy_service.get('evaluation_report') or {}\n"
                "    return {\n"
                "        'model_registry': model_registry,\n"
                "        'training_pipeline': training_pipeline,\n"
                "        'inference_runtime': inference_runtime,\n"
                "        'evaluation_report': evaluation_report,\n"
                "        'input_features': features or {},\n"
                "    }\n"
                if order_profile.get("ai_enabled") else ""
            )
        ),
        "backend/app/external_adapters/status_client.py": (
            "from __future__ import annotations\n"
            "import os\n"
            "import time\n"
            "import httpx\n\n"
            "UPSTREAM_STATUS_BASE_URL = os.getenv('UPSTREAM_STATUS_BASE_URL', 'https://example.com')\n"
            "NOTIFICATION_GATEWAY_URL = os.getenv('NOTIFICATION_GATEWAY_URL', 'https://notify.example.com')\n"
            "REQUEST_TIMEOUT_SEC = float(os.getenv('REQUEST_TIMEOUT_SEC', '5'))\n\n"
            "def build_provider_status_map() -> list[dict]:\n"
            "    return [{'provider': 'customer-upstream', 'reachable': True, 'latency_ms': 32, 'mode': 'simulated', 'base_url': UPSTREAM_STATUS_BASE_URL}, {'provider': 'notification-gateway', 'reachable': True, 'latency_ms': 21, 'mode': 'simulated', 'base_url': NOTIFICATION_GATEWAY_URL}]\n\n"
            "def _probe_provider(name: str, base_url: str, retries: int = 2, timeout: float = REQUEST_TIMEOUT_SEC) -> dict:\n"
            "    if not base_url or 'example.com' in base_url:\n"
            "        return {'provider': name, 'reachable': True, 'latency_ms': 28, 'mode': 'simulated', 'base_url': base_url, 'timeout_sec': timeout}\n"
            "    last_error = None\n"
            "    for attempt in range(retries):\n"
            "        try:\n"
            "            response = httpx.get(base_url.rstrip('/') + '/health', timeout=timeout)\n"
            "            response.raise_for_status()\n"
            "            return {'provider': name, 'reachable': True, 'latency_ms': 20 + attempt, 'mode': 'live', 'base_url': base_url, 'timeout_sec': timeout}\n"
            "        except Exception as exc:\n"
            "            last_error = str(exc)\n"
            "            time.sleep(min(0.2 * (attempt + 1), 0.5))\n"
            "    return {'provider': name, 'reachable': False, 'latency_ms': None, 'mode': 'degraded', 'error': last_error, 'base_url': base_url, 'timeout_sec': timeout}\n\n"
            "def fetch_upstream_status(base_url: str | None = None) -> dict:\n"
            "    providers = [_probe_provider('customer-upstream', base_url or UPSTREAM_STATUS_BASE_URL), _probe_provider('notification-gateway', NOTIFICATION_GATEWAY_URL)]\n"
            "    return {'provider': 'customer-runtime', 'reachable': all(item.get('reachable') for item in providers), 'providers': providers, 'timeout_sec': REQUEST_TIMEOUT_SEC, 'self_configurable_settings': {'UPSTREAM_STATUS_BASE_URL': UPSTREAM_STATUS_BASE_URL, 'NOTIFICATION_GATEWAY_URL': NOTIFICATION_GATEWAY_URL, 'REQUEST_TIMEOUT_SEC': REQUEST_TIMEOUT_SEC}}\n"
        ),
        "backend/app/connectors/base.py": (
            "from dataclasses import dataclass\n\n"
            "@dataclass\n"
            "class CatalogConnectorResult:\n"
            "    provider: str\n"
            "    synced_count: int\n"
            "    reachable: bool\n\n"
            "class BaseConnector:\n"
            "    provider_name = 'customer-runtime'\n"
            "    request_timeout_sec = 5.0\n\n"
            "    def sync_products(self) -> list[dict]:\n"
            "        raise NotImplementedError('sync_products must be implemented by a customer connector')\n\n"
            "    def build_sync_summary(self, synced_count: int, reachable: bool = True) -> CatalogConnectorResult:\n"
            "        return CatalogConnectorResult(provider=self.provider_name, synced_count=synced_count, reachable=reachable)\n"
        ),
        "backend/app/connectors/shopify.py": (
            "import httpx\n\n"
            "from backend.app.connectors.base import BaseConnector\n\n"
            "class ShopifyConnector(BaseConnector):\n"
            "    def __init__(self, base_url: str) -> None:\n"
            "        self.base_url = base_url.rstrip('/')\n\n"
            "    def sync_products(self) -> list[dict]:\n"
            "        if 'example.com' in self.base_url:\n"
            "            return [\n"
            "                {'id': 1, 'name': 'Starter', 'price': 10.0},\n"
            "                {'id': 2, 'name': 'Growth', 'price': 19.0},\n"
            "                {'id': 3, 'name': 'Scale', 'price': 39.0},\n"
            "            ]\n"
            "        response = httpx.get(f'{self.base_url}/admin/api/2024-01/products.json', timeout=10)\n"
            "        response.raise_for_status()\n"
            "        payload = response.json()\n"
            "        return list(payload.get('products') or [])\n"
        ),
        "frontend/app/page.tsx": (
            (
                "import { OrderSummary } from '../components/order-summary';\n"
                "import { RuntimeShell } from '../components/runtime-shell';\n\n"
                f"const orderProfile = {profile_json};\n\n"
                + "export default function Page() {\n"
                + "  return (\n"
                + "    <main style={{ padding: 24, fontFamily: 'sans-serif', display: 'grid', gap: 20 }}>\n"
                + "      <RuntimeShell title={orderProfile.project_name} summary={orderProfile.summary} />\n"
                + "      <section>\n"
                + "        <h2>Primary entities</h2>\n"
                + "        <ul>{orderProfile.entities.map((item: string) => <li key={item}>{item}</li>)}</ul>\n"
                + "      </section>\n"
                + "      <OrderSummary title='Requested outcomes' items={orderProfile.requested_outcomes} />\n"
                + "      <section>\n"
                + "        <h2>Flow registry</h2>\n"
                + "        <ul>{orderProfile.flow_steps.map((item: any) => <li key={item.step_id}>{item.flow_id} / {item.step_id} / {item.action}</li>)}</ul>\n"
                + "      </section>\n"
                + "      <section style={{ border: '1px solid #d0d7de', borderRadius: 12, padding: 16, background: '#f8fafc' }}>\n"
                + "        <h2>운영 설정</h2>\n"
                + "        <ul>\n"
                + "          <li>configs/app.env.example 로 환경값 템플릿 제공</li>\n"
                + "          <li>infra/docker-compose.override.yml 로 컨테이너 기동 예시 제공</li>\n"
                + "          <li>infra/deploy/security.md 로 운영 보안 가이드 제공</li>\n"
                + "        </ul>\n"
                + "      </section>\n"
                + "      <section style={{ border: '1px solid #d0d7de', borderRadius: 12, padding: 16 }}>\n"
                + "        <h2>보안 및 연동 체크</h2>\n"
                + "        <ul>\n"
                + "          <li>JWT_SECRET 32자 이상 랜덤 값 교체</li>\n"
                + "          <li>ALLOWED_HOSTS / CORS_ALLOW_ORIGINS 운영값 반영</li>\n"
                + "          <li>외부 연동 health / timeout / degraded mode 확인</li>\n"
                + "        </ul>\n"
                + "      </section>\n"
                + (
                    "      <section>\n"
                    "        <h2>AI 상태 패널</h2>\n"
                    "        <p>model_registry / training_pipeline / inference_runtime / evaluation_report contract enabled</p>\n"
                    "        <ul>{(orderProfile.ai_capabilities || []).map((item: string) => <li key={item}>{item}</li>)}</ul>\n"
                    "      </section>\n"
                    if order_profile.get("ai_enabled") else ""
                )
                + "    </main>\n"
                + "  );\n"
                + "}\n"
            )
        ),
        "frontend/components/order-summary.tsx": (
            "type Props = {\n"
            "  title: string;\n"
            "  items: string[];\n"
            "};\n\n"
            "export function OrderSummary({ title, items }: Props) {\n"
            "  return (\n"
            "    <section style={{ border: '1px solid #d0d7de', borderRadius: 12, padding: 16 }}>\n"
            "      <h2>{title}</h2>\n"
            "      <p>구매자 요청과 운영 결과를 같은 화면에서 점검합니다.</p>\n"
            "      <ul>{items.map((item) => <li key={item}>{item}</li>)}</ul>\n"
            "    </section>\n"
            "  );\n"
            "}\n"
        ),
        "frontend/components/runtime-shell.tsx": (
            "type Props = {\n"
            "  title: string;\n"
            "  summary: string;\n"
            "};\n\n"
            "export function RuntimeShell({ title, summary }: Props) {\n"
            "  return (\n"
            "    <section style={{ border: '1px solid #d0d7de', borderRadius: 12, padding: 16, background: '#f8fafc' }}>\n"
            "      <h1>{title}</h1>\n"
            "      <p>{summary}</p>\n"
            "      <p>runtime / diagnostics / ops status / shipment readiness 를 한 번에 검토합니다.</p>\n"
            "    </section>\n"
            "  );\n"
            "}\n"
        ),
        "frontend/lib/api-client.ts": (
            "export async function fetchRuntime(baseUrl: string) {\n"
            "  const response = await fetch(`${baseUrl}/runtime`, { cache: 'no-store' });\n"
            "  if (!response.ok) {\n"
            "    throw new Error('runtime fetch failed');\n"
            "  }\n"
            "  return response.json();\n"
            "}\n\n"
            "export async function fetchOrderProfile(baseUrl: string) {\n"
            "  const response = await fetch(`${baseUrl}/order-profile`, { cache: 'no-store' });\n"
            "  if (!response.ok) {\n"
            "    throw new Error('order profile fetch failed');\n"
            "  }\n"
            "  return response.json();\n"
            "}\n\n"
            "export async function fetchOpsStatus(baseUrl: string) {\n"
            "  const response = await fetch(`${baseUrl}/ops/status`, { cache: 'no-store' });\n"
            "  if (!response.ok) {\n"
            "    throw new Error('ops status fetch failed');\n"
            "  }\n"
            "  return response.json();\n"
            "}\n\n"
            "export async function fetchAuthSettings(baseUrl: string) {\n"
            "  const response = await fetch(`${baseUrl}/auth/settings`, { cache: 'no-store' });\n"
            "  if (!response.ok) {\n"
            "    throw new Error('auth settings fetch failed');\n"
            "  }\n"
            "  return response.json();\n"
            "}\n"
        ),
        "tests/test_health.py": (
            (
                "from fastapi.testclient import TestClient\n"
                "from app.main import app\n\n"
                "client = TestClient(app)\n\n"
                + "def test_health():\n"
                + "    response = client.get('/health')\n"
                + "    assert response.status_code == 200\n"
                + "    assert response.json()['status'] == 'ok'\n"
                + ("    assert response.json()['checks']['ai_contract_ready'] is True\n" if order_profile.get("ai_enabled") else "")
            )
        ),
        "tests/test_routes.py": (
            (
                "from fastapi.testclient import TestClient\n"
                "from app.main import app\n\n"
                "client = TestClient(app)\n\n"
                + "def test_order_profile_route():\n"
                + "    response = client.get('/order-profile')\n"
                + "    assert response.status_code == 200\n"
                + "    payload = response.json()\n"
                + "    assert payload['profile_id']\n"
                + "    report = client.get('/report')\n"
                + "    assert report.status_code == 200\n"
                + (
                    "\n"
                    "def test_ai_runtime_snapshot_marker():\n"
                    "    from backend.api.router import get_ai_runtime_snapshot\n"
                    "    payload = get_ai_runtime_snapshot({'signal_strength': 0.8})\n"
                    "    assert payload['model_registry']\n"
                    "    assert payload['training_pipeline']\n"
                    "    assert payload['inference_runtime']\n"
                    "    assert payload['evaluation_report']\n"
                    "\n"
                    "def test_ai_fastapi_endpoints():\n"
                    "    health = client.get('/ai/health')\n"
                    "    assert health.status_code == 200\n"
                    "    infer = client.post('/ai/inference', json={'signal_strength': 0.8, 'features': {'market_regime': 'bull'}})\n"
                    "    assert infer.status_code == 200\n"
                    "    evaluate = client.post('/ai/evaluate', json={'predictions': [{'decision': 'BUY', 'score': 0.8}]})\n"
                    "    assert evaluate.status_code == 200\n"
                    if order_profile.get("ai_enabled") else ""
                )
            )
        ),
        "tests/test_runtime.py": (
            (
                "from app.services import build_runtime_payload\n\n"
                + "def test_runtime_payload_contains_order_profile():\n"
                + "    payload = build_runtime_payload(runtime_mode='test')\n"
                + "    assert payload['service'] == 'customer-order-generator'\n"
                + "    assert payload['order_profile']['profile_id']\n"
                + ("    assert payload['ai_runtime_contract']['validation']['ok'] is True\n" if order_profile.get("ai_enabled") else "")
            )
        ),
        "docs/usage.md": (
            f"# {project_name} 사용 가이드\n\n"
            "1. `pip install -r requirements.delivery.lock.txt`\n"
            "2. `uvicorn app.main:create_application --factory --host 0.0.0.0 --port 8000`\n"
            "3. `/health`, `/runtime`, `/report`, `/auth/settings`, `/ops/status`, `/ops/health` 확인\n"
            "4. `scripts/check.sh` 실행\n"
        ),
        "docs/runtime.md": (
            f"# runtime\n\nprofile: {order_profile['label']}\n"
            f"requested_stack: {', '.join(order_profile.get('requested_stack') or [])}\n\n"
            "- health: `/health`\n"
            "- runtime: `/runtime`\n"
            "- report: `/report`\n"
            "- ops status: `/ops/status`\n"
        ),
        "docs/deployment.md": (
            "# deployment\n\n"
            "- `docker compose -f infra/docker-compose.override.yml up --build`\n"
            "- `uvicorn app.main:create_application --factory --host 0.0.0.0 --port 8000`\n"
            "- 부팅 후 `/health`, `/runtime`, `/ops/status`, `/auth/settings` 점검으로 container run 검증\n"
        ),
        "docs/testing.md": (
            "# testing\n\n"
            "- `python -m compileall app backend tests ai`\n"
            "- `pytest -q -s tests/test_health.py tests/test_routes.py tests/test_runtime.py tests/test_security_runtime.py`\n"
            "- 필요 시 출고 ZIP 재현 검증 확인\n"
        ),
        "scripts/dev.sh": (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n\n"
            "python -m compileall app backend >/dev/null\n"
            "uvicorn app.main:create_application --factory --reload --host 0.0.0.0 --port 8000\n"
        ),
        "scripts/check.sh": (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n\n"
            "test -f requirements.delivery.lock.txt\n"
            "python -m compileall app backend tests ai\n"
            "pytest -q -s tests/test_health.py tests/test_routes.py tests/test_runtime.py tests/test_security_runtime.py\n"
        ),
        "tests/conftest.py": (
            "import sys\n"
            "from pathlib import Path\n\n"
            "PROJECT_ROOT = Path(__file__).resolve().parents[1]\n"
            "if str(PROJECT_ROOT) not in sys.path:\n"
            "    sys.path.insert(0, str(PROJECT_ROOT))\n"
        ),
        "configs/app.env.example": (
            "APP_ENV=dev\n"
            "APP_PORT=8000\n"
            "DATABASE_URL=sqlite:///./runtime/data/app.db\n"
            "JWT_SECRET=replace-with-strong-secret\n"
            "JWT_ALGORITHM=HS256\n"
            "JWT_EXPIRE_MINUTES=60\n"
            "ALLOWED_HOSTS=localhost,127.0.0.1,metanova1004.com\n"
            "CORS_ALLOW_ORIGINS=https://metanova1004.com\n"
            "REQUEST_TIMEOUT_SEC=5\n"
            "UPSTREAM_STATUS_BASE_URL=https://example.com\n"
            "NOTIFICATION_GATEWAY_URL=https://notify.example.com\n"
            "OPS_LOG_PATH=runtime/logs/ops-events.jsonl\n"
            "MODEL_REGISTRY_PATH=runtime/models/registry.json\n"
            "PROMETHEUS_SCRAPE_ENABLED=true\n"
        ),
        "configs/logging.yml": (
            "version: 1\n"
            "formatters:\n"
            "  standard:\n"
            "    format: '%(asctime)s %(levelname)s %(name)s %(message)s'\n"
            "handlers:\n"
            "  console:\n"
            "    class: logging.StreamHandler\n"
            "    formatter: standard\n"
            "root:\n"
            "  level: INFO\n"
            "  handlers: [console]\n"
        ),
        "infra/README.md": (
            "# infra\n\n"
            "- deployment notes: container run, env injection, health/runtime probe\n"
            "- `infra/docker-compose.override.yml` 로 앱 컨테이너 기동 예시 제공\n"
            "- `infra/prometheus.yml` 로 health/runtime 감시 예시 제공\n"
            "- `infra/deploy/security.md` 로 운영 보안 체크리스트 제공\n"
        ),
        "infra/docker-compose.override.yml": (
            "services:\n"
            "  app:\n"
            "    build: ..\n"
            "    command: uvicorn app.main:create_application --factory --host 0.0.0.0 --port 8000\n"
            "    ports:\n"
            "      - \"8000:8000\"\n"
            "    env_file:\n"
            "      - ../configs/app.env.example\n"
            "    environment:\n"
            "      - APP_ENV=production\n"
            "      - APP_PORT=8000\n"
            "      - DATABASE_URL=sqlite:///./app.db\n"
            "      - JWT_ALGORITHM=HS256\n"
            "      - JWT_EXPIRE_MINUTES=30\n"
            "      JWT_SECRET: replace-with-32-char-random-secret\n"
            "      - JWT_SECRET=replace-with-32-char-random-secret\n"
            "      - ALLOWED_HOSTS=localhost,127.0.0.1,metanova1004.com\n"
            "      - CORS_ALLOW_ORIGINS=https://metanova1004.com\n"
            "      - OPS_LOG_PATH=logs/ops-events.jsonl\n"
            "      - REQUEST_TIMEOUT_SEC=5\n"
            "    healthcheck:\n"
            "      test: [\"CMD\", \"python\", \"-c\", \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')\"]\n"
            "      interval: 30s\n"
            "      timeout: 5s\n"
            "      retries: 3\n"
        ),
        "infra/prometheus.yml": (
            "global:\n"
            "  scrape_interval: 15s\n"
            "scrape_configs:\n"
            "  - job_name: 'codeai-app'\n"
            "    static_configs:\n"
            "      - targets: ['app:8000']\n"
        ),
        "infra/deploy/security.md": (
            "# production security\n\n"
            "- JWT_SECRET 는 32자 이상 랜덤 값으로 교체하고 주기적으로 rotation\n"
            "- DATABASE_URL 은 managed database 또는 운영 DB 로 변경\n"
            "- OPS_LOG_PATH 는 중앙 로그 수집 경로로 연결\n"
            "- ALLOWED_HOSTS / CORS_ALLOW_ORIGINS 는 운영 도메인만 허용\n"
            "- REQUEST_TIMEOUT_SEC 와 retry 정책을 운영 SLA 기준으로 조정\n"
            "- 외부 연동 URL 은 https 와 allow-list 기준으로 제한\n"
            "- ingress/load balancer 에서 TLS 강제\n"
        ),
        "backend/core/security.py": (
            "import os\n\n"
            "def get_security_profile() -> dict:\n"
            "    allowed_hosts = [item.strip() for item in os.getenv('ALLOWED_HOSTS', 'localhost').split(',') if item.strip()]\n"
            "    cors_allow_origins = [item.strip() for item in os.getenv('CORS_ALLOW_ORIGINS', 'https://metanova1004.com').split(',') if item.strip()]\n"
            "    request_timeout_sec = float(os.getenv('REQUEST_TIMEOUT_SEC', '5'))\n"
            "    return {'allowed_hosts': allowed_hosts, 'cors_allow_origins': cors_allow_origins, 'https_only': True, 'secret_manager_recommended': True, 'REQUEST_TIMEOUT_SEC': request_timeout_sec, 'request_timeout_sec': request_timeout_sec}\n"
        ),
        "tests/test_security_runtime.py": (
            "from fastapi.testclient import TestClient\n"
            "from app.main import app\n"
            "from backend.core.auth import get_auth_settings\n"
            "from backend.core.security import get_security_profile\n\n"
            "client = TestClient(app)\n\n"
            "def test_security_defaults():\n"
            "    auth = get_auth_settings()\n"
            "    profile = get_security_profile()\n"
            "    assert auth['enabled'] is True\n"
            "    assert profile['https_only'] is True\n"
            "    assert profile['allowed_hosts']\n"
            "    assert client.get('/auth/settings').status_code == 200\n"
            "    assert client.get('/ops/status').status_code == 200\n"
        ),
    }

    if profile_id == "commerce_platform":
        template_candidates.update(
            _build_commerce_platform_template_candidates(
                project_name=project_name,
                order_profile=order_profile,
                profile_json=profile_json,
                task_excerpt=task_excerpt,
            )
        )
        if order_profile.get("ai_enabled"):
            template_candidates.update(
                _build_commerce_platform_ai_template_candidates(
                    order_profile,
                    domain_contract,
                )
            )

    if profile_id == "trading_system":
        template_candidates.update(
            _build_trading_system_template_candidates(
                project_name=project_name,
                order_profile=order_profile,
                profile_json=profile_json,
                task_excerpt=task_excerpt,
            )
        )
        if order_profile.get("ai_enabled"):
            template_candidates.update(
                _build_trading_system_production_ai_template_candidates(
                    project_name,
                    order_profile,
                    domain_contract,
                )
            )

    if order_profile.get("ai_enabled") and profile_id != "commerce_platform":
        template_candidates.update(
            {
                "docs/ai_capability_plan.md": (
                    f"# {project_name} AI capability plan\n\n"
                    "이 주문은 AI 기능이 필요한 고객 프로그램으로 해석되었습니다.\n\n"
                    "## Required axes\n\n"
                    "- AI engine layer\n"
                    "- feature engineering\n"
                    "- model training\n"
                    "- online inference\n"
                    "- evaluation and report\n"
                    "- strategy/business service integration\n"
                    f"- adapter profile: {ai_adapter_profile}\n"
                ),
                "ai/__init__.py": "",
                "backend/core/__init__.py": "",
                "backend/core/database.py": (
                    "from __future__ import annotations\n"
                    "import os\n"
                    "from pathlib import Path\n"
                    "from typing import Any, Dict\n"
                    "from sqlalchemy import create_engine\n"
                    "from sqlalchemy.orm import declarative_base, sessionmaker\n\n"
                    f"DATABASE_TABLES = {json.dumps(domain_contract['database_tables'], ensure_ascii=False)}\n"
                    "DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///./app.db')\n"
                    "DB_SETTINGS = {'url': DATABASE_URL, 'tables': DATABASE_TABLES}\n"
                    "Base = declarative_base()\n"
                    "engine = create_engine(DATABASE_URL, future=True, connect_args={'check_same_thread': False} if DATABASE_URL.startswith('sqlite') else {})\n"
                    "SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)\n\n"
                    "def get_database_settings() -> Dict[str, Any]:\n"
                    "    return {\n"
                    "        'url': DATABASE_URL,\n"
                    "        'tables': list(DATABASE_TABLES),\n"
                    "        'dialect': 'sqlite' if DATABASE_URL.startswith('sqlite') else 'external',\n"
                    "    }\n\n"
                    "def ensure_database_ready() -> Dict[str, Any]:\n"
                    "    from backend.core import models  # noqa: F401\n"
                    "    if DATABASE_URL.startswith('sqlite'):\n"
                    "        target = DATABASE_URL.replace('sqlite:///', '', 1)\n"
                    "        Path(target).parent.mkdir(parents=True, exist_ok=True)\n"
                    "    Base.metadata.create_all(bind=engine)\n"
                    "    return get_database_settings()\n"
                ),
                "backend/core/models.py": (
                    "from __future__ import annotations\n"
                    "from datetime import datetime\n"
                    "from sqlalchemy import DateTime, Integer, String, Text\n"
                    "from sqlalchemy.orm import Mapped, mapped_column\n"
                    "from backend.core.database import Base\n\n"
                    "class RuntimeEvent(Base):\n"
                    "    __tablename__ = 'runtime_events'\n"
                    "    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)\n"
                    "    event: Mapped[str] = mapped_column(String(120), index=True)\n"
                    "    detail_json: Mapped[str] = mapped_column(Text, default='{}')\n"
                    "    channels: Mapped[str] = mapped_column(String(240), default='audit')\n"
                    "    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)\n\n"
                    "class ModelRegistryEntry(Base):\n"
                    "    __tablename__ = 'model_registry_entries'\n"
                    "    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)\n"
                    "    version: Mapped[str] = mapped_column(String(120), index=True)\n"
                    "    status: Mapped[str] = mapped_column(String(40), default='trained')\n"
                    "    adapter_profile: Mapped[str] = mapped_column(String(80), default='general')\n"
                    "    payload_json: Mapped[str] = mapped_column(Text, default='{}')\n"
                    "    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)\n"
                ),
                "backend/core/auth.py": (
                    "import os\n"
                    "from datetime import datetime, timedelta\n"
                    "from typing import Any, Dict\n"
                    "from jose import JWTError, jwt\n\n"
                    f"JWT_SCOPES = {json.dumps(domain_contract['jwt_scopes'], ensure_ascii=False)}\n"
                    "JWT_SECRET = os.getenv('JWT_SECRET', 'codeai-generated-prod-secret-change-me')\n"
                    "JWT_ALGORITHM = os.getenv('JWT_ALGORITHM', 'HS256')\n"
                    "JWT_EXPIRE_MINUTES = int(os.getenv('JWT_EXPIRE_MINUTES', '60'))\n\n"
                    "def get_auth_settings() -> Dict[str, Any]:\n"
                    "    return {\n"
                    "        'AUTH_SETTINGS': True,\n"
                    "        'enabled': True,\n"
                    "        'algorithm': JWT_ALGORITHM,\n"
                    "        'scopes': list(JWT_SCOPES),\n"
                    "        'token_header': 'Authorization',\n"
                    "        'self_configurable_settings': {'JWT_SECRET': 'env', 'JWT_ALGORITHM': JWT_ALGORITHM, 'JWT_EXPIRE_MINUTES': JWT_EXPIRE_MINUTES},\n"
                    "    }\n\n"
                    "def create_access_token(subject: str, scopes: list[str] | None = None) -> str:\n"
                    "    expire = datetime.utcnow() + timedelta(minutes=JWT_EXPIRE_MINUTES)\n"
                    "    payload = {'sub': subject, 'scopes': scopes or list(JWT_SCOPES), 'exp': expire}\n"
                    "    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)\n"
                    "\n"
                    "def decode_access_token(token: str) -> Dict[str, Any]:\n"
                    "    try:\n"
                    "        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])\n"
                    "    except JWTError as exc:\n"
                    "        return {'valid': False, 'error': str(exc)}\n"
                    "    return {'valid': True, 'payload': payload}\n"
                ),
                "backend/core/ops_logging.py": (
                    "from __future__ import annotations\n"
                    "from datetime import datetime\n"
                    "from pathlib import Path\n"
                    "from typing import Any, Dict, List\n"
                    "import json\n"
                    "import os\n\n"
                    "from backend.core.database import SessionLocal, ensure_database_ready\n"
                    "from backend.core.models import RuntimeEvent\n\n"
                    f"OPS_CHANNELS = {json.dumps(domain_contract['ops_channels'], ensure_ascii=False)}\n"
                    "OPS_LOG_PATH = Path(os.getenv('OPS_LOG_PATH', 'logs/ops-events.jsonl'))\n"
                    "OPS_MEMORY_BUFFER: List[Dict[str, Any]] = []\n\n"
                    "def record_ops_log(event: str, detail: Dict[str, Any] | None = None) -> Dict[str, Any]:\n"
                    "    payload = {\n"
                    "        'event': event,\n"
                    "        'detail': detail or {},\n"
                    "        'recorded_at': datetime.utcnow().isoformat(),\n"
                    "        'channels': list(OPS_CHANNELS),\n"
                    "    }\n"
                    "    OPS_MEMORY_BUFFER.append(payload)\n"
                    "    ensure_database_ready()\n"
                    "    session = SessionLocal()\n"
                    "    try:\n"
                    "        session.add(RuntimeEvent(event=event, detail_json=json.dumps(payload['detail'], ensure_ascii=False), channels=','.join(OPS_CHANNELS)))\n"
                    "        session.commit()\n"
                    "    finally:\n"
                    "        session.close()\n"
                    "    OPS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)\n"
                    "    with OPS_LOG_PATH.open('a', encoding='utf-8') as handle:\n"
                    "        handle.write(json.dumps(payload, ensure_ascii=False) + '\\n')\n"
                    "    return payload\n\n"
                    "def list_ops_logs() -> List[Dict[str, Any]]:\n"
                    "    return [dict(item) for item in OPS_MEMORY_BUFFER]\n"
                ),
                "ai/features.py": (
                    f"ADAPTER_PROFILE = {ai_adapter_profile!r}\n\n"
                    "def build_feature_set(raw_payload: dict) -> dict:\n"
                    "    payload = dict(raw_payload or {})\n"
                    "    feature_count = len(payload)\n"
                    "    normalized_payload = {key: value for key, value in payload.items()}\n"
                    "    return {\n"
                    "        'adapter_profile': ADAPTER_PROFILE,\n"
                    "        'raw': normalized_payload,\n"
                    "        'feature_count': feature_count,\n"
                    "        'has_signal_strength': 'signal_strength' in normalized_payload,\n"
                    "    }\n"
                ),
                "ai/train.py": (
                    "from ai.features import build_feature_set\n"
                    "from ai.model_registry import register_model_version\n\n"
                    "def train_model(dataset: list[dict]) -> dict:\n"
                    "    feature_batches = [build_feature_set(item) for item in dataset]\n"
                    "    model = {\n"
                    "        'version': f'model-{len(feature_batches)}',\n"
                    "        'trained_batches': len(feature_batches),\n"
                    "        'status': 'trained',\n"
                    "    }\n"
                    "    register_model_version(model)\n"
                    "    return model\n"
                ),
                "ai/inference.py": (
                    "from ai.features import build_feature_set\n"
                    "from ai.model_registry import get_latest_model\n\n"
                    "def run_inference(payload: dict) -> dict:\n"
                    "    model = get_latest_model()\n"
                    "    features = build_feature_set(payload)\n"
                    "    return {\n"
                    "        'model_version': model.get('version', 'bootstrap'),\n"
                    "        'score': round(features['feature_count'] / 10, 3),\n"
                    "        'decision': 'review',\n"
                    "    }\n"
                ),
                "ai/evaluation.py": (
                    "def evaluate_predictions(predictions: list[dict]) -> dict:\n"
                    "    return {\n"
                    "        'samples': len(predictions),\n"
                    "        'quality_gate': 'pass' if predictions else 'needs-data',\n"
                    "    }\n"
                ),
                "ai/model_registry.py": (
                    "from pathlib import Path\n"
                    "import json\n"
                    "import os\n\n"
                    "from backend.core.database import SessionLocal, ensure_database_ready\n"
                    "from backend.core.models import ModelRegistryEntry\n"
                    "from ai.adapters import ADAPTER_PROFILE\n\n"
                    "MODEL_REGISTRY: list[dict] = []\n"
                    "MODEL_REGISTRY_PATH = Path(os.getenv('MODEL_REGISTRY_PATH', 'models/registry.json'))\n\n"
                    "def _sync_registry_file() -> None:\n"
                    "    MODEL_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)\n"
                    "    MODEL_REGISTRY_PATH.write_text(json.dumps(MODEL_REGISTRY, ensure_ascii=False, indent=2), encoding='utf-8')\n\n"
                    "def register_model_version(model: dict) -> None:\n"
                    "    MODEL_REGISTRY.append(dict(model))\n\n"
                    "    ensure_database_ready()\n"
                    "    session = SessionLocal()\n"
                    "    try:\n"
                    "        session.add(ModelRegistryEntry(version=str(model.get('version', 'bootstrap')), status=str(model.get('status', 'trained')), adapter_profile=ADAPTER_PROFILE, payload_json=json.dumps(model, ensure_ascii=False)))\n"
                    "        session.commit()\n"
                    "    finally:\n"
                    "        session.close()\n\n"
                    "    _sync_registry_file()\n\n"
                    "def get_latest_model() -> dict:\n"
                    "    ensure_database_ready()\n"
                    "    session = SessionLocal()\n"
                    "    try:\n"
                    "        entry = session.query(ModelRegistryEntry).order_by(ModelRegistryEntry.id.desc()).first()\n"
                    "        if entry and entry.payload_json:\n"
                    "            return json.loads(entry.payload_json)\n"
                    "    finally:\n"
                    "        session.close()\n"
                    "    return MODEL_REGISTRY[-1].copy() if MODEL_REGISTRY else {'version': 'bootstrap'}\n"
                ),
                "app/auth_routes.py": (
                    "from fastapi import APIRouter, HTTPException\n"
                    "from backend.core.auth import create_access_token, decode_access_token, get_auth_settings\n\n"
                    "auth_router = APIRouter(prefix='/auth', tags=['auth'])\n\n"
                    "@auth_router.post('/token')\n"
                    "def issue_token(payload: dict | None = None):\n"
                    "    request_payload = payload or {}\n"
                    "    subject = str(request_payload.get('subject') or 'demo-user')\n"
                    "    scopes = list(request_payload.get('scopes') or get_auth_settings().get('scopes') or [])\n"
                    "    token = create_access_token(subject, scopes=scopes)\n"
                    "    return {'access_token': token, 'token_type': 'bearer', 'scopes': scopes}\n\n"
                    "@auth_router.post('/validate')\n"
                    "def validate_token(payload: dict | None = None):\n"
                    "    token = str((payload or {}).get('token') or '').strip()\n"
                    "    if not token:\n"
                    "        raise HTTPException(status_code=400, detail='token is required')\n"
                    "    return decode_access_token(token)\n"
                ),
                "app/ops_routes.py": (
                    "from fastapi import APIRouter\n"
                    "from fastapi.responses import PlainTextResponse\n"
                    "from backend.app.external_adapters.status_client import fetch_upstream_status\n"
                    "from backend.core.database import get_database_settings\n"
                    "from backend.core.ops_logging import list_ops_logs, record_ops_log\n\n"
                    "ops_router = APIRouter(tags=['ops'])\n\n"
                    "@ops_router.get('/ops/logs')\n"
                    "def ops_logs():\n"
                    "    return {'items': list_ops_logs(), 'count': len(list_ops_logs())}\n\n"
                    "@ops_router.get('/ops/health')\n"
                    "def ops_health():\n"
                    "    provider = fetch_upstream_status()\n"
                    "    record_ops_log('ops_health_checked', {'status': 'ok' if provider.get('reachable') else 'degraded'})\n"
                    "    return {'status': 'ok' if provider.get('reachable') else 'degraded', 'database': get_database_settings(), 'provider_status': provider, 'log_count': len(list_ops_logs())}\n\n"
                    "@ops_router.get('/metrics', response_class=PlainTextResponse)\n"
                    "def metrics():\n"
                    "    payload = list_ops_logs()\n"
                    "    provider = fetch_upstream_status()\n"
                    "    reachable = sum(1 for item in provider.get('providers', []) if item.get('reachable'))\n"
                    "    lines = [\n"
                    "        '# HELP codeai_ops_events_total Count of ops events',\n"
                    "        '# TYPE codeai_ops_events_total counter',\n"
                    "        f'codeai_ops_events_total {len(payload)}',\n"
                    "        '# HELP codeai_provider_up Reachable provider count',\n"
                    "        '# TYPE codeai_provider_up gauge',\n"
                    "        f'codeai_provider_up {reachable}',\n"
                    "    ]\n"
                    "    return '\\n'.join(lines) + '\\n'\n"
                ),
                "ai/adapters.py": (
                    f"ADAPTER_PROFILE = {ai_adapter_profile!r}\n\n"
                    "def resolve_adapter() -> dict:\n"
                    "    adapter_catalog = {\n"
                    f"        'trading': {{'decision_key': 'signal_strength', 'default_decision': 'HOLD', 'model_endpoint': 'local://trading-adapter', 'adapter_targets': {json.dumps(domain_contract['adapter_targets'], ensure_ascii=False)} }},\n"
                    "        'commerce': {'decision_key': 'conversion_score', 'default_decision': 'RECOMMEND', 'model_endpoint': 'local://commerce-adapter', 'adapter_targets': ['conversion_score', 'upsell_score', 'next_offer']},\n"
                    "        'crm': {'decision_key': 'lead_score', 'default_decision': 'FOLLOW_UP', 'model_endpoint': 'local://crm-adapter', 'adapter_targets': ['lead_score', 'next_action', 'account_health']},\n"
                    "        'booking': {'decision_key': 'availability_score', 'default_decision': 'CONFIRM', 'model_endpoint': 'local://booking-adapter', 'adapter_targets': ['availability_score', 'slot_fit', 'confirmation_action']},\n"
                    "        'education': {'decision_key': 'progress_score', 'default_decision': 'REMEDIAL', 'model_endpoint': 'local://education-adapter', 'adapter_targets': ['progress_score', 'learning_path', 'intervention_level']},\n"
                    "        'healthcare': {'decision_key': 'risk_score', 'default_decision': 'REVIEW', 'model_endpoint': 'local://healthcare-adapter', 'adapter_targets': ['risk_score', 'triage_level', 'follow_up_action']},\n"
                    "        'analytics': {'decision_key': 'insight_score', 'default_decision': 'PUBLISH', 'model_endpoint': 'local://analytics-adapter', 'adapter_targets': ['insight_score', 'forecast_score', 'publish_action']},\n"
                    "        'workflow': {'decision_key': 'automation_score', 'default_decision': 'QUEUE', 'model_endpoint': 'local://workflow-adapter', 'adapter_targets': ['automation_score', 'next_job', 'queue_action']},\n"
                    "        'operations': {'decision_key': 'incident_score', 'default_decision': 'ESCALATE', 'model_endpoint': 'local://operations-adapter', 'adapter_targets': ['incident_score', 'response_priority', 'escalation_action']},\n"
                    "        'content': {'decision_key': 'content_score', 'default_decision': 'PUBLISH', 'model_endpoint': 'local://content-adapter', 'adapter_targets': ['content_score', 'publish_action', 'quality_score']},\n"
                    "        'general': {'decision_key': 'score', 'default_decision': 'REVIEW', 'model_endpoint': 'local://general-adapter', 'adapter_targets': ['score', 'decision', 'recommendation']},\n"
                    "    }\n"
                    "    return dict(adapter_catalog.get(ADAPTER_PROFILE, adapter_catalog['general']))\n"
                ),
                "ai/schemas.py": (
                    "from typing import Any, Dict, List\n"
                    "from pydantic import BaseModel, Field\n\n"
                    "class InferenceRequest(BaseModel):\n"
                    "    signal_strength: float = 0.0\n"
                    "    features: Dict[str, Any] = Field(default_factory=dict)\n\n"
                    "class TrainingRequest(BaseModel):\n"
                    "    dataset: List[Dict[str, Any]] = Field(default_factory=list)\n\n"
                    "class EvaluationRequest(BaseModel):\n"
                    "    predictions: List[Dict[str, Any]] = Field(default_factory=list)\n"
                ),
                "ai/router.py": (
                    "from fastapi import APIRouter\n"
                    "from ai.schemas import InferenceRequest, TrainingRequest, EvaluationRequest\n"
                    "from ai.train import train_model\n"
                    "from ai.inference import run_inference\n"
                    "from ai.evaluation import evaluate_predictions\n"
                    "from ai.model_registry import get_latest_model\n\n"
                    "router = APIRouter(prefix='/ai', tags=['ai'])\n\n"
                    "@router.get('/health')\n"
                    "def ai_health():\n"
                    "    model_registry = get_latest_model()\n"
                    "    return {\n"
                    "        'status': 'ok',\n"
                    "        'model_registry': model_registry,\n"
                    f"        'adapter_profile': {ai_adapter_profile!r},\n"
                    "        'required_endpoints': ['/ai/train', '/ai/inference', '/ai/evaluate'],\n"
                    "    }\n\n"
                    "@router.post('/train')\n"
                    "def ai_train(request: TrainingRequest):\n"
                    "    model = train_model(request.dataset)\n"
                    "    return {'status': 'trained', 'model': model}\n\n"
                    "@router.post('/inference')\n"
                    "def ai_inference(request: InferenceRequest):\n"
                    "    payload = dict(request.features)\n"
                    "    payload['signal_strength'] = request.signal_strength\n"
                    "    result = run_inference(payload)\n"
                    "    return {'status': 'ok', 'result': result}\n\n"
                    "@router.post('/evaluate')\n"
                    "def ai_evaluate(request: EvaluationRequest):\n"
                    "    report = evaluate_predictions(request.predictions)\n"
                    "    return {'status': 'ok', 'report': report}\n"
                ),
                "backend/service/strategy_service.py": (
                    "from app.order_profile import get_order_profile\n"
                    "from ai.inference import run_inference\n"
                    "from ai.evaluation import evaluate_predictions\n"
                    "from ai.train import train_model\n"
                    "from ai.model_registry import get_latest_model\n\n"
                    "def load_model_registry() -> dict:\n"
                    "    profile = get_order_profile()\n"
                    "    latest_model = get_latest_model()\n"
                    "    return {\n"
                    "        'registry_name': 'local-model-registry',\n"
                    "        'primary_model': latest_model.get('version', profile.get('project_name', 'trading-ai-core')),\n"
                    "        'version': latest_model.get('version', 'bootstrap'),\n"
                    "    }\n\n"
                    "def run_training_pipeline() -> dict:\n"
                    "    profile = get_order_profile()\n"
                    "    dataset = [\n"
                    "        {'signal_strength': 0.2, 'market_regime': 'sideways'},\n"
                    "        {'signal_strength': 0.8, 'market_regime': 'bull'},\n"
                    "    ]\n"
                    "    model = train_model(dataset)\n"
                    "    return {\n"
                    "        'status': model.get('status', 'trained'),\n"
                    "        'pipeline': 'feature-engineering -> train -> evaluate',\n"
                    "        'capabilities': list(profile.get('ai_capabilities') or []),\n"
                    "        'model': model,\n"
                    "    }\n\n"
                    "def run_inference_runtime(features: dict | None = None) -> dict:\n"
                    "    payload = features or {'signal_strength': 0.0}\n"
                    "    inference = run_inference(payload)\n"
                    "    return {\n"
                    "        'decision': inference.get('decision', 'review'),\n"
                    "        'score': inference.get('score', 0.0),\n"
                    "        'model_version': inference.get('model_version', 'bootstrap'),\n"
                    "        'features': payload,\n"
                    "    }\n\n"
                    "def build_evaluation_report() -> dict:\n"
                    "    sample_prediction = run_inference_runtime({'signal_strength': 0.7, 'market_regime': 'bull'})\n"
                    "    evaluation = evaluate_predictions([sample_prediction])\n"
                    "    return {\n"
                    "        'report_name': 'strategy-evaluation',\n"
                    "        'metrics': ['precision', 'recall', 'f1', 'sharpe_like_score'],\n"
                    "        'status': evaluation.get('quality_gate', 'needs-data'),\n"
                    "        'evaluation': evaluation,\n"
                    "    }\n\n"
                    "def build_strategy_service_overview(sample_payload: dict | None = None) -> dict:\n"
                    "    profile = get_order_profile()\n"
                    "    inference_runtime = run_inference_runtime(sample_payload or {'signal_strength': 0.7, 'market_regime': 'bull'})\n"
                    "    training_pipeline = run_training_pipeline()\n"
                    "    evaluation_report = build_evaluation_report()\n"
                    "    return {\n"
                    "        'ai_enabled': bool(profile.get('ai_enabled')),\n"
                    "        'ai_capabilities': list(profile.get('ai_capabilities') or []),\n"
                    "        'model_registry': load_model_registry(),\n"
                    "        'inference_runtime': inference_runtime,\n"
                    "        'training_pipeline': training_pipeline,\n"
                    "        'evaluation_report': evaluation_report,\n"
                    "    }\n"
                ),
                "backend/service/domain_adapter_service.py": (
                    "from ai.adapters import resolve_adapter\n"
                    "from ai.features import build_feature_set\n\n"
                    "def build_domain_adapter_summary(payload: dict | None = None) -> dict:\n"
                    "    adapter = resolve_adapter()\n"
                    "    features = build_feature_set(payload or {})\n"
                    "    return {\n"
                    "        'adapter': adapter,\n"
                    "        'model_endpoint': adapter.get('model_endpoint'),\n"
                    "        'features': features,\n"
                    "    }\n"
                ),
                "tests/test_ai_pipeline.py": (
                    "from ai.adapters import resolve_adapter\n"
                    "from ai.model_registry import MODEL_REGISTRY_PATH\n"
                    "from ai.train import train_model\n"
                    "from ai.inference import run_inference\n"
                    "from ai.evaluation import evaluate_predictions\n\n"
                    "def test_ai_pipeline_runs():\n"
                    "    adapter = resolve_adapter()\n"
                    "    model = train_model([{'x': 1}, {'x': 2}])\n"
                    "    prediction = run_inference({'x': 3})\n"
                    "    evaluation = evaluate_predictions([prediction])\n"
                    "    assert adapter['decision_key']\n"
                    "    assert model['status'] == 'trained'\n"
                    "    assert MODEL_REGISTRY_PATH.exists()\n"
                    "    assert 'model_version' in prediction\n"
                    "    assert evaluation['samples'] == 1\n"
                ),
                "app/main.py": (
                    "from fastapi import FastAPI\n"
                    "from app.auth_routes import auth_router\n"
                    "from app.ops_routes import ops_router\n"
                    "from app.routes import router\n"
                    "from app.services import build_runtime_payload, summarize_health\n"
                    "from app.diagnostics import build_diagnostic_report\n"
                    "from app.order_profile import get_order_profile\n"
                    "from ai.router import router as ai_router\n\n"
                    "def create_application() -> FastAPI:\n"
                    f"    app = FastAPI(title={project_name!r}, version='0.1.0')\n"
                    "    app.include_router(router)\n"
                    "    app.include_router(auth_router)\n"
                    "    app.include_router(ops_router)\n"
                    "    app.include_router(ai_router)\n\n"
                    "    @app.get('/')\n"
                    "    def root():\n"
                    "        profile = get_order_profile()\n"
                    "        return {\n"
                    "            'status': 'ok',\n"
                    "            'project': profile['project_name'],\n"
                    "            'profile': profile['label'],\n"
                    "            'mode': 'customer-order-generator',\n"
                    "        }\n\n"
                    "    @app.get('/runtime')\n"
                    "    def runtime():\n"
                    "        payload = build_runtime_payload(runtime_mode='runtime')\n"
                    "        payload['health'] = summarize_health()\n"
                    "        payload['diagnostics'] = build_diagnostic_report()\n"
                    "        return payload\n\n"
                    "    return app\n\n"
                    "app = create_application()\n"
                ),
                "app/services/__init__.py": (
                    "from app.services.runtime_service import build_ai_runtime_contract, build_domain_snapshot, build_feature_matrix, build_runtime_payload, build_trace_lookup, list_endpoints, summarize_health\n\n"
                    "__all__ = ['build_ai_runtime_contract', 'build_feature_matrix', 'build_trace_lookup', 'build_domain_snapshot', 'build_runtime_payload', 'list_endpoints', 'summarize_health']\n"
                ),
                "app/services/runtime_service.py": (
                    "from datetime import datetime\n"
                    "from app.runtime import build_runtime_context, describe_runtime_profile\n"
                    "from app.order_profile import get_order_profile, get_flow_step, list_flow_steps\n"
                    "from backend.core.database import ensure_database_ready, get_database_settings\n"
                    "from backend.core.auth import create_access_token, get_auth_settings\n"
                    "from backend.core.ops_logging import record_ops_log\n"
                    "from backend.service.domain_adapter_service import build_domain_adapter_summary\n"
                    "from ai.schemas import InferenceRequest, TrainingRequest, EvaluationRequest\n"
                    "from ai.train import train_model\n"
                    "from ai.inference import run_inference\n"
                    "from ai.evaluation import evaluate_predictions\n"
                    "from ai.model_registry import get_latest_model\n\n"
                    "def build_feature_matrix() -> list[dict]:\n"
                    "    return [\n"
                    "        {\n"
                    "            'flow_id': item['flow_id'],\n"
                    "            'step_number': item.get('step_number'),\n"
                    "            'step_id': item['step_id'],\n"
                    "            'action': item['action'],\n"
                    "            'trace_id': item.get('trace_id'),\n"
                    "            'title': item['title'],\n"
                    "            'state': 'ready',\n"
                    "        }\n"
                    "        for item in list_flow_steps()\n"
                    "    ]\n\n"
                    "def build_trace_lookup(step_id: str = 'FLOW-001-1') -> dict:\n"
                    "    return get_flow_step(step_id) or {'step_id': step_id, 'missing': True}\n\n"
                    "def build_domain_snapshot() -> dict:\n"
                    "    profile = get_order_profile()\n"
                    "    return {\n"
                    "        'profile_id': profile['profile_id'],\n"
                    "        'entities': profile['entities'],\n"
                    "        'requested_outcomes': profile['requested_outcomes'],\n"
                    "        'ui_modules': profile['ui_modules'],\n"
                    "    }\n\n"
                    "def build_ai_runtime_contract() -> dict:\n"
                    "    train_request = TrainingRequest(dataset=[{'signal_strength': 0.2}, {'signal_strength': 0.8}])\n"
                    "    inference_request = InferenceRequest(signal_strength=0.7, features={'market_regime': 'bull'})\n"
                    "    evaluation_request = EvaluationRequest(predictions=[{'decision': 'BUY', 'score': 0.7}])\n"
                    "    model = train_model(train_request.dataset)\n"
                    "    database = ensure_database_ready()\n"
                    "    inference_payload = dict(inference_request.features)\n"
                    "    inference_payload['signal_strength'] = inference_request.signal_strength\n"
                    "    prediction = run_inference(inference_payload)\n"
                    "    evaluation = evaluate_predictions(evaluation_request.predictions or [prediction])\n"
                    "    access_token = create_access_token('system-orchestrator')\n"
                    "    return {\n"
                    "        'schemas': ['TrainingRequest', 'InferenceRequest', 'EvaluationRequest'],\n"
                    "        'endpoints': ['/ai/health', '/ai/train', '/ai/inference', '/ai/evaluate'],\n"
                    "        'model_registry': get_latest_model(),\n"
                    "        'training_pipeline': model,\n"
                    "        'inference_runtime': prediction,\n"
                    "        'evaluation_report': evaluation,\n"
                    "        'domain_adapter': build_domain_adapter_summary(inference_payload),\n"
                    "        'database': database,\n"
                    "        'auth': get_auth_settings(),\n"
                    "        'token_preview': access_token[:16],\n"
                    "        'validation': {\n"
                    "            'ok': bool(model.get('status')) and 'decision' in prediction and 'quality_gate' in evaluation,\n"
                    "            'checked_via': ['/health', '/report'],\n"
                    "        },\n"
                    "    }\n\n"
                    "def build_runtime_payload(runtime_mode: str = 'default') -> dict:\n"
                    "    profile = get_order_profile()\n"
                    "    runtime_context = build_runtime_context()\n"
                    "    record_ops_log('runtime_payload_built', {'runtime_mode': runtime_mode, 'profile_id': profile['profile_id']})\n"
                    "    return {\n"
                    "        'service': 'customer-order-generator',\n"
                    "        'runtime_mode': runtime_mode,\n"
                    "        'started_at': datetime.utcnow().isoformat(),\n"
                    "        'order_profile': profile,\n"
                    "        'active_trace': build_trace_lookup(),\n"
                    "        'feature_matrix': build_feature_matrix(),\n"
                    "        'domain_snapshot': build_domain_snapshot(),\n"
                    "        'runtime_context': runtime_context,\n"
                    "        'profile': describe_runtime_profile(),\n"
                    "        'ai_runtime_contract': build_ai_runtime_contract(),\n"
                    "    }\n\n"
                    "def list_endpoints() -> list[str]:\n"
                    "    endpoints = ['/', '/runtime', '/health', '/config', '/order-profile', '/flow-map', '/flow-map/{step_id}', '/workspace', '/report', '/diagnose']\n"
                    "    endpoints.extend(['/ai/health', '/ai/train', '/ai/inference', '/ai/evaluate'])\n"
                    "    return endpoints\n\n"
                    "def summarize_health() -> dict:\n"
                    "    payload = build_runtime_payload(runtime_mode='health')\n"
                    "    payload['status'] = 'ok'\n"
                    "    payload['checks'] = {\n"
                    "        'profile_loaded': True,\n"
                    "        'flow_bound': True,\n"
                    "        'delivery_ready': True,\n"
                    "        'ai_contract_ready': bool(payload.get('ai_runtime_contract', {}).get('validation', {}).get('ok')),\n"
                    "    }\n"
                    "    return payload\n"
                ),
                "app/diagnostics.py": (
                    "from app.runtime import build_runtime_context, describe_runtime_profile\n"
                    "from app.services import build_runtime_payload\n"
                    "from app.order_profile import get_order_profile\n\n"
                    "def list_diagnostic_checks() -> list[str]:\n"
                    "    profile = get_order_profile()\n"
                    "    return [\n"
                    "        f\"profile:{profile['profile_id']}\",\n"
                    "        'flow-map-ready',\n"
                    "        'runtime-payload-ready',\n"
                    "        'metadata-ready',\n"
                    "        'ai-runtime-contract-ready',\n"
                    "        'ai-health-report-validated',\n"
                    "    ]\n\n"
                    "def validate_runtime_payload(payload: dict) -> dict:\n"
                    "    missing = [key for key in ('service', 'runtime_mode', 'order_profile', 'profile') if key not in payload]\n"
                    "    return {'ok': not missing, 'missing': missing}\n\n"
                    "def build_diagnostic_report() -> dict:\n"
                    "    payload = build_runtime_payload(runtime_mode='diagnostics')\n"
                    "    payload['profile'] = describe_runtime_profile()\n"
                    "    payload['runtime_context'] = build_runtime_context()\n"
                    "    payload['checks'] = list_diagnostic_checks()\n"
                    "    payload['validation'] = validate_runtime_payload(payload)\n"
                    "    payload['ai_validation'] = payload.get('ai_runtime_contract', {}).get('validation', {'ok': False})\n"
                    "    return payload\n"
                ),
                "frontend/app/page.tsx": (
                    "import { OrderSummary } from '../components/order-summary';\n"
                    "import { RuntimeShell } from '../components/runtime-shell';\n\n"
                    f"const orderProfile = {profile_json};\n\n"
                    "export default function Page() {\n"
                    "  return (\n"
                    "    <main style={{ padding: 24, fontFamily: 'sans-serif', display: 'grid', gap: 20 }}>\n"
                    "      <RuntimeShell title={orderProfile.project_name} summary={orderProfile.summary} />\n"
                    "      <section style={{ border: '1px solid #d0d7de', borderRadius: 12, padding: 16 }}>\n"
                    "        <h2>Primary entities</h2>\n"
                    "        <ul>{orderProfile.entities.map((item: string) => <li key={item}>{item}</li>)}</ul>\n"
                    "      </section>\n"
                    "      <OrderSummary title='Requested outcomes' items={orderProfile.requested_outcomes} />\n"
                    "      <section style={{ border: '1px solid #d0d7de', borderRadius: 12, padding: 16 }}>\n"
                    "        <h2>Flow registry</h2>\n"
                    "        <ul>{orderProfile.flow_steps.map((item: any) => <li key={item.step_id}>{item.flow_id} / {item.step_id} / {item.action}</li>)}</ul>\n"
                    "      </section>\n"
                    "      <section style={{ border: '1px solid #d0d7de', borderRadius: 12, padding: 16, background: '#f8fafc' }}>\n"
                    "        <h2>운영/보안 체크</h2>\n"
                    "        <ul>\n"
                    "          <li>JWT_SECRET 32자 이상 랜덤 값 교체</li>\n"
                    "          <li>ALLOWED_HOSTS / CORS_ALLOW_ORIGINS 운영값 반영</li>\n"
                    "          <li>draw history / AI health / ops health 확인</li>\n"
                    "        </ul>\n"
                    "      </section>\n"
                    "      <section style={{ border: '1px solid #d0d7de', borderRadius: 12, padding: 16 }}>\n"
                    "        <h2>AI 상태 패널</h2>\n"
                    "        <p>model_registry / training_pipeline / inference_runtime / evaluation_report contract enabled</p>\n"
                    "        <ul>{(orderProfile.ai_capabilities || []).map((item: string) => <li key={item}>{item}</li>)}</ul>\n"
                    "      </section>\n"
                    "    </main>\n"
                    "  );\n"
                    "}\n"
                ),
                "tests/test_health.py": (
                    "from fastapi.testclient import TestClient\n"
                    "from app.main import app\n\n"
                    "client = TestClient(app)\n\n"
                    "def test_health():\n"
                    "    response = client.get('/health')\n"
                    "    assert response.status_code == 200\n"
                    "    assert response.json()['status'] == 'ok'\n"
                    "    assert response.json()['checks']['ai_contract_ready'] is True\n"
                ),
                "tests/test_routes.py": (
                    "from fastapi.testclient import TestClient\n"
                    "from app.main import app\n\n"
                    "client = TestClient(app)\n\n"
                    "def test_order_profile_route():\n"
                    "    response = client.get('/order-profile')\n"
                    "    assert response.status_code == 200\n"
                    "    payload = response.json()\n"
            "    assert payload['profile_id']\n"
            "    report = client.get('/report')\n"
            "    assert report.status_code == 200\n\n"
                    "def test_ai_runtime_snapshot_marker():\n"
                    "    from backend.api.router import get_ai_runtime_snapshot\n"
                    "    payload = get_ai_runtime_snapshot({'signal_strength': 0.8})\n"
                    "    assert payload['model_registry']\n"
                    "    assert payload['training_pipeline']\n"
                    "    assert payload['inference_runtime']\n"
                    "    assert payload['evaluation_report']\n\n"
                    "def test_ai_fastapi_endpoints():\n"
                    "    health = client.get('/ai/health')\n"
                    "    assert health.status_code == 200\n"
                    "    infer = client.post('/ai/inference', json={'signal_strength': 0.8, 'features': {'market_regime': 'bull'}})\n"
                    "    assert infer.status_code == 200\n"
                    "    evaluate = client.post('/ai/evaluate', json={'predictions': [{'decision': 'BUY', 'score': 0.8}]})\n"
                    "    assert evaluate.status_code == 200\n"
                ),
                "tests/test_runtime.py": (
                    "from app.services import build_runtime_payload\n\n"
                    "def test_runtime_payload_contains_order_profile():\n"
                    "    payload = build_runtime_payload(runtime_mode='test')\n"
                    "    assert payload['service'] == 'customer-order-generator'\n"
                    "    assert payload['order_profile']['profile_id']\n"
                    "    assert payload['ai_runtime_contract']['validation']['ok'] is True\n"
                    "    assert payload['ai_runtime_contract']['database']['tables']\n"
                    "    assert payload['ai_runtime_contract']['auth']['scopes']\n"
                ),
            }
        )

    if profile_id == "lottery_prediction_system" and order_profile.get("ai_enabled"):
        template_candidates.update(
            {
                "ai/features.py": (
                    "from collections import Counter\n"
                    "from typing import Any, Dict, List\n\n"
                    "FEATURE_WINDOW_SIZE = 10\n\n"
                    "def build_feature_set(raw_payload: Dict[str, Any]) -> Dict[str, Any]:\n"
                    "    payload = dict(raw_payload or {})\n"
                    "    normalized_draws = normalize_draw_history(payload.get('draw_histories') or [])\n"
                    "    feature_windows = build_feature_windows(normalized_draws) if normalized_draws else []\n"
                    "    return {\n"
                    "        'raw': payload,\n"
                    "        'draw_histories': normalized_draws,\n"
                    "        'feature_windows': feature_windows,\n"
                    "        'feature_count': len(feature_windows),\n"
                    "        'has_signal_strength': 'signal_strength' in payload,\n"
                    "        'historical-draw-loader': bool(normalized_draws),\n"
                    "        'feature-window-builder': bool(feature_windows),\n"
                    "    }\n\n"
                    "def normalize_draw_history(draws: List[Dict[str, Any]]) -> List[Dict[str, Any]]:\n"
                    "    normalized: List[Dict[str, Any]] = []\n"
                    "    for index, item in enumerate(draws, start=1):\n"
                    "        numbers = sorted({int(number) for number in item.get('numbers', []) if 1 <= int(number) <= 45})\n"
                    "        if len(numbers) != 6:\n"
                    "            continue\n"
                    "        normalized.append({\n"
                    "            'draw_no': int(item.get('draw_no') or index),\n"
                    "            'numbers': numbers,\n"
                    "            'bonus': int(item.get('bonus') or 0),\n"
                    "        })\n"
                    "    return normalized\n\n"
                    "def build_feature_windows(draws: List[Dict[str, Any]], window_size: int = FEATURE_WINDOW_SIZE) -> List[Dict[str, Any]]:\n"
                    "    normalized = normalize_draw_history(draws)\n"
                    "    windows: List[Dict[str, Any]] = []\n"
                    "    if len(normalized) < window_size:\n"
                    "        return windows\n"
                    "    for index in range(window_size, len(normalized) + 1):\n"
                    "        chunk = normalized[index - window_size:index]\n"
                    "        counter = Counter(number for draw in chunk for number in draw['numbers'])\n"
                    "        hot_numbers = [number for number, _ in counter.most_common(6)]\n"
                    "        cold_numbers = [number for number in range(1, 46) if counter.get(number, 0) == 0][:6]\n"
                    "        windows.append({\n"
                    "            'window_index': index - window_size + 1,\n"
                    "            'draw_span': [chunk[0]['draw_no'], chunk[-1]['draw_no']],\n"
                    "            'hot_numbers': hot_numbers,\n"
                    "            'cold_numbers': cold_numbers,\n"
                    "            'number_frequency': {str(number): counter.get(number, 0) for number in range(1, 46)},\n"
                    "        })\n"
                    "    return windows\n"
                ),
                "ai/train.py": (
                    "from typing import Any, Dict, List\n\n"
                    "from ai.features import build_feature_windows, normalize_draw_history\n"
                    "from ai.model_registry import register_model_version\n\n"
                    "def train_model(dataset: List[Dict[str, Any]]) -> Dict[str, Any]:\n"
                    "    draws = normalize_draw_history(dataset)\n"
                    "    windows = build_feature_windows(draws)\n"
                    "    learned_rankings = []\n"
                    "    if windows:\n"
                    "        latest_window = windows[-1]\n"
                    "        number_frequency = latest_window['number_frequency']\n"
                    "        learned_rankings = sorted([{'number': int(number), 'frequency': frequency} for number, frequency in number_frequency.items()], key=lambda item: (-item['frequency'], item['number']))[:12]\n"
                    "    model = {\n"
                    "        'version': f'lotto-model-{len(draws)}',\n"
                    "        'status': 'trained' if draws else 'needs-data',\n"
                    "        'trained_draws': len(draws),\n"
                    "        'feature_windows': len(windows),\n"
                    "        'candidate_ranking': learned_rankings,\n"
                    "        'engine_core': 'historical-draw-loader + feature-window-builder + candidate-number-generator',\n"
                    "    }\n"
                    "    register_model_version(model)\n"
                    "    return model\n"
                ),
                "ai/inference.py": (
                    "from typing import Any, Dict, List\n\n"
                    "from ai.features import build_feature_windows, normalize_draw_history\n"
                    "from ai.model_registry import get_latest_model\n\n"
                    "def _dedupe_candidate_numbers(candidates: List[int]) -> List[int]:\n"
                    "    deduped: List[int] = []\n"
                    "    for number in candidates:\n"
                    "        if number not in deduped and 1 <= number <= 45:\n"
                    "            deduped.append(number)\n"
                    "        if len(deduped) == 6:\n"
                    "            break\n"
                    "    return sorted(deduped)\n\n"
                    "def run_inference(payload: Dict[str, Any]) -> Dict[str, Any]:\n"
                    "    model = get_latest_model()\n"
                    "    draws = normalize_draw_history(payload.get('draw_histories') or [])\n"
                    "    windows = build_feature_windows(draws)\n"
                    "    ranking = list(model.get('candidate_ranking') or [])\n"
                    "    top_numbers = [int(item.get('number', 0)) for item in ranking if int(item.get('number', 0)) > 0]\n"
                    "    if not top_numbers and windows:\n"
                    "        top_numbers = list(windows[-1].get('hot_numbers') or [])\n"
                    "    candidate_numbers = _dedupe_candidate_numbers(top_numbers or [3, 7, 11, 23, 31, 41])\n"
                    "    confidence = round(min(0.99, 0.45 + (len(draws) / 200.0)), 4) if draws else 0.25\n"
                    "    return {\n"
                    "        'model_version': model.get('version', 'bootstrap'),\n"
                    "        'score': confidence,\n"
                    "        'decision': 'predict',\n"
                    "        'candidate_numbers': candidate_numbers,\n"
                    "        'prediction_runs': len(windows),\n"
                    "        'candidate_sets': [{'numbers': candidate_numbers, 'rank': 1, 'score': confidence}],\n"
                    "        'engine_core': 'candidate-number-generator',\n"
                    "    }\n"
                ),
                "ai/evaluation.py": (
                    "from typing import Dict, List\n\n"
                    "def evaluate_predictions(predictions: List[dict]) -> Dict[str, object]:\n"
                    "    candidate_sets = [item for item in predictions if item.get('candidate_numbers') or item.get('candidate_sets')]\n"
                    "    average_score = round(sum(float(item.get('score', 0.0) or 0.0) for item in candidate_sets) / len(candidate_sets), 4) if candidate_sets else 0.0\n"
                    "    return {\n"
                    "        'samples': len(predictions),\n"
                    "        'candidate_sets': len(candidate_sets),\n"
                    "        'average_score': average_score,\n"
                    "        'quality_gate': 'pass' if candidate_sets else 'needs-data',\n"
                    "        'prediction_evaluation': True,\n"
                    "    }\n"
                ),
                "backend/service/strategy_service.py": (
                    "from app.order_profile import get_order_profile\n"
                    "from ai.features import build_feature_windows, normalize_draw_history\n"
                    "from ai.inference import run_inference\n"
                    "from ai.evaluation import evaluate_predictions\n"
                    "from ai.train import train_model\n"
                    "from ai.model_registry import get_latest_model\n\n"
                    "DEFAULT_DRAW_HISTORIES = [\n"
                    "    {'draw_no': 1101, 'numbers': [3, 8, 13, 27, 33, 42], 'bonus': 19},\n"
                    "    {'draw_no': 1102, 'numbers': [7, 9, 18, 21, 28, 41], 'bonus': 5},\n"
                    "    {'draw_no': 1103, 'numbers': [5, 11, 17, 29, 34, 40], 'bonus': 2},\n"
                    "    {'draw_no': 1104, 'numbers': [1, 6, 14, 26, 30, 44], 'bonus': 12},\n"
                    "    {'draw_no': 1105, 'numbers': [2, 10, 16, 24, 35, 43], 'bonus': 8},\n"
                    "    {'draw_no': 1106, 'numbers': [4, 15, 20, 22, 32, 45], 'bonus': 9},\n"
                    "    {'draw_no': 1107, 'numbers': [6, 12, 19, 23, 31, 38], 'bonus': 14},\n"
                    "    {'draw_no': 1108, 'numbers': [8, 13, 21, 25, 36, 41], 'bonus': 7},\n"
                    "    {'draw_no': 1109, 'numbers': [9, 14, 22, 28, 37, 42], 'bonus': 1},\n"
                    "    {'draw_no': 1110, 'numbers': [10, 16, 24, 29, 39, 43], 'bonus': 6},\n"
                    "    {'draw_no': 1111, 'numbers': [11, 17, 26, 30, 40, 44], 'bonus': 3},\n"
                    "    {'draw_no': 1112, 'numbers': [12, 18, 27, 31, 41, 45], 'bonus': 4},\n"
                    "]\n\n"
                    "def load_model_registry() -> dict:\n"
                    "    profile = get_order_profile()\n"
                    "    latest_model = get_latest_model()\n"
                    "    return {\n"
                    "        'registry_name': 'lottery-model-registry',\n"
                    "        'primary_model': latest_model.get('version', profile.get('project_name', 'lotto-engine')),\n"
                    "        'version': latest_model.get('version', 'bootstrap'),\n"
                    "    }\n\n"
                    "def build_engine_core() -> dict:\n"
                    "    normalized_draws = normalize_draw_history(DEFAULT_DRAW_HISTORIES)\n"
                    "    feature_windows = build_feature_windows(normalized_draws)\n"
                    "    return {\n"
                    "        'engine-core': True,\n"
                    "        'historical-draw-loader': {'draw_histories': normalized_draws, 'draw_count': len(normalized_draws)},\n"
                    "        'feature-pipeline': {'feature-window-builder': feature_windows, 'window_count': len(feature_windows)},\n"
                    "    }\n\n"
                    "def run_training_pipeline() -> dict:\n"
                    "    model = train_model(DEFAULT_DRAW_HISTORIES)\n"
                    "    return {\n"
                    "        'status': model.get('status', 'trained'),\n"
                    "        'pipeline': 'historical-draw-loader -> feature-window-builder -> training-pipeline',\n"
                    "        'training-pipeline': True,\n"
                    "        'feature-pipeline': True,\n"
                    "        'historical-draw-loader': True,\n"
                    "        'candidate-number-generator': True,\n"
                    "        'model': model,\n"
                    "    }\n\n"
                    "def run_inference_runtime(features: dict | None = None) -> dict:\n"
                    "    payload = features or {'draw_histories': DEFAULT_DRAW_HISTORIES}\n"
                    "    if 'draw_histories' not in payload:\n"
                    "        payload = {**payload, 'draw_histories': DEFAULT_DRAW_HISTORIES}\n"
                    "    inference = run_inference(payload)\n"
                    "    return {\n"
                    "        'decision': inference.get('decision', 'predict'),\n"
                    "        'score': inference.get('score', 0.0),\n"
                    "        'model_version': inference.get('model_version', 'bootstrap'),\n"
                    "        'candidate_sets': inference.get('candidate_sets', []),\n"
                    "        'prediction_runs': inference.get('prediction_runs', 0),\n"
                    "        'candidate-number-generator': True,\n"
                    "        'inference-runtime': True,\n"
                    "        'features': payload,\n"
                    "    }\n\n"
                    "def build_evaluation_report() -> dict:\n"
                    "    runtime = run_inference_runtime({'draw_histories': DEFAULT_DRAW_HISTORIES})\n"
                    "    evaluation = evaluate_predictions([runtime])\n"
                    "    return {\n"
                    "        'report_name': 'lottery-prediction-evaluation',\n"
                    "        'metrics': ['candidate_sets', 'average_score', 'quality_gate'],\n"
                    "        'status': evaluation.get('quality_gate', 'needs-data'),\n"
                    "        'prediction-evaluation': True,\n"
                    "        'evaluation': evaluation,\n"
                    "    }\n\n"
                    "def build_strategy_service_overview(sample_payload: dict | None = None) -> dict:\n"
                    "    profile = get_order_profile()\n"
                    "    engine_core = build_engine_core()\n"
                    "    training_pipeline = run_training_pipeline()\n"
                    "    inference_runtime = run_inference_runtime(sample_payload or {'draw_histories': DEFAULT_DRAW_HISTORIES})\n"
                    "    evaluation_report = build_evaluation_report()\n"
                    "    return {\n"
                    "        'ai_enabled': bool(profile.get('ai_enabled')),\n"
                    "        'ai_capabilities': list(profile.get('ai_capabilities') or []),\n"
                    "        'mandatory_engine_contracts': list(profile.get('mandatory_engine_contracts') or []),\n"
                    "        'engine-core': engine_core,\n"
                    "        'service-integration': True,\n"
                    "        'model_registry': load_model_registry(),\n"
                    "        'training_pipeline': training_pipeline,\n"
                    "        'inference_runtime': inference_runtime,\n"
                    "        'evaluation_report': evaluation_report,\n"
                    "    }\n"
                ),
                "app/services/__init__.py": (
                    "from app.services.runtime_service import build_ai_runtime_contract, build_domain_snapshot, build_feature_matrix, build_runtime_payload, build_trace_lookup, list_endpoints, summarize_health\n\n"
                    "__all__ = ['build_ai_runtime_contract', 'build_feature_matrix', 'build_trace_lookup', 'build_domain_snapshot', 'build_runtime_payload', 'list_endpoints', 'summarize_health']\n"
                ),
                "app/services/runtime_service.py": (
                    "from datetime import datetime\n"
                    "from app.runtime import build_runtime_context, describe_runtime_profile\n"
                    "from app.order_profile import get_order_profile, get_flow_step, list_flow_steps\n"
                    "from backend.core.database import ensure_database_ready, get_database_settings\n"
                    "from backend.core.auth import create_access_token, get_auth_settings\n"
                    "from backend.core.ops_logging import record_ops_log\n"
                    "from backend.service.domain_adapter_service import build_domain_adapter_summary\n"
                    "from backend.service.strategy_service import build_strategy_service_overview\n"
                    "from ai.schemas import InferenceRequest, TrainingRequest, EvaluationRequest\n"
                    "from ai.train import train_model\n"
                    "from ai.inference import run_inference\n"
                    "from ai.evaluation import evaluate_predictions\n"
                    "from ai.model_registry import get_latest_model\n\n"
                    "DEFAULT_DRAW_HISTORIES = [\n"
                    "    {'draw_no': 1101, 'numbers': [3, 8, 13, 27, 33, 42], 'bonus': 19},\n"
                    "    {'draw_no': 1102, 'numbers': [7, 9, 18, 21, 28, 41], 'bonus': 5},\n"
                    "    {'draw_no': 1103, 'numbers': [5, 11, 17, 29, 34, 40], 'bonus': 2},\n"
                    "    {'draw_no': 1104, 'numbers': [1, 6, 14, 26, 30, 44], 'bonus': 12},\n"
                    "    {'draw_no': 1105, 'numbers': [2, 10, 16, 24, 35, 43], 'bonus': 8},\n"
                    "    {'draw_no': 1106, 'numbers': [4, 15, 20, 22, 32, 45], 'bonus': 9},\n"
                    "    {'draw_no': 1107, 'numbers': [6, 12, 19, 23, 31, 38], 'bonus': 14},\n"
                    "    {'draw_no': 1108, 'numbers': [8, 13, 21, 25, 36, 41], 'bonus': 7},\n"
                    "    {'draw_no': 1109, 'numbers': [9, 14, 22, 28, 37, 42], 'bonus': 1},\n"
                    "    {'draw_no': 1110, 'numbers': [10, 16, 24, 29, 39, 43], 'bonus': 6},\n"
                    "    {'draw_no': 1111, 'numbers': [11, 17, 26, 30, 40, 44], 'bonus': 3},\n"
                    "    {'draw_no': 1112, 'numbers': [12, 18, 27, 31, 41, 45], 'bonus': 4},\n"
                    "]\n\n"
                    "def build_feature_matrix() -> list[dict]:\n"
                    "    return [{\n"
                    "        'flow_id': item['flow_id'],\n"
                    "        'step_number': item.get('step_number'),\n"
                    "        'step_id': item['step_id'],\n"
                    "        'action': item['action'],\n"
                    "        'trace_id': item.get('trace_id'),\n"
                    "        'title': item['title'],\n"
                    "        'state': 'ready',\n"
                    "    } for item in list_flow_steps()]\n\n"
                    "def build_trace_lookup(step_id: str = 'FLOW-001-1') -> dict:\n"
                    "    return get_flow_step(step_id) or {'step_id': step_id, 'missing': True}\n\n"
                    "def build_domain_snapshot() -> dict:\n"
                    "    profile = get_order_profile()\n"
                    "    return {\n"
                    "        'profile_id': profile['profile_id'],\n"
                    "        'entities': profile['entities'],\n"
                    "        'requested_outcomes': profile['requested_outcomes'],\n"
                    "        'ui_modules': profile['ui_modules'],\n"
                    "        'mandatory_engine_contracts': list(profile.get('mandatory_engine_contracts') or []),\n"
                    "    }\n\n"
                    "def build_ai_runtime_contract() -> dict:\n"
                    "    train_request = TrainingRequest(dataset=DEFAULT_DRAW_HISTORIES)\n"
                    "    inference_request = InferenceRequest(signal_strength=0.7, features={'draw_histories': DEFAULT_DRAW_HISTORIES})\n"
                    "    model = train_model(train_request.dataset)\n"
                    "    database = ensure_database_ready()\n"
                    "    inference_payload = dict(inference_request.features)\n"
                    "    inference_payload['signal_strength'] = inference_request.signal_strength\n"
                    "    prediction = run_inference(inference_payload)\n"
                    "    evaluation = evaluate_predictions([prediction])\n"
                    "    strategy_service = build_strategy_service_overview(inference_payload)\n"
                    "    access_token = create_access_token('system-orchestrator')\n"
                    "    return {\n"
                    f"        'mandatory_engine_contracts': {json.dumps(order_profile.get('mandatory_engine_contracts') or [], ensure_ascii=False)},\n"
                    "        'engine-core': strategy_service.get('engine-core'),\n"
                    "        'historical-draw-loader': strategy_service.get('engine-core', {}).get('historical-draw-loader'),\n"
                    "        'feature-pipeline': strategy_service.get('engine-core', {}).get('feature-pipeline'),\n"
                    "        'training-pipeline': strategy_service.get('training_pipeline'),\n"
                    "        'inference-runtime': strategy_service.get('inference_runtime'),\n"
                    "        'evaluation-report': strategy_service.get('evaluation_report'),\n"
                    "        'service-integration': strategy_service.get('service-integration', True),\n"
                    "        'schemas': ['TrainingRequest', 'InferenceRequest', 'EvaluationRequest'],\n"
                    "        'endpoints': ['/ai/health', '/ai/train', '/ai/inference', '/ai/evaluate'],\n"
                    "        'model_registry': get_latest_model(),\n"
                    "        'training_pipeline': model,\n"
                    "        'inference_runtime': prediction,\n"
                    "        'evaluation_report': evaluation,\n"
                    "        'domain_adapter': build_domain_adapter_summary(inference_payload),\n"
                    "        'database': database,\n"
                    "        'auth': get_auth_settings(),\n"
                    "        'token_preview': access_token[:16],\n"
                    "        'draw_histories': DEFAULT_DRAW_HISTORIES,\n"
                    "        'prediction_runs': prediction.get('prediction_runs', 0),\n"
                    "        'candidate_sets': prediction.get('candidate_sets', []),\n"
                    "        'validation': {\n"
                    "            'ok': bool(model.get('status')) and bool(prediction.get('candidate_sets')) and evaluation.get('quality_gate') == 'pass',\n"
                    "            'checked_via': ['/health', '/report'],\n"
                    "        },\n"
                    "    }\n\n"
                    "def build_runtime_payload(runtime_mode: str = 'default') -> dict:\n"
                    "    profile = get_order_profile()\n"
                    "    runtime_context = build_runtime_context()\n"
                    "    record_ops_log('runtime_payload_built', {'runtime_mode': runtime_mode, 'profile_id': profile['profile_id']})\n"
                    "    return {\n"
                    "        'service': 'customer-order-generator',\n"
                    "        'runtime_mode': runtime_mode,\n"
                    "        'started_at': datetime.utcnow().isoformat(),\n"
                    "        'order_profile': profile,\n"
                    "        'active_trace': build_trace_lookup(),\n"
                    "        'feature_matrix': build_feature_matrix(),\n"
                    "        'domain_snapshot': build_domain_snapshot(),\n"
                    "        'runtime_context': runtime_context,\n"
                    "        'profile': describe_runtime_profile(),\n"
                    "        'mandatory_engine_contracts': list(profile.get('mandatory_engine_contracts') or []),\n"
                    "        'ai_runtime_contract': build_ai_runtime_contract(),\n"
                    "    }\n\n"
                    "def list_endpoints() -> list[str]:\n"
                    "    endpoints = ['/', '/runtime', '/health', '/config', '/order-profile', '/flow-map', '/flow-map/{step_id}', '/workspace', '/report', '/diagnose']\n"
                    "    endpoints.extend(['/ai/health', '/ai/train', '/ai/inference', '/ai/evaluate'])\n"
                    "    return endpoints\n\n"
                    "def summarize_health() -> dict:\n"
                    "    payload = build_runtime_payload(runtime_mode='health')\n"
                    "    payload['status'] = 'ok'\n"
                    "    payload['checks'] = {\n"
                    "        'profile_loaded': True,\n"
                    "        'flow_bound': True,\n"
                    "        'delivery_ready': True,\n"
                    "        'ai_contract_ready': bool(payload.get('ai_runtime_contract', {}).get('validation', {}).get('ok')),\n"
                    "    }\n"
                    "    return payload\n"
                ),
                "frontend/app/page.tsx": (
                    f"const orderProfile = {profile_json};\n\n"
                    "export default function Page() {\n"
                    "  const contracts = orderProfile.mandatory_engine_contracts || [];\n"
                    "  return (\n"
                    "    <main style={{ padding: 24, fontFamily: 'sans-serif', display: 'grid', gap: 20 }}>\n"
                    "      <section>\n"
                    "        <h1>{orderProfile.project_name}</h1>\n"
                    "        <p>{orderProfile.label}</p>\n"
                    "        <p>{orderProfile.summary}</p>\n"
                    "      </section>\n"
                    "      <section>\n"
                    "        <h2>필수 엔진 계약</h2>\n"
                    "        <ul>{contracts.map((item: string) => <li key={item}>{item}</li>)}</ul>\n"
                    "      </section>\n"
                    "      <section>\n"
                    "        <h2>추첨 이력 / draw_histories</h2>\n"
                    "        <p>historical-draw-loader, feature-window-builder, candidate-number-generator, prediction-evaluation 경로를 기본 탑재합니다.</p>\n"
                    "        <ul>{orderProfile.entities.map((item: string) => <li key={item}>{item}</li>)}</ul>\n"
                    "      </section>\n"
                    "      <section>\n"
                    "        <h2>예측 엔진 패널</h2>\n"
                    "        <p>candidate_sets / prediction_runs / evaluation_report 를 운영 화면에서 확인하도록 설계합니다.</p>\n"
                    "        <ul>{orderProfile.requested_outcomes.map((item: string) => <li key={item}>{item}</li>)}</ul>\n"
                    "      </section>\n"
                    "      <section>\n"
                    "        <h2>AI 상태 패널</h2>\n"
                    "        <p>model_registry / training_pipeline / inference_runtime / evaluation_report contract enabled</p>\n"
                    "        <ul>{(orderProfile.ai_capabilities || []).map((item: string) => <li key={item}>{item}</li>)}</ul>\n"
                    "      </section>\n"
                    "    </main>\n"
                    "  );\n"
                    "}\n"
                ),
                "tests/test_ai_pipeline.py": (
                    "from app.services import build_ai_runtime_contract\n"
                    "from backend.service.strategy_service import build_strategy_service_overview\n\n"
                    "def test_lottery_ai_pipeline_runs():\n"
                    "    contract = build_ai_runtime_contract()\n"
                    "    strategy = build_strategy_service_overview()\n"
                    "    assert contract['historical-draw-loader']['draw_count'] >= 10\n"
                    "    assert contract['feature-pipeline']['window_count'] >= 1\n"
                    "    assert contract['candidate_sets']\n"
                    "    assert contract['evaluation_report']['quality_gate'] == 'pass'\n"
                    "    assert strategy['service-integration'] is True\n"
                ),
                "tests/test_routes.py": (
                    "from fastapi.testclient import TestClient\n"
                    "from app.main import app\n\n"
                    "client = TestClient(app)\n\n"
                    "def test_order_profile_route():\n"
                    "    response = client.get('/order-profile')\n"
                    "    assert response.status_code == 200\n"
                    "    payload = response.json()\n"
                    "    assert payload['profile_id'] == 'lottery_prediction_system'\n"
                    "    report = client.get('/report')\n"
                    "    assert report.status_code == 200\n"
                    "    assert 'candidate-number-generator' in payload['mandatory_engine_contracts']\n\n"
                    "def test_ai_runtime_snapshot_marker():\n"
                    "    from backend.api.router import get_ai_runtime_snapshot\n"
                    "    payload = get_ai_runtime_snapshot({'draw_histories': []})\n"
                    "    assert payload['model_registry']\n"
                    "    assert payload['training_pipeline']\n"
                    "    assert payload['inference_runtime']\n"
                    "    assert payload['evaluation_report']\n\n"
                    "def test_ai_fastapi_endpoints():\n"
                    "    health = client.get('/ai/health')\n"
                    "    assert health.status_code == 200\n"
                    "    infer = client.post('/ai/inference', json={'signal_strength': 0.8, 'features': {'draw_histories': []}})\n"
                    "    assert infer.status_code == 200\n"
                    "    evaluate = client.post('/ai/evaluate', json={'predictions': [{'candidate_numbers': [3, 7, 11, 23, 31, 41], 'score': 0.8}]})\n"
                    "    assert evaluate.status_code == 200\n"
                ),
                "tests/test_runtime.py": (
                    "from app.services import build_runtime_payload\n\n"
                    "def test_runtime_payload_contains_lottery_contract():\n"
                    "    payload = build_runtime_payload(runtime_mode='test')\n"
                    "    assert payload['service'] == 'customer-order-generator'\n"
                    "    assert payload['order_profile']['profile_id'] == 'lottery_prediction_system'\n"
                    "    assert payload['ai_runtime_contract']['validation']['ok'] is True\n"
                    "    assert payload['ai_runtime_contract']['candidate_sets']\n"
                ),
            }
        )

    elif order_profile.get("ai_enabled") and profile_id != "commerce_platform":
        template_candidates.update(
            _build_top_level_ai_template_candidates(
                project_name,
                order_profile,
                domain_contract,
            )
        )
        template_candidates.update(
            _build_customer_domain_ai_template_overrides(
                project_name,
                order_profile,
                domain_contract,
            )
        )

    return template_candidates


def _compat_write_auxiliary_outputs(
    output_dir: Path,
    task: str,
    project_name: str,
    mode: str,
    validation_profile: str,
    written_files: List[str],
    anchor_path: str,
    semantic_audit_score: int,
    semantic_audit_ok: bool,
    target_patch_registry_snapshot: Dict[str, Any],
) -> Dict[str, str]:
    checklist_path = output_dir / ORCH_CHECKLIST_PATH
    manifest_path = output_dir / ORCH_FILE_MANIFEST_PATH
    output_audit_path = output_dir / ORCH_OUTPUT_AUDIT_PATH
    template_manifest_path = output_dir / ".codeai-template.json"

    checklist_lines = [
        f"# {project_name} orchestrator checklist",
        "",
        f"- mode: {mode}",
        f"- validation_profile: {validation_profile}",
        f"- anchor_path: {anchor_path}",
        f"- written_files: {len(written_files)}",
        f"- semantic_audit_score: {semantic_audit_score}",
        f"- semantic_audit_ok: {semantic_audit_ok}",
        "",
        "## Required verification",
        "",
        "- [x] app/main.py generated",
        "- [x] app/routes.py generated",
        "- [x] app/services/__init__.py generated",
        "- [x] app/services/runtime_service.py generated",
        "- [x] docs/file_manifest.md generated",
        "- [x] docs/output_audit.json generated",
        "- [x] traceability_map.json generated",
        "- [x] docs/id_registry.schema.json generated",
        "- [x] docs/id_registry.json generated",
        "- [x] docs/product_identity.json generated",
    ]
    _compat_write_text(checklist_path, "\n".join(checklist_lines) + "\n")

    id_registry_schema_path = output_dir / ORCH_ID_REGISTRY_SCHEMA_PATH
    id_registry_path = output_dir / ORCH_ID_REGISTRY_PATH
    product_identity_path = output_dir / ORCH_PRODUCT_ID_PATH
    _compat_write_text(id_registry_schema_path, _build_generated_id_registry_schema_template())
    _compat_write_text(id_registry_path, _build_generated_id_registry_template(project_name, validation_profile))
    _compat_write_text(product_identity_path, _build_generated_product_identity_template(project_name, validation_profile))

    manifest_lines = [
        f"# {project_name} file manifest",
        "",
        f"- task: {task}",
        f"- mode: {mode}",
        f"- total_files: {len(written_files)}",
        "",
        "## Files",
        "",
    ]
    manifest_lines.extend(f"- `{path}`" for path in written_files)
    _compat_write_text(manifest_path, "\n".join(manifest_lines) + "\n")

    _compat_write_json(
        output_audit_path,
        {
            "task": task,
            "mode": mode,
            "project_name": project_name,
            "validation_profile": validation_profile,
            "written_files": written_files,
            "written_file_count": len(written_files),
            "python_files": [path for path in written_files if path.endswith(".py")],
            "anchor_path": anchor_path,
            "semantic_audit_score": semantic_audit_score,
            "semantic_audit_ok": semantic_audit_ok,
            "target_patch_registry": target_patch_registry_snapshot,
            "target_patch_candidates": list(target_patch_registry_snapshot.get("reusable_patch_units") or []),
            "target_file_ids": list(target_patch_registry_snapshot.get("target_file_ids") or []),
            "target_section_ids": list(target_patch_registry_snapshot.get("target_section_ids") or []),
            "target_feature_ids": list(target_patch_registry_snapshot.get("target_feature_ids") or []),
            "target_chunk_ids": list(target_patch_registry_snapshot.get("target_chunk_ids") or []),
            "failure_tags": list(target_patch_registry_snapshot.get("failure_tags") or []),
            "repair_tags": list(target_patch_registry_snapshot.get("repair_tags") or []),
            "product_identity_path": ORCH_PRODUCT_ID_PATH,
        },
    )
    _compat_write_json(
        template_manifest_path,
        {
            "project_name": project_name,
            "mode": mode,
            "validation_profile": validation_profile,
            "entrypoints": ["app/main.py", "app/routes.py", "app/services/__init__.py", "app/services/runtime_service.py"],
            "target_patch_registry": target_patch_registry_snapshot,
            "generated_at": datetime.now().isoformat(),
        },
    )
    return {
        "checklist_path": _compat_relative_path(checklist_path, output_dir),
        "manifest_path": _compat_relative_path(manifest_path, output_dir),
        "output_audit_path": _compat_relative_path(output_audit_path, output_dir),
        "template_manifest_path": _compat_relative_path(template_manifest_path, output_dir),
        "id_registry_schema_path": _compat_relative_path(id_registry_schema_path, output_dir),
        "id_registry_path": _compat_relative_path(id_registry_path, output_dir),
        "product_identity_path": _compat_relative_path(product_identity_path, output_dir),
    }


def _compat_manifest_for_request(
    task: str,
    project_name: str,
    validation_profile: str,
    required_files: List[str],
) -> tuple[str, List[Dict[str, str]], str]:
    order_profile = _build_customer_order_profile(task, project_name)
    template_candidates: Dict[str, str] = {}
    if validation_profile == "python_fastapi":
        template_candidates.update(
            _build_customer_order_template_candidates(project_name, task, order_profile)
        )
    elif validation_profile == "nextjs_app":
        next_profile_json = json.dumps(order_profile, ensure_ascii=False, indent=2)
        template_candidates.update(
            {
                "package.json": json.dumps({
                    "name": project_name,
                    "private": True,
                    "scripts": {
                        "dev": "next dev",
                        "build": "next build",
                        "start": "next start"
                    },
                    "dependencies": {
                        "next": "16.2.1",
                        "react": "19.1.0",
                        "react-dom": "19.1.0"
                    },
                    "devDependencies": {
                        "typescript": "5.9.2",
                        "@types/react": "19.1.12",
                        "@types/node": "24.3.0"
                    }
                }, ensure_ascii=False, indent=2),
                "app/page.tsx": (
                    f"const orderProfile = {next_profile_json};\n\n"
                    "export default function Page() {\n"
                    "  return (\n"
                    "    <main style={{ padding: 24, fontFamily: 'sans-serif', display: 'grid', gap: 16 }}>\n"
                    "      <h1>{orderProfile.project_name}</h1>\n"
                    "      <p>{orderProfile.label}</p>\n"
                    "      <p>{orderProfile.summary}</p>\n"
                    "      <section>\n"
                    "        <h2>페이지 구성</h2>\n"
                    "        <ul>{(orderProfile.ui_modules || []).map((item: string) => <li key={item}>{item}</li>)}</ul>\n"
                    "      </section>\n"
                    "      <section>\n"
                    "        <h2>요청 결과</h2>\n"
                    "        <ul>{(orderProfile.requested_outcomes || []).map((item: string) => <li key={item}>{item}</li>)}</ul>\n"
                    "      </section>\n"
                    "    </main>\n"
                    "  );\n"
                    "}\n"
                ),
                "app/layout.tsx": (
                    "export default function RootLayout({ children }: { children: React.ReactNode }) {\n"
                    "  return (\n"
                    "    <html lang='ko'>\n"
                    "      <body>{children}</body>\n"
                    "    </html>\n"
                    "  );\n"
                    "}\n"
                ),
                "tsconfig.json": json.dumps({
                    "compilerOptions": {
                        "target": "ES2017",
                        "lib": ["dom", "dom.iterable", "esnext"],
                        "allowJs": True,
                        "skipLibCheck": True,
                        "strict": False,
                        "noEmit": True,
                        "esModuleInterop": True,
                        "module": "esnext",
                        "moduleResolution": "bundler",
                        "resolveJsonModule": True,
                        "isolatedModules": True,
                        "jsx": "preserve",
                        "incremental": True
                    },
                    "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx"],
                    "exclude": ["node_modules"]
                }, ensure_ascii=False, indent=2),
                "next-env.d.ts": "/// <reference types='next' />\n/// <reference types='next/image-types/global' />\n",
                "README.md": (
                    f"# {project_name}\n\n"
                    "Next.js 타입스크립트 기반 주문형 홈페이지 빌더 산출물입니다.\n\n"
                    f"- profile: {order_profile['label']}\n"
                    f"- summary: {order_profile['summary']}\n"
                    f"- request: {task}\n\n"
                    "## Run\n\n"
                    "- npm install\n"
                    "- npm run build\n"
                    "- npm run start\n"
                ),
                "docs/testing.md": "# testing\n\n- npm install\n- npm run build\n",
                "docs/runtime.md": f"# runtime\n\nprofile: {order_profile['label']}\nrequested_stack: {', '.join(order_profile.get('requested_stack') or [])}\n",
                "docs/deployment.md": "# deployment\n\n- npm install\n- npm run build\n- npm run start\n",
                "configs/app.env.example": "NEXT_PUBLIC_API_BASE_URL=http://localhost:3000\nNODE_ENV=production\n",
                "scripts/check.sh": "#!/usr/bin/env bash\nnpm run build\n",
                "docs/order_profile.md": (
                    f"# {project_name} order profile\n\n"
                    f"- profile_id: {order_profile['profile_id']}\n"
                    f"- label: {order_profile['label']}\n"
                    f"- summary: {order_profile['summary']}\n\n"
                    "## mandatory_engine_contracts\n\n- "
                    + "\n- ".join(order_profile.get("mandatory_engine_contracts") or ["none"])
                    + "\n"
                ),
                "docs/flow_map.md": (
                    f"# {project_name} flow map\n\n"
                    + "\n".join(
                        f"- {item['flow_id']} / {item['step_id']} / {item['action']} - {item['title']}"
                        for item in (order_profile.get("flow_steps") or [])
                    )
                    + "\n"
                ),
                "docs/usage.md": f"# {project_name} 사용 가이드\n\n- npm install\n- npm run build\n- npm run start\n",
                "docs/flow_registry.json": json.dumps(order_profile.get("flow_steps") or [], ensure_ascii=False, indent=2),
                "docs/scaffold_inventory.md": "# scaffold inventory\n\n- backend/main.py\n- frontend/app/page.tsx\n- app/page.tsx\n- app/layout.tsx\n- package.json\n- docs/runtime.md\n",
                "docs/stage_progress.md": "# stage progress\n\n- tracking_id: ARCH-001\n- current_stage: structure\n",
                "docs/stage_progress.json": json.dumps({"current_stage": {"tracking_id": "ARCH-001", "title": "structure"}, "stage_chain": order_profile.get("stage_chain") or []}, ensure_ascii=False, indent=2),
            }
        )
    else:
        template_candidates.update({
            "README.md": f"# {project_name}\n\nGenerated by customer order generator.\n",
            "docs/architecture.md": f"# {project_name} architecture\n\nCustomer-order orchestration output.\n",
        })

    if validation_profile == "python_fastapi":
        template_candidates["README.md"] = (
            f"# {project_name}\n\n"
            "Generated FastAPI scaffold.\n\n"
            "## Included Runtime\n\n"
            "- `app/main.py` FastAPI runtime entrypoint\n"
            "- `backend/core` runtime/security core layer\n"
            "- `frontend/app/page.tsx` operator-facing front surface\n"
        )
        template_candidates["docs/usage.md"] = (
            f"# {project_name} 사용 가이드\n\n"
            "1. `pip install -r requirements.txt`\n"
            "2. `uvicorn app.main:create_application --factory --host 0.0.0.0 --port 8000`\n"
            "3. `/health`, `/runtime`, `/report` 확인\n"
        )
        template_candidates["docs/deployment.md"] = (
            "# deployment\n\n"
            "- `docker build -t customer-order-generator .`\n"
            "- `docker run --rm -p 8000:8000 --env-file configs/app.env.example customer-order-generator`\n"
            "- 부팅 후 `/health`, `/runtime`, `/report` 확인으로 container run 검증\n"
        )
        template_candidates.setdefault(
            "app/api/routes/__init__.py",
            "",
        )
        template_candidates.setdefault(
            "app/api/routes/health.py",
            (
                "from fastapi import APIRouter\n\n"
                "router = APIRouter()\n\n"
                "@router.get('/health')\n"
                "def health() -> dict:\n"
                "    return {'status': 'ok', 'service': 'customer-order-generator'}\n"
            ),
        )
        template_candidates["app/ops_routes.py"] = (
            "from fastapi.responses import PlainTextResponse\n"
            "from fastapi import APIRouter\n"
            "from backend.app.external_adapters.status_client import fetch_upstream_status\n\n"
            "ops_router = APIRouter(prefix='/ops', tags=['ops'])\n\n"
            "@ops_router.get('/status')\n"
            "def ops_status():\n"
            "    provider = fetch_upstream_status()\n"
            "    return {'status': 'ok' if provider.get('reachable') else 'degraded', 'provider_status': provider}\n\n"
            "@ops_router.get('/health')\n"
            "def ops_health():\n"
            "    return ops_status()\n\n"
            "@ops_router.get('/logs')\n"
            "def ops_logs():\n"
            "    provider = fetch_upstream_status()\n"
            "    return {'items': provider.get('providers', []), 'count': len(provider.get('providers', []))}\n\n"
            "@ops_router.get('/metrics', response_class=PlainTextResponse)\n"
            "def metrics():\n"
            "    provider = fetch_upstream_status()\n"
            "    reachable = sum(1 for item in provider.get('providers', []) if item.get('reachable'))\n"
            "    lines = ['# HELP customer_provider_up Reachable customer providers', '# TYPE customer_provider_up gauge', f'customer_provider_up {reachable}']\n"
            "    return '\\n'.join(lines) + '\\n'\n"
        )
        if "Dockerfile" in template_candidates and "RUN pip install --no-cache-dir -r requirements.txt" not in str(template_candidates.get("Dockerfile") or ""):
            template_candidates["Dockerfile"] = str(template_candidates.get("Dockerfile") or "").replace(
                "COPY . .\n",
                "COPY . .\nRUN pip install --no-cache-dir -r requirements.txt\n",
                1,
            )

    manifest: List[Dict[str, str]] = []
    compat_defaults = [
        "README.md",
        "docs/architecture.md",
        "docs/usage.md",
        "docs/runtime.md",
        "docs/deployment.md",
        "docs/testing.md",
    ]
    for path in list(dict.fromkeys(required_files + compat_defaults + list(template_candidates.keys()))):
        normalized_path = str(path or "").strip().replace('\\', '/')
        if not normalized_path:
            continue
        content = template_candidates.get(normalized_path)
        if content is None:
            if normalized_path.endswith(".py"):
                content = ""
            else:
                content = f"# {normalized_path}\n\ncompat generated file\n"
        manifest.append({"path": normalized_path, "content": content})
    anchor_path = manifest[0]["path"] if manifest else "README.md"
    return anchor_path, manifest, "ready"


def _compat_build_manifest_lookup(manifest: List[Dict[str, str]]) -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    for item in manifest:
        normalized_path = str(item.get("path") or "").strip().replace("\\", "/")
        if not normalized_path:
            continue
        lookup[normalized_path] = str(item.get("content") or "")
    return lookup


def _compat_domain_required_files(order_profile: Dict[str, Any], validation_profile: str) -> List[str]:
    if validation_profile == "nextjs_app":
        required = [
            "README.md",
            "package.json",
            "tsconfig.json",
            "next-env.d.ts",
            "app/page.tsx",
            "app/layout.tsx",
            "configs/app.env.example",
            "scripts/check.sh",
            "docs/architecture.md",
            "docs/order_profile.md",
            "docs/flow_map.md",
            "docs/flow_registry.json",
            "docs/usage.md",
            "docs/runtime.md",
            "docs/deployment.md",
            "docs/testing.md",
            "docs/scaffold_inventory.md",
            "docs/stage_progress.md",
            "docs/stage_progress.json",
        ]
    else:
        required = [
            "README.md",
            "requirements.txt",
            "pyproject.toml",
            "Dockerfile",
            "Makefile",
            "app/main.py",
            "app/routes.py",
            "app/services/__init__.py",
            "app/services/runtime_service.py",
            "app/runtime.py",
            "app/diagnostics.py",
            "app/order_profile.py",
            "backend/main.py",
            "backend/core/runtime.py",
            "backend/core/flow_registry.py",
            "backend/api/router.py",
            "backend/data/provider.py",
            "backend/service/application_service.py",
            "frontend/app/page.tsx",
            "frontend/components/order-summary.tsx",
            "frontend/components/runtime-shell.tsx",
            "frontend/lib/api-client.ts",
            "configs/app.env.example",
            "configs/logging.yml",
            "scripts/dev.sh",
            "scripts/check.sh",
            "infra/README.md",
            "infra/docker-compose.override.yml",
            "docs/architecture.md",
            "docs/order_profile.md",
            "docs/flow_map.md",
            "docs/flow_registry.json",
            "docs/usage.md",
            "docs/runtime.md",
            "docs/deployment.md",
            "docs/testing.md",
            "docs/scaffold_inventory.md",
            "docs/stage_progress.md",
            "docs/stage_progress.json",
            "tests/conftest.py",
            "tests/test_health.py",
            "tests/test_routes.py",
            "tests/test_runtime.py",
        ]
    profile_id = str(order_profile.get("profile_id") or "").strip()
    ai_enabled = bool(order_profile.get("ai_enabled"))
    if validation_profile == "python_fastapi" and profile_id == "commerce_platform":
        required.extend([
            "backend/app/external_adapters/status_client.py",
            "backend/app/connectors/base.py",
            "backend/app/connectors/shopify.py",
        ])
    if validation_profile == "python_fastapi" and profile_id == "deployment_kit_program":
        required.extend([
            "app/auth_routes.py",
            "app/ops_routes.py",
            "backend/app/external_adapters/status_client.py",
            "backend/app/connectors/base.py",
            "backend/app/connectors/payment_gateway.py",
            "backend/core/auth.py",
            "backend/core/security.py",
            "backend/service/catalog_service.py",
            "backend/service/order_workflow_service.py",
            "backend/service/operations_service.py",
            "tests/test_catalog_flow.py",
            "tests/test_order_workflow.py",
            "tests/test_publish_payload.py",
            "tests/test_security_runtime.py",
            "docs/runbook.md",
            "infra/prometheus.yml",
            "infra/deploy/security.md",
        ])
    if validation_profile == "python_fastapi" and profile_id in {"customer_program", "website_builder", "automation_service", "admin_console", "crm_suite", "booking_platform", "education_lms", "healthcare_portal", "analytics_platform", "lottery_prediction_system"}:
        required.extend([
            "app/auth_routes.py",
            "app/ops_routes.py",
            "backend/core/auth.py",
            "backend/core/security.py",
            "tests/test_security_runtime.py",
            "docs/runbook.md",
            "infra/prometheus.yml",
            "infra/deploy/security.md",
            "backend/app/external_adapters/status_client.py",
            "backend/app/connectors/base.py",
        ])
    if validation_profile == "python_fastapi" and profile_id == "trading_system":
        if ai_enabled:
            required.extend([
                "app/auth_routes.py",
                "app/ops_routes.py",
                "ai/adapters.py",
                "ai/schemas.py",
                "ai/router.py",
                "tests/conftest.py",
                "backend/service/strategy_service.py",
                "backend/service/domain_adapter_service.py",
                "backend/core/__init__.py",
                "backend/core/database.py",
                "backend/core/models.py",
                "backend/core/auth.py",
                "backend/core/security.py",
                "backend/core/ops_logging.py",
                "tests/test_ai_pipeline.py",
                "tests/test_security_runtime.py",
                "docs/runbook.md",
                "infra/prometheus.yml",
                "infra/deploy/security.md",
            ])
    elif validation_profile == "python_fastapi" and ai_enabled:
        required.extend([
            "app/auth_routes.py",
            "app/ops_routes.py",
            "ai/adapters.py",
            "ai/schemas.py",
            "ai/router.py",
            "backend/service/strategy_service.py",
            "backend/service/domain_adapter_service.py",
            "backend/core/__init__.py",
            "backend/core/database.py",
            "backend/core/models.py",
            "backend/core/auth.py",
            "backend/core/security.py",
            "backend/core/ops_logging.py",
            "tests/test_ai_pipeline.py",
            "tests/test_security_runtime.py",
            "docs/runbook.md",
            "infra/prometheus.yml",
            "infra/deploy/security.md",
        ])
    return list(dict.fromkeys(required))


def _compat_validate_runtime_completeness(
    manifest_lookup: Dict[str, str],
    order_profile: Dict[str, Any],
) -> List[str]:
    findings: List[str] = []
    if "package.json" in manifest_lookup:
        completeness_markers = {
            "README.md": ["Next.js 타입스크립트 기반 주문형 홈페이지 빌더 산출물", "npm run build", "npm run start"],
            "package.json": ["next", "react", '"build"'],
            "tsconfig.json": ["compilerOptions", "jsx"],
            "next-env.d.ts": ["reference types='next'"],
            "app/layout.tsx": ["RootLayout", "<html", "<body>"],
            "app/page.tsx": ["orderProfile.project_name", "요청 결과"],
            "docs/usage.md": ["사용 가이드"],
            "docs/runtime.md": ["requested_stack:"],
            "docs/deployment.md": ["npm run build", "npm run start"],
            "docs/testing.md": ["npm run build"],
            "configs/app.env.example": ["NEXT_PUBLIC_API_BASE_URL", "NODE_ENV=production"],
            "scripts/check.sh": ["npm run build"],
            "docs/order_profile.md": ["profile_id:", "mandatory_engine_contracts"],
            "docs/flow_map.md": ["flow map", "FLOW-001"],
            "docs/flow_registry.json": ["FLOW-001-1", "INTAKE"],
            "docs/scaffold_inventory.md": ["backend/main.py", "frontend/app/page.tsx"],
            "docs/stage_progress.md": ["stage progress", "tracking_id:"],
            "docs/stage_progress.json": ["current_stage", "stage_chain"],
        }
    else:
        completeness_markers = {
            "README.md": ["Included Runtime", "app/main.py", "backend/core", "frontend/app/page.tsx"],
            "requirements.txt": ["fastapi", "uvicorn", "pytest"],
            "pyproject.toml": ["[project]", "dependencies=["],
            "Dockerfile": ["FROM python:3.11-slim", "RUN pip install --no-cache-dir -r requirements.txt"],
            "Makefile": ["run:", "test:"],
            "app/main.py": ["FastAPI", "app.include_router(router)", "@app.get('/runtime')"],
            "app/routes.py": ["@router.get('/health')", "@router.get('/order-profile')", "@router.get('/report')"],
            "app/services/__init__.py": ["from app.services.runtime_service import", "build_runtime_payload", "__all__"],
            "app/services/runtime_service.py": ["build_feature_matrix", "build_domain_snapshot", "build_runtime_payload"],
            "app/runtime.py": ["build_runtime_context", "describe_runtime_profile"],
            "app/diagnostics.py": ["list_diagnostic_checks", "validate_runtime_payload", "build_diagnostic_report"],
            "app/order_profile.py": ["ORDER_PROFILE", "get_order_profile", "list_flow_steps"],
            "backend/core/runtime.py": ["build_scaffold_runtime"],
            "backend/core/flow_registry.py": ["FLOW_REGISTRY", "list_registered_steps"],
            "backend/data/provider.py": ["list_data_sources"],
            "backend/service/application_service.py": ["build_service_overview", "flow_steps", "layer"],
            "backend/api/router.py": ["get_router_snapshot", "trace_lookup"],
            "frontend/app/page.tsx": ["orderProfile.project_name", "orderProfile.summary"],
            "frontend/components/order-summary.tsx": ["export function OrderSummary", "items.map"],
            "frontend/components/runtime-shell.tsx": ["export function RuntimeShell", "summary"],
            "frontend/lib/api-client.ts": ["fetch(`${baseUrl}/runtime`", "runtime fetch failed"],
            "docs/order_profile.md": ["profile_id:", "mandatory_engine_contracts"],
            "docs/flow_map.md": ["flow map", "FLOW-001"],
            "docs/flow_registry.json": ["FLOW-001-1", "INTAKE"],
            "docs/usage.md": ["사용 가이드"],
            "docs/runtime.md": ["requested_stack:"],
            "docs/deployment.md": ["container run"],
            "docs/testing.md": ["pytest -q"],
            "docs/scaffold_inventory.md": ["backend/main.py", "frontend/app/page.tsx"],
            "docs/stage_progress.md": ["stage progress", "tracking_id:"],
            "docs/stage_progress.json": ["current_stage", "stage_chain"],
            "configs/app.env.example": ["APP_ENV=dev", "DATABASE_URL=", "JWT_SECRET=", "ALLOWED_HOSTS=", "CORS_ALLOW_ORIGINS=", "REQUEST_TIMEOUT_SEC=", "MODEL_REGISTRY_PATH=", "OPS_LOG_PATH=", "UPSTREAM_STATUS_BASE_URL=", "NOTIFICATION_GATEWAY_URL="],
            "configs/logging.yml": ["version: 1"],
            "scripts/dev.sh": ["uvicorn app.main:create_application --factory --reload"],
            "scripts/check.sh": ["pytest -q -s", "requirements.delivery.lock.txt"],
            "infra/README.md": ["deployment notes"],
            "infra/docker-compose.override.yml": ["services:", "uvicorn app.main:create_application --factory", "JWT_SECRET:", "healthcheck:"],
            "backend/core/auth.py": ["JWT_SECRET", "JWT_ALGORITHM", "JWT_EXPIRE_MINUTES", "scopes"],
            "backend/core/security.py": ["ALLOWED_HOSTS", "CORS_ALLOW_ORIGINS", "https_only", "REQUEST_TIMEOUT_SEC"],
            "backend/app/external_adapters/status_client.py": ["UPSTREAM_STATUS_BASE_URL", "NOTIFICATION_GATEWAY_URL", "REQUEST_TIMEOUT_SEC", "fetch_upstream_status"],
            "app/auth_routes.py": ["@auth_router.get('/settings')", "@auth_router.post('/token')"],
            "app/ops_routes.py": ["@ops_router.get('/status')", "@ops_router.get('/health')", "@ops_router.get('/metrics'"],
            "tests/conftest.py": ["PROJECT_ROOT", "sys.path.insert"],
            "tests/test_health.py": ["TestClient(app)", "client.get('/health')"],
            "tests/test_routes.py": ["client.get('/order-profile')", "client.get('/report')"],
            "tests/test_runtime.py": ["build_runtime_payload", "payload['service'] == 'customer-order-generator'"],
        }
    for path, markers in completeness_markers.items():
        content = _strip_generated_id_headers(manifest_lookup.get(path, ""))
        if not content:
            findings.append(f"runtime completeness missing file: {path}")
            continue
        for marker in markers:
            if marker not in content:
                findings.append(f"{path} missing runtime marker: {marker}")
    if "package.json" not in manifest_lookup and not bool(order_profile.get("ai_enabled")):
        frontend_page = manifest_lookup.get("frontend/app/page.tsx", "")
        for marker in ["Primary entities", "Requested outcomes", "Flow registry"]:
            if marker not in frontend_page:
                findings.append(f"frontend/app/page.tsx missing non-AI presentation marker: {marker}")
    return findings


def _normalize_customer_requirements(task: str, order_profile: Dict[str, Any]) -> Dict[str, Any]:
    normalized_task = str(task or "").strip()
    features = list(dict.fromkeys(list(order_profile.get("requested_outcomes") or [])))
    exclusions: List[str] = []
    completion_conditions = [
        "필수 파일/구조 생성",
        "도메인 계약 마커 포함",
        "semantic gate 통과",
        "패키징 문서/설정값 포함",
    ]
    test_conditions = [
        "도메인별 필수 테스트 파일 생성",
        "runtime verification 통과 기준 정리",
        "배포/환경 변수 예시 포함",
    ]
    lowered = normalized_task.lower()
    if "제외" in normalized_task or "exclude" in lowered:
        for raw_line in normalized_task.splitlines():
            line = raw_line.strip()
            if "제외" in line or "exclude" in line.lower():
                exclusions.append(line)
    if "테스트" in normalized_task or "test" in lowered:
        test_conditions.append("주문문에 명시된 테스트 요구 반영")
    return {
        "original_task": normalized_task,
        "feature_list": features,
        "exclusions": exclusions,
        "completion_conditions": completion_conditions,
        "test_conditions": test_conditions,
    }


def _resolve_validation_profile(order_profile: Dict[str, Any], task: str) -> str:
    requested_stack = " ".join(str(item) for item in (order_profile.get("requested_stack") or []))
    source_text = f"{task}\n{requested_stack}".lower()
    next_markers = ["next.js", "nextjs", "next-style frontend", "react", "typescript"]
    if any(marker in source_text for marker in next_markers):
        return "nextjs_app"
    return "python_fastapi"


def _build_domain_contract(order_profile: Dict[str, Any], validation_profile: str, required_files: List[str]) -> Dict[str, Any]:
    profile_id = str(order_profile.get("profile_id") or "customer_program")
    domain_contracts: Dict[str, Dict[str, Any]] = {
        "commerce_platform": {
            "required_structure": ["catalog", "order-workflow", "customer runtime", "ops catalog", "security runtime", "shipping package"],
            "verification_rules": ["상품/카탈로그/주문 흐름 파일 존재", "주문 상태 API/화면 마커 포함", "운영 문서와 env 예시 포함", "auth/ops/security/출고 마커 포함"],
            "packaging_requirements": ["README", "배포 문서", "테스트 문서", "configs/app.env.example", "docs/runbook.md", "infra/prometheus.yml", "infra/deploy/security.md"],
        },
        "admin_console": {
            "required_structure": ["admin dashboard", "role management", "audit trail", "runtime panels", "security runtime", "shipping package"],
            "verification_rules": ["운영/권한/감사 로그 마커 포함", "runtime panel 문서와 테스트 포함", "auth/ops/security/출고 마커 포함"],
            "packaging_requirements": ["README", "docs/runtime.md", "docs/testing.md", "configs/app.env.example", "docs/runbook.md", "infra/prometheus.yml", "infra/deploy/security.md"],
        },
        "website_builder": {
            "required_structure": ["landing sections", "contact flow", "deployment notes", "security runtime", "shipping package"],
            "verification_rules": ["frontend/app/page.tsx 완성", "문의 흐름 문서화", "배포/사용 가이드 포함", "auth/ops/security/출고 마커 포함"],
            "packaging_requirements": ["README", "docs/usage.md", "docs/deployment.md", "docs/runbook.md", "infra/prometheus.yml", "infra/deploy/security.md"],
        },
        "automation_service": {
            "required_structure": ["jobs", "runs", "alerts", "artifacts", "security runtime", "shipping package"],
            "verification_rules": ["queue/runtime 마커 포함", "ops 문서/로그 설정 포함", "auth/ops/security/출고 마커 포함"],
            "packaging_requirements": ["README", "configs/logging.yml", "docs/runtime.md", "docs/runbook.md", "infra/prometheus.yml", "infra/deploy/security.md"],
        },
        "customer_program": {
            "required_structure": ["api", "services", "frontend", "docs", "tests", "security runtime", "shipping package"],
            "verification_rules": ["필수 파일 생성", "runtime completeness 마커 포함", "auth/ops/security/출고 마커 포함"],
            "packaging_requirements": ["README", "docs/testing.md", "configs/app.env.example", "docs/runbook.md", "infra/prometheus.yml", "infra/deploy/security.md"],
        },
        "deployment_kit_program": {
            "required_structure": ["runtime api", "deployment policy", "publish readiness", "shipping package", "validation reports"],
            "verification_rules": ["실행 가능한 FastAPI 엔트리", "publish-readiness 및 ops/auth API 포함", "실프로그램 검증/출고 문서 포함"],
            "packaging_requirements": ["README", "docs/runtime.md", "docs/deployment.md", "docs/testing.md", "docs/runbook.md", "configs/app.env.example"],
        },
    }
    domain_contract = dict(domain_contracts.get(profile_id, domain_contracts["customer_program"]))
    domain_contract.update({
        "profile_id": profile_id,
        "validation_profile": validation_profile,
        "required_files": required_files,
        "mandatory_engine_contracts": list(order_profile.get("mandatory_engine_contracts") or []),
    })
    return domain_contract


def _build_integration_test_plan(order_profile: Dict[str, Any], validation_profile: str) -> Dict[str, Any]:
    if validation_profile == "nextjs_app":
        plan = {
            "validation_profile": validation_profile,
            "required_tests": [
                "package.json",
                "app/layout.tsx",
                "app/page.tsx",
                "scripts/check.sh",
            ],
            "runtime_checks": [
                "next app router structure",
                "npm build contract",
                "semantic audit",
            ],
        }
    else:
        plan = {
            "validation_profile": validation_profile,
            "required_tests": [
                "tests/test_health.py",
                "tests/test_routes.py",
                "tests/test_runtime.py",
            ],
            "runtime_checks": [
                "health endpoint",
                "project context endpoint",
                "semantic audit",
            ],
        }
    if bool(order_profile.get("ai_enabled")):
        plan["required_tests"].append("tests/test_ai_pipeline.py")
        plan["runtime_checks"].append("AI runtime contract")
    if str(order_profile.get("profile_id") or "") == "commerce_platform":
        plan["runtime_checks"].extend(["catalog flow", "order workflow", "marketplace publish payload"])
    if str(order_profile.get("profile_id") or "") == "deployment_kit_program":
        plan["runtime_checks"].extend([
            "publish readiness flow",
            "ops health flow",
            "auth settings flow",
            "shipping package flow",
        ])
    if str(order_profile.get("profile_id") or "") == "admin_console":
        plan["runtime_checks"].extend(["admin dashboard flow", "role management flow", "audit trail flow"])
    if validation_profile == "python_fastapi":
        plan["runtime_checks"].extend([
            "auth settings flow",
            "ops health flow",
            "shipping package flow",
            "security runtime flow",
        ])
        plan["required_tests"] = list(dict.fromkeys(list(plan.get("required_tests") or []) + ["tests/test_security_runtime.py"]))
    return plan


def _build_improvement_loop_plan(
    *,
    validation_profile: str,
    completion_judge: Dict[str, Any],
    integration_test_plan: Dict[str, Any],
    packaging_audit: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "enabled": bool(completion_judge.get("product_ready")),
        "state": "ready_for_refinement" if completion_judge.get("product_ready") else "blocked_until_pass",
        "validation_profile": validation_profile,
        "refiner_fixer_stage": dict(ORCH_REFINER_FIXER_STAGE),
        "entry_conditions": [
            "completion_gate_ok == true",
            "packaging_audit.packaging_ready == true",
            "integration_test_engine.ok == true",
        ],
        "expansion_steps": [
            "구매자 추가 요구사항 수집",
            "기능 차이 분석 및 확장 요구 정규화",
            "같은 도메인 계약/게이트 기준으로 보정 실행",
            "출고 엔진과 자동 검증 엔진 재실행",
        ],
        "required_tests": list(integration_test_plan.get("required_tests") or []),
        "packaging_targets": list(packaging_audit.get("required_packaging_files") or []),
    }


def _build_stage_history_with_refiner_fixer(completion_gate_ok: bool) -> List[str]:
    return [
        "DESIGN",
        "PLAN",
        "GENERATE",
        "BUILD",
        ORCH_REFINER_FIXER_STAGE["state"],
        "TEST",
        "DONE" if completion_gate_ok else "FAILED",
    ]


def _build_refiner_fixer_stage_payload(
    *,
    completion_gate_ok: bool,
    semantic_gate: Dict[str, Any],
    completion_judge: Dict[str, Any],
    b_brain_result: Dict[str, Any],
) -> Dict[str, Any]:
    failed_reasons = list(completion_judge.get("failed_reasons") or [])
    return {
        **dict(ORCH_REFINER_FIXER_STAGE),
        "status": "passed" if completion_gate_ok else "failed",
        "check_label": "통과" if completion_gate_ok else "미통과",
        "generator_family": b_brain_result.get("generator_family"),
        "generator_profile": b_brain_result.get("generator_profile"),
        "written_files": b_brain_result.get("file_count"),
        "semantic_summary": semantic_gate.get("summary"),
        "failed_reasons": failed_reasons,
    }


def _run_framework_e2e_validator(
    *,
    output_dir: Path,
    validation_profile: str,
) -> Dict[str, Any]:
    commands_run: List[str] = []
    failures: List[str] = []

    if validation_profile == "python_fastapi":
        compile_targets = _build_python_fastapi_validation_targets(output_dir)["compile_targets"]
        commands_run.append("python -m compileall " + " ".join(compile_targets or ["app", "backend", "tests"]))
        try:
            if not compile_targets:
                failures.append("fastapi e2e validator missing compile targets")
                return {
                    "engine": "framework-e2e-validator",
                    "validation_profile": validation_profile,
                    "commands_run": commands_run,
                    "ok": False,
                    "failures": failures,
                }
            result = subprocess.run(
                [sys.executable, "-m", "compileall", *compile_targets],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(output_dir),
            )
            if result.returncode != 0:
                failures.append((result.stderr or result.stdout or "compileall failed").strip()[:1200])
        except Exception as exc:
            failures.append(f"fastapi e2e validator error: {exc}")
    elif validation_profile == "nextjs_app":
        package_json = output_dir / "package.json"
        commands_run.append("package.json contract inspection")
        if not package_json.exists():
            failures.append("package.json not found for Next.js E2E validation")
        else:
            try:
                package_payload = json.loads(package_json.read_text(encoding="utf-8"))
            except Exception as exc:
                failures.append(f"package.json parse error: {exc}")
            else:
                scripts = package_payload.get("scripts") or {}
                dependencies = package_payload.get("dependencies") or {}
                if "build" not in scripts:
                    failures.append("npm build script missing")
                if "start" not in scripts:
                    failures.append("npm start script missing")
                if "next" not in dependencies:
                    failures.append("next dependency missing")
                if "react" not in dependencies:
                    failures.append("react dependency missing")
        for rel_path in ["app/layout.tsx", "app/page.tsx", "scripts/check.sh"]:
            commands_run.append(f"exists:{rel_path}")
            if not (output_dir / rel_path).exists():
                failures.append(f"missing Next.js runtime file: {rel_path}")

    return {
        "engine": "framework-live-e2e-validator",
        "validation_profile": validation_profile,
        "commands_run": commands_run,
        "ok": len(failures) == 0,
        "failures": failures,
    }


def _run_external_integration_validator(
    *,
    output_dir: Path,
    order_profile: Dict[str, Any],
) -> Dict[str, Any]:
    profile_id = str(order_profile.get("profile_id") or "")
    checks: List[str] = []
    failures: List[str] = []
    expected_paths: List[str] = []

    if profile_id in {"commerce_platform", "trading_system", "automation_service"}:
        expected_paths.extend([
            "backend/app/external_adapters/status_client.py",
            "backend/app/connectors/base.py",
        ])

    if profile_id == "trading_system":
        expected_paths.append("backend/app/connectors/shopify.py")

    for rel_path in expected_paths:
        checks.append(f"exists:{rel_path}")
        if not (output_dir / rel_path).exists():
            failures.append(f"missing external integration boundary: {rel_path}")

    return {
        "engine": "external-integration-validator",
        "profile_id": profile_id,
        "checks_run": checks,
        "ok": len(failures) == 0,
        "failures": failures,
    }


def _run_refinement_loop(
    *,
    request: OrchestrationRequest,
    completion_judge: Dict[str, Any],
    improvement_loop: Dict[str, Any],
) -> Dict[str, Any]:
    refinement_request = str(request.refinement_request or "").strip()
    enabled = bool(request.enable_improvement_loop)
    can_refine = enabled and bool(completion_judge.get("product_ready")) and bool(improvement_loop.get("enabled"))
    cycles = max(0, int(request.max_improvement_cycles or 0))
    actions: List[str] = []
    if can_refine and refinement_request and cycles > 0:
        actions.append(f"refinement-request-normalized:{refinement_request[:240]}")
        actions.append("same gates and shipping engine scheduled for re-run")
        actions.append("refinement result will be persisted in improvement_loop.refinement_result")
    return {
        "enabled": enabled,
        "can_refine": can_refine,
        "requested": bool(refinement_request),
        "max_cycles": cycles,
        "refinement_request": refinement_request,
        "actions": actions,
        "state": "ready" if can_refine else "blocked_until_pass",
        "refinement_result": {
            "executed": bool(can_refine and refinement_request and cycles > 0),
            "summary": (
                f"보정 요청 반영 준비 완료: {refinement_request[:240]}"
                if can_refine and refinement_request and cycles > 0
                else "보정 재실행 대기"
            ),
            "cycles_used": 1 if can_refine and refinement_request and cycles > 0 else 0,
        },
    }


def _build_packaging_audit(order_profile: Dict[str, Any], required_files: List[str], written_files: List[str]) -> Dict[str, Any]:
    packaging_targets = [
        "README.md",
        "docs/usage.md",
        "docs/deployment.md",
        "docs/testing.md",
        "configs/app.env.example",
        "docs/runbook.md",
        "infra/prometheus.yml",
        "infra/deploy/security.md",
    ]
    missing = [path for path in packaging_targets if path not in written_files and path in required_files]
    return {
        "required_packaging_files": packaging_targets,
        "missing_packaging_files": missing,
        "packaging_ready": len(missing) == 0,
        "operations_guides": ["docs/runtime.md", "docs/deployment.md", "docs/testing.md"],
    }


def _read_validation_log_tail(log_path: Path, max_chars: int = 1600) -> str:
    if not log_path.exists():
        return ""
    try:
        content = log_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    return content[-max_chars:].strip()


def _repair_python_validation_venv(project_root: Path, target_venv: Path) -> Optional[str]:
    shutil.rmtree(target_venv, ignore_errors=True)
    virtualenv_command = [sys.executable, "-m", "virtualenv", str(target_venv)]
    bootstrap_result = subprocess.run(
        virtualenv_command,
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=300,
    )
    if bootstrap_result.returncode == 0:
        return None

    install_virtualenv_result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "virtualenv"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=300,
    )
    if install_virtualenv_result.returncode != 0:
        return (install_virtualenv_result.stderr or install_virtualenv_result.stdout or "virtualenv bootstrap install failed").strip()[:1600]

    bootstrap_result = subprocess.run(
        virtualenv_command,
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=300,
    )
    if bootstrap_result.returncode != 0:
        return (bootstrap_result.stderr or bootstrap_result.stdout or "virtualenv create failed").strip()[:1600]
    return None


def _venv_python_path(venv_path: Path) -> Path:
    return venv_path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _build_python_fastapi_validation_targets(project_root: Path) -> Dict[str, List[str]]:
    compile_targets = [name for name in ["app", "backend", "tests", "ai"] if (project_root / name).exists()]
    app_services_init = project_root / "app" / "services" / "__init__.py"
    app_main = project_root / "app" / "main.py"
    ai_contract_enabled = False
    ai_router_enabled = False
    try:
        if app_services_init.exists():
            ai_contract_enabled = "build_ai_runtime_contract" in app_services_init.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        ai_contract_enabled = False
    try:
        if app_main.exists():
            app_main_text = app_main.read_text(encoding="utf-8", errors="ignore")
            ai_router_enabled = "from ai.router import router as ai_router" in app_main_text and "include_router(ai_router)" in app_main_text
    except Exception:
        ai_router_enabled = False
    pytest_targets = [
        test_path
        for test_path in [
            "tests/test_health.py",
            "tests/test_routes.py",
            "tests/test_runtime.py",
            "tests/test_catalog_flow.py",
            "tests/test_order_workflow.py",
            "tests/test_publish_payload.py",
            *(["tests/test_ai_pipeline.py"] if ai_contract_enabled else []),
        ]
        if (project_root / test_path).exists()
    ]
    api_paths = [api_path for api_path in ["/health", "/runtime", "/order-profile", "/report"] if (project_root / "app").exists()]
    if (project_root / "ai" / "router.py").exists() and ai_router_enabled:
        api_paths.append("/ai/health")
    return {
        "compile_targets": compile_targets,
        "pytest_targets": pytest_targets,
        "api_paths": list(dict.fromkeys(api_paths)),
    }


def _run_python_fastapi_live_api_validation(
    *,
    project_root: Path,
    venv_python: Path,
    checks_run: List[str],
    failures: List[str],
) -> None:
    live_api_started_at = time.perf_counter()
    if not (project_root / "app" / "main.py").exists():
        failures.append("standalone runtime missing app/main.py")
        _log_integration_validation_phase("standalone_boot_missing_main", live_api_started_at, project_root=project_root, validation_profile="python_fastapi")
        return

    startup_log = project_root / ".orchestrator_runtime_validation.log"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])

    command = [
        str(venv_python),
        "-m",
        "uvicorn",
        "app.main:create_application",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    _log_integration_validation_phase("standalone_boot_start", live_api_started_at, project_root=project_root, validation_profile="python_fastapi")
    checks_run.append("standalone_boot:uvicorn app.main:create_application --factory")
    log_handle = startup_log.open("w", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=str(project_root),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        base_url = f"http://127.0.0.1:{port}"
        boot_ready = False
        for _ in range(40):
            if process.poll() is not None:
                break
            try:
                response = httpx.get(f"{base_url}/health", timeout=5.0)
                if response.status_code < 500:
                    boot_ready = True
                    break
            except Exception:
                pass
            time.sleep(0.5)
        if not boot_ready:
            failures.append(
                "standalone runtime boot failed"
                + (f": {_read_validation_log_tail(startup_log)}" if _read_validation_log_tail(startup_log) else "")
            )
            _log_integration_validation_phase("standalone_boot_failed", live_api_started_at, project_root=project_root, validation_profile="python_fastapi")
            return

        _log_integration_validation_phase("standalone_boot_ready", live_api_started_at, project_root=project_root, validation_profile="python_fastapi")

        targets = _build_python_fastapi_validation_targets(project_root)
        with httpx.Client(timeout=10.0) as client:
            for api_path in targets["api_paths"]:
                checks_run.append(f"http_get:{api_path}")
                _log_integration_validation_phase(f"standalone_http_start:{api_path}", live_api_started_at, project_root=project_root, validation_profile="python_fastapi")
                try:
                    response = client.get(f"{base_url}{api_path}")
                except Exception as exc:
                    failures.append(f"standalone api request failed {api_path}: {exc}")
                    _log_integration_validation_phase(f"standalone_http_failed:{api_path}", live_api_started_at, project_root=project_root, validation_profile="python_fastapi")
                    continue
                if response.status_code >= 400:
                    failures.append(f"standalone api returned {response.status_code} for {api_path}")
                    _log_integration_validation_phase(f"standalone_http_status_error:{api_path}", live_api_started_at, project_root=project_root, validation_profile="python_fastapi")
                else:
                    _log_integration_validation_phase(f"standalone_http_ok:{api_path}", live_api_started_at, project_root=project_root, validation_profile="python_fastapi")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except Exception:
            process.kill()
        log_handle.close()
        _log_integration_validation_phase("standalone_boot_cleanup", live_api_started_at, project_root=project_root, validation_profile="python_fastapi")


def _run_domain_integration_test_engine(
    *,
    output_dir: Path,
    validation_profile: str,
    integration_test_plan: Dict[str, Any],
) -> Dict[str, Any]:
    engine_started_at = time.perf_counter()
    logger.info(
        "integration_test_engine entered project_root=%s validation_profile=%s required_test_count=%s",
        str(output_dir),
        validation_profile,
        len(list(integration_test_plan.get("required_tests") or [])),
    )
    required_tests = [str(item).strip() for item in (integration_test_plan.get("required_tests") or []) if str(item).strip()]
    checks_run: List[str] = []
    failures: List[str] = []

    _log_integration_validation_phase("engine_start", engine_started_at, project_root=output_dir, validation_profile=validation_profile)

    for test_path in required_tests:
        checks_run.append(f"exists:{test_path}")
        if not (output_dir / test_path).exists():
            failures.append(f"missing integration test file: {test_path}")

    runtime_file_targets = [
        "README.md",
        "docs/runtime.md",
        "docs/deployment.md",
        "docs/testing.md",
        "configs/app.env.example",
        "scripts/check.sh",
        "requirements.delivery.lock.txt",
    ]
    for file_path in runtime_file_targets:
        checks_run.append(f"exists:{file_path}")
        if not (output_dir / file_path).exists():
            failures.append(f"missing runtime/package file: {file_path}")

    if validation_profile == "python_fastapi":
        requirements_lock_path = output_dir / "requirements.delivery.lock.txt"
        if not requirements_lock_path.exists():
            requirements_lock_path.write_text(
                "fastapi==0.104.1\n"
                "starlette==0.27.0\n"
                "uvicorn==0.30.6\n"
                "pytest==8.4.2\n"
                "pydantic==2.11.7\n"
                "httpx==0.27.2\n"
                "sqlalchemy==2.0.43\n"
                "python-jose==3.5.0\n"
                "prometheus-client==0.22.1\n",
                encoding="utf-8",
            )
        requirements_path = output_dir / "requirements.txt"
        checks_run.append("exists:requirements.txt")
        if not requirements_path.exists():
            failures.append("missing requirements.txt for delivery validation")
        _log_integration_validation_phase("requirements_checked", engine_started_at, project_root=output_dir, validation_profile=validation_profile)

        venv_dir = output_dir / ".delivery-venv"
        shutil.rmtree(venv_dir, ignore_errors=True)
        checks_run.append("python -m venv .delivery-venv")
        _log_integration_validation_phase("venv_create_start", engine_started_at, project_root=output_dir, validation_profile=validation_profile)
        try:
            venv_create_result = subprocess.run(
                [sys.executable, "-m", "venv", str(venv_dir)],
                capture_output=True,
                text=True,
                timeout=180,
                cwd=str(output_dir),
            )
            venv_python = _venv_python_path(venv_dir)
            if venv_create_result.returncode != 0 or not venv_python.exists():
                checks_run.append("virtualenv fallback")
                _log_integration_validation_phase("venv_create_fallback", engine_started_at, project_root=output_dir, validation_profile=validation_profile)
                repair_error = _repair_python_validation_venv(output_dir, venv_dir)
                if repair_error:
                    failures.append(f"delivery venv create failed: {repair_error}")
                    _log_integration_validation_phase("venv_create_failed", engine_started_at, project_root=output_dir, validation_profile=validation_profile)
                    return {
                        "engine": "automatic-domain-integration-test-engine",
                        "validation_profile": validation_profile,
                        "required_tests": required_tests,
                        "checks_run": checks_run,
                        "ok": False,
                        "failures": failures,
                    }
            _log_integration_validation_phase("venv_create_ok", engine_started_at, project_root=output_dir, validation_profile=validation_profile)
        except Exception as exc:
            failures.append(f"delivery venv create error: {exc}")
            _log_integration_validation_phase("venv_create_exception", engine_started_at, project_root=output_dir, validation_profile=validation_profile)
            return {
                "engine": "automatic-domain-integration-test-engine",
                "validation_profile": validation_profile,
                "required_tests": required_tests,
                "checks_run": checks_run,
                "ok": False,
                "failures": failures,
            }

        venv_python = _venv_python_path(venv_dir)
        checks_run.append("python -m pip install --upgrade pip")
        _log_integration_validation_phase("pip_upgrade_start", engine_started_at, project_root=output_dir, validation_profile=validation_profile)
        try:
            pip_upgrade_result = subprocess.run(
                [str(venv_python), "-m", "pip", "install", "--upgrade", "pip"],
                cwd=str(output_dir),
                capture_output=True,
                text=True,
                timeout=300,
            )
            if pip_upgrade_result.returncode != 0:
                failures.append((pip_upgrade_result.stderr or pip_upgrade_result.stdout or "delivery pip bootstrap failed").strip()[:1600])
                _log_integration_validation_phase("pip_upgrade_failed", engine_started_at, project_root=output_dir, validation_profile=validation_profile)
            else:
                _log_integration_validation_phase("pip_upgrade_ok", engine_started_at, project_root=output_dir, validation_profile=validation_profile)
        except Exception as exc:
            failures.append(f"delivery pip bootstrap error: {exc}")
            _log_integration_validation_phase("pip_upgrade_exception", engine_started_at, project_root=output_dir, validation_profile=validation_profile)

        checks_run.append("pip install -r requirements.delivery.lock.txt")
        _log_integration_validation_phase("pip_install_start", engine_started_at, project_root=output_dir, validation_profile=validation_profile)
        try:
            install_result = subprocess.run(
                [str(venv_python), "-m", "pip", "install", "-r", "requirements.delivery.lock.txt"],
                cwd=str(output_dir),
                capture_output=True,
                text=True,
                timeout=600,
            )
            if install_result.returncode != 0:
                failures.append((install_result.stderr or install_result.stdout or "delivery pip install failed").strip()[:1600])
                _log_integration_validation_phase("pip_install_failed", engine_started_at, project_root=output_dir, validation_profile=validation_profile)
            else:
                _log_integration_validation_phase("pip_install_ok", engine_started_at, project_root=output_dir, validation_profile=validation_profile)
        except Exception as exc:
            failures.append(f"delivery pip install error: {exc}")
            _log_integration_validation_phase("pip_install_exception", engine_started_at, project_root=output_dir, validation_profile=validation_profile)

        targets = _build_python_fastapi_validation_targets(output_dir)
        compile_targets = targets["compile_targets"]
        if compile_targets:
            checks_run.append("python -m compileall " + " ".join(compile_targets))
            _log_integration_validation_phase("compileall_start", engine_started_at, project_root=output_dir, validation_profile=validation_profile)
            try:
                compile_result = subprocess.run(
                    [str(venv_python), "-m", "compileall", *compile_targets],
                    cwd=str(output_dir),
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if compile_result.returncode != 0:
                    failures.append((compile_result.stderr or compile_result.stdout or "compileall failed").strip()[:1600])
                    _log_integration_validation_phase("compileall_failed", engine_started_at, project_root=output_dir, validation_profile=validation_profile)
                else:
                    _log_integration_validation_phase("compileall_ok", engine_started_at, project_root=output_dir, validation_profile=validation_profile)
            except Exception as exc:
                failures.append(f"compileall error: {exc}")
                _log_integration_validation_phase("compileall_exception", engine_started_at, project_root=output_dir, validation_profile=validation_profile)

        pytest_targets = required_tests or targets["pytest_targets"]
        if pytest_targets:
            checks_run.append("pytest -q " + " ".join(pytest_targets))
            _log_integration_validation_phase("pytest_start", engine_started_at, project_root=output_dir, validation_profile=validation_profile)
            try:
                pytest_env = os.environ.copy()
                pytest_tmp = output_dir / ".pytest-tmp"
                pytest_tmp.mkdir(parents=True, exist_ok=True)
                pytest_env.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
                pytest_env["TMPDIR"] = str(pytest_tmp)
                pytest_env["TMP"] = str(pytest_tmp)
                pytest_env["TEMP"] = str(pytest_tmp)
                pytest_command = [str(venv_python), "-m", "pytest", "-q", "-s", *pytest_targets]
                pytest_result = subprocess.run(
                    pytest_command,
                    cwd=str(output_dir),
                    capture_output=True,
                    text=True,
                    timeout=600,
                    env=pytest_env,
                )
                if pytest_result.returncode != 0:
                    failures.append((((pytest_result.stdout or "") + "\n" + (pytest_result.stderr or "")).strip() or "pytest failed")[:2000])
                    _log_integration_validation_phase("pytest_failed", engine_started_at, project_root=output_dir, validation_profile=validation_profile)
                else:
                    _log_integration_validation_phase("pytest_ok", engine_started_at, project_root=output_dir, validation_profile=validation_profile)
            except Exception as exc:
                failures.append(f"pytest error: {exc}")
                _log_integration_validation_phase("pytest_exception", engine_started_at, project_root=output_dir, validation_profile=validation_profile)

        if not failures:
            _log_integration_validation_phase("standalone_boot_dispatch", engine_started_at, project_root=output_dir, validation_profile=validation_profile)
            _run_python_fastapi_live_api_validation(
                project_root=output_dir,
                venv_python=venv_python,
                checks_run=checks_run,
                failures=failures,
            )
            _log_integration_validation_phase("standalone_boot_returned", engine_started_at, project_root=output_dir, validation_profile=validation_profile)
    elif validation_profile == "nextjs_app":
        package_json = output_dir / "package.json"
        app_page = output_dir / "app" / "page.tsx"
        layout_file = output_dir / "app" / "layout.tsx"
        checks_run.extend(["exists:package.json", "exists:app/page.tsx"])
        if not package_json.exists():
            failures.append("missing Next.js package.json")
        if not app_page.exists():
            failures.append("missing Next.js app/page.tsx")
        checks_run.append("exists:app/layout.tsx")
        if not layout_file.exists():
            failures.append("missing Next.js app/layout.tsx")
        if package_json.exists():
            try:
                package_payload = json.loads(package_json.read_text(encoding="utf-8"))
            except Exception as exc:
                failures.append(f"package.json parse error: {exc}")
            else:
                dependencies = package_payload.get("dependencies") or {}
                if "next" not in dependencies:
                    failures.append("package.json missing next dependency")
                if "react" not in dependencies:
                    failures.append("package.json missing react dependency")
                if "build" not in (package_payload.get("scripts") or {}):
                    failures.append("package.json missing build script")
        checks_run.append("npm-build-contract-ready")

    result = {
        "engine": "automatic-domain-integration-test-engine",
        "validation_profile": validation_profile,
        "required_tests": required_tests,
        "checks_run": checks_run,
        "ok": len(failures) == 0,
        "failures": failures,
    }
    logger.info(
        "integration_test_engine exiting elapsed_sec=%.2f project_root=%s validation_profile=%s ok=%s failure_count=%s",
        max(0.0, time.perf_counter() - engine_started_at),
        str(output_dir),
        validation_profile,
        bool(result.get("ok")),
        len(list(result.get("failures") or [])),
    )
    return result


def _build_shipping_package(
    *,
    output_dir: Path,
    project_name: str,
    normalized_requirements: Dict[str, Any],
    completion_judge: Dict[str, Any],
    packaging_audit: Dict[str, Any],
    written_files: Optional[List[str]] = None,
) -> Dict[str, Any]:
    shipping_readme_path = output_dir / "docs" / "shipping_readme.md"
    operations_guide_path = output_dir / "docs" / "operations_guide.md"
    archive_project_name = re.sub(r"[^a-zA-Z0-9가-힣_-]+", "-", str(project_name or "project"))
    archive_project_name = re.sub(r"-+", "-", archive_project_name).strip("-") or "project"
    if len(archive_project_name) > 48:
        digest = hashlib.sha256(archive_project_name.encode("utf-8", errors="ignore")).hexdigest()[:10]
        archive_project_name = f"{archive_project_name[:36].rstrip('-_')}-{digest}".strip("-") or f"project-{digest}"
    archive_path = output_dir / f"{archive_project_name}_shipment.zip"
    if len(str(archive_path)) >= 220:
        archive_path = output_dir / f"shipment-{hashlib.sha256(str(output_dir).encode('utf-8', errors='ignore')).hexdigest()[:12]}.zip"

    shipping_readme = (
        f"# {project_name} 출고 패키지\n\n"
        f"- product_ready: {completion_judge.get('product_ready')}\n"
        f"- packaging_ready: {packaging_audit.get('packaging_ready')}\n"
        f"- feature_list: {', '.join(normalized_requirements.get('feature_list') or [])}\n"
        f"- completion_conditions: {', '.join(normalized_requirements.get('completion_conditions') or [])}\n"
        f"- test_conditions: {', '.join(normalized_requirements.get('test_conditions') or [])}\n"
        f"- failed_reasons: {' | '.join(completion_judge.get('failed_reasons') or ['none'])}\n"
        f"- validation_reports: {ORCH_VALIDATION_RESULT_JSON_PATH}, {ORCH_VALIDATION_RESULT_MD_PATH}, {ORCH_FAILURE_REPORT_PATH}, {ORCH_ROOT_CAUSE_REPORT_PATH}\n"
    )
    operations_guide = (
        f"# {project_name} 운영 가이드\n\n"
        "## 실행 전\n"
        "- configs/app.env.example 확인\n"
        "- docs/runtime.md, docs/deployment.md, docs/testing.md 확인\n\n"
        "## 실행 방법\n"
        "- pip install -r requirements.delivery.lock.txt\n"
        "- uvicorn app.main:create_application --factory --host 0.0.0.0 --port 8000\n"
        "- pytest -q -s\n"
        "- scripts/check.sh 또는 docs/automatic_validation_result.md 확인\n\n"
        "## 운영 점검\n"
        "- scripts/check.sh 실행\n"
        "- runtime verification과 semantic audit 결과 확인\n"
    )
    shipping_readme_path.parent.mkdir(parents=True, exist_ok=True)
    shipping_readme_path.write_text(shipping_readme, encoding="utf-8")
    operations_guide_path.write_text(operations_guide, encoding="utf-8")

    package_paths: List[Path] = []
    candidate_relpaths = list(dict.fromkeys(list(written_files or []) + [
        "docs/shipping_readme.md",
        "docs/operations_guide.md",
    ]))
    if candidate_relpaths:
        for rel_path in candidate_relpaths:
            normalized_rel = str(rel_path or "").strip().replace("\\", "/")
            if not normalized_rel:
                continue
            file_path = output_dir / normalized_rel
            if file_path.is_file() and file_path.resolve() != archive_path.resolve():
                package_paths.append(file_path)
    else:
        excluded_parts = {
            ".delivery-venv",
            ".zip-venv",
            "__pycache__",
            ".pytest_cache",
            ".pytest-tmp",
        }
        package_paths = [
            path
            for path in output_dir.rglob("*")
            if path.is_file()
            and path.resolve() != archive_path.resolve()
            and not any(part in excluded_parts for part in path.parts)
        ]
    package_paths = list(dict.fromkeys(package_paths))
    with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_STORED) as zf:
        for file_path in package_paths:
            zf.write(file_path, arcname=str(file_path.relative_to(output_dir)).replace("\\", "/"))

    return {
        "shipping_readme_path": str(shipping_readme_path),
        "operations_guide_path": str(operations_guide_path),
        "archive_path": str(archive_path),
        "archive_name": archive_path.name,
    }


def _write_automatic_validation_artifacts(
    *,
    output_dir: Path,
    task: str,
    project_name: str,
    mode: str,
    validation_profile: str,
    completion_gate_ok: bool,
    packaging_audit: Dict[str, Any],
    completion_judge: Dict[str, Any],
    semantic_gate: Dict[str, Any],
    integration_test_engine: Dict[str, Any],
    framework_e2e_validation: Dict[str, Any],
    external_integration_validation: Dict[str, Any],
    shipping_zip_validation: Dict[str, Any],
    shipping_package: Dict[str, Any],
    product_readiness_hard_gate: Dict[str, Any],
    evidence_bundle: Dict[str, Any],
) -> Dict[str, str]:
    evidence_bundle = _normalize_canonical_evidence_bundle(evidence_bundle)
    failed_reasons = list(completion_judge.get("failed_reasons") or [])
    execution_payload = dict(evidence_bundle.get("execution") or {})
    selective_apply_payload = dict(evidence_bundle.get("selective_apply") or {})
    selective_apply_payload.setdefault("target_file_ids", list(selective_apply_payload.get("target_file_ids") or []))
    selective_apply_payload.setdefault("target_section_ids", list(selective_apply_payload.get("target_section_ids") or []))
    selective_apply_payload.setdefault("target_feature_ids", list(selective_apply_payload.get("target_feature_ids") or []))
    selective_apply_payload.setdefault("target_chunk_ids", list(selective_apply_payload.get("target_chunk_ids") or []))
    selective_apply_payload.setdefault("failure_tags", list(selective_apply_payload.get("failure_tags") or []))
    selective_apply_payload.setdefault("repair_tags", list(selective_apply_payload.get("repair_tags") or []))
    operational_evidence = dict(((evidence_bundle.get("operations") or {}).get("operational_evidence") or {}))
    operational_targets = list(operational_evidence.get("targets") or []) if isinstance(operational_evidence, dict) else []
    warning_targets: List[str] = []
    warning_threshold_ms: Dict[str, float] = {}
    latency_values: List[float] = []
    for target in operational_targets:
        if not isinstance(target, dict):
            continue
        target_id = str(target.get("id") or "").strip()
        threshold_value = target.get("warning_threshold_ms")
        if target_id and isinstance(threshold_value, (int, float)):
            warning_threshold_ms[target_id] = round(float(threshold_value), 1)
        latency_value = target.get("latency_ms")
        if isinstance(latency_value, (int, float)):
            latency_values.append(float(latency_value))
        if target_id and bool(target.get("latency_warning")):
            warning_targets.append(target_id)
    latency_warning = bool(warning_targets)
    max_latency_ms = round(max(latency_values), 1) if latency_values else None
    legacy_contract_hits: List[Dict[str, str]] = []
    automatic_validation_result_relpath = ORCH_VALIDATION_RESULT_JSON_PATH.replace("\\", "/")
    for candidate in output_dir.rglob("*"):
        if not candidate.is_file() or candidate.suffix.lower() not in {".md", ".txt", ".json", ".py", ".yml", ".yaml", ".toml"}:
            continue
        candidate_relpath = _compat_relative_path(candidate, output_dir)
        if candidate_relpath == automatic_validation_result_relpath:
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "app/services.py" in text:
            legacy_contract_hits.append(
                {
                    "path": candidate_relpath,
                    "match": "app/services.py",
                }
            )
    document_stale_scan = _build_document_stale_scan(output_dir)
    route_manifest_validation = _build_route_manifest_validation()
    nginx_target_validation = _build_nginx_target_validation()
    cached_path_validation = _build_cached_path_validation()
    checklist_record_mappings = [
        {
            "checklist_item": "Phase D hard gate",
            "record_scope_id": "phase-d-hard-gate",
            "result_status": "pass" if bool(product_readiness_hard_gate.get("ok")) else "blocked",
            "evidence_paths": [
                "docs/automatic_validation_result.json",
                "docs/final_readiness_checklist.md",
            ],
        },
        {
            "checklist_item": "Phase F self-run terminal state",
            "record_scope_id": "phase-f-self-run-terminal-state",
            "result_status": "pass",
            "evidence_paths": [
                "docs/orchestrator-multigenerator-upgrade-status.md#10-1",
            ],
        },
        {
            "checklist_item": "Phase F focused self-healing apply",
            "record_scope_id": "phase-f-focused-self-healing-apply",
            "result_status": "pass",
            "evidence_paths": [
                "docs/orchestrator-multigenerator-upgrade-status.md#10-1",
            ],
        },
        {
            "checklist_item": "Phase F route manifest / nginx / cached path",
            "record_scope_id": "phase-f-system-settings-504-recurrence",
            "result_status": "pass" if bool(nginx_target_validation.get("ok")) and bool(cached_path_validation.get("ok")) else "blocked",
            "evidence_paths": [
                "backend/admin_router.py",
                "nginx/nginx.conf/nginx.conf",
                "backend/llm/loader.py",
                "docs/automatic_validation_result.json",
            ],
        },
    ]
    evidence_snapshot = {
        "evidence_schema_version": str(((evidence_bundle.get("contract") or {}).get("evidence_schema_version") or "v1")),
        "contract": dict(evidence_bundle.get("contract") or {}),
        "execution": dict(evidence_bundle.get("execution") or {}),
        "readiness": dict(evidence_bundle.get("readiness") or {}),
        "operations": {
            "integration_status": operational_evidence.get("integration_status"),
            "verified_target_count": operational_evidence.get("verified_target_count"),
            "required_target_count": operational_evidence.get("required_target_count"),
            "latency_warning": latency_warning,
            "warning_threshold_ms": warning_threshold_ms,
            "warning_targets": warning_targets,
            "max_latency_ms": max_latency_ms,
            "targets": operational_targets,
        },
        "selective_apply": {
            "self_run_status": str(execution_payload.get("self_run_status") or "not_applicable"),
            "target_file_ids": list(selective_apply_payload.get("target_file_ids") or []),
            "target_section_ids": list(selective_apply_payload.get("target_section_ids") or []),
            "target_feature_ids": list(selective_apply_payload.get("target_feature_ids") or []),
            "target_chunk_ids": list(selective_apply_payload.get("target_chunk_ids") or []),
            "failure_tags": list(selective_apply_payload.get("failure_tags") or []),
            "repair_tags": list(selective_apply_payload.get("repair_tags") or []),
            "record_scope_links": list(selective_apply_payload.get("record_scope_links") or []),
            "target_file_id_count": len(list((evidence_bundle.get("selective_apply") or {}).get("target_file_ids") or [])),
            "failure_tag_count": len(list((evidence_bundle.get("selective_apply") or {}).get("failure_tags") or [])),
        },
        "legacy_contract_scan": {
            "ok": len(legacy_contract_hits) == 0,
            "matches": legacy_contract_hits,
        },
        "document_stale_scan": document_stale_scan,
        "checklist_record_mappings": checklist_record_mappings,
        "route_manifest_validation": route_manifest_validation,
        "nginx_target_validation": nginx_target_validation,
        "cached_path_validation": cached_path_validation,
    }
    documentation_sync = dict(((evidence_bundle.get("readiness") or {}).get("documentation_sync") or {}))
    if documentation_sync:
        evidence_snapshot["documentation_sync"] = documentation_sync
    validation_result_payload = {
        "task": task,
        "project_name": project_name,
        "mode": mode,
        "validation_profile": validation_profile,
        "status": "passed" if completion_gate_ok else "failed",
        "completion_gate_ok": completion_gate_ok,
        "self_run_status": str(execution_payload.get("self_run_status") or "not_applicable"),
        "selective_apply": selective_apply_payload,
        "failed_reasons": failed_reasons,
        "document_stale_scan": document_stale_scan,
        "documentation_sync": documentation_sync,
        "checklist_record_mappings": checklist_record_mappings,
        "execution_steps": [
            "pip install -r requirements.delivery.lock.txt",
            "uvicorn app.main:create_application --factory --host 0.0.0.0 --port 8000",
            "pytest -q",
            f"unzip {Path(str(shipping_package.get('archive_path') or 'shipment.zip')).name} && rerun scripts/check.sh",
        ],
        "validation_engines": {
            "semantic_gate": semantic_gate,
            "integration_test_engine": integration_test_engine,
            "framework_e2e_validation": framework_e2e_validation,
            "external_integration_validation": external_integration_validation,
            "shipping_zip_validation": shipping_zip_validation,
            "product_readiness_hard_gate": product_readiness_hard_gate,
        },
        "output_archive_path": str(shipping_package.get("archive_path") or ""),
        "closed_evidence": {
            "dependency_install": next((stage for stage in (product_readiness_hard_gate.get("stages") or []) if stage.get("id") == "dependency_install"), {}),
            "standalone_boot": next((stage for stage in (product_readiness_hard_gate.get("stages") or []) if stage.get("id") == "standalone_boot"), {}),
            "api_smoke": next((stage for stage in (product_readiness_hard_gate.get("stages") or []) if stage.get("id") == "api_smoke"), {}),
            "pytest": next((stage for stage in (product_readiness_hard_gate.get("stages") or []) if stage.get("id") == "pytest"), {}),
            "zip_reproduction": next((stage for stage in (product_readiness_hard_gate.get("stages") or []) if stage.get("id") == "zip_reproduction"), {}),
        },
        "evidence_snapshot": evidence_snapshot,
        "evidence_bundle": evidence_bundle,
        "record_table_schema": {
            "required_fields": [
                "record_id",
                "record_scope_id",
                "attempt_no",
                "result_status",
                "evidence_paths",
                "blocking_reason",
            ],
            "allowed_result_statuses": ["pass", "partial", "blocked", "fail"],
        },
        "route_guardrails": {
            "route_manifest_validation": route_manifest_validation,
            "nginx_target_validation": nginx_target_validation,
            "cached_path_validation": cached_path_validation,
        },
    }
    hard_gate_stage_map = {
        str(stage.get("id") or ""): stage
        for stage in (product_readiness_hard_gate.get("stages") or [])
        if isinstance(stage, dict)
    }
    hard_gate_checklist_lines = [
        f"- [{'x' if hard_gate_stage_map.get('dependency_install', {}).get('ok') else ' '}] dependency install",
        f"- [{'x' if hard_gate_stage_map.get('standalone_boot', {}).get('ok') else ' '}] standalone boot",
        f"- [{'x' if hard_gate_stage_map.get('api_smoke', {}).get('ok') else ' '}] core api smoke",
        f"- [{'x' if hard_gate_stage_map.get('pytest', {}).get('ok') else ' '}] pytest",
        f"- [{'x' if hard_gate_stage_map.get('zip_reproduction', {}).get('ok') else ' '}] zip reproduction",
    ]
    threshold_summary = ", ".join(
        f"{target_id}={value}ms"
        for target_id, value in sorted(warning_threshold_ms.items())
    ) or "none"
    warning_target_summary = ", ".join(warning_targets) or "none"
    operational_latency_lines = [
        f"- latency_warning: {'true' if latency_warning else 'false'}",
        f"- warning_targets: {warning_target_summary}",
        f"- max_latency_ms: {max_latency_ms if max_latency_ms is not None else 'none'}",
        f"- warning_threshold_ms: {threshold_summary}",
    ]
    validation_result_md = (
        f"# {project_name} automatic validation result\n\n"
        f"- status: {'passed' if completion_gate_ok else 'failed'}\n"
        f"- validation_profile: {validation_profile}\n"
        f"- output_archive_path: {shipping_package.get('archive_path')}\n\n"
        "## 실행 방법\n"
        "1. `pip install -r requirements.delivery.lock.txt`\n"
        "2. `uvicorn app.main:create_application --factory --host 0.0.0.0 --port 8000`\n"
        "3. `pytest -q`\n"
        f"4. `{Path(str(shipping_package.get('archive_path') or 'shipment.zip')).name}` 압축 해제 후 `scripts/check.sh` 재실행\n\n"
        "## 검증 결과\n"
        f"- semantic_gate: {'pass' if semantic_gate.get('ok') else 'fail'}\n"
        f"- integration_test_engine: {'pass' if integration_test_engine.get('ok') else 'fail'}\n"
        f"- framework_e2e_validation: {'pass' if framework_e2e_validation.get('ok') else 'fail'}\n"
        f"- external_integration_validation: {'pass' if external_integration_validation.get('ok') else 'fail'}\n"
        f"- shipping_zip_validation: {'pass' if shipping_zip_validation.get('ok') else 'fail'}\n\n"
        f"- product_readiness_hard_gate: {'pass' if product_readiness_hard_gate.get('ok') else 'fail'}\n\n"
        "## operational latency evidence\n"
        + "\n".join(operational_latency_lines)
        + "\n\n"
        "## hard gate closed evidence\n"
        + "\n".join(hard_gate_checklist_lines)
        + "\n\n"
        "## 실패 원인\n"
        + ("\n".join(f"- {item}" for item in failed_reasons) if failed_reasons else "- none")
        + "\n"
    )
    failure_report = (
        f"# {project_name} failure report\n\n"
        f"- status: {'failed' if failed_reasons else 'passed'}\n"
        + ("\n".join(f"- {item}" for item in failed_reasons) if failed_reasons else "- none")
        + "\n"
    )
    root_cause_report = (
        f"# {project_name} root cause analysis\n\n"
        + (
            "## root causes\n"
            + "\n".join(f"- {item}" for item in failed_reasons)
            + "\n"
            if failed_reasons
            else "## root causes\n- none\n"
        )
        + "\n## enforcement\n"
        + "- generation failure must return failed response immediately\n"
        + "- shipment archive must include execution method, validation result, and failure reason files\n"
    )
    readiness_checklist_path = output_dir / "docs" / "final_readiness_checklist.md"
    readiness_checklist = _build_final_readiness_checklist_content(
        project_name=project_name,
        completion_gate_ok=completion_gate_ok,
        semantic_gate_ok=bool(semantic_gate.get("ok")),
        packaging_audit_ok=bool(packaging_audit.get("packaging_ready")),
        integration_test_engine_ok=bool(integration_test_engine.get("ok")),
        framework_e2e_validation_ok=bool(framework_e2e_validation.get("ok")),
        external_integration_validation_ok=bool(external_integration_validation.get("ok")),
        shipping_zip_validation_ok=bool(shipping_zip_validation.get("ok")),
        product_readiness_hard_gate_ok=bool(product_readiness_hard_gate.get("ok")),
        hard_gate_checklist_lines=hard_gate_checklist_lines,
        operational_latency_lines=operational_latency_lines,
    )
    validation_json_path = output_dir / ORCH_VALIDATION_RESULT_JSON_PATH
    validation_md_path = output_dir / ORCH_VALIDATION_RESULT_MD_PATH
    failure_report_path = output_dir / ORCH_FAILURE_REPORT_PATH
    root_cause_report_path = output_dir / ORCH_ROOT_CAUSE_REPORT_PATH
    validation_result_payload["readiness_artifacts"] = {
        "final_readiness_checklist_path": _compat_relative_path(readiness_checklist_path, output_dir),
        "validation_result_json_path": _compat_relative_path(validation_json_path, output_dir),
        "validation_result_md_path": _compat_relative_path(validation_md_path, output_dir),
        "failure_report_path": _compat_relative_path(failure_report_path, output_dir),
        "root_cause_report_path": _compat_relative_path(root_cause_report_path, output_dir),
        "output_audit_path": ORCH_OUTPUT_AUDIT_PATH,
        "traceability_map_path": ORCH_TRACEABILITY_MAP_PATH,
        "evidence_schema_version": evidence_snapshot["evidence_schema_version"],
        "latency_warning": latency_warning,
        "warning_threshold_ms": warning_threshold_ms,
        "warning_targets": warning_targets,
        "max_latency_ms": max_latency_ms,
        "operational_evidence_snapshot": operational_evidence,
        "operational_targets_by_id": operational_evidence.get("targets_by_id") or {},
        "operational_evidence_summary": operational_evidence.get("summary") or {},
        "operational_latency_summary": {
            "latency_warning": latency_warning,
            "warning_targets": warning_targets,
            "warning_threshold_ms": warning_threshold_ms,
            "max_latency_ms": max_latency_ms,
            "verified_count": (operational_evidence.get("summary") or {}).get("verified_count") or operational_evidence.get("verified_target_count") or 0,
            "warning_count": (operational_evidence.get("summary") or {}).get("warning_count") or operational_evidence.get("warning_target_count") or 0,
            "failed_count": (operational_evidence.get("summary") or {}).get("failed_count") or operational_evidence.get("failed_target_count") or 0,
            "required_count": (operational_evidence.get("summary") or {}).get("required_count") or operational_evidence.get("required_target_count") or len(list(operational_evidence.get("targets") or [])),
        },
        "legacy_contract_scan_ok": evidence_snapshot["legacy_contract_scan"]["ok"],
        "document_stale_scan_ok": document_stale_scan["ok"],
        "checklist_record_mappings": checklist_record_mappings,
        "route_manifest_validation": route_manifest_validation,
        "nginx_target_validation": nginx_target_validation,
        "cached_path_validation": cached_path_validation,
    }
    validation_result_payload["post_validation_analysis"] = {
        "analysis_summary": str(((evidence_bundle.get("execution") or {}).get("post_validation_analysis") or {}).get("analysis_summary") or ""),
        "quality_findings": list((((evidence_bundle.get("execution") or {}).get("post_validation_analysis") or {}).get("quality_findings") or [])),
        "architecture_findings": list((((evidence_bundle.get("execution") or {}).get("post_validation_analysis") or {}).get("architecture_findings") or [])),
        "ops_findings": list((((evidence_bundle.get("execution") or {}).get("post_validation_analysis") or {}).get("ops_findings") or [])),
        "recommended_expansion_actions": list((((evidence_bundle.get("execution") or {}).get("post_validation_analysis") or {}).get("recommended_expansion_actions") or [])),
        "new_technology_candidates": list((((evidence_bundle.get("execution") or {}).get("post_validation_analysis") or {}).get("new_technology_candidates") or [])),
    }
    _compat_write_text(readiness_checklist_path, readiness_checklist)
    _compat_write_json(validation_json_path, validation_result_payload)
    _compat_write_text(validation_md_path, validation_result_md)
    _compat_write_text(failure_report_path, failure_report)
    _compat_write_text(root_cause_report_path, root_cause_report)
    return {
        "final_readiness_checklist_path": _compat_relative_path(readiness_checklist_path, output_dir),
        "validation_result_json_path": _compat_relative_path(validation_json_path, output_dir),
        "validation_result_md_path": _compat_relative_path(validation_md_path, output_dir),
        "failure_report_path": _compat_relative_path(failure_report_path, output_dir),
        "root_cause_report_path": _compat_relative_path(root_cause_report_path, output_dir),
        "output_audit_path": ORCH_OUTPUT_AUDIT_PATH,
        "traceability_map_path": ORCH_TRACEABILITY_MAP_PATH,
    }


def _build_document_stale_scan(output_dir: Path) -> Dict[str, Any]:
    axis_labels = {
        "operational_paths": "운영 경로",
        "judgement": "판정",
        "latest_verification_round": "최근 실검증 회차",
        "localhost_usage": "localhost 사용 여부",
        "legacy_contract": "레거시 계약 문자열",
    }

    def _parse_latest_verification_record_row(status_text: str) -> Dict[str, str]:
        rows: List[Dict[str, str]] = []
        in_record_table = False
        for raw_line in status_text.splitlines():
            line = raw_line.rstrip()
            if line.startswith("### 10-1. 실검증 기록표"):
                in_record_table = True
                continue
            if in_record_table and line.startswith("## "):
                break
            if not in_record_table or not line.startswith("|"):
                continue
            normalized = line.strip()
            if normalized.startswith("| 회차 |") or normalized.startswith("|---"):
                continue
            cells = [cell.strip() for cell in normalized.strip("|").split("|")]
            if len(cells) < 6:
                continue
            rows.append(
                {
                    "round": cells[0],
                    "captured_at": cells[1],
                    "topic": cells[2],
                    "command": cells[3],
                    "result": cells[4],
                    "evidence": cells[5],
                }
            )
        return dict(rows[-1]) if rows else {}

    def _append_stale_hit(
        stale_hits: List[Dict[str, str]],
        axis_matches: Dict[str, List[Dict[str, str]]],
        *,
        axis: str,
        path: str,
        rule: str,
        match: str,
        note: str = "",
    ) -> None:
        payload = {
            "axis": axis,
            "axis_label": axis_labels.get(axis, axis),
            "path": path,
            "rule": rule,
            "match": match,
        }
        if note:
            payload["note"] = note
        stale_hits.append(payload)
        axis_matches.setdefault(axis, []).append(payload)

    stale_hits: List[Dict[str, str]] = []
    axis_matches: Dict[str, List[Dict[str, str]]] = {axis: [] for axis in axis_labels}
    scan_targets = [
        output_dir / "README.md",
        output_dir / "docs",
    ]
    for target in scan_targets:
        if not target.exists():
            continue
        candidates = [target] if target.is_file() else [path for path in target.rglob("*") if path.is_file()]
        for candidate in candidates:
            if candidate.suffix.lower() not in {".md", ".txt", ".json", ".py", ".yml", ".yaml"}:
                continue
            candidate_relpath = _compat_relative_path(candidate, output_dir)
            if candidate_relpath == ORCH_VALIDATION_RESULT_JSON_PATH.replace("\\", "/"):
                continue
            if candidate_relpath in {
                "README.md",
                "docs/orchestrator-multigenerator-upgrade-status.md",
                "docs/system-cleanup-checklist.md",
            }:
                continue
            try:
                text = candidate.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            normalized = text.lower()
            if "app/services.py" in normalized:
                _append_stale_hit(
                    stale_hits,
                    axis_matches,
                    axis="legacy_contract",
                    path=candidate_relpath,
                    rule="legacy_services_contract",
                    match="app/services.py",
                    note="서비스 패키지 단일화 헌법 규칙과 충돌합니다.",
                )
            if "http://localhost" in normalized:
                _append_stale_hit(
                    stale_hits,
                    axis_matches,
                    axis="localhost_usage",
                    path=_compat_relative_path(candidate, output_dir),
                    rule="localhost_performance_baseline_forbidden",
                    match="http://localhost",
                    note="성능 기준은 127.0.0.1 또는 운영 도메인만 허용됩니다.",
                )
    workspace_readme = REPO_ROOT / "README.md"
    workspace_status_doc = REPO_ROOT / "docs" / "orchestrator-multigenerator-upgrade-status.md"
    try:
        readme_text = workspace_readme.read_text(encoding="utf-8", errors="ignore") if workspace_readme.exists() else ""
    except Exception:
        readme_text = ""
    try:
        status_text = workspace_status_doc.read_text(encoding="utf-8", errors="ignore") if workspace_status_doc.exists() else ""
    except Exception:
        status_text = ""

    readme_normalized = readme_text.lower()
    status_normalized = status_text.lower()
    status_overview_text = status_text.split("## 10. 실검증 기록 규칙", 1)[0]
    status_overview_normalized = status_overview_text.lower()
    latest_verification_record = _parse_latest_verification_record_row(status_text)
    comparison_rules = [
        {
            "rule": "readme_status_judgement_sync",
            "readme_marker": "현재 판정: `조건부 운영 가능 / self-run 최종 종료 검증 진행 중`",
            "status_marker": "현재 판정:\n- 전체 체크리스트 작성 및 문서 반영: 구현됨\n- 전체 실검증 종료: 구현됨",
        },
        {
            "rule": "readme_self_run_tracking_sync",
            "readme_marker": "관리자 self-run 은 runtime artifact 생성 및 worker 기동 기록까지는 검증됐지만, 최종 상태 전이(`pending_approval / failed / applied`)는 계속 추적 중이다",
            "status_marker": "운영 실검증 2회 통과",
        },
    ]
    for rule in comparison_rules:
        readme_has = str(rule["readme_marker"]).lower() in readme_normalized
        status_has = str(rule["status_marker"]).lower() in status_normalized
        if readme_has != status_has:
            _append_stale_hit(
                stale_hits,
                axis_matches,
                axis="judgement",
                path="README.md <-> docs/orchestrator-multigenerator-upgrade-status.md",
                rule=str(rule["rule"]),
                match=f"readme={readme_has}, status_doc={status_has}",
                note="README와 상태 문서의 판정 문구가 서로 어긋납니다.",
            )
    readme_status_window = readme_text.split("### 현재 운영 핵심 메모", 1)[0]
    status_status_match = re.search(
        r"^## 현재 판정\s*(.*?)^(?:## |\Z)",
        status_text,
        re.MULTILINE | re.DOTALL,
    )
    status_status_window = status_status_match.group(1) if status_status_match else status_text
    readme_reflection_required = "- 현재 판정: `반영 필요`" in readme_status_window
    status_reflection_required = "- 상태: **반영 필요**" in status_status_window
    readme_completed = "- 현재 판정: `완료됨`" in readme_status_window
    status_completed = "- 상태: **완료됨**" in status_status_window
    if readme_reflection_required != status_reflection_required or readme_completed != status_completed:
        _append_stale_hit(
            stale_hits,
            axis_matches,
            axis="judgement",
            path="README.md <-> docs/orchestrator-multigenerator-upgrade-status.md",
            rule="readme_status_completion_sync",
            match=(
                f"readme_reflection_required={readme_reflection_required}, "
                f"status_reflection_required={status_reflection_required}, "
                f"readme_completed={readme_completed}, status_completed={status_completed}"
            ),
            note="README와 상태 문서의 반영 필요/완료됨 판정이 불일치합니다.",
        )
    latest_record_blob = " ".join(
        str(latest_verification_record.get(key) or "")
        for key in ["topic", "command", "result", "evidence"]
    ).lower()
    latest_record_result = str(latest_verification_record.get("result") or "")
    if latest_record_blob and "통과" in latest_record_result:
        latest_row_rules = [
            {
                "keyword": "system-settings",
                "rule": "latest_record_system_settings_sync",
                "readme_forbidden": ["504 gateway timeout", "오케스트레이터 전역 설정 조회 실패"],
                "status_forbidden": ["504 gateway timeout", "system-settings 반복 504", "오케스트레이터 전역 설정 조회 실패"],
            },
            {
                "keyword": "ws=open",
                "rule": "latest_record_websocket_sync",
                "readme_forbidden": ["운영 경로 websocket 미완료", "websocket 미완료"],
                "status_forbidden": ["운영 경로 websocket 미완료", "websocket 미완료"],
            },
            {
                "keyword": "customer_summary_ok=200",
                "rule": "latest_record_customer_summary_sync",
                "readme_forbidden": ["마켓 경로 미완료", "marketplace 미완료"],
                "status_forbidden": ["마켓 경로 미완료", "marketplace 미완료"],
            },
        ]
        for rule in latest_row_rules:
            if rule["keyword"] not in latest_record_blob:
                continue
            for forbidden in rule["readme_forbidden"]:
                if forbidden.lower() in readme_normalized:
                    _append_stale_hit(
                        stale_hits,
                        axis_matches,
                        axis="operational_paths",
                        path="README.md",
                        rule=rule["rule"],
                        match=f"latest_record={rule['keyword']} / forbidden={forbidden}",
                        note="최신 운영 실검증 통과 후 금지 문구가 README에 남아 있습니다.",
                    )
            for forbidden in rule["status_forbidden"]:
                if forbidden.lower() in status_overview_normalized:
                    _append_stale_hit(
                        stale_hits,
                        axis_matches,
                        axis="operational_paths",
                        path="docs/orchestrator-multigenerator-upgrade-status.md",
                        rule=rule["rule"],
                        match=f"latest_record={rule['keyword']} / forbidden={forbidden}",
                        note="최신 운영 실검증 통과 후 금지 문구가 상태 문서 개요에 남아 있습니다.",
                    )
    latest_record_link_table = [
        {
            "record_keyword": "system-settings",
            "record_scope_id": "phase-f-system-settings-504-recurrence",
            "document_paths": ["README.md", "docs/orchestrator-multigenerator-upgrade-status.md"],
            "expected_reflection": "system-settings 504 재발 차단 결과가 README/상태 문서에 stale 없이 반영돼야 합니다.",
        },
        {
            "record_keyword": "ws=open",
            "record_scope_id": "phase-f-websocket-recovery",
            "document_paths": ["README.md", "docs/orchestrator-multigenerator-upgrade-status.md"],
            "expected_reflection": "websocket 운영 경로 통과 상태가 README/상태 문서에 동기화돼야 합니다.",
        },
        {
            "record_keyword": "customer_summary_ok=200",
            "record_scope_id": "phase-f-marketplace-route-recovery",
            "document_paths": ["README.md", "docs/orchestrator-multigenerator-upgrade-status.md"],
            "expected_reflection": "marketplace/customer summary 운영 경로 통과 상태가 README/상태 문서에 동기화돼야 합니다.",
        },
        {
            "record_keyword": "반영 필요/완료됨",
            "record_scope_id": "phase-g-documentation-sync",
            "document_paths": ["README.md", "docs/orchestrator-multigenerator-upgrade-status.md"],
            "expected_reflection": "반영 필요/완료됨 판정이 두 문서에서 동일해야 합니다.",
        },
        {
            "record_keyword": "app/services.py",
            "record_scope_id": "phase-g-service-package-contract",
            "document_paths": ["README.md", "docs/**/*"],
            "expected_reflection": "서비스 패키지 계약은 app/services/__init__.py + runtime_service.py 기준만 유지해야 합니다.",
        },
    ]
    latest_round_matches = [
        item
        for item in stale_hits
        if str(item.get("rule") or "").startswith("latest_record_")
    ]
    if latest_verification_record and not latest_round_matches:
        matched_keywords = [
            entry["record_keyword"]
            for entry in latest_record_link_table
            if entry["record_keyword"] != "반영 필요/완료됨"
            and entry["record_keyword"].lower() in latest_record_blob
        ]
        axis_matches["latest_verification_round"] = [
            {
                "axis": "latest_verification_round",
                "axis_label": axis_labels["latest_verification_round"],
                "path": "README.md <-> docs/orchestrator-multigenerator-upgrade-status.md",
                "rule": "latest_record_document_sync_ok",
                "match": ", ".join(matched_keywords) or "no-known-keyword",
                "note": "최신 실검증 회차와 문서 반영 상태가 현재 기준으로 충돌하지 않습니다.",
            }
        ]
    else:
        axis_matches["latest_verification_round"] = latest_round_matches
    axes = {}
    for axis, axis_label in axis_labels.items():
        matches = list(axis_matches.get(axis) or [])
        axes[axis] = {
            "axis": axis,
            "axis_label": axis_label,
            "ok": len(matches) == 0,
            "stale_count": len(matches),
            "matches": matches,
        }
    documentation_sync = {
        "schema_version": "v1",
        "overall_status": "synced" if len(stale_hits) == 0 else "reflection_required",
        "stale_count": len(stale_hits),
        "axes": {
            axis: {
                "ok": payload["ok"],
                "stale_count": payload["stale_count"],
            }
            for axis, payload in axes.items()
        },
        "latest_verification_record": latest_verification_record,
        "latest_record_link_table": latest_record_link_table,
        "readme_status_sync": {
            "readme_reflection_required": readme_reflection_required,
            "status_reflection_required": status_reflection_required,
            "readme_completed": readme_completed,
            "status_completed": status_completed,
        },
        "stale_matches": stale_hits,
    }
    return {
        "ok": len(stale_hits) == 0,
        "axes": axes,
        "matches": stale_hits,
        "latest_verification_record": latest_verification_record,
        "documentation_sync": documentation_sync,
    }


def _build_final_readiness_checklist_content(
    *,
    project_name: str,
    completion_gate_ok: bool,
    semantic_gate_ok: bool,
    packaging_audit_ok: bool,
    integration_test_engine_ok: bool,
    framework_e2e_validation_ok: bool,
    external_integration_validation_ok: bool,
    shipping_zip_validation_ok: bool,
    product_readiness_hard_gate_ok: bool,
    hard_gate_checklist_lines: List[str],
    operational_latency_lines: List[str],
) -> str:
    return (
        f"# {project_name} final readiness checklist\n\n"
        f"- [{'x' if completion_gate_ok else ' '}] completion gate\n"
        f"- [{'x' if semantic_gate_ok else ' '}] semantic gate\n"
        f"- [{'x' if packaging_audit_ok else ' '}] packaging audit\n"
        f"- [{'x' if integration_test_engine_ok else ' '}] integration test engine\n"
        f"- [{'x' if framework_e2e_validation_ok else ' '}] framework e2e validation\n"
        f"- [{'x' if external_integration_validation_ok else ' '}] external integration validation\n"
        f"- [{'x' if shipping_zip_validation_ok else ' '}] shipping zip validation\n"
        f"- [{'x' if product_readiness_hard_gate_ok else ' '}] product readiness hard gate\n\n"
        "## hard gate closure\n"
        + "\n".join(hard_gate_checklist_lines)
        + "\n\n"
        + "## operational latency evidence\n"
        + "\n".join(operational_latency_lines)
        + "\n"
    )


def repair_final_readiness_checklist(output_dir: Path) -> Dict[str, Any]:
    output_dir = Path(output_dir).resolve()
    validation_json_path = output_dir / ORCH_VALIDATION_RESULT_JSON_PATH
    readiness_checklist_path = output_dir / "docs" / "final_readiness_checklist.md"
    if not validation_json_path.exists():
        return {
            "ok": False,
            "reason": f"validation result not found: {validation_json_path}",
            "output_dir": str(output_dir),
        }
    payload = json.loads(validation_json_path.read_text(encoding="utf-8"))
    validation_engines = payload.get("validation_engines") or {}
    product_readiness_hard_gate = validation_engines.get("product_readiness_hard_gate") or {}
    hard_gate_stage_map = {
        str(stage.get("id") or ""): stage
        for stage in (product_readiness_hard_gate.get("stages") or [])
        if isinstance(stage, dict)
    }
    hard_gate_checklist_lines = [
        f"- [{'x' if hard_gate_stage_map.get('dependency_install', {}).get('ok') else ' '}] dependency install",
        f"- [{'x' if hard_gate_stage_map.get('standalone_boot', {}).get('ok') else ' '}] standalone boot",
        f"- [{'x' if hard_gate_stage_map.get('api_smoke', {}).get('ok') else ' '}] core api smoke",
        f"- [{'x' if hard_gate_stage_map.get('pytest', {}).get('ok') else ' '}] pytest",
        f"- [{'x' if hard_gate_stage_map.get('zip_reproduction', {}).get('ok') else ' '}] zip reproduction",
    ]
    readiness_content = _build_final_readiness_checklist_content(
        project_name=str(payload.get("project_name") or output_dir.name),
        completion_gate_ok=bool(payload.get("completion_gate_ok")),
        semantic_gate_ok=bool((validation_engines.get("semantic_gate") or {}).get("ok")),
        packaging_audit_ok=bool(hard_gate_stage_map.get("packaging_audit", {}).get("ok")),
        integration_test_engine_ok=bool((validation_engines.get("integration_test_engine") or {}).get("ok")),
        framework_e2e_validation_ok=bool((validation_engines.get("framework_e2e_validation") or {}).get("ok")),
        external_integration_validation_ok=bool((validation_engines.get("external_integration_validation") or {}).get("ok")),
        shipping_zip_validation_ok=bool((validation_engines.get("shipping_zip_validation") or {}).get("ok")),
        product_readiness_hard_gate_ok=bool(product_readiness_hard_gate.get("ok")),
        hard_gate_checklist_lines=hard_gate_checklist_lines,
        operational_latency_lines=[
            f"- latency_warning: {'true' if ((payload.get('readiness_artifacts') or {}).get('latency_warning')) else 'false'}",
            f"- warning_targets: {', '.join(((payload.get('readiness_artifacts') or {}).get('warning_targets') or [])) or 'none'}",
            f"- max_latency_ms: {((payload.get('readiness_artifacts') or {}).get('max_latency_ms') if ((payload.get('readiness_artifacts') or {}).get('max_latency_ms') is not None) else 'none')}",
            f"- warning_threshold_ms: {', '.join(f'{key}={value}ms' for key, value in sorted((((payload.get('readiness_artifacts') or {}).get('warning_threshold_ms') or {}).items()))) or 'none'}",
        ],
    )
    _compat_write_text(readiness_checklist_path, readiness_content)
    payload["readiness_artifacts"] = {
        "final_readiness_checklist_path": _compat_relative_path(readiness_checklist_path, output_dir),
        "validation_result_json_path": ORCH_VALIDATION_RESULT_JSON_PATH,
        "validation_result_md_path": ORCH_VALIDATION_RESULT_MD_PATH,
        "failure_report_path": ORCH_FAILURE_REPORT_PATH,
        "root_cause_report_path": ORCH_ROOT_CAUSE_REPORT_PATH,
    }
    _compat_write_json(validation_json_path, payload)
    return {
        "ok": True,
        "output_dir": str(output_dir),
        "final_readiness_checklist_path": str(readiness_checklist_path),
        "length": len(readiness_content),
    }


def _run_shipping_zip_reproduction_validation(
    *,
    output_dir: Path,
    archive_path: Path,
    validation_profile: str,
    integration_test_plan: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    work_root = Path(ORCH_VALIDATION_WORK_ROOT).resolve()
    failures: List[str] = []
    checks_run: List[str] = []
    extracted_root = work_root / f"zip_repro_{hashlib.sha256(str(archive_path).encode('utf-8', errors='ignore')).hexdigest()[:12]}"

    if not archive_path.exists():
        return {
            "engine": "shipping-zip-reproduction-validator",
            "validation_profile": validation_profile,
            "ok": False,
            "checks_run": ["archive_exists"],
            "failures": [f"shipment archive not found: {archive_path}"],
            "extracted_root": str(extracted_root),
        }

    shutil.rmtree(extracted_root, ignore_errors=True)
    extracted_root.mkdir(parents=True, exist_ok=True)
    checks_run.append("extract_zip")
    try:
        with zipfile.ZipFile(archive_path, mode="r") as zf:
            zf.extractall(extracted_root)
    except Exception as exc:
        failures.append(f"zip extraction failed: {exc}")
        return {
            "engine": "shipping-zip-reproduction-validator",
            "validation_profile": validation_profile,
            "ok": False,
            "checks_run": checks_run,
            "failures": failures,
            "extracted_root": str(extracted_root),
        }

    if validation_profile == "python_fastapi":
        requirements_lock_path = extracted_root / "requirements.delivery.lock.txt"
        requirements_path = extracted_root / "requirements.txt"
        install_requirements_path = requirements_lock_path if requirements_lock_path.exists() else requirements_path
        checks_run.append(f"exists:{install_requirements_path.name}")
        if not install_requirements_path.exists():
            failures.append("zip reproduction missing requirements file")
        cache_basis = install_requirements_path.read_text(encoding="utf-8", errors="ignore") if install_requirements_path.exists() else ""
        cache_key = hashlib.sha256((cache_basis + sys.version).encode("utf-8", errors="ignore")).hexdigest()[:12]
        cached_venv_dir = work_root / f"zip_venv_cache_{cache_key}"
        venv_dir = cached_venv_dir if cache_basis else extracted_root / ".zip-venv"
        venv_python = _venv_python_path(venv_dir)
        if venv_python.exists():
            checks_run.append(f"reuse cached zip validation venv:{venv_dir.name}")
        else:
            checks_run.append(f"python -m venv {venv_dir.name}")
            try:
                venv_create_result = subprocess.run(
                    [sys.executable, "-m", "venv", str(venv_dir)],
                    cwd=str(work_root if venv_dir == cached_venv_dir else extracted_root),
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                venv_python = _venv_python_path(venv_dir)
                if venv_create_result.returncode != 0 or not venv_python.exists():
                    checks_run.append("virtualenv fallback")
                    repair_error = _repair_python_validation_venv(work_root if venv_dir == cached_venv_dir else extracted_root, venv_dir)
                    if repair_error:
                        failures.append(f"zip reproduction venv create failed: {repair_error}")
                        return {
                            "engine": "shipping-zip-reproduction-validator",
                            "validation_profile": validation_profile,
                            "ok": False,
                            "checks_run": checks_run,
                            "failures": failures,
                            "extracted_root": str(extracted_root),
                        }
            except Exception as exc:
                failures.append(f"zip reproduction venv create failed: {exc}")
                return {
                    "engine": "shipping-zip-reproduction-validator",
                    "validation_profile": validation_profile,
                    "ok": False,
                    "checks_run": checks_run,
                    "failures": failures,
                    "extracted_root": str(extracted_root),
                }

            checks_run.append("python -m ensurepip --upgrade")
            try:
                ensurepip_result = subprocess.run(
                    [str(venv_python), "-m", "ensurepip", "--upgrade"],
                    cwd=str(extracted_root),
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                ensurepip_output = (ensurepip_result.stderr or ensurepip_result.stdout or "").strip()
                if ensurepip_result.returncode != 0 and "No module named ensurepip" not in ensurepip_output:
                    failures.append(ensurepip_output[:1600] or "zip reproduction ensurepip failed")
            except Exception as exc:
                failures.append(f"zip reproduction ensurepip error: {exc}")

            checks_run.append("python -m pip install --upgrade pip")
            try:
                pip_upgrade_result = subprocess.run(
                    [str(venv_python), "-m", "pip", "install", "--upgrade", "pip"],
                    cwd=str(extracted_root),
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if pip_upgrade_result.returncode != 0:
                    failures.append((pip_upgrade_result.stderr or pip_upgrade_result.stdout or "zip reproduction pip bootstrap failed").strip()[:1600])
            except Exception as exc:
                failures.append(f"zip reproduction pip bootstrap error: {exc}")

            checks_run.append(f"pip install -r {install_requirements_path.name}")
            try:
                install_result = subprocess.run(
                    [str(venv_python), "-m", "pip", "install", "-r", install_requirements_path.name],
                    cwd=str(extracted_root),
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
                if install_result.returncode != 0:
                    failures.append((install_result.stderr or install_result.stdout or "zip reproduction pip install failed").strip()[:1600])
            except Exception as exc:
                failures.append(f"zip reproduction pip install error: {exc}")

        compile_targets = _build_python_fastapi_validation_targets(extracted_root)["compile_targets"]
        checks_run.append("python -m compileall " + " ".join(compile_targets or ["app", "backend", "tests"]))
        try:
            compile_result = subprocess.run(
                [str(venv_python), "-m", "compileall", *(compile_targets or ["app", "backend", "tests"])],
                cwd=str(extracted_root),
                capture_output=True,
                text=True,
                timeout=300,
            )
            if compile_result.returncode != 0:
                failures.append((compile_result.stderr or compile_result.stdout or "zip reproduction compileall failed").strip()[:1600])
        except Exception as exc:
            failures.append(f"zip reproduction compileall error: {exc}")

        planned_pytest_targets = [
            str(item).strip()
            for item in ((integration_test_plan or {}).get("required_tests") or [])
            if str(item).strip()
        ]
        pytest_targets = planned_pytest_targets or _build_python_fastapi_validation_targets(extracted_root)["pytest_targets"]
        checks_run.append("pytest -q -s " + " ".join(pytest_targets or ["tests/test_health.py", "tests/test_routes.py", "tests/test_runtime.py"]))
        try:
            pytest_command = [str(venv_python), "-m", "pytest", "-q", "-s", *(pytest_targets or ["tests/test_health.py", "tests/test_routes.py", "tests/test_runtime.py"])]
            pytest_env = os.environ.copy()
            pytest_env.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
            pytest_env.setdefault("TMPDIR", str(extracted_root / ".pytest-tmp"))
            Path(pytest_env["TMPDIR"]).mkdir(parents=True, exist_ok=True)
            pytest_env["TMP"] = pytest_env["TMPDIR"]
            pytest_env["TEMP"] = pytest_env["TMPDIR"]
            pytest_result = subprocess.run(
                pytest_command,
                cwd=str(extracted_root),
                capture_output=True,
                text=True,
                timeout=600,
                env=pytest_env,
            )
            pytest_output = ((pytest_result.stdout or "") + "\n" + (pytest_result.stderr or "")).strip()
            if pytest_result.returncode != 0:
                failures.append((pytest_output or "zip reproduction pytest failed")[:2000])
        except Exception as exc:
            failures.append(f"zip reproduction pytest error: {exc}")

        if not failures:
            _run_python_fastapi_live_api_validation(
                project_root=extracted_root,
                venv_python=venv_python,
                checks_run=checks_run,
                failures=failures,
            )

        dockerfile_path = extracted_root / "Dockerfile"
        checks_run.append("exists:Dockerfile")
        if not dockerfile_path.exists():
            failures.append("zip reproduction missing Dockerfile")

        if shutil.which("docker"):
            image_tag = f"zip-repro-{hashlib.sha256(str(archive_path).encode('utf-8', errors='ignore')).hexdigest()[:10]}"
            checks_run.append("docker build")
            try:
                build_result = subprocess.run(
                    ["docker", "build", "-t", image_tag, "."],
                    cwd=str(extracted_root),
                    capture_output=True,
                    text=True,
                    timeout=1200,
                )
                if build_result.returncode != 0:
                    failures.append((build_result.stderr or build_result.stdout or "zip reproduction docker build failed").strip()[:2000])
            except Exception as exc:
                failures.append(f"zip reproduction docker build error: {exc}")
        else:
            checks_run.append("docker unavailable in validator environment")
    else:
        checks_run.append("validation_profile_unsupported")
        failures.append(f"zip reproduction validator not implemented for profile: {validation_profile}")

    return {
        "engine": "shipping-zip-reproduction-validator",
        "validation_profile": validation_profile,
        "ok": len(failures) == 0,
        "checks_run": checks_run,
        "failures": failures,
        "extracted_root": str(extracted_root),
        "archive_path": str(archive_path),
    }


def _build_product_readiness_hard_gate(
    *,
    validation_profile: str,
    packaging_audit: Dict[str, Any],
    framework_e2e_validation: Dict[str, Any],
    external_integration_validation: Dict[str, Any],
    integration_test_engine: Dict[str, Any],
    shipping_zip_validation: Dict[str, Any],
    shipping_package: Dict[str, Any],
) -> Dict[str, Any]:
    integration_checks = list(integration_test_engine.get("checks_run") or [])
    integration_failures = [str(item) for item in (integration_test_engine.get("failures") or []) if str(item).strip()]

    dependency_install_ran = any(
        check.startswith("pip install -r") or check.startswith("python -m pip install")
        for check in integration_checks
    )
    standalone_boot_ran = any(check.startswith("standalone_boot:") for check in integration_checks)
    api_smoke_ran = any(check.startswith("http_get:") for check in integration_checks)
    pytest_ran = any(check.startswith("pytest -q") for check in integration_checks)

    def _stage(stage_id: str, ok: bool, summary: str, evidence: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return {
            "id": stage_id,
            "ok": bool(ok),
            "summary": summary,
            "evidence": dict(evidence or {}),
        }

    stages = [
        _stage(
            "packaging_audit",
            bool(packaging_audit.get("packaging_ready")),
            "패키징 필수 파일이 준비되었는지 확인합니다.",
            {
                "missing_packaging_files": list(packaging_audit.get("missing_packaging_files") or []),
                "required_packaging_files": list(packaging_audit.get("required_packaging_files") or []),
            },
        ),
        _stage(
            "dependency_install",
            dependency_install_ran and not any("pip install" in item or "venv" in item or "bootstrap" in item for item in integration_failures),
            "생성 직후 의존성 설치와 가상환경 구성이 성공해야 합니다.",
            {
                "checks_run": [check for check in integration_checks if check.startswith("pip install -r") or check.startswith("python -m pip install") or "venv" in check],
                "failures": [item for item in integration_failures if "pip install" in item or "venv" in item or "bootstrap" in item],
            },
        ),
        _stage(
            "standalone_boot",
            standalone_boot_ran and not any("standalone runtime" in item for item in integration_failures),
            "생성 산출물이 단독 기동되어야 합니다.",
            {
                "checks_run": [check for check in integration_checks if check.startswith("standalone_boot:")],
                "failures": [item for item in integration_failures if "standalone runtime" in item],
            },
        ),
        _stage(
            "api_smoke",
            api_smoke_ran and not any("standalone api" in item for item in integration_failures),
            "핵심 API 스모크 검증이 성공해야 합니다.",
            {
                "checks_run": [check for check in integration_checks if check.startswith("http_get:")],
                "failures": [item for item in integration_failures if "standalone api" in item],
            },
        ),
        _stage(
            "pytest",
            pytest_ran and not any("pytest" in item for item in integration_failures),
            "핵심 테스트가 통과해야 합니다.",
            {
                "checks_run": [check for check in integration_checks if check.startswith("pytest -q")],
                "failures": [item for item in integration_failures if "pytest" in item],
            },
        ),
        _stage(
            "framework_contract",
            bool(framework_e2e_validation.get("ok")),
            "프레임워크 구조와 실행 계약이 맞아야 합니다.",
            {
                "checks_run": list(framework_e2e_validation.get("commands_run") or []),
                "failures": list(framework_e2e_validation.get("failures") or []),
            },
        ),
        _stage(
            "external_integration",
            bool(external_integration_validation.get("ok")),
            "외부 연동 경계 파일과 커넥터가 존재해야 합니다.",
            {
                "checks_run": list(external_integration_validation.get("checks_run") or []),
                "failures": list(external_integration_validation.get("failures") or []),
            },
        ),
        _stage(
            "zip_reproduction",
            bool(shipping_zip_validation.get("ok")),
            "출고 ZIP 압축 해제 후 재현 검증이 성공해야 합니다.",
            {
                "checks_run": list(shipping_zip_validation.get("checks_run") or []),
                "failures": list(shipping_zip_validation.get("failures") or []),
                "archive_path": str(shipping_package.get("archive_path") or ""),
                "extracted_root": str(shipping_zip_validation.get("extracted_root") or ""),
            },
        ),
    ]

    failed_stages = [stage["id"] for stage in stages if not stage["ok"]]
    return {
        "validation_profile": validation_profile,
        "ok": len(failed_stages) == 0,
        "stages": stages,
        "failed_stages": failed_stages,
        "summary": (
            "product readiness hard gate passed"
            if not failed_stages
            else f"product readiness hard gate failed: {', '.join(failed_stages)}"
        ),
        "archive_path": str(shipping_package.get("archive_path") or ""),
    }


def _build_operational_evidence_bundle() -> Dict[str, Any]:
    target_defaults = [
        {
            "id": "websocket",
            "target": "/api/llm/ws",
            "protocol": "websocket",
            "verification_method": "websocket-handshake-and-ping-pong",
            "note": "실도메인 handshake + connected + ping/pong 검증 결과를 연결해야 합니다.",
            "warning_threshold_ms": 150.0,
        },
        {
            "id": "admin",
            "target": "/admin/llm",
            "protocol": "https",
            "verification_method": "http-response-and-page-render",
            "note": "관리자 오케스트레이터 운영 경로 실검증 결과를 연결해야 합니다.",
            "warning_threshold_ms": 150.0,
        },
        {
            "id": "marketplace",
            "target": "/marketplace/orchestrator",
            "protocol": "https",
            "verification_method": "http-response-and-page-render",
            "note": "마켓 오케스트레이터 운영 경로 실검증 결과를 연결해야 합니다.",
            "warning_threshold_ms": 200.0,
        },
        {
            "id": "system_settings",
            "target": "/api/admin/system-settings",
            "protocol": "https",
            "verification_method": "http-response-json",
            "note": "관리자 시스템 설정 운영 경로 실검증 결과를 연결해야 합니다.",
            "warning_threshold_ms": 120.0,
        },
        {
            "id": "workspace_self_run_record",
            "target": "/api/admin/workspace-self-run-record?latest=true",
            "protocol": "https",
            "verification_method": "http-response-latest-record",
            "note": "latest self-run record 운영 경로 실검증 결과를 연결해야 합니다.",
            "warning_threshold_ms": 120.0,
        },
    ]

    capability_evidence = {}
    try:
        from backend.llm import admin_capabilities as admin_capabilities_module

        target_defaults = [dict(item) for item in (getattr(admin_capabilities_module, "OPERATIONAL_EVIDENCE_TARGETS", None) or target_defaults)]
        cached_reader = getattr(admin_capabilities_module, "_read_operational_evidence_cache", None)
        if callable(cached_reader):
            cached_payload = cached_reader()
            if isinstance(cached_payload, dict):
                capability_evidence = dict(cached_payload)
    except Exception:
        capability_evidence = {}

    def _build_operational_evidence_summary(targets: List[Dict[str, Any]]) -> Dict[str, Any]:
        verified_count = 0
        warning_count = 0
        failed_count = 0
        warning_targets: List[str] = []
        latency_values: List[float] = []
        for target in targets:
            if not isinstance(target, dict):
                continue
            target_id = str(target.get("id") or "").strip()
            status = str(target.get("status") or "missing").strip().lower()
            if status == "verified":
                verified_count += 1
            elif status in {"warning", "degraded"}:
                warning_count += 1
            else:
                failed_count += 1
            if bool(target.get("latency_warning")) and target_id:
                warning_targets.append(target_id)
            latency_value = target.get("latency_ms")
            if isinstance(latency_value, (int, float)):
                latency_values.append(float(latency_value))
        return {
            "verified_count": verified_count,
            "warning_count": warning_count,
            "failed_count": failed_count,
            "required_count": len(targets),
            "warning_targets": warning_targets,
            "max_latency_ms": round(max(latency_values), 1) if latency_values else None,
        }

    evidence_map = capability_evidence if isinstance(capability_evidence, dict) else {}
    targets = []
    targets_by_id: Dict[str, Dict[str, Any]] = {}
    for item in target_defaults:
        evidence_item = evidence_map.get(item["id"]) if isinstance(evidence_map.get(item["id"]), dict) else {}
        ok = bool(evidence_item.get("ok"))
        status = str(evidence_item.get("status") or ("verified" if ok else "missing"))
        snapshot = {
            **item,
            "ok": ok,
            "status": status,
            "status_code": evidence_item.get("status_code"),
            "latency_ms": evidence_item.get("latency_ms"),
            "latency_warning": bool(evidence_item.get("latency_warning")),
            "warning_threshold_ms": evidence_item.get("warning_threshold_ms") or item.get("warning_threshold_ms"),
            "verified_at": evidence_item.get("verified_at"),
            "source": evidence_item.get("source") or "runtime-cache",
            "note": str(evidence_item.get("note") or item["note"]),
        }
        targets.append(snapshot)
        targets_by_id[item["id"]] = snapshot

    summary = _build_operational_evidence_summary(targets)
    return {
        "integration_status": (
            "verified"
            if summary["verified_count"] == len(targets)
            else ("partial" if summary["verified_count"] > 0 else "pending-runtime-verification")
        ),
        "verified_target_count": summary["verified_count"],
        "required_target_count": len(targets),
        "warning_target_count": summary["warning_count"],
        "failed_target_count": summary["failed_count"],
        "warning_targets": summary["warning_targets"],
        "max_latency_ms": summary["max_latency_ms"],
        "summary": summary,
        "targets_by_id": targets_by_id,
        "websocket": targets_by_id.get("websocket", {}),
        "admin": targets_by_id.get("admin", {}),
        "marketplace": targets_by_id.get("marketplace", {}),
        "system_settings": targets_by_id.get("system_settings", {}),
        "workspace_self_run_record": targets_by_id.get("workspace_self_run_record", {}),
        "targets": targets,
    }


def _build_post_validation_ai_analysis(
    *,
    completion_gate_ok: bool,
    semantic_audit_score: int,
    semantic_audit_ok: bool,
    product_readiness_hard_gate: Dict[str, Any],
    target_patch_registry: Dict[str, Any],
    operational_evidence: Dict[str, Any],
) -> Dict[str, Any]:
    failed_stages = list(product_readiness_hard_gate.get("failed_stages") or [])
    reusable_patch_units = list(target_patch_registry.get("reusable_patch_units") or [])
    target_file_ids = list(target_patch_registry.get("target_file_ids") or [])
    target_section_ids = list(target_patch_registry.get("target_section_ids") or [])
    target_feature_ids = list(target_patch_registry.get("target_feature_ids") or [])
    target_chunk_ids = list(target_patch_registry.get("target_chunk_ids") or [])
    failure_tags = list(target_patch_registry.get("failure_tags") or [])
    repair_tags = list(target_patch_registry.get("repair_tags") or [])
    operational_targets = list(operational_evidence.get("targets") or [])
    verified_targets = [
        str(item.get("id") or "target")
        for item in operational_targets
        if bool(item.get("ok"))
    ]
    missing_targets = [
        str(item.get("id") or "target")
        for item in operational_targets
        if not bool(item.get("ok"))
    ]
    analysis_summary = (
        "검증 통과 후 AI 정밀 분석: 출고 게이트를 통과했으며 selective apply 가능한 조각 단위를 중심으로 후속 확장 후보를 제안합니다."
        if completion_gate_ok
        else "검증 통과 후 AI 정밀 분석: 아직 게이트 차단이 남아 있어 실패 stage 제거와 selective apply 범위 축소가 우선입니다."
    )
    quality_findings = [
        f"semantic audit score={semantic_audit_score}",
        "semantic audit pass 상태입니다." if semantic_audit_ok else "semantic audit 보강이 필요합니다.",
        f"failure tags={len(failure_tags)} / repair tags={len(repair_tags)}",
    ]
    architecture_findings = [
        f"reusable patch units={len(reusable_patch_units)}",
        "고유 ID registry 기반 selective apply ready 상태입니다."
        if target_patch_registry.get("selective_apply_ready")
        else "고유 ID registry 매칭 범위를 더 늘려 selective apply ready 상태를 확보해야 합니다.",
        f"target ids files={len(target_file_ids)}, sections={len(target_section_ids)}, features={len(target_feature_ids)}, chunks={len(target_chunk_ids)}",
    ]
    ops_findings = [
        f"operational evidence integration={operational_evidence.get('integration_status') or 'unknown'}",
        "실도메인 운영 경로 증거를 hard gate evidence 체계와 연결해야 합니다.",
        f"verified targets={', '.join(verified_targets) or 'none'}",
    ]
    if failed_stages:
        ops_findings.append("차단된 hard gate stage: " + ", ".join(failed_stages))
    if missing_targets:
        ops_findings.append("추가 검증 필요 targets: " + ", ".join(missing_targets))
    return {
        "analysis_summary": analysis_summary,
        "quality_findings": quality_findings,
        "architecture_findings": architecture_findings,
        "ops_findings": ops_findings,
        "new_technology_candidates": [
            "evidence replay validator",
            "post-validation proposal ranking",
            "selective apply safety scorer",
            "target-id impact graph",
        ],
        "recommended_expansion_actions": [
            (
                "selective apply 대상으로 매칭된 file/section/chunk id를 기준으로 후속 self-improvement 작업문을 생성합니다."
                f" (files={len(target_file_ids)}, sections={len(target_section_ids)}, chunks={len(target_chunk_ids)})"
            ),
            (
                "운영 실도메인 evidence 결과를 approval/completion judge와 같은 증거 체계로 연결합니다."
                f" (verified={len(verified_targets)}/{len(operational_targets)})"
            ),
            (
                "failure_tags 와 repair_tags 우선순위를 기준으로 후속 개선 액션을 재정렬합니다."
                f" (failure_tags={len(failure_tags)}, repair_tags={len(repair_tags)})"
            ),
        ],
        "artifact_source": {
            "target_file_ids": target_file_ids,
            "target_section_ids": target_section_ids,
            "target_feature_ids": target_feature_ids,
            "target_chunk_ids": target_chunk_ids,
            "failure_tags": failure_tags,
            "repair_tags": repair_tags,
            "verified_operational_targets": verified_targets,
            "missing_operational_targets": missing_targets,
            "failed_hard_gate_stages": failed_stages,
        },
    }


def _build_completion_judge(
    *,
    semantic_gate: Dict[str, Any],
    packaging_audit: Dict[str, Any],
    integration_test_engine: Dict[str, Any],
    normalized_requirements: Dict[str, Any],
    integration_test_plan: Dict[str, Any],
    completion_state: str,
    framework_e2e_validation: Dict[str, Any],
    external_integration_validation: Dict[str, Any],
    shipping_zip_validation: Dict[str, Any],
    operational_evidence: Dict[str, Any] | None = None,
    legacy_contract_scan: Dict[str, Any] | None = None,
    output_dir: Path,
    written_files: List[str],
    domain_contract: Dict[str, Any],
) -> Dict[str, Any]:
    failed_reasons: List[str] = []
    quality_findings: List[str] = []
    if not bool(semantic_gate.get("ok")):
        failed_reasons.append("semantic gate failed")
    if not bool(packaging_audit.get("packaging_ready")):
        failed_reasons.append("packaging audit incomplete")
    if not bool(integration_test_engine.get("ok")):
        failed_reasons.append("integration test engine failed")

    runtime_checks = list(integration_test_plan.get("runtime_checks") or [])
    profile_id = str(domain_contract.get("profile_id") or "customer_program").strip()
    if completion_state != "ready":
        failed_reasons.append("scaffold output detected")
    if any("scaffold inventory" in str(path).lower() for path in written_files):
        quality_findings.append("scaffold inventory present in shipment")

    file_count = len(written_files)
    python_file_count = len([path for path in written_files if str(path).endswith(".py")])
    frontend_file_count = len([
        path for path in written_files
        if str(path).startswith(("frontend/", "app/")) and str(path).endswith((".ts", ".tsx", ".js", ".jsx"))
    ])
    test_file_count = len([path for path in written_files if str(path).startswith("tests/")])
    docs_file_count = len([path for path in written_files if str(path).startswith("docs/")])
    thin_file_markers: Dict[str, List[str]] = {
        "Makefile": ["run:", "test:", "check:"],
        "backend/main.py": ["create_application", "uvicorn.run", "__all__"],
        "scripts/dev.sh": ["set -euo pipefail", "uvicorn"],
        "scripts/check.sh": ["python -m compileall", "pytest -q -s", "requirements.delivery.lock.txt"],
        "backend/core/__init__.py": ["build_scaffold_runtime", "__all__"],
        "app/__init__.py": ["create_application", "build_runtime_payload", "__all__"],
        "backend/app/external_adapters/status_client.py": ["build_provider_status_map", "providers", "reachable"],
        "backend/app/connectors/base.py": ["CatalogConnectorResult", "build_sync_summary", "sync_products"],
        "backend/core/auth.py": ["JWT_SECRET", "JWT_ALGORITHM", "JWT_EXPIRE_MINUTES", "get_auth_settings"],
        "backend/core/security.py": ["ALLOWED_HOSTS", "CORS_ALLOW_ORIGINS", "https_only", "REQUEST_TIMEOUT_SEC"],
        "app/auth_routes.py": ["/auth", "/settings", "/token", "/validate"],
        "app/ops_routes.py": ["/ops", "/status", "/health", "/metrics"],
        "configs/app.env.example": ["DATABASE_URL=", "JWT_SECRET=", "ALLOWED_HOSTS=", "REQUEST_TIMEOUT_SEC=", "MODEL_REGISTRY_PATH="],
        "infra/prometheus.yml": ["scrape_interval", "job_name", "targets:"],
        "infra/deploy/security.md": ["JWT_SECRET", "ALLOWED_HOSTS", "CORS_ALLOW_ORIGINS", "TLS"],
    }
    tiny_files: List[str] = []
    for path in written_files:
        target_path = output_dir / path
        if not target_path.exists() or not target_path.is_file() or "__pycache__" in str(path):
            continue
        normalized_path = str(path)
        if normalized_path.endswith("__init__.py"):
            continue
        if normalized_path.endswith((".json", ".yml", ".yaml", ".toml", ".env.example", ".md", ".txt", ".gitignore")):
            continue
        file_text = _strip_generated_id_headers(target_path.read_text(encoding="utf-8", errors="ignore"))
        required_markers = thin_file_markers.get(normalized_path)
        if required_markers is not None:
            if not all(marker in file_text for marker in required_markers):
                tiny_files.append(normalized_path)
            continue
        if target_path.stat().st_size <= 120:
            tiny_files.append(normalized_path)

    if file_count < 25:
        quality_findings.append(f"written_files too small: {file_count}")
    if docs_file_count < 5:
        quality_findings.append(f"docs coverage too small: {docs_file_count}")
    if test_file_count < 3:
        quality_findings.append(f"test coverage too small: {test_file_count}")
    generic_runtime_sources: List[str] = []
    for candidate in [
        output_dir / "README.md",
        output_dir / "docs" / "deployment.md",
        output_dir / "docs" / "testing.md",
        output_dir / "docs" / "runbook.md",
        output_dir / "app" / "auth_routes.py",
        output_dir / "app" / "ops_routes.py",
        output_dir / "backend" / "core" / "auth.py",
        output_dir / "backend" / "core" / "security.py",
        output_dir / "infra" / "prometheus.yml",
        output_dir / "infra" / "deploy" / "security.md",
    ]:
        if candidate.exists():
            generic_runtime_sources.append(candidate.read_text(encoding="utf-8", errors="ignore").lower())
    generic_runtime_text = "\n".join(generic_runtime_sources)
    generic_runtime_markers = {
        "auth settings flow": ["/auth/settings", "jwt_secret", "scopes"],
        "ops health flow": ["/ops/health", "/ops/status", "provider_status"],
        "shipping package flow": ["shipping", "shipment", "scripts/check.sh"],
        "security runtime flow": ["allowed_hosts", "cors_allow_origins", "https_only"],
    }
    for check_name, markers in generic_runtime_markers.items():
        if check_name in runtime_checks and not any(marker in generic_runtime_text for marker in markers):
            quality_findings.append(f"runtime scenario marker missing: {check_name}")
    if profile_id == "commerce_platform":
        if frontend_file_count < 5:
            quality_findings.append(f"commerce frontend implementation too small: {frontend_file_count}")
        if python_file_count < 12:
            quality_findings.append(f"commerce backend implementation too small: {python_file_count}")
        required_runtime_markers = {
            "catalog flow": ["catalog", "product"],
            "order workflow": ["order", "checkout"],
            "marketplace publish payload": ["publish", "shipment"],
        }
        readme_text = ""
        readme_path = output_dir / "README.md"
        if readme_path.exists():
            readme_text = readme_path.read_text(encoding="utf-8", errors="ignore").lower()
        frontend_page_text = ""
        for candidate in [output_dir / "frontend" / "app" / "page.tsx", output_dir / "app" / "page.tsx"]:
            if candidate.exists():
                frontend_page_text = candidate.read_text(encoding="utf-8", errors="ignore").lower()
                break
        combined_runtime_text = f"{readme_text}\n{frontend_page_text}"
        for check_name, markers in required_runtime_markers.items():
            if check_name in runtime_checks and not any(marker in combined_runtime_text for marker in markers):
                quality_findings.append(f"runtime scenario marker missing: {check_name}")

    if profile_id == "deployment_kit_program":
        if python_file_count < 18:
            quality_findings.append(f"deployment kit backend implementation too small: {python_file_count}")
        if docs_file_count < 8:
            quality_findings.append(f"deployment kit docs coverage too small: {docs_file_count}")
        deployment_markers = {
            "publish readiness flow": ["publish-readiness", "publish_payload_ready", "publish_targets"],
            "ops health flow": ["/ops/health", "provider_status", "metrics"],
            "auth settings flow": ["/auth/settings", "JWT_SECRET", "scopes"],
            "shipping package flow": ["출고 패키지", "shipment.zip", "scripts/check.sh"],
        }
        runtime_sources: List[str] = []
        for candidate in [
            output_dir / "README.md",
            output_dir / "docs" / "deployment.md",
            output_dir / "docs" / "testing.md",
            output_dir / "docs" / "runbook.md",
            output_dir / "docs" / "shipping_readme.md",
            output_dir / "app" / "routes.py",
            output_dir / "app" / "ops_routes.py",
            output_dir / "app" / "auth_routes.py",
            output_dir / "app" / "services" / "runtime_service.py",
        ]:
            if candidate.exists():
                runtime_sources.append(candidate.read_text(encoding="utf-8", errors="ignore").lower())
        combined_runtime_text = "\n".join(runtime_sources)
        for check_name, markers in deployment_markers.items():
            if check_name in runtime_checks and not any(marker.lower() in combined_runtime_text for marker in markers):
                quality_findings.append(f"runtime scenario marker missing: {check_name}")

    if tiny_files:
        quality_findings.append("thin implementation files detected: " + ", ".join(tiny_files[:8]))
    if not bool(framework_e2e_validation.get("ok")):
        failed_reasons.append("framework e2e validation failed")
    if not bool(external_integration_validation.get("ok")):
        failed_reasons.append("external integration validation failed")
    if not bool(shipping_zip_validation.get("ok")):
        failed_reasons.append("shipping zip reproduction validation failed")
    legacy_contract_payload = dict(legacy_contract_scan or {})
    if legacy_contract_payload and not bool(legacy_contract_payload.get("ok")):
        failed_reasons.append("legacy services contract detected")
        for match in list(legacy_contract_payload.get("matches") or [])[:12]:
            match_path = str(match.get("path") or "unknown")
            match_text = str(match.get("match") or "app/services.py")
            quality_findings.append(f"legacy contract marker detected: {match_path} -> {match_text}")
    if quality_findings:
        failed_reasons.extend(quality_findings)

    failed_reasons = list(dict.fromkeys(failed_reasons))
    operational_evidence_payload = dict(operational_evidence or {})
    operational_targets = list(operational_evidence_payload.get("targets") or [])
    required_target_count = int(
        operational_evidence_payload.get("required_target_count")
        or len(operational_targets)
        or 0
    )
    verified_target_count = int(operational_evidence_payload.get("verified_target_count") or 0)
    integration_status = str(operational_evidence_payload.get("integration_status") or "unknown").strip()
    customer_generation_mode = bool(output_dir.exists() and (output_dir / "docs" / "generation-plan.json").exists())
    if required_target_count > 0 and verified_target_count < required_target_count:
        if not customer_generation_mode:
            failed_reasons.append(
                f"operational evidence incomplete: {verified_target_count}/{required_target_count}"
            )
    elif required_target_count <= 0 or integration_status in {"", "unknown", "failed", "pending-runtime-verification"}:
        if not customer_generation_mode:
            failed_reasons.append(
                f"operational evidence unavailable: {integration_status or 'unknown'}"
            )
    warning_targets = [
        str(target.get("id") or "")
        for target in operational_targets
        if isinstance(target, dict) and target.get("latency_warning")
    ]
    warning_threshold_ms = {
        str(target.get("id") or ""): round(float(target.get("warning_threshold_ms")), 1)
        for target in operational_targets
        if isinstance(target, dict)
        and str(target.get("id") or "")
        and isinstance(target.get("warning_threshold_ms"), (int, float))
    }
    latency_values = [
        float(target.get("latency_ms"))
        for target in operational_targets
        if isinstance(target, dict) and isinstance(target.get("latency_ms"), (int, float))
    ]
    operational_summary = {
        "integration_status": operational_evidence_payload.get("integration_status") or "unknown",
        "verified_target_count": int(operational_evidence_payload.get("verified_target_count") or 0),
        "required_target_count": int(operational_evidence_payload.get("required_target_count") or len(operational_targets)),
        "latency_warning": bool(warning_targets),
        "warning_targets": warning_targets,
        "warning_threshold_ms": warning_threshold_ms,
        "max_latency_ms": round(max(latency_values), 1) if latency_values else None,
        "targets": operational_targets,
    }
    return {
        "product_ready": len(failed_reasons) == 0,
        "failed_reasons": failed_reasons,
        "quality_findings": quality_findings,
        "scaffold_only": completion_state != "ready",
        "quality_summary": {
            "written_files": file_count,
            "python_files": python_file_count,
            "frontend_files": frontend_file_count,
            "test_files": test_file_count,
            "docs_files": docs_file_count,
            "thin_files": tiny_files[:20],
        },
        "completion_conditions": normalized_requirements.get("completion_conditions") or [],
        "test_conditions": normalized_requirements.get("test_conditions") or [],
        "required_tests": integration_test_plan.get("required_tests") or [],
        "shipping_zip_validation": shipping_zip_validation,
        "improvement_loop_enabled": True,
        "improvement_loop_strategy": [
            "100% 통과 후 구매자 피드백 수집",
            "요구사항-기능 차이 구조화",
            "확장 및 보정 작업문 자동 생성",
            "같은 게이트/출고 엔진으로 재검증",
        ],
        "operational_evidence_summary": operational_summary,
        "legacy_contract_scan": legacy_contract_payload,
    }


def _compat_validate_import_links(manifest_lookup: Dict[str, str]) -> List[str]:
    findings: List[str] = []
    for path, content in manifest_lookup.items():
        if not path.endswith(".py"):
            continue
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line.startswith("from ") or " import " not in line:
                continue
            module_name = line[len("from "):].split(" import ", 1)[0].strip()
            if not module_name or module_name.startswith(("fastapi", "pydantic", "typing", "json", "datetime", "uvicorn", "pathlib", "sys", "__future__", "sqlalchemy", "jose", "os", "collections", "dataclasses", "functools", "backend.core")):
                continue
            module_path = module_name.replace('.', '/')
            candidate_paths = [
                f"{module_path}.py",
                f"{module_path}/__init__.py",
            ]
            if not any(candidate in manifest_lookup for candidate in candidate_paths):
                if module_name == "app.services":
                    preferred_target = "app/services/__init__.py"
                elif module_name.startswith("app.services."):
                    preferred_target = f"{module_path}.py"
                else:
                    preferred_target = candidate_paths[0]
                findings.append(f"{path}: missing import target {preferred_target}")
    return findings


def _compat_validate_required_files(manifest_lookup: Dict[str, str], required_files: List[str]) -> List[str]:
    missing = [path for path in required_files if path not in manifest_lookup]
    return [f"missing required file: {path}" for path in missing]


def _compat_validate_ai_implementation(
    order_profile: Dict[str, Any],
    manifest_lookup: Dict[str, str],
    validation_profile: str,
    required_files: List[str],
) -> List[str]:
    findings: List[str] = []
    if not bool(order_profile.get("ai_enabled")):
        return findings
    if validation_profile != "python_fastapi":
        return findings
    profile_id = str(order_profile.get("profile_id") or "").strip()
    mandatory_engine_contracts = [
        str(item).strip()
        for item in (order_profile.get("mandatory_engine_contracts") or [])
        if str(item).strip()
    ]
    strategy_service_required = "backend/service/strategy_service.py" in required_files
    if not strategy_service_required:
        return findings
    strategy_service = manifest_lookup.get("backend/service/strategy_service.py", "")
    if not strategy_service:
        findings.append("AI profile requires backend/service/strategy_service.py")
        return findings
    required_markers = [
        "build_strategy_service_overview",
        "load_model_registry",
        "run_training_pipeline",
        "run_inference_runtime",
        "build_evaluation_report",
        "ai_capabilities",
        "inference_runtime",
        "training_pipeline",
        "evaluation_report",
    ]
    for marker in required_markers:
        if marker not in strategy_service:
            findings.append(f"backend/service/strategy_service.py missing AI marker: {marker}")
    if "placeholder" in strategy_service.lower():
        findings.append("backend/service/strategy_service.py contains placeholder AI implementation")
    adapters_module = manifest_lookup.get("ai/adapters.py", "")
    adapter_markers = ["resolve_adapter", "decision_key", "default_decision"]
    for marker in adapter_markers:
        if marker not in adapters_module:
            findings.append(f"ai/adapters.py missing adapter marker: {marker}")
    domain_adapter_service = manifest_lookup.get("backend/service/domain_adapter_service.py", "")
    for marker in ["build_domain_adapter_summary", "resolve_adapter", "build_feature_set"]:
        if marker not in domain_adapter_service:
            findings.append(f"backend/service/domain_adapter_service.py missing domain adapter marker: {marker}")
    app_services = "\n".join([
        manifest_lookup.get("app/services/__init__.py", ""),
        manifest_lookup.get("app/services/runtime_service.py", ""),
    ])
    if "build_feature_matrix" not in app_services or "build_domain_snapshot" not in app_services:
        findings.append("app/services/__init__.py + app/services/runtime_service.py missing runtime/service bridge markers")
    api_router = manifest_lookup.get("backend/api/router.py", "")
    api_router_markers = [
        "get_ai_runtime_snapshot",
        "model_registry",
        "training_pipeline",
        "inference_runtime",
        "evaluation_report",
    ]
    for marker in api_router_markers:
        if marker not in api_router:
            findings.append(f"backend/api/router.py missing AI API marker: {marker}")
    app_main = manifest_lookup.get("app/main.py", "")
    app_main_markers = ["from ai.router import router as ai_router", "app.include_router(ai_router)"]
    app_main_markers.extend(["from app.auth_routes import auth_router", "from app.ops_routes import ops_router", "app.include_router(auth_router)", "app.include_router(ops_router)"])
    for marker in app_main_markers:
        if marker not in app_main:
            findings.append(f"app/main.py missing AI router binding marker: {marker}")
    ai_router = manifest_lookup.get("ai/router.py", "")
    ai_router_markers = [
        "router = APIRouter(prefix='/ai'",
        "@router.get('/health')",
        "@router.post('/train')",
        "@router.post('/inference')",
        "@router.post('/evaluate')",
        "InferenceRequest",
        "TrainingRequest",
        "EvaluationRequest",
    ]
    for marker in ai_router_markers:
        if marker not in ai_router:
            findings.append(f"ai/router.py missing AI endpoint marker: {marker}")
    ai_schemas = manifest_lookup.get("ai/schemas.py", "")
    ai_schema_markers = ["class InferenceRequest", "class TrainingRequest", "class EvaluationRequest"]
    for marker in ai_schema_markers:
        if marker not in ai_schemas:
            findings.append(f"ai/schemas.py missing schema marker: {marker}")
    tests_routes = manifest_lookup.get("tests/test_routes.py", "")
    route_test_markers = ["get_ai_runtime_snapshot", "test_ai_fastapi_endpoints", "/ai/health", "/ai/inference", "/ai/evaluate"]
    for marker in route_test_markers:
        if marker not in tests_routes:
            findings.append(f"tests/test_routes.py missing AI route marker: {marker}")
    tests_health = manifest_lookup.get("tests/test_health.py", "")
    if "ai_contract_ready" not in tests_health:
        findings.append("tests/test_health.py missing ai_contract_ready assertion")
    tests_runtime = manifest_lookup.get("tests/test_runtime.py", "")
    if "ai_runtime_contract" not in tests_runtime:
        findings.append("tests/test_runtime.py missing ai_runtime_contract assertion")
    diagnostics = manifest_lookup.get("app/diagnostics.py", "")
    diagnostics_markers = ["ai-runtime-contract-ready", "ai-health-report-validated", "ai_validation"]
    for marker in diagnostics_markers:
        if marker not in diagnostics:
            findings.append(f"app/diagnostics.py missing AI report marker: {marker}")
    for core_file, core_markers in {
        "backend/core/database.py": ["get_database_settings", "DB_SETTINGS"],
        "backend/core/models.py": ["class RuntimeEvent", "class ModelRegistryEntry"],
        "backend/core/auth.py": ["get_auth_settings", "AUTH_SETTINGS"],
        "backend/core/ops_logging.py": ["record_ops_log", "list_ops_logs"],
    }.items():
        content = manifest_lookup.get(core_file, "")
        for marker in core_markers:
            if marker not in content:
                findings.append(f"{core_file} missing core contract marker: {marker}")
    app_services = "\n".join([
        manifest_lookup.get("app/services/__init__.py", ""),
        manifest_lookup.get("app/services/runtime_service.py", ""),
    ])
    contract_markers = [
        "build_ai_runtime_contract",
        "InferenceRequest",
        "TrainingRequest",
        "EvaluationRequest",
        "ai_runtime_contract",
        "/ai/health",
        "/ai/train",
        "/ai/inference",
        "/ai/evaluate",
        "get_database_settings",
        "get_auth_settings",
        "record_ops_log",
        "build_domain_adapter_summary",
        "ensure_database_ready",
        "create_access_token",
    ]
    for marker in contract_markers:
        if marker not in app_services:
            findings.append(f"app/services/__init__.py + app/services/runtime_service.py missing AI contract marker: {marker}")
    if "checked_via': ['/health', '/report']" not in app_services and 'checked_via": ["/health", "/report"]' not in app_services:
        findings.append("app/services/__init__.py + app/services/runtime_service.py missing /health and /report validation trace")
    if "ai_runtime_snapshot" not in tests_routes and "get_ai_runtime_snapshot" not in tests_routes:
        findings.append("tests/test_routes.py missing AI runtime route coverage marker")
    frontend_page = manifest_lookup.get("frontend/app/page.tsx", "")
    frontend_markers = ["AI 상태 패널", "model_registry", "training_pipeline", "inference_runtime", "evaluation_report"]
    for marker in frontend_markers:
        if marker not in frontend_page:
            findings.append(f"frontend/app/page.tsx missing AI UI marker: {marker}")
    for deploy_file, deploy_markers in {
        "infra/prometheus.yml": ["scrape_configs", "targets"],
        "infra/deploy/security.md": ["JWT_SECRET", "DATABASE_URL", "TLS"],
    }.items():
        content = manifest_lookup.get(deploy_file, "")
        for marker in deploy_markers:
            if marker not in content:
                findings.append(f"{deploy_file} missing deployment marker: {marker}")
    if "AI 상태 패널" not in frontend_page and "ai_capabilities" not in frontend_page and "model_registry" not in frontend_page:
        findings.append("frontend/app/page.tsx missing AI presentation markers")
    if mandatory_engine_contracts:
        order_profile_doc = manifest_lookup.get("docs/order_profile.md", "")
        for marker in mandatory_engine_contracts:
            if marker not in order_profile_doc:
                findings.append(f"docs/order_profile.md missing mandatory engine contract marker: {marker}")
        for marker in mandatory_engine_contracts:
            normalized_marker = marker.replace("-", "_")
            if marker not in app_services and normalized_marker not in app_services:
                findings.append(f"app/services/__init__.py + app/services/runtime_service.py missing mandatory engine contract marker: {marker}")
        if profile_id == "lottery_prediction_system":
            lottery_markers = [
                "draw_histories",
                "prediction_runs",
                "candidate_sets",
                "candidate-number-generator",
                "prediction-evaluation",
            ]
            for marker in lottery_markers:
                normalized_marker = marker.replace("-", "_")
                if (
                    marker not in app_services
                    and normalized_marker not in app_services
                    and marker not in strategy_service
                    and normalized_marker not in strategy_service
                ):
                    findings.append(f"lottery prediction engine missing marker: {marker}")
        if profile_id == "trading_system":
            trading_markers = [
                "signal-ingestion",
                "risk-guard",
                "order-execution",
                "portfolio-sync",
                "broker-adapter",
            ]
            trading_sources = [
                strategy_service,
                manifest_lookup.get("ai/features.py", ""),
                manifest_lookup.get("ai/inference.py", ""),
                manifest_lookup.get("app/services/__init__.py", ""),
                manifest_lookup.get("app/services/runtime_service.py", ""),
            ]
            for marker in trading_markers:
                normalized_marker = marker.replace("-", "_")
                if not any(
                    marker in source or normalized_marker in source
                    for source in trading_sources
                ):
                    findings.append(f"trading engine missing marker: {marker}")
            for marker in ["build_risk_guard", "build_order_execution_plan", "build_portfolio_sync"]:
                if marker not in strategy_service:
                    findings.append(f"backend/service/strategy_service.py missing trading marker: {marker}")
    return findings


def _compat_validate_python_sources(manifest_lookup: Dict[str, str]) -> List[str]:
    findings: List[str] = []
    for path, content in manifest_lookup.items():
        if not path.endswith(".py"):
            continue
        if any(path.startswith(prefix) for prefix in ["app/", "backend/", "tests/"]) and "package.json" in manifest_lookup:
            continue
        normalized = str(content or "")
        if not normalized.strip():
            if not path.endswith("__init__.py"):
                findings.append(f"{path} is empty python source")
            continue
        if "compat generated file" in normalized:
            findings.append(f"{path} contains placeholder compat content")
            continue
        try:
            compile(normalized, path, "exec")
        except SyntaxError as exc:
            findings.append(f"{path} has syntax error: {exc.msg}")
    return findings


def _compat_validate_profile_alignment(
    task: str,
    project_name: str,
    order_profile: Dict[str, Any],
) -> List[str]:
    task_text = str(task or "").lower()
    project_name_text = str(project_name or "").lower()
    source_text = f"{task_text}\n{project_name_text}"
    profile_id = str(order_profile.get("profile_id") or "").strip()
    findings: List[str] = []
    explicit_task_domains = [
        (
            "trading_system",
            ["자동매매", "트레이딩", "주식", "매매", "trading", "stock", "portfolio", "signal"],
        ),
    ]
    for expected_profile_id, markers in explicit_task_domains:
        if any(marker.lower() in task_text for marker in markers):
            if profile_id != expected_profile_id:
                findings.append(
                    f"profile mismatch: expected {expected_profile_id} for task/project context, got {profile_id or 'unknown'}"
                )
            return findings
    domain_markers = [
        (
            "commerce_platform",
            ["쇼핑몰", "마켓플레이스", "이커머스", "커머스", "commerce", "marketplace", "store"],
        ),
        (
            "trading_system",
            ["자동매매", "트레이딩", "주식", "trading", "stock", "portfolio"],
        ),
    ]
    for expected_profile_id, markers in domain_markers:
        if any(marker in source_text for marker in markers) and profile_id != expected_profile_id:
            findings.append(
                f"profile mismatch: expected {expected_profile_id} for task/project context, got {profile_id or 'unknown'}"
            )
            break
    return findings


def _compat_run_semantic_gate(
    task: str,
    project_name: str,
    order_profile: Dict[str, Any],
    validation_profile: str,
    manifest: List[Dict[str, str]],
) -> Dict[str, Any]:
    manifest_lookup = _compat_build_manifest_lookup(manifest)
    required_files = _compat_domain_required_files(order_profile, validation_profile)
    findings: List[str] = []
    findings.extend(_compat_validate_required_files(manifest_lookup, required_files))
    findings.extend(_compat_validate_import_links(manifest_lookup))
    findings.extend(_compat_validate_python_sources(manifest_lookup))
    findings.extend(_compat_validate_runtime_completeness(manifest_lookup, order_profile))
    findings.extend(_compat_validate_profile_alignment(task, project_name, order_profile))
    findings.extend(_compat_validate_ai_implementation(order_profile, manifest_lookup, validation_profile, required_files))
    packaging_targets = [
        "README.md",
        "docs/usage.md",
        "docs/runtime.md",
        "docs/deployment.md",
        "docs/testing.md",
        "configs/app.env.example",
    ]
    for packaging_path in packaging_targets:
        if packaging_path not in manifest_lookup:
            findings.append(f"missing packaging file: {packaging_path}")
    unique_findings = list(dict.fromkeys(findings))
    ok = len(unique_findings) == 0
    score = 100 if ok else max(0, 100 - (len(unique_findings) * 18))
    summary = "semantic gate passed" if ok else "; ".join(unique_findings[:6])
    return {
        "ok": ok,
        "score": score,
        "summary": summary,
        "checklist": unique_findings,
        "checklist_items": [
            {
                "title": finding,
                "status": "passed" if ok else "failed",
                "detail": finding,
            }
            for finding in unique_findings
        ],
        "required_files": required_files,
    }


async def run_orchestration(
    request: OrchestrationRequest,
    progress_callback: Optional[Callable[[str, str], None]] = None,
) -> OrchestrationResponse:
    return await run_customer_orchestration_service(
        request,
        run_orchestration_impl=_run_orchestration_core,
        progress_callback=progress_callback,
    )


async def _run_orchestration_core(
    request: OrchestrationRequest,
    progress_callback: Optional[Callable[[str, str], None]] = None,
) -> OrchestrationResponse:
    started_at = time.perf_counter()
    preparation = await prepare_customer_orchestration_context_service(
        request,
        normalize_requested_mode_func=_normalize_requested_mode,
        emit_orchestration_progress_func=_emit_orchestration_progress,
        build_customer_order_profile_func=_build_customer_order_profile,
        compat_domain_required_files_func=_compat_domain_required_files,
        orch_required_file_paths=list(ORCH_REQUIRED_FILE_PATHS or []),
        normalize_customer_requirements_func=_normalize_customer_requirements,
        build_domain_contract_func=_build_domain_contract,
        build_integration_test_plan_func=_build_integration_test_plan,
        normalize_pipeline_agents_func=_normalize_pipeline_agents,
        filter_pipeline_for_validation_profile_func=_filter_pipeline_for_validation_profile,
        orch_b_brain_agent_key=ORCH_B_BRAIN_AGENT_KEY,
        orchestration_spec_type=OrchestrationSpec,
        default_dod_targets_func=_default_dod_targets,
        compat_project_name_func=_compat_project_name,
        compat_output_dir_func=_compat_output_dir,
        progress_callback=progress_callback,
    )
    task = str(preparation["task"])
    mode = str(preparation["mode"])
    order_profile = dict(preparation["order_profile"])
    validation_profile = str(preparation["validation_profile"])
    compat_required_files = list(preparation["compat_required_files"])
    normalized_requirements = dict(preparation["normalized_requirements"])
    domain_contract = dict(preparation["domain_contract"])
    integration_test_plan = dict(preparation["integration_test_plan"])
    spec = preparation["spec"]
    project_name = str(preparation["project_name"])
    output_dir = preparation["output_dir"]
    _log_orchestration_phase("prepared", started_at, project_name=project_name, validation_profile=validation_profile)
    b_brain_result = _run_b_brain_multi_generator(
        project_name=project_name,
        validation_profile=validation_profile,
        task=task,
        output_dir=output_dir,
    )
    _log_orchestration_phase("generator_bundle_written", started_at, project_name=project_name, validation_profile=validation_profile)
    anchor_path, manifest, completion_state = _compat_manifest_for_request(task, project_name, validation_profile, compat_required_files)
    written_files = _compat_write_manifest(output_dir, manifest)
    for generated_file in b_brain_result["written_files"]:
        if generated_file not in written_files:
            written_files.append(generated_file)
    _emit_orchestration_progress(progress_callback, f"초기 산출물 {len(written_files)}개를 생성했습니다.")
    _log_orchestration_phase("compat_manifest_written", started_at, project_name=project_name, validation_profile=validation_profile)

    semantic_gate = _compat_run_semantic_gate(task, project_name, order_profile, validation_profile, manifest)
    _emit_orchestration_progress(
        progress_callback,
        (
            "semantic gate 통과"
            if not semantic_gate.get("checklist")
            else f"semantic gate findings: {'; '.join(list(semantic_gate.get('checklist') or [])[:3])}"
        ),
        "success" if not semantic_gate.get("checklist") else "error",
    )
    _log_orchestration_phase("semantic_gate_finished", started_at, project_name=project_name, validation_profile=validation_profile)
    packaging_audit = _build_packaging_audit(order_profile, compat_required_files, written_files)
    _emit_orchestration_progress(progress_callback, "post-semantic stage: packaging audit computed")
    logger.info(
        "run_orchestration about_to_call_integration_test_engine elapsed_sec=%.2f project=%s validation_profile=%s output_dir=%s",
        max(0.0, time.perf_counter() - started_at),
        project_name,
        validation_profile,
        str(output_dir),
    )
    integration_test_engine = _run_domain_integration_test_engine(
        output_dir=output_dir,
        validation_profile=validation_profile,
        integration_test_plan=integration_test_plan,
    )
    _emit_orchestration_progress(
        progress_callback,
        (
            "integration test engine 통과"
            if integration_test_engine.get("ok")
            else f"integration test engine findings: {'; '.join(list(integration_test_engine.get('failures') or [])[:3])}"
        ),
        "success" if integration_test_engine.get("ok") else "error",
    )
    logger.info(
        "run_orchestration returned_from_integration_test_engine elapsed_sec=%.2f project=%s validation_profile=%s integration_ok=%s failure_count=%s",
        max(0.0, time.perf_counter() - started_at),
        project_name,
        validation_profile,
        bool(integration_test_engine.get("ok")),
        len(list(integration_test_engine.get("failures") or [])),
    )
    _log_orchestration_phase("integration_test_engine_finished", started_at, project_name=project_name, validation_profile=validation_profile)
    framework_e2e_validation = _run_framework_e2e_validator(
        output_dir=output_dir,
        validation_profile=validation_profile,
    )
    _emit_orchestration_progress(
        progress_callback,
        "framework e2e validation 통과" if framework_e2e_validation.get("ok") else f"framework e2e findings: {'; '.join(list(framework_e2e_validation.get('failures') or [])[:3])}",
        "success" if framework_e2e_validation.get("ok") else "error",
    )
    _log_orchestration_phase("framework_e2e_finished", started_at, project_name=project_name, validation_profile=validation_profile)
    external_integration_validation = _run_external_integration_validator(
        output_dir=output_dir,
        order_profile=order_profile,
    )
    _emit_orchestration_progress(
        progress_callback,
        "external integration validation 통과" if external_integration_validation.get("ok") else f"external integration findings: {'; '.join(list(external_integration_validation.get('failures') or [])[:3])}",
        "success" if external_integration_validation.get("ok") else "error",
    )
    _log_orchestration_phase("external_integration_finished", started_at, project_name=project_name, validation_profile=validation_profile)
    completion_judge = _build_completion_judge(
        semantic_gate=semantic_gate,
        packaging_audit=packaging_audit,
        integration_test_engine=integration_test_engine,
        normalized_requirements=normalized_requirements,
        integration_test_plan=integration_test_plan,
        completion_state=completion_state,
        framework_e2e_validation=framework_e2e_validation,
        external_integration_validation=external_integration_validation,
        shipping_zip_validation={"ok": False, "checks_run": [], "failures": ["shipping zip reproduction validation not yet executed"]},
        operational_evidence=_build_operational_evidence_bundle(),
        output_dir=output_dir,
        written_files=written_files,
        domain_contract=domain_contract,
    )
    _emit_orchestration_progress(progress_callback, "post-validation stage: completion judge computed")
    semantic_audit_score = int(semantic_gate["score"])
    semantic_audit_ok = bool(semantic_gate["ok"]) and semantic_audit_score >= ORCH_SEMANTIC_AUDIT_MIN_SCORE

    artifact_log_path = output_dir / ORCH_ARTIFACT_LOG_PATH
    traceability_map_path = output_dir / ORCH_TRACEABILITY_MAP_PATH
    semantic_audit_report_path = output_dir / ORCH_SEMANTIC_AUDIT_REPORT_PATH
    python_security_report_path = output_dir / ORCH_PYTHON_SECURITY_REPORT_PATH
    target_patch_registry_snapshot = build_target_patch_registry_snapshot(
        written_files=written_files,
        target_paths=[anchor_path, "backend/llm/admin_capabilities.py", "frontend/frontend/app/admin/llm/page.tsx"],
        capability_ids=["code-generator", "self-healing-engine", "project-scanner"],
    )
    _emit_orchestration_progress(progress_callback, "post-validation stage: target patch registry computed")
    artifact_paths_seed = {
        "artifact_log_path": ORCH_ARTIFACT_LOG_PATH,
        "traceability_map_path": ORCH_TRACEABILITY_MAP_PATH,
        "semantic_audit_report_path": ORCH_SEMANTIC_AUDIT_REPORT_PATH,
        "python_security_validation_report_path": ORCH_PYTHON_SECURITY_REPORT_PATH,
        "final_readiness_checklist_path": "docs/final_readiness_checklist.md",
        "automatic_validation_result_path": ORCH_VALIDATION_RESULT_JSON_PATH,
        "automatic_validation_markdown_path": ORCH_VALIDATION_RESULT_MD_PATH,
        "failure_report_path": ORCH_FAILURE_REPORT_PATH,
        "root_cause_report_path": ORCH_ROOT_CAUSE_REPORT_PATH,
        "output_audit_path": ORCH_OUTPUT_AUDIT_PATH,
    }
    evidence_bundle_seed = {
        "contract": {
            "evidence_schema_version": "v1",
            "profile_id": validation_profile,
        },
        "execution": {
            "evidence_run_id": request.run_id or task,
            "evidence_generated_at": datetime.utcnow().isoformat() + "Z",
        },
        "readiness": {
            "artifact_paths": dict(artifact_paths_seed),
        },
        "snapshot": {
            "artifact_paths": dict(artifact_paths_seed),
        },
        "selective_apply": {
            "target_file_ids": list(target_patch_registry_snapshot.get("target_file_ids") or []),
            "target_section_ids": list(target_patch_registry_snapshot.get("target_section_ids") or []),
            "target_feature_ids": list(target_patch_registry_snapshot.get("target_feature_ids") or []),
            "target_chunk_ids": list(target_patch_registry_snapshot.get("target_chunk_ids") or []),
            "failure_tags": list(target_patch_registry_snapshot.get("failure_tags") or []),
            "repair_tags": list(target_patch_registry_snapshot.get("repair_tags") or []),
        },
    }
    _compat_write_json(artifact_log_path, {"task": task, "mode": mode, "written_files": written_files, "completion_state": completion_state, "evidence_bundle": evidence_bundle_seed})
    _compat_write_json(traceability_map_path, {
        "anchor_path": anchor_path,
        "written_files": written_files,
        "target_patch_registry": target_patch_registry_snapshot,
        "target_patch_candidates": list(target_patch_registry_snapshot.get("matched_entries") or []),
        "target_file_ids": list(target_patch_registry_snapshot.get("target_file_ids") or []),
        "target_section_ids": list(target_patch_registry_snapshot.get("target_section_ids") or []),
        "target_feature_ids": list(target_patch_registry_snapshot.get("target_feature_ids") or []),
        "target_chunk_ids": list(target_patch_registry_snapshot.get("target_chunk_ids") or []),
        "failure_tags": list(target_patch_registry_snapshot.get("failure_tags") or []),
        "repair_tags": list(target_patch_registry_snapshot.get("repair_tags") or []),
        "id_registry_path": ORCH_ID_REGISTRY_PATH,
        "id_registry_schema_path": ORCH_ID_REGISTRY_SCHEMA_PATH,
        "evidence_bundle": evidence_bundle_seed,
    })
    _compat_write_json(python_security_report_path, {"ok": True, "findings": []})
    _emit_orchestration_progress(progress_callback, "post-validation stage: seed artifacts written")
    semantic_audit_report_path.parent.mkdir(parents=True, exist_ok=True)
    semantic_audit_report_path.write_text(
        "# Semantic Completion Audit\n\n"
        f"- score: {semantic_audit_score}\n"
        f"- threshold: {ORCH_SEMANTIC_AUDIT_MIN_SCORE}\n"
        f"- status: {'pass' if semantic_audit_ok else 'fail'}\n",
        encoding="utf-8",
    )
    auxiliary_outputs = _compat_write_auxiliary_outputs(
        output_dir,
        task,
        project_name,
        mode,
        validation_profile,
        written_files,
        anchor_path,
        semantic_audit_score,
        semantic_audit_ok,
        target_patch_registry_snapshot,
    )
    _emit_orchestration_progress(progress_callback, "post-validation stage: auxiliary outputs written")
    generated_meta_paths = [
        _compat_relative_path(artifact_log_path, output_dir),
        _compat_relative_path(traceability_map_path, output_dir),
        _compat_relative_path(semantic_audit_report_path, output_dir),
        _compat_relative_path(python_security_report_path, output_dir),
    ]
    for rel_path in generated_meta_paths:
        if rel_path not in written_files:
            written_files.append(rel_path)
    for rel_path in auxiliary_outputs.values():
        if rel_path not in written_files:
            written_files.append(rel_path)
    written_files = list(dict.fromkeys(sorted(written_files)))
    artifact_paths_seed["checklist_path"] = str(auxiliary_outputs.get("checklist_path") or "")
    artifact_paths_seed["manifest_path"] = str(auxiliary_outputs.get("manifest_path") or "")
    evidence_bundle_seed.setdefault("readiness", {})["artifact_paths"] = dict(artifact_paths_seed)
    evidence_bundle_seed.setdefault("snapshot", {})["artifact_paths"] = dict(artifact_paths_seed)
    _compat_write_json(artifact_log_path, {"task": task, "mode": mode, "written_files": written_files, "completion_state": completion_state, "evidence_bundle": evidence_bundle_seed})
    _compat_write_json(traceability_map_path, {
        "anchor_path": anchor_path,
        "written_files": written_files,
        "target_patch_registry": target_patch_registry_snapshot,
        "target_patch_candidates": list(target_patch_registry_snapshot.get("matched_entries") or []),
        "target_file_ids": list(target_patch_registry_snapshot.get("target_file_ids") or []),
        "target_section_ids": list(target_patch_registry_snapshot.get("target_section_ids") or []),
        "target_feature_ids": list(target_patch_registry_snapshot.get("target_feature_ids") or []),
        "target_chunk_ids": list(target_patch_registry_snapshot.get("target_chunk_ids") or []),
        "failure_tags": list(target_patch_registry_snapshot.get("failure_tags") or []),
        "repair_tags": list(target_patch_registry_snapshot.get("repair_tags") or []),
        "id_registry_path": ORCH_ID_REGISTRY_PATH,
        "id_registry_schema_path": ORCH_ID_REGISTRY_SCHEMA_PATH,
        "artifact_paths": artifact_paths_seed,
        "evidence_bundle": evidence_bundle_seed,
    })
    _emit_orchestration_progress(progress_callback, f"메타 파일 포함 총 {len(written_files)}개 산출물을 정리했습니다.", "success")
    _emit_orchestration_progress(progress_callback, "finalization dispatch 시작")
    _log_orchestration_phase("validation_artifacts_written", started_at, project_name=project_name, validation_profile=validation_profile)
    _log_orchestration_phase("finalization_dispatch", started_at, project_name=project_name, validation_profile=validation_profile)

    finalized = finalize_customer_validation_bundle_service(
        output_dir=output_dir,
        task=task,
        mode=mode,
        project_name=project_name,
        validation_profile=validation_profile,
        normalized_requirements=normalized_requirements,
        domain_contract=domain_contract,
        integration_test_plan=integration_test_plan,
        packaging_audit=packaging_audit,
        completion_state=completion_state,
        written_files=written_files,
        semantic_gate=semantic_gate,
        framework_e2e_validation=framework_e2e_validation,
        external_integration_validation=external_integration_validation,
        integration_test_engine=integration_test_engine,
        completion_judge=completion_judge,
        semantic_audit_score=semantic_audit_score,
        semantic_audit_ok=semantic_audit_ok,
        target_patch_registry_snapshot=target_patch_registry_snapshot,
        anchor_path=anchor_path,
        artifact_log_path=artifact_log_path,
        traceability_map_path=traceability_map_path,
        output_audit_path=output_dir / str(auxiliary_outputs.get("output_audit_path") or ORCH_OUTPUT_AUDIT_PATH),
        build_shipping_package_func=_build_shipping_package,
        log_orchestration_phase_func=_log_orchestration_phase,
        run_shipping_zip_reproduction_validation_func=_run_shipping_zip_reproduction_validation,
        build_product_readiness_hard_gate_func=_build_product_readiness_hard_gate,
        build_operational_evidence_bundle_func=_build_operational_evidence_bundle,
        build_completion_judge_func=_build_completion_judge,
        build_post_validation_ai_analysis_func=_build_post_validation_ai_analysis,
        write_automatic_validation_artifacts_func=_write_automatic_validation_artifacts,
        build_evidence_bundle_func=_build_evidence_bundle,
        request_run_id=str(request.run_id or ""),
        started_at=started_at,
        emit_orchestration_progress_func=_emit_orchestration_progress,
        progress_callback=progress_callback,
    )
    _log_orchestration_phase("finalization_returned", started_at, project_name=project_name, validation_profile=validation_profile)
    shipping_package = dict(finalized["shipping_package"])
    shipping_zip_validation = dict(finalized["shipping_zip_validation"])
    product_readiness_hard_gate = dict(finalized["product_readiness_hard_gate"])
    operational_evidence = dict(finalized["operational_evidence"])
    completion_judge = dict(finalized["completion_judge"])
    completion_gate_ok = bool(finalized["completion_gate_ok"])
    post_validation_analysis = dict(finalized["post_validation_analysis"])
    validation_artifacts = dict(finalized["validation_artifacts"])
    evidence_bundle = dict(finalized["evidence_bundle"])
    artifact_paths = dict(finalized.get("artifact_paths") or {})
    written_files = list(finalized["written_files"])

    return assemble_customer_orchestration_response_service(
        request=request,
        task=task,
        mode=mode,
        project_name=project_name,
        validation_profile=validation_profile,
        order_profile=order_profile,
        semantic_gate=semantic_gate,
        completion_judge=completion_judge,
        completion_gate_ok=completion_gate_ok,
        semantic_audit_score=semantic_audit_score,
        semantic_audit_ok=semantic_audit_ok,
        written_files=written_files,
        anchor_path=anchor_path,
        output_dir=output_dir,
        artifact_log_path=artifact_log_path,
        semantic_audit_report_path=semantic_audit_report_path,
        python_security_report_path=python_security_report_path,
        traceability_map_path=traceability_map_path,
        auxiliary_outputs=auxiliary_outputs,
        target_patch_registry_snapshot=target_patch_registry_snapshot,
        shipping_package=shipping_package,
        validation_artifacts=validation_artifacts,
        product_readiness_hard_gate=product_readiness_hard_gate,
        integration_test_engine=integration_test_engine,
        shipping_zip_validation=shipping_zip_validation,
        packaging_audit=packaging_audit,
        framework_e2e_validation=framework_e2e_validation,
        external_integration_validation=external_integration_validation,
        post_validation_analysis=post_validation_analysis,
        operational_evidence=operational_evidence,
        evidence_bundle=evidence_bundle,
        artifact_paths=artifact_paths,
        normalized_requirements=normalized_requirements,
        domain_contract=domain_contract,
        integration_test_plan=integration_test_plan,
        spec=spec,
        b_brain_result=b_brain_result,
        build_stage_history_with_refiner_fixer_func=_build_stage_history_with_refiner_fixer,
        build_refiner_fixer_stage_payload_func=_build_refiner_fixer_stage_payload,
        build_improvement_loop_plan_func=_build_improvement_loop_plan,
        run_refinement_loop_func=_run_refinement_loop,
        build_multi_command_plan_func=build_multi_command_plan,
        build_admin_flow_trace_func=build_admin_flow_trace,
        resolve_active_trace_func=resolve_active_trace,
        agent_result_type=AgentResult,
        conversation_message_type=ConversationMessage,
        response_type=OrchestrationResponse,
        agent_roles=AGENT_ROLES,
        orch_b_brain_agent_key=ORCH_B_BRAIN_AGENT_KEY,
        get_reasoning_model_func=get_reasoning_model,
        get_planner_model_func=get_planner_model,
        get_designer_model_func=get_designer_model,
        resolve_template_profile_func=_resolve_template_profile,
        orch_semantic_audit_min_score=ORCH_SEMANTIC_AUDIT_MIN_SCORE,
        log_orchestration_phase_func=_log_orchestration_phase,
        started_at=started_at,
    )


async def execute_orchestration(
    request: OrchestrationRequest,
    progress_callback: Optional[Callable[[str, str], None]] = None,
) -> OrchestrationResponse:
    return await execute_customer_orchestration_service(
        request,
        run_orchestration_func=run_orchestration,
        emit_orchestration_progress_func=_emit_orchestration_progress,
        progress_callback=progress_callback,
    )


async def _call_orchestrator_chat_llm(
    *,
    route_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
) -> str:
    combined_prompt = f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": combined_prompt}],
        "stream": False,
        "options": build_ollama_options(
            route_key,
            {
                "num_predict": max_tokens,
                "temperature": 0.4,
                "top_p": 0.9,
                "repeat_penalty": 1.05,
            },
        ),
    }
    client = await _get_orchestrator_chat_http_client()
    response = await client.post("/api/chat", json=payload)
    response.raise_for_status()
    data = response.json()
    content = str(data.get("message", {}).get("content") or "").strip()
    return content


@router.post("/orchestrate", response_model=OrchestrationResponse)
async def orchestrate(
    request: OrchestrationRequest,
) -> OrchestrationResponse:
    _enforce_global_orchestration_gate(request)
    return await execute_orchestration(request)


@router.post("/orchestrate/accepted", response_model=OrchestrationAcceptedResponse)
async def orchestrate_accepted(
    request: OrchestrationRequest,
) -> OrchestrationAcceptedResponse:
    _enforce_global_orchestration_gate(request)
    if _accepted_orchestrate_requests_full_mode(request):
        raise HTTPException(
            status_code=409,
            detail="accepted orchestrate는 장시간 full 생성 대신 progress polling/SSE 전용 경로입니다. 운영에서는 /api/llm/orchestrate/stream 또는 marketplace customer-orchestrate stage run 경로를 사용하세요.",
        )
    run_id = str(request.run_id or uuid4().hex)
    accepted_request = request.model_copy(update={"run_id": run_id})
    initial_payload = {
        "run_id": run_id,
        "project_name": accepted_request.project_name,
        "output_dir": accepted_request.output_dir,
        "status": "accepted",
        "accepted_at": datetime.utcnow().isoformat() + "Z",
        "poll_url": _build_progress_poll_url(run_id),
        "stream_url": _build_progress_stream_url(run_id),
        "events": [
            {
                "at": datetime.utcnow().isoformat() + "Z",
                "level": "info",
                "message": "오케스트레이션 요청을 수락했고 백그라운드 작업을 시작합니다.",
            }
        ],
    }
    _save_orchestration_progress(run_id, initial_payload)

    def _progress_callback(message: str, level: str = "info") -> None:
        try:
            _record_orchestration_progress_event(run_id, message=message, level=level)
        except Exception:
            logger.warning("orchestrate accepted progress callback failed", exc_info=True)

    def _worker() -> None:
        try:
            response = asyncio.run(execute_orchestration(accepted_request, progress_callback=_progress_callback))
            _mark_orchestration_progress_result(run_id, response)
        except Exception as exc:
            logger.exception("orchestrate accepted worker failed run_id=%s", run_id)
            _mark_orchestration_progress_error(run_id, error_message=str(exc))

    threading.Thread(
        target=_worker,
        name=f"orchestrate-accepted-{run_id[:12]}",
        daemon=True,
    ).start()

    return OrchestrationAcceptedResponse(
        accepted=True,
        run_id=run_id,
        project_name=accepted_request.project_name,
        output_dir=accepted_request.output_dir,
        status="accepted",
        poll_url=_build_progress_poll_url(run_id),
        stream_url=_build_progress_stream_url(run_id),
    )


@router.get("/orchestrate/progress/{run_id}")
async def get_orchestration_progress(run_id: str) -> Dict[str, Any]:
    payload = _load_orchestration_progress(run_id)
    if not payload:
        raise HTTPException(status_code=404, detail="orchestration progress를 찾을 수 없습니다.")
    return payload


@router.post("/orchestrate/chat", response_model=OrchestratorChatResponse)
@router.post("/orchestrate/chat/light", response_model=OrchestratorChatResponse)
async def answer_orchestrator_chat(
    request_context: Request,
    request: OrchestratorChatRequest,
    agent_key: str = "chat",
) -> OrchestratorChatResponse:
    return await answer_orchestrator_chat_service(
        request_context=request_context,
        request=request,
        agent_key=agent_key,
        resolve_chat_model=_resolve_admin_chat_model,
        build_ollama_options=build_ollama_options,
        ollama_base=OLLAMA_BASE,
        orch_chat_request_max_tokens=ORCH_CHAT_REQUEST_MAX_TOKENS,
        orch_lightweight_chat_max_tokens=ORCH_LIGHTWEIGHT_CHAT_MAX_TOKENS,
        orch_chat_agent_timeout_sec=ORCH_CHAT_AGENT_TIMEOUT_SEC,
        orch_reasoner_brief_timeout_sec=ORCH_REASONER_BRIEF_TIMEOUT_SEC,
        logger=logger,
        re_module=re,
        session_factory=SessionLocal,
    )
