"""Base class for Tecan EVO arm firmware wrappers.

Provides position caching for collision avoidance between arms sharing the
same worktable (e.g. LiHa and RoMa X-axes).
"""

from __future__ import annotations

from typing import Dict, List, Protocol, runtime_checkable


@runtime_checkable
class CommandInterface(Protocol):
  """Duck-typed interface for anything that can send Tecan firmware commands.

  This allows firmware wrappers to work with either the legacy EVOBackend
  or the new TecanEVODriver without importing either.
  """

  async def send_command(
    self,
    module: str,
    command: str,
    params: list | None = ...,
    **kwargs: object,
  ) -> dict: ...


class EVOArm:
  """Base class for EVO arm firmware wrappers. Caches arm positions."""

  _pos_cache: Dict[str, int] = {}

  def __init__(self, interface: CommandInterface, module: str):
    self.interface = interface
    self.module = module

  async def position_initialization_x(self) -> None:
    """Reinitializes X-axis of the arm."""
    await self.interface.send_command(module=self.module, command="PIX")

  async def report_x_param(self, param: int) -> int:
    """Report current parameter for x-axis.

    Args:
      param: 0 - current position, 5 - actual machine range
    """
    resp: List[int] = (
      await self.interface.send_command(module=self.module, command="RPX", params=[param])
    )["data"]
    return resp[0]

  async def report_y_param(self, param: int) -> List[int]:
    """Report current parameters for y-axis.

    Args:
      param: 0 - current position, 5 - actual machine range
    """
    resp: List[int] = (
      await self.interface.send_command(module=self.module, command="RPY", params=[param])
    )["data"]
    return resp
