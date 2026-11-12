"""Resource definitions for Sarstedt tubes."""

from pylabrobot.resources.tube import Tube, TubeBottomType


def sarstedt_tube_2mL_Vb(name: str) -> Tube:
  """Sarstedt screw-cap micro tube, part no. 72.609.300, without cap.

  Transparent polypropylene tube with a skirted conical base and knurling.
  The specified working volume is 2,000 uL.

  The overall height is measured as 45 mm. The contributor identifies the
  specification's 44 mm "Length of product" as the inner depth, giving a 1 mm
  offset from the skirt bottom to the cavity floor.

  Height-volume conversion and automatic surface following are not supported.

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
    material_z_thickness=1.0,  # derived: 45 mm overall height - 44 mm clarified inner depth
    model=sarstedt_tube_2mL_Vb.__name__,
    bottom_type=TubeBottomType.V,  # from spec: conical cavity within a standing skirt
  )
