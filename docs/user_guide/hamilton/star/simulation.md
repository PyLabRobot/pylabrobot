# STAR chatterbox simulation

`STARChatterboxBackend` is a device-free STAR backend. Constructing it with no extra arguments uses
the unconfigured query defaults, so existing scripts and tests do not need to change.

```python
from pylabrobot.legacy.liquid_handling import LiquidHandler
from pylabrobot.legacy.liquid_handling.backends.hamilton.STAR_chatterbox import (
  STARChatterboxBackend,
  STARChatterboxState,
)
from pylabrobot.resources.hamilton import STARLetDeck

lh = LiquidHandler(backend=STARChatterboxBackend(), deck=STARLetDeck())
```

## Configurable query state

Pass a `STARChatterboxState` to configure simulated device/environment readings for deterministic
protocol or recovery tests:

```python
backend = STARChatterboxBackend(
  chatterbox_state=STARChatterboxState(
    iswap_initialization_status=False,
    channel_z_positions=[100.0] * 8,
    dispensing_drive_positions=[0.0] * 8,
  )
)
```

Configurable readings:

- `iswap_initialization_status` — `request_iswap_initialization_status()` (default `True`)
- `channel_z_positions` — `request_z_pos_channel_n()` (default `285.0` mm per channel)
- `dispensing_drive_positions` — `channel_dispensing_drive_request_position()` (default `0.0`)

These are simulated query replies, not physical verification. Motion commands do not update them.

To change readings after construction, assign a new `STARChatterboxState`. Assignment replaces the
whole object: omitted vectors refill the unconfigured defaults. The setter copies the vectors and
checks their lengths against `num_channels`. Bound channel vectors are tuples; in-place mutation is
not supported. `setup()` does not reset this state.

```python
backend.chatterbox_state = STARChatterboxState(
  iswap_initialization_status=False,
  channel_z_positions=[90.0] * backend.num_channels,
  dispensing_drive_positions=[3.0] * backend.num_channels,
)
```

`channel_dispensing_drive_request_position(channel_idx, simulated_value=...)` still accepts a
per-call override. Omit the argument to use instance state. Pass `simulated_value=0.0` when the
override should be zero; that is distinct from omitting the argument. Overrides do not write back
to instance state.

## Owned elsewhere (not in `STARChatterboxState`)

- Tip presence, 96-head tip presence, and tip length: `TipTracker`
- Last LLD heights: latched by simulated LLD (`request_pip_height_last_lld`)
- Installed modules and factory geometry: `MachineConfiguration`, `ExtendedConfiguration`,
  `iSWAPInformation`, and `channels_minimum_y_spacing`
- iSWAP parked / CoRe parked: existing latched backend flags

## Not a fault scenario

This is static configurable query state. It does not script response sequences, inject timeouts or
failures, model partial physical success, or replace the resource model. Carrier-presence and CoRe
resource-existence commands are not chatterbox overrides; they still follow the firmware command
path.
