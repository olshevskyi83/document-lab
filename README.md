# Document Lab

Document Lab is a local, Dockerized document-ingestion service for an Ubuntu homelab. It scans an unrestricted, recursively discovered Library tree or accepts uploads through a FastAPI/Jinja2 UI. Extraction results wait for manual approval before they are considered complete.

Document Lab deliberately does **not** connect to Qdrant, an LLM, or an embedding service. A disabled UI extension point documents the future boundary: `Add to Knowledge / Qdrant via Homelab Core`.

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
```

Supported formats: text PDF, scanned PDF, hidden-text DJVU, scanned DJVU, TXT, Markdown, EPUB, and DOCX.

- PDF: `pdftotext` first; OCRmyPDF only when quality is insufficient; then `pdftotext` again.
- DJVU: `djvutxt`/`djvused` first; `ddjvu` plus Tesseract only when hidden text is insufficient.
- OCR selections: auto, `ukr`, `rus`, `eng`, `deu`, `spa`, and useful two-language combinations.
- Worker concurrency is intentionally one. HTTP requests never run OCR.

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

Missing directories are created at startup. Library categories are never hardcoded: all nested directories are discovered recursively. Uploaded originals are stored under `originals/` with collision-resistant names and are not deleted when a task is deleted. Library source files are not modified.

Completed extraction artifacts:

```text
/remote/Documents/ready/<document_id>/text.txt
/remote/Documents/ready/<document_id>/metadata.json
/remote/Documents/reports/<document_id>.json
```

SQLite state is stored at `./data/document_lab.sqlite3` on the host. Interrupted `processing` tasks return to `queued` on application restart. Exact SHA-256 duplicates are marked `duplicate` and are not queued; equal filenames with different content are distinct.

## Ubuntu/Docker deployment

Run these exact commands from the repository checkout:

```bash
cd /path/to/document-lab
sudo mkdir -p /home/homelabuser/RemoteDrop/Documents/{incoming,processing,ready,failed,reports,originals,imports,Library}
sudo chown -R 10001:10001 /home/homelabuser/RemoteDrop
mkdir -p data logs
sudo chown -R 10001:10001 data logs
docker network inspect ai-network >/dev/null 2>&1 || docker network create ai-network
docker compose build --pull
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
5. Preview extracted text, metadata, and the quality report, then approve the task.
6. Upload the same bytes under another filename; confirm the task is clearly marked duplicate and is not processed.
7. Upload different bytes with the same filename; confirm a distinct task is queued.
8. Upload a PDF with a good text layer; confirm `extraction_method` is `pdf_text` and `ocr_used` is false.
9. Upload a scanned PDF; confirm `extraction_method` is `pdf_ocr` and selected OCR languages are recorded.
10. Upload hidden-text and scanned DJVU samples; confirm `djvu_hidden_text` and `djvu_ocr` respectively, with explicit page markers for OCR.
11. Upload EPUB and DOCX samples and review title/author extraction.
12. Stop the container while a task is processing, start it again, and confirm the task safely returns to the queue.
13. Retry a failed task. Delete a non-processing task and confirm its original remains intact.
14. Confirm no Qdrant, embedding, LLM, or external AI request is made.

## Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `REMOTE_ROOT` | `/remote` | RemoteDrop mount |
| `DATA_ROOT` | `/app/data` | SQLite data |
| `LOGS_ROOT` | `/app/logs` | Application logs |
| `WORKER_POLL_SECONDS` | `1` | Queue polling interval |
| `COMMAND_TIMEOUT_SECONDS` | `3600` | Extraction/OCR subprocess timeout |

## Known MVP limitations

- Work is handled by one in-process background thread, suitable for a single Uvicorn process only. Do not add multiple Uvicorn workers.
- OCR language `auto` lets Tesseract/OCRmyPDF use its configured default; it does not run all installed languages or perform language detection.
- Quality scoring is heuristic. Partially scanned documents receive warnings, but per-page selective OCR is not yet implemented.
- Encrypted or severely damaged files fail with a persisted error and can be retried after correction.
- EPUB reading order follows EbookLib document item order and may vary for unusual EPUB packages.
- Deleting a task removes generated output/report only; immutable uploaded originals and Library files are retained intentionally.
