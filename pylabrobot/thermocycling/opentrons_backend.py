"""Backend that drives an Opentrons Thermocycler via the HTTP API."""

import sys
from typing import cast, Optional

# OT-API HTTP client
from ot_api.modules import (
    list_connected_modules,
    thermocycler_open_lid,
    thermocycler_close_lid,
    thermocycler_set_block_temperature,
    thermocycler_set_lid_temperature,
    thermocycler_deactivate_block,
    thermocycler_deactivate_lid,
    thermocycler_run_profile_no_wait,
)

from pylabrobot.thermocycling.backend import ThermocyclerBackend

# Only supported on Python 3.10 with the OT-API HTTP client installed
PYTHON_VERSION = sys.version_info[:2]
USE_OT = PYTHON_VERSION == (3, 10)


class OpentronsThermocyclerBackend(ThermocyclerBackend):
    """HTTP-API backend for the Opentrons GEN-1/GEN-2 Thermocycler.

    All core functions are supported. run_profile() is fire-and-forget,
    since PCR runs can outlive the decorator’s default timeout.

    Note: Gen-2 via HTTP-API does not expose a separate lid target field,
    so get_lid_target_temperature() will always return None.
    """

    def __init__(self, opentrons_id: str):
        """Create a new backend bound to a specific thermocycler.

        Args:
          opentrons_id: The OT-API module “id” for your thermocycler.
        """
        super().__init__()  # Call parent constructor
        if not USE_OT:
            raise RuntimeError(
                "Opentrons HTTP-API client not available. "
                "Install via pip install -e <path-to-opentrons-python-api> on Python 3.10."
            )
        self.opentrons_id = opentrons_id

    async def setup(self):
        """No extra setup needed for HTTP-API thermocycler."""

    async def stop(self):
        """Gracefully deactivate both heaters."""
        await self.deactivate_block()
        await self.deactivate_lid()

    def serialize(self) -> dict:
        """Include the Opentrons module ID in serialized state."""
        return {**super().serialize(), "opentrons_id": self.opentrons_id}

    async def open_lid(self):
        """Open the thermocycler lid."""
        return thermocycler_open_lid(module_id=self.opentrons_id)

    async def close_lid(self):
        """Close the thermocycler lid."""
        return thermocycler_close_lid(module_id=self.opentrons_id)

    async def set_block_temperature(self, celsius: float):
        """Set block temperature in °C."""
        return thermocycler_set_block_temperature(
            celsius=celsius, module_id=self.opentrons_id
        )

    async def set_lid_temperature(self, celsius: float):
        """Set lid temperature in °C."""
        return thermocycler_set_lid_temperature(
            celsius=celsius, module_id=self.opentrons_id
        )

    async def deactivate_block(self):
        """Deactivate the block heater."""
        return thermocycler_deactivate_block(module_id=self.opentrons_id)

    async def deactivate_lid(self):
        """Deactivate the lid heater."""
        return thermocycler_deactivate_lid(module_id=self.opentrons_id)

    async def run_profile(self, profile: list[dict], block_max_volume: float):
        """Enqueue and return immediately (no wait) the PCR profile command."""
        return thermocycler_run_profile_no_wait(
            profile=profile,
            block_max_volume=block_max_volume,
            module_id=self.opentrons_id,
        )

    def _find_module(self) -> dict:
        """Helper to locate this module’s live-data dict."""
        for m in list_connected_modules():
            if m["id"] == self.opentrons_id:
                return cast(dict, m["data"])
        raise RuntimeError(f"Module '{self.opentrons_id}' not found")

    async def get_block_current_temperature(self) -> float:
        return cast(float, self._find_module()["currentTemperature"])

    async def get_block_target_temperature(self) -> Optional[float]:
        return cast(Optional[float], self._find_module().get("targetTemperature"))

    async def get_lid_current_temperature(self) -> float:
        return cast(float, self._find_module()["lidTemperature"])

    async def get_lid_target_temperature(self) -> Optional[float]:
        """Always None on Opentrons Thermocycler HTTP-API."""
        return cast(Optional[float], self._find_module().get("lidTargetTemperature"))

    async def get_lid_status(self) -> str:
        return cast(str, self._find_module()["lidStatus"])

    async def get_hold_time(self) -> float:
        return cast(float, self._find_module().get("holdTime", 0.0))

    async def get_current_cycle_index(self) -> int:
        """Get the one-based index of the current cycle from the Opentrons API."""
        return cast(int, self._find_module()["currentCycleIndex"])

    async def get_total_cycle_count(self) -> int:
        return cast(int, self._find_module()["totalCycleCount"])

    async def get_current_step_index(self) -> int:
        """Get the one-based index of the current step from the Opentrons API."""
        return cast(int, self._find_module()["currentStepIndex"])

    async def get_total_step_count(self) -> int:
        return cast(int, self._find_module()["totalStepCount"])
