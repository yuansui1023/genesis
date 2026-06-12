from __future__ import annotations

import json
import socket
import threading
import unittest
from typing import Any, Callable, Iterable

from genesis.instruments.virtual_mems.driver import (
    MEMSControlBusyError,
    MEMSControlClient,
    MEMSControlDisabledError,
    MEMSControlError,
    VirtualMEMSInstrument,
)


class _FakeJsonLinesServer:
    def __init__(
        self, handler: Callable[[dict[str, Any]], Iterable[dict[str, Any] | str]]
    ) -> None:
        self.handler = handler
        self.requests: list[dict[str, Any]] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self.host, self.port = self._sock.getsockname()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self) -> "_FakeJsonLinesServer":
        self._sock.listen(1)
        self._thread.start()
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=1.0)

    def _serve(self) -> None:
        try:
            conn, _addr = self._sock.accept()
        except OSError:
            return
        with conn:
            file = conn.makefile("rwb")
            while True:
                line = file.readline()
                if not line:
                    return
                request = json.loads(line.decode("utf-8"))
                self.requests.append(request)
                for response in self.handler(request):
                    if isinstance(response, str):
                        payload = response
                    else:
                        payload = json.dumps(response, separators=(",", ":"))
                    file.write(payload.encode("utf-8") + b"\n")
                    file.flush()


class VirtualMEMSClientTests(unittest.TestCase):
    def test_ping_and_get_position(self) -> None:
        def handler(request: dict[str, Any]) -> Iterable[dict[str, Any]]:
            if request["cmd"] == "PING":
                return [{"alive": True}]
            if request["cmd"] == "GET_POSITION":
                return [{"position": 123}]
            raise AssertionError(request)

        with _FakeJsonLinesServer(handler) as server:
            client = MEMSControlClient(
                host=server.host, port=server.port, timeout=1.0
            )
            client.connect()
            self.assertTrue(client.ping())
            self.assertEqual(client.get_position(), 123.0)
            client.close()

    def test_move_waits_through_running_heartbeat(self) -> None:
        def handler(request: dict[str, Any]) -> Iterable[dict[str, Any]]:
            self.assertEqual(request["cmd"], "MOVE")
            self.assertEqual(request["target_step"], 100000)
            self.assertEqual(request["microstep"], 16)
            self.assertEqual(request["speed"], 500.0)
            return [{"status": "running"}, {"status": "done", "success": True}]

        with _FakeJsonLinesServer(handler) as server:
            client = MEMSControlClient(
                host=server.host,
                port=server.port,
                timeout=1.0,
                move_timeout=5.0,
            )
            client.connect()
            client.move(target_step=100000, microstep=16, speed=500.0)
            client.close()
            self.assertEqual(len(server.requests), 1)

    def test_move_disabled_raises_clear_error(self) -> None:
        def handler(_request: dict[str, Any]) -> Iterable[dict[str, Any]]:
            return [{"status": "disabled"}]

        with _FakeJsonLinesServer(handler) as server:
            client = MEMSControlClient(
                host=server.host, port=server.port, timeout=1.0
            )
            client.connect()
            with self.assertRaises(MEMSControlDisabledError):
                client.move(target_step=1)
            client.close()

    def test_move_busy_raises_clear_error(self) -> None:
        def handler(_request: dict[str, Any]) -> Iterable[dict[str, Any]]:
            return [{"status": "busy"}]

        with _FakeJsonLinesServer(handler) as server:
            client = MEMSControlClient(
                host=server.host, port=server.port, timeout=1.0
            )
            client.connect()
            with self.assertRaises(MEMSControlBusyError):
                client.move(target_step=1)
            client.close()

    def test_move_failed_final_response_raises_error_text(self) -> None:
        def handler(_request: dict[str, Any]) -> Iterable[dict[str, Any]]:
            return [{"status": "done", "success": False, "error": "test error"}]

        with _FakeJsonLinesServer(handler) as server:
            client = MEMSControlClient(
                host=server.host, port=server.port, timeout=1.0
            )
            client.connect()
            with self.assertRaisesRegex(MEMSControlError, "test error"):
                client.move(target_step=1)
            client.close()

    def test_driver_marks_target_step_as_atomic(self) -> None:
        fields = {field.key: field for field in VirtualMEMSInstrument.getJobConfigFields()}
        self.assertTrue(fields["targetStep"].sweepable)
        transport = _ClosedTransport()
        instrument = VirtualMEMSInstrument(name="mems", transport=transport)
        self.assertFalse(instrument.shouldUseRuntimeSlew("targetStep"))
        self.assertTrue(instrument.shouldUseRuntimeSlew("moveSpeed"))


class _ClosedTransport:
    resourceName = "closed"

    def open(self) -> None:
        return None

    def close(self) -> None:
        return None

    def write(self, command: str) -> None:
        raise RuntimeError(command)

    def read(self) -> str:
        raise RuntimeError("closed")


if __name__ == "__main__":
    unittest.main()
