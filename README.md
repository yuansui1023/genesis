# Genesis

> Disclaimer: This project is vibe-coded.

Genesis is a PySide6 desktop application for configuring and running automated instrument sweeps, visualizing live data, and exporting run artifacts for analysis.

It currently includes built-in drivers for:
- Keysight/Agilent B29xx SMU family
- Agilent/HP 34401A 6½ digit digital multimeter (VISA/SCPI)
- Stanford Research SR850 lock-in amplifier
- American Magnetics Inc. Model 420 superconducting magnet power supply programmer
- VirtualMEMS TCP controller for the external `mems_control_app`

## Core Functionalities

- Build jobs from a GUI (instrument instances, configs, sweep definitions, plot definitions).
- Run 1D and 2D sweeps with live plotting.
- Live XY plots and 2D heatmaps (embedded and pop-up large view).
- Configurable sweep slew limiting for safer setpoint transitions.
- Optional critical ramping: stop a run when a selected measurement crosses a threshold, with a choice to hold current instrument settings or return to safe state.
- Safe-state logic across initialize, stop, natural sweep completion, and abort.
- Abort control for immediate stop behavior.
- Raw data export (CSV/NPY) and job save/load (JSON).
- User folder bootstrapping on startup:
  - `~/Documents/Genesis/`
  - `~/Documents/Genesis/jobs/`
  - `~/Documents/Genesis/runs/`

## Beginner Quick Start

### 1) Requirements

- Python 3.10+
- VISA backend available on your machine if using real hardware
  - The app uses `pyvisa`/`pyvisa-py`
  - On Windows, install a VISA runtime (for example NI-VISA) if your instrument connection requires it
  - If GPIB raises `VI_ERROR_BERR`, check cabling and address first; Genesis enables SCPI newline terminators and timeouts on `VisaTransport` by default—see the driver guide section on VISA / `transportSettings` for overrides.
  - For bus-level debugging, set `GENESIS_VISA_IO_LOG=1` or put `"visaLogIoStdout": true` under `"transportSettings"` for an instrument so `VisaTransport` prints UTC-timestamped frames to stdout with `repr()` (line endings visible as escapes).
- VirtualMEMS requires the external `mems_control_app` TCP JSON Lines server to be running and remote control enabled. The default endpoint is `127.0.0.1:12345`.

### 2) Install

From repository root.

macOS/Linux:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Windows (PowerShell):

```powershell
py -3 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Windows (Command Prompt):

```bat
py -3 -m venv venv
venv\Scripts\activate.bat
python -m pip install -r requirements.txt
```

### 3) Launch

macOS/Linux:

```bash
python -m genesis.app.main
```

Windows (PowerShell or Command Prompt):

```powershell
python -m genesis.app.main
```

## How To Use (Beginner Workflow)

1. Start the app and create/load a job.
2. Add instrument instances and configure fields from the driver forms.
3. Define sweep settings (1D or 2D).
4. Optionally enable Critical Ramping on the Sweep tab: choose measurement conditions (`>`, `<`, `|x| >`, `|x| <`), whether any/all must match, consecutive-sample debounce, and whether to hold instrument settings on trigger.
5. Define plots (XY and/or heatmap).
6. Click Initialize to apply safe initialization state.
7. Click Start Sweep to begin acquisition.
8. Use Stop for controlled return-to-safe flow, or Abort for immediate stop.
9. Save job JSON to `~/Documents/Genesis/jobs/` and run outputs to `~/Documents/Genesis/runs/`.

### Notes for New Users

- If you abort a run, re-initialize before starting another sweep.
- Critical ramping is a software-layer stop. It is not a hardware interlock or a substitute for instrument protection settings. Checks run after each acquired sample, and also during software slew steps and `waitForSetpoint` progress callbacks when the mode is enabled, so trigger latency follows that sampling cadence.
- If critical ramping triggers with **Hold instrument settings** enabled, outputs stay at their triggered values. Start Sweep is disabled until you leave that state: Stop ramps swept outputs back to safe state, Abort applies safe state immediately, and Initialize rebuilds from the job's safe/config values.
- When hold is disabled, a critical trigger uses the same controlled return-to-safe path as Stop, not the natural sweep-complete path.
- Trigger details are written next to the run data file as `*_critical_trigger.json`.
- Heatmap pop-up is intended for larger inspection while embedded view stays compact.
- For simulation/testing without hardware, use dummy transport paths in the runtime stack.
- For the Agilent/HP 34401A, the factory address is `GPIB0::22::INSTR`.
  Select one measurement function and request its matching signal; other signal
  keys are ignored without switching functions. A batch returns its arithmetic
  mean only when every sample is valid. All settings are non-sweepable.
  Safe state applies `safeConfig` over the job configuration, replacing resistance,
  continuity, and diode modes with autoranged DC voltage to remove their test
  current. This does not disconnect the leads or the current-input shunt.
  The default VISA timeout is 120 seconds; increase `transportSettings.visaTimeoutMs`
  for large batches, long trigger delays, or external-trigger waits. BUS triggering
  is limited to 512 buffered samples; IMM/EXT support up to 50,000 streamed samples.
  After a read timeout, a pending measurement requires a VISA device clear before
  safe-state configuration can take effect. Reopening with `visaClearOnOpen: true`
  attempts this clear, but the transport ignores clear failures; confirm recovery
  or use a supported controller device clear before reinitializing. See the field help and
  [34401A User's Guide](https://www.keysight.com.cn/cn/zh/assets/9018-01063/user-manuals/9018-01063.pdf)
  for configuration limits and command details.
- For VirtualMEMS, choose the `VirtualMEMS TCP Controller` instrument, keep the default `tcp_jsonl` transport, and set `Address / Resource` to `host:port` if the MEMS control app is not on `127.0.0.1:12345`. Sweep `targetStep` for MEMS angle (atomic `MOVE`) or `vzV` for bias voltage (atomic `SET_VZ`); `microstep`, `moveSpeed`, and `moveTimeoutSeconds` parameterize each `MOVE`, while `vzRampRateVPerS` (0 = immediate jump, >0 = MEMS V/s ramp) is included in each `SET_VZ`, and `moveTimeoutSeconds` also bounds remote-task waits. `MOVE` and `SET_VZ` are peer-level remote tasks—only one runs at a time, and Genesis waits for each to finish before sending the next.

## Project Layout (High Level)

- `src/genesis/app/` - App bootstrap and main window orchestration.
- `src/genesis/core/` - Runtime, job model, safety, transport abstractions.
- `src/genesis/ui/` - Qt widgets, job builder editors, plotting widgets.
- `src/genesis/instruments/` - Driver implementations and authoring guide.
- `src/genesis/export/` - Data export management.
- `tests/` - Integration and unit tests for safety/slew behavior.

## Contributing Guide

### 1) Setup Dev Environment

macOS/Linux:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -e ".[dev]"
pre-commit install
```

