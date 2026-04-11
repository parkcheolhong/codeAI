from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from backend.generators.facade import generate_python_project_bundle


def run_once(run_name: str) -> dict:
    tmp = tempfile.mkdtemp(prefix=f"{run_name}_")
    try:
        out = Path(tmp) / run_name
        result = generate_python_project_bundle(
            project_name=f"checklist-fix-{run_name}",
            profile="python_fastapi",
            task="AI엔진 멀티 오케스트레이터 업그레이드",
            output_dir=out,
        )
        checklist_path = out / "docs" / "generator_checklist.md"
        text = checklist_path.read_text(encoding="utf-8")
        return {
            "run": run_name,
            "exists": checklist_path.exists(),
            "has_missing_self_artifact": "missing required artifact: docs/generator_checklist.md" in text,
            "generation_ok": result.metadata.get("generation_ok"),
            "generation_findings": result.metadata.get("generation_findings"),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    findings = [run_once("run1"), run_once("run2")]
    print(json.dumps(findings, ensure_ascii=False))


if __name__ == "__main__":
    main()
