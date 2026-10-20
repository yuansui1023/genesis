# Genesis

> Disclaimer: This project is vibe-coded.

Genesis is a PySide6 desktop application for configuring and running automated instrument sweeps, visualizing live data, and exporting run artifacts for analysis.

It currently includes built-in drivers for:
- Keysight/Agilent B29xx SMU family
- Agilent/HP 34401A 6½ digit digital multimeter (VISA/SCPI)
- HP 4145B semiconductor parameter analyzer (one VISA session; SMU1–SMU4, VS1/VS2, VM1/VM2)
- YOKOGAWA 7651 programmable DC source (GP-IB, manual voltage/current source)
- Stanford Research SR850 lock-in amplifier
- American Magnetics Inc. Model 420 superconducting magnet power supply programmer
- VirtualMEMS TCP controller for the external `mems_control_app`

## Core Functionalities

- Build jobs from a GUI (instrument instances, configs, sweep definitions, plot definitions).
- Name instrument variables on a dedicated Variable Names tab; use those nicknames in plot selectors, expressions, axes, and legends.
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
3. Open Variable Names (between Instruments and Sweep), choose an instrument, and edit nicknames for its settings and measurement signals. Defaults include `V_set`, `I_meas`, and `SMU1_V_set`. Search the table or use Reset to Defaults for the selected instrument.
4. Define sweep settings (1D or 2D).
5. Optionally enable Critical Ramping on the Sweep tab: choose measurement conditions (`>`, `<`, `|x| >`, `|x| <`), whether any/all must match, consecutive-sample debounce, and whether to hold instrument settings on trigger.
6. Define plots (XY and/or heatmap). Variable choices and expression insertion use nicknames, such as `b29xx-1:I_meas`; axes and legends use the same names. An explicitly entered curve name still takes precedence.
7. Click Initialize to apply safe initialization state.
8. Click Start Sweep to begin acquisition.
9. Use Stop for controlled return-to-safe flow, or Abort for immediate stop.
10. Save job JSON to `~/Documents/Genesis/jobs/` and run outputs to `~/Documents/Genesis/runs/`.

### Notes for New Users

- Variable nicknames are saved per instrument in the job and can contain letters
  (including Chinese), numbers, and underscores; they cannot start with a number
  and must be unique within that instrument. Cloned instruments copy their names
  and can then be edited independently. Older jobs receive defaults automatically.
  Changing a nickname keeps plot selections and formulas attached to the same
  variable. Job expressions, instrument commands, and raw export column keys keep
  their stable original references.
  For the 7651, `V_set`/`I_set` are commanded settings and
  `V_set_readback`/`I_set_readback` are instrument-reported settings, not measured
  terminal voltage or load current. `Output_on` remains a separate signal.
- If you abort a run, re-initialize before starting another sweep.
- Critical ramping is a software-layer stop. It is not a hardware interlock or a substitute for instrument protection settings. Checks run after each acquired sample, and also during software slew steps and `waitForSetpoint` progress callbacks when the mode is enabled, so trigger latency follows that sampling cadence.
- If critical ramping triggers with **Hold instrument settings** enabled, outputs stay at their triggered values. Start Sweep is disabled until you leave that state: Stop ramps swept outputs back to safe state, Abort applies safe state immediately, and Initialize rebuilds from the job's safe/config values.
- When hold is disabled, a critical trigger uses the same controlled return-to-safe path as Stop, not the natural sweep-complete path.
- Trigger details are written next to the run data file as `*_critical_trigger.json`.
- Heatmap pop-up is intended for larger inspection while embedded view stays compact.
- For simulation/testing without hardware, use dummy transport paths in the runtime stack.
- For the HP 4145B, use one instrument instance at factory address
  `GPIB0::17::INSTR`. Genesis sweeps in software; the driver does not program
  VAR1/VAR2, System Mode pages, disk, HP-GL, or COM. SMU voltage/current and
  VS voltage are the sweepable setpoints. Outputs start off, ranges start at
  AUTO, and integration starts at MEDIUM (`IT2`); those defaults are
  driver-chosen, not factory presets. A disabled SMU accepts new levels
  without sourcing. Enabling it, or changing an enabled source, sends a full
  `DV`/`DI` with range, level, and compliance. The outer ±100 V / ±100 mA
  bounds are not a rectangle: 20 V / 40 V / 100 V ranges allow 100 mA / 50 mA
  / 20 mA, and current-source voltage compliance follows the subranges at or
  below 20 mA, above 20 mA through 50 mA, and above 50 mA through 100 mA.
  VS 0 V is not a disconnect. VM1/VM2 are `TV5`/`TV6`. Readings are sequential spot measurements, not a
  simultaneous sample. Only status `N` is returned; compliance, saturation,
  oscillation, and interval-too-short statuses are errors and are omitted
  from that sample. The acquisition loop logs the error and continues, so
  this is not a hardware interlock. User Mode will not measure with the
  fixture lid open; the driver does not bypass that. Abort turns every SMU
  off and programs both VS outputs to 0 V unless `safeConfig` explicitly
  holds a valid bias. Stop and Initialize do not call `applySafeState`; with
  an empty `safeConfig` they reapply the job config, which can leave an SMU
  enabled. Put output-off and VS 0 V in `safeConfig` when Stop must remove
  bias. The default VISA timeout is 10 s and `visaClearOnOpen` is false,
  because a device clear leaves User Mode. A read in progress can delay Abort
  until that timeout. See the field help and the
  [HP 4145B Operation Manual](https://web.mit.edu/scholvin/www/mq753/Documents/probe.HP4145B_Manual.pdf)
  (part 04145-90310, April 1989).
- For the YOKOGAWA 7651, set the VISA resource yourself. The GP-IB address is
  chosen on the front panel and kept across power-off; the manual does not
  publish a factory address. A resource looks like `GPIB0::1::INSTR`. The
  instrument is a source, not a meter. `sourceVoltageSettingV` and
  `sourceCurrentSettingA` are the values the instrument reports for its
  setting, not measured terminal voltage or load current. Read
  `outputEnabled` as well: output OFF does not mean the setting is zero, and
  a setting of zero does not mean the output is off. Only the active
  function's setting is returned. Sweep the voltage or current setting.
  Fixed ranges use `S` and `E` together; auto-range uses `SA` and can enter
  the 10 mV or 100 mV range, where the current limiter does not operate.
  Voltage mode sends `LA` in whole milliamps from 5 to 120. Current mode
  sends `LV` in whole volts from 1 to 30. The other limit is stored until
  the function changes. Defaults are 0 V, the 1 V range, output off, a 1 V
  voltage limit, and a 5 mA current limit. The 1 V range matches the manual's
  power-on function; the 5 mA and 1 V limits are driver choices, tighter than
  the manual's 120 mA and 30 V. Output off is not an open circuit. Abort
  sends `RU0` and `O0E` unless `safeConfig` explicitly holds a valid bias.
  Stop and Initialize do not call `applySafeState`; with an empty
  `safeConfig` they reapply the job, which can leave the output on. The
  default timeout is 10 s and `visaClearOnOpen` is false, because a device
  clear restores the power-on panel and blocks communication for a few
  seconds. Critical ramping on these setting readbacks is not a measurement
  of the DUT. See
  [IM 7651-01E](https://cdn.tmi.yokogawa.com/IM7651-01E.is.pdf).
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
