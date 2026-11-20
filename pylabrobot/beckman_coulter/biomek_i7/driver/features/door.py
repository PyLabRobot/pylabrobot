"""The instrument's door: STAR's `FrontCover`, and reached by its name, `driver.front_cover` /
`biomek.front_cover`.

The door sensor is the chassis's digital input "Door Sensor": 1 closed, 0 open. The door only
reports: opening it does not trip the interlock, and Biomek arms the interlock with it open
(Biomek's logs, July to October 2026). The interlock (`chassis`) answers to the light curtain and
to the host. STAR's `enable_control` / `disable_control` have no counterpart here: the door has no
interlock to turn off.
"""

from typing import TYPE_CHECKING, Callable, Literal

from pylabrobot.beckman_coulter.biomek_i7.driver import protocol as p

if TYPE_CHECKING:
  from pylabrobot.beckman_coulter.biomek_i7.driver.features.chassis import Chassis

DoorState = Literal["open", "closed"]
DOOR_SENSOR_INPUT = "Door Sensor"
DOOR_CLOSED = 1


class Door:
  """The door, read through the chassis's door sensor."""

  def __init__(self, chassis: "Chassis"):
    self._chassis = chassis

  def _input(self) -> int:
    config = self._chassis._require_configuration()
    for index, name in config.digital_inputs.items():
      if name == DOOR_SENSOR_INPUT:
        return index
    raise RuntimeError(f"the chassis has no digital input named {DOOR_SENSOR_INPUT!r}")

  async def request_state(self) -> DoorState:
    """Whether the door is open or closed (STAR's `FrontCover.request_state`). Moves nothing."""
    board = self._chassis.board
    reply = await board.send(p.read_input_output(p.Device.DIGITAL_INPUT, self._input()))
    return "closed" if reply.payload[0] == DOOR_CLOSED else "open"

  def subscribe(self, callback: Callable[[DoorState], None]) -> Callable[[], None]:
    """Call `callback` whenever the door opens or closes. Returns an unsubscribe.

    The chassis reports changes only once asked to (Notify On Digital Input Change, which the
    Master Controller sends at every start and `notify_changes` sends)."""
    index = self._input()

    def on_message(reply: p.Reply) -> None:
      if (reply.device, reply.opcode) == p.DIGITAL_INPUT_VALUE and reply.index == index:
        callback("closed" if reply.payload[:1] == bytes([DOOR_CLOSED]) else "open")

    return self._chassis.board.subscribe(on_message)

  async def notify_changes(self, notify: bool = True) -> None:
    """Ask the chassis to report the door opening and closing (or, False, to stop). Moves
    nothing."""
    await self._chassis._unchecked_fw_notify_on_input_change(self._input(), notify)
