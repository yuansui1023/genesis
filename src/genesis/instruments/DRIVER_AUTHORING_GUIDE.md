# Genesis Instrument Driver Authoring Guide

This document explains how to write a functional instrument driver in Genesis with no prior project context.

It is based on current working drivers:
- `src/genesis/instruments/b29xx/driver.py`
- `src/genesis/instruments/sr850/driver.py`
- `src/genesis/instruments/agilent34401a/driver.py` (example of a single-function
  DMM with dependent configuration replay and batched readings)
- `src/genesis/instruments/ami420/driver.py` (example of a controller-managed
  ramp that uses the async settle hook)
- `src/genesis/instruments/virtual_mems/driver.py` (example of a TCP JSON Lines
  virtual instrument with an atomic move command)

## 1) File and Module Layout

Create a folder under `src/genesis/instruments/`:

- `src/genesis/instruments/<instrument_key>/driver.py`
- `src/genesis/instruments/<instrument_key>/__init__.py`

Example:
- `src/genesis/instruments/my_device/driver.py`

Genesis discovers this automatically via `loadBuiltInInstruments()` in:
- `src/genesis/core/instrument/discovery.py`

Your module must export:
- `registerInstruments(registry)`

Also add built-in driver modules to `_BUILT_IN_DRIVER_MODULES` in `discovery.py`.
The explicit list supports environments where package enumeration is unavailable;
the normal package scan remains automatic.

## 2) Required APIs to Implement

Your class must extend `BaseInstrument` from:
- `src/genesis/core/instrument/base_instrument.py`

Implement/override:

1. `initialize(self) -> None`
   - Apply configured state without full reset commands unless explicitly needed.

2. `applySafeState(self) -> None`
   - Drive instrument to safe state from `metadata["safeConfig"]` (fallback to job config if needed).

3. `applyConfigValue(self, key: str, value: float | int | str) -> None`
   - Translate one logical config key into one or more transport commands.

4. `readMeasurements(self, signalKeys: list[str]) -> dict[str, float]`
   - Return only requested signals, parsed as floats.

Recommended class methods:

5. `getJobConfigFields(cls) -> list[ConfigFieldDefinition]`
   - Define UI + serialization config schema.

6. `getAvailableMeasurementSignals(cls) -> list[tuple[str, str]]`
   - Define user-selectable measured signals.

7. `getDefaultAddress(cls) -> str`
   - Return factory default resource when known, e.g. a VISA resource string or
     TCP endpoint such as `127.0.0.1:12345`.

8. `getSupportedTransportKeys(cls) -> list[str]`
   - Usually `["visa"]` for GPIB/SCPI instruments.
   - Use `["tcp_jsonl"]` for newline-delimited JSON TCP instruments.

Optional (recommended for flaky GPIB, TCP instruments, or long ramp devices):

9. `getDefaultTransportSettings(cls) -> dict[str, Any]`
   - Returned dict is merged with per-job `"transportSettings"` and passed into
     the selected transport.
   - VISA keys include `visaTimeoutMs`, `writeTermination`, `readTermination`.
   - TCP JSON Lines keys include `connectTimeoutSeconds` and
     `responseTimeoutSeconds`.

## 3) Naming Conventions

Follow these conventions to match current code:

- `INSTRUMENT_TYPE_KEY`: short lowercase key, e.g. `"b29xx"`, `"sr850"`.
- Driver class: `PascalCase`, e.g. `B29xxInstrument`.
- Folder name should match instrument key.
- Config keys:
  - descriptive + unit suffix when numeric (e.g. `referenceFrequencyHz`, `forceVoltageLevelV`).
  - enums use `...Index` when the instrument expects integer selectors.
- Measurement keys:
  - stable short keys used in job JSON and plotting (`x`, `y`, `r`, `theta`, `senseVoltageV`).

## 4) Config Schema: What to Include from the Manual

Use `ConfigFieldDefinition` (`src/genesis/core/instrument/config_field.py`) for each user-exposed parameter.

Field properties you should set carefully:
- `key`, `label`, `fieldType`, `default`
- `minValue`, `maxValue`, `stepValue` for numeric fields
- `choices` for enums (`ConfigChoice`)
- `helpText` with command mapping and key constraints
- `sweepable=True` only for safe/meaningful dynamic setpoints

### Parameter selection strategy

Include parameters that are:
1. Frequently needed for normal operation.
2. Stable to change at runtime.
3. Supported in remote mode with deterministic command mapping.

Avoid exposing:
1. Rare service/diagnostic/internal calibration controls.
2. Mode/commands that require complex preconditions unless implemented robustly.
3. Parameters known to vary by firmware unless guarded.

## 5) Defaults: How to Choose from Manual

