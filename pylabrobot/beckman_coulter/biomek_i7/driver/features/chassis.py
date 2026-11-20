"""The chassis module: its inputs and outputs, the legacy deck devices it lists, and the interlock's
state."""

from typing import Dict, List, Optional

from pylabrobot.beckman_coulter.biomek_i7.driver import protocol as p
from pylabrobot.beckman_coulter.biomek_i7.driver.board import AceBoard
from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import ModuleConfiguration
from pylabrobot.beckman_coulter.biomek_i7.driver.module import (
  read_analog_inputs,
  read_module,
)


class Chassis:
  """The chassis's ACE module. Reached as `driver.chassis`."""

  def __init__(self, board: AceBoard):
    self.board = board
    self.configuration: Optional[ModuleConfiguration] = None

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
  # The interlock cuts the arms' drive power when the light curtain is broken or the host trips it;
  # nothing moves while it is tripped. Here it is only read.

  async def request_interlock_state(self) -> p.InterlockState:
    """`armed` (the drives may have power) or `tripped`. Moves nothing.

    Biomek's logs never show the Master Controller sending Get Interlock Status. The request only
    reads the state; it changes nothing.
    """
    return p.parse_interlock_status((await self.board.send(p.get_interlock_status())).payload)
