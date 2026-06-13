from __future__ import annotations

import importlib
import pkgutil

from genesis.core.instrument.registry import InstrumentRegistry


def loadBuiltInInstruments(registry: InstrumentRegistry) -> None:
    """
    Discover and register built-in instruments under `genesis.instruments.*`.

    Contributor workflow:
    - Add a new instrument folder under `src/genesis/instruments/<name>/`
    - Provide a `driver.py` (or `Driver.py`) that exports `registerInstruments(registry)`
    - Genesis will import it automatically.
    """

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
            except ModuleNotFoundError:
                continue

        if driverModule is None:
            continue

        registerFn = getattr(driverModule, "registerInstruments", None)
        if registerFn is None:
            continue

        registerFn(registry)
