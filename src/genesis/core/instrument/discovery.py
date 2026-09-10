from __future__ import annotations

import importlib
import pkgutil

from genesis.core.instrument.registry import InstrumentRegistry

_BUILT_IN_DRIVER_MODULES = [
    "genesis.instruments.agilent34401a.driver",
    "genesis.instruments.ami420.driver",
    "genesis.instruments.b29xx.driver",
    "genesis.instruments.sr850.driver",
    "genesis.instruments.virtual_mems.driver",
]


def loadBuiltInInstruments(registry: InstrumentRegistry) -> None:
    """
    Discover and register built-in instruments under `genesis.instruments.*`.

    Contributor workflow:
    - Add a new instrument folder under `src/genesis/instruments/<name>/`
    - Provide a `driver.py` (or `Driver.py`) that exports `registerInstruments(registry)`
    - Genesis will import it automatically.
    """

    for moduleName in _BUILT_IN_DRIVER_MODULES:
        _registerDriverModule(registry, moduleName)

    import genesis.instruments as instrumentsPkg

    for moduleInfo in pkgutil.iter_modules(instrumentsPkg.__path__):
        instrumentPackageName = moduleInfo.name

        # Try lowercase first (recommended) and then fallback to original plan-casing.
        driverModuleNames = [
            f"genesis.instruments.{instrumentPackageName}.driver",
            f"genesis.instruments.{instrumentPackageName}.Driver",
        ]

        driverModule = None
        for moduleName in driverModuleNames:
            try:
                driverModule = importlib.import_module(moduleName)
                break
            except ModuleNotFoundError as exc:
                # Only treat the driver module itself as optional. Missing
                # imports inside a driver should surface instead of making the
                # instrument silently disappear from the GUI.
                if exc.name != moduleName:
                    raise
                continue

        if driverModule is None:
            continue

        _registerLoadedDriverModule(registry, driverModule)


def _registerDriverModule(registry: InstrumentRegistry, moduleName: str) -> None:
    driverModule = importlib.import_module(moduleName)
    _registerLoadedDriverModule(registry, driverModule)


def _registerLoadedDriverModule(registry: InstrumentRegistry, driverModule) -> None:
    registerFn = getattr(driverModule, "registerInstruments", None)
    if registerFn is None:
        return
    try:
        registerFn(registry)
    except ValueError as exc:
        if "Instrument key already registered" not in str(exc):
            raise
