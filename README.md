# Document Lab

Document Lab is a local, Dockerized document-ingestion service for an Ubuntu homelab. It scans an unrestricted, recursively discovered Library tree or accepts uploads through a FastAPI/Jinja2 UI. Library scans silently skip content whose SHA-256 is already known and enqueue only new or changed files. Extraction results wait for manual approval before they are considered complete.

Document Lab deliberately does **not** connect directly to Qdrant, an LLM, or
an embedding service. Approved documents can be registered, indexed,
reindexed, and removed through the generic Homelab Core Knowledge API.

## MVP pipeline

```text
upload / Library scan
→ format detection
→ SHA-256 and duplicate check
→ persisted SQLite queue
→ extraction / conditional OCR
→ text cleanup and quality report
→ atomic output writes
→ preview
→ manual approval
→ approved uploads copied into the selected Library catalog
```

Supported formats: text PDF, scanned PDF, hidden-text DJVU, scanned DJVU, TXT, Markdown, EPUB, and DOCX.

### Upload versus Library import

- **Upload** is the normal Document Lab workflow: the immutable original is stored outside Library, processed and previewed, and copied into the selected Library catalog only after manual approval. Pending, failed, duplicate, deleted, and unapproved uploads do not enter Library.
- **Library import scan** is only for documents placed directly into `/remote/Documents/Library` outside Document Lab—for example through SMB, Finder, rsync, or the shell. It imports only new or changed SHA-256 content. Files already managed by Document Lab, including approved uploads now present in Library, are silently skipped and do not create another task.

- PDF: `pdftotext` first; OCRmyPDF only when quality is insufficient; then `pdftotext` again.
- Partial-PDF OCR detects Ghostscript automatically. Ghostscript 10.00.0–10.02.0, unavailable, or unparseable versions use a conservative force-OCR compatibility strategy instead of `--redo-ocr`.
- Every OCRmyPDF strategy explicitly uses `--output-type pdf`; PDF/A generation is disabled because OCR PDFs are temporary inputs for `pdftotext`, not archival artifacts.
- `pikepdf==9.11.0` is pinned for OCRmyPDF 16.10.4 compatibility. OCRmyPDF allows `pikepdf>=8.10.1` without an upper bound, but pikepdf 10 removed the deprecated `Pdf.check()` method still called by OCRmyPDF 16.10.4 after OCR. The 9.11.0 pin retains that API and provides reproducible Python 3.12 Linux wheels.
- DJVU: `djvutxt`/`djvused` first; `ddjvu` plus Tesseract only when hidden text is insufficient.
- OCR selections map explicitly to Tesseract models: Russian `rus`, Ukrainian `ukr`, English `eng`, German `deu`, Spanish `spa`, plus `rus+eng`, `ukr+eng`, `rus+ukr`, and `rus+ukr+eng` combinations.
- Smart Auto never delegates language choice to Tesseract's implicit default. It uses available hidden/sample text first; for fully scanned input it compares OCR from at most three representative sample pages with Russian, Ukrainian, combined Cyrillic/English, and English candidates, then OCRs the full document once with the best-scoring set.
- OCR quality includes Unicode script/language heuristics and a plausibility score based on script agreement, vowel/consonant noise, malformed tokens, excessive case noise, and mixed-script tokens. Abundant but implausible text is marked insufficient with a wrong-language/corruption warning.
- Worker concurrency is intentionally one. HTTP requests never run OCR.

### Progress and cancellation

The worker persists a numeric `progress`, a machine-readable `stage`, optional `stage_detail`, and `started_at`/`finished_at` timestamps. DJVU fallback reports every completed OCR page. OCRmyPDF runs as an indeterminate `pdf_ocr` stage because its output does not provide a stable page-progress interface; the UI shows the known document page count without inventing a time-based percentage.

The dashboard polls `/api/tasks/status` every two seconds and updates only status badges, progress, stage details, timings, counters, and action buttons. Upload and catalog forms are never interrupted by a full-page refresh. SQLite task IDs remain internal route/debug identifiers; dashboard counters describe current non-duplicate task rows and do not depend on AUTOINCREMENT history.

