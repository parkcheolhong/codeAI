from __future__ import annotations

from pathlib import Path
from tempfile import gettempdir
from typing import Any, Dict, List
from uuid import uuid4
import csv
import zipfile
from datetime import datetime, timezone

from ..contracts import extract_prompt_keywords, summarize_prompt


def _column_name(keywords: List[str], index: int) -> str:
    if index < len(keywords):
        return keywords[index].replace("-", "_")[:18]
    return f"field_{index + 1}"


def _build_output_root() -> Path:
    root = Path(gettempdir()) / "codeai-marketplace-sheet"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _build_rows(columns: List[Dict[str, Any]], row_goal: int, keywords: List[str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    topic = keywords[0] if keywords else "campaign"
    for row_index in range(row_goal):
        row: Dict[str, Any] = {}
        for column_index, column in enumerate(columns):
            column_name = str(column.get("name") or f"field_{column_index + 1}")
            column_type = str(column.get("type") or "text")
            if column_type == "number":
                row[column_name] = (row_index + 1) * 10
            elif column_type == "date":
                day = (row_index % 28) + 1
                row[column_name] = f"2026-04-{day:02d}"
            else:
                row[column_name] = f"{topic}_{column_name}_{row_index + 1}"
        rows.append(row)
    return rows


def _xlsx_column_name(index: int) -> str:
    value = index + 1
    letters = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _xlsx_cell_xml(column_index: int, row_index: int, value: Any, cell_type: str) -> str:
    cell_ref = f"{_xlsx_column_name(column_index)}{row_index}"
    if cell_type == "number":
        return f'<c r="{cell_ref}"><v>{value}</v></c>'
    escaped = (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return f'<c r="{cell_ref}" t="inlineStr"><is><t>{escaped}</t></is></c>'


def _render_csv(output_path: Path, columns: List[Dict[str, Any]], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    fieldnames = [str(column.get("name") or "") for column in columns]
    with output_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return {
        "row_count": len(rows),
        "column_count": len(fieldnames),
    }


def _render_xlsx(output_path: Path, sheet_name: str, columns: List[Dict[str, Any]], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    worksheet_rows: List[str] = []
    header_cells = [
        _xlsx_cell_xml(column_index, 1, str(column.get("name") or ""), "text")
        for column_index, column in enumerate(columns)
    ]
    worksheet_rows.append(f'<row r="1">{"".join(header_cells)}</row>')

    for row_offset, row in enumerate(rows, start=2):
        cells: List[str] = []
        for column_index, column in enumerate(columns):
            column_name = str(column.get("name") or "")
            column_type = str(column.get("type") or "text")
            value = row.get(column_name, "")
            cells.append(_xlsx_cell_xml(column_index, row_offset, value, column_type))
        worksheet_rows.append(f'<row r="{row_offset}">{"".join(cells)}</row>')

    worksheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData>'
        + "".join(worksheet_rows)
        + '</sheetData></worksheet>'
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>'
    )

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as workbook:
        workbook.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '</Types>',
        )
        workbook.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>',
        )
        workbook.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '</Relationships>',
        )
        workbook.writestr("xl/workbook.xml", workbook_xml)
        workbook.writestr("xl/worksheets/sheet1.xml", worksheet_xml)
    return {
        "row_count": len(rows),
        "column_count": len(columns),
    }


def _build_delivery_asset(path: Path, format_name: str, mime_type: str) -> Dict[str, Any]:
    exists = path.exists()
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "format": format_name,
        "path": str(path),
        "path_hint": str(path),
        "mime_type": mime_type,
        "size_bytes": path.stat().st_size if exists else 0,
        "exists": exists,
        "generated_at": generated_at,
    }


def build_spreadsheet_preview(payload: Dict[str, Any]) -> Dict[str, Any]:
    prompt = str(payload.get("prompt") or "").strip()
    keywords = extract_prompt_keywords(prompt, limit=6)
    template_id = str(payload.get("template_id") or "sheet-schema-template")
    project_name = str(payload.get("project_name") or "marketplace-sheet-run")
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    columns = [
        {"name": _column_name(keywords, index), "type": data_type}
        for index, data_type in enumerate(["text", "number", "text", "date"])
    ]
    return {
        "artifact_id": f"ai-sheet-preview-{uuid4().hex[:8]}",
        "feature_id": "ai-sheet",
        "phase": "preview",
        "status": "ready",
        "asset_kind": "sheet-plan",
        "title": "시트 schema preview",
        "prompt": prompt,
        "prompt_summary": summarize_prompt(prompt),
        "keywords": keywords,
        "generated_at": generated_at,
        "notes": [
            f"template_id={template_id}",
            f"project_name={project_name}",
            "preview 단계에서는 시트 컬럼 구조와 목표 행 수를 먼저 확정합니다.",
        ],
        "sheet_schema": {
            "sheet_name": "GeneratedSheet",
            "columns": columns,
            "row_goal": 24,
        },
        "bridge_payload": dict(payload.get("bridge_payload") or {}),
        "failure_tags": [],
    }


