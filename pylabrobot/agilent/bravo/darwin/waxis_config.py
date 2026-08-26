"""Per-head-type W-axis calibration and unit conversion.

The W axis is the plunger; its hardware range, calibration offset, and
uL->mm factor all vary by the pipette head currently attached.

Callers (``DarwinController.set_head_type``) should:

  1. Look up the head-type config here.
  2. Replace the W-axis :class:`~.calibration.AxisCalibration` with these
     values.
  3. Re-apply the 57-entry W-axis PID table (see :mod:`.waxis_params`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from ..types import HeadType
from .calibration import AxisCalibration


@dataclass(frozen=True)
class WAxisHeadConfig:
  """W-axis settings for one head type.

  Attributes:
    hardware_min: The W axis's hardware travel minimum for this head, in mm.
    hardware_max: The W axis's hardware travel maximum for this head, in mm.
    software_min: The enforced move-target minimum for this head, in mm.
    software_max: The enforced move-target maximum for this head, in mm.
    ul_to_mm_factor: Multiplier converting a pipetted volume in
      microliters to W-axis travel in mm.
    homing_timeout: Homing timeout for the W axis with this head
      installed, in seconds.
  """

  hardware_min: float
  hardware_max: float
  software_min: float
  software_max: float
  ul_to_mm_factor: float
  homing_timeout: float = 40.0

  def calibration(self, calibration_offset: float = 0.0) -> AxisCalibration:
    """Build the W-axis calibration record for this head type.

    Sets both the hardware and software limits explicitly. Omitting the
    software limits here would leave :meth:`~.calibration.AxisCalibration.validate_target`
    falling back to ``hardware_min + 0.07``, which is much looser than the
    head's actual safe envelope and would let task-level bugs (e.g. a
    profile specifying a tips-off W position below this head's real
    ``software_min``) reach the wire.

    Args:
      calibration_offset: Offset applied between normalized and physical
        units.

    Returns:
      The calibration record for this head type.
    """
    return AxisCalibration(
      hardware_min=self.hardware_min,
      hardware_max=self.hardware_max,
      software_min=self.software_min,
      software_max=self.software_max,
      park_position=0.0,
      calibration_offset=calibration_offset,
    )


# Shared configs used by multiple head types.
_DTIP_STANDARD = WAxisHeadConfig(
  hardware_min=-16.48,
  hardware_max=63.52,
  software_min=-9.1862,
  software_max=56.226,
  ul_to_mm_factor=448.0 / 2000.0,
)

_ST384 = WAxisHeadConfig(
  hardware_min=-14.197,
  hardware_max=65.803,
  software_min=-9.31446,
  software_max=60.92,
  ul_to_mm_factor=1692.0 / 2000.0,
)

_ASSAYMAP = WAxisHeadConfig(
  hardware_min=-19.921875,
  hardware_max=80.078125,
  software_min=-0.0024,
  software_max=60.15865,
  ul_to_mm_factor=385.0 / 1600.0,
)

_F96_50 = WAxisHeadConfig(
  hardware_min=-24.55,
  hardware_max=55.45,
  software_min=-0.00618,
  software_max=30.90618,
  ul_to_mm_factor=1236.0 / 2000.0,
)

_F96_200 = WAxisHeadConfig(
  hardware_min=-13.98,
  hardware_max=61.02,
  software_min=-9.1862,
  software_max=56.226,
  ul_to_mm_factor=487.0 / 2000.0,
)


HEAD_CONFIGS: Dict[HeadType, WAxisHeadConfig] = {
  "96_assaymap": _ASSAYMAP,
  "8_d_lt": _DTIP_STANDARD,
  "96_d_70": _DTIP_STANDARD,
  "96_d_70_s2": _DTIP_STANDARD,
  "96_d_200": _DTIP_STANDARD,
  "96_d_200_s2": _DTIP_STANDARD,
  "16_d_st": _ST384,
  "384_d_70": _ST384,
  "384_d_70_s2": _ST384,
  "384_f_50": _ST384,
  "8_f_50": _ST384,
  "96_f_50": _F96_50,
  "96_f_200": _F96_200,
}


def config_for_head(head_type: HeadType) -> Optional[WAxisHeadConfig]:
  """Return the W-axis config for a given head type.

  Args:
    head_type: The head type to look up.

  Returns:
    The head's W-axis configuration, or ``None`` if the head type has no
    W-axis mapping.
  """
  return HEAD_CONFIGS.get(head_type)


def ul_to_mm(volume_ul: float, head_type: HeadType) -> float:
  """Convert a pipette volume in microliters to W-axis travel in mm.

  Args:
    volume_ul: The volume to convert, in microliters.
    head_type: The installed head type.

  Returns:
    The equivalent W-axis travel, in mm.

  Raises:
    ValueError: If ``head_type`` has no W-axis mapping.
  """
  cfg = config_for_head(head_type)
  if cfg is None:
    raise ValueError(f"Unknown W-axis head type: {head_type!r}")
  return volume_ul * cfg.ul_to_mm_factor


def mm_to_ul(travel_mm: float, head_type: HeadType) -> float:
  """Convert W-axis travel in mm back to a volume in microliters.

  Args:
    travel_mm: The W-axis travel to convert, in mm.
    head_type: The installed head type.

  Returns:
    The equivalent volume, in microliters. ``0.0`` if the head's
    uL-to-mm factor is zero.

  Raises:
    ValueError: If ``head_type`` has no W-axis mapping.
  """
  cfg = config_for_head(head_type)
  if cfg is None:
    raise ValueError(f"Unknown W-axis head type: {head_type!r}")
  if cfg.ul_to_mm_factor == 0:
    return 0.0
  return travel_mm / cfg.ul_to_mm_factor
