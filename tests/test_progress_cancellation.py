from __future__ import annotations

import signal
import subprocess
import threading
import time
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as web
from app.config import Settings
from app.db import Database
from app.models import TaskStatus
from app.services import commands, djvu, pdf
from app.services.commands import CommandCancelledError
from app.services.deletion import delete_managed_document
from app.services.document_worker import DocumentProcessor, Worker
from app.services.intake import enqueue_document, store_upload


def environment(tmp_path: Path) -> tuple[Settings, Database]:
    settings = Settings(tmp_path / "remote", tmp_path / "data", tmp_path / "logs")
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    return settings, database


def test_cancel_queued_upload_is_immediate_and_removes_original(tmp_path: Path):
    settings, database = environment(tmp_path)
    original = store_upload(BytesIO(b"queued upload"), "queued.txt", settings)
    task = enqueue_document(original, settings=settings, database=database)

    assert database.request_cancellation(task.id) == TaskStatus.CANCELLED
    from app.services.deletion import cleanup_cancelled_task

    cleanup_cancelled_task(database.get_task(task.id), settings)
    cancelled = database.get_task(task.id)
    assert cancelled.status == TaskStatus.CANCELLED
    assert not original.exists()
    assert database.claim_next_task() is None


def test_cancelled_task_can_be_deleted(tmp_path: Path):
    settings, database = environment(tmp_path)
    original = store_upload(BytesIO(b"cancel then delete"), "cancelled.txt", settings)
    task = enqueue_document(original, settings=settings, database=database)
    assert database.request_cancellation(task.id) == TaskStatus.CANCELLED
    from app.services.deletion import cleanup_cancelled_task

    cleanup_cancelled_task(database.get_task(task.id), settings)
    delete_managed_document(task.id, settings, database)
    assert database.get_task(task.id) is None


def test_djvu_progress_and_cancellation_stop_between_pages(monkeypatch, tmp_path: Path):
    source = tmp_path / "book.djvu"
    source.write_bytes(b"AT&TFORM")
    monkeypatch.setattr(djvu, "djvu_page_count", lambda *_: 3)
    monkeypatch.setattr(djvu, "extract_hidden_text", lambda *_: "")

    def fake_run(arguments, **_kwargs):
        pattern = arguments[-1]
        for number in range(1, 4):
            Path(pattern.replace("%06d", f"{number:06d}")).write_bytes(b"image")
        return SimpleNamespace(stdout="", stderr="")

    completed_pages = []
    monkeypatch.setattr(djvu, "run_command", fake_run)
    monkeypatch.setattr(djvu, "ocr_image", lambda *_args, **_kwargs: "meaningful " * 100)

    with pytest.raises(CommandCancelledError):
        djvu.process_djvu(
            source,
            ["eng"],
            60,
            progress_callback=lambda stage, detail, progress: completed_pages.append(
                (stage, detail, progress)
            ),
            cancel_check=lambda: any(item[0] == "djvu_ocr" for item in completed_pages),
        )

    page_updates = [item for item in completed_pages if item[0] == "djvu_ocr"]
    assert len(page_updates) == 1
    assert page_updates[0][1] == "Сторінка 1 з 3"
    assert page_updates[0][2] > 25


def test_pdf_ocr_is_cancellable_and_reports_indeterminate_stage(monkeypatch, tmp_path: Path):
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF")
    monkeypatch.setattr(pdf, "pdf_info", lambda *_: {"pages": "70"})
    monkeypatch.setattr(pdf, "extract_pdf_text", lambda *_: "")
    received = {}

    def fake_ocr(*_args, **kwargs):
        received.update(kwargs)
        raise CommandCancelledError("cancelled")

    monkeypatch.setattr(pdf, "ocr_pdf", fake_ocr)
    updates = []
    with pytest.raises(CommandCancelledError):
        pdf.process_pdf(
            source,
            ["eng"],
            60,
            progress_callback=lambda *values: updates.append(values),
            cancel_check=lambda: False,
        )
    assert callable(received["cancel_check"])
    assert ("pdf_ocr", "70 сторінок", None) in updates


def test_cancelled_processing_never_becomes_ready_and_leaves_no_outputs(tmp_path: Path):
    settings, database = environment(tmp_path)
    source = settings.library_root / "book.txt"
    source.write_text("content " * 100, encoding="utf-8")
    task = enqueue_document(source, settings=settings, database=database)
    database.claim_next_task()
    assert database.request_cancellation(task.id) == TaskStatus.CANCELLING

    with pytest.raises(Exception, match="cancelled"):
        DocumentProcessor(settings, database).process(task.id)

    current = database.get_task(task.id)
    assert current.status == TaskStatus.CANCELLING
    assert not (settings.ready_root / task.document_id).exists()
    assert not (settings.reports_root / f"{task.document_id}.json").exists()