Default values should come from the instrument manual, in this order:
1. Factory defaults from command reference.
2. If factory default is unsafe/unhelpful, choose conservative lab-safe defaults and document why.
3. Ensure defaults are valid under other defaults (e.g. mode-dependent values).

Always document source rationale in a nearby comment or `helpText`.

Examples already in code:
- B29xx default GPIB address `23`
- SR850 default GPIB address `8`

## 6) Determining `sweepable`

Mark a parameter `sweepable=True` only if all are true:
1. It represents a physical setpoint intended to vary point-to-point.
2. Runtime changes are supported and safe for repeated command updates.
3. Its command path is deterministic in `applyConfigValue`.
4. It is meaningful as an axis in job sweeps/plots.

Typical sweepable examples:
- SMU forced level (V or A)
- Lock-in reference frequency / amplitude / phase

Typical non-sweepable examples:
- Static wiring/config modes
- Auto-range toggles
- Deep filter/config indices unless explicitly desired

## 7) Safety and Runtime Behavior Requirements

Genesis now provides centralized setpoint safety in:
- `src/genesis/core/runtime/setpoint_safety.py`

Important implications:
- Drivers should expose accurate numeric bounds in `ConfigFieldDefinition` (`minValue`, `maxValue`).
- Runtime safety uses those bounds for clamping and slew-limited stepping.
- Keep `applyConfigValue` deterministic and idempotent; do not hide extra asynchronous behavior.

Slew behavior is orchestrated outside drivers (device-agnostic), so most new
drivers do not need custom slew code.

If a driver command must remain atomic, override:

```python
def shouldUseRuntimeSlew(self, key: str) -> bool:
    return False if key == "targetStep" else super().shouldUseRuntimeSlew(key)
```

Genesis will still clamp numeric bounds, but will call `applyConfigValue()` once
for that key instead of splitting the transition into multiple writes. Use this
only when the device/API requires a single complete command, such as
VirtualMEMS `MOVE` with `target_step`, `microstep`, and `speed` in one JSON
object, or VirtualMEMS `SET_VZ` with `vz_v` in one JSON object.

### 7.1) Asynchronous Settle Hook (`waitForSetpoint`)

Some instruments (e.g. AMI Model 420 magnet controllers) handle ramping
internally on the device. After commanding a new setpoint, the program must
wait for the device to physically reach the target without freezing the UI.

`BaseInstrument` provides:

```python
def waitForSetpoint(
    self,
    key: str,
    target_value: float,
    should_stop: Callable[[], bool] | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> bool:
    ...
```

Contract:
- Default implementation is a no-op (`return True`); most drivers should not override.
- Override only when hardware needs measurable settling and the runtime
  should not advance to sampling until the setpoint is physically reached.
- The implementation MUST cooperatively check `should_stop()` between polls
  so abort latency stays small.
- The implementation SHOULD invoke `on_progress(measured_value)` periodically
  so live plots and progress indicators stay responsive.
- Return `False` for any condition where the setpoint cannot be considered
  reached (timeout, quench, hardware fault). Return `True` once within
  tolerance.

The acquisition worker calls this automatically after each non-time sweep
step's setpoint is applied, both for 1D and 2D sweeps.

When a job enables **critical ramping**, the acquisition worker also reads
configured measurement keys during `on_progress` (and during software slew
`on_applied` callbacks). Keep progress callbacks reasonably frequent so a
threshold crossing can be observed before the next sweep point, and keep
`readMeasurements` cheap enough to call at that cadence.

### 7.2) Initialization settling (`finalizeInitialization`)

After the main window applies safe/config setpoints during **Initialize**, it
calls `finalizeInitialization(should_stop)` on each instrument. Default:
`return True` (immediate).

Override when the physical plant still needs time to match the last written
setpoint after initialization (e.g. AMI Model 420: internal magnet ramp).
Typically delegate to the same polling logic as `waitForSetpoint` for the
commanded field/current. Runs on the background init thread; keep it
cooperative with `should_stop` for abort.

### 7.3) Critical ramping (runtime, not driver-local)

Genesis can stop a run when a selected instrument measurement crosses a
threshold (`src/genesis/core/runtime/critical_condition.py`). This is
device-agnostic orchestration in the acquisition worker, not a per-driver
feature.

Driver contract implications:

- Measurement keys used in a critical condition must be readable on every
  sample via `readMeasurements`. Genesis auto-adds those keys to the job's
  measurement set at runtime even if the user did not check them in the
  instrument form.
- `readMeasurements` should return only successfully parsed finite floats.
  Missing keys, parse failures, and NaN/Inf values do not satisfy a
  condition.
- If a job enables **hold settings on trigger**, Genesis will **not** call
  `applySafeState` after that stop. Drivers must not assume that a run
  always ends in safe state. Stop, Abort, and Initialize remain the
  explicit ways to leave a held output.
