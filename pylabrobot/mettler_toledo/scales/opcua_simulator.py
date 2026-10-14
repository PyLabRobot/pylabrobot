"""Run an OPC UA balance simulator with editable load and fault injection.

Start with ``python -m pylabrobot.mettler_toledo.scales.opcua_simulator``.
No serial transport is constructed and no hardware is accessed.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import signal
from types import FrameType
from typing import Optional

from .driver import MettlerToledoResponse, MTSICSDriver
from .errors import MettlerToledoError
from .opcua import USE_ASYNCUA, MettlerToledoOPCUAServer

if USE_ASYNCUA:
  from asyncua import ua  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)
SIMULATION_NAMESPACE_URI = "https://pylabrobot.org/opcua/mettler-toledo/simulation"


class SimulatedBalance(MTSICSDriver):
  """An in-memory MT-SICS balance with a controllable gross load and overload fault."""

  def __init__(self) -> None:
    """Initialize a 12.345 g load without constructing the driver's serial transport."""
    self.device_type = "WXS205SDU (SIMULATED)"
    self.serial_number = "SIMULATED-001"
    self.firmware_version = "simulation"
    self.capacity = 220.009
    self._load = 12.345
    self._zero = 0.0
    self._tare = 0.0
    self._fault = False

  def set_load(self, load: float) -> None:
    """Set the simulated gross load in grams, within its physical capacity."""
    if not math.isfinite(load) or not 0 <= load <= self.capacity:
      raise ValueError(f"load must be finite and between 0 and {self.capacity} g")
    self._load = load

  def set_fault(self, active: bool) -> None:
    """Enable or clear a simulated overload rejection of balance commands."""
    self._fault = active

  async def setup(self) -> None:
    """Make the simulator ready; its in-memory connection needs no setup."""

  async def stop(self) -> None:
    """Leave the in-memory simulator unchanged; there is no hardware connection to close."""

  async def send_command(self, command: str, timeout: int = 60) -> list[MettlerToledoResponse]:
    """Implement the six server operations using MT-SICS responses and validation."""
    if self._fault:
      raise MettlerToledoError.overload()
    if command in ("S", "SI"):
      return [MettlerToledoResponse("S", "S", [str(self._load - self._zero - self._tare), "g"])]
    if command == "TA":
      return [MettlerToledoResponse("TA", "A", [str(self._tare), "g"])]
    if command == "Z":
      self._zero, self._tare = self._load, 0.0
    elif command == "T":
      self._tare = self._load - self._zero
      return [MettlerToledoResponse("T", "S", [str(self._tare), "g"])]
    elif command == "TAC":
      self._tare = 0.0
    else:
      raise MettlerToledoError("Unsupported simulated command", command)
    return [MettlerToledoResponse(command, "A")]


async def add_simulation_controls(
  adapter: MettlerToledoOPCUAServer, device: SimulatedBalance
) -> None:
  """Add typed load/fault controls in a separate simulator-only namespace."""
  namespace = await adapter.server.register_namespace(SIMULATION_NAMESPACE_URI)
  simulation = await adapter.server.nodes.objects.add_object(
    ua.NodeId("Simulation", namespace), ua.QualifiedName("Simulation", namespace)
  )

  async def set_load(parent: ua.NodeId, *inputs: ua.Variant) -> ua.CallMethodResult:
    """Validate a scalar Double input before changing the simulated gross load."""
    result = ua.CallMethodResult()
    if parent != simulation.nodeid:
      result.StatusCode = ua.StatusCode(ua.StatusCodes.BadMethodInvalid)
    elif len(inputs) != 1:
      result.StatusCode = ua.StatusCode(
        ua.StatusCodes.BadArgumentsMissing if not inputs else ua.StatusCodes.BadTooManyArguments
      )
    elif inputs[0].VariantType != ua.VariantType.Double or not isinstance(inputs[0].Value, float):
      result.StatusCode = ua.StatusCode(ua.StatusCodes.BadTypeMismatch)
    else:
      try:
        device.set_load(inputs[0].Value)
      except ValueError:
        result.StatusCode = ua.StatusCode(ua.StatusCodes.BadOutOfRange)
    return result

  async def set_fault(parent: ua.NodeId, *inputs: ua.Variant) -> ua.CallMethodResult:
    """Validate a scalar Boolean input before toggling the simulated overload."""
    result = ua.CallMethodResult()
    if parent != simulation.nodeid:
      result.StatusCode = ua.StatusCode(ua.StatusCodes.BadMethodInvalid)
    elif len(inputs) != 1:
      result.StatusCode = ua.StatusCode(
        ua.StatusCodes.BadArgumentsMissing if not inputs else ua.StatusCodes.BadTooManyArguments
      )
    elif inputs[0].VariantType != ua.VariantType.Boolean or not isinstance(inputs[0].Value, bool):
      result.StatusCode = ua.StatusCode(ua.StatusCodes.BadTypeMismatch)
    else:
      device.set_fault(inputs[0].Value)
    return result

  await simulation.add_method(
    ua.NodeId("Simulation.SetLoad", namespace),
    ua.QualifiedName("SetLoad", namespace),
    set_load,
    [
      ua.Argument(
        Name="load",
        DataType=ua.NodeId(ua.ObjectIds.Double),
        ValueRank=-1,
        Description=ua.LocalizedText("Simulated gross load in grams, from 0 to 220.009"),
      )
    ],
    [],
  )
  await simulation.add_method(
    ua.NodeId("Simulation.SetFault", namespace),
    ua.QualifiedName("SetFault", namespace),
    set_fault,
    [
      ua.Argument(
        Name="active",
        DataType=ua.NodeId(ua.ObjectIds.Boolean),
        ValueRank=-1,
        Description=ua.LocalizedText(
          "True injects an overload fault; False restores normal operation"
        ),
      )
    ],
    [],
  )


async def serve(endpoint: str, poll_interval: float) -> None:
  """Serve the simulator until interrupted, polling weight at the requested interval."""
  device = SimulatedBalance()
  adapter = MettlerToledoOPCUAServer(device, endpoint, poll_interval=poll_interval)
  stopped = asyncio.Event()
  loop = asyncio.get_running_loop()

  def terminate(signum: int, frame: Optional[FrameType]) -> None:
    """Request clean shutdown when a background process receives SIGTERM."""
    loop.call_soon_threadsafe(stopped.set)

  previous_handler = signal.signal(signal.SIGTERM, terminate)
  try:
    await adapter.setup()
    await add_simulation_controls(adapter, device)
    logger.info("READY: simulated balance at %s; no hardware connected", endpoint)
    await stopped.wait()
  finally:
    await adapter.stop()
    signal.signal(signal.SIGTERM, previous_handler)


def main() -> None:
  """Parse server settings and run the hardware-free demonstration."""
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--endpoint", default="opc.tcp://127.0.0.1:4840/pylabrobot/mettler-toledo/")
  parser.add_argument(
    "--poll-interval", type=float, default=0.5, help="Seconds between weight polls"
  )
  args = parser.parse_args()
  logging.basicConfig(level=logging.INFO)
  logging.getLogger("asyncua").setLevel(logging.WARNING)
  try:
    asyncio.run(serve(args.endpoint, args.poll_interval))
  except KeyboardInterrupt:
    pass


if __name__ == "__main__":
  main()
