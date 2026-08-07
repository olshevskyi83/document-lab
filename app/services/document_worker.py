from __future__ import annotations

import logging
import threading
from pathlib import Path

from app.config import Settings
from app.db import Database
from app.models import DocumentMetadata, ProcessingReport, TaskStatus, utc_now
from app.services.basic_formats import process_docx, process_epub, process_text
from app.services.commands import CommandCancelledError
from app.services.deletion import DeletionError, cleanup_cancelled_task
from app.services.djvu import process_djvu
from app.services.ocr import parse_ocr_languages
from app.services.pdf import process_pdf
from app.services.quality import evaluate_text_quality
from app.services.report import atomic_write_model, atomic_write_text
from app.services.text_cleanup import clean_text

logger = logging.getLogger(__name__)


class ProcessingCancelled(RuntimeError):
    pass


class DocumentProcessor:
    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database

    def process(self, task_id: int) -> None:
        task = self.database.get_task(task_id)
        if task is None:
            return
        source = Path(task.source_path)
        self._check_cancelled(task_id)
        self._progress(task_id, "preparing", "Підготовка документа", 5)
        if not source.is_file():
            raise FileNotFoundError(f"Source document no longer exists: {source}")
        languages = parse_ocr_languages(task.ocr_languages)
        self._check_cancelled(task_id)
        def callback(stage: str, detail: str | None, progress: int | None) -> None:
            self._progress(task_id, stage, detail, progress)
        if task.format == "pdf":
            extraction = process_pdf(
                source,
                languages,
                self.settings.command_timeout_seconds,
                progress_callback=callback,
                cancel_check=lambda: self.database.is_cancellation_requested(task_id),
            )
        elif task.format == "djvu":
            extraction = process_djvu(
                source,
                languages,
                self.settings.command_timeout_seconds,
                progress_callback=callback,
                cancel_check=lambda: self.database.is_cancellation_requested(task_id),
            )
        elif task.format in {"txt", "md"}:
            self._progress(task_id, "extracting", "Витягування тексту", 30)
            extraction = process_text(source, task.format)
        elif task.format == "docx":
            self._progress(task_id, "extracting", "Витягування DOCX", 30)
            extraction = process_docx(source)
        elif task.format == "epub":
            self._progress(task_id, "extracting", "Витягування EPUB", 30)
            extraction = process_epub(source)
        else:
            raise ValueError(f"Unsupported format: {task.format}")
        self._check_cancelled(task_id)
        self._progress(task_id, "cleaning", "Очищення тексту", 80)
        text = clean_text(extraction.text)
        self._check_cancelled(task_id)
        self._progress(task_id, "verifying", "Перевірка повноти extraction", 90)
        quality = evaluate_text_quality(text, extraction.page_count)
        quality.warnings = list(dict.fromkeys([*quality.warnings, *extraction.warnings]))
        now = utc_now()
        metadata = DocumentMetadata(
            document_id=task.document_id,
            sha256=task.sha256,
            source_filename=task.source_filename,
            source_type="library" if task.source_path.startswith(str(self.settings.library_root)) else "upload",
            content_type=task.content_type,
            catalog=task.catalog,
            relative_path=task.relative_path,
            title=extraction.title or source.stem,
            author=extraction.author,
            format=task.format,
            page_count=extraction.page_count,
            initial_text_page_count=extraction.initial_text_page_count,
            initial_text_coverage=extraction.initial_text_coverage,
            text_page_count=extraction.text_page_count,
            text_coverage=extraction.text_coverage,
            partially_scanned=extraction.partially_scanned,
            extraction_method=extraction.method,
            ocr_used=extraction.ocr_used,
            ocr_languages=extraction.ocr_languages,
            ocr_page_count=extraction.ocr_page_count,
            ocr_strategy=extraction.ocr_strategy,
            ghostscript_version=extraction.ghostscript_version,
            character_count=len(text),
            word_count=quality.word_count,
            created_at=task.created_at,
            processed_at=now,
            pipeline_version=self.settings.pipeline_version,
        )
        report = ProcessingReport(
            document_id=task.document_id,
            status=TaskStatus.READY,
            metadata=metadata,
            quality=quality,
            warnings=quality.warnings,
            source_reference=task.source_path,
        )
        output = self.settings.ready_root / task.document_id
        text_path = output / "text.txt"
        metadata_path = output / "metadata.json"
        report_path = self.settings.reports_root / f"{task.document_id}.json"
        self._check_cancelled(task_id)
        self._progress(task_id, "writing", "Запис результатів", 95)
        atomic_write_text(text_path, text)
        self._check_cancelled(task_id)
        atomic_write_model(metadata_path, metadata)
        self._check_cancelled(task_id)
        atomic_write_model(report_path, report)
        self._check_cancelled(task_id)
        if not self.database.finish_processing(
            task_id,
            status=TaskStatus.READY,
            progress=100,
            stage="ready",
            stage_detail="Готово до підтвердження",
            text_path=str(text_path),
            metadata_path=str(metadata_path),
            report_path=str(report_path),
            error=None,
            finished_at=utc_now(),
        ):
            raise ProcessingCancelled("Processing cancelled before completion")

    def _check_cancelled(self, task_id: int) -> None:
        if self.database.is_cancellation_requested(task_id):
            raise ProcessingCancelled("Processing cancelled by user")

    def _progress(
        self, task_id: int, stage: str, detail: str | None, progress: int | None
    ) -> None:
        self._check_cancelled(task_id)
        values: dict[str, object] = {"stage": stage, "stage_detail": detail}
        if progress is not None:
            values["progress"] = max(0, min(100, progress))
        self.database.update_task(task_id, **values)


class Worker:
    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database
        self.processor = DocumentProcessor(settings, database)
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self.run, name="document-worker", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)

    def run(self) -> None:
        while not self.stop_event.is_set():
            task = self.database.claim_next_task()
            if task is None:
                self.stop_event.wait(self.settings.worker_poll_seconds)
                continue
            try:
                self.processor.process(task.id)
            except (ProcessingCancelled, CommandCancelledError):
                self._finish_cancelled(task.id)
            except Exception as exc:  # keep the recoverable worker alive
                if self.database.is_cancellation_requested(task.id):
                    self._finish_cancelled(task.id)
                    continue
                logger.exception("Document task %s failed", task.id)
                self.database.update_task(
                    task.id,
                    status=TaskStatus.FAILED,
                    progress=0,
                    stage="failed",
                    stage_detail=None,
                    finished_at=utc_now(),
                    error=str(exc),
                )

    def _finish_cancelled(self, task_id: int) -> None:
        logger.info("Document task %s cancelled", task_id)
        current = self.database.get_task(task_id)
        if not current:
            return
        try:
            cleanup_cancelled_task(current, self.settings)
            error = None
        except DeletionError as exc:
            logger.exception("Cancellation cleanup failed for task %s", task_id)
            error = f"Cancellation cleanup warning: {exc}"
        self.database.update_task(
            task_id,
            status=TaskStatus.CANCELLED,
            stage="cancelled",
            stage_detail=None,
            progress=0,
            finished_at=utc_now(),
            text_path=None,
            metadata_path=None,
            report_path=None,
            error=error,
        )
