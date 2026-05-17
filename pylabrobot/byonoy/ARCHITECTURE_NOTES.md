# Byonoy package — architecture notes for future refactors

These notes capture the v1b1-capability review results from the
`byonoy-luminescence` branch (12 commits, HEAD `d28c0aebe`) so the
context is preserved for whoever next reorganises this module. They
are advisory — the package works as-is and ships in v1b1.

## Pre-existing structural divergence from canonical v1b1

The pre-existing `ByonoyBase` (inherited from `upstream/v1b1`) collapses
the `Driver` and `CapabilityBackend` layers into one class:

```
ByonoyBase(Driver, metaclass=ABCMeta)        # acts as both Driver + base for Backends
  └─ ByonoyLuminescence96Backend(ByonoyBase, LuminescenceBackend)
  └─ ByonoyAbsorbance96Backend(ByonoyBase, AbsorbanceBackend)
```

`ByonoyLuminescence96Backend` is therefore *both* a `Driver` and a
`LuminescenceBackend`. Compared to canonical v1b1:

- **P-06 (four-layer architecture)**: not separated — the `Driver` and
  `CapabilityBackend` are fused.
- **P-05 (backend stores `_driver` reference)**: not applicable — the
  backend *is* the driver.
- **P-08 (`<Vendor><Device>Driver` naming)**: `ByonoyBase` does not
  follow the convention. v1b1 precedent: `BioShakeDriver`,
  `NimbusDriver`, `XArm6Driver`, `STARDriver`, `TecanInfiniteDriver`.
- **P-25 (lifecycle hook scope)**: capability-specific init lives in
  `setup` instead of `_on_setup` (visible in
  `ByonoyAbsorbance96Backend.setup`, which calls
  `initialize_measurements` and `request_available_absorbance_wavelengths`
  inside the driver-level `setup`). Pre-existing in upstream/v1b1.

When a future PR refactors:

1. Introduce `class ByonoyDriver(Driver)` carrying the HID transport,
   heartbeat thread, `send_command`, the device-info methods, the
   abort flag, and the LED operations.
2. Make `ByonoyLuminescence96Backend(LuminescenceBackend)` a plain
   `CapabilityBackend` that takes a `driver: ByonoyDriver` in
   `__init__` and stores it as `self._driver`.
3. Move capability-specific work (the abs96 wavelength discovery,
   `initialize_measurements`) from `setup` into `_on_setup`.
4. The Device class stays at `ByonoyLuminescence96(Resource, Device)`
   and constructs the driver + backend separately, then wires
   `_capabilities = [self.luminescence]`. v1b1 precedent for the
   driver-shared-across-multiple-backends shape:
   `pylabrobot/tecan/infinite/infinite.py:31-75` — `TecanInfinite200Pro`
   wires `Absorbance`, `Fluorescence`, `Luminescence`, `LoadingTray`
   backends onto a single `TecanInfiniteDriver`.

## Findings introduced by the `byonoy-luminescence` branch

### F1 — LED control could be a P-16 helper subsystem (soft)

`set_led` (public) and `_set_led_effect` (private helper) live as flat
methods on the `Driver`. They form a coherent subsystem (touch reports
0x0350 / 0x0351, share manual-mode coordination — `set_led` already
chains an effect-set + color-write). v1b1 precedent: `STARCover`,
`STARWashStation`, `NimbusDoor` group related operations into a plain
helper class attached as a Driver attribute, with `_on_setup` /
`_on_stop` hooks.

Suggested shape:

```python
class ByonoyLEDBar:
  """Plain helper class (not a CapabilityBackend), following the
  STARCover pattern. Drives the 20-pixel front bar."""
  def __init__(self, driver: ByonoyDriver) -> None:
    self._driver = driver
  async def _on_setup(self) -> None: pass
  async def _on_stop(self) -> None: pass
  async def set(self, colors: List[Tuple[int, int, int]],
                effect: LedEffect = LedEffect.SOLID, ...) -> None: ...
```

