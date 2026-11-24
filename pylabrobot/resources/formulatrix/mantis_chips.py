"""Formulatrix Mantis chips and their nozzles: the classes and one definition per part.

Specifications: https://help.formulatrix.com/mantis/5.5/Content/Chip_Overview.htm
"""

from typing import Any, Dict, Literal, Optional, Tuple

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource

# How liquid reaches a chip's input: a tube, or a pipette tip.
MantisChipInput = Literal["tube", "pipette_tip"]


class MantisChipNozzle(Resource):
  """A nozzle fitted to a Mantis chip, below its body; it sets the drop's exit diameter."""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    *,
    inner_diameter: float,
    color: str,
    material: Optional[Literal["silicone", "pfe"]] = None,
    category: Optional[str] = "mantis_chip_nozzle",
    model: Optional[str] = None,
  ):
    """
    Args:
      name: the resource name.
      size_x: footprint x, mm.
      size_y: footprint y, mm.
      size_z: height, mm.
      inner_diameter: the bore's diameter, mm.
      color: the nozzle's color, as sold.
      material: "silicone" or "pfe"; None when not given.
      category: the resource category.
      model: the model name; each definition gives its own.
    """
    super().__init__(
      name=name, size_x=size_x, size_y=size_y, size_z=size_z, category=category, model=model
    )
    self.inner_diameter = inner_diameter
    self.color = color
    self.material = material

  def __eq__(self, other: object) -> bool:
    """Compare resource fields and the nozzle's bore, color and material."""
    return (
      isinstance(other, MantisChipNozzle)
      and super().__eq__(other)
      and self.inner_diameter == other.inner_diameter
      and self.color == other.color
      and self.material == other.material
    )

  def serialize(self) -> dict:
    """Serialize the resource fields and the nozzle's bore, color and material."""
    return {
      **super().serialize(),
      "inner_diameter": self.inner_diameter,
      "color": self.color,
      "material": self.material,
    }


class MantisChip(Resource):
  """A Mantis chip, of one chip type.

  Volumes in uL, lengths in mm. Its nozzle, when fitted, is a child below its body
  (``nozzle``). ``rfid_tag`` is this chip's tag once read, None before.
  """

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    *,
    chip_type: str,
    dispense_type: Literal["drops", "continuous_flow"],
    diaphragm_volumes: Tuple[float, ...],
    minimum_volume: float,
    hold_up_volume: float,
    maximum_pulses_per_second: Dict[float, float],
    fluid_channel_diameter: float,
    default_prime_volume: Dict[MantisChipInput, float],
    default_predispense_volume: Dict[MantisChipInput, float],
    category: Optional[str] = "mantis_chip",
    model: Optional[str] = None,
  ):
    """
    Args:
      name: the resource name.
      size_x: footprint x, mm.
      size_y: footprint y, mm.
      size_z: the body's height, without the nozzle, mm.
      chip_type: the chip type, as the RFID reader names it.
      dispense_type: "drops" (a diaphragm pulse per drop) or "continuous_flow".
      diaphragm_volumes: the volume of each drop size, uL; empty for a continuous flow.
      minimum_volume: the smallest volume it dispenses, uL.
      hold_up_volume: the liquid the chip holds, uL.
      maximum_pulses_per_second: the highest pulse rate, by diaphragm volume.
      fluid_channel_diameter: the fluid channel's diameter at its narrowest, mm.
      default_prime_volume: the volume to prime, uL, by input.
      default_predispense_volume: the volume to predispense, uL, by input.
      category: the resource category.
      model: the model name; each definition gives its own.
    """
    super().__init__(
      name=name, size_x=size_x, size_y=size_y, size_z=size_z, category=category, model=model
    )
    self.chip_type = chip_type
    self.dispense_type = dispense_type
    self.diaphragm_volumes = tuple(diaphragm_volumes)
    self.minimum_volume = minimum_volume
    self.hold_up_volume = hold_up_volume
    # Keys as floats again after a JSON round trip, which writes them as strings.
    self.maximum_pulses_per_second = {
      float(volume): rate for volume, rate in maximum_pulses_per_second.items()
    }
    self.fluid_channel_diameter = fluid_channel_diameter
    self.default_prime_volume = default_prime_volume
    self.default_predispense_volume = default_predispense_volume
    self.rfid_tag: Optional[Dict[str, Any]] = None

  @property
  def nozzle(self) -> Optional[MantisChipNozzle]:
    """The nozzle fitted to this chip; None without one."""
    for child in self.children:
      if isinstance(child, MantisChipNozzle):
        return child
    return None

  def __eq__(self, other: object) -> bool:
    """Compare resource fields and the chip type's data; ``rfid_tag`` is state, not compared."""
    return (
      isinstance(other, MantisChip)
      and super().__eq__(other)
      and self.chip_type == other.chip_type
      and self.dispense_type == other.dispense_type
      and self.diaphragm_volumes == other.diaphragm_volumes
      and self.minimum_volume == other.minimum_volume
      and self.hold_up_volume == other.hold_up_volume
      and self.maximum_pulses_per_second == other.maximum_pulses_per_second
      and self.fluid_channel_diameter == other.fluid_channel_diameter
      and self.default_prime_volume == other.default_prime_volume
      and self.default_predispense_volume == other.default_predispense_volume
    )

  def serialize(self) -> dict:
    """Serialize the resource fields and the chip type's data."""
    return {
      **super().serialize(),
      "chip_type": self.chip_type,
      "dispense_type": self.dispense_type,
      "diaphragm_volumes": list(self.diaphragm_volumes),
      "minimum_volume": self.minimum_volume,
      "hold_up_volume": self.hold_up_volume,
      "maximum_pulses_per_second": dict(self.maximum_pulses_per_second),
      "fluid_channel_diameter": self.fluid_channel_diameter,
      "default_prime_volume": dict(self.default_prime_volume),
      "default_predispense_volume": dict(self.default_predispense_volume),
    }

  def serialize_state(self) -> Dict[str, Any]:
    """Serialize the resource state and the chip's RFID tag."""
    return {**super().serialize_state(), "rfid_tag": self.rfid_tag}

  def load_state(self, state: Dict[str, Any]) -> None:
    """Load the resource state and the chip's RFID tag."""
    super().load_state(state)
    self.rfid_tag = state.get("rfid_tag")


