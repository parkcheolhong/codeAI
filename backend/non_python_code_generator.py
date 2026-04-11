from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from backend.generators.models import GeneratedArtifact
from backend.llm.orchestrator import (
    _build_nextjs_vertical_slice_files,
    _build_node_service_vertical_slice_files,
)


@dataclass(frozen=True)
class NonPythonGenerationPlan:
    project_name: str
    profile: str
    task: str
    output_dir: Path
    artifacts: List[GeneratedArtifact]

    @property
    def file_count(self) -> int:
        return len(self.artifacts)

    def summary(self) -> Dict[str, object]:
        return {
            "project_name": self.project_name,
            "profile": self.profile,
            "task": self.task,
            "output_dir": str(self.output_dir),
            "file_count": self.file_count,
            "files": [artifact.path for artifact in self.artifacts],
        }


SUPPORTED_NON_PYTHON_PROFILES = {
    "nextjs_react",
    "node_service",
    "go_service",
    "rust_service",
}


def _normalize_artifacts(files: Dict[str, str]) -> List[GeneratedArtifact]:
    return [
        GeneratedArtifact(path=str(path).replace('\\', '/').strip(), content=content)
        for path, content in files.items()
        if str(path).strip()
    ]


def _build_go_service_files(project_name: str, task: str) -> Dict[str, str]:
    module_name = f"generated/{project_name.replace('-', '_')}"
    return {
        "README.md": (
            f"# {project_name}\n\n"
            "Generated Go operational service scaffold.\n\n"
            f"- task: {task}\n"
            "- validation: go build ./...\n"
        ),
        "go.mod": (
            f"module {module_name}\n\n"
            "go 1.23.0\n"
        ),
        "cmd/app/main.go": (
            "package main\n\n"
            "import (\n"
            "\t\"log\"\n"
            f"\t\"{module_name}/internal/app\"\n"
            ")\n\n"
            "func main() {\n"
            "\tif err := app.Run(); err != nil {\n"
            "\t\tlog.Fatal(err)\n"
            "\t}\n"
            "}\n"
        ),
        "internal/app/app.go": (
            "package app\n\n"
            "import (\n"
            "\t\"fmt\"\n"
            "\t\"net/http\"\n"
            f"\t\"{module_name}/internal/httpapi\"\n"
            ")\n\n"
            "func Run() error {\n"
            "\trouter := httpapi.NewRouter()\n"
            "\treturn http.ListenAndServe(\":8080\", router)\n"
            "}\n"
        ),
        "internal/http/router.go": (
            "package httpapi\n\n"
            "import (\n"
            "\t\"net/http\"\n"
            f"\t\"{module_name}/internal/http/handlers\"\n"
            f"\t\"{module_name}/internal/repository\"\n"
            f"\t\"{module_name}/internal/service\"\n"
            ")\n\n"
            "func NewRouter() http.Handler {\n"
            "\trepo := repository.NewInventoryRepository()\n"
            "\tserviceLayer := service.NewInventoryService(repo)\n"
            "\tmux := http.NewServeMux()\n"
            "\tmux.Handle(\"/health\", handlers.NewHealthHandler(serviceLayer))\n"
            "\tmux.Handle(\"/inventory\", handlers.NewInventoryHandler(serviceLayer))\n"
            "\treturn mux\n"
            "}\n"
        ),
        "internal/http/handlers/health.go": (
            "package handlers\n\n"
            "import (\n"
            "\t\"encoding/json\"\n"
            "\t\"net/http\"\n"
            f"\t\"{module_name}/internal/service\"\n"
            ")\n\n"
            "type HealthHandler struct {\n"
            "\tservice service.InventoryService\n"
            "}\n\n"
            "func NewHealthHandler(service service.InventoryService) HealthHandler {\n"
            "\treturn HealthHandler{service: service}\n"
            "}\n\n"
            "func (handler HealthHandler) ServeHTTP(writer http.ResponseWriter, _ *http.Request) {\n"
            "\twriter.Header().Set(\"Content-Type\", \"application/json\")\n"
            "\t_ = json.NewEncoder(writer).Encode(handler.service.HealthPayload())\n"
            "}\n"
        ),
        "internal/http/handlers/inventory.go": (
            "package handlers\n\n"
            "import (\n"
            "\t\"encoding/json\"\n"
            "\t\"net/http\"\n"
            f"\t\"{module_name}/internal/service\"\n"
            ")\n\n"
            "type InventoryHandler struct {\n"
            "\tservice service.InventoryService\n"
            "}\n\n"
            "func NewInventoryHandler(service service.InventoryService) InventoryHandler {\n"
            "\treturn InventoryHandler{service: service}\n"
            "}\n\n"
            "func (handler InventoryHandler) ServeHTTP(writer http.ResponseWriter, _ *http.Request) {\n"
            "\twriter.Header().Set(\"Content-Type\", \"application/json\")\n"
            "\t_ = json.NewEncoder(writer).Encode(handler.service.InventoryPayload())\n"
            "}\n"
        ),
        "internal/service/inventory_service.go": (
            "package service\n\n"
            f"import \"{module_name}/internal/repository\"\n\n"
            "type InventoryService struct {\n"
            "\trepo repository.InventoryRepository\n"
            "}\n\n"
            "func NewInventoryService(repo repository.InventoryRepository) InventoryService {\n"
            "\treturn InventoryService{repo: repo}\n"
            "}\n\n"
            "func (service InventoryService) HealthPayload() map[string]any {\n"
            "\treturn map[string]any{\"ok\": true, \"service\": \"go-ops-service\"}\n"
            "}\n\n"
            "func (service InventoryService) InventoryPayload() map[string]any {\n"
            "\treturn map[string]any{\"items\": service.repo.List(), \"count\": len(service.repo.List())}\n"
            "}\n"
        ),
        "internal/repository/inventory_repository.go": (
            "package repository\n\n"
            f"import \"{module_name}/internal/domain\"\n\n"
            "type InventoryRepository struct{}\n\n"
            "func NewInventoryRepository() InventoryRepository {\n"
            "\treturn InventoryRepository{}\n"
            "}\n\n"
            "func (InventoryRepository) List() []domain.InventoryItem {\n"
            "\treturn []domain.InventoryItem{{ID: \"item-1\", Name: \"Starter\", Quantity: 3}}\n"
            "}\n"
        ),
        "internal/platform/runtime_store.go": (
            "package platform\n\n"
            "func ReadRuntimeProfile() map[string]any {\n"
            "\treturn map[string]any{\"profile\": \"local-deterministic\", \"ready\": true}\n"
            "}\n"
        ),
        "internal/domain/inventory.go": (
            "package domain\n\n"
            "type InventoryItem struct {\n"
            "\tID string `json:\"id\"`\n"
            "\tName string `json:\"name\"`\n"
            "\tQuantity int `json:\"quantity\"`\n"
            "}\n"
        ),
    }


