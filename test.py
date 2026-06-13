from __future__ import annotations

import traceback


def main() -> None:
    try:
        import genesis

        print("genesis file:", getattr(genesis, "__file__", None))
        print("genesis path:", list(getattr(genesis, "__path__", [])))
    except Exception:
        print("failed to import genesis:")
        traceback.print_exc()
        return

    try:
        import genesis.instruments.virtual_mems.driver as virtual_mems_driver

        print("virtual_mems driver:", virtual_mems_driver.__file__)
    except Exception:
        print("failed to import virtual_mems driver:")
        traceback.print_exc()

    try:
        from genesis.core.instrument.discovery import loadBuiltInInstruments
        from genesis.core.instrument.registry import InstrumentRegistry

        registry = InstrumentRegistry()
        loadBuiltInInstruments(registry)
        print("instruments:", registry.listInstruments())
    except Exception:
        print("failed to load built-in instruments:")
        traceback.print_exc()


if __name__ == "__main__":
    main()