@pytest.mark.parametrize(
    ("filename", "content"),
    (("scan.pdf", b"%PDF-1.7"), ("scan.djvu", b"AT&TFORM")),
)
def test_worker_finishes_pdf_or_djvu_cancellation_as_cancelled(
    tmp_path: Path, filename: str, content: bytes
):
    settings = Settings(
        tmp_path / "remote",
        tmp_path / "data",
        tmp_path / "logs",
        worker_poll_seconds=0.01,
    )
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    source = settings.library_root / filename
    source.write_bytes(content)
    task = enqueue_document(source, settings=settings, database=database)
    worker = Worker(settings, database)
    started = threading.Event()

    def cancellable_process(task_id: int):
        started.set()
        while not database.is_cancellation_requested(task_id):
            time.sleep(0.005)
        raise CommandCancelledError("external OCR stopped")

    worker.processor.process = cancellable_process
    worker.start()
    try:
        assert started.wait(timeout=1)
        assert database.request_cancellation(task.id) == TaskStatus.CANCELLING
        deadline = time.monotonic() + 1
        while database.get_task(task.id).status != TaskStatus.CANCELLED:
            assert time.monotonic() < deadline
            time.sleep(0.01)
    finally:
        worker.stop()

    cancelled = database.get_task(task.id)
    assert cancelled.status == TaskStatus.CANCELLED
    assert cancelled.status != TaskStatus.COMPLETED
    assert cancelled.finished_at
    assert not (settings.ready_root / task.document_id).exists()


def test_command_cancellation_terminates_process_group(monkeypatch):
    class FakeProcess:
        pid = 4321
        returncode = None

        def communicate(self, timeout=None):
            if timeout is not None and self.returncode is None:
                raise subprocess.TimeoutExpired("ocrmypdf", timeout)
            return "", ""

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.returncode = -signal.SIGTERM
            return self.returncode

    process = FakeProcess()
    signals = []
    monkeypatch.setattr(commands, "require_binary", lambda name: name)
    monkeypatch.setattr(commands.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(commands, "os_killpg", lambda pid, sig: signals.append((pid, sig)))

    with pytest.raises(CommandCancelledError):
        commands.run_command(["ocrmypdf", "in.pdf", "out.pdf"], timeout=60, cancel_check=lambda: True)

    assert signals == [(4321, signal.SIGTERM)]
    assert process.returncode is not None


def test_current_counters_ignore_duplicates_and_deleted_id_gaps(tmp_path: Path):
    settings, database = environment(tmp_path)
    first = settings.library_root / "one.txt"
    second = settings.library_root / "two.txt"
    third = settings.library_root / "three.txt"
    for number, path in enumerate((first, second, third), 1):
        path.write_text(f"content {number}", encoding="utf-8")
    tasks = [enqueue_document(path, settings=settings, database=database) for path in (first, second, third)]
    database.delete_task_record(tasks[1].id)
    duplicate = database.create_task(
        document_id="duplicate",
        sha256=tasks[0].sha256,
        source_path=str(first),
        source_filename="copy.txt",
        relative_path="copy.txt",
        catalog="",
        format="txt",
        content_type="auto",
        ocr_languages="auto",
        status=TaskStatus.DUPLICATE,
        progress=100,
        duplicate_of=tasks[0].document_id,
    )
    assert duplicate.id > tasks[2].id
    counts = database.task_counts()
    assert counts["documents"] == 2
    assert counts["waiting"] == 2


def test_status_endpoint_and_polling_ui(monkeypatch, tmp_path: Path):
    settings, database = environment(tmp_path)
    source = settings.library_root / "status.txt"
    source.write_text("status", encoding="utf-8")
    task = enqueue_document(source, settings=settings, database=database)
    monkeypatch.setattr(web, "settings", settings)
    monkeypatch.setattr(web, "database", database)
    client = TestClient(web.app)

    payload = client.get("/api/tasks/status").json()
    assert payload["tasks"][0]["id"] == task.id
    assert payload["tasks"][0]["stage"] == "queued"
    dashboard = client.get("/").text
    assert "Internal task ID" not in dashboard
    assert "count-documents" in dashboard
    assert "/static/tasks.js" in dashboard
    script = client.get("/static/tasks.js").text
    assert 'fetch("/api/tasks/status"' in script
    assert "location.reload" not in script


def test_completed_txt_has_persisted_stages_timing_and_full_progress(tmp_path: Path):
    settings, database = environment(tmp_path)
    source = settings.library_root / "complete.txt"
    source.write_text("meaningful content " * 100, encoding="utf-8")
    task = enqueue_document(source, settings=settings, database=database)
    database.claim_next_task()
    DocumentProcessor(settings, database).process(task.id)
    completed = database.get_task(task.id)
    assert completed.status == TaskStatus.READY
    assert completed.stage == "ready"
    assert completed.progress == 100
    assert completed.started_at
    assert completed.finished_at
