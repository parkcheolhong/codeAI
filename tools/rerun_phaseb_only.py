from __future__ import annotations

import asyncio
import json
from pathlib import Path

from backend.llm.orchestrator import OrchestrationRequest, run_orchestration


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TARGET_DIRS = [
    WORKSPACE_ROOT / "uploads/projects/phaseb-direct-run-01_20260405_225523",
    WORKSPACE_ROOT / "uploads/projects/phaseb-direct-run-02_20260405_232213",
]


def _read_text(target_dir: Path, relative_path: str) -> str:
    return (target_dir / relative_path).read_text(encoding="utf-8", errors="ignore")


def _inspect(target_dir: Path) -> dict[str, object]:
    readiness = _read_text(target_dir, "docs/final_readiness_checklist.md")
    orchestrator = _read_text(target_dir, "docs/orchestrator_checklist.md")
    semantic_audit = _read_text(target_dir, "docs/semantic_completion_audit.md")
    validation_json = json.loads(_read_text(target_dir, "docs/automatic_validation_result.json"))
    semantic_gate = dict((validation_json.get("validation_engines") or {}).get("semantic_gate") or {})
    integration_engine = dict((validation_json.get("validation_engines") or {}).get("integration_test_engine") or {})
    return {
        "completion_gate_checked": "- [x] completion gate" in readiness,
        "semantic_gate_checked": "- [x] semantic gate" in readiness,
        "semantic_audit_ok_true": "semantic_audit_ok: True" in orchestrator,
        "semantic_completion_pass": "- score: 100" in semantic_audit and "- status: pass" in semantic_audit,
        "semantic_gate_score": semantic_gate.get("score"),
        "semantic_gate_findings": list(semantic_gate.get("checklist") or []),
        "integration_ok": integration_engine.get("ok"),
        "integration_failures": list(integration_engine.get("failures") or []),
    }


async def _run_once(target_dir: Path, pass_name: str) -> dict[str, object]:
    plan = json.loads((target_dir / "docs" / "generation-plan.json").read_text(encoding="utf-8"))
    request = OrchestrationRequest(
        task=str(plan.get("task") or target_dir.name),
        mode="auto",
        project_name=str(plan.get("project_name") or target_dir.name),
        output_dir=str(target_dir),
        output_base_dir="uploads/projects",
        continue_in_place=True,
        manual_mode=False,
        run_postcheck=True,
        retry_on_postcheck_fail=True,
        forensic_on_fail=True,
        enable_improvement_loop=False,
    )
    response = await run_orchestration(request)
    inspection = _inspect(target_dir)
    return {
        "pass": pass_name,
        "target_dir": str(target_dir),
        "response_completion_gate_ok": bool(response.completion_gate_ok),
        "response_semantic_audit_ok": bool(response.semantic_audit_ok),
        "response_semantic_audit_score": response.semantic_audit_score,
        **inspection,
    }


async def main() -> None:
    results: list[dict[str, object]] = []
    for index in range(2):
        pass_name = f"run{index + 1}"
        for target_dir in TARGET_DIRS:
            results.append(await _run_once(target_dir, pass_name))
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
