"""Per-head-type W-axis calibration and unit conversion.

Ported from ``Get-WUlToMmFactor`` and ``Configure-WAxis`` in
``darwin_bridge.ps1`` (lines 58-77, 228-328). The W axis is the plunger;
its hardware range, calibration offset, and µL→mm factor all vary by the
pipette head currently attached.

Callers (DarwinController.set_head_type) should:
    1. Look up the head-type config here
    2. Replace the W-axis ``AxisCalibration`` with these values
    3. Re-apply the 57-entry W-axis PID table (see waxis_params.py)
"""

from __future__ import annotations

from dataclasses import dataclass

from pylabrobot.liquid_handling.backends.agilent.bravo.darwin.calibration import AxisCalibration
from pylabrobot.liquid_handling.backends.agilent.bravo.types import HeadType


@dataclass(frozen=True)
class WAxisHeadConfig:
    """W-axis settings for one head type."""

    hardware_min: float
    hardware_max: float
    software_min: float
    software_max: float
    ul_to_mm_factor: float       # multiplier: volume_mm = volume_ul * factor
    homing_timeout_ms: int = 40_000

    def calibration(self, calibration_offset: float = 0.0) -> AxisCalibration:
        # Mirror the bridge's Configure-WAxis behaviour: it sets both
        # HardwareMinimum/Maximum AND SoftwareMinimum/Maximum per head type.
        # Dropping the software limits here would leave validate_target
        # falling back to (hardware_min + 0.07), which is much looser than
        # the head's actual safe envelope and would let task-level bugs
        # (e.g. a profile specifying tips_off_w_position = -11 for a head
        # whose software_min is -9.3) reach the wire.
        return AxisCalibration(
            hardware_min=self.hardware_min,
            hardware_max=self.hardware_max,
            software_min=self.software_min,
            software_max=self.software_max,
            park_position=0.0,
            calibration_offset=calibration_offset,
        )


# Shared configs used by multiple head types
_DTIP_STANDARD = WAxisHeadConfig(
    hardware_min=-16.48, hardware_max=63.52,
    software_min=-9.1862, software_max=56.226,
    ul_to_mm_factor=448.0 / 2000.0,
)

_ST384 = WAxisHeadConfig(
    hardware_min=-14.197, hardware_max=65.803,
    software_min=-9.31446, software_max=60.92,
    ul_to_mm_factor=1692.0 / 2000.0,
)

_ASSAYMAP = WAxisHeadConfig(
    hardware_min=-19.921875, hardware_max=80.078125,
    software_min=-0.0024, software_max=60.15865,
    ul_to_mm_factor=385.0 / 1600.0,
)

_F96_50 = WAxisHeadConfig(
    hardware_min=-24.55, hardware_max=55.45,
    software_min=-0.00618, software_max=30.90618,
    ul_to_mm_factor=1236.0 / 2000.0,
)

_F96_200 = WAxisHeadConfig(
    hardware_min=-13.98, hardware_max=61.02,
    software_min=-9.1862, software_max=56.226,
    ul_to_mm_factor=487.0 / 2000.0,
)


HEAD_CONFIGS: dict[HeadType, WAxisHeadConfig] = {
    HeadType.HT_96_ASSAYMAP: _ASSAYMAP,
    HeadType.HT_8_D_LT: _DTIP_STANDARD,
    HeadType.HT_96_D_70: _DTIP_STANDARD,
    HeadType.HT_96_D_70_S2: _DTIP_STANDARD,
    HeadType.HT_96_D_200: _DTIP_STANDARD,
    HeadType.HT_96_D_200_S2: _DTIP_STANDARD,
    HeadType.HT_16_D_ST: _ST384,
    HeadType.HT_384_D_70: _ST384,
    HeadType.HT_384_D_70_S2: _ST384,
    HeadType.HT_384_F_50: _ST384,
    HeadType.HT_8_F_50: _ST384,
    HeadType.HT_96_F_50: _F96_50,
    HeadType.HT_96_F_200: _F96_200,
}


def config_for_head(head_type: HeadType) -> WAxisHeadConfig | None:
    """Return the W-axis config for a given head type, or None if unsupported."""
    return HEAD_CONFIGS.get(head_type)


def ul_to_mm(volume_ul: float, head_type: HeadType) -> float:
    """Convert a pipette volume (µL) to W-axis travel distance (mm)."""
    cfg = config_for_head(head_type)
    if cfg is None:
        raise ValueError(f"Unknown W-axis head type: {head_type!r}")
    return volume_ul * cfg.ul_to_mm_factor


def mm_to_ul(travel_mm: float, head_type: HeadType) -> float:
    """Convert W-axis travel distance (mm) back to volume (µL)."""
    cfg = config_for_head(head_type)
    if cfg is None:
        raise ValueError(f"Unknown W-axis head type: {head_type!r}")
    if cfg.ul_to_mm_factor == 0:
        return 0.0
    return travel_mm / cfg.ul_to_mm_factor
