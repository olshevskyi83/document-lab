from __future__ import annotations

import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable


class CommandError(RuntimeError):
    pass


class CommandTimeoutError(CommandError):
    pass


class CommandCancelledError(CommandError):
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
    arguments: list[str],
    *,
    timeout: int,
    cwd: Path | None = None,
    text: bool = True,
    cancel_check: Callable[[], bool] | None = None,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    if not arguments:
        raise ValueError("Command is required")
    arguments = [require_binary(arguments[0]), *arguments[1:]]
    process = subprocess.Popen(
        arguments,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        shell=False,
        start_new_session=True,
    )
    started = time.monotonic()
    while True:
        try:
            stdout, stderr = process.communicate(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            if cancel_check and cancel_check():
                _terminate_process_group(process)
                process.communicate()
                raise CommandCancelledError(f"Command cancelled: {Path(arguments[0]).name}")
            if time.monotonic() - started >= timeout:
                _terminate_process_group(process)
                process.communicate()
                raise CommandTimeoutError(f"Command timed out: {Path(arguments[0]).name}")
    if process.returncode:
        normalized_stderr = (
            stderr.decode(errors="replace") if isinstance(stderr, bytes) else stderr
        )
        raise CommandExecutionError(
            Path(arguments[0]).name, process.returncode, (normalized_stderr or "").strip()
        )
    return subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)


def _terminate_process_group(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        # OCRmyPDF may spawn Ghostscript/Tesseract children; terminate the whole session.
        os_killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=3)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            try:
                os_killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()


def os_killpg(process_group: int, signal_number: int) -> None:
    # Kept as a small seam for regression tests.
    import os

    os.killpg(process_group, signal_number)
