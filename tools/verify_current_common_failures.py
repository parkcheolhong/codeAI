from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from backend.generators.facade import generate_python_project_bundle
from backend.llm.orchestrator import (
    _build_completion_judge,
    _build_customer_order_profile,
    _build_domain_contract,
    _build_integration_test_plan,
    _build_packaging_audit,
    _compat_run_semantic_gate,
    _resolve_validation_profile,
)


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


def run_once(run_name: str) -> dict[str, object]:
    tmp = tempfile.mkdtemp(prefix=f"current_common_failures_{run_name}_")
    try:
        task = "AI엔진 멀티 오케스트레이터 업그레이드"
        project_name = f"current-common-failures-{run_name}"
        out = Path(tmp) / run_name

        generate_python_project_bundle(
            project_name=project_name,
            profile="python_fastapi",
            task=task,
            output_dir=out,
        )

        order_profile = _build_customer_order_profile(task, project_name)
        validation_profile = _resolve_validation_profile(order_profile, task)
        required_files = sorted(
            {
                path.relative_to(out).as_posix()
                for path in out.rglob("*")
                if path.is_file()
            }
        )
        normalized_requirements = {
            "feature_list": list(order_profile.get("requested_outcomes") or []),
            "completion_conditions": ["필수 파일/구조 생성", "semantic gate 통과", "패키징 문서/설정값 포함"],
            "test_conditions": ["도메인별 필수 테스트 파일 생성", "runtime verification 통과 기준 정리"],
        }
        domain_contract = _build_domain_contract(order_profile, validation_profile, required_files)
        integration_test_plan = _build_integration_test_plan(order_profile, validation_profile)
        semantic_gate = _compat_run_semantic_gate(
            task=task,
            project_name=project_name,
            order_profile=order_profile,
            validation_profile=validation_profile,
            manifest=build_manifest(out),
        )
        packaging_audit = _build_packaging_audit(order_profile, required_files, required_files)
        completion_judge = _build_completion_judge(
            semantic_gate=semantic_gate,
            packaging_audit=packaging_audit,
            integration_test_engine={"ok": True, "checks_run": [], "failures": []},
            normalized_requirements=normalized_requirements,
            integration_test_plan=integration_test_plan,
            completion_state="ready",
            framework_e2e_validation={"ok": True, "commands_run": [], "failures": []},
            external_integration_validation={"ok": True, "checks_run": [], "failures": []},
            shipping_zip_validation={"ok": True, "checks_run": [], "failures": []},
            operational_evidence={
                "integration_status": "verified",
                "verified_target_count": 1,
                "required_target_count": 1,
                "targets": [{"id": "sample", "ok": True, "latency_warning": False, "latency_ms": 20.0, "warning_threshold_ms": 100.0}],
            },
            output_dir=out,
            written_files=required_files,
            domain_contract=domain_contract,
        )

        return {
            "run": run_name,
            "completion_gate_ok": completion_judge.get("product_ready"),
            "completion_failed_reasons": list(completion_judge.get("failed_reasons") or []),
            "semantic_gate_ok": semantic_gate.get("ok"),
            "semantic_gate_score": semantic_gate.get("score"),
            "semantic_gate_findings": list(semantic_gate.get("checklist") or []),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print(json.dumps([run_once("run1"), run_once("run2")], ensure_ascii=False))
