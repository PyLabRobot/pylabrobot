"""W-axis per-head-type PID and motion parameter sets.

57 parameters x 5 head-type sets. Each head type resolves to a named
parameter set (``ST96``, ``ST384``, ``LT``, ``AM``, ``F96_50``), and the set
dictates the values written to the W-axis device parameter database.

Almost every entry is encoded as Float32, matching the axis controller's
parameter declarations. The sole exception is ``I2T_TIME``, which the
firmware declares UInt32 -- it must be written as a plain uint (e.g. 5000 ms
-> 0x00001388) or the firmware replies with ``OUT_OF_RANGE`` (a
float-encoded 5000.0 lands at a huge magnitude when reinterpreted).

Application:

  1. :meth:`~.params.ParameterAccess.write_float`/
     :meth:`~.params.ParameterAccess.write_uint` for each entry.
     Pointer-caching cuts wire packets by roughly half because the entries
     are ordered by :class:`~pylabrobot.agilent.bravo.protocol.gemini.enums.ParamDBs`
     index.
  2. :meth:`~.params.ParameterAccess.apply` once at the end to commit.
  3. The caller remembers the current head type so subsequent enable/move
     calls can skip the re-apply unless the head changed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Literal, Optional, Tuple

from ..protocol.gemini.enums import ParamDBs
from ..types import HeadType
from .params import ParameterAccess

WAxisParamSet = Literal["ST96", "ST384", "LT", "AM", "F96_50"]
"""Which calibration family a head belongs to.