# A diaphragm chip's footprint and its body's height, mm; its nozzle hangs 2 mm below.
MANTIS_CHIP_SIZE_X = 16.0  # measured
MANTIS_CHIP_SIZE_Y = 34.0  # measured
MANTIS_CHIP_SIZE_Z = 5.0  # measured
# A nozzle's footprint and height, mm, and where it sits on the chip, in the chip's frame.
# TODO: measure the footprint and the position; only the 2 mm height is measured.
MANTIS_CHIP_NOZZLE_SIZE_X = 4.0  # estimated
MANTIS_CHIP_NOZZLE_SIZE_Y = 4.0  # estimated
MANTIS_CHIP_NOZZLE_SIZE_Z = 2.0  # measured
MANTIS_CHIP_NOZZLE_LOCATION = Coordinate(
  x=(MANTIS_CHIP_SIZE_X - MANTIS_CHIP_NOZZLE_SIZE_X) / 2,  # estimated: centred
  y=(MANTIS_CHIP_SIZE_Y - MANTIS_CHIP_NOZZLE_SIZE_Y) / 2,  # estimated: centred
  z=-MANTIS_CHIP_NOZZLE_SIZE_Z,
)


def _mantis_chip_nozzle(
  name: str,
  *,
  inner_diameter: float,
  color: str,
  material: Optional[Literal["silicone", "pfe"]],
  model: str,
) -> MantisChipNozzle:
  """A nozzle of the shared size."""
  return MantisChipNozzle(
    name=name,
    size_x=MANTIS_CHIP_NOZZLE_SIZE_X,
    size_y=MANTIS_CHIP_NOZZLE_SIZE_Y,
    size_z=MANTIS_CHIP_NOZZLE_SIZE_Z,
    inner_diameter=inner_diameter,
    color=color,
    material=material,
    model=model,
  )


def mantis_chip_nozzle_silicone_0_1mm(name: str) -> MantisChipNozzle:
  """Formulatrix Mantis chip nozzle, silicone, 0.1 mm, red.

  Part no.: 232994
  """
  return _mantis_chip_nozzle(
    name=name,
    inner_diameter=0.1,  # from spec
    color="red",  # from spec
    material="silicone",
    model="mantis_chip_nozzle_silicone_0_1mm",
  )


def mantis_chip_nozzle_silicone_0_2mm(name: str) -> MantisChipNozzle:
  """Formulatrix Mantis chip nozzle, silicone, 0.2 mm, blue.

  Part no.: 232995
  The Low Volume chip's standard nozzle.
  """
  return _mantis_chip_nozzle(
    name=name,
    inner_diameter=0.2,  # from spec
    color="blue",  # from spec
    material="silicone",
    model="mantis_chip_nozzle_silicone_0_2mm",
  )


