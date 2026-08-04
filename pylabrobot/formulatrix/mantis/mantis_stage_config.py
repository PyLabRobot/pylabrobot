"""Config-driven Mantis plate→arm transform — an EXACT reimplementation of the
vendor's ``MantisMicroplate.GetAbsolutePosition`` / ``StagePositionCalculator``.

Mantis-native (NOT PLR-plate-based). Every parameter is loaded, per machine, from
the vendor install — there is NO empirical/fitted constant here. Validated to a
byte-exact match against the vendor dispense sequence (all XY and Z motor packets identical).

The vendor chain (named for the vendor's own stage-positioning routines):

  1. well (row,col) -> plate-frame vector       (MantisMicroplate.GetWellVector)
         P = (col*pitchCol + leftTop.X, row*pitchRow + leftTop.Y, leftTop.Z)
  2. arm-type rotation + translation             (Utility.GetMatrixTransformationForSpecificType)
         P = rotateXY(P, ArmTypeRotationDegree) + ArmTypeTranslation     (identity for "Standard")
  3. add the stage origin                         (MantisMicroplate: result += Adapter.Origin)
         v = P + Origin
  4. stage-tuning correction                      (StagePositionCalculator.CalculateCorrectedPosition, MATRIX overload)
         XY = projective 3x3 MicroplateTransformationMatrix applied to v.xy
         Z  = Origin.Z + (v.Z-Origin.Z) + relX*offsetX.Z + relY*offsetY.Z   (relX,relY = v-Origin)
  the resulting arm-frame (X,Y,Z) then goes straight to the SCARA inverse
  kinematics (MantisArmEquation == our MantisKinematics.xy_to_theta).

Per-machine parameter sources:
  * Origin               <- Data/Device/Adapters/SBS Adapter.ad.txt
  * MicroplateTransformationMatrix, MicroplateOffsetX/Y, ArmType*  <- Data/Device/Configs/Device.config
  * leftTop, pitch       <- Data/System/{Plates,DefaultPlates}/<plate>.pd.txt

Works for any machine/plate: a contributor's own install files supply their values.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

_ADAPTER_REL = os.path.join("Data", "Device", "Adapters", "SBS Adapter.ad.txt")
_DEVICE_CFG_REL = os.path.join("Data", "Device", "Configs", "Device.config")
# Plate definitions ship in either of these dirs (the vendor searches both).
_PLATE_DIRS = (
  os.path.join("Data", "System", "Plates"),
  os.path.join("Data", "System", "DefaultPlates"),
)

Vec3 = Tuple[float, float, float]


def _parse_xyz(text: str) -> Vec3:
  """Parse a 'X .. Y .. Z ..' triple (order-independent)."""
  d = dict(re.findall(r"([XYZ])\s+(-?\d+\.?\d*(?:[eE][-+]?\d+)?)", text))
  return (float(d["X"]), float(d["Y"]), float(d.get("Z", 0.0)))


def _device_cfg_value(text: str, key: str) -> Optional[str]:
  m = re.search(rf'key="{re.escape(key)}"\s+value="([^"]*)"', text)
  return m.group(1) if m else None


def well_name_to_rc(name: str) -> Tuple[int, int]:
  """'A1'->(0,0), 'B2'->(1,1), 'D4'->(3,3), 'H12'->(7,11)."""
  return ord(name[0].upper()) - ord("A"), int(name[1:]) - 1


@dataclass
class StageTransform:
  """Exact vendor plate→arm transform, parameterized from the install files."""

  origin: Vec3  # SBS Adapter.ad.txt (plate->arm translation + Z base)
  matrix: List[List[float]]  # Device.config MicroplateTransformationMatrix (3x3 projective, XY)
  offset_x: Vec3  # Device.config MicroplateOffsetX (Z component supplies the Z tilt)
  offset_y: Vec3  # Device.config MicroplateOffsetY
  left_top: Vec3  # plate .pd.txt "Well 1" (A1) position, plate frame
  pitch_col: float  # plate .pd.txt column pitch (mm)
  pitch_row: float  # plate .pd.txt row pitch (mm)
  arm_rot_deg: float = 0.0  # Device.config ArmTypeRotationDegree (0 for "Standard")
  arm_translation: Vec3 = (0.0, 0.0, 0.0)  # Device.config ArmTypeTranslation
  plate_name: str = ""

  @classmethod
  def from_install(cls, install_root: str, plate: str = "PT3-96-Assay") -> "StageTransform":
    # --- stage origin (per machine) ---
    with open(os.path.join(install_root, _ADAPTER_REL), encoding="utf-8-sig") as fh:
      origin = _parse_xyz(fh.read())

    # --- Device.config: matrix, offsets, arm-type framing (per machine) ---
    with open(os.path.join(install_root, _DEVICE_CFG_REL), encoding="utf-8-sig") as fh:
      dev = fh.read()
    matrix = json.loads(_device_cfg_value(dev, "MicroplateTransformationMatrix"))
    offset_x = _parse_xyz(_device_cfg_value(dev, "MicroplateOffsetX"))
    offset_y = _parse_xyz(_device_cfg_value(dev, "MicroplateOffsetY"))
    arm_type = _device_cfg_value(dev, "ArmType")
    if arm_type:  # "Standard" -> rotation/translation apply (both 0 by default = identity)
      arm_rot_deg = float(_device_cfg_value(dev, "ArmTypeRotationDegree") or 0.0)
      arm_translation = _parse_xyz(_device_cfg_value(dev, "ArmTypeTranslation") or "X 0 Y 0 Z 0")
    else:
      arm_rot_deg, arm_translation = 0.0, (0.0, 0.0, 0.0)

    # --- plate definition (.pd.txt): leftTop + pitch ---
    left_top, pitch_col, pitch_row = cls._load_plate(install_root, plate)
    return cls(
      origin=origin,
      matrix=matrix,
      offset_x=offset_x,
      offset_y=offset_y,
      left_top=left_top,
      pitch_col=pitch_col,
      pitch_row=pitch_row,
      arm_rot_deg=arm_rot_deg,
      arm_translation=arm_translation,
      plate_name=plate,
    )

  @staticmethod
  def _load_plate(install_root: str, plate: str) -> Tuple[Vec3, float, float]:
    path = None
    for d in _PLATE_DIRS:
      cand = os.path.join(install_root, d, plate + ".pd.txt")
      if os.path.exists(cand):
        path = cand
        break
    if path is None:
      raise FileNotFoundError(f"plate definition {plate!r}.pd.txt not found under {_PLATE_DIRS}")
    with open(path, encoding="utf-8-sig") as fh:
      text = fh.read()
    # header: "<name> <rows> <cols> <pitchCol> <pitchRow> <height> <adapter> ..."
    # the name may itself be one whitespace-free token (spaces encoded as %32).
    hdr = next(
      ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith(("[", "Well"))
    ).split()
    pitch_col, pitch_row = float(hdr[3]), float(hdr[4])
    wm = re.search(r"Well\s+1\s+(X\s+-?\d+\.?\d*\s+Y\s+-?\d+\.?\d*\s+Z\s+-?\d+\.?\d*)", text)
    left_top = _parse_xyz(wm.group(1))
    return left_top, pitch_col, pitch_row

  # -- geometry --

  def _rotate_translate(self, p: Vec3) -> Vec3:
    """Arm-type rotation about origin in XY + translation (vendor step 2)."""
    rad = math.radians(self.arm_rot_deg)
    c, s = math.cos(rad), math.sin(rad)
    x = p[0] * c - p[1] * s + self.arm_translation[0]
    y = p[0] * s + p[1] * c + self.arm_translation[1]
    return (x, y, p[2] + self.arm_translation[2])

  def well_arm_xyz(self, row: int, col: int) -> Vec3:
    """Exact arm-frame (X, Y, Z) in mm for 0-based (row, col). row 0=A, col 0=column 1."""
    ox, oy, oz = self.origin
    # 1. plate-frame well vector
    p = (
      col * self.pitch_col + self.left_top[0],
      row * self.pitch_row + self.left_top[1],
      self.left_top[2],
    )
    # 2. arm-type framing (identity for "Standard")
    p = self._rotate_translate(p)
    # 3. add stage origin
    vx, vy, vz = p[0] + ox, p[1] + oy, p[2] + oz
    # 4a. XY via projective 3x3 matrix
    m = self.matrix
    tx = m[0][0] * vx + m[0][1] * vy + m[0][2]
    ty = m[1][0] * vx + m[1][1] * vy + m[1][2]
    tw = m[2][0] * vx + m[2][1] * vy + m[2][2]
    arm_x, arm_y = tx / tw, ty / tw
    # 4b. Z via affine offset (the per-well tilt); relX,relY = v - origin = plate vector
    rel_x, rel_y, rel_z = vx - ox, vy - oy, vz - oz
    arm_z = oz + rel_z + rel_x * self.offset_x[2] + rel_y * self.offset_y[2]
    return (arm_x, arm_y, arm_z)

  def well_arm_xyz_name(self, name: str) -> Vec3:
    row, col = well_name_to_rc(name)
    return self.well_arm_xyz(row, col)
