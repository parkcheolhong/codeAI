from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
from typing import Dict, List, Tuple

from backend.generation_dsl import normalize_generation_dsl, parse_request_to_generation_dsl
from backend.generation_optimizer import score_generation_artifacts
from backend.generators.models import GeneratedArtifact
from backend.meta_programming import build_project_graph
from backend.non_python_code_generator import NonPythonGenerationPlan, SUPPORTED_NON_PYTHON_PROFILES, build_non_python_generation_plan, write_non_python_generation_plan
from backend.python_code_generator import PythonGenerationPlan, SUPPORTED_PYTHON_PROFILES, build_python_generation_plan, write_python_generation_plan
from backend.template_generator import select_template_bindings

from .checklist import (
    build_architecture_contract_json,
    build_auto_link_map_json,
    build_generator_checklist_markdown,
    build_id_registry_json,
    build_product_identity_json,
    build_role_separation_markdown,
)


def _decorate_generated_content(path: str, content: str) -> str:
    normalized_path = str(path or "").replace("\\", "/").strip()
    if not normalized_path or normalized_path.startswith("docs/"):
        return content
    import re
    file_stub = re.sub(r"[^A-Za-z0-9]+", "-", normalized_path.upper()).strip("-") or "GENERATED-FILE"
    section_stub = f"SECTION-{file_stub}-MAIN"
    feature_stub = f"FEATURE-{file_stub}-RUNTIME"
    chunk_stub = f"CHUNK-{file_stub}-001"
    suffix = normalized_path.rsplit('.', 1)[-1].lower() if '.' in normalized_path else ''
    if suffix in {"ts", "tsx", "js", "jsx", "css", "scss"}:
        header = (
            f"/* FILE-ID: FILE-{file_stub} */\n"
            f"/* SECTION-ID: {section_stub} */\n"
            f"/* FEATURE-ID: {feature_stub} */\n"
            f"/* CHUNK-ID: {chunk_stub} */\n\n"
        )
        return content if "FILE-ID:" in content[:200] else header + content
    if suffix in {"json"}:
        return content
    header = (
        f"# FILE-ID: FILE-{file_stub}\n"
        f"# SECTION-ID: {section_stub}\n"
        f"# FEATURE-ID: {feature_stub}\n"
        f"# CHUNK-ID: {chunk_stub}\n\n"
    )
    return content if content.startswith("# FILE-ID:") else header + content


def _decorate_plan_artifacts_with_ids(plan: PythonGenerationPlan) -> PythonGenerationPlan:
    return PythonGenerationPlan(
        project_name=plan.project_name,
        profile=plan.profile,
        task=plan.task,
        output_dir=plan.output_dir,
        artifacts=[
            GeneratedArtifact(path=artifact.path, content=_decorate_generated_content(artifact.path, artifact.content))
            for artifact in plan.artifacts
        ],
    )


@dataclass(frozen=True)
class GenerationResult:
    plan: PythonGenerationPlan | NonPythonGenerationPlan
    written_files: List[str]
    metadata: Dict[str, object]


def _normalize_profile_sequence(primary_profile: str, additional_profiles: List[str]) -> List[str]:
    ordered: List[str] = []
    for profile in [primary_profile, *additional_profiles]:
        normalized = str(profile or "").strip().lower()
        if not normalized or normalized in ordered:
            continue
        ordered.append(normalized)
    return ordered


def _sidecar_prefix(profile: str) -> str:
    return f"addons/{str(profile or 'generic').strip().lower()}"


def _prefix_generated_artifacts(artifacts: List[GeneratedArtifact], prefix: str) -> List[GeneratedArtifact]:
    normalized_prefix = str(prefix or "").strip().strip("/")
    return [
        GeneratedArtifact(
            path=f"{normalized_prefix}/{artifact.path}" if normalized_prefix else artifact.path,
            content=artifact.content,
        )
        for artifact in artifacts
    ]


def _build_python_generation_result(*, project_name: str, profile: str, task: str, output_dir: Path) -> GenerationResult:
    base_plan = build_python_generation_plan(
        project_name=project_name,
        profile=profile,
        task=task,
        output_dir=output_dir,
    )
    governed_plan, document, graph, bindings, final_score = _with_governance_artifacts(base_plan)
    metadata: Dict[str, object] = {
        "document": document.summary(),
        "graph": graph.auto_link_map(),
        "bindings": [binding.__dict__ for binding in bindings],
        "generation_score": final_score.score,
        "generation_ok": final_score.ok,
        "generation_findings": final_score.checklist,
    }
    return GenerationResult(
        plan=governed_plan,
        written_files=[],
        metadata=metadata,
    )


