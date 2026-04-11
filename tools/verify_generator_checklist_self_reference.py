from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from backend.generators.facade import generate_python_project_bundle


def main() -> None:
    base = Path(tempfile.mkdtemp(prefix="gen-check-"))
    results: list[dict[str, object]] = []
    try:
        for index in range(2):
            output_dir = base / f"run{index + 1}"
            generate_python_project_bundle(
                project_name="generator-check-self-ref",
                profile="python_fastapi",
                task="generator checklist self reference validation",
                output_dir=output_dir,
            )
            checklist_path = output_dir / "docs" / "generator_checklist.md"
            text = checklist_path.read_text(encoding="utf-8")
            results.append(
                {
                    "run": index + 1,
                    "artifact_exists": checklist_path.exists(),
                    "has_missing_self_artifact": "missing required artifact: docs/generator_checklist.md" in text,
                    "has_all_required_message": "all required generation artifacts present" in text,
                    "generation_ok_true": "generation_ok: true" in text,
                }
            )
    finally:
        shutil.rmtree(base, ignore_errors=True)

    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
