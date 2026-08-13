from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.db import Database
from app.models import DocumentType, TaskStatus
from app.services.document_worker import Worker
from app.services.dependencies import dependency_versions
from app.services.deletion import (
    DeletionError,
    backfill_managed_library_paths,
    cleanup_cancelled_task,
    delete_managed_document,
)
from app.services.intake import (
    LibraryScanResult,
    enqueue_document,
    enqueue_new_library_documents,
    place_approved_upload,
    store_upload,
)
from app.services.knowledge_gateway import KnowledgeGateway, KnowledgeGatewayError
from app.services.library import (
    create_catalog,
    delete_empty_catalog,
    discover_catalogs,
    library_tree,
    rename_catalog,
    safe_library_path,
)
from app.services.ocr import OCR_LANGUAGE_LABELS, OCR_LANGUAGE_OPTIONS
from app.services.report import atomic_write_text

settings = get_settings()
database = Database(settings.database_path)
worker = Worker(settings, database)
knowledge_gateway = KnowledgeGateway(settings)
BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")
runtime_dependencies: dict[str, str] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.ensure_directories()
    database.initialize()
    backfill_managed_library_paths(settings, database)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(settings.logs_root / "document-lab.log"), logging.StreamHandler()],
    )
    for task in database.list_tasks_by_status(TaskStatus.CANCELLED):
        try:
            cleanup_cancelled_task(task, settings)
            database.update_task(task.id, text_path=None, metadata_path=None, report_path=None)
        except DeletionError as exc:
            logging.getLogger(__name__).warning(
                "Recovered cancellation cleanup failed for task %s: %s", task.id, exc
            )
    runtime_dependencies.clear()
    runtime_dependencies.update(dependency_versions())
    logging.getLogger(__name__).info("Dependency versions: %s", runtime_dependencies)
    worker.start()
    try:
        yield
    finally:
        worker.stop()


app = FastAPI(title="Document Lab", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


def redirect(message: str = "", error: str = "") -> RedirectResponse:
    query = f"?{urlencode({'message': message})}" if message else f"?{urlencode({'error': error})}" if error else ""
    return RedirectResponse(f"/{query}", status_code=303)


def library_scan_message(result: LibraryScanResult) -> str:
    if result.new > 0:
        message = f"Імпортовано нових файлів: {result.new}"
        return f"{message}. Помилок імпорту: {result.failed}" if result.failed else message
    if result.failed == 0:
        return "Нових файлів у Library не знайдено."
    return f"Нових файлів не імпортовано. Помилок імпорту: {result.failed}"


@app.get("/health")
def health() -> dict[str, object]:
    if not runtime_dependencies:
        runtime_dependencies.update(dependency_versions())
    return {
        "status": "ok",
        "pipeline_version": settings.pipeline_version,
        "dependencies": runtime_dependencies,
    }


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "tasks": database.list_tasks(),
            "counts": database.task_counts(),
            "catalogs": discover_catalogs(settings.library_root),
            "tree": library_tree(settings.library_root),
            "ocr_options": OCR_LANGUAGE_OPTIONS,
            "ocr_labels": OCR_LANGUAGE_LABELS,
            "document_types": list(DocumentType),
            "statuses": TaskStatus,
            "message": request.query_params.get("message", ""),
            "error": request.query_params.get("error", ""),
        },
    )


@app.get("/api/tasks/status")
def task_statuses() -> dict[str, object]:
    tasks = database.list_tasks()
    return {
        "counts": database.task_counts(),
        "tasks": [
            {
                "id": task.id,
                "status": task.status,
                "progress": task.progress,
                "stage": task.stage,
                "stage_detail": task.stage_detail,
                "updated_at": task.updated_at,
                "started_at": task.started_at,
                "finished_at": task.finished_at,
                "library_managed": bool(task.library_path),
            }
            for task in tasks
        ],
    }


@app.post("/upload")
def upload_document(
    file: UploadFile = File(...),
    ocr_languages: str = Form("auto"),
    content_type: str = Form("auto"),
    catalog: str = Form(""),
):
    try:
        catalog = catalog.strip()
        if not catalog:
            raise ValueError("Оберіть каталог Library перед запуском оцифрування")
        catalog_path = safe_library_path(settings.library_root, catalog)
        if not catalog_path.is_dir():
            raise ValueError("Selected catalog does not exist")
        original = store_upload(file.file, file.filename or "", settings)
        task = enqueue_document(
            original,
            settings=settings,
            database=database,
            content_type=content_type,
            ocr_languages=ocr_languages,
            catalog=catalog,
            source_filename=file.filename,
        )
        return redirect(message=f"{task.source_filename}: {task.status}")
    except Exception as exc:
        return redirect(error=str(exc))
    finally:
        file.file.close()


@app.post("/library/scan")
def scan_library(ocr_languages: str = Form("auto"), content_type: str = Form("auto")):
    result = enqueue_new_library_documents(
        settings=settings,
        database=database,
        content_type=content_type,
        ocr_languages=ocr_languages,
    )
    logging.getLogger(__name__).info(
        "Library import scan: new=%s skipped_known=%s failed=%s",
        result.new,
        result.already_known,
        result.failed,
    )
    return redirect(message=library_scan_message(result))


