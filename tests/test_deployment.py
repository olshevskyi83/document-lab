from pathlib import Path

from importlib.metadata import version

import pikepdf


ROOT = Path(__file__).parents[1]


def test_docker_defaults_to_homelab_uid_gid():
    dockerfile = (ROOT / "Dockerfile").read_text()
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "ARG APP_UID=1000" in dockerfile
    assert "ARG APP_GID=1000" in dockerfile
    assert 'user: "${APP_UID:-1000}:${APP_GID:-1000}"' in compose
    assert "chown -R documentlab:documentlab /app /remote" not in dockerfile
    assert "chown -R 10001:10001" not in (ROOT / "README.md").read_text()


def test_ocrmypdf_pikepdf_compatibility_is_pinned():
    requirements = (ROOT / "requirements.txt").read_text().splitlines()
    assert "ocrmypdf==16.10.4" in requirements
    assert "pikepdf==9.11.0" in requirements
    assert not any(line.startswith("pikepdf==10.") for line in requirements)
    assert version("pikepdf") == "9.11.0"
    assert hasattr(pikepdf.Pdf, "check")
