"""Temporary localhost.run tunnel. No personal SSH keys or raw SSH logs exposed."""

import json
import queue
import re
import shutil

# vetted SSH executable with a fixed argv
import subprocess  # nosec B404
import threading
import time
from contextlib import suppress

from .config import ROOT


class TunnelError(RuntimeError):
    pass


def accepted_url(line: str) -> str | None:
    # Service includes a long ANSI QR code after the address. Bound the whole
    # event separately, then scan only the short registration prefix.
    if len(line) > 131072:
        return None
    try:
        event = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(event, dict) or event.get("type") != "v1.tcpip_forward.register.accepted":
        return None
    data = event.get("data")
    if not isinstance(data, str) or len(data) > 65536:
        return None
    match = re.search(
        r"https://[a-zA-Z0-9-]+\.(?:lhr\.life|lhr\.rocks|localhost\.run)(?![a-zA-Z0-9_.-])",
        data[:4096],
    )
    return match.group(0) if match else None


class LocalhostTunnel:
    def __init__(self, port: int):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Некорректный порт туннеля.")
        self.port = port
        self.process = None
        self.url = None

    def start(self, timeout: float = 45) -> str:
        binary = shutil.which("ssh")
        if not binary:
            raise TunnelError(
                "OpenSSH не найден. Используйте официальный клиент или HTTPS_MODE=external."
            )
        self.url = None
        data = ROOT / "data"
        data.mkdir(exist_ok=True)
        command = [
            binary,
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "IdentityFile=none",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"UserKnownHostsFile={data / 'localhost-run-known-hosts'}",
            "-o",
            "ConnectTimeout=12",
            "-o",
            "ServerAliveInterval=20",
            "-o",
            "ServerAliveCountMax=3",
            "-o",
            "ExitOnForwardFailure=yes",
            "-R",
            f"80:127.0.0.1:{self.port}",
            "nokey@localhost.run",
            "--",
            "--output",
            "json",
        ]
        # Absolute executable, fixed argv, no shell. No user-provided SSH command.
        process = self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )  # nosec B603
        lines = queue.Queue(maxsize=100)

        def read():
            for line in process.stdout:
                value = accepted_url(line)
                if value:
                    self.url = value
                with suppress(queue.Full):
                    lines.put_nowait(line)

        threading.Thread(target=read, daemon=True).start()
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise TunnelError(
                        "localhost.run завершил соединение. Проверьте сеть/VPN и SSH."
                    )
                if self.url:
                    return self.url
                try:
                    line = lines.get(timeout=min(0.3, max(0.001, deadline - time.monotonic())))
                except queue.Empty:
                    continue
                value = accepted_url(line)
                if value:
                    self.url = value
                    return value
            raise TunnelError("localhost.run не выдал HTTPS-адрес. Проверьте подключение.")
        except (TunnelError, OSError):
            self.close()
            raise

    def close(self):
        process = self.process
        if process:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if process.stdout:
                process.stdout.close()