Queued tasks can be cancelled immediately. Processing tasks transition through `cancelling` to `cancelled`. External commands run in their own process session, so cancellation terminates OCRmyPDF/Tesseract/Ghostscript/ddjvu and their children as one process group. DJVU OCR also checks cancellation between pages. Cancelled work does not publish text, metadata, or reports; task-owned temporary/output files and uploaded originals are cleaned, while pre-existing Library sources are retained until the user explicitly deletes the cancelled document.

## Storage

The host directory `/home/homelabuser/RemoteDrop` is mounted at `/remote` and should contain:

```text
/remote/Documents/
├── incoming/
├── processing/
├── ready/
├── failed/
├── reports/
├── originals/
├── imports/
└── Library/
```

Missing directories are created at startup. Library categories are never hardcoded: all nested directories are discovered recursively. Uploaded originals are stored under `originals/` with collision-resistant names. An upload enters its selected Library catalog only after manual approval; failed, duplicate, deleted, and unapproved uploads do not. Sources discovered by a Library scan stay in place and are never copied again on approval.

### Document deletion

The normal Delete action removes a document from Document Lab completely. After validating exact stored paths, regular-file type, managed-root containment, absence of symlinks, and SHA-256 identity for originals, it removes generated text, metadata, report, the Document Lab-owned upload original, and the managed Library copy when present. It never removes catalog directories or searches by filename.

Deletion is refused while a task is processing or cancelling; cancel it first. If a managed source or Library file cannot be removed safely, the database task is kept and the UI reports the error. Missing optional generated artifacts do not prevent cleanup.

Local deletion and Knowledge deletion are intentionally independent. Removing a
document from Homelab Knowledge deletes its vectors and records Core status
`deleted`, but keeps the original, extracted TXT, metadata, and report. Removing
a local Document Lab task does not silently cascade into Knowledge.

On startup, SQLite is migrated in place with nullable `library_path`, `stage_detail`, `started_at`, and `finished_at` columns plus a defaulted `stage` column when needed. Existing tasks remain readable; Document Lab backfills `library_path` only when an exact existing Library file can be validated against the task SHA-256.

Completed extraction artifacts:

```text
/remote/Documents/ready/<document_id>/text.txt
/remote/Documents/ready/<document_id>/metadata.json
/remote/Documents/reports/<document_id>.json
```

Metadata and reports include document pages when determinable, meaningful text pages, OCR pages, extraction coverage, OCR strategy, Ghostscript version, and completeness warnings. DOCX and reflowable EPUB pagination is explicitly reported as unknown because reliable physical page counts require rendering.

SQLite state is stored at `./data/document_lab.sqlite3` on the host. Interrupted `processing` tasks return to `queued` on application restart. Exact SHA-256 duplicates are marked `duplicate` and are not queued; equal filenames with different content are distinct.

## Ubuntu/Docker deployment

Run these exact commands from the repository checkout:

```bash
cd /path/to/document-lab
mkdir -p /home/homelabuser/RemoteDrop/Documents/{incoming,processing,ready,failed,reports,originals,imports,Library}
mkdir -p data logs
docker network inspect ai-network >/dev/null 2>&1 || docker network create ai-network
APP_UID=1000 APP_GID=1000 docker compose build --pull
docker compose up -d
```

Open `http://SERVER_IP:3012/`.

### Deployment verification

```bash
docker compose ps
docker compose logs --tail=100 document-lab
curl --fail http://127.0.0.1:3012/health
docker compose exec document-lab pdftotext -v
docker compose exec document-lab pdfinfo -v
docker compose exec document-lab ocrmypdf --version
docker compose exec document-lab tesseract --version
docker compose exec document-lab tesseract --list-langs
docker compose exec document-lab djvutxt --help
docker compose exec document-lab ddjvu --help
docker compose exec document-lab djvused --help
```

