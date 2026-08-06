# KingFisher Presto

Python driver for the **KingFisher Presto** magnetic particle processor. Uses USB HID and the instrument’s XML command/response/event protocol (see [KingFisher Presto Interface Specification](presto_docs/KingFisherPrestoInterfaceSpecification.pdf)).

---

## Project structure

| Module | Role |
|--------|------|
| **presto_connection.py** | Connection layer: error/warning codes (spec 4.8, 4.9), HID transport (`KingFisherHID` with `send_feature_report` for Abort), 64-byte framing, XML message loop, Res/Evt demux, event queue. Defines `PrestoConnection`, `PrestoConnectionError`. |
| **presto_backend.py** | Backend: `KingFisherPrestoBackend` (MachineBackend). Builds `<Cmd>` XML, sends commands via `PrestoConnection`, tracks turntable state. Exposes connect/disconnect, get_status, list_protocols, get_protocol_duration, get_protocol_time_left, start/stop protocol, rotate, load_plate, acknowledge, error_acknowledge, abort, events. |
| **presto.py** | Frontend: `KingFisherPresto` (Machine). Wraps the backend; adds `next_event()` (returns `name`, `evt`, `ack`) and `get_run_state()`. All high-level usage goes through this class. |
| **presto_tests.py** | Critical tests for framing, command XML, error handling, turntable state, and frontend behavior. |
| **presto_docs/** | Interface spec PDF, integration guide, example BDZ files, and the [step-by-step demo notebook](presto_docs/kingfisher_two_mix_steps.ipynb). |

**Public API** (from `pylabrobot.particle_processing.kingfisher`):

- `KingFisherPresto` – high-level machine (use this for normal operation)
- `KingFisherPrestoBackend` – backend (passed into `KingFisherPresto`)
- `TurntableLocation` – constants `"processing"` and `"loading"`

---

## Communication

- **Transport:** USB HID. VID `0x0AB6`, PID `0x02C9` (per Interface Specification). Optional `serial_number` when multiple units are present.
- **Framing:** 64-byte Output reports: byte 0 = payload length (1–63), bytes 1–63 = payload. Commands terminated with newline (ASCII 10). Messages are XML: `<Cmd>`, `<Res>`, `<Evt>`.
- **Flow:** One command at a time. Send `<Cmd>`, wait for matching `<Res>`. `<Evt>` messages are queued and do not block the command/response pair. Events are consumed via `get_event()` or the async generator `events()`.
- **Abort:** Two-phase: Feature report (2 bytes, control endpoint) then Abort character in payload (spec 3.2.3, 5.1).

Error and warning codes (Res/Evt) are defined in the spec (sections 4.8, 4.9) and mapped in `presto_connection` for `PrestoConnectionError` and `get_status()["error_code_description"]`.

---

## Commands (instrument protocol)

| Command | Purpose |
|--------|---------|
| **Connect** | Establish session; optional `setTime` (YYYY-MM-DD hh:mm:ss). Response includes Instrument, Version, Serial. |
| **GetStatus** | Instrument state: Idle, Busy, or In error; optional Error code/text. |
| **GetProtocolDuration** | Protocol structure: tips and step names/durations (no BDZ download). §5.7. |
| **ListProtocols** | Protocol names in memory and memory used %. |
| **GetProtocolTimeLeft** | Time left (and optional time to next pause) for running protocol. |
| **StartProtocol** | Start full protocol or single step. Attributes: `protocol`, optional `tip`, optional `step`. Protocol must already be in instrument memory. |
| **Stop** | Stop current protocol/step execution. |
| **Acknowledge** | Required after user events: LoadPlate, RemovePlate, ChangePlate, Pause. |
| **ErrorAcknowledge** | Clear instrument error state after Error event. |
| **Rotate** | Move turntable: `nest` (position 1 or 2), `position` (1 = processing, 2 = loading). Completion signaled by Evt Ready or Error. |
| **Disconnect** | End session (instrument may not reply). |

Responses (`<Res>`) carry `ok="true"` or `ok="false"`; when false, an `<Error>` element with `code` and optional text is present. The driver raises `PrestoConnectionError` with a formatted message (and optional `code`) when appropriate.

---

## Usage

1. **Create backend and frontend**
   ```python
   from pylabrobot.particle_processing.kingfisher import KingFisherPresto, KingFisherPrestoBackend

   backend = KingFisherPrestoBackend()  # optional: serial_number="..." for multiple units
   presto = KingFisherPresto(backend=backend)
   ```

2. **Connect**
   ```python
   await presto.setup(initialize_turntable=True)  # True = set known turntable state so load_plate() works (note: may move the turntable)
   ```
   After setup you can use `presto.instrument`, `presto.version`, `presto.serial`, and `await presto.get_turntable_state()`.

3. **List protocols and inspect a protocol’s steps**
   ```python
   names, mem = await presto.list_protocols()
   info = await presto.get_protocol_duration("Instrument Test Protocol")
   # info["protocol"], info["total_duration"], info["tips"] (each tip has "name" and "steps": [{"name", "duration"}])
   ```

4. **Start a protocol or a single step**
   ```python
   await presto.start_protocol("Instrument Test Protocol", tip="Tip1", step="Pick-Up")  # single step
   # or: await presto.start_protocol("Instrument Test Protocol")  # full protocol
   ```

5. **Drive the run with events**
   - Call `name, evt, ack = await presto.next_event()`.
   - If `name` is `Ready`, `Aborted`, or `Error`, the run is done (`ack` is `None` for these).
   - If `name` is `LoadPlate`, `RemovePlate`, `ChangePlate`, or `Pause`, you must **interact with the instrument** (e.g. load or remove the plate, ensure plates are in place) before calling `await ack()`. Only then call `next_event()` again. Do not ack in a tight loop without performing the required action.
   - On `Error`, you can call `await presto.error_acknowledge()` to clear instrument error state.
   - **Single-step runs:** When you start a single step (e.g. `start_protocol(..., tip=..., step=...)`), there is typically nothing to acknowledge. `next_event()` shows progress (StepStarted, ProtocolTimeLeft, etc.) and eventually returns Ready (or Aborted/Error) when the step completes or fails—just loop until you get one of those.
   - **Full protocols:** May emit LoadPlate, RemovePlate, ChangePlate, or Pause. When you get one of these, perform the required action at the instrument (load/remove plate, etc.), then call `await ack()`, then keep calling `next_event()` until Ready/Aborted/Error.

6. **Disconnect**
   ```python
   await presto.stop()
   ```

---

## Step-by-step execution (single step)

Run the following as separate cells (e.g. in Jupyter with top-level `await`). Each step’s output informs the next: you use the tip and step names from `get_protocol_duration` when calling `start_protocol`. For a single step there is typically nothing to acknowledge; `next_event()` shows progress and returns Ready (or Aborted/Error) when the step completes.

**Cell 1 — Imports and connect**
```python
from pylabrobot.particle_processing.kingfisher import KingFisherPresto, KingFisherPrestoBackend

backend = KingFisherPrestoBackend()
presto = KingFisherPresto(backend=backend)

await presto.setup(initialize_turntable=True)  # may move the turntable
```

**Cell 2 — List protocols and get protocol structure**
```python
names, mem = await presto.list_protocols()
print("Protocols on instrument:", names)

PROTOCOL_NAME = "Instrument Test Protocol"  # or pick from names
info = await presto.get_protocol_duration(PROTOCOL_NAME)
print(f"Protocol: {info['protocol']}, total: {info.get('total_duration', 'N/A')}")
for tip in info["tips"]:
    steps = tip["steps"]
    print(f"  Tip: {tip['name']} — steps: {[s['name'] for s in steps]}")
```
*Example output:* `Tip: Tip1 — steps: ['Pick-Up', 'Mix1', 'Leave', 'Unload']` — use one of these tip/step names in the next cell.

**Cell 3 — Start a single step**
```python
# Use tip and step names from the output above
await presto.start_protocol(PROTOCOL_NAME, tip="Tip1", step="Pick-Up")
```

**Cell 4 — Wait for step to finish (single step: no ack needed)**
```python
while True:
    name, evt, ack = await presto.next_event()
    if name in ("Ready", "Aborted", "Error"):
        print(f"Run finished: {name}")
        break
```

**Cell 5 — Disconnect**
```python
await presto.stop()
```

---

## Full protocol: plate interaction steps

When running the **whole protocol** (e.g. `await presto.start_protocol(protocol_name)` with no tip/step), the instrument may emit **LoadPlate**, **RemovePlate**, **ChangePlate**, or **Pause**. Use `next_event()` to get the next such step; when you receive one, perform the required instrument or operator actions, then call `await ack()` to continue. Do not put this in a tight loop—other operations may happen between `next_event()` and `ack()`.

**Cell — Get next event**
```python
name, evt, ack = await presto.next_event()
print(name)  # e.g. LoadPlate, RemovePlate, ChangePlate, Pause, or Ready/Aborted/Error
```

**Cell — If that was a plate-interaction event, do the action then acknowledge**
```python
if ack is not None:
    # Perform required actions at the instrument (load/remove plate, etc.)
    await ack()
```

Repeat: run “Get next event” again, then if `ack` is not None run “do action then acknowledge”, until `name` is **Ready**, **Aborted**, or **Error**. See [presto_docs/kingfisher_two_mix_steps.ipynb](presto_docs/kingfisher_two_mix_steps.ipynb) for a full notebook.

---

## Turntable and load_plate

The turntable has two positions (slots) 1 and 2. Each can be at **processing** (under the magnetic head) or **loading** (load/unload station). State is inferred only after a successful `rotate()` or after `setup(initialize_turntable=True)`. **Note:** `initialize_turntable=True` may move the turntable to establish known state.

- `rotate(position=1, location=TurntableLocation.PROCESSING)` moves slot 1 to processing.
- `get_turntable_state()` returns `{1: "processing"|"loading"|None, 2: ...}`.
- `load_plate()` rotates so whatever is at loading moves to processing; it requires known turntable state (call `rotate()` first or use `initialize_turntable=True`; the latter may move the turntable on connect).

---

## Errors

- **PrestoConnectionError** – Raised when the instrument returns `Res@ok="false"` or on communication failure (e.g. timeout). Has `.code` and `.res_name` when available.
- **ValueError** – e.g. from `load_plate()` when turntable state is unknown, or from `rotate()` with invalid position/location.

Use `get_status()` for `ok`, `status`, `error_code`, `error_text`, and `error_code_description` (spec 4.8 descriptions when known).
