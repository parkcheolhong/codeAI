from __future__ import annotations

import json
from pathlib import Path

from backend.python_code_generator import _python_fastapi_artifacts, build_python_generation_plan


EXPECTED = [
    "app/routes.py",
    "app/runtime.py",
    "app/diagnostics.py",
    "app/order_profile.py",
    "backend/main.py",
    "backend/core/runtime.py",
    "backend/core/flow_registry.py",
    "backend/api/router.py",
    "backend/data/provider.py",
    "backend/service/application_service.py",
    "app/auth_routes.py",
    "app/ops_routes.py",
    "backend/core/auth.py",
    "backend/core/security.py",
    "backend/app/external_adapters/status_client.py",
    "backend/app/connectors/base.py",
]


def main() -> None:
    artifacts = _python_fastapi_artifacts("artifact-inspect", "semantic gate alignment")
    plan = build_python_generation_plan(
        project_name="artifact-inspect",
        profile="python_fastapi",
        task="semantic gate alignment",
        output_dir=Path("."),
    )
    paths = [artifact.path for artifact in artifacts]
    payload = {
        "artifact_count": len(paths),
        "missing_expected": [path for path in EXPECTED if path not in paths],
        "present_expected": [path for path in EXPECTED if path in paths],
        "all_paths": paths,
        "plan_file_count": plan.file_count,
    }
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