def mantis_chip_nozzle_silicone_0_3mm(name: str) -> MantisChipNozzle:
  """Formulatrix Mantis chip nozzle, silicone, 0.3 mm, yellow.

  Part no.: 232996
  """
  return _mantis_chip_nozzle(
    name=name,
    inner_diameter=0.3,  # from spec
    color="yellow",  # from spec
    material="silicone",
    model="mantis_chip_nozzle_silicone_0_3mm",
  )


def mantis_chip_nozzle_silicone_0_4mm(name: str) -> MantisChipNozzle:
  """Formulatrix Mantis chip nozzle, silicone, 0.4 mm, peach.

  Part no.: 232997
  Recommended for the High Viscous Continuous Flow chip.
  """
  return _mantis_chip_nozzle(
    name=name,
    inner_diameter=0.4,  # from spec
    color="peach",  # from spec
    material="silicone",
    model="mantis_chip_nozzle_silicone_0_4mm",
  )


def mantis_chip_nozzle_silicone_0_5mm(name: str) -> MantisChipNozzle:
  """Formulatrix Mantis chip nozzle, silicone, 0.5 mm, green.

  Part no.: 206237
  The High Volume chip's standard nozzle; recommended for the High Viscous
  Continuous Flow chip.
  """
  return _mantis_chip_nozzle(
    name=name,
    inner_diameter=0.5,  # from spec
    color="green",  # from spec
    material="silicone",
    model="mantis_chip_nozzle_silicone_0_5mm",
  )


def mantis_chip_nozzle_silicone_0_6mm(name: str) -> MantisChipNozzle:
  """Formulatrix Mantis chip nozzle, silicone, 0.6 mm, maroon.

  Part no.: 232998
  Recommended for the High Viscous Continuous Flow chip.
  """
  return _mantis_chip_nozzle(
    name=name,
    inner_diameter=0.6,  # from spec
    color="maroon",  # from spec
    material="silicone",
    model="mantis_chip_nozzle_silicone_0_6mm",
  )


def mantis_chip_nozzle_silicone_1_0mm(name: str) -> MantisChipNozzle:
  """Formulatrix Mantis chip nozzle, silicone, 1 mm, translucent.

  Part no.: 819023A
  Recommended for the High Viscous Continuous Flow chip.
  """
  return _mantis_chip_nozzle(
    name=name,
    inner_diameter=1.0,  # from spec
    color="translucent",  # from spec
    material="silicone",
    model="mantis_chip_nozzle_silicone_1_0mm",
  )


def mantis_chip_nozzle_pfe_0_15mm(name: str) -> MantisChipNozzle:
  """Formulatrix Mantis chip nozzle, PFE, 0.15 mm, dark brown.

  Part no.: 232289
  The Low Volume PFE and Continuous Flow PFE chips' standard nozzle.
  """
  return _mantis_chip_nozzle(
    name=name,
    inner_diameter=0.15,  # from spec
    color="dark brown",  # from spec
    material="pfe",
    model="mantis_chip_nozzle_pfe_0_15mm",
  )


def mantis_chip_nozzle_pfe_0_3mm(name: str) -> MantisChipNozzle:
  """Formulatrix Mantis chip nozzle, PFE, 0.3 mm, dark brown.

  Part no.: 232518
  The High Volume PFE chip's standard nozzle.
  """
  return _mantis_chip_nozzle(
    name=name,
    inner_diameter=0.3,  # from spec
    color="dark brown",  # from spec
    material="pfe",
    model="mantis_chip_nozzle_pfe_0_3mm",
  )


def mantis_chip_nozzle_0_8mm(name: str) -> MantisChipNozzle:
  """Formulatrix Mantis chip nozzle, 0.8 mm, brown.

  Part no.: 836771A
  For the XL Volume PFE chip; its material is not given.
  """
  return _mantis_chip_nozzle(
    name=name,
    inner_diameter=0.8,  # from spec
    color="brown",  # from spec
    material=None,
    model="mantis_chip_nozzle_0_8mm",
  )


def mantis_chip_low_volume(name: str) -> MantisChip:
  """Formulatrix Mantis Silicone Low Volume (LV) chip, with RFID.

  Part no.: 233581 (pack of 6), 233649 (pack of 12)
  Fitted with its standard nozzle, ``mantis_chip_nozzle_silicone_0_2mm``.
  Drops of 0.1 and 0.5 uL. Accuracy error 10% (both drop sizes), CV <3%, rated for 1,000,000
  cycles.
  """
  chip = MantisChip(
    name=name,
    size_x=MANTIS_CHIP_SIZE_X,
    size_y=MANTIS_CHIP_SIZE_Y,
    size_z=MANTIS_CHIP_SIZE_Z,
    chip_type="low_volume",
    dispense_type="drops",
    diaphragm_volumes=(0.1, 0.5),  # from spec
    minimum_volume=0.1,  # from spec
    hold_up_volume=60.0,  # from spec
    maximum_pulses_per_second={0.1: 10.0, 0.5: 7.0},  # from spec
    fluid_channel_diameter=0.25,  # from spec
    default_prime_volume={"tube": 100.4, "pipette_tip": 6.0},  # from spec
    default_predispense_volume={"tube": 0.6, "pipette_tip": 0.6},  # from spec
    model="mantis_chip_low_volume",
  )
  chip.assign_child_resource(
    resource=mantis_chip_nozzle_silicone_0_2mm(name=f"{name}_nozzle"),
    location=MANTIS_CHIP_NOZZLE_LOCATION,
  )
  return chip


def mantis_chip_high_volume(name: str) -> MantisChip:
  """Formulatrix Mantis Silicone High Volume (HV) chip, with RFID.

  Part no.: 233580 (pack of 6)
  Fitted with its standard nozzle, ``mantis_chip_nozzle_silicone_0_5mm``.
  Drops of 1 and 5 uL. Accuracy error 10% (both drop sizes), CV <3%, rated for 1,000,000 cycles.
  """
  chip = MantisChip(
    name=name,
    size_x=MANTIS_CHIP_SIZE_X,
    size_y=MANTIS_CHIP_SIZE_Y,
    size_z=MANTIS_CHIP_SIZE_Z,
    chip_type="high_volume",
    dispense_type="drops",
    diaphragm_volumes=(1.0, 5.0),  # from spec
    minimum_volume=1.0,  # from spec
    hold_up_volume=200.0,  # from spec
    maximum_pulses_per_second={1.0: 10.0, 5.0: 4.0},  # from spec
    fluid_channel_diameter=0.38,  # from spec
    default_prime_volume={"tube": 264.0, "pipette_tip": 16.0},  # from spec
    default_predispense_volume={"tube": 6.0, "pipette_tip": 6.0},  # from spec
    model="mantis_chip_high_volume",
  )
  chip.assign_child_resource(
    resource=mantis_chip_nozzle_silicone_0_5mm(name=f"{name}_nozzle"),
    location=MANTIS_CHIP_NOZZLE_LOCATION,
  )
  return chip


def mantis_chip_low_volume_hi_fi(name: str) -> MantisChip:
  """Formulatrix Mantis Silicone Low Volume (LV) Hi-Fi chip, with RFID.

  Part no.: 809239A (pack of 6)
  Drops of 0.1 and 0.5 uL. Accuracy error 5% (both drop sizes), CV <2%, rated for 1,000,000
  cycles.
  """
  return MantisChip(
    name=name,
    size_x=MANTIS_CHIP_SIZE_X,
    size_y=MANTIS_CHIP_SIZE_Y,
    size_z=MANTIS_CHIP_SIZE_Z,
    chip_type="low_volume_hi_fi",
    dispense_type="drops",
    diaphragm_volumes=(0.1, 0.5),  # from spec
    minimum_volume=0.1,  # from spec
    hold_up_volume=60.0,  # from spec
    maximum_pulses_per_second={0.1: 10.0, 0.5: 7.0},  # from spec
    fluid_channel_diameter=0.25,  # from spec, the Low Volume chips'
    default_prime_volume={"tube": 100.4, "pipette_tip": 6.0},  # from spec
    default_predispense_volume={"tube": 0.6, "pipette_tip": 0.6},  # from spec
    model="mantis_chip_low_volume_hi_fi",
  )


def mantis_chip_high_volume_hi_fi(name: str) -> MantisChip:
  """Formulatrix Mantis Silicone High Volume (HV) Hi-Fi chip, with RFID.

  Part no.: 806984A (pack of 6)
  Drops of 1 and 5 uL. Accuracy error 5% (both drop sizes), CV <2%, rated for 1,000,000 cycles.
  """
  return MantisChip(
    name=name,
    size_x=MANTIS_CHIP_SIZE_X,
    size_y=MANTIS_CHIP_SIZE_Y,
    size_z=MANTIS_CHIP_SIZE_Z,
    chip_type="high_volume_hi_fi",
    dispense_type="drops",
    diaphragm_volumes=(1.0, 5.0),  # from spec
    minimum_volume=1.0,  # from spec
    hold_up_volume=200.0,  # from spec
    maximum_pulses_per_second={1.0: 10.0, 5.0: 4.0},  # from spec
    fluid_channel_diameter=0.38,  # from spec, the High Volume chips'
    default_prime_volume={"tube": 264.0, "pipette_tip": 16.0},  # from spec
    default_predispense_volume={"tube": 6.0, "pipette_tip": 6.0},  # from spec
    model="mantis_chip_high_volume_hi_fi",
  )