def render_spreadsheet_final(payload: Dict[str, Any], preview_artifact: Dict[str, Any]) -> Dict[str, Any]:
    sheet_schema = dict(preview_artifact.get("sheet_schema") or {})
    columns = list(sheet_schema.get("columns") or [])
    row_goal = max(1, int(sheet_schema.get("row_goal") or 24))
    rows = _build_rows(columns, row_goal, list(preview_artifact.get("keywords") or []))
    artifact_seed = uuid4().hex[:8]
    output_root = _build_output_root() / artifact_seed
    output_root.mkdir(parents=True, exist_ok=True)
    sheet_name = str(sheet_schema.get("sheet_name") or "GeneratedSheet")
    xlsx_path = output_root / f"{artifact_seed}.xlsx"
    csv_path = output_root / f"{artifact_seed}.csv"
    xlsx_metrics = _render_xlsx(xlsx_path, sheet_name, columns, rows)
    csv_metrics = _render_csv(csv_path, columns, rows)
    delivery_assets = [
        _build_delivery_asset(xlsx_path, "xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        _build_delivery_asset(csv_path, "csv", "text/csv"),
    ]
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "artifact_id": f"ai-sheet-final-{artifact_seed}",
        "feature_id": "ai-sheet",
        "phase": "final",
        "status": "generated",
        "asset_kind": "spreadsheet-package",
        "title": "최종 workbook 패키지",
        "preview_artifact_id": preview_artifact.get("artifact_id"),
        "prompt_summary": str(preview_artifact.get("prompt_summary") or ""),
        "keywords": list(preview_artifact.get("keywords") or []),
        "generated_at": generated_at,
        "workbook": {
            "sheet_name": sheet_name,
            "column_count": len(columns),
            "row_count": len(rows),
            "sample_rows": rows[:3],
        },
        "delivery_assets": delivery_assets,
        "notes": [
            "xlsx 와 csv 패키지를 함께 생성했습니다.",
            f"output_root={output_root}",
        ],
        "runtime_source": {
            "engine": "local_sheet_packager",
            "output_root": str(output_root),
        },
        "render_metrics": {
            "xlsx": xlsx_metrics,
            "csv": csv_metrics,
        },
        "bridge_payload": dict(payload.get("bridge_payload") or {}),
        "failure_tags": [],
    }


def review_spreadsheet_quality(
    payload: Dict[str, Any],
    preview_artifact: Dict[str, Any],
    final_artifact: Dict[str, Any],
) -> Dict[str, Any]:
    del payload
    delivery_assets = list(final_artifact.get("delivery_assets") or [])
    existing_assets = [asset for asset in delivery_assets if asset.get("exists") and Path(str(asset.get("path") or "")).exists()]
    format_names = {str(asset.get("format") or "").lower() for asset in existing_assets}
    workbook = dict(final_artifact.get("workbook") or {})
    column_count = int(workbook.get("column_count") or 0)
    row_count = int(workbook.get("row_count") or 0)
    expected_columns = len(((preview_artifact.get("sheet_schema") or {}).get("columns") or []))
    passed = (
        column_count == expected_columns
        and column_count >= 4
        and row_count >= 1
        and {"xlsx", "csv"}.issubset(format_names)
    )
    return {
        "passed": passed,
        "status": "approved" if passed else "needs-review",
        "feature_id": "ai-sheet",
        "fallback_state": "completed" if passed else "completed_preview_only",
        "score": 90 if passed else 62,
        "review_summary": "시트 schema, final workbook row/column 수, delivery asset 실파일 계약이 일치합니다.",
        "failure_tags": [] if passed else ["spreadsheet-delivery-assets-missing"],
        "checks": {
            "column_count_match": column_count == expected_columns,
            "row_count_ready": row_count >= 1,
            "xlsx_exists": "xlsx" in format_names,
            "csv_exists": "csv" in format_names,
        },
        "preview_artifact_id": preview_artifact.get("artifact_id"),
        "final_artifact_id": final_artifact.get("artifact_id"),
    }