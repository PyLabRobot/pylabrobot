"""Resource definitions for Sarstedt tubes."""

from pylabrobot.resources.tube import Tube, TubeBottomType


def sarstedt_tube_2mL_Vb(name: str) -> Tube:
  """Sarstedt screw-cap micro tube, part no. 72.609.300, without cap.

  Transparent polypropylene tube with a skirted conical base and knurling.
  The specified working volume is 2,000 uL.

  The overall height is 45 mm, the inner depth is 44 mm, and the cavity floor
  is 1 mm above the outside base of the skirt.

  Height-volume conversion uses piecewise-linear interpolation of measured
  liquid heights above the cavity floor. The calibrated range is 50-2,000 uL,
  corresponding to heights of 2.55-39.36 mm. Values outside these ranges raise
  ValueError; the model does not extrapolate to empty or brim-full conditions.

  Specification: tech_drawings/Product-Specification-Screw-cap-micro-tube-2-ml-72.609.300.pdf
  Manufacturer: https://www.sarstedt.com/en/MX/c/life-science/sample-handling/screw-cap-micro-tubes/p/screw-cap-micro-tube-2-ml/72.609.300

  Args:
    name: Name of the tube.
  """
  return Tube(
    name=name,
    size_x=10.8,  # from spec
    size_y=10.8,  # from spec
    size_z=45.0,  # measured
    max_volume=2_000,  # from spec: working volume in uL
    material_z_thickness=1.0,  # cavity floor above the outside base
    model=sarstedt_tube_2mL_Vb.__name__,
    bottom_type=TubeBottomType.V,  # from spec: conical cavity within a standing skirt
    # Liquid heights measured from the outside base with micrometers, minus the 1 mm
    # cavity-floor offset. Keys are heights above the cavity floor (mm); values are uL.
    height_volume_data={
      2.55: 50.0,
      3.12: 100.0,
      6.20: 200.0,
      8.61: 300.0,
      10.29: 400.0,
      12.35: 500.0,
      14.10: 600.0,
      15.81: 700.0,
      17.63: 800.0,
      19.62: 900.0,
      21.37: 1000.0,
      30.55: 1500.0,
      39.36: 2000.0,
    },
  )