``ST96`` is short-tip 96, ``ST384`` is short-tip 384, ``LT`` is long-tip,
``AM`` is AssayMAP, and ``F96_50`` is fixed-tip 96-channel 50 uL.
"""

HEAD_TYPE_TO_SET: Dict[HeadType, WAxisParamSet] = {
  "96_assaymap": "AM",
  "8_d_lt": "LT",
  "96_d_200": "LT",
  "96_d_200_s2": "LT",
  "96_f_200": "LT",
  "384_d_70": "ST384",
  "384_d_70_s2": "ST384",
  "384_f_50": "ST384",
  "16_d_st": "ST384",
  "96_d_70": "ST96",
  "96_d_70_s2": "ST96",
  "96_f_50": "F96_50",
  "8_f_50": "F96_50",
}


@dataclass(frozen=True)
class WAxisParamEntry:
  """One parameter id with per-head-type values.

  Attributes:
    param: The parameter-database index this entry writes.
    ST96: The value for the short-tip 96 parameter set.
    ST384: The value for the short-tip 384 parameter set.
    LT: The value for the long-tip parameter set.
    AM: The value for the AssayMAP parameter set.
    F96_50: The value for the fixed-tip 96-channel 50 uL parameter set.
  """

  param: ParamDBs
  ST96: float
  ST384: float
  LT: float
  AM: float
  F96_50: float

  def value_for(self, param_set: WAxisParamSet) -> float:
    """Return this entry's value for the given parameter set.

    Args:
      param_set: Which head-type parameter family to select.

    Returns:
      The value to write for ``param_set``.
    """
    values: Dict[WAxisParamSet, float] = {
      "ST96": self.ST96,
      "ST384": self.ST384,
      "LT": self.LT,
      "AM": self.AM,
      "F96_50": self.F96_50,
    }
    return float(values[param_set])


# Ordered by ParamDBs index within each half of the table for pointer-caching
# efficiency.
WAXIS_PARAM_TABLE: Tuple[WAxisParamEntry, ...] = (
  WAxisParamEntry(ParamDBs.IQ_PTERM, ST96=0.3, ST384=0.35, LT=0.195, AM=0.31, F96_50=0.3),
  WAxisParamEntry(ParamDBs.IQ_ITERM, ST96=2050.0, ST384=2050.0, LT=1550.0, AM=977.0, F96_50=2050.0),
  WAxisParamEntry(ParamDBs.ID_PTERM, ST96=0.3, ST384=0.35, LT=0.195, AM=0.31, F96_50=0.3),
  WAxisParamEntry(ParamDBs.ID_ITERM, ST96=2050.0, ST384=2050.0, LT=1550.0, AM=977.0, F96_50=2050.0),
  WAxisParamEntry(ParamDBs.VEL_PTERM, ST96=1.75, ST384=1.75, LT=1.85, AM=1.75, F96_50=1.75),
  WAxisParamEntry(ParamDBs.VEL_ITERM, ST96=0.1, ST384=1.25, LT=1.5, AM=5.0, F96_50=0.1),
  WAxisParamEntry(ParamDBs.VEL_DTERM, ST96=0.0025, ST384=0.002, LT=0.001, AM=0.002, F96_50=0.0025),
  WAxisParamEntry(
    ParamDBs.VEL_CURR_OUT_SATURATION, ST96=0.95, ST384=0.95, LT=0.95, AM=0.95, F96_50=0.95
  ),
  WAxisParamEntry(ParamDBs.POS_PTERM, ST96=680.0, ST384=780.0, LT=650.0, AM=450.0, F96_50=680.0),
  WAxisParamEntry(ParamDBs.POS_ITERM, ST96=3.0, ST384=5.0, LT=7.5, AM=2.0, F96_50=3.0),
  WAxisParamEntry(
    ParamDBs.POS_DTERM, ST96=0.001, ST384=0.00075, LT=0.002, AM=0.00125, F96_50=0.001
  ),
  WAxisParamEntry(
    ParamDBs.ACCELERATION, ST96=6.345, ST384=6.345, LT=1.68, AM=1.44375, F96_50=4.635
  ),
  WAxisParamEntry(ParamDBs.JERK, ST96=1250.0, ST384=1250.0, LT=1171.875, AM=1250.0, F96_50=1250.0),
  WAxisParamEntry(ParamDBs.SPEED, ST96=6.345, ST384=6.345, LT=1.12, AM=1.44375, F96_50=4.635),
  WAxisParamEntry(
    ParamDBs.I2T_TIME, ST96=2000.0, ST384=5000.0, LT=2000.0, AM=2000.0, F96_50=2000.0
  ),
  WAxisParamEntry(
    ParamDBs.I2T_CONT_CURRENT,
    ST96=0.09127,
    ST384=0.09127,
    LT=0.09127,
    AM=0.0943,
    F96_50=0.09127,
  ),
  WAxisParamEntry(ParamDBs.I2T_PEAK_CURRENT, ST96=0.2, ST384=0.817, LT=0.2, AM=0.15, F96_50=0.2),
  WAxisParamEntry(
    ParamDBs.POS_MARGIN, ST96=0.0001, ST384=0.0001, LT=0.0001, AM=0.0001, F96_50=0.0001
  ),
  WAxisParamEntry(ParamDBs.POS_ERR_LIMIT, ST96=0.01, ST384=0.0125, LT=0.0125, AM=0.01, F96_50=0.01),
  WAxisParamEntry(
    ParamDBs.HOMING_OVERSHOOT, ST96=0.0375, ST384=0.0375, LT=0.0375, AM=0.03, F96_50=0.0187
  ),
  WAxisParamEntry(
    ParamDBs.HOMING_SPEED,
    ST96=0.016666666666,
    ST384=0.016666666666,
    LT=0.025,
    AM=0.016666666666,
    F96_50=0.016666666666,
  ),
  WAxisParamEntry(
    ParamDBs.HOMING_POS,
    ST96=0.1774625,
    ST384=0.1774625,
    LT=0.206,
    AM=0.19921875,
    F96_50=0.306875,
  ),
  WAxisParamEntry(ParamDBs.ALIGN_PTERM, ST96=0.43, ST384=0.43, LT=0.43, AM=1.28, F96_50=0.43),
  WAxisParamEntry(ParamDBs.ALIGN_ITERM, ST96=600.0, ST384=200.0, LT=600.0, AM=200.0, F96_50=600.0),
  WAxisParamEntry(
    ParamDBs.ALIGN_RAMP_CURRENT_TARGET,
    ST96=0.09127,
    ST384=0.09127,
    LT=0.09127,
    AM=0.0943,
    F96_50=0.09127,
  ),
  WAxisParamEntry(
    ParamDBs.SPEED_FEED_FWD_GAIN, ST96=0.35, ST384=0.36, LT=0.225, AM=0.55, F96_50=0.35
  ),
  WAxisParamEntry(
    ParamDBs.CURRENT_FEED_FWD_GAIN1, ST96=0.0, ST384=0.0, LT=0.0, AM=0.03, F96_50=0.0
  ),
  WAxisParamEntry(ParamDBs.CURRENT_FEED_FWD_GAIN2, ST96=0.0, ST384=0.0, LT=0.0, AM=0.4, F96_50=0.0),
  WAxisParamEntry(
    ParamDBs.CURRENT_FEED_FWD_GAIN3, ST96=0.0, ST384=0.0, LT=0.0, AM=0.05, F96_50=0.0
  ),
  WAxisParamEntry(
    ParamDBs.STATIONARY_VEL_PTERM, ST96=1.25, ST384=1.25, LT=1.75, AM=2.0, F96_50=1.25
  ),
  WAxisParamEntry(
    ParamDBs.STATIONARY_VEL_ITERM, ST96=0.01, ST384=0.01, LT=0.1, AM=0.01, F96_50=0.01
  ),
  WAxisParamEntry(
    ParamDBs.STATIONARY_VEL_DTERM,
    ST96=0.002,
    ST384=0.002,
    LT=0.00125,
    AM=0.00175,
    F96_50=0.002,
  ),
  WAxisParamEntry(
    ParamDBs.STATIONARY_POS_PTERM, ST96=620.0, ST384=780.0, LT=650.0, AM=450.0, F96_50=620.0
  ),
  WAxisParamEntry(
    ParamDBs.STATIONARY_POS_ITERM, ST96=75.0, ST384=75.0, LT=75.0, AM=30.0, F96_50=75.0
  ),
  WAxisParamEntry(
    ParamDBs.STATIONARY_POS_DTERM,
    ST96=0.00075,
    ST384=0.00075,
    LT=0.0025,
    AM=0.001,
    F96_50=0.00075,
  ),
  WAxisParamEntry(ParamDBs.STATIONARY_MAX_ERROR, ST96=0.0, ST384=0.0, LT=0.0, AM=0.0, F96_50=0.0),
  WAxisParamEntry(
    ParamDBs.SM_THRESHOLD,
    ST96=0.052875,
    ST384=0.0125,
    LT=0.056,
    AM=0.048125,
    F96_50=0.052875,
  ),
  WAxisParamEntry(ParamDBs.SM_VEL_PTERM, ST96=1.35, ST384=2.35, LT=1.35, AM=2.75, F96_50=1.35),
  WAxisParamEntry(ParamDBs.SM_VEL_ITERM, ST96=1.0, ST384=1.0, LT=0.1, AM=0.2, F96_50=1.0),
  WAxisParamEntry(
    ParamDBs.SM_VEL_DTERM, ST96=0.002, ST384=0.002, LT=0.0025, AM=0.002, F96_50=0.002
  ),
  WAxisParamEntry(ParamDBs.SM_POS_PTERM, ST96=750.0, ST384=750.0, LT=750.0, AM=550.0, F96_50=750.0),
  WAxisParamEntry(ParamDBs.SM_POS_ITERM, ST96=32.0, ST384=64.0, LT=1.5, AM=2.0, F96_50=32.0),
  WAxisParamEntry(
    ParamDBs.SM_STATIONARY_VEL_PTERM, ST96=1.05, ST384=2.0, LT=1.15, AM=2.15, F96_50=1.05
  ),
  WAxisParamEntry(
    ParamDBs.SM_STATIONARY_VEL_ITERM, ST96=0.0, ST384=0.01, LT=0.1, AM=0.01, F96_50=0.0
  ),
  WAxisParamEntry(
    ParamDBs.SM_STATIONARY_VEL_DTERM,
    ST96=0.002,
    ST384=0.0015,
    LT=0.003,
    AM=0.00175,
    F96_50=0.002,
  ),
  WAxisParamEntry(
    ParamDBs.SM_STATIONARY_POS_PTERM,
    ST96=650.0,
    ST384=780.0,
    LT=1500.0,
    AM=550.0,
    F96_50=750.0,
  ),
  WAxisParamEntry(
    ParamDBs.SM_STATIONARY_POS_ITERM, ST96=48.0, ST384=75.0, LT=125.0, AM=100.0, F96_50=48.0
  ),
  WAxisParamEntry(
    ParamDBs.SM_STATIONARY_POS_DTERM,
    ST96=0.001,
    ST384=0.00075,
    LT=0.00115,
    AM=0.001,
    F96_50=0.001,
  ),
  WAxisParamEntry(
    ParamDBs.SM_ACCELERATION, ST96=6.35, ST384=6.35, LT=1.68, AM=1.44375, F96_50=4.635
  ),
  WAxisParamEntry(
    ParamDBs.SM_JERK, ST96=1250.0, ST384=1250.0, LT=1171.875, AM=1250.0, F96_50=1250.0
  ),
  WAxisParamEntry(ParamDBs.SM_SPEED, ST96=6.35, ST384=6.35, LT=1.12, AM=1.44375, F96_50=4.635),
  WAxisParamEntry(
    ParamDBs.SM_SPEED_FEED_FWD_GAIN, ST96=0.1, ST384=0.36, LT=0.225, AM=0.35, F96_50=0.275
  ),
  WAxisParamEntry(
    ParamDBs.SM_CURRENT_FEED_FWD_GAIN1, ST96=0.0, ST384=0.0, LT=0.0, AM=0.0, F96_50=0.0
  ),
  WAxisParamEntry(
    ParamDBs.SM_CURRENT_FEED_FWD_GAIN2, ST96=0.0, ST384=0.0, LT=0.0, AM=0.0, F96_50=0.0
  ),
  WAxisParamEntry(
    ParamDBs.SM_CURRENT_FEED_FWD_GAIN3, ST96=0.0, ST384=0.0, LT=0.0, AM=0.0, F96_50=0.0
  ),
  WAxisParamEntry(
    ParamDBs.SM_POS_MARGIN, ST96=0.0001, ST384=0.0001, LT=0.0001, AM=0.0001, F96_50=0.0001
  ),
  WAxisParamEntry(ParamDBs.SPEED_SCALE, ST96=3.0, ST384=3.0, LT=2.0, AM=2.0, F96_50=3.0),
)

assert len(WAXIS_PARAM_TABLE) == 57, f"expected 57 W-axis params, got {len(WAXIS_PARAM_TABLE)}"

# Parameters the axis controller declares as UInt32. Every other entry in
# WAXIS_PARAM_TABLE is Float32. Keep this set narrow: writing a
# float32-reinterpreted value to a uint param lands in a completely
# different numeric range and the firmware will NAK it.
_UINT_PARAMS = frozenset({ParamDBs.I2T_TIME})


def param_set_for_head(head_type: HeadType) -> Optional[WAxisParamSet]:
  """Return the W-axis param set for a head type.

  Args:
    head_type: The head type to look up.

  Returns:
    The parameter set name, or ``None`` if the head type is unsupported --
    the caller should skip the apply entirely in that case.
  """
  return HEAD_TYPE_TO_SET.get(head_type)


def apply_waxis_parameters(
  params: ParameterAccess,
  head_type: HeadType,
  *,
  per_param_timeout: float = 5.0,
  apply_timeout: float = 10.0,
) -> bool:
  """Write every W-axis parameter for ``head_type`` and commit.

  Args:
    params: The parameter accessor for the W-axis device.
    head_type: The head type to apply parameters for.
    per_param_timeout: Maximum time to wait for each parameter write, in
      seconds.
    apply_timeout: Maximum time to wait for the commit, in seconds.

  Returns:
    True if parameters were applied, False if the head type has no mapping
    (in which case nothing is written).
  """
  param_set = param_set_for_head(head_type)
  if param_set is None:
    return False
  for entry in WAXIS_PARAM_TABLE:
    value = entry.value_for(param_set)
    if entry.param in _UINT_PARAMS:
      params.write_uint(int(entry.param), int(value), timeout=per_param_timeout)
    else:
      params.write_float(int(entry.param), value, timeout=per_param_timeout)
  params.apply(timeout=apply_timeout)
  return True
