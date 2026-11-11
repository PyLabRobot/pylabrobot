"""One pod: an arm on the shared X rail, and its ACE module."""

from typing import Dict, Literal, Optional, Tuple

from pylabrobot.beckman_coulter.biomek_i7.driver import protocol as p
from pylabrobot.beckman_coulter.biomek_i7.driver.board import AceBoard
from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import ModuleConfiguration
from pylabrobot.beckman_coulter.biomek_i7.driver.module import (
  read_analog_inputs,
  read_module,
)

# How Biomek numbers its pods. Pod1 is the left pod and Pod2 the right on the instrument this
# driver was written against; whether that holds on every build is not established.
POD_NUMBERS = {"left": 1, "right": 2}
COMPONENT_PREFIXES = {"multichannel": "MC", "span8": "SPAN8"}

# The arm board's analog input carrying the interlocked motor supply ("+36V Interlock").
DRIVE_SUPPLY_INPUT = "+36V Interlock"


class Pod:
  """One pod. Reached as `driver.left_x_arm` / `driver.right_x_arm`: it fills the slot of STAR's
  `XArm`, though more of the instrument hangs on it. One board drives its X, Y and Z, its head
  or channels and its gripper.

  `module_type` is what the board reports itself to be (Get Module ID): `multichannel` (a
  96-channel head) or `span8` (eight independent channels).
  """

  def __init__(self, board: AceBoard, side: Literal["left", "right"], module_type: str):
    self.board = board
    self.side = side
    self.module_type = module_type
    self.configuration: Optional[ModuleConfiguration] = None

  @property
  def pod_number(self) -> int:
    return POD_NUMBERS[self.side]

  def __str__(self) -> str:
    return f"{self.side} pod (Pod{self.pod_number}, {self.module_type})"

  @property
  def component(self) -> str:
    """The name Biomek gives this pod's component in its logs (`host_command.component`):
    `MC_1`, `SPAN8_2`."""
    return f"{COMPONENT_PREFIXES[self.module_type]}_{self.pod_number}"

  async def discover(self) -> ModuleConfiguration:
    """Read what the pod's module says about itself. Moves nothing."""
    self.configuration = await read_module(self.board)
    return self.configuration

  async def request_drive_supply(self) -> float:
    """The pod's interlocked motor supply, in volts: about 36 with the interlock armed, about 0.1
    tripped. Moves nothing.

    Raises:
      RuntimeError: if the pod has no input named for it.
    """
    if self.configuration is None:
      raise RuntimeError(f"{self} has not been read; call `discover()` or `setup()` first")
    for index, name in self.configuration.analog_inputs.items():
      if name.startswith(DRIVE_SUPPLY_INPUT):
        reply = await self.board.send(p.read_input_output(p.Device.ANALOG_INPUT, index))
        return p.parse_float(reply.payload)
    raise RuntimeError(f"{self} has no {DRIVE_SUPPLY_INPUT!r} analog input")

  async def request_accuframe_offsets(self) -> Tuple[float, float, float]:
    """The X, Y and Z offsets the pod's AccuFrame board holds."""
    return p.parse_accuframe_offsets((await self.board.send(p.get_accuframe_offsets())).payload)

  async def request_analog_inputs(self) -> Dict[str, float]:
    if self.configuration is None:
      raise RuntimeError(f"{self} has not been read; call `discover()` or `setup()` first")
    return await read_analog_inputs(self.board, self.configuration)
