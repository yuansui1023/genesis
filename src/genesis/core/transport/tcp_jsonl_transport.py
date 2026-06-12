from __future__ import annotations

import socket
from typing import Any

from .base_transport import BaseTransport, TransportSettingsLike


class TcpJsonLinesTransport(BaseTransport):
    """
    TCP transport for newline-delimited JSON request/response protocols.

    ``resourceName`` accepts ``"host:port"``, ``"tcp://host:port"``, or an
    empty string to use the default ``127.0.0.1:12345``.
    """

    def __init__(
        self, resourceName: str, settings: TransportSettingsLike | None = None
    ) -> None:
        super().__init__(resourceName=resourceName, settings=settings)
        self._socket: socket.socket | None = None
        self._buffer = b""

    def _setting(self, key: str, default: Any) -> Any:
        if self.settings is None:
            return default
        return self.settings.get(key, default)

    def open(self) -> None:
        if self._socket is not None:
            return
        host, port = self._parse_resource_name(self.resourceName)
        timeout = float(self._setting("connectTimeoutSeconds", 5.0))
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as exc:
            raise ConnectionError(
                f"Could not connect to TCP JSONL endpoint {host}:{port}: {exc}"
            ) from exc
        sock.settimeout(float(self._setting("responseTimeoutSeconds", 15.0)))
        self._socket = sock
        self._buffer = b""

    def close(self) -> None:
        sock = self._socket
        self._socket = None
        self._buffer = b""
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def write(self, command: str) -> None:
        if self._socket is None:
            raise RuntimeError("TCP JSONL transport is not open.")
        payload = str(command)
        if not payload.endswith("\n"):
            payload += "\n"
        try:
            self._socket.sendall(payload.encode("utf-8"))
        except OSError as exc:
            raise ConnectionError(f"TCP JSONL write failed: {exc}") from exc

    def read(self) -> str:
        if self._socket is None:
            raise RuntimeError("TCP JSONL transport is not open.")

        while b"\n" not in self._buffer:
            try:
                chunk = self._socket.recv(4096)
            except socket.timeout as exc:
                raise TimeoutError("Timed out waiting for TCP JSONL response.") from exc
            except OSError as exc:
                raise ConnectionError(f"TCP JSONL read failed: {exc}") from exc
            if not chunk:
                raise ConnectionError("TCP JSONL connection closed by peer.")
            self._buffer += chunk

        line, self._buffer = self._buffer.split(b"\n", 1)
        try:
            return line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("TCP JSONL response is not valid UTF-8.") from exc

    @staticmethod
    def _parse_resource_name(resource_name: str) -> tuple[str, int]:
        raw = str(resource_name or "").strip()
        if not raw:
            return "127.0.0.1", 12345
        if raw.startswith("tcp://"):
            raw = raw[len("tcp://") :]
        if ":" not in raw:
            return raw, 12345
        host, port_text = raw.rsplit(":", 1)
        host = host.strip() or "127.0.0.1"
        try:
            port = int(port_text.strip())
        except ValueError as exc:
            raise ValueError(f"Invalid TCP JSONL port in resource: {resource_name!r}") from exc
        return host, port