The `/health` response reports the installed OCRmyPDF, pikepdf, Ghostscript, and Tesseract versions under `dependencies`.

`tesseract --list-langs` should include `deu`, `eng`, `rus`, `spa`, and `ukr`.

## Local development and tests

Python 3.11+ is required.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
pytest -q
python -m compileall -q app tests
```

For local startup, provide writable directories instead of the container defaults:

```bash
REMOTE_ROOT="$PWD/.runtime/remote" \
DATA_ROOT="$PWD/.runtime/data" \
LOGS_ROOT="$PWD/.runtime/logs" \
uvicorn app.main:app --host 127.0.0.1 --port 3012
```

## Manual verification checklist

1. Open the dashboard and create nested catalogs such as `Biology/Mycology`.
2. Confirm absolute paths, `..`, traversal paths, and deletion of a non-empty catalog are rejected.
3. Create a directory manually under `RemoteDrop/Documents/Library`, refresh, and confirm it appears.
4. Upload TXT and Markdown files; confirm they pass through queued/processing/ready.
5. Before approval, confirm an upload exists under `originals/` but not in its selected Library catalog. Preview it, approve it, then confirm it appears in Library.
6. Upload the same bytes under another filename; confirm the task is clearly marked duplicate and is not processed.
7. Upload different bytes with the same filename; confirm a distinct task is queued.
8. Upload a PDF with a good text layer; confirm `extraction_method` is `pdf_text` and `ocr_used` is false.
9. Upload a scanned PDF; confirm `extraction_method` is `pdf_ocr` and selected OCR languages are recorded. For a partially scanned PDF, confirm `pdf_partial_ocr`, page coverage metadata, and a partial OCR warning.
10. Upload hidden-text and scanned DJVU samples; confirm `djvu_hidden_text` and `djvu_ocr` respectively, with explicit page markers for OCR.
11. Upload EPUB and DOCX samples and review title/author extraction.
12. Cancel queued PDF/DJVU uploads and confirm they never start. Cancel active OCR, confirm `cancelling` becomes `cancelled`, and verify no OCR child process or partial output remains.
13. Stop the container while a task is processing, start it again, and confirm the task safely returns to the queue.
14. Retry a failed task. Delete a non-processing task and confirm its managed original, Library copy, and generated artifacts are removed while catalog directories remain intact.
15. For an approved document, test Add, Reindex, and Remove from Knowledge. Confirm removal leaves all local artifacts intact and that Document Lab makes requests only to Homelab Core, never directly to Qdrant.

## Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `REMOTE_ROOT` | `/remote` | RemoteDrop mount |
| `DATA_ROOT` | `/app/data` | SQLite data |
| `LOGS_ROOT` | `/app/logs` | Application logs |
| `WORKER_POLL_SECONDS` | `1` | Queue polling interval |
| `COMMAND_TIMEOUT_SECONDS` | `3600` | Extraction/OCR subprocess timeout |
| `CORE_URL` | `http://ai-gateway:8080` | Homelab Core base URL |
| `CORE_TIMEOUT_SECONDS` | `300` | Knowledge API request timeout |

Docker build arguments and the Compose runtime user default to `APP_UID=1000` and `APP_GID=1000`. Override both only when the owning homelab account uses different IDs. Document Lab never recursively changes ownership of `/home/homelabuser/RemoteDrop`.

## Known MVP limitations

- Work is handled by one in-process background thread, suitable for a single Uvicorn process only. Do not add multiple Uvicorn workers.
- OCR language `auto` lets Tesseract/OCRmyPDF use its configured default; it does not run all installed languages or perform language detection.
- Quality scoring is heuristic. Partially scanned documents receive warnings, but per-page selective OCR is not yet implemented.
- Encrypted or severely damaged files fail with a persisted error and can be retried after correction.
- EPUB reading order follows EbookLib document item order and may vary for unusual EPUB packages.
- Deleting a task removes its exact managed upload original, Library copy, and generated artifacts after safety and SHA-256 validation; it never deletes parent catalog directories.
