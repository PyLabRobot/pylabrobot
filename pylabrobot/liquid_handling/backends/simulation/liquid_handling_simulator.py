import logging
from typing import Any, Dict, Optional

from pylabrobot.liquid_handling.standard import Move
from pylabrobot.liquid_handling.backends import SerializingBackend
from pylabrobot.resources import Resource
from pylabrobot.simulator import Simulator, SimulatorBackend


logger = logging.getLogger("pylabrobot")


class LiquidHandlerSimulator(SerializingBackend, SimulatorBackend):
  """ Backend for using a liquid handler in the simulator.

  .. note::

    See :doc:`/using-the-simulator` for a more complete tutorial.
  """

  def __init__(self, simulator: Simulator):
    """ Create a new liquid handling simulator backend. This will connect to the
    {class}`~pylabrobot.simulator.Simulator` and send commands to it.

    Args:
      simulator: The simulator to use.
    """

    SerializingBackend.__init__(self, num_channels=8) # TODO: num_channels
    SimulatorBackend.__init__(self, simulator=simulator)

    self.simulator = simulator

  async def send_command(
    self,
    command: str,
    data: Optional[Dict[str, Any]] = None
  ) -> Optional[dict]:
    return await self.simulator.send_command(device=self, event=command, data=data)

  async def move_resource(self, move: Move, **backend_kwargs):
    raise NotImplementedError("This method is not yet implemented in the simulator.")

  async def assigned_resource_callback(self, resource: Resource):
    # In simulation, resource assignment is handled by the Simulator, not the backend, -> do nothing
    return

  async def unassigned_resource_callback(self, name: str):
    # In simulation, resource assignment is handled by the Simulator, not the backend, -> do nothing
    return
