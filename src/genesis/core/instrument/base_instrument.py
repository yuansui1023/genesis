from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

from genesis.core.transport.base_transport import BaseTransport

from .config_field import ConfigFieldDefinition


class BaseInstrument(ABC):
    """
    Abstract base for all instruments controlled by Genesis.

    Instruments are responsible for translating high-level operations into
    low-level transport commands.
    """

    def __init__(
        self,
        name: str,
        transport: BaseTransport,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.transport = transport
        self.metadata = metadata or {}

    @abstractmethod
    def initialize(self) -> None:
        """
        Perform any one-time initialization of the instrument.
        """

    @abstractmethod
    def applySafeState(self) -> None:
        """
        Drive the instrument into a safe state.
        """

    def readMeasurements(self, signalKeys: list[str]) -> dict[str, float]:
        """
        Read the specified measurement signal keys from the instrument.

        For v1 this can be implemented by simulated instruments and/or by
        real drivers over VISA. Genesis will use this for live views/plots.
        """

        raise NotImplementedError

    def applyConfigValue(self, key: str, value: float | int | str) -> None:
        """
        Apply one runtime configuration value (used by sweep execution).
        """

        raise NotImplementedError

    def finalizeInitialization(
        self, should_stop: Callable[[], bool] | None = None
    ) -> bool:
        """
        Optional hook after safe/config values have been applied during job
        initialization. Instruments whose physical output lags the last write
        (e.g. magnet controllers) should override and block here until
        settling completes or should_stop returns True.

        Runs on the background initialization thread, not the GUI thread.
        """

        return True

    def waitForSetpoint(
        self,
        key: str,
        target_value: float,
        should_stop: Callable[[], bool] | None = None,
        on_progress: Callable[[float], None] | None = None,
    ) -> bool:
        """
        Optional hook called after applyConfigValue to block until the
        instrument has physically reached the commanded setpoint.

        Default implementation is a no-op returning True (immediate). Drivers
        that need to wait for hardware settling (e.g. magnet controllers that
        handle ramping internally) should override this and:
          - poll the relevant measurement until within tolerance,
          - cooperatively check should_stop between polls so abort works,
          - call on_progress(measured_value) periodically so the runtime can
            update active values, live plots, and progress indicators.

        Returns True on successful settle (or no-op), False if interrupted by
        should_stop or by a fatal device condition.
        """

        return True

    def shouldUseRuntimeSlew(self, key: str) -> bool:
        """
        Return whether Genesis may split numeric setpoint changes for this key
        into multiple driver writes for software-side slew limiting.

        Most instruments accept repeated setpoint writes, so the default is
        True. Drivers whose command is intrinsically atomic can override this
        for specific keys.
        """

        return True

    # ---- Job-builder / configuration metadata (no behavior lives here) ----

    @classmethod
    def getJobConfigFields(cls) -> list[ConfigFieldDefinition]:
        """
        Describe the instrument-specific parameters that the user can set
        while building a job.
        """

        return []

    @classmethod
    def getDefaultJobConfig(cls) -> dict[str, Any]:
        """
        Default parameter values for job creation.

        Note: These defaults are user-editable in the job builder and are
        persisted into the job JSON.
        """

        fields = cls.getJobConfigFields()
        return {field.key: field.default for field in fields}

    @classmethod
    def getDefaultTransportKey(cls) -> str:
        return "visa"

    @classmethod
    def getSupportedTransportKeys(cls) -> list[str]:
        return [cls.getDefaultTransportKey()]

    @classmethod
    def getDefaultAddress(cls) -> str:
        return ""

    @classmethod
    def getAvailableMeasurementSignals(cls) -> list[tuple[str, str]]:
        """
        Return a list of (signal_key, human_label) that this instrument can
        provide as measurements, for use by the job builder UI.

        Example for a lock-in: [("x", "X"), ("y", "Y"), ("r", "R"), ("theta", "θ")].
        """

        return []

    @classmethod
    def getAvailablePlotSignals(cls) -> list[tuple[str, str]]:
        """
        Return a list of (signal_key, human_label) that make sense to plot
        in real time for this instrument.
        """

        return cls.getAvailableMeasurementSignals()