User call site changes from `reader.driver.set_led(...)` to
`reader.driver.led_bar.set(...)`.

### F2 — Device-info queries could be a P-16 helper subsystem (soft)

Eight related methods on the `Driver` (`get_status`, `get_environment`,
`get_versions`, `get_api_version`, `get_supported_reports`,
`read_data_field`, `get_device_info`, `describe_error_code`) plus a
class-attribute extension hook (`_ERROR_NAMES`). The override is
currently per-backend-subclass (`ByonoyAbsorbance96Backend._ERROR_NAMES
= ABS96_ERROR_NAMES`); a helper class would localise the override
surface alongside the methods that consume it.

Suggested shape:

```python
class ByonoyDiagnostics:
  """Plain helper class (not a CapabilityBackend), following the
  STARCover pattern. Reads device metadata and decodes firmware
  errors per the device's known table."""
  _ERROR_NAMES: Dict[int, str] = _GENERIC_ERROR_NAMES  # override per device

  def __init__(self, driver: ByonoyDriver) -> None:
    self._driver = driver
  async def _on_setup(self) -> None: pass
  async def _on_stop(self) -> None: pass
  async def get_status(self) -> ByonoyStatus: ...
  async def get_environment(self) -> ByonoyEnvironment: ...
  # ... etc.
  def describe_error_code(self, code: int) -> str: ...
```

Per-device subclasses (`Abs96Diagnostics(ByonoyDiagnostics)`) override
`_ERROR_NAMES`. The Driver constructs the right subclass per its
device type.

### F3 — `LuminescenceParams` shape is correct (informational, positive)

The new `mode` / `integration_time` / `selected_wells` fields on a
typed dataclass inheriting `BackendParams` match v1b1 idiom (P-22).
The integration-mode preset table (`LUM96_PRESET_S`) is co-located.
The `integration_time is not None → CUSTOM` resolution preserves the
legacy call shape. No change needed.

### F4 — `_abort_requested` flag should propagate to abs96 (soft)

Setting and consuming the abort flag works because the backend *is*
the driver (collapse). With a Driver/Backend split, the flag belongs
on the Driver so all backends see it. Until then: copy the
`if self._abort_requested: ... raise asyncio.CancelledError(...)`
guard from `luminescence_96.py` read loop into
`absorbance_96.py:_run_abs_measurement`'s read loop. Same shape; one
block; makes `cancel()` consistent across both backends.

### F5 — `ByonoyBase` → `ByonoyDriver` rename (soft, out of scope)

The `Base` suffix is non-idiomatic. Every v1b1 device driver is named
`<Vendor><Device>Driver`. When the architectural split (above) happens,
rename to `ByonoyDriver`. The per-device pid is already passed via
`__init__`, so no signature change.

## Why the divergences are tolerable today

- The package works on real hardware (validated against an L96 with
  serial `BYOMAL00029`).
- The collapse predates this branch — splitting it is independent
  refactoring work.
- The user-visible API (`reader.luminescence.read(...)`,
  `reader.driver.get_status()`) doesn't depend on the internal
  layering and would survive a refactor unchanged for callers.
- Helper-subsystem grouping (F1, F2) changes call sites
  (`driver.led_bar.set` vs `driver.set_led`); worth
  doing in a single coordinated PR rather than piecemeal.

## Reference

- v1b1-capability skill review run: `2026-05-06`
- Patterns cited: P-05, P-06, P-08, P-13, P-16, P-19, P-22, P-25 from
  `~/.claude/skills/v1b1-capability/reference.md`
- v1b1 helper precedent: `pylabrobot/hamilton/liquid_handlers/star/cover.py`,
  `wash_station.py`, `x_arm.py`, `autoload.py`, and
  `pylabrobot/hamilton/liquid_handlers/nimbus/door.py`
