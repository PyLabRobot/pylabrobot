"""Firmware wrapper for ZaapMotion BLDC motor controllers on Air LiHa.

Each Air LiHa tip has an independent ZaapMotion BLDC controller
addressed via the transparent pipeline prefix ``T2{tip_index}``.
"""

from __future__ import annotations

from pylabrobot.tecan.evo.firmware.arm_base import CommandInterface


class ZaapMotion:
  """Commands for ZaapMotion motor controllers (T2x pipeline).

  Each Air LiHa tip has an independent ZaapMotion BLDC controller
  addressed via the transparent pipeline prefix ``T2{tip_index}``.
  """

  def __init__(self, interface: CommandInterface, module: str = "C5"):
    self.interface = interface
    self.module = module

  def _prefix(self, tip: int) -> str:
    return f"T2{tip}"

  async def exit_boot_mode(self, tip: int) -> None:
    """Exit bootloader mode (T2{tip}X)."""
    await self.interface.send_command(
      module=self.module, command=f"{self._prefix(tip)}X"
    )

  async def read_firmware_version(self, tip: int) -> str:
    """Read firmware version (T2{tip}RFV).

    Returns:
      Firmware version string. Contains ``'BOOT'`` if in bootloader mode.
    """
    resp = await self.interface.send_command(
      module=self.module, command=f"{self._prefix(tip)}RFV"
    )
    return str(resp["data"][0]) if resp and resp.get("data") else ""

  async def read_config_status(self, tip: int) -> None:
    """Read configuration status (T2{tip}RCS).

    Raises:
      TecanError: if the controller is not yet configured.
    """
    await self.interface.send_command(
      module=self.module, command=f"{self._prefix(tip)}RCS"
    )

  async def set_force_ramp(self, tip: int, value: int) -> None:
    """Set force ramp value (T2{tip}SFR{value}).

    Args:
      value: Force ramp. 133120 for active, 3752 for idle.
    """
    await self.interface.send_command(
      module=self.module, command=f"{self._prefix(tip)}SFR{value}"
    )

  async def set_force_mode(self, tip: int) -> None:
    """Enable force position mode (T2{tip}SFP1)."""
    await self.interface.send_command(
      module=self.module, command=f"{self._prefix(tip)}SFP1"
    )

  async def set_default_position(self, tip: int, value: int) -> None:
    """Set default position (T2{tip}SDP{value}).

    Args:
      value: Default position value. 1400 is standard idle.
    """
    await self.interface.send_command(
      module=self.module, command=f"{self._prefix(tip)}SDP{value}"
    )

  async def configure_motor(self, tip: int, command: str) -> None:
    """Send a motor configuration command (T2{tip}{command}).

    Args:
      command: One of the ZAAPMOTION_CONFIG commands (e.g. ``'CFE 255,500'``).
    """
    await self.interface.send_command(
      module=self.module, command=f"{self._prefix(tip)}{command}"
    )

  async def set_sdo(self, param: str) -> None:
    """Set SDO parameter (T23SDO{param}).

    Args:
      param: SDO parameter string (e.g. ``'11,1'``).
    """
    await self.interface.send_command(
      module=self.module, command=f"T23SDO{param}"
    )
