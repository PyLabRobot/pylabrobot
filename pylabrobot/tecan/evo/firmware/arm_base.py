"""Base class for Tecan EVO arm firmware wrappers.

Provides position caching for collision avoidance between arms sharing the
same worktable (e.g. LiHa and RoMa X-axes).
"""

from __future__ import annotations

from typing import Dict, List

from pylabrobot.tecan.evo.driver import TecanEVODriver


class EVOArm:
  """Base class for EVO arm firmware wrappers. Caches arm positions."""

  _pos_cache: Dict[str, int] = {}

  def __init__(self, driver: TecanEVODriver, module: str):
    self.driver = driver
    self.module = module

  async def position_initialization_x(self) -> None:
    """Reinitializes X-axis of the arm."""
    await self.driver.send_command(module=self.module, command="PIX")

  async def report_x_param(self, param: int) -> int:
    """Report current parameter for x-axis.

    Args:
      param: 0 - current position, 5 - actual machine range
    """
    resp: List[int] = (
      await self.driver.send_command(module=self.module, command="RPX", params=[param])
    )["data"]
    return resp[0]

  async def report_y_param(self, param: int) -> List[int]:
    """Report current parameters for y-axis.

    Args:
      param: 0 - current position, 5 - actual machine range
    """
    resp: List[int] = (
      await self.driver.send_command(module=self.module, command="RPY", params=[param])
    )["data"]
    return resp

  async def read_error_register(self, param: int = 0) -> str:
    """Read error register (REE).

    Args:
      param: 0 = current errors, 1 = extended error info

    Returns:
      Error string where each character represents one axis status.
      ``'@'`` = no error, ``'A'`` = init failed, ``'G'`` = not initialized.
    """
    resp = await self.driver.send_command(module=self.module, command="REE", params=[param])
    data = resp.get("data")
    if data and isinstance(data, list) and len(data) > 0:
      return str(data[0])
    return ""

  async def position_init_all(self) -> None:
    """Initialize all axes (PIA)."""
    await self.driver.send_command(module=self.module, command="PIA")

  async def position_init_bus(self) -> None:
    """Initialize bus (PIB). Used for MCA modules."""
    await self.driver.send_command(module=self.module, command="PIB")

  async def set_bus_mode(self, mode: int) -> None:
    """Set bus mode (BMX).

    Args:
      mode: 2 = normal operation
    """
    await self.driver.send_command(module=self.module, command="BMX", params=[mode])

  async def bus_module_action(self, p1: int, p2: int, p3: int) -> None:
    """Bus module action (BMA). Use ``(0, 0, 0)`` to halt all axes.

    Args:
      p1: action parameter 1
      p2: action parameter 2
      p3: action parameter 3
    """
    await self.driver.send_command(module=self.module, command="BMA", params=[p1, p2, p3])
