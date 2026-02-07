"""EL406 query methods.

This module contains the mixin class for query operations on the
BioTek EL406 plate washer.
"""

from __future__ import annotations

import enum
import logging
from typing import TypeVar

from .constants import (
  GET_PERISTALTIC_INSTALLED_COMMAND,
  GET_SENSOR_ENABLED_COMMAND,
  GET_SERIAL_NUMBER_COMMAND,
  GET_SYRINGE_BOX_INFO_COMMAND,
  GET_SYRINGE_MANIFOLD_COMMAND,
  GET_WASHER_MANIFOLD_COMMAND,
  LONG_READ_TIMEOUT,
  RUN_SELF_CHECK_COMMAND,
)
from .enums import (
  EL406Sensor,
  EL406SyringeManifold,
  EL406WasherManifold,
)

logger = logging.getLogger("pylabrobot.plate_washing.biotek.el406")

_E = TypeVar("_E", bound=enum.Enum)


class EL406QueriesMixin:
  """Mixin providing query methods for the EL406.

  This mixin provides:
  - Manifold queries (washer, syringe)
  - Serial number query
  - Sensor status query
  - Syringe box info query
  - Peristaltic pump installation query
  - Instrument settings query
  - Self-check query

  Requires:
    self._send_framed_query: Async method for sending framed queries
  """

  async def _send_framed_query(
    self,
    command: int,
    data: bytes = b"",
    timeout: float | None = None,
  ) -> bytes:
    raise NotImplementedError

  @staticmethod
  def _extract_payload_byte(response_data: bytes) -> int:
    """Extract the first payload byte, handling optional 2-byte header prefix."""
    return response_data[2] if len(response_data) > 2 else response_data[0]

  async def _query_enum(self, command: int, enum_cls: type[_E], label: str) -> _E:
    """Send a framed query and parse the response byte as an *enum_cls* member."""
    logger.info("Querying %s", label)
    response_data = await self._send_framed_query(command)
    logger.debug("%s response data: %s", label.capitalize(), response_data.hex())
    value_byte = self._extract_payload_byte(response_data)

    try:
      result = enum_cls(value_byte)
    except ValueError:
      logger.warning("Unknown %s: %d (0x%02X)", label, value_byte, value_byte)
      raise ValueError(
        f"Unknown {label}: {value_byte} (0x{value_byte:02X}). "
        f"Valid types: {[m.name for m in enum_cls]}"
      ) from None

    logger.info("%s: %s (0x%02X)", label.capitalize(), result.name, result.value)
    return result

  async def get_washer_manifold(self) -> EL406WasherManifold:
    """Query the installed washer manifold type."""
    return await self._query_enum(
      GET_WASHER_MANIFOLD_COMMAND, EL406WasherManifold, "washer manifold type"
    )

  async def get_syringe_manifold(self) -> EL406SyringeManifold:
    """Query the installed syringe manifold type."""
    return await self._query_enum(
      GET_SYRINGE_MANIFOLD_COMMAND, EL406SyringeManifold, "syringe manifold type"
    )

  async def get_serial_number(self) -> str:
    """Query the product serial number."""
    logger.info("Querying product serial number")
    response_data = await self._send_framed_query(GET_SERIAL_NUMBER_COMMAND)
    serial_number = response_data[2:].decode("ascii", errors="ignore").strip().rstrip("\x00")
    logger.info("Product serial number: %s", serial_number)
    return serial_number

  async def get_sensor_enabled(self, sensor: EL406Sensor) -> bool:
    """Query whether a specific sensor is enabled."""
    logger.info("Querying sensor enabled status: %s", sensor.name)
    response_data = await self._send_framed_query(GET_SENSOR_ENABLED_COMMAND, bytes([sensor.value]))
    logger.debug("Sensor enabled response data: %s", response_data.hex())
    enabled = bool(self._extract_payload_byte(response_data))
    logger.info("Sensor %s enabled: %s", sensor.name, enabled)
    return enabled

  async def get_syringe_box_info(self) -> dict:
    """Get syringe box information."""
    logger.info("Querying syringe box info")
    response_data = await self._send_framed_query(GET_SYRINGE_BOX_INFO_COMMAND)
    logger.debug("Syringe box info response data: %s", response_data.hex())

    box_type = self._extract_payload_byte(response_data)
    box_size = (
      response_data[3]
      if len(response_data) > 3
      else (response_data[1] if len(response_data) > 1 else 0)
    )
    installed = box_type != 0

    info = {
      "box_type": box_type,
      "box_size": box_size,
      "installed": installed,
    }

    logger.info("Syringe box info: %s", info)
    return info

  async def get_peristaltic_installed(self, selector: int) -> bool:
    """Check if a peristaltic pump is installed."""
    if selector < 0 or selector > 1:
      raise ValueError(f"Invalid selector {selector}. Must be 0 (primary) or 1 (secondary).")

    logger.info("Querying peristaltic pump installed: selector=%d", selector)
    response_data = await self._send_framed_query(
      GET_PERISTALTIC_INSTALLED_COMMAND, bytes([selector])
    )
    logger.debug("Peristaltic installed response data: %s", response_data.hex())

    installed = bool(self._extract_payload_byte(response_data))

    logger.info("Peristaltic pump %d installed: %s", selector, installed)
    return installed

  async def get_instrument_settings(self) -> dict:
    """Get current instrument hardware configuration."""
    logger.info("Querying instrument settings from hardware")

    washer_manifold = await self.get_washer_manifold()
    syringe_manifold = await self.get_syringe_manifold()
    syringe_box = await self.get_syringe_box_info()
    peristaltic_1 = await self.get_peristaltic_installed(0)
    peristaltic_2 = await self.get_peristaltic_installed(1)

    settings = {
      "washer_manifold": washer_manifold,
      "syringe_manifold": syringe_manifold,
      "syringe_box": syringe_box,
      "peristaltic_pump_1": peristaltic_1,
      "peristaltic_pump_2": peristaltic_2,
    }

    logger.info("Instrument settings: %s", settings)
    return settings

  async def run_self_check(self) -> dict:
    """Run instrument self-check diagnostics."""
    logger.info("Running instrument self-check")
    response_data = await self._send_framed_query(RUN_SELF_CHECK_COMMAND, timeout=LONG_READ_TIMEOUT)
    logger.debug("Self-check response data: %s", response_data.hex())
    error_code = self._extract_payload_byte(response_data)
    success = error_code == 0

    result = {
      "success": success,
      "error_code": error_code,
      "message": "Self-check passed"
      if success
      else f"Self-check failed (error code: {error_code})",
    }

    logger.info("Self-check result: %s", result["message"])
    return result