Windows (PowerShell):

```powershell
py -3 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install -e ".[dev]"
pre-commit install
```

### 2) Make Changes

- Prefer small, focused PRs.
- Keep hardware-specific logic in drivers.
- Keep cross-device behavior (safety, slew, orchestration) in shared runtime/core layers.
- Add or update tests for behavioral changes.

### 3) Validate Before Commit

Run:

```bash
pre-commit run --all-files
./venv/bin/python -m unittest discover -s tests -p "test_*.py"
```

Windows equivalent:

```powershell
pre-commit run --all-files
python -m unittest discover -s tests -p "test_*.py"
```

### 4) Submit

- Use clear commit messages focused on behavioral intent.
- Include a short test plan in your PR description.
- Call out any hardware-dependent behavior explicitly.

## Style Guide

### Python Style

- Formatting: `black` (line length 88).
- Target version: Python 3.10+.
- Prefer explicit type hints for new/changed code.
- Keep functions small and focused on one responsibility.
- Prefer descriptive names over comments; add comments only where logic is non-obvious.

### Architecture Style

- UI widgets should not embed driver-specific SCPI details.
- Runtime safety logic should be centralized and reusable.
- Driver contracts should remain consistent with `base_instrument` and config field definitions.
- Avoid bypassing shared safety/slew paths in new execution flows.

### Instrument / SCPI Style

- Prefer **short-form (abbreviated) SCPI keywords** in transport strings, consistent with manuals and with other Genesis drivers (e.g. `:SOUR...:CURR`, `CONF:FIELD:PROG`). Avoid long mixed-case spellings unless the device requires them.

### Testing Style

- Add regression tests when fixing bugs.
- Cover both nominal and safety/abort edge cases.
- Keep tests deterministic and independent of real hardware whenever possible.

## Driver Development

For adding new instruments, start with:

- `src/genesis/instruments/DRIVER_AUTHORING_GUIDE.md`

This guide describes required APIs, naming conventions, config field strategy, and sweepability decisions.

## Documentation Maintenance Policy

For critical functionality changes, keep documentation synchronized in the same change set:

- Update `README.md` with user-facing behavior/workflow changes.
- Update `src/genesis/instruments/DRIVER_AUTHORING_GUIDE.md` when changes affect driver expectations, runtime safety, or integration patterns.
- In any context handoff summary, explicitly state:
  - whether these docs were updated,
  - which sections changed,
  - and any remaining documentation follow-ups.