@app.post("/library/catalogs")
def add_catalog(path: str = Form(...)):
    try:
        create_catalog(settings.library_root, path)
        return redirect(message="Catalog created")
    except Exception as exc:
        return redirect(error=str(exc))


@app.post("/library/catalogs/rename")
def update_catalog(path: str = Form(...), new_name: str = Form(...)):
    try:
        rename_catalog(settings.library_root, path, new_name)
        return redirect(message="Catalog renamed")
    except Exception as exc:
        return redirect(error=str(exc))


@app.post("/library/catalogs/delete")
def remove_catalog(path: str = Form(...)):
    try:
        delete_empty_catalog(settings.library_root, path)
        return redirect(message="Catalog deleted")
    except Exception as exc:
        return redirect(error=str(exc))


@app.get("/tasks/{task_id}", response_class=HTMLResponse)
def task_detail(request: Request, task_id: int):
    task = database.get_task(task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    text = Path(task.text_path).read_text(encoding="utf-8") if task.text_path and Path(task.text_path).is_file() else ""
    metadata = json.loads(Path(task.metadata_path).read_text(encoding="utf-8")) if task.metadata_path and Path(task.metadata_path).is_file() else None
    report = json.loads(Path(task.report_path).read_text(encoding="utf-8")) if task.report_path and Path(task.report_path).is_file() else None
    method = metadata.get("extraction_method", "") if metadata else ""
    method = {"docx_text": "docx", "epub_text": "epub", "txt_text": "txt", "md_text": "md"}.get(method, method)

    # Check factual Core status (authoritative source of truth)
    # Query central registry (not single-document endpoint which may be stale)
    core_available = True
    try:
        knowledge_exists_in_core = knowledge_gateway.check_knowledge_exists(task.document_id)
    except KnowledgeGatewayError as exc:
        # An unavailable Core is not evidence that the document is absent.
        core_available = False
        knowledge_exists_in_core = False
        logging.getLogger(__name__).warning(
            "Could not read central Knowledge registry for task %s: %s", task_id, exc
        )

    # Reconcile local cache if stale
    # If central list says absent but local cache says indexed → stale cache
    if core_available and not knowledge_exists_in_core and task.knowledge_status == "indexed":
        logging.getLogger(__name__).info(
            "Reconciling stale local knowledge_status for task %s: "
            "central registry absent but local cache=indexed", task_id
        )
        database.update_knowledge(task_id, status="not_indexed", error=None)
        task = database.get_task(task_id)

    return templates.TemplateResponse(
        request,
        "task.html",
        {
            "task": task,
            "text": text,
            "metadata": metadata,
            "report": report,
            "processing_method": method,
            "core_available": core_available,
            "knowledge_exists_in_core": knowledge_exists_in_core,
        },
    )


@app.get("/tasks/{task_id}/download-text")
def download_task_text(task_id: int):
    task = database.get_task(task_id)
    if task is None or not task.text_path:
        raise HTTPException(404, "Extracted text not found")
    text_path = Path(task.text_path).resolve()
    ready_root = settings.ready_root.resolve()
    if ready_root not in text_path.parents or not text_path.is_file():
        raise HTTPException(404, "Extracted text not found")
    return FileResponse(
        text_path,
        media_type="text/plain; charset=utf-8",
        filename=f"{Path(task.source_filename).stem}.txt",
    )


def download_json_artifact(task_id: int, attribute: str, root: Path, suffix: str):
    task = database.get_task(task_id)
    path_value = getattr(task, attribute, None) if task else None
    if not path_value:
        raise HTTPException(404, f"{suffix.title()} not found")
    artifact = Path(path_value).resolve()
    resolved_root = root.resolve()
    if artifact.parent != resolved_root and resolved_root not in artifact.parents:
        raise HTTPException(404, f"{suffix.title()} not found")
    if not artifact.is_file():
        raise HTTPException(404, f"{suffix.title()} not found")
    return FileResponse(
        artifact,
        media_type="application/json",
        filename=f"{Path(task.source_filename).stem}-{suffix}.json",
    )


@app.get("/tasks/{task_id}/download-metadata")
def download_task_metadata(task_id: int):
    return download_json_artifact(task_id, "metadata_path", settings.ready_root, "metadata")


@app.get("/tasks/{task_id}/download-report")
def download_task_report(task_id: int):
    return download_json_artifact(task_id, "report_path", settings.reports_root, "report")


@app.post("/tasks/{task_id}/approve")
def approve_task(task_id: int):
    task = database.get_task(task_id)
    if task is None or task.status != TaskStatus.READY:
        return redirect(error="Only ready tasks can be approved")
    try:
        library_destination = place_approved_upload(task, settings)
    except Exception as exc:
        return redirect(error=f"Could not add approved upload to Library: {exc}")
    if not database.approve(task_id):
        return redirect(error="Only ready tasks can be approved")
    if library_destination:
        relative_destination = library_destination.relative_to(settings.documents_root).as_posix()
        database.update_task(
            task_id,
            relative_path=relative_destination,
            library_path=str(library_destination.resolve()),
        )
        if task.metadata_path and Path(task.metadata_path).is_file():
            metadata = json.loads(Path(task.metadata_path).read_text(encoding="utf-8"))
            metadata["relative_path"] = relative_destination
            atomic_write_text(Path(task.metadata_path), json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    if task and task.report_path and Path(task.report_path).is_file():
        report = json.loads(Path(task.report_path).read_text(encoding="utf-8"))
        report["status"] = TaskStatus.COMPLETED
        if library_destination:
            report["library_destination"] = library_destination.relative_to(settings.documents_root).as_posix()
            report["metadata"]["relative_path"] = library_destination.relative_to(settings.documents_root).as_posix()
        atomic_write_text(Path(task.report_path), json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return redirect(message="Document approved")


def update_knowledge_from_response(task_id: int, response: dict) -> str:
    status = str(response.get("status") or "unknown")
    database.update_knowledge(task_id, status=status, error=None)
    return status


@app.post("/tasks/{task_id}/knowledge/index")
def index_task_in_knowledge(task_id: int):
    task = database.get_task(task_id)
    if task is None:
        return redirect(error="Document not found")
    try:
        status = update_knowledge_from_response(task_id, knowledge_gateway.index(task))
    except KnowledgeGatewayError as exc:
        database.update_knowledge(task_id, error=str(exc))
        return redirect(error=str(exc))
    return redirect(message=f"Knowledge status: {status}")


@app.post("/tasks/{task_id}/knowledge/reindex")
def reindex_task_in_knowledge(task_id: int):
    task = database.get_task(task_id)
    if task is None:
        return redirect(error="Document not found")
    try:
        status = update_knowledge_from_response(
            task_id,
            knowledge_gateway.index(task, reindex=True),
        )
    except KnowledgeGatewayError as exc:
        database.update_knowledge(task_id, error=str(exc))
        return redirect(error=str(exc))
    return redirect(message=f"Knowledge status: {status}")


@app.post("/tasks/{task_id}/knowledge/delete")
def delete_task_from_knowledge(task_id: int):
    task = database.get_task(task_id)
    if task is None:
        return redirect(error="Document not found")
    try:
        status = update_knowledge_from_response(task_id, knowledge_gateway.delete(task))
    except KnowledgeGatewayError as exc:
        database.update_knowledge(task_id, error=str(exc))
        return redirect(error=str(exc))
    return redirect(message=f"Knowledge status: {status}")


@app.get("/api/tasks/{task_id}/knowledge/status")
def check_task_knowledge_status(task_id: int) -> dict[str, object]:
    """
    Read-only endpoint to check the factual Knowledge status from Homelab Core.

    Returns:
    {
        "exists_in_core": bool,  # Authoritative source of truth
        "local_cache": str,      # Local knowledge_status field value (for reference)
    }

    Homelab Core is the single source of truth for Knowledge membership.
    Does NOT trigger registration or indexing. It reconciles only a confirmed stale
    local `indexed` cache after a successful central-list read shows absence.
    """
    task = database.get_task(task_id)
    if task is None:
        raise HTTPException(404, "Task not found")

    # Check factual Core state (authoritative source of truth)
    try:
        exists_in_core = knowledge_gateway.check_knowledge_exists(task.document_id)
        core_available = True
    except KnowledgeGatewayError:
        exists_in_core = False
        core_available = False

    # If local cache is stale (says indexed but Core says no), reconcile it
    if core_available and not exists_in_core and task.knowledge_status == "indexed":
        logging.getLogger(__name__).info(
            "Reconciling stale local knowledge_status for task %s: "
            "local=indexed but Core has no document", task_id
        )
        database.update_knowledge(task_id, status="not_indexed", error=None)

    return {
        "core_available": core_available,
        "exists_in_core": exists_in_core,
        "local_cache": database.get_task(task_id).knowledge_status,
    }


@app.post("/tasks/{task_id}/retry")
def retry_task(task_id: int):
    if not database.retry(task_id):
        return redirect(error="Only failed tasks can be retried")
    return redirect(message="Document queued again")


@app.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: int):
    new_status = database.request_cancellation(task_id)
    if new_status is None:
        return redirect(error="Only queued or processing tasks can be cancelled")
    if new_status == TaskStatus.CANCELLED:
        task = database.get_task(task_id)
        if task:
            try:
                cleanup_cancelled_task(task, settings)
            except DeletionError as exc:
                database.update_task(task_id, error=f"Cancellation cleanup warning: {exc}")
                return redirect(error=f"Task cancelled, but cleanup needs attention: {exc}")
        return redirect(message="Task cancelled")
    return redirect(message="Cancellation requested")


@app.post("/tasks/{task_id}/delete")
def delete_task(task_id: int):
    try:
        delete_managed_document(task_id, settings, database)
    except DeletionError as exc:
        return redirect(error=str(exc))
    return redirect(message="Document and managed files deleted")
