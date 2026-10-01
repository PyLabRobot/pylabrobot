"""Hamilton PCR plate adapters."""

from pylabrobot.resources.plate_adapter import PlateAdapter


def Hamilton_96_adapter_182531(name: str) -> PlateAdapter:
  """Hamilton 182531 PCR insert for the 182070 landscape carrier.

  Geometry is derived from the five ``182531_RNO`` meshes in Hamilton's 3D model.
  The 12 x 8 hole grid has 9 mm spacing; H1 is 5.5/6.0 mm from the left/front edges.
  The mesh's top opening diameter is approximately 7.732 mm, and its cavity bottom
  is 1.951 mm above the insert base, giving a 13.049 mm cavity depth.

  ``dz`` represents the modeled cavity bottom. Automatic plate placement uses that
  reference; it does not model contact between a particular PCR plate and the tapered
  cavities. Supply an explicit plate location if its seating height differs.

  Args:
    name: The insert name.
  """
  hole_diameter = 7.732  # from the .x mesh's top opening vertices, rounded to 0.001 mm
  return PlateAdapter(
    name=name,
    size_x=110.0,  # from the .x mesh bounds
    size_y=75.0,  # from the .x mesh bounds
    size_z=15.0,  # from the .x mesh: 108.7 - 93.7
    dx=5.5 - hole_diameter / 2,  # from the .x hole centers relative to the insert's left edge
    dy=6.0 - hole_diameter / 2,  # from the .x hole centers relative to the insert's front edge
    dz=1.951,  # from the .x mesh: cavity bottom 95.651 - insert base 93.7
    adapter_hole_size_x=hole_diameter,
    adapter_hole_size_y=hole_diameter,
    adapter_hole_dx=9.0,  # from the .x hole grid
    adapter_hole_dy=9.0,  # from the .x hole grid
    model="Hamilton_96_adapter_182531",
  )


def Hamilton_96_adapter_188182(name: str) -> PlateAdapter:
  """Hamilton cat. no.: 188182
  Adapter for 96 well PCR plate, plunged.
  Does not have an ANSI/SLAS footprint -> requires assignment with specified location.
  """
  return PlateAdapter(
    name=name,
    size_x=110.0,
    size_y=75.0,
    size_z=15.0,
    dx=1,
    dy=2.2,
    dz=2.8,  # TODO: correct dz once Plate definition has been completely fixed
    adapter_hole_size_x=7.4,
    adapter_hole_size_y=7.4,
    adapter_hole_size_z=10.0,  # guesstimate
    model="Hamilton_96_adapter_188182",
  )
