from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class CommandError(RuntimeError):
    pass


class CommandTimeoutError(CommandError):
    pass


class CommandExecutionError(CommandError):
    def __init__(self, command: str, returncode: int, stderr: str):
        self.command = command
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"{command} failed with exit code {returncode}: {stderr}")


def require_binary(name: str) -> str:
    resolved = shutil.which(name)
    if not resolved:
        raise CommandError(f"Required binary is not available: {name}")
    return resolved


def run_command(
    arguments: list[str], *, timeout: int, cwd: Path | None = None, text: bool = True
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    if not arguments:
        raise ValueError("Command is required")
    arguments = [require_binary(arguments[0]), *arguments[1:]]
    try:
        return subprocess.run(
            arguments,
            cwd=cwd,
            capture_output=True,
            text=text,
            timeout=timeout,
            check=True,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CommandTimeoutError(f"Command timed out: {Path(arguments[0]).name}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
        raise CommandExecutionError(
            Path(arguments[0]).name, exc.returncode, (stderr or "").strip()
        ) from exc