- Abort still calls `applySafeState` immediately, including from a held
  state.

Critical ramping is software-layer only. Document any hardware protection
the instrument itself provides; do not treat this mode as an interlock.

## 8) `applyConfigValue` Implementation Pattern

Use a clear key-dispatch structure:

1. Persist incoming value into `self.jobConfig[key]`.
2. Derive dependent mode/channel state from `self.jobConfig`.
3. For each supported key:
   - validate/normalize if needed,
   - emit command(s),
   - return.
4. Ignore unknown keys safely (or raise if appropriate).

For interdependent settings, validate the proposed complete configuration before
persisting values or sending commands. Reject invalid numeric values (including
NaN/infinity) and unsupported discrete choices without mutating the previous
configuration. Numeric enums should publish both their choices and bounds.

Some instruments preset additional settings when selecting a function. For
example, the 34401A's `CONF:<function>` presets trigger source/count/delay, sample
count, autozero, AC filter, impedance, and math state. Apply the function first,
then replay the supported dependent settings in a fixed order. Validate and
retain inactive function-specific settings without sending invalid commands;
apply them when that function is explicitly selected. Separate manual-range and
manual-delay values from their automatic-mode switches so replaying a dormant
manual value cannot silently disable an enabled automatic mode. The 34401A has
no output setpoint, so every configuration field is `sweepable=False`.

Keep formatting helpers (`_fmtFloat`) for consistent command strings.

### SCPI command spelling

Use **short-form SCPI keywords** (abbreviated headers) in `write` / `query`
strings unless a specific instrument requires a longer form. This matches other
Genesis drivers (e.g. Keysight-style `:SOUR:...:CURR`) and typical programmer
manuals. Document the exact strings used in the class docstring or field
`helpText` where users map UI to hardware.

## 9) Measurement Read Pattern

Recommended:
- Map signal keys to query commands or SNAP indices.
- Read only requested keys.
- Parse robustly (strip, split commas if needed).
- Return `dict[str, float]` with only successful values.

### 9.1) Single-function instruments and sample batches

When hardware can measure only one function at a time, document whether reads
return only the active function or explicitly switch/restore functions. Prefer
active-function-only reads: silently ignore other requested keys and avoid I/O
when none match. Do not use a query that implicitly reconfigures the instrument.

Define how multiple samples become a scalar signal. The 34401A driver returns a
batch mean, requires the expected sample count, accepts a trailing comma, and
omits the key if any sample is malformed, nonfinite, or an overload sentinel.
Transport errors still propagate. Its `READ?` path streams IMM/EXT acquisitions;
BUS uses `INIT`, `*TRG`, `FETC?` and enforces the 512-reading memory limit even
though `SAMP:COUN` supports 50,000 readings. Do not insert `*OPC?` between `INIT`
and `*TRG`: the 34401A does not accept ordinary commands while waiting for BUS.

Measurement-only does not imply absence of excitation. The 34401A's resistance,
continuity and diode modes source current. Its safe-state target overlays
`metadata["safeConfig"]` on `jobConfig`, then substitutes autoranged DCV for all
four excitation modes before applying commands. Document electrical limits and
abort limitations: this changes function, not wiring, and a pending acquisition
after a timeout needs a device clear before new configuration can take effect.
The current `BaseTransport` API has no device-clear hook. Closing and reopening
with `visaClearOnOpen` enabled attempts a clear, but the transport ignores clear
failures. Confirm recovery or use a supported controller device clear before
reinitializing.

## 10) Registration Boilerplate

Each `driver.py` should include:

1. Factory function:
- builds instrument with `name`, `transport`, `metadata`, `jobConfig`.

2. Registration function:
- `registerInstruments(registry)` calling `registry.registerInstrument(...)`.

Minimal template:

```python
INSTRUMENT_TYPE_KEY = "mydevice"

class MyDeviceInstrument(BaseInstrument):
    ...

def _myDeviceFactory(name, transport, metadata=None, jobConfig=None):
    return MyDeviceInstrument(
        name=name,
        transport=transport,
        metadata=metadata,
        jobConfig=jobConfig,
    )

def registerInstruments(registry: InstrumentRegistry) -> None:
    registry.registerInstrument(
        key=INSTRUMENT_TYPE_KEY,
        instrumentType=MyDeviceInstrument,
        factory=_myDeviceFactory,
    )
```

## 11) Practical Manual-to-Driver Workflow

1. Read manual sections:
   - command summary,
   - parameter ranges,
   - defaults,
   - remote command syntax examples.
2. Draft `getJobConfigFields` with explicit bounds/defaults/choices.
3. Implement `applyConfigValue` key-by-key with exact command mapping.
4. Implement `readMeasurements` for selected signals.
5. Implement `initialize` and `applySafeState`.
6. Register driver and run app.
7. Validate with real hardware or dummy transport first, then hardware.

