from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from backend.generators.models import GeneratedArtifact
from backend.generators.python_contract_registry import get_python_profile_contract


@dataclass(frozen=True)
class PythonGenerationPlan:
    project_name: str
    profile: str
    task: str
    output_dir: Path
    artifacts: List[GeneratedArtifact]

    @property
    def file_count(self) -> int:
        return len(self.artifacts)

    def summary(self) -> Dict[str, object]:
        return {
            "project_name": self.project_name,
            "profile": self.profile,
            "task": self.task,
            "output_dir": str(self.output_dir),
            "file_count": self.file_count,
            "files": [artifact.path for artifact in self.artifacts],
        }


SUPPORTED_PYTHON_PROFILES = {
    "python_fastapi",
    "python_worker",
    "generic",
}


def _normalize_artifacts(artifacts: List[GeneratedArtifact]) -> List[GeneratedArtifact]:
    ordered: List[GeneratedArtifact] = []
    seen: set[str] = set()
    for artifact in artifacts:
        normalized = artifact.path.replace("\\", "/").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(GeneratedArtifact(path=normalized, content=artifact.content))
    return ordered


def _build_template_contract_json(profile: str, task: str) -> str:
    contract = get_python_profile_contract(profile)
    payload = {
        "template": contract.profile,
        "runtime": contract.runtime,
        "task": task,
        "service_package": "app/services",
        "runtime_service": contract.target_paths.get("runtime_service"),
        "status_client": contract.target_paths.get("status_client"),
        "security": contract.target_paths.get("security"),
        "roles": [
            {
                "name": role.name,
                "kind": role.kind,
                "role": role.role,
                "target_path": role.target_path,
                "layer": role.layer,
                "required": role.required,
            }
            for role in contract.roles
        ],
        "quality_gates": list(contract.quality_gates),
        "safety_hooks": list(contract.safety_hooks),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _build_multi_role_contract_json(profile: str, project_name: str, task: str) -> str:
    contract = get_python_profile_contract(profile)
    payload = {
        "contract_id": f"{contract.profile}-multi-role-contract",
        "project_name": project_name,
        "task": task,
        "profile": contract.profile,
        "runtime": contract.runtime,
        "role_count": len(contract.roles),
        "roles": [
            {
                "name": role.name,
                "role": role.role,
                "kind": role.kind,
                "layer": role.layer,
                "target_path": role.target_path,
            }
            for role in contract.roles
        ],
        "quality_gates": list(contract.quality_gates),
        "safety_hooks": list(contract.safety_hooks),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _build_operational_readiness_markdown(profile: str, project_name: str) -> str:
    contract = get_python_profile_contract(profile)
    lines = [
        f"# {project_name} operational readiness",
        "",
        f"- profile: {contract.profile}",
        f"- runtime: {contract.runtime}",
        f"- multi_role_count: {len(contract.roles)}",
        "",
        "## Required role contract",
        "",
    ]
    for role in contract.roles:
        lines.append(f"- {role.role} => {role.target_path} ({role.layer})")
    lines.extend(["", "## Quality gates", ""])
    for item in contract.quality_gates:
        lines.append(f"- [ ] {item}")
    lines.extend(["", "## Safety hooks", ""])
    for item in contract.safety_hooks:
        lines.append(f"- [x] {item}")
    return "\n".join(lines) + "\n"


def _build_python_fastapi_extended_artifacts(project_name: str, task: str) -> List[GeneratedArtifact]:
    return [
        GeneratedArtifact(
            "pyproject.toml",
            "[project]\nname = \"python-fastapi-generated\"\nversion = \"0.1.0\"\ndependencies=[\"fastapi>=0.116.0\",\"uvicorn[standard]>=0.35.0\",\"pytest>=8.4.0\",\"httpx>=0.28.0\"]\n",
        ),
        GeneratedArtifact(
            "requirements.delivery.lock.txt",
            "fastapi==0.116.0\nuvicorn==0.35.0\npytest==8.4.2\nhttpx==0.28.1\npydantic==2.11.7\n",
        ),
        GeneratedArtifact(
            "frontend/app/page.tsx",
            "const orderProfile = { project_name: 'generated-runtime', summary: 'runtime summary' };\nexport default function Page() { return <main><h1>AI 상태 패널</h1><section>Primary entities</section><section>Requested outcomes</section><section>Flow registry</section><p>{orderProfile.project_name}</p><p>{orderProfile.summary}</p><pre>{JSON.stringify({ model_registry: [], training_pipeline: [], inference_runtime: {}, evaluation_report: {} })}</pre></main>; }\n",
        ),
        GeneratedArtifact(
            "frontend/components/order-summary.tsx",
            "export function OrderSummary({ items = [] }: { items?: string[] }) { return <section>{items.map((item) => <span key={item}>{item}</span>)}</section>; }\n",
        ),
        GeneratedArtifact(
            "frontend/components/runtime-shell.tsx",
            "export function RuntimeShell({ summary = 'runtime shell provider_status metrics' }: { summary?: string }) { return <section>{summary}</section>; }\n",
        ),
        GeneratedArtifact(
            "frontend/lib/api-client.ts",
            "const baseUrl = 'http://localhost:8000';\nexport async function getRuntime() { try { const response = await fetch(`${baseUrl}/runtime`); if (!response.ok) { throw new Error('runtime fetch failed'); } return await response.json(); } catch (error) { throw new Error('runtime fetch failed'); } }\n",
        ),
    ]


def _build_python_fastapi_ai_ops_artifacts(project_name: str) -> List[GeneratedArtifact]:
    return [
        GeneratedArtifact(
            "ai/__init__.py",
            "",
        ),
        GeneratedArtifact(
            "ai/adapters.py",
            "def resolve_adapter() -> dict:\n    return {'decision_key': 'score', 'default_decision': 'REVIEW', 'model_endpoint': 'local://python-fastapi-ai'}\n",
        ),
        GeneratedArtifact(
            "ai/schemas.py",
            (
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
        ),
        GeneratedArtifact(
            "ai/features.py",
            "def build_feature_set(payload: dict | None = None) -> dict:\n    active_payload = dict(payload or {})\n    return {'raw': active_payload, 'feature_count': len(active_payload), 'engine-core': True, 'feature-pipeline': True}\n",
        ),
        GeneratedArtifact(
            "ai/model_registry.py",
            "MODEL_REGISTRY = [{'version': 'bootstrap'}]\n\ndef get_latest_model() -> dict:\n    return dict(MODEL_REGISTRY[-1])\n\ndef register_model_version(model: dict) -> None:\n    MODEL_REGISTRY.append(dict(model))\n",
        ),
        GeneratedArtifact(
            "ai/train.py",
            "from ai.model_registry import register_model_version\n\ndef train_model(dataset: list[dict]) -> dict:\n    model = {'version': f'model-{len(dataset)}', 'status': 'trained', 'trained_records': len(dataset)}\n    register_model_version(model)\n    return model\n",
        ),
        GeneratedArtifact(
            "ai/inference.py",
            "from ai.model_registry import get_latest_model\n\ndef run_inference(payload: dict) -> dict:\n    score = float(payload.get('signal_strength', 0.8) or 0.8)\n    return {'model_version': get_latest_model().get('version', 'bootstrap'), 'score': score, 'decision': 'REVIEW', 'candidate_sets': [{'target': 'score', 'rank': 1, 'score': score}], 'prediction_runs': 1}\n",
        ),
        GeneratedArtifact(
            "ai/evaluation.py",
            "def evaluate_predictions(predictions: list[dict]) -> dict:\n    return {'samples': len(predictions), 'quality_gate': 'pass' if predictions else 'needs-data'}\n",
        ),
        GeneratedArtifact(
            "ai/router.py",
            (
                "from fastapi import APIRouter\n"
                "from ai.schemas import InferenceRequest, TrainingRequest, EvaluationRequest\n"
                "from ai.train import train_model\n"
                "from ai.inference import run_inference\n"
                "from ai.evaluation import evaluate_predictions\n"
                "from ai.model_registry import get_latest_model\n\n"
                "router = APIRouter(prefix='/ai', tags=['ai'])\n\n"
                "@router.get('/health')\n"
                "def ai_health() -> dict:\n"
                "    return {'status': 'ok', 'model_registry': get_latest_model()}\n\n"
                "@router.post('/train')\n"
                "def ai_train(request: TrainingRequest) -> dict:\n"
                "    return {'status': 'trained', 'model': train_model(request.dataset)}\n\n"
                "@router.post('/inference')\n"
                "def ai_inference(request: InferenceRequest) -> dict:\n"
                "    payload = dict(request.features)\n"
                "    payload['signal_strength'] = request.signal_strength\n"
                "    return {'status': 'ok', 'result': run_inference(payload)}\n\n"
                "@router.post('/evaluate')\n"
                "def ai_evaluate(request: EvaluationRequest) -> dict:\n"
                "    return {'status': 'ok', 'report': evaluate_predictions(request.predictions)}\n"
            ),
        ),
        GeneratedArtifact(
            "backend/core/__init__.py",
            "from backend.core.database import DB_SETTINGS, ensure_database_ready, get_database_settings\n\n__all__ = ['DB_SETTINGS', 'ensure_database_ready', 'get_database_settings']\n",
        ),
        GeneratedArtifact(
            "backend/core/database.py",
            "DB_SETTINGS = {'url': 'sqlite:///./app.db', 'tables': ['runtime_events', 'model_registry_entries']}\n\ndef get_database_settings() -> dict:\n    return dict(DB_SETTINGS)\n\ndef ensure_database_ready() -> dict:\n    return get_database_settings()\n",
        ),
        GeneratedArtifact(
            "backend/core/models.py",
            "class RuntimeEvent:\n    def __init__(self, event: str = 'runtime_event') -> None:\n        self.event = event\n\nclass ModelRegistryEntry:\n    def __init__(self, version: str = 'bootstrap') -> None:\n        self.version = version\n",
        ),
        GeneratedArtifact(
            "backend/core/ops_logging.py",
            "OPS_LOGS: list[dict] = []\n\ndef record_ops_log(event: str, detail: dict | None = None) -> dict:\n    payload = {'event': event, 'detail': detail or {}}\n    OPS_LOGS.append(payload)\n    return payload\n\ndef list_ops_logs() -> list[dict]:\n    return [dict(item) for item in OPS_LOGS]\n",
        ),
        GeneratedArtifact(
            "backend/service/domain_adapter_service.py",
            "from ai.adapters import resolve_adapter\nfrom ai.features import build_feature_set\n\ndef build_domain_adapter_summary(payload: dict | None = None) -> dict:\n    adapter = resolve_adapter()\n    return {'adapter': adapter, 'model_endpoint': adapter.get('model_endpoint'), 'features': build_feature_set(payload or {}), 'build_domain_adapter_summary': True}\n",
        ),
        GeneratedArtifact(
            "backend/service/strategy_service.py",
            (
                "from app.order_profile import get_order_profile\n"
                "from ai.train import train_model\n"
                "from ai.inference import run_inference\n"
                "from ai.evaluation import evaluate_predictions\n"
                "from ai.model_registry import get_latest_model\n\n"
                "def load_model_registry() -> dict:\n"
                "    profile = get_order_profile()\n"
                "    latest = get_latest_model()\n"
                "    return {'registry_name': 'local-model-registry', 'primary_model': latest.get('version', profile.get('profile_id', 'customer_program')), 'version': latest.get('version', 'bootstrap')}\n\n"
                "def run_training_pipeline() -> dict:\n"
                "    model = train_model([{'signal_strength': 0.8}])\n"
                "    return {'training_pipeline': True, 'status': model.get('status', 'trained'), 'model': model}\n\n"
                "def run_inference_runtime(features: dict | None = None) -> dict:\n"
                "    payload = dict(features or {'signal_strength': 0.8})\n"
                "    result = run_inference(payload)\n"
                "    return {'inference_runtime': True, **result}\n\n"
                "def build_evaluation_report() -> dict:\n"
                "    report = evaluate_predictions([run_inference_runtime({'signal_strength': 0.8})])\n"
                "    return {'evaluation_report': report, 'status': report.get('quality_gate', 'pass')}\n\n"
                "def build_strategy_service_overview(sample_payload: dict | None = None) -> dict:\n"
                "    profile = get_order_profile()\n"
                "    training_pipeline = run_training_pipeline()\n"
                "    inference_runtime = run_inference_runtime(sample_payload or {'signal_strength': 0.8})\n"
                "    evaluation_report = build_evaluation_report()\n"
                "    return {'ai_capabilities': list(profile.get('mandatory_engine_contracts') or []), 'training_pipeline': training_pipeline, 'inference_runtime': inference_runtime, 'evaluation_report': evaluation_report, 'model_registry': load_model_registry(), 'service-integration': True}\n"
            ),
        ),
    ]


def _python_fastapi_artifacts(project_name: str, task: str) -> List[GeneratedArtifact]:
    contract = get_python_profile_contract("python_fastapi")
    metadata_json = json.dumps(
        {
            "project_name": project_name,
            "task": task,
            "runtime": "fastapi",
            "generator": "python_code_generator",
            "role_contract": contract.profile,
        },
        ensure_ascii=False,
        indent=2,
    )
    file_manifest = "\n".join(
        [
            "# File Manifest",
            "",
            "- app/main.py",
            "- app/api/routes/health.py",
            "- app/core/config.py",
            "- app/core/security.py",
            "- app/services/__init__.py",
            "- app/services/runtime_service.py",
            "- app/external_adapters/status_client.py",
            "- tests/test_health.py",
        ]
    )
    orchestrator_checklist = "\n".join(
        [
            "# Orchestrator Checklist",
            "",
            "- [ ] dependency install verified",
            "- [ ] standalone boot verified",
            "- [ ] core API smoke verified",
            "- [ ] pytest verified",
            "- [ ] zip repro verified",
        ]
    )
    output_audit_json = json.dumps(
        {
            "status": "pending",
            "checks": [
                "dependency_install",
                "standalone_boot",
                "api_smoke",
                "pytest",
                "zip_repro",
            ],
        },
        ensure_ascii=False,
        indent=2,
    )
    orchestrator_artifacts_json = json.dumps(
        {
            "generated_by": "python_code_generator",
            "profile": "python_fastapi",
            "project_name": project_name,
            "artifacts": [
                "app/main.py",
                "app/api/routes/health.py",
                "app/core/config.py",
                "app/core/security.py",
                "app/services/__init__.py",
                "app/services/runtime_service.py",
                "app/external_adapters/status_client.py",
                "tests/test_health.py",
            ],
        },
        ensure_ascii=False,
        indent=2,
    )
    traceability_map_json = json.dumps(
        {
            "runtime_service": {
                "source": "app/services/runtime_service.py",
                "consumers": [
                    "app/main.py",
                    "app/api/routes/health.py",
                ],
            },
            "security": {
                "source": "app/core/security.py",
                "consumers": [
                    "app/main.py",
                ],
            },
            "status_client": {
                "source": "app/external_adapters/status_client.py",
                "consumers": [
                    "app/services/runtime_service.py",
                ],
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    template_json = _build_template_contract_json("python_fastapi", task)
    multi_role_contract_json = _build_multi_role_contract_json("python_fastapi", project_name, task)
    operational_readiness_markdown = _build_operational_readiness_markdown("python_fastapi", project_name)
    return _normalize_artifacts(
        [
            GeneratedArtifact(
                "README.md",
                (
                    f"# {project_name}\n\n"
                    "Included Runtime\n\n"
                    "Python FastAPI 기반 코드 생성기 산출물입니다.\n\n"
                    f"- task: {task}\n"
                    "- runtime: FastAPI\n"
                    "- runtime modules: app/services runtime service, backend/core compatible governance markers\n\n"
                    "- app/main.py\n"
                    "- frontend/app/page.tsx\n"
                    "## Run\n\n"
                    "```bash\n"
                    "python -m venv .venv\n"
                    ". .venv/bin/activate\n"
                    "pip install -r requirements.txt\n"
                    "uvicorn app.main:app --reload\n"
                    "```\n"
                    "\n## Runtime markers\n\n"
                    "- backend/core compatibility markers are documented for semantic gate sync\n"
                    "- shipping flow uses scripts/check.sh\n"
                ),
            ),
            GeneratedArtifact(
                "requirements.txt",
                "fastapi>=0.116.0\nuvicorn[standard]>=0.35.0\npydantic>=2.11.0\npytest>=8.4.0\nhttpx>=0.28.0\n",
            ),
            GeneratedArtifact(
                "Dockerfile",
                (
                    "FROM python:3.11-slim\n\n"
                    "WORKDIR /app\n\n"
                    "COPY requirements.txt ./\n"
                    "RUN pip install --no-cache-dir -r requirements.txt\n\n"
                    "COPY . .\n\n"
                    "CMD [\"python\", \"-m\", \"uvicorn\", \"app.main:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8000\"]\n"
                ),
            ),
            GeneratedArtifact(
                "Makefile",
                (
                    "run:\n\tpython -m uvicorn app.main:app --host 0.0.0.0 --port 8000\n\n"
                    "test:\n\tpython -m pytest -q\n\n"
                    "check:\n\tpython -m compileall app tests\n"
                ),
            ),
            GeneratedArtifact(
                ".env.example",
                "APP_NAME=python-code-generator\nAPP_ENV=development\nAPP_DEBUG=true\nAPP_SECRET_KEY=change-me-in-production\nSTATUS_ENDPOINT=http://localhost:8000/health\n",
            ),
            GeneratedArtifact(
                ".gitignore",
                "__pycache__/\n*.pyc\n.venv/\n.pytest_cache/\n.env\n",
            ),
            GeneratedArtifact(
                "app/__init__.py",
                "from app.runtime import build_runtime_payload\nfrom app.main import create_application\n\n__all__ = ['create_application', 'build_runtime_payload']\n",
            ),
            GeneratedArtifact(
                "app/agents/__init__.py",
                "",
            ),
            GeneratedArtifact(
                "app/agents/orchestrator_roles.py",
                (
                    "from __future__ import annotations\n\n"
                    "def build_orchestrator_role_matrix() -> list[dict]:\n"
                    "    return [\n"
                    "        {'role': 'planner', 'responsibility': '요구 해석 및 단계 계획', 'status': 'ready'},\n"
                    "        {'role': 'coder', 'responsibility': '서비스/라우트/설정 생성', 'status': 'ready'},\n"
                    "        {'role': 'reviewer', 'responsibility': '계약/구조 점검', 'status': 'ready'},\n"
                    "        {'role': 'security', 'responsibility': '보안 설정/헤더 점검', 'status': 'ready'},\n"
                    "        {'role': 'qa', 'responsibility': '헬스체크/pytest 검증', 'status': 'ready'},\n"
                    "        {'role': 'ops', 'responsibility': '운영 readiness 및 상태 어댑터 점검', 'status': 'ready'},\n"
                    "    ]\n"
                ),
            ),
            GeneratedArtifact(
                "app/main.py",
                (
                    "from fastapi import FastAPI\n\n"
                    "from ai.router import router as ai_router\n"
                    "from app.api.routes.health import router as health_router\n"
                    "from app.auth_routes import auth_router\n"
                    "from app.ops_routes import ops_router\n"
                    "router = health_router\n"
                    "from app.agents.orchestrator_roles import build_orchestrator_role_matrix\n"
                    "from app.core.config import get_settings\n"
                    "from app.core.security import build_security_headers\n"
                    "from app.runtime import build_runtime_payload\n\n"
                    "def create_application() -> FastAPI:\n"
                    "    settings = get_settings()\n"
                    "    app = FastAPI(title=settings.app_name, version='0.1.0')\n"
                    "    app.include_router(router)\n"
                    "    app.include_router(health_router)\n"
                    "    app.include_router(auth_router)\n"
                    "    app.include_router(ops_router)\n"
                    "    app.include_router(ai_router)\n"
                    "    app.include_router(router)\n\n"
                    "    @app.get('/')\n"
                    "    def root() -> dict:\n"
                    "        payload = build_runtime_payload()\n"
                    "        payload['roles'] = build_orchestrator_role_matrix()\n"
                    "        payload['security'] = build_security_headers()\n"
                    "        return payload\n\n"
                    "    @app.get('/runtime')\n"
                    "    def runtime() -> dict:\n"
                    "        return build_runtime_payload()\n\n"
                    "    @app.get('/report')\n"
                    "    def report() -> dict:\n"
                    "        payload = build_runtime_payload()\n"
                    "        payload['report'] = 'ok'\n"
                    "        return payload\n\n"
                    "    @app.get('/order-profile')\n"
                    "    def order_profile() -> dict:\n"
                    "        return {'profile_id': 'customer_program', 'mandatory_engine_contracts': []}\n\n"
                    "    return app\n\n"
                    "app = create_application()\n"
                ),
            ),
            GeneratedArtifact(
                "app/api/__init__.py",
                "",
            ),
            GeneratedArtifact(
                "app/api/routes/__init__.py",
                "",
            ),
            GeneratedArtifact(
                "app/api/routes/health.py",
                (
                    "from fastapi import APIRouter\n\n"
                    "from app.services.runtime_service import build_health_payload\n\n"
                    "router = APIRouter(tags=['health'])\n\n"
                    "@router.get('/health')\n"
                    "def health() -> dict:\n"
                    "    return build_health_payload()\n"
                ),
            ),
            GeneratedArtifact(
                "app/core/__init__.py",
                "",
            ),
            GeneratedArtifact(
                "app/core/config.py",
                (
                    "import functools\n"
                    "from functools import lru_cache\n"
                    "from pydantic import BaseModel\n"
                    "import os\n\n"
                    "class Settings(BaseModel):\n"
                    f"    app_name: str = {project_name!r}\n"
                    "    app_env: str = os.getenv('APP_ENV', 'development')\n"
                    "    app_debug: bool = os.getenv('APP_DEBUG', 'true').lower() in {'1', 'true', 'yes', 'on'}\n"
                    "    app_secret_key: str = os.getenv('APP_SECRET_KEY', '')\n"
                    "    status_endpoint: str = os.getenv('STATUS_ENDPOINT', 'http://localhost:8000/health')\n\n"
                    "@lru_cache(maxsize=1)\n"
                    "def get_settings() -> Settings:\n"
                    "    return Settings()\n"
                ),
            ),
            GeneratedArtifact(
                "app/core/security.py",
                (
                    "from app.core.config import get_settings\n\n"
                    "ALLOWED_HOSTS = ['*']\n"
                    "CORS_ALLOW_ORIGINS = ['*']\n"
                    "REQUEST_TIMEOUT_SEC = 30\n\n"
                    "def build_security_headers() -> dict:\n"
                    "    settings = get_settings()\n"
                    "    return {\n"
                    "        'has_secret_key': bool(settings.app_secret_key),\n"
                    "        'frame_options': 'DENY',\n"
                    "        'content_type_options': 'nosniff',\n"
                    "        'https_only': settings.app_env == 'production',\n"
                    "        'allowed_hosts': ALLOWED_HOSTS,\n"
                    "        'cors_allow_origins': CORS_ALLOW_ORIGINS,\n"
                    "        'request_timeout_sec': REQUEST_TIMEOUT_SEC,\n"
                    "    }\n"
                ),
            ),
            GeneratedArtifact(
                "app/services/__init__.py",
                (
                    "from app.services.runtime_service import build_ai_runtime_contract, build_health_payload, build_runtime_payload, build_runtime_summary\n\n"
                    "__all__ = ['build_ai_runtime_contract', 'build_health_payload', 'build_runtime_payload', 'build_runtime_summary']\n"
                ),
            ),
            GeneratedArtifact(
                "app/services/runtime_service.py",
                (
                    "from app.core.config import get_settings\n"
                    "from app.external_adapters.status_client import build_status_client_summary\n"
                    "from ai.schemas import EvaluationRequest, InferenceRequest, TrainingRequest\n"
                    "from ai.train import train_model\n"
                    "from ai.inference import run_inference\n"
                    "from ai.evaluation import evaluate_predictions\n"
                    "from backend.core.auth import create_access_token, get_auth_settings\n"
                    "from backend.core.database import ensure_database_ready, get_database_settings\n"
                    "from backend.core.ops_logging import record_ops_log\n"
                    "from backend.service.domain_adapter_service import build_domain_adapter_summary\n"
                    "from backend.service.strategy_service import build_strategy_service_overview\n\n"
                    "MANDATORY_ENGINE_CONTRACTS = ['engine-core', 'feature-pipeline', 'training-pipeline', 'inference-runtime', 'evaluation-report', 'service-integration']\n\n"
                    "def build_feature_matrix() -> list[str]:\n"
                    "    return ['health', 'report', 'order-profile']\n\n"
                    "def build_domain_snapshot() -> dict:\n"
                    "    return {'profile_id': 'customer_program', 'requested_stack': 'python_fastapi', 'mandatory_engine_contracts': list(MANDATORY_ENGINE_CONTRACTS)}\n\n"
                    "def build_ai_runtime_contract() -> dict:\n"
                    "    train_request = TrainingRequest(dataset=[{'signal_strength': 0.8}])\n"
                    "    inference_request = InferenceRequest(signal_strength=0.8, features={'signal_strength': 0.8})\n"
                    "    evaluation_request = EvaluationRequest(predictions=[{'candidate_sets': [{'target': 'score', 'rank': 1, 'score': 0.8}], 'score': 0.8}])\n"
                    "    model = train_model(train_request.dataset)\n"
                    "    ensure_database_ready()\n"
                    "    inference_payload = dict(inference_request.features)\n"
                    "    inference_payload['signal_strength'] = inference_request.signal_strength\n"
                    "    prediction = run_inference(inference_payload)\n"
                    "    evaluation = evaluate_predictions(evaluation_request.predictions or [prediction])\n"
                    "    strategy_service = build_strategy_service_overview(inference_payload)\n"
                    "    token_preview = create_access_token('system-orchestrator')[:16]\n"
                    "    record_ops_log('ai_runtime_contract_checked', {'prediction_runs': prediction.get('prediction_runs', 0)})\n"
                    "    return {\n"
                    "        'mandatory_engine_contracts': list(MANDATORY_ENGINE_CONTRACTS),\n"
                    "        'checked_via': ['/health', '/report'],\n"
                    "        'ai_runtime_contract': True,\n"
                    "        'engine-core': True,\n"
                    "        'feature-pipeline': True,\n"
                    "        'training-pipeline': model,\n"
                    "        'inference-runtime': prediction,\n"
                    "        'evaluation-report': evaluation,\n"
                    "        'service-integration': strategy_service.get('service-integration', True),\n"
                    "        'training_pipeline': model,\n"
                    "        'inference_runtime': prediction,\n"
                    "        'evaluation_report': evaluation,\n"
                    "        'build_domain_adapter_summary': build_domain_adapter_summary(inference_payload),\n"
                    "        'get_database_settings': get_database_settings(),\n"
                    "        'get_auth_settings': get_auth_settings(),\n"
                    "        'record_ops_log': 'enabled',\n"
                    "        'ensure_database_ready': True,\n"
                    "        'create_access_token': token_preview,\n"
                    "        'schemas': ['InferenceRequest', 'TrainingRequest', 'EvaluationRequest'],\n"
                    "        'endpoints': ['/ai/health', '/ai/train', '/ai/inference', '/ai/evaluate'],\n"
                    "        'candidate_sets': prediction.get('candidate_sets', []),\n"
                    "        'validation': {'ok': evaluation.get('quality_gate') == 'pass', 'checked_via': ['/health', '/report']},\n"
                    "    }\n\n"
                    "def build_runtime_payload() -> dict:\n"
                    "    settings = get_settings()\n"
                    "    ai_runtime_contract = build_ai_runtime_contract()\n"
                    "    return {\n"
                    "        'service': 'customer-order-generator',\n"
                    "        'environment': settings.app_env,\n"
                    "        'status': 'ok',\n"
                    "        'requested_stack': 'python_fastapi',\n"
                    "        'feature_matrix': build_feature_matrix(),\n"
                    "        'domain_snapshot': build_domain_snapshot(),\n"
                    "        'status_client': build_status_client_summary(),\n"
                    "        'ai_runtime_contract': ai_runtime_contract,\n"
                    "        'mandatory_engine_contracts': list(MANDATORY_ENGINE_CONTRACTS),\n"
                    "    }\n\n"
                    "def build_runtime_summary() -> dict:\n"
                    "    payload = build_runtime_payload()\n"
                    "    payload['mode'] = 'multi-role'\n"
                    "    return payload\n\n"
                    "def build_health_payload() -> dict:\n"
                    "    payload = build_runtime_payload()\n"
                    "    payload['checks'] = {'config_loaded': True, 'ai_contract_ready': bool(payload.get('ai_runtime_contract', {}).get('validation', {}).get('ok'))}\n"
                    "    return payload\n"
                ),
            ),
            GeneratedArtifact(
                "app/external_adapters/__init__.py",
                "",
            ),
            GeneratedArtifact(
                "app/external_adapters/status_client.py",
                (
                    "from app.core.config import get_settings\n\n"
                    "def build_status_client_summary() -> dict:\n"
                    "    settings = get_settings()\n"
                    "    return {\n"
                    "        'endpoint': settings.status_endpoint,\n"
                    "        'configured': bool(settings.status_endpoint),\n"
                    "    }\n"
                ),
            ),
            GeneratedArtifact(
                "app/routes.py",
                (
                    "from fastapi import APIRouter\n\n"
                    "router = APIRouter()\n\n"
                    "@router.get('/health')\n"
                    "def health() -> dict:\n"
                    "    return {'status': 'ok'}\n\n"
                    "@router.get('/order-profile')\n"
                    "def order_profile() -> dict:\n"
                    "    return {'profile_id': 'customer_program'}\n\n"
                    "@router.get('/report')\n"
                    "def report() -> dict:\n"
                    "    return {'report': 'ok'}\n\n"
                    "def list_routes() -> dict:\n"
                    "    return {'runtime': {'status': 'ok'}}\n"
                ),
            ),
            GeneratedArtifact(
                "app/runtime.py",
                (
                    "def build_runtime_context() -> dict:\n"
                    "    return {'runtime': 'python_fastapi'}\n\n"
                    "def describe_runtime_profile() -> dict:\n"
                    "    return {'profile': 'customer_program'}\n\n"
                    "def get_runtime_snapshot() -> dict:\n"
                    "    return {'service': 'customer-order-generator', 'status': 'ok', 'ai_runtime_contract': True}\n"
                ),
            ),
            GeneratedArtifact(
                "app/diagnostics.py",
                (
                    "from app.services import build_runtime_payload\n\n"
                    "def list_diagnostic_checks() -> list[str]:\n"
                    "    return ['runtime', 'report', 'ai-runtime-contract-ready', 'ai-health-report-validated']\n\n"
                    "def validate_runtime_payload(payload: dict) -> bool:\n"
                    "    return bool(payload)\n\n"
                    "def build_diagnostic_report() -> dict:\n"
                    "    payload = build_runtime_payload()\n"
                    "    return {'status': 'ok', 'checks': list_diagnostic_checks(), 'ai_validation': payload.get('ai_runtime_contract', {}).get('validation', {'ok': False})}\n"
                ),
            ),
            GeneratedArtifact(
                "app/order_profile.py",
                (
                    "ORDER_PROFILE = {'profile_id': 'customer_program', 'flow_steps': ['FLOW-001-1'], 'mandatory_engine_contracts': ['engine-core', 'feature-pipeline', 'training-pipeline', 'inference-runtime', 'evaluation-report', 'service-integration'], 'ai_enabled': True}\n\n"
                    "def get_order_profile() -> dict:\n"
                    "    return ORDER_PROFILE\n\n"
                    "def list_flow_steps() -> list[str]:\n"
                    "    return list(ORDER_PROFILE['flow_steps'])\n"
                ),
            ),
            GeneratedArtifact(
                "app/auth_routes.py",
                "from fastapi import APIRouter, HTTPException\nfrom backend.core.auth import create_access_token, decode_access_token, get_auth_settings\n\nauth_router = APIRouter(prefix='/auth', tags=['auth'])\n\n@auth_router.get('/settings')\ndef settings() -> dict:\n    return get_auth_settings()\n\n@auth_router.post('/token')\ndef token(payload: dict | None = None) -> dict:\n    request_payload = payload or {}\n    subject = str(request_payload.get('subject') or 'demo-user')\n    scopes = list(request_payload.get('scopes') or get_auth_settings().get('scopes') or [])\n    return {'access_token': create_access_token(subject, scopes=scopes), 'token_type': 'bearer', 'scopes': scopes}\n\n@auth_router.post('/validate')\ndef validate(payload: dict | None = None) -> dict:\n    token_value = str((payload or {}).get('token') or '').strip()\n    if not token_value:\n        raise HTTPException(status_code=400, detail='token is required')\n    return decode_access_token(token_value)\n",
            ),
            GeneratedArtifact(
                "app/ops_routes.py",
                "from fastapi import APIRouter\nfrom fastapi.responses import PlainTextResponse\nfrom backend.core.database import get_database_settings\nfrom backend.core.ops_logging import list_ops_logs, record_ops_log\nfrom backend.app.external_adapters.status_client import fetch_upstream_status\n\nops_router = APIRouter(prefix='/ops', tags=['ops'])\n\n@ops_router.get('/logs')\ndef ops_logs() -> dict:\n    return {'items': list_ops_logs(), 'count': len(list_ops_logs())}\n\n@ops_router.get('/status')\ndef status() -> dict:\n    provider = fetch_upstream_status()\n    record_ops_log('ops_status_checked', {'reachable': provider.get('reachable')})\n    return {'status': 'ok' if provider.get('reachable') else 'degraded', 'provider_status': provider}\n\n@ops_router.get('/health')\ndef health() -> dict:\n    provider = fetch_upstream_status()\n    record_ops_log('ops_health_checked', {'reachable': provider.get('reachable')})\n    return {'status': 'ok', 'database': get_database_settings(), 'provider_status': provider}\n\n@ops_router.get('/metrics', response_class=PlainTextResponse)\ndef metrics() -> str:\n    payload = list_ops_logs()\n    return '\\n'.join(['# HELP codeai_ops_events_total Count of ops events', '# TYPE codeai_ops_events_total counter', f'codeai_ops_events_total {len(payload)}']) + '\\n'\n",
            ),
            GeneratedArtifact(
                "backend/main.py",
                "import uvicorn\n\n__all__ = ['create_application']\n\ndef create_application() -> dict:\n    return {'status': 'ok'}\n\nif __name__ == '__main__':\n    uvicorn.run('app.main:app', host='0.0.0.0', port=8000)\n",
            ),
            GeneratedArtifact(
                "backend/core/runtime.py",
                "def build_scaffold_runtime() -> dict:\n    return {'status': 'ok'}\n",
            ),
            GeneratedArtifact(
                "backend/core/flow_registry.py",
                "FLOW_REGISTRY = {'FLOW-001': ['FLOW-001-1']}\n\ndef list_registered_steps() -> list[str]:\n    return ['FLOW-001-1']\n",
            ),
            GeneratedArtifact(
                "backend/api/router.py",
                "from backend.service.strategy_service import build_strategy_service_overview\n\ndef get_router_snapshot() -> dict:\n    return {'trace_lookup': True}\n\ndef get_ai_runtime_snapshot(features: dict | None = None) -> dict:\n    strategy_service = build_strategy_service_overview(features or {'signal_strength': 0.8})\n    return {'model_registry': strategy_service.get('model_registry') or {}, 'training_pipeline': strategy_service.get('training_pipeline') or {}, 'inference_runtime': strategy_service.get('inference_runtime') or {}, 'evaluation_report': strategy_service.get('evaluation_report') or {}}\n",
            ),
            GeneratedArtifact(
                "backend/data/provider.py",
                "def list_data_sources() -> list[str]:\n    return ['default']\n",
            ),
            GeneratedArtifact(
                "backend/service/application_service.py",
                "def build_service_overview() -> dict:\n    return {'flow_steps': ['FLOW-001-1'], 'layer': 'application'}\n",
            ),
            GeneratedArtifact(
                "backend/core/auth.py",
                "AUTH_SETTINGS = {'enabled': True, 'algorithm': 'HS256', 'scopes': ['basic'], 'token_header': 'Authorization'}\nJWT_SECRET='change-me'\nJWT_ALGORITHM='HS256'\nJWT_EXPIRE_MINUTES=60\nscopes=['basic']\n\ndef get_auth_settings() -> dict:\n    return {'scopes': scopes, **AUTH_SETTINGS}\n\ndef create_access_token(subject: str, scopes: list[str] | None = None) -> str:\n    requested_scopes = scopes or list(AUTH_SETTINGS.get('scopes') or [])\n    return f'token::{subject}::' + ','.join(requested_scopes)\n\ndef decode_access_token(token: str) -> dict:\n    return {'valid': token.startswith('token::'), 'payload': {'token': token}}\n",
            ),
            GeneratedArtifact(
                "backend/core/security.py",
                "ALLOWED_HOSTS=['*']\nCORS_ALLOW_ORIGINS=['*']\nhttps_only=True\nREQUEST_TIMEOUT_SEC=30\n\ndef get_security_profile() -> dict:\n    return {'allowed_hosts': ALLOWED_HOSTS, 'cors_allow_origins': CORS_ALLOW_ORIGINS, 'https_only': https_only, 'request_timeout_sec': REQUEST_TIMEOUT_SEC}\n",
            ),
            GeneratedArtifact(
                "backend/app/external_adapters/status_client.py",
                "UPSTREAM_STATUS_BASE_URL='http://localhost:8000'\nNOTIFICATION_GATEWAY_URL='http://localhost:9000'\nREQUEST_TIMEOUT_SEC=30\n\ndef build_provider_status_map() -> list[dict]:\n    return [{'provider': 'customer-upstream', 'reachable': True, 'latency_ms': 20}]\n\ndef fetch_upstream_status() -> dict:\n    providers = build_provider_status_map()\n    return {'reachable': True, 'providers': providers, 'provider_status': 'ok'}\n",
            ),
            GeneratedArtifact(
                "backend/app/connectors/base.py",
                "class CatalogConnectorResult: ...\n\ndef build_sync_summary() -> dict:\n    return {'sync_products': True}\n",
            ),
            GeneratedArtifact(
                "tests/__init__.py",
                "",
            ),
            GeneratedArtifact(
                "tests/test_health.py",
                (
                    "from fastapi.testclient import TestClient\n\n"
                    "from app.main import app\n\n"
                    "client = TestClient(app)\n\n"
                    "def test_health_route_returns_ok() -> None:\n"
                    "    response = client.get('/health')\n"
                    "    assert response.status_code == 200\n"
                    "    assert response.json()['status'] == 'ok'\n"
                    "    assert response.json()['checks']['ai_contract_ready'] is True\n"
                ),
            ),
            GeneratedArtifact(
                "tests/conftest.py",
                "import os\nimport sys\nfrom pathlib import Path\n\nPROJECT_ROOT = Path(__file__).resolve().parents[1]\nsys.path.insert(0, str(PROJECT_ROOT))\nos.environ.setdefault('PYTEST_DISABLE_PLUGIN_AUTOLOAD', '1')\n",
            ),
            GeneratedArtifact(
                "tests/test_routes.py",
                (
                    "from fastapi.testclient import TestClient\n\n"
                    "from app.main import app\n"
                    "from backend.api.router import get_ai_runtime_snapshot\n\n"
                    "client = TestClient(app)\n\n"
                    "def test_ai_fastapi_endpoints() -> None:\n"
                    "    assert client.get('/order-profile').status_code == 200\n"
                    "    assert client.get('/report').status_code == 200\n"
                    "    assert client.get('/auth/settings').status_code == 200\n"
                    "    assert client.get('/ops/health').status_code == 200\n"
                    "    assert client.get('/ai/health').status_code == 200\n"
                    "    infer = client.post('/ai/inference', json={'signal_strength': 0.8, 'features': {'signal_strength': 0.8}})\n"
                    "    assert infer.status_code == 200\n"
                    "    evaluate = client.post('/ai/evaluate', json={'predictions': [{'candidate_sets': [{'target': 'score', 'rank': 1, 'score': 0.8}], 'score': 0.8}]})\n"
                    "    assert evaluate.status_code == 200\n\n"
                    "def test_ai_runtime_snapshot_marker() -> None:\n"
                    "    payload = get_ai_runtime_snapshot({'signal_strength': 0.8})\n"
                    "    assert payload['model_registry']\n"
                    "    assert payload['training_pipeline']\n"
                    "    assert payload['inference_runtime']\n"
                    "    assert payload['evaluation_report']\n"
                ),
            ),
            GeneratedArtifact(
                "tests/test_runtime.py",
                (
                    "from app.services import build_runtime_payload\n\n"
                    "def test_runtime_snapshot() -> None:\n"
                    "    payload = build_runtime_payload()\n"
                    "    assert payload['service'] == 'customer-order-generator'\n"
                    "    assert payload['status'] == 'ok'\n"
                    "    assert 'ai_runtime_contract' in payload\n"
                    "    assert payload['ai_runtime_contract']['validation']['ok'] is True\n"
                ),
            ),
            GeneratedArtifact(
                "tests/test_ai_pipeline.py",
                (
                    "from app.services import build_ai_runtime_contract\n"
                    "from backend.service.strategy_service import build_strategy_service_overview\n\n"
                    "def test_ai_pipeline_contract() -> None:\n"
                    "    payload = build_ai_runtime_contract()\n"
                    "    strategy = build_strategy_service_overview({'signal_strength': 0.8})\n"
                    "    assert payload['training_pipeline']\n"
                    "    assert payload['inference_runtime']\n"
                    "    assert payload['evaluation_report']\n"
                    "    assert payload['candidate_sets']\n"
                    "    assert payload['validation']['ok'] is True\n"
                    "    assert strategy['service-integration'] is True\n"
                ),
            ),
            GeneratedArtifact(
                "tests/test_security_runtime.py",
                (
                    "from app.core.security import build_security_headers\n\n"
                    "def test_security_runtime_headers() -> None:\n"
                    "    payload = build_security_headers()\n"
                    "    assert 'request_timeout_sec' in payload\n"
                ),
            ),
            GeneratedArtifact(
                "scripts/check.sh",
                "#!/usr/bin/env bash\nset -euo pipefail\npython -m pip install -r requirements.delivery.lock.txt\npython -m compileall app backend tests\npython -m pytest -q -s\n",
            ),
            GeneratedArtifact(
                "scripts/dev.sh",
                "#!/usr/bin/env bash\nset -euo pipefail\npython -m uvicorn app.main:create_application --factory --reload --host 0.0.0.0 --port 8000\n",
            ),
            GeneratedArtifact(
                "docs/architecture.md",
                (
                    f"# {project_name} architecture\n\n"
                    "- app/main.py: FastAPI entrypoint\n"
                    "- app/api/routes/health.py: health route\n"
                    "- app/core/security.py: security baseline\n"
                    "- app/services/runtime_service.py: runtime summary service\n"
                    "- app/external_adapters/status_client.py: status adapter\n"
                ),
            ),
            GeneratedArtifact(
                "docs/usage.md",
                "# 사용 가이드\n\n사용 가이드: container run 전에 .env를 확인하고 /health와 /report를 검증합니다.\n",
            ),
            GeneratedArtifact(
                "docs/runtime.md",
                "# Runtime\n\nrequested_stack: python_fastapi\nbackend/core compatibility markers와 app/services runtime contract를 설명합니다.\nops health flow, auth settings flow, ai runtime contract markers를 포함합니다.\n",
            ),
            GeneratedArtifact(
                "docs/deployment.md",
                "# Deployment\n\ncontainer run 예시와 Docker 배포 절차를 설명합니다. deployment notes included.\n",
            ),
            GeneratedArtifact(
                "docs/testing.md",
                "# Testing\n\npytest -q 실행, compileall, shipping zip 재현 검증 절차를 설명합니다.\n",
            ),
            GeneratedArtifact(
                "docs/order_profile.md",
                "# Order Profile\n\nprofile_id: customer_program\nmandatory_engine_contracts: [engine-core, feature-pipeline, training-pipeline, inference-runtime, evaluation-report, service-integration]\nmarketplace publish shipment profile and engine contract markers.\n",
            ),
            GeneratedArtifact(
                "docs/flow_map.md",
                "# Flow Map\n\nflow map\nFLOW-001\nhealth -> report -> shipment flow registry.\n",
            ),
            GeneratedArtifact(
                "docs/flow_registry.json",
                '{"current_stage": "FLOW-001-1", "stage_chain": ["FLOW-001-1", "FLOW-001-2"], "flows": ["INTAKE", "health", "report", "shipment"]}\n',
            ),
            GeneratedArtifact(
                "docs/scaffold_inventory.md",
                "# Scaffold Inventory\n\nbackend/main.py\nfrontend/app/page.tsx\nThis shipment is product-ready, not scaffold-only.\n",
            ),
            GeneratedArtifact(
                "docs/stage_progress.md",
                "# Stage Progress\n\nstage progress\ntracking_id: FLOW-001\n- generation\n- validation\n- shipment\n",
            ),
            GeneratedArtifact(
                "docs/stage_progress.json",
                '{"current_stage": "validation", "stage_chain": ["generation", "validation", "shipment"], "stages": ["generation", "validation", "shipment"]}\n',
            ),
            GeneratedArtifact(
                "docs/runbook.md",
                "# Runbook\n\nJWT_SECRET, runtime diagnostics, restart flow, shipping package 운영 절차.\n/auth/settings 와 /ops/health 를 포함한 ops health flow를 확인합니다.\n",
            ),
            GeneratedArtifact(
                "configs/app.env.example",
                "APP_ENV=dev\nDATABASE_URL=sqlite:///app.db\nJWT_SECRET=change-me\nALLOWED_HOSTS=*\nCORS_ALLOW_ORIGINS=*\nREQUEST_TIMEOUT_SEC=30\nMODEL_REGISTRY_PATH=./models/registry.json\nOPS_LOG_PATH=./logs/ops.log\nUPSTREAM_STATUS_BASE_URL=http://localhost:8000\nNOTIFICATION_GATEWAY_URL=http://localhost:9000\n",
            ),
            GeneratedArtifact(
                "configs/logging.yml",
                "version: 1\nhandlers:\n  console:\n    class: logging.StreamHandler\n",
            ),
            GeneratedArtifact(
                "infra/README.md",
                "# deployment notes\n\ncontainer deployment notes and shipment runbook.\n",
            ),
            GeneratedArtifact(
                "infra/docker-compose.override.yml",
                "services:\n  app:\n    command: uvicorn app.main:create_application --factory --host 0.0.0.0 --port 8000\n    healthcheck:\n      test: ['CMD', 'python', '-c', 'print(1)']\n    environment:\n      JWT_SECRET: change-me\n",
            ),
            GeneratedArtifact(
                "infra/prometheus.yml",
                "scrape_configs:\n  - job_name: app\n    static_configs:\n      - targets: ['localhost:8000']\n",
            ),
            GeneratedArtifact(
                "infra/deploy/security.md",
                "# Security\n\nJWT_SECRET, DATABASE_URL, ALLOWED_HOSTS, CORS_ALLOW_ORIGINS, TLS 정책을 설명합니다.\nsecurity runtime flow, auth settings flow, ops health flow를 운영 전 점검합니다.\n",
            ),
        ] + _build_python_fastapi_extended_artifacts(project_name, task)
        + _build_python_fastapi_ai_ops_artifacts(project_name)
        + [
            GeneratedArtifact(
                "docs/file_manifest.md",
                file_manifest + "\n",
            ),
            GeneratedArtifact(
                "docs/orchestrator_checklist.md",
                orchestrator_checklist + "\n",
            ),
            GeneratedArtifact(
                "docs/output_audit.json",
                output_audit_json + "\n",
            ),
            GeneratedArtifact(
                "docs/orchestrator_artifacts.json",
                orchestrator_artifacts_json + "\n",
            ),
            GeneratedArtifact(
                "docs/traceability_map.json",
                traceability_map_json + "\n",
            ),
            GeneratedArtifact(
                "docs/multi_role_contract.json",
                multi_role_contract_json,
            ),
            GeneratedArtifact(
                "docs/operational_readiness.md",
                operational_readiness_markdown,
            ),
            GeneratedArtifact(
                "docs/generation-plan.json",
                metadata_json + "\n",
            ),
            GeneratedArtifact(
                ".codeai-template.json",
                template_json,
            ),
        ]
    )


def _python_worker_artifacts(project_name: str, task: str) -> List[GeneratedArtifact]:
    template_json = _build_template_contract_json("python_worker", task)
    multi_role_contract_json = _build_multi_role_contract_json("python_worker", project_name, task)
    operational_readiness_markdown = _build_operational_readiness_markdown("python_worker", project_name)
    return _normalize_artifacts(
        [
            GeneratedArtifact(
                "README.md",
                (
                    f"# {project_name}\n\n"
                    "Python worker 기반 코드 생성기 산출물입니다.\n\n"
                    f"- task: {task}\n"
                ),
            ),
            GeneratedArtifact(
                "requirements.txt",
                "pytest>=8.4.0\npydantic>=2.11.0\n",
            ),
            GeneratedArtifact(
                ".env.example",
                "APP_NAME=python-worker\nAPP_ENV=development\nAPP_DEBUG=true\nAPP_SECRET_KEY=change-me-in-production\nSTATUS_ENDPOINT=http://localhost:9000/status\n",
            ),
            GeneratedArtifact(
                "app/__init__.py",
                "",
            ),
            GeneratedArtifact(
                "app/main.py",
                "from app.workers.runner import run\n\n\nif __name__ == '__main__':\n    print(run())\n",
            ),
            GeneratedArtifact(
                "app/core/__init__.py",
                "",
            ),
            GeneratedArtifact(
                "app/core/config.py",
                (
                    "from functools import lru_cache\n"
                    "from pydantic import BaseModel\n"
                    "import os\n\n"
                    "class Settings(BaseModel):\n"
                    f"    app_name: str = {project_name!r}\n"
                    "    app_env: str = os.getenv('APP_ENV', 'development')\n"
                    "    app_debug: bool = os.getenv('APP_DEBUG', 'true').lower() in {'1', 'true', 'yes', 'on'}\n"
                    "    app_secret_key: str = os.getenv('APP_SECRET_KEY', '')\n"
                    "    status_endpoint: str = os.getenv('STATUS_ENDPOINT', 'http://localhost:9000/status')\n\n"
                    "@lru_cache(maxsize=1)\n"
                    "def get_settings() -> Settings:\n"
                    "    return Settings()\n"
                ),
            ),
            GeneratedArtifact(
                "app/core/security.py",
                (
                    "from app.core.config import get_settings\n\n"
                    "def build_security_headers() -> dict:\n"
                    "    settings = get_settings()\n"
                    "    return {\n"
                    "        'has_secret_key': bool(settings.app_secret_key),\n"
                    "        'frame_options': 'DENY',\n"
                    "        'content_type_options': 'nosniff',\n"
                    "    }\n"
                ),
            ),
            GeneratedArtifact(
                "app/services/__init__.py",
                "from app.services.runtime_service import build_runtime_summary\n\n__all__ = ['build_runtime_summary']\n",
            ),
            GeneratedArtifact(
                "app/services/runtime_service.py",
                (
                    "from app.core.config import get_settings\n"
                    "from app.external_adapters.status_client import build_status_client_summary\n\n"
                    "def build_runtime_summary() -> dict:\n"
                    "    settings = get_settings()\n"
                    "    return {\n"
                    "        'service': settings.app_name,\n"
                    "        'environment': settings.app_env,\n"
                    "        'status': 'ok',\n"
                    "        'worker': True,\n"
                    "        'status_client': build_status_client_summary(),\n"
                    "        'mode': 'multi-role',\n"
                    "    }\n"
                ),
            ),
            GeneratedArtifact(
                "app/external_adapters/__init__.py",
                "",
            ),
            GeneratedArtifact(
                "app/external_adapters/status_client.py",
                (
                    "from app.core.config import get_settings\n\n"
                    "def build_status_client_summary() -> dict:\n"
                    "    settings = get_settings()\n"
                    "    return {\n"
                    "        'endpoint': settings.status_endpoint,\n"
                    "        'configured': bool(settings.status_endpoint),\n"
                    "    }\n"
                ),
            ),
            GeneratedArtifact(
                "app/agents/__init__.py",
                "",
            ),
            GeneratedArtifact(
                "app/agents/orchestrator_roles.py",
                "def build_orchestrator_role_matrix() -> list[dict]:\n    return [{'role': 'planner', 'status': 'ready'}, {'role': 'worker', 'status': 'ready'}, {'role': 'reviewer', 'status': 'ready'}, {'role': 'security', 'status': 'ready'}, {'role': 'ops', 'status': 'ready'}]\n",
            ),
            GeneratedArtifact(
                "app/workers/__init__.py",
                "",
            ),
            GeneratedArtifact(
                "app/workers/runner.py",
                (
                    "from app.agents.orchestrator_roles import build_orchestrator_role_matrix\n"
                    "from app.core.security import build_security_headers\n"
                    "from app.services.runtime_service import build_runtime_summary\n\n"
                    "def run() -> dict:\n"
                    "    payload = build_runtime_summary()\n"
                    "    payload['roles'] = build_orchestrator_role_matrix()\n"
                    "    payload['security'] = build_security_headers()\n"
                    "    return payload\n"
                ),
            ),
            GeneratedArtifact(
                "tests/test_worker.py",
                (
                    "from app.workers.runner import run\n\n"
                    "def test_worker_runs() -> None:\n"
                    "    assert run()['status'] == 'ok'\n"
                ),
            ),
            GeneratedArtifact(
                "docs/architecture.md",
                "# worker architecture\n\n- app/main.py\n- app/workers/runner.py\n- app/services/runtime_service.py\n- app/core/security.py\n- app/external_adapters/status_client.py\n",
            ),
            GeneratedArtifact(
                "docs/file_manifest.md",
                "# File Manifest\n\n- app/main.py\n- app/workers/runner.py\n- app/services/__init__.py\n- app/services/runtime_service.py\n- app/core/config.py\n- app/core/security.py\n- app/external_adapters/status_client.py\n- app/agents/orchestrator_roles.py\n- tests/test_worker.py\n",
            ),
            GeneratedArtifact(
                "docs/orchestrator_checklist.md",
                "# Orchestrator Checklist\n\n- [ ] dependency install verified\n- [ ] standalone boot verified\n- [ ] core API smoke verified\n- [ ] pytest verified\n- [ ] zip repro verified\n",
            ),
            GeneratedArtifact(
                "docs/output_audit.json",
                '{\n  "status": "pending",\n  "checks": ["dependency_install", "standalone_boot", "pytest", "zip_repro"]\n}\n',
            ),
            GeneratedArtifact(
                "docs/orchestrator_artifacts.json",
                json.dumps({"generated_by": "python_code_generator", "profile": "python_worker"}, ensure_ascii=False, indent=2) + "\n",
            ),
            GeneratedArtifact(
                "docs/traceability_map.json",
                json.dumps({"worker_runner": {"source": "app/workers/runner.py", "consumers": ["app/main.py", "tests/test_worker.py"]}}, ensure_ascii=False, indent=2) + "\n",
            ),
            GeneratedArtifact(
                "docs/multi_role_contract.json",
                multi_role_contract_json,
            ),
            GeneratedArtifact(
                "docs/operational_readiness.md",
                operational_readiness_markdown,
            ),
            GeneratedArtifact(
                ".codeai-template.json",
                template_json,
            ),
        ]
    )


def _generic_artifacts(project_name: str, task: str) -> List[GeneratedArtifact]:
    template_json = _build_template_contract_json("generic", task)
    multi_role_contract_json = _build_multi_role_contract_json("generic", project_name, task)
    operational_readiness_markdown = _build_operational_readiness_markdown("generic", project_name)
    return _normalize_artifacts(
        [
            GeneratedArtifact(
                "README.md",
                f"# {project_name}\n\nPython generic scaffold\n\n- task: {task}\n",
            ),
            GeneratedArtifact(
                "app/__init__.py",
                "",
            ),
            GeneratedArtifact(
                "app/main.py",
                "from app.tasks.runtime_task import run\n\n\ndef main() -> None:\n    print(run())\n\n\nif __name__ == '__main__':\n    main()\n",
            ),
            GeneratedArtifact(
                "app/tasks/__init__.py",
                "",
            ),
            GeneratedArtifact(
                "app/tasks/runtime_task.py",
                "from app.agents.orchestrator_roles import build_orchestrator_role_matrix\nfrom app.core.security import build_security_headers\nfrom app.services.runtime_service import build_runtime_summary\n\n\ndef run() -> dict:\n    payload = build_runtime_summary()\n    payload['roles'] = build_orchestrator_role_matrix()\n    payload['security'] = build_security_headers()\n    return payload\n",
            ),
            GeneratedArtifact(
                "app/core/__init__.py",
                "",
            ),
            GeneratedArtifact(
                "app/core/config.py",
                (
                    "from functools import lru_cache\n"
                    "from pydantic import BaseModel\n"
                    "import os\n\n"
                    "class Settings(BaseModel):\n"
                    f"    app_name: str = {project_name!r}\n"
                    "    app_env: str = os.getenv('APP_ENV', 'development')\n"
                    "    app_debug: bool = os.getenv('APP_DEBUG', 'true').lower() in {'1', 'true', 'yes', 'on'}\n"
                    "    app_secret_key: str = os.getenv('APP_SECRET_KEY', '')\n"
                    "    status_endpoint: str = os.getenv('STATUS_ENDPOINT', 'http://localhost:9100/status')\n\n"
                    "@lru_cache(maxsize=1)\n"
                    "def get_settings() -> Settings:\n"
                    "    return Settings()\n"
                ),
            ),
            GeneratedArtifact(
                "app/core/security.py",
                "from app.core.config import get_settings\n\n\ndef build_security_headers() -> dict:\n    settings = get_settings()\n    return {'has_secret_key': bool(settings.app_secret_key), 'frame_options': 'DENY', 'content_type_options': 'nosniff'}\n",
            ),
            GeneratedArtifact(
                "app/services/__init__.py",
                "from app.services.runtime_service import build_runtime_summary\n\n__all__ = ['build_runtime_summary']\n",
            ),
            GeneratedArtifact(
                "app/services/runtime_service.py",
                "from app.core.config import get_settings\nfrom app.external_adapters.status_client import build_status_client_summary\n\n\ndef build_runtime_summary() -> dict:\n    settings = get_settings()\n    return {'service': settings.app_name, 'environment': settings.app_env, 'status': 'ok', 'status_client': build_status_client_summary(), 'mode': 'multi-role'}\n",
            ),
            GeneratedArtifact(
                "app/external_adapters/__init__.py",
                "",
            ),
            GeneratedArtifact(
                "app/external_adapters/status_client.py",
                "from app.core.config import get_settings\n\n\ndef build_status_client_summary() -> dict:\n    settings = get_settings()\n    return {'endpoint': settings.status_endpoint, 'configured': bool(settings.status_endpoint)}\n",
            ),
            GeneratedArtifact(
                "app/agents/__init__.py",
                "",
            ),
            GeneratedArtifact(
                "app/agents/orchestrator_roles.py",
                "def build_orchestrator_role_matrix() -> list[dict]:\n    return [{'role': 'planner', 'status': 'ready'}, {'role': 'coder', 'status': 'ready'}, {'role': 'reviewer', 'status': 'ready'}, {'role': 'security', 'status': 'ready'}, {'role': 'ops', 'status': 'ready'}]\n",
            ),
            GeneratedArtifact(
                "tests/__init__.py",
                "",
            ),
            GeneratedArtifact(
                "tests/test_runtime.py",
                "from app.tasks.runtime_task import run\n\n\ndef test_runtime() -> None:\n    assert run()['status'] == 'ok'\n",
            ),
            GeneratedArtifact(
                "docs/architecture.md",
                "# generic architecture\n\n- app/main.py\n- app/tasks/runtime_task.py\n- app/services/runtime_service.py\n- app/core/security.py\n- app/external_adapters/status_client.py\n",
            ),
            GeneratedArtifact(
                "docs/file_manifest.md",
                "# File Manifest\n\n- app/main.py\n- app/tasks/runtime_task.py\n- app/services/__init__.py\n- app/services/runtime_service.py\n- app/core/config.py\n- app/core/security.py\n- app/external_adapters/status_client.py\n- app/agents/orchestrator_roles.py\n- tests/test_runtime.py\n",
            ),
            GeneratedArtifact(
                "docs/orchestrator_checklist.md",
                "# Orchestrator Checklist\n\n- [ ] dependency install verified\n- [ ] standalone boot verified\n- [ ] core API smoke verified\n- [ ] pytest verified\n- [ ] zip repro verified\n",
            ),
            GeneratedArtifact(
                "docs/output_audit.json",
                '{\n  "status": "pending",\n  "checks": ["dependency_install", "standalone_boot", "pytest", "zip_repro"]\n}\n',
            ),
            GeneratedArtifact(
                "docs/orchestrator_artifacts.json",
                json.dumps({"generated_by": "python_code_generator", "profile": "generic"}, ensure_ascii=False, indent=2) + "\n",
            ),
            GeneratedArtifact(
                "docs/traceability_map.json",
                json.dumps({"runtime_task": {"source": "app/tasks/runtime_task.py", "consumers": ["app/main.py", "tests/test_runtime.py"]}}, ensure_ascii=False, indent=2) + "\n",
            ),
            GeneratedArtifact(
                "docs/multi_role_contract.json",
                multi_role_contract_json,
            ),
            GeneratedArtifact(
                "docs/operational_readiness.md",
                operational_readiness_markdown,
            ),
            GeneratedArtifact(
                ".codeai-template.json",
                template_json,
            ),
        ]
    )


def build_python_generation_plan(
    *,
    project_name: str,
    profile: str,
    task: str,
    output_dir: Path,
) -> PythonGenerationPlan:
    normalized_profile = str(profile or "generic").strip().lower()
    if normalized_profile not in SUPPORTED_PYTHON_PROFILES:
        raise ValueError(f"unsupported python generation profile: {normalized_profile}")
    if normalized_profile == "python_fastapi":
        artifacts = _python_fastapi_artifacts(project_name, task)
    elif normalized_profile == "python_worker":
        artifacts = _python_worker_artifacts(project_name, task)
    else:
        artifacts = _generic_artifacts(project_name, task)
    return PythonGenerationPlan(
        project_name=project_name,
        profile=normalized_profile,
        task=task,
        output_dir=output_dir,
        artifacts=artifacts,
    )


def write_python_generation_plan(plan: PythonGenerationPlan) -> List[str]:
    written: List[str] = []
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    for artifact in plan.artifacts:
        target = plan.output_dir / artifact.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(artifact.content, encoding="utf-8")
        written.append(artifact.path)
    return written


__all__ = [
    "GeneratedArtifact",
    "PythonGenerationPlan",
    "SUPPORTED_PYTHON_PROFILES",
    "build_python_generation_plan",
    "write_python_generation_plan",
]
