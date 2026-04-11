from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from backend.generators.facade import generate_multi_project_bundle


def run_once(run_name: str) -> dict:
    tmp = tempfile.mkdtemp(prefix=f"multi_code_generator_{run_name}_")
    try:
        out = Path(tmp) / run_name
        result = generate_multi_project_bundle(
            project_name=f"multi-code-generator-{run_name}",
            primary_profile="python_fastapi",
            additional_profiles=["nextjs_react", "node_service"],
            task="AI엔진 멀티 오케스트레이터 업그레이드",
            output_dir=out,
        )
        required_paths = [
            "app/main.py",
            "docs/multi_generator_matrix.json",
            "addons/nextjs_react/app/page.tsx",
            "addons/node_service/src/http/router.go",
        ]
        existing = {path: (out / path).exists() for path in required_paths}
        matrix_payload = json.loads((out / "docs" / "multi_generator_matrix.json").read_text(encoding="utf-8"))
        return {
            "run": run_name,
            "written_count": len(result.written_files),
            "existing": existing,
            "generator": result.metadata.get("generator"),
            "primary_profile": result.metadata.get("primary_profile"),
            "generator_profiles": result.metadata.get("generator_profiles"),
            "sidecar_count": len(result.metadata.get("sidecars") or []),
            "matrix_profiles": matrix_payload.get("profiles"),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    print(json.dumps([run_once("run1"), run_once("run2")], ensure_ascii=False))


if __name__ == "__main__":
    main()
