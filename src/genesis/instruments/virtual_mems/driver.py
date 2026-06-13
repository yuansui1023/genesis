from __future__ import annotations

import json
import time
from typing import Any

from genesis.core.instrument.base_instrument import BaseInstrument
from genesis.core.instrument.config_field import ConfigFieldDefinition
from genesis.core.instrument.registry import InstrumentRegistry
from genesis.core.transport.base_transport import BaseTransport
from genesis.core.transport.tcp_jsonl_transport import TcpJsonLinesTransport

INSTRUMENT_TYPE_KEY = "virtual_mems"


class MEMSControlError(RuntimeError):
    """Base error for VirtualMEMS remote-control failures."""


class MEMSControlDisabledError(MEMSControlError):
    """Raised when the external MEMS app has remote control disabled."""


class MEMSControlBusyError(MEMSControlError):
    """Raised when the external MEMS app rejects a command because it is busy."""


class MEMSControlClient:
    """
    JSON Lines client for the external ``mems_control_app`` TCP API.

    The client can either own a TCP transport from host/port or wrap the
    transport Genesis already opened for this instrument.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 12345,
        timeout: float = 5.0,
        move_timeout: float | None = None,
        transport: BaseTransport | None = None,
    ) -> None:
        self.host = str(host)
        self.port = int(port)
        self.timeout = float(timeout)
        self.move_timeout = move_timeout
        self._transport = transport or TcpJsonLinesTransport(
            f"{self.host}:{self.port}",
            settings={
                "connectTimeoutSeconds": self.timeout,
                "responseTimeoutSeconds": self.timeout,
            },
        )
        self._owns_transport = transport is None

    def connect(self) -> None:
        self._transport.open()

    def close(self) -> None:
        self._transport.close()

    def ping(self) -> bool:
        response = self._request({"cmd": "PING"})
        if response == {"alive": True}:
            return True
        if "alive" in response:
            return bool(response.get("alive"))
        raise MEMSControlError(f"Unexpected PING response: {response!r}")

    def get_position(self) -> float:
        response = self._request({"cmd": "GET_POSITION"})
        if "position" not in response:
            raise MEMSControlError(f"Unexpected GET_POSITION response: {response!r}")
        return float(response["position"])

    def get_status(self) -> dict[str, Any]:
        response = self._request({"cmd": "GET_STATUS"})
        required = {"position", "target", "moving", "remote_enabled", "task_status"}
        missing = sorted(required - set(response))
        if missing:
            raise MEMSControlError(
                f"Unexpected GET_STATUS response, missing {missing}: {response!r}"
            )
        return response

    def move(
        self, target_step: float, microstep: int = 16, speed: float = 500.0
    ) -> None:
        self._send(
            {
                "cmd": "MOVE",
                "target_step": self._format_target_step(target_step),
                "microstep": int(microstep),
                "speed": float(speed),
            }
        )

        deadline = (
            None
            if self.move_timeout is None or float(self.move_timeout) <= 0.0
            else time.monotonic() + float(self.move_timeout)
        )
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for VirtualMEMS MOVE to finish.")
            response = self._read_response()

            status = response.get("status")
            if status == "running":
                continue
            if status == "disabled":
                raise MEMSControlDisabledError(
                    "VirtualMEMS remote control is disabled."
                )
            if status == "busy":
                raise MEMSControlBusyError("VirtualMEMS is busy.")
            if status == "done":
                if response.get("success") is True:
                    return
                error = response.get("error", "MOVE failed.")
                raise MEMSControlError(f"VirtualMEMS MOVE failed: {error}")
            raise MEMSControlError(f"Unexpected MOVE response: {response!r}")

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._send(payload)
        return self._read_response()

    def _format_target_step(self, target_step: float) -> int | float:
        rounded = round(float(target_step), 3)
        if rounded.is_integer():
            return int(rounded)
        return rounded

    def _send(self, payload: dict[str, Any]) -> None:
        try:
            text = json.dumps(payload, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise MEMSControlError(f"Could not encode VirtualMEMS command: {exc}") from exc
        self._transport.write(text)

    def _read_response(self) -> dict[str, Any]:
        raw = self._transport.read().strip()
        try:
            response = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"VirtualMEMS response is not valid JSON: {raw!r}") from exc
        if not isinstance(response, dict):
            raise MEMSControlError(f"VirtualMEMS response is not an object: {response!r}")
        return response


class VirtualMEMSInstrument(BaseInstrument):
    """
    Virtual instrument wrapper for the external ``mems_control_app``.

    Commands are JSON Lines over TCP:
    - ``PING`` verifies the remote-control endpoint is alive.
    - ``GET_POSITION`` / ``GET_STATUS`` provide measurement values.
    - ``MOVE`` is atomic and includes target position, microstep, and speed.
    """

    displayName = "VirtualMEMS TCP Controller"

    @classmethod
    def getDefaultTransportKey(cls) -> str:
        return "tcp_jsonl"

    @classmethod
    def getSupportedTransportKeys(cls) -> list[str]:
        return ["tcp_jsonl"]

    @classmethod
    def getDefaultAddress(cls) -> str:
        return "127.0.0.1:12345"

    @classmethod
    def getDefaultTransportSettings(cls) -> dict[str, Any]:
        return {
            "connectTimeoutSeconds": 5.0,
            "responseTimeoutSeconds": 15.0,
        }

    @classmethod
    def getAvailableMeasurementSignals(cls) -> list[tuple[str, str]]:
        return [
            ("positionStep", "Position (step)"),
            ("targetStepReadback", "Target Readback (step)"),
            ("moving", "Moving (0/1)"),
            ("remoteEnabled", "Remote Enabled (0/1)"),
        ]

    @classmethod
    def getJobConfigFields(cls) -> list[ConfigFieldDefinition]:
        return [
            ConfigFieldDefinition(
                key="targetStep",
                label="Target Position (step)",
                fieldType="float",
                default=0.0,
                minValue=-2147483648,
                maxValue=2147483647,
                stepValue=0.001,
                sweepable=True,
                helpText=(
                    "Sweepable MEMS target position. Applying this field sends "
                    "one atomic MOVE command containing target_step rounded to "
                    "three decimal places, microstep, and speed."
                ),
            ),
            ConfigFieldDefinition(
                key="microstep",
                label="Microstep",
                fieldType="int",
                default=16,
                minValue=1,
                maxValue=256,
                stepValue=1,
                helpText="Microstep value included in each atomic MOVE command.",
            ),
            ConfigFieldDefinition(
                key="moveSpeed",
                label="Ramp Speed (steps/s)",
                fieldType="float",
                default=500.0,
                minValue=0.0,
                maxValue=1.0e9,
                stepValue=1.0,
                helpText="Speed value included in each atomic MOVE command.",
            ),
            ConfigFieldDefinition(
                key="moveTimeoutSeconds",
                label="Move Timeout (s)",
                fieldType="float",
                default=0.0,
                minValue=0.0,
                maxValue=24.0 * 3600.0,
                stepValue=1.0,
                helpText="Maximum total wait for MOVE completion. Zero disables the total timeout.",
            ),
        ]

    def __init__(
        self,
        name: str,
        transport: BaseTransport,
        metadata: dict[str, Any] | None = None,
        jobConfig: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(name=name, transport=transport, metadata=metadata)
        self.jobConfig = dict(self.getDefaultJobConfig())
        self.jobConfig.update(jobConfig or {})
        self._safeConfig = dict((metadata or {}).get("safeConfig", {}))
        self._client = MEMSControlClient(
            transport=transport,
            move_timeout=self._move_timeout_seconds(),
        )

    def initialize(self) -> None:
        self._ensure_remote_ready()

    def applySafeState(self) -> None:
        target = self._safeConfig or self.jobConfig
        self._move_to(
            target.get("targetStep", self.jobConfig.get("targetStep", 0)),
            target.get("microstep", self.jobConfig.get("microstep", 16)),
            target.get("moveSpeed", self.jobConfig.get("moveSpeed", 500.0)),
            target.get(
                "moveTimeoutSeconds",
                self.jobConfig.get("moveTimeoutSeconds", 0.0),
            ),
        )

    def readMeasurements(self, signalKeys: list[str]) -> dict[str, float]:
        requested = {str(k) for k in signalKeys}
        values: dict[str, float] = {}
        if "positionStep" in requested and len(requested) == 1:
            values["positionStep"] = float(self._client.get_position())
            return values

        status = self._client.get_status()
        if "positionStep" in requested:
            values["positionStep"] = float(status.get("position", 0.0))
        if "targetStepReadback" in requested:
            values["targetStepReadback"] = float(status.get("target", 0.0))
        if "moving" in requested:
            values["moving"] = 1.0 if bool(status.get("moving", False)) else 0.0
        if "remoteEnabled" in requested:
            values["remoteEnabled"] = (
                1.0 if bool(status.get("remote_enabled", False)) else 0.0
            )
        return values

    def applyConfigValue(self, key: str, value: float | int | str) -> None:
        self.jobConfig[key] = value
        if key == "targetStep":
            self._move_to(
                value,
                self.jobConfig.get("microstep", 16),
                self.jobConfig.get("moveSpeed", 500.0),
                self.jobConfig.get("moveTimeoutSeconds", 0.0),
            )
            return
        if key in {"microstep", "moveSpeed", "moveTimeoutSeconds"}:
            return

    def shouldUseRuntimeSlew(self, key: str) -> bool:
        if key == "targetStep":
            return False
        return super().shouldUseRuntimeSlew(key)

    def _ensure_remote_ready(self) -> None:
        if not self._client.ping():
            raise MEMSControlError("VirtualMEMS PING returned alive=false.")
        status = self._client.get_status()
        if not bool(status.get("remote_enabled", False)):
            raise MEMSControlDisabledError("VirtualMEMS remote control is disabled.")

    def _move_to(
        self,
        target_step: float | int | str,
        microstep: float | int | str,
        speed: float | int | str,
        move_timeout_seconds: float | int | str,
    ) -> None:
        self._client.move_timeout = self._normalize_timeout(move_timeout_seconds)
        self._client.move(
            target_step=float(target_step),
            microstep=int(float(microstep)),
            speed=float(speed),
        )
        self.jobConfig["targetStep"] = round(float(target_step), 3)
        self.jobConfig["microstep"] = int(float(microstep))
        self.jobConfig["moveSpeed"] = float(speed)
        self.jobConfig["moveTimeoutSeconds"] = float(move_timeout_seconds)

    def _move_timeout_seconds(self) -> float | None:
        return self._normalize_timeout(self.jobConfig.get("moveTimeoutSeconds", 0.0))

    @staticmethod
    def _normalize_timeout(value: Any) -> float | None:
        seconds = float(value)
        return None if seconds <= 0.0 else seconds


def _virtualMemsFactory(
    name: str,
    transport: BaseTransport,
    metadata: dict[str, Any] | None = None,
    jobConfig: dict[str, Any] | None = None,
) -> BaseInstrument:
    return VirtualMEMSInstrument(
        name=name,
        transport=transport,
        metadata=metadata,
        jobConfig=jobConfig,
    )


def registerInstruments(registry: InstrumentRegistry) -> None:
    registry.registerInstrument(
        key=INSTRUMENT_TYPE_KEY,
        instrumentType=VirtualMEMSInstrument,
        factory=_virtualMemsFactory,
    )
