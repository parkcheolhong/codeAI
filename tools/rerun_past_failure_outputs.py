from __future__ import annotations

import asyncio
import json
from pathlib import Path

from backend.llm.orchestrator import OrchestrationRequest, run_orchestration


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TARGET_DIRS = [
    WORKSPACE_ROOT / "uploads/projects/phaseb-direct-run-01_20260405_225523",
    WORKSPACE_ROOT / "uploads/projects/phaseb-direct-run-02_20260405_232213",
    WORKSPACE_ROOT / "uploads/projects/phase-c-and-d-smoke_20260405_183024",
    WORKSPACE_ROOT / "uploads/projects/phase-c-and-d-smoke-rerun_20260405_183237",
    WORKSPACE_ROOT / "uploads/projects/ui-hard-gate-smoke_20260405_172507",
]


def _load_generation_plan(target_dir: Path) -> dict[str, object]:
    plan_path = target_dir / "docs" / "generation-plan.json"
    return json.loads(plan_path.read_text(encoding="utf-8"))


def _read_text(target_dir: Path, relative_path: str) -> str:
    return (target_dir / relative_path).read_text(encoding="utf-8", errors="ignore")


def _inspect_target(target_dir: Path) -> dict[str, object]:
    readiness = _read_text(target_dir, "docs/final_readiness_checklist.md")
    orchestrator = _read_text(target_dir, "docs/orchestrator_checklist.md")
    semantic_audit = _read_text(target_dir, "docs/semantic_completion_audit.md")
    generator_checklist = _read_text(target_dir, "docs/generator_checklist.md")
    return {
        "target_dir": str(target_dir),
        "completion_gate_checked": "- [x] completion gate" in readiness,
        "semantic_gate_checked": "- [x] semantic gate" in readiness,
        "semantic_audit_ok_true": "semantic_audit_ok: True" in orchestrator,
        "semantic_audit_score_100": "semantic_audit_score: 100" in orchestrator,
        "semantic_completion_pass": "- score: 100" in semantic_audit and "- status: pass" in semantic_audit,
        "generator_missing_self_artifact": "missing required artifact: docs/generator_checklist.md" in generator_checklist,
    }


async def _run_once(target_dir: Path, pass_name: str) -> dict[str, object]:
    generation_plan = _load_generation_plan(target_dir)
    request = OrchestrationRequest(
        task=str(generation_plan.get("task") or target_dir.name),
        mode="auto",
        project_name=str(generation_plan.get("project_name") or target_dir.name),
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
    inspection = _inspect_target(target_dir)
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
