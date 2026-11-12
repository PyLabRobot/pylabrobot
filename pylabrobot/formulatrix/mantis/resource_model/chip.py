"""The Mantis's chips: one resource factory per chip type, each chip carrying its type's identity.

A chip's identity is what the device and the install know it by: the code its RFID tag
carries, its folder of sequences in the install's ``System/Chips``, and whether it dispenses in
drops or in a continuous flow (``Device/Others/MicrofluidicChips.txt``).
"""

from typing import Callable, Dict, Literal, Optional

from pylabrobot.resources.resource import Resource

DispenseType = Literal["standard", "continuous_flow"]
# A chip's size, in mm: its footprint and its body with the nozzle below it. Measured on a chip,
# not taken from the manufacturer's model; the resource model draws it, nothing moves by it.
CHIP_SIZE_X, CHIP_SIZE_Y, CHIP_SIZE_Z = 16.0, 34.0, 7.0


class MantisChip(Resource):
  """A chip, of one chip type: the identity of that type, nothing about this chip's state."""

  def __init__(
    self,
    name: str,
    *,
    chip_type: str,
    rfid_chip_type_code: int,
    sequence_folder: str,
    dispense_type: DispenseType,
    size_x: float = CHIP_SIZE_X,
    size_y: float = CHIP_SIZE_Y,
    size_z: float = CHIP_SIZE_Z,
    category: str = "mantis_chip",
    model: Optional[str] = None,
  ):
    """
    Args:
      name: the resource name.
      chip_type: the chip type, a key of ``MANTIS_CHIP_FACTORIES``.
      rfid_chip_type_code: the code its RFID tag carries.
      sequence_folder: its folder in the install's ``System/Chips``.
      dispense_type: "standard" (drops) or "continuous_flow".
      size_x: footprint x, mm.
      size_y: footprint y, mm.
      size_z: height, mm.
      category: the resource category.
      model: the model name; each factory gives its own.
    """
    super().__init__(
      name=name, size_x=size_x, size_y=size_y, size_z=size_z, category=category, model=model
    )
    self.chip_type = chip_type
    self.rfid_chip_type_code = rfid_chip_type_code
    self.sequence_folder = sequence_folder
    self.dispense_type = dispense_type

  def serialize(self) -> dict:
    """The resource's fields and the chip type's identity."""
    return {
      **super().serialize(),
      "chip_type": self.chip_type,
      "rfid_chip_type_code": self.rfid_chip_type_code,
      "sequence_folder": self.sequence_folder,
      "dispense_type": self.dispense_type,
    }


def mantis_high_volume_chip(name: str) -> MantisChip:
  """High Volume chip: drops, RFID code 0x00."""
  return MantisChip(
    name=name,
    chip_type="high_volume",
    rfid_chip_type_code=0x00,
    sequence_folder="HighVolume",
    dispense_type="standard",
    model="mantis_high_volume_chip",
  )


def mantis_low_volume_chip(name: str) -> MantisChip:
  """Low Volume chip: drops, RFID code 0x01."""
  return MantisChip(
    name=name,
    chip_type="low_volume",
    rfid_chip_type_code=0x01,
    sequence_folder="LowVolume",
    dispense_type="standard",
    model="mantis_low_volume_chip",
  )


def mantis_high_volume_pfe_chip(name: str) -> MantisChip:
  """High Volume PFE chip: drops, RFID code 0x02."""
  return MantisChip(
    name=name,
    chip_type="high_volume_pfe",
    rfid_chip_type_code=0x02,
    sequence_folder="HighVolumePFE",
    dispense_type="standard",
    model="mantis_high_volume_pfe_chip",
  )


def mantis_low_volume_pfe_chip(name: str) -> MantisChip:
  """Low Volume PFE chip: drops, RFID code 0x03."""
  return MantisChip(
    name=name,
    chip_type="low_volume_pfe",
    rfid_chip_type_code=0x03,
    sequence_folder="LowVolumePFE",
    dispense_type="standard",
    model="mantis_low_volume_pfe_chip",
  )


def mantis_high_volume_3pfe_chip(name: str) -> MantisChip:
  """High Volume 3PFE chip: drops, RFID code 0x04."""
  return MantisChip(
    name=name,
    chip_type="high_volume_3pfe",
    rfid_chip_type_code=0x04,
    sequence_folder="HighVolume3PFE",
    dispense_type="standard",
    model="mantis_high_volume_3pfe_chip",
  )


def mantis_low_volume_3pfe_chip(name: str) -> MantisChip:
  """Low Volume 3PFE chip: drops, RFID code 0x05."""
  return MantisChip(
    name=name,
    chip_type="low_volume_3pfe",
    rfid_chip_type_code=0x05,
    sequence_folder="LowVolume3PFE",
    dispense_type="standard",
    model="mantis_low_volume_3pfe_chip",
  )


def mantis_hv_continuous_flow_chip(name: str) -> MantisChip:
  """Continuous Flow chip: a continuous flow, RFID code 0x06."""
  return MantisChip(
    name=name,
    chip_type="hv_continuous_flow",
    rfid_chip_type_code=0x06,
    sequence_folder="HVContinuousFlow",
    dispense_type="continuous_flow",
    model="mantis_hv_continuous_flow_chip",
  )


def mantis_high_volume_hi_fi_chip(name: str) -> MantisChip:
  """High Volume Hi-Fi chip: drops, RFID code 0x07, seen on a tag."""
  return MantisChip(
    name=name,
    chip_type="high_volume_hi_fi",
    rfid_chip_type_code=0x07,
    sequence_folder="HighVolumeHi-Fi",
    dispense_type="standard",
    model="mantis_high_volume_hi_fi_chip",
  )


def mantis_low_volume_hi_fi_chip(name: str) -> MantisChip:
  """Low Volume Hi-Fi chip: drops, RFID code 0x08, seen on a tag."""
  return MantisChip(
    name=name,
    chip_type="low_volume_hi_fi",
    rfid_chip_type_code=0x08,
    sequence_folder="LowVolumeHi-Fi",
    dispense_type="standard",
    model="mantis_low_volume_hi_fi_chip",
  )


def mantis_high_viscosity_continuous_flow_chip(name: str) -> MantisChip:
  """High Viscosity Continuous Flow chip: a continuous flow, RFID code 0x09."""
  return MantisChip(
    name=name,
    chip_type="high_viscosity_continuous_flow",
    rfid_chip_type_code=0x09,
    sequence_folder="HighViscosityContinuousFlow",
    dispense_type="continuous_flow",
    model="mantis_high_viscosity_continuous_flow_chip",
  )


def mantis_xl_volume_pfe_chip(name: str) -> MantisChip:
  """XL Volume PFE chip: drops, RFID code 0x0A."""
  return MantisChip(
    name=name,
    chip_type="xl_volume_pfe",
    rfid_chip_type_code=0x0A,
    sequence_folder="XLVolumePFE",
    dispense_type="standard",
    model="mantis_xl_volume_pfe_chip",
  )


# Each chip type's factory, by chip type.
MANTIS_CHIP_FACTORIES: Dict[str, Callable[..., MantisChip]] = {
  "high_volume": mantis_high_volume_chip,
  "low_volume": mantis_low_volume_chip,
  "high_volume_pfe": mantis_high_volume_pfe_chip,
  "low_volume_pfe": mantis_low_volume_pfe_chip,
  "high_volume_3pfe": mantis_high_volume_3pfe_chip,
  "low_volume_3pfe": mantis_low_volume_3pfe_chip,
  "hv_continuous_flow": mantis_hv_continuous_flow_chip,
  "high_volume_hi_fi": mantis_high_volume_hi_fi_chip,
  "low_volume_hi_fi": mantis_low_volume_hi_fi_chip,
  "high_viscosity_continuous_flow": mantis_high_viscosity_continuous_flow_chip,
  "xl_volume_pfe": mantis_xl_volume_pfe_chip,
}
# The chip type each tag code names, from the factories. Only 0x07 and 0x08 have been read off a
# tag; the other codes are not confirmed.
RFID_CHIP_TYPE_CODES: Dict[int, str] = {
  factory(name=chip_type).rfid_chip_type_code: chip_type
  for chip_type, factory in MANTIS_CHIP_FACTORIES.items()
}


def get_chip_factory(*, chip_type: str) -> Callable[..., MantisChip]:
  """The factory of a chip type.

  Args:
    chip_type: a key of ``MANTIS_CHIP_FACTORIES``.

  Raises:
    ValueError: if ``chip_type`` is not one.
  """
  if chip_type not in MANTIS_CHIP_FACTORIES:
    raise ValueError(f"chip_type must be one of {sorted(MANTIS_CHIP_FACTORIES)}, got {chip_type!r}")
  return MANTIS_CHIP_FACTORIES[chip_type]
