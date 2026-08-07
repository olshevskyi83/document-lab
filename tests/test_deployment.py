from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_docker_defaults_to_homelab_uid_gid():
    dockerfile = (ROOT / "Dockerfile").read_text()
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "ARG APP_UID=1000" in dockerfile
    assert "ARG APP_GID=1000" in dockerfile
    assert 'user: "${APP_UID:-1000}:${APP_GID:-1000}"' in compose
    assert "chown -R documentlab:documentlab /app /remote" not in dockerfile
    assert "chown -R 10001:10001" not in (ROOT / "README.md").read_text()
