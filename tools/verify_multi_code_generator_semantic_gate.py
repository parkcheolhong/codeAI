from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from backend.generators.facade import generate_multi_project_bundle
from backend.llm.orchestrator import _compat_run_semantic_gate


def build_manifest(output_dir: Path) -> list[dict[str, str]]:
    manifest: list[dict[str, str]] = []
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file():
            continue
        manifest.append(
            {
                "path": path.relative_to(output_dir).as_posix(),
                "content": path.read_text(encoding="utf-8", errors="ignore"),
            }
        )
    return manifest


def run_once(run_name: str) -> dict:
    tmp = tempfile.mkdtemp(prefix=f"multi_semantic_{run_name}_")
    try:
        out = Path(tmp) / run_name
        result = generate_multi_project_bundle(
            project_name=f"multi-semantic-{run_name}",
            primary_profile="python_fastapi",
            additional_profiles=["nextjs_react", "node_service"],
            task="AI엔진 멀티 오케스트레이터 업그레이드",
            output_dir=out,
        )
        gate = _compat_run_semantic_gate(
            task="AI엔진 멀티 오케스트레이터 업그레이드",
            project_name=f"multi-semantic-{run_name}",
            order_profile={
                "profile_id": "customer_program",
                "ai_enabled": False,
                "mandatory_engine_contracts": [],
            },
            validation_profile="python_fastapi",
            manifest=build_manifest(out),
        )
        return {
            "run": run_name,
            "written_count": len(result.written_files),
            "semantic_gate_ok": gate.get("ok"),
            "semantic_gate_score": gate.get("score"),
            "semantic_gate_summary": gate.get("summary"),
            "semantic_gate_findings": list(gate.get("checklist") or []),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print(json.dumps([run_once("run1"), run_once("run2")], ensure_ascii=False))
