from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional

import typer

from backend.llm.project_indexer import project_indexer
from backend.generators import generate_multi_project_bundle, generate_non_python_project_bundle, generate_python_project_bundle
from backend.non_python_code_generator import SUPPORTED_NON_PYTHON_PROFILES
from backend.python_code_generator import SUPPORTED_PYTHON_PROFILES

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Deterministic boilerplate generator for codeAI.",
)

PROFILE_ALIASES = {
    "python-fastapi": "python_fastapi",
    "python_fastapi": "python_fastapi",
    "python-worker": "python_worker",
    "python_worker": "python_worker",
    "nextjs-react": "nextjs_react",
    "nextjs_react": "nextjs_react",
    "node-service": "node_service",
    "node_service": "node_service",
    "go-service": "go_service",
    "go_service": "go_service",
    "rust-service": "rust_service",
    "rust_service": "rust_service",
    "multi-code-generator": "multi_code_generator",
    "multi_code_generator": "multi_code_generator",
    "generic": "generic",
}


@app.callback()
def main() -> None:
    """codeAI boilerplate CLI root."""


def _normalize_profile(profile: str) -> str:
    normalized = PROFILE_ALIASES.get(str(profile).strip().lower())
    if not normalized:
        raise typer.BadParameter(
            "profile must be one of: python-fastapi, python-worker, nextjs-react, "
            "node-service, go-service, rust-service, multi-code-generator, generic"
        )
    return normalized
def _is_python_generation_profile(profile: str) -> bool:
    return profile in SUPPORTED_PYTHON_PROFILES


def _is_non_python_generation_profile(profile: str) -> bool:
    return profile in SUPPORTED_NON_PYTHON_PROFILES
@app.command("generate")
def generate(
    name: str = typer.Option(..., "--name", help="Project name."),
    profile: str = typer.Option(
        "python-fastapi",
        "--profile",
        help="Boilerplate profile.",
    ),
    output_dir: Path = typer.Option(
        ...,
        "--output-dir",
        help="Directory to write generated files into.",
    ),
    task: Optional[str] = typer.Option(
        None,
        "--task",
        help="Optional task description stored in generated docs.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Delete existing output directory before generation.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print generation plan without writing files.",
    ),
) -> None:
    normalized_profile = _normalize_profile(profile)
    resolved_task = (
        task
        or f"{normalized_profile} boilerplate generation for {name}"
    )
    target_dir = output_dir.expanduser().resolve()

    if target_dir.exists() and any(target_dir.iterdir()):
        if not force:
            raise typer.BadParameter(
                "output directory is not empty; use --force to replace it"
            )
        shutil.rmtree(target_dir)

    if _is_python_generation_profile(normalized_profile):
        result = generate_python_project_bundle(
            project_name=name,
            profile=normalized_profile,
            task=resolved_task,
            output_dir=target_dir,
        )
        if dry_run:
            typer.echo(json.dumps(result.plan.summary(), ensure_ascii=False, indent=2))
            return
        project_indexer.index_workspace(target_dir, force=True)
        typer.echo(
            json.dumps(
                {
                    **result.plan.summary(),
                    "generator": "python_code_generator",
                    "written_count": len(result.written_files),
                    "written_files": result.written_files,
                    "metadata": result.metadata,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if normalized_profile == "multi_code_generator":
        result = generate_multi_project_bundle(
            project_name=name,
            primary_profile="python_fastapi",
            additional_profiles=["nextjs_react", "node_service"],
            task=resolved_task,
            output_dir=target_dir,
        )
        if dry_run:
            typer.echo(json.dumps(result.plan.summary(), ensure_ascii=False, indent=2))
            return
        project_indexer.index_workspace(target_dir, force=True)
        typer.echo(
            json.dumps(
                {
                    **result.plan.summary(),
                    "generator": "multi_code_generator",
                    "written_count": len(result.written_files),
                    "written_files": result.written_files,
                    "metadata": result.metadata,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if _is_non_python_generation_profile(normalized_profile):
        result = generate_non_python_project_bundle(
            project_name=name,
            profile=normalized_profile,
            task=resolved_task,
            output_dir=target_dir,
        )
        if dry_run:
            typer.echo(json.dumps(result.plan.summary(), ensure_ascii=False, indent=2))
            return
        project_indexer.index_workspace(target_dir, force=True)
        typer.echo(
            json.dumps(
                {
                    **result.plan.summary(),
                    "generator": "non_python_code_generator",
                    "written_count": len(result.written_files),
                    "written_files": result.written_files,
                    "metadata": result.metadata,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    raise RuntimeError(
        "legacy template generator path is disabled; use a supported generator profile or redesign the remaining profile generators"
    )


if __name__ == "__main__":
    app()
