from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.main as web
from app.services import dependencies


def test_dependency_versions_reports_packages_and_binaries(monkeypatch):
    versions = {"ocrmypdf": "16.10.4", "pikepdf": "9.11.0"}
    monkeypatch.setattr(dependencies, "package_version", lambda name: versions[name])

    def fake_run(arguments, **_kwargs):
        output = "10.00.0\n" if arguments[0] == "gs" else "tesseract 5.3.0\n libraries"
        return SimpleNamespace(stdout=output)

    monkeypatch.setattr(dependencies, "run_command", fake_run)
    assert dependencies.dependency_versions() == {
        "ocrmypdf": "16.10.4",
        "pikepdf": "9.11.0",
        "ghostscript": "10.00.0",
        "tesseract": "5.3.0",
    }


def test_health_exposes_dependency_versions(monkeypatch):
    expected = {
        "ocrmypdf": "16.10.4",
        "pikepdf": "9.11.0",
        "ghostscript": "10.00.0",
        "tesseract": "5.3.0",
    }
    monkeypatch.setattr(web, "runtime_dependencies", expected.copy())
    response = TestClient(web.app).get("/health")
    assert response.status_code == 200
    assert response.json()["dependencies"] == expected
