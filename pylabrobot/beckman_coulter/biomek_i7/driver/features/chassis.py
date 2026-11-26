"""The chassis module: interlock, status display, deck I/O and the instrument's own sensors (the door
is its own feature, `door.py`, read through the chassis)."""

from typing import Dict, List, Optional

from pylabrobot.beckman_coulter.biomek_i7.driver import protocol as p
from pylabrobot.beckman_coulter.biomek_i7.driver.board import AceBoard
from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import ModuleConfiguration
from pylabrobot.beckman_coulter.biomek_i7.driver.features.door import Door
from pylabrobot.beckman_coulter.biomek_i7.driver.module import (
  read_analog_inputs,
  read_module,
)


class Chassis:
  """The chassis's ACE module. Reached as `driver.chassis`."""

  def __init__(self, board: AceBoard):
    self.board = board
    self.configuration: Optional[ModuleConfiguration] = None
    self.front_cover = Door(self)

  async def discover(self) -> ModuleConfiguration:
    """Read what the chassis says about itself. Moves nothing."""
    self.configuration = await read_module(self.board)
    return self.configuration

  def _require_configuration(self) -> ModuleConfiguration:
    if self.configuration is None:
      raise RuntimeError("the chassis has not been read; call `discover()` or `setup()` first")
    return self.configuration

  async def request_analog_inputs(self) -> Dict[str, float]:
    """Every analog input (supply voltages, temperature...) by name, in its units."""
    return await read_analog_inputs(self.board, self._require_configuration())

  async def request_digital_inputs(self) -> Dict[str, int]:
    config = self._require_configuration()
    replies = await self.board.send_many(
      [p.read_input_output(p.Device.DIGITAL_INPUT, i) for i in config.digital_inputs]
    )
    return {config.digital_inputs[i]: r.payload[0] for i, r in zip(config.digital_inputs, replies)}

  async def request_digital_outputs(self) -> Dict[str, int]:
    config = self._require_configuration()
    replies = await self.board.send_many(
      [p.read_input_output(p.Device.DIGITAL_OUTPUT, i) for i in config.digital_outputs]
    )
    return {
      config.digital_outputs[i]: r.payload[0] for i, r in zip(config.digital_outputs, replies)
    }

  async def request_legacy_alp_devices(self) -> List[bytes]:
    """The legacy ALP devices the chassis lists, as the raw entries it answers."""
    payload = (await self.board.send(p.list_legacy_alp_devices())).payload
    return [payload[i : i + 2] for i in range(0, len(payload), 2)]

  # -- interlock -------------------------------------------------------------
  # The interlock cuts the arms' drive power when the light curtain is broken or the host trips it
  # (the door does not: see `door.py`); nothing moves while it is tripped. It can be read, tripped and re-armed; there is
  # deliberately no way here to bypass it.

  async def request_interlock_state(self) -> p.InterlockState:
    """`armed` (the drives may have power) or `tripped`. Moves nothing.

    Biomek's logs never show the Master Controller sending Get Interlock Status. The request only
    reads the state; it changes nothing.
    """
    return p.parse_interlock_status((await self.board.send(p.get_interlock_status())).payload)

  async def prepare_interlock(self) -> None:
    """Trip the interlock, ask to be told when it trips, and show READY, as before the motor tables
    are loaded after a power-up."""
    await self._unchecked_fw_trip_interlock()
    await self._unchecked_fw_notify_on_interlock_trip()
    await self._unchecked_fw_set_status_display("ready")

  async def _io_box_write_after_configure(self) -> None:
    """Send the legacy deck digital I/O box its WRITE 01, a cancel of what that box was doing:
    Biomek's logs show it after every Stop Module Execution (the Master Controller stopping the
    box), which in a cold start falls after the motor tables are loaded. Kept where the recorded
    start sends it, so the start stays byte for byte; it has nothing to do with the light curtain."""
    await self._unchecked_fw_send_digital_io_box(b"\x02\x01")

  async def trip_interlock(self, error: bool = False) -> None:
    """Cut the arms' drive power and show READY: how the Master Controller leaves the instrument
    at rest (it does so at every start; the drive supply reads off in over 90% of its polls).
    With `error`, show ERROR, as it does after a failed move."""
    await self._unchecked_fw_trip_interlock()
    await self._unchecked_fw_set_status_display("error" if error else "ready")

  async def arm_interlock(self) -> None:
    """Re-arm the interlock and show RUNNING. The light curtain must be clear.

    Raises:
      RuntimeError: if the interlock does not report ARMED.
    """
    state = await self._unchecked_fw_rearm_interlock()
    if state[:1] != b"\x00":
      raise RuntimeError(
        f"the interlock did not arm (state {state.hex()}); is the light curtain clear?"
      )
    await self._unchecked_fw_set_status_display("running")

  # -- firmware commands -----------------------------------------------------
  # The chassis's writes, one request each, as the Master Controller sends them. No checks and no
  # bookkeeping: the public methods above decide when each is sent.

  async def _unchecked_fw_trip_interlock(self) -> None:
    await self.board.send(p.trip_interlock())

  async def _unchecked_fw_rearm_interlock(self) -> bytes:
    """Re-Arm Interlock. Returns what it answers: `00` when armed."""
    return (await self.board.send(p.rearm_interlock())).payload

  async def _unchecked_fw_notify_on_interlock_trip(self) -> None:
    await self.board.send(p.notify_on_interlock_trip())

  async def _unchecked_fw_set_status_display(self, state: p.StatusDisplayState) -> None:
    await self.board.send(p.set_status_display(state))

  async def _unchecked_fw_send_digital_io_box(self, data: bytes) -> None:
    await self.board.send(p.send_digital_io_box(data))

  async def _unchecked_fw_notify_on_input_change(self, index: int, notify: bool) -> None:
    await self.board.send(p.notify_on_change(p.Device.DIGITAL_INPUT, index, notify))