## 12) VISA defaults, ``transportSettings``, and ``VI_ERROR_BERR``

Genesis configures PyVISA *message-based* resources on ``open()``: newline write
and read terminators (IEEE 488.2 SCPI convention), timeouts, optional
``clear()``, and ``query()`` uses ``resource.query`` (not separate untimed
write+read). Missing terminators often show up from NI backends as
``VI_ERROR_BERR: Bus error occurred during transfer``.

**Per-job overrides:** add optional ``transportSettings`` to an instrument dict
(job JSON); values override driver defaults merged in
``MainWindow._buildRuntimeFromJob``.

Example (longer GPIB timeouts, terminators unchanged):

```json
"instruments": [{
  "id": "mag",
  "type": "ami420",
  "transport": "visa",
  "address": "GPIB0::22::INSTR",
  "transportSettings": { "visaTimeoutMs": 60000, "visaClearOnOpen": false }
}]
```

Recognized keys (see ``VisaTransport`` docstring for defaults): ``visaTimeoutMs``,
``writeTermination``, ``readTermination``, ``visaQueryDelay``,
``visaClearOnOpen``, ``visaSendEndOnWrite``, ``visaLogIoStdout`` (echo I/O frames
with UTC timestamps on stdout via ``repr()``, ``GENESIS_VISA_IO_LOG`` env toggles).

Hardware checklist if errors persist:

- GPIB cable, termination, duplicate controller access, instrument address.
- Try ``visaClearOnOpen``: ``false`` in ``transportSettings`` if the programmer rejects IEEE 488 clear.
- Try ``writeTermination`` of ``"\r"`` or ``"\r\n"`` only if the manual specifies it.

The AMI Model 420 driver exposes ``getDefaultTransportSettings()`` for
reasonable magnet-ramp timeouts; merge with manual hardware checks first.

For integrating meters, document the timeout budget in the driver and expose
per-job overrides for combinations beyond it. The 34401A defaults to 120,000 ms:
at 50 Hz, 100 PLC takes 2 s per integration; an engineering allowance of three
integrations plus 1 s settling per sample gives 70 s for ten samples, plus 50 s
margin. This does not cover every valid sample count or trigger delay. Account
for delays before each sample, autozero, filter settling, and external-trigger
waits when choosing a larger `visaTimeoutMs`.

## 13) TCP JSON Lines virtual instruments

Genesis provides `tcp_jsonl` for line-delimited JSON request/response devices.
The resource string accepts `host:port`, `tcp://host:port`, or an empty value for
`127.0.0.1:12345`.

Use this transport when a driver talks to a local or network control app rather
than a VISA/GPIB device. The driver should:

- Keep protocol-specific JSON validation and high-level methods in a reusable
  client class.
- Send exactly one command object for atomic operations.
- Treat busy, disabled, timeout, malformed JSON, and unexpected responses as
  clear exceptions.
- Expose only meaningful high-level config fields in the GUI; for VirtualMEMS,
  `targetStep` and `vzV` are sweepable while `microstep`, `moveSpeed`, and
  `moveTimeoutSeconds` parameterize each `MOVE`, `vzRampRateVPerS` parameterizes
  each `SET_VZ` ramp (zero omits `ramp_rate_v_per_s` for an immediate jump),
  and the timeout also bounds remote-task waits.

VirtualMEMS uses peer-level remote tasks:

```json
{"cmd":"MOVE","target_step":100000,"microstep":16,"speed":500.0}
{"cmd":"SET_VZ","vz_v":20.0,"ramp_rate_v_per_s":5.0}
```

The client waits through heartbeat responses such as `{"status":"running"}` and
returns only after `{"status":"done","success":true}`. Only one remote task may
run at a time; do not send the next control command until the previous `done`.

## 14) Validation Checklist Before Merge

- Driver discovered by registry automatically.
- Config fields render correctly in Job Builder.
- Defaults load and initialize without errors.
- Each config key maps to expected command.
- Measurement reads parse correctly.
- Sweepable fields change correctly during sweeps.
- Stop/abort and safe-state behavior works as expected.
- No reset-heavy side effects in normal initialize path.

## 15) Documentation Sync Requirement

When critical functionality changes are implemented in Genesis (especially runtime safety, sweep orchestration, plotting behavior, or driver contract expectations), documentation must be updated in the same work:

- `README.md` for user-facing workflow/behavior changes.
- `src/genesis/instruments/DRIVER_AUTHORING_GUIDE.md` for driver-facing requirements and patterns.

For agent-to-agent context handoff summaries, explicitly include:
- whether these documents were updated,
- what sections changed,
- and any remaining doc follow-up items.
