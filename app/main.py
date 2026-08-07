from __future__ import annotations

import json
import logging
import shutil
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
from app.services.intake import (
    enqueue_document,
    enqueue_new_library_documents,
    place_approved_upload,
    store_upload,
)
from app.services.library import (
    create_catalog,
    delete_empty_catalog,
    discover_catalogs,
    library_tree,
    rename_catalog,
    safe_library_path,
)
from app.services.ocr import OCR_LANGUAGE_OPTIONS
from app.services.report import atomic_write_text

settings = get_settings()
database = Database(settings.database_path)
worker = Worker(settings, database)
BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.ensure_directories()
    database.initialize()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(settings.logs_root / "document-lab.log"), logging.StreamHandler()],
    )
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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "pipeline_version": settings.pipeline_version}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "tasks": database.list_tasks(),
            "catalogs": discover_catalogs(settings.library_root),
            "tree": library_tree(settings.library_root),
            "ocr_options": OCR_LANGUAGE_OPTIONS,
            "document_types": list(DocumentType),
            "statuses": TaskStatus,
            "message": request.query_params.get("message", ""),
            "error": request.query_params.get("error", ""),
        },
    )


@app.post("/upload")
def upload_document(
    file: UploadFile = File(...),
    ocr_languages: str = Form("auto"),
    content_type: str = Form("auto"),
    catalog: str = Form(""),
):
    try:
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
        return redirect(message=f"Task {task.id}: {task.status}")
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
    return redirect(
        message=f"Library scan: {result.new} new, {result.already_known} already known, {result.failed} failed"
    )


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
    return templates.TemplateResponse(
        request,
        "task.html",
        {"task": task, "text": text, "metadata": metadata, "report": report, "processing_method": method},
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
        database.update_task(task_id, relative_path=relative_destination)
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
    return redirect(message=f"Task {task_id} approved")


@app.post("/tasks/{task_id}/retry")
def retry_task(task_id: int):
    if not database.retry(task_id):
        return redirect(error="Only failed tasks can be retried")
    return redirect(message=f"Task {task_id} queued again")


@app.post("/tasks/{task_id}/delete")
def delete_task(task_id: int):
    task = database.delete_task(task_id)
    if task is None:
        return redirect(error="Task not found or currently processing")
    if task.text_path:
        output = Path(task.text_path).parent.resolve()
        ready = settings.ready_root.resolve()
        if ready in output.parents:
            shutil.rmtree(output, ignore_errors=True)
    if task.report_path:
        report = Path(task.report_path).resolve()
        if report.parent == settings.reports_root.resolve():
            report.unlink(missing_ok=True)
    return redirect(message=f"Task {task_id} deleted; original retained")