def _build_non_python_generation_result(*, project_name: str, profile: str, task: str, output_dir: Path) -> GenerationResult:
    plan = build_non_python_generation_plan(
        project_name=project_name,
        profile=profile,
        task=task,
        output_dir=output_dir,
    )
    document = normalize_generation_dsl(
        parse_request_to_generation_dsl(
            task=plan.task,
            project_name=plan.project_name,
            profile=plan.profile,
        )
    )
    graph = build_project_graph(document)
    bindings = select_template_bindings(graph)
    metadata: Dict[str, object] = {
        "generator": "non_python_code_generator",
        "document": document.summary(),
        "graph": graph.auto_link_map(),
        "bindings": [binding.__dict__ for binding in bindings],
        "generation_ok": True,
        "generation_findings": [],
    }
    return GenerationResult(
        plan=plan,
        written_files=[],
        metadata=metadata,
    )


def _build_generation_result(*, project_name: str, profile: str, task: str, output_dir: Path) -> GenerationResult:
    normalized_profile = str(profile or "").strip().lower()
    if normalized_profile in SUPPORTED_PYTHON_PROFILES:
        return _build_python_generation_result(
            project_name=project_name,
            profile=normalized_profile,
            task=task,
            output_dir=output_dir,
        )
    if normalized_profile in SUPPORTED_NON_PYTHON_PROFILES:
        return _build_non_python_generation_result(
            project_name=project_name,
            profile=normalized_profile,
            task=task,
            output_dir=output_dir,
        )
    raise ValueError(f"unsupported generator profile: {profile}")


def _write_generated_artifacts(output_dir: Path, artifacts: List[GeneratedArtifact]) -> List[str]:
    written: List[str] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    for artifact in artifacts:
        target = output_dir / artifact.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(artifact.content, encoding="utf-8")
        written.append(artifact.path)
    return written


def _with_governance_artifacts(plan: PythonGenerationPlan) -> Tuple[PythonGenerationPlan, object, object, object, object]:
    document = normalize_generation_dsl(
        parse_request_to_generation_dsl(
            task=plan.task,
            project_name=plan.project_name,
            profile=plan.profile,
        )
    )
    graph = build_project_graph(document)
    bindings = select_template_bindings(graph)
    governed_artifacts = list(plan.artifacts)
    governed_artifacts.extend(
        [
            GeneratedArtifact(
                path="docs/auto_link_map.json",
                content=build_auto_link_map_json(document, graph, bindings),
            ),
            GeneratedArtifact(
                path="docs/architecture.contract.json",
                content=build_architecture_contract_json(document, graph),
            ),
            GeneratedArtifact(
                path="docs/role_separation.md",
                content=build_role_separation_markdown(document, graph, bindings),
            ),
            GeneratedArtifact(
                path="docs/id_registry.json",
                content=build_id_registry_json(document, graph, bindings),
            ),
            GeneratedArtifact(
                path="docs/product_identity.json",
                content=build_product_identity_json(document),
            ),
        ]
    )
    interim_plan = PythonGenerationPlan(
        project_name=plan.project_name,
        profile=plan.profile,
        task=plan.task,
        output_dir=plan.output_dir,
        artifacts=governed_artifacts,
    )
    generator_checklist_path = "docs/generator_checklist.md"
    preview_score = score_generation_artifacts(
        [artifact.path for artifact in interim_plan.artifacts] + [generator_checklist_path]
    )
    governed_artifacts.append(
        GeneratedArtifact(
            path=generator_checklist_path,
            content=build_generator_checklist_markdown(document, graph, preview_score),
        )
    )
    final_score = score_generation_artifacts([artifact.path for artifact in governed_artifacts])
    governed_artifacts[-1] = GeneratedArtifact(
        path=generator_checklist_path,
        content=build_generator_checklist_markdown(document, graph, final_score),
    )
    final_plan = PythonGenerationPlan(
        project_name=plan.project_name,
        profile=plan.profile,
        task=plan.task,
        output_dir=plan.output_dir,
        artifacts=governed_artifacts,
    )
    return _decorate_plan_artifacts_with_ids(final_plan), document, graph, bindings, final_score


