from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from backend.generators.facade import generate_python_project_bundle
from backend.llm.orchestrator import _compat_run_semantic_gate


def build_manifest(output_dir: Path) -> list[dict[str, str]]:
    manifest: list[dict[str, str]] = []
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(output_dir).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        manifest.append({"path": rel, "content": text})
    return manifest


def run_once(run_name: str) -> dict:
    tmp = tempfile.mkdtemp(prefix=f"semantic_gate_{run_name}_")
    try:
        out = Path(tmp) / run_name
        result = generate_python_project_bundle(
            project_name=f"semantic-gate-fix-{run_name}",
            profile="python_fastapi",
            task="AI엔진 멀티 오케스트레이터 업그레이드",
            output_dir=out,
        )
        manifest = build_manifest(out)
        order_profile = {
            "profile_id": "customer_program",
            "ai_enabled": False,
            "mandatory_engine_contracts": [],
        }
        gate = _compat_run_semantic_gate(
            task="AI엔진 멀티 오케스트레이터 업그레이드",
            project_name=f"semantic-gate-fix-{run_name}",
            order_profile=order_profile,
            validation_profile="python_fastapi",
            manifest=manifest,
        )
        return {
            "run": run_name,
            "generation_ok": result.metadata.get("generation_ok"),
            "generation_findings": result.metadata.get("generation_findings"),
            "semantic_gate_ok": gate.get("ok"),
            "semantic_gate_score": gate.get("score"),
            "semantic_gate_summary": gate.get("summary"),
            "semantic_gate_findings": gate.get("checklist"),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    findings = [run_once("run1"), run_once("run2")]
    print(json.dumps(findings, ensure_ascii=False))


if __name__ == "__main__":
    main()