def _build_rust_service_files(project_name: str, task: str) -> Dict[str, str]:
    crate_name = project_name.replace('-', '_')
    return {
        "README.md": (
            f"# {project_name}\n\n"
            "Generated Rust operational service scaffold.\n\n"
            f"- task: {task}\n"
            "- validation: cargo check\n"
        ),
        "Cargo.toml": (
            "[package]\n"
            f"name = \"{crate_name}\"\n"
            "version = \"0.1.0\"\n"
            "edition = \"2021\"\n\n"
            "[dependencies]\n"
            "axum = \"0.7\"\n"
            "serde = { version = \"1\", features = [\"derive\"] }\n"
            "serde_json = \"1\"\n"
            "tokio = { version = \"1\", features = [\"macros\", \"rt-multi-thread\"] }\n"
        ),
        "src/main.rs": (
            "mod app;\nmod domain;\nmod http;\nmod platform;\nmod repository;\nmod service;\n\n"
            "#[tokio::main]\n"
            "async fn main() {\n"
            "    let app = app::build_app();\n"
            "    let listener = tokio::net::TcpListener::bind(\"0.0.0.0:8080\").await.expect(\"bind\");\n"
            "    axum::serve(listener, app).await.expect(\"serve\");\n"
            "}\n"
        ),
        "src/app.rs": (
            "use axum::Router;\n\n"
            "use crate::http::router::build_router;\n"
            "use crate::repository::order_repository::OrderRepository;\n"
            "use crate::service::order_service::OrderService;\n\n"
            "pub fn build_app() -> Router {\n"
            "    let repository = OrderRepository::new();\n"
            "    let service = OrderService::new(repository);\n"
            "    build_router(service)\n"
            "}\n"
        ),
        "src/http/mod.rs": "pub mod handlers;\npub mod router;\n",
        "src/http/router.rs": (
            "use axum::{routing::get, Router};\n\n"
            "use crate::http::handlers::{health_handler, list_orders_handler};\n"
            "use crate::service::order_service::OrderService;\n\n"
            "pub fn build_router(service: OrderService) -> Router {\n"
            "    Router::new()\n"
            "        .route(\"/health\", get(health_handler))\n"
            "        .route(\"/orders\", get(move || list_orders_handler(service.clone())))\n"
            "}\n"
        ),
        "src/http/handlers.rs": (
            "use axum::Json;\nuse serde_json::{json, Value};\n\n"
            "use crate::service::order_service::OrderService;\n\n"
            "pub async fn health_handler() -> Json<Value> {\n"
            "    Json(json!({\"ok\": true, \"service\": \"rust-ops-service\"}))\n"
            "}\n\n"
            "pub async fn list_orders_handler(service: OrderService) -> Json<Value> {\n"
            "    Json(json!({\"items\": service.list_orders()}))\n"
            "}\n"
        ),
        "src/service/mod.rs": "pub mod order_service;\n",
        "src/service/order_service.rs": (
            "use crate::repository::order_repository::OrderRepository;\nuse crate::domain::order::Order;\n\n"
            "#[derive(Clone)]\n"
            "pub struct OrderService {\n"
            "    repository: OrderRepository,\n"
            "}\n\n"
            "impl OrderService {\n"
            "    pub fn new(repository: OrderRepository) -> Self {\n"
            "        Self { repository }\n"
            "    }\n\n"
            "    pub fn list_orders(&self) -> Vec<Order> {\n"
            "        self.repository.list()\n"
            "    }\n"
            "}\n"
        ),
        "src/repository/mod.rs": "pub mod order_repository;\n",
        "src/repository/order_repository.rs": (
            "use crate::domain::order::Order;\n\n"
            "#[derive(Clone)]\n"
            "pub struct OrderRepository;\n\n"
            "impl OrderRepository {\n"
            "    pub fn new() -> Self {\n"
            "        Self\n"
            "    }\n\n"
            "    pub fn list(&self) -> Vec<Order> {\n"
            "        vec![Order { id: \"ord-1\".into(), customer: \"metanova\".into(), total: 120000 }]\n"
            "    }\n"
            "}\n"
        ),
        "src/platform/mod.rs": "pub mod runtime_store;\n",
        "src/platform/runtime_store.rs": (
            "pub fn read_runtime_profile() -> &'static str {\n"
            "    \"local-deterministic\"\n"
            "}\n"
        ),
        "src/domain/mod.rs": "pub mod order;\n",
        "src/domain/order.rs": (
            "use serde::Serialize;\n\n"
            "#[derive(Clone, Serialize)]\n"
            "pub struct Order {\n"
            "    pub id: String,\n"
            "    pub customer: String,\n"
            "    pub total: i64,\n"
            "}\n"
        ),
    }