def generate_python_project_bundle(*, project_name: str, profile: str, task: str, output_dir: Path) -> GenerationResult:
    result = _build_python_generation_result(
        project_name=project_name,
        profile=profile,
        task=task,
        output_dir=output_dir,
    )
    written_files = write_python_generation_plan(result.plan)
    return GenerationResult(plan=result.plan, written_files=written_files, metadata=result.metadata)


def generate_non_python_project_bundle(*, project_name: str, profile: str, task: str, output_dir: Path) -> GenerationResult:
    result = _build_non_python_generation_result(
        project_name=project_name,
        profile=profile,
        task=task,
        output_dir=output_dir,
    )
    written_files = write_non_python_generation_plan(result.plan)
    return GenerationResult(plan=result.plan, written_files=written_files, metadata=result.metadata)


def generate_multi_project_bundle(
    *,
    project_name: str,
    primary_profile: str,
    additional_profiles: List[str],
    task: str,
    output_dir: Path,
) -> GenerationResult:
    profiles = _normalize_profile_sequence(primary_profile, additional_profiles)
    if not profiles:
        raise ValueError("at least one generator profile is required")

    primary_result = _build_generation_result(
        project_name=project_name,
        profile=profiles[0],
        task=task,
        output_dir=output_dir,
    )

    combined_artifacts = list(primary_result.plan.artifacts)
    sidecar_metadata: List[Dict[str, object]] = []
    sidecar_paths: List[str] = []

    for profile in profiles[1:]:
        sidecar_project_name = f"{project_name}-{profile}"
        sidecar_output_dir = output_dir / _sidecar_prefix(profile)
        sidecar_result = _build_generation_result(
            project_name=sidecar_project_name,
            profile=profile,
            task=task,
            output_dir=sidecar_output_dir,
        )
        prefixed_artifacts = _prefix_generated_artifacts(
            list(sidecar_result.plan.artifacts),
            _sidecar_prefix(profile),
        )
        combined_artifacts.extend(prefixed_artifacts)
        sidecar_paths.extend([artifact.path for artifact in prefixed_artifacts])
        sidecar_metadata.append(
            {
                "profile": profile,
                "generator": sidecar_result.metadata.get("generator") or (
                    "python_code_generator"
                    if profile in SUPPORTED_PYTHON_PROFILES
                    else "non_python_code_generator"
                ),
                "output_prefix": _sidecar_prefix(profile),
                "file_count": len(prefixed_artifacts),
            }
        )

    matrix_path = "docs/multi_generator_matrix.json"
    combined_artifacts.append(
        GeneratedArtifact(
            path=matrix_path,
            content=json.dumps(
                {
                    "project_name": project_name,
                    "task": task,
                    "primary_profile": profiles[0],
                    "profiles": profiles,
                    "sidecars": sidecar_metadata,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
    )

    if isinstance(primary_result.plan, PythonGenerationPlan):
        combined_plan: PythonGenerationPlan | NonPythonGenerationPlan = PythonGenerationPlan(
            project_name=primary_result.plan.project_name,
            profile=primary_result.plan.profile,
            task=primary_result.plan.task,
            output_dir=primary_result.plan.output_dir,
            artifacts=combined_artifacts,
        )
    else:
        combined_plan = NonPythonGenerationPlan(
            project_name=primary_result.plan.project_name,
            profile=primary_result.plan.profile,
            task=primary_result.plan.task,
            output_dir=primary_result.plan.output_dir,
            artifacts=combined_artifacts,
        )

    written_files = _write_generated_artifacts(output_dir, combined_artifacts)
    metadata = dict(primary_result.metadata)
    metadata["generator"] = "multi_code_generator"
    metadata["primary_profile"] = profiles[0]
    metadata["generator_profiles"] = profiles
    metadata["sidecars"] = sidecar_metadata
    metadata["sidecar_file_count"] = len(sidecar_paths)
    metadata["multi_generator_matrix_path"] = matrix_path
    return GenerationResult(plan=combined_plan, written_files=written_files, metadata=metadata)
