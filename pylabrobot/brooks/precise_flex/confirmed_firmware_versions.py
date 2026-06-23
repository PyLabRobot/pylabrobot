"""PreciseFlex software stacks confirmed with this driver.

This tracks the digital setup only - the GPL firmware, the TCS app, and the loaded
TCS modules - not the physical configuration (links, rail, gripper), which is
discovered into ``PreciseFlexConfiguration``. At setup the driver checks the
discovered stack here:

- ``SUPPORTED_ROBOT_TYPES`` gates the client-side kinematics. This driver's FK/IK
  is the PreciseFlex 400 two-link SCARA geometry, so other models would get
  silently-wrong joint targets and are flagged.
- ``CONFIRMED_FIRMWARE_VERSIONS`` is the set of full (model, GPL, TCS, module-set)
  software stacks validated against this driver; an unlisted one logs a warning
  asking for a report so it can be added.

(Which modules the driver *requires* lives in ``tcs_modules.py``, a separate concern.)

Version strings keep the name and version but drop the trailing build date, which
varies by build without changing behaviour.
"""

import re
from dataclasses import dataclass

# robot_type (DataID 116) -> model name, for the models whose kinematics this driver implements.
SUPPORTED_ROBOT_TYPES = {
  12: "PreciseFlex 400",
}


@dataclass(frozen=True)
class ConfirmedFirmware:
  """A full software stack validated against this driver (build dates stripped)."""

  robot_type: int
  gpl_version: str  # e.g. "GPL 5.1D4"
  tcs_version: str  # e.g. "TCP Command Server 3.0D4"
  modules: tuple  # one "name version" per loaded TCS module


CONFIRMED_FIRMWARE_VERSIONS = frozenset(
  [
    ConfirmedFirmware(
      robot_type=12,
      gpl_version="GPL 5.1D4",
      tcs_version="TCP Command Server 3.0D4",
      modules=(
        "IntelliGuide 1.0",
        "Load-Save Module 3.0B2",
        "PARobot Auto Center Module 3.0D3",
        "PARobot Module 3.0D4",
        "SSGrip Module 3.0D4",
      ),
    ),
  ]
)

# Trailing build date (e.g. "10-25-2024" or "Apr 25 2025") and anything after it.
_DATE = re.compile(r",?\s*(\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}).*$")


def strip_build_date(version: str) -> str:
  """Reduce a version string to its name + version, dropping the build date."""
  return _DATE.sub("", version).strip().rstrip(",").strip()


def is_supported_model(robot_type: int) -> bool:
  """Whether this driver's kinematics cover the given robot_type (DataID 116)."""
  return robot_type in SUPPORTED_ROBOT_TYPES


def is_confirmed(robot_type: int, gpl_version: str, tcs_version: str, modules: tuple) -> bool:
  """Whether the full (model, GPL, TCS, module-set) combination has been validated."""
  target = (
    robot_type,
    strip_build_date(gpl_version),
    strip_build_date(tcs_version),
    tuple(sorted(strip_build_date(m) for m in modules)),
  )
  return any(
    (
      c.robot_type,
      strip_build_date(c.gpl_version),
      strip_build_date(c.tcs_version),
      tuple(sorted(strip_build_date(m) for m in c.modules)),
    )
    == target
    for c in CONFIRMED_FIRMWARE_VERSIONS
  )


def suggest_entry(robot_type: int, gpl_version: str, tcs_version: str, modules: tuple) -> str:
  """Format a discovered stack as a ``ConfirmedFirmware(...)`` literal to paste into
  ``CONFIRMED_FIRMWARE_VERSIONS`` (build dates stripped, modules sorted)."""
  module_lines = ",\n      ".join(f'"{m}"' for m in sorted(strip_build_date(m) for m in modules))
  return (
    "  ConfirmedFirmware(\n"
    f"    robot_type={robot_type},\n"
    f'    gpl_version="{strip_build_date(gpl_version)}",\n'
    f'    tcs_version="{strip_build_date(tcs_version)}",\n'
    "    modules=(\n"
    f"      {module_lines},\n"
    "    ),\n"
    "  ),"
  )