def build_non_python_generation_plan(*, project_name: str, profile: str, task: str, output_dir: Path) -> NonPythonGenerationPlan:
    normalized_profile = str(profile or '').strip().lower()
    if normalized_profile == 'nextjs_react':
        artifacts = _normalize_artifacts(_build_nextjs_vertical_slice_files(project_name))
    elif normalized_profile == 'node_service':
        artifacts = _normalize_artifacts(_build_node_service_vertical_slice_files(project_name))
    elif normalized_profile == 'go_service':
        artifacts = _normalize_artifacts(_build_go_service_files(project_name, task))
    elif normalized_profile == 'rust_service':
        artifacts = _normalize_artifacts(_build_rust_service_files(project_name, task))
    else:
        raise ValueError(f'unsupported non-python generation profile: {profile}')

    return NonPythonGenerationPlan(
        project_name=project_name,
        profile=normalized_profile,
        task=task,
        output_dir=output_dir,
        artifacts=artifacts,
    )


def write_non_python_generation_plan(plan: NonPythonGenerationPlan) -> List[str]:
    written_files: List[str] = []
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    for artifact in plan.artifacts:
        target_path = plan.output_dir / artifact.path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(artifact.content, encoding='utf-8')
        written_files.append(str(target_path.relative_to(plan.output_dir)).replace('\\', '/'))
    return written_files


__all__ = [
    'NonPythonGenerationPlan',
    'SUPPORTED_NON_PYTHON_PROFILES',
    'build_non_python_generation_plan',
    'write_non_python_generation_plan',
]
