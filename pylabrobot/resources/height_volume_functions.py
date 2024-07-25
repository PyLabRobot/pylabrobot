# General use functions for calculating the height of a liquid inside different container geometries

import math


def _height_of_volume_in_spherical_cap(
  r: float,
  liquid_volume: float
) -> float:
  """ Calculate the height of liquid in a spherical cap given the radius of the sphere and the
  volume of the liquid.

  This function uses binary search to determine the height of the liquid within a spherical cap
  (a portion of a sphere).

  Parameters:
    r: The radius of the sphere in millimeters.
    liquid_volume: The volume of the liquid in microliter/cubic millimeters.

  Returns:
    The height of the liquid in the spherical cap in millimeters.

  Raises:
    ValueError: If the liquid volume exceeds the volume of a hemisphere of the given radius.

  Example:
    >>> _height_of_volume_in_spherical_cap(6.9, 100)
    2.28 # units: mm

  Notes:
    - The height is calculated with a precision defined by the tolerance value (1e-6).
  """

  def volume_of_spherical_cap(h: float):
    return (1/3) * math.pi * h**2 * (3*r - h)

  # Maximum volume of the spherical cap with height equal to the radius
  max_volume = volume_of_spherical_cap(r)
  if liquid_volume > max_volume:
    raise ValueError("""WARNING: Liquid volume exceeds the volume of a
                         hemisphere of the given radius.""")

  # Binary search to solve for h
  low, high = 0.0, r
  tolerance = 1e-6
  while high - low > tolerance:
    mid = (low + high) / 2
    if volume_of_spherical_cap(mid) < liquid_volume:
      low = mid
    else:
      high = mid
  liquid_height = (low + high) / 2

  return liquid_height


def calculate_liquid_height_in_container_2segments_square_vbottom(
  x: float,
  y: float,
  h_pyramid: float,
  h_cube: float,
  liquid_volume: float
) -> float:
  """ Calculate the height of liquid in a container consisting of an upside-down square pyramid at
  the bottom and a cuboid on top. The container has the same x and y dimensions for both the pyramid
  and the cuboid.

  The function calculates the height based on the given liquid volume.

  Parameters:
    x: The base length of the square pyramid and cube in mm.
    y: The base width of the square pyramid and cube in mm.
    h_pyramid: The height of the square pyramid in mm.
    h_cube: The height of the cube in mm.
    liquid_volume: The volume of the liquid in the container in cubic millimeters.

  Returns:
    The height of the liquid in the container in mm.
  """
  base_area = x * y

  # Calculating the full volume of the pyramid
  full_pyramid_volume = (1/3) * base_area * h_pyramid

  if liquid_volume <= full_pyramid_volume:
    # Liquid volume is within the pyramid
    scale_factor = (liquid_volume / full_pyramid_volume) ** (1/3)
    liquid_height = scale_factor * h_pyramid
  else:
    # Liquid volume extends into the cube
    cube_liquid_volume = liquid_volume - full_pyramid_volume
    cube_liquid_height = cube_liquid_volume / base_area
    liquid_height = h_pyramid + cube_liquid_height

    if liquid_height > h_pyramid + h_cube:
      raise ValueError("""WARNING: Liquid overflow detected;
      check your labware definition and/or that you are using the right labware.""")

  return float(liquid_height)


def calculate_liquid_volume_container_2segments_square_vbottom(
  x: float,
  y: float,
  h_pyramid: float,
  h_cube: float,
  liquid_height: float
) -> float:
  """ Calculate the volume of liquid in a container consisting of an upside-down square pyramid at
  the bottom and a cuboid on top. The container has the same x and y dimensions for both the pyramid
  and the cuboid.

  The function calculates the volume based on whether the liquid height is within the pyramid or
  extends into the cuboid.

  Parameters:
    x: The base length of the square pyramid and cube in mm.
    y: The base width of the square pyramid and cube in mm.
    h_pyramid: The height of the square pyramid in mm.
    h_cube: The height of the cube in mm.
    liquid_height: The height of the liquid in the container in mm.

  Returns:
    The volume of the liquid in cubic millimeters.
  """
  if liquid_height > h_pyramid + h_cube:
    raise ValueError("""WARNING: Liquid overflow detected;
    check your labware definiton and/or that you are using the right labware.""")

  # Calculating the base area
  base_area = x * y

  # Calculating the full volume of the pyramid
  full_pyramid_volume = (1/3) * base_area * h_pyramid

  if liquid_height <= h_pyramid:
    # Liquid height is within the pyramid
    # Calculating the scale factor for the reduced height
    scale_factor = liquid_height / h_pyramid
    # Calculating the sub-volume of the pyramid
    liquid_volume = full_pyramid_volume * (scale_factor ** 3)
  else:
    # Liquid height extends into the cube
    # Calculating the volume of the cube portion filled with liquid
    cube_liquid_height = liquid_height - h_pyramid
    cube_liquid_volume = base_area * cube_liquid_height
    # Total liquid volume is the sum of the pyramid and cube volumes
    liquid_volume = full_pyramid_volume + cube_liquid_volume

  return float(liquid_volume)


def calculate_liquid_height_in_container_2segments_square_ubottom(
  x: float,
  h_cuboid: float,
  liquid_volume: float
) -> float:
  """ Calculate the height of liquid in a container with a hemispherical bottom and a cuboidal top.
  The diameter of the hemisphere is equal to the side length of the square base of the cuboid.

  The function calculates the height based on the given liquid volume.

  Parameters:
    x: The side length of the square base of the cuboid and diameter of the hemisphere in mm.
    h_cuboid: The height of the cuboid in mm.
    liquid_volume: The volume of the liquid in the container in cubic millimeters.

  Returns:
    The height of the liquid in the container in mm.
  """
  r = x / 2  # Radius of the hemisphere
  full_hemisphere_volume = (2/3) * math.pi * r**3

  if liquid_volume <= full_hemisphere_volume:
    # Liquid volume is within the hemisphere
    liquid_height = _height_of_volume_in_spherical_cap(r=r, liquid_volume=liquid_volume)
  else:
    # Liquid volume extends into the cuboid
    cuboid_liquid_volume = liquid_volume - full_hemisphere_volume
    cuboid_liquid_height = cuboid_liquid_volume / (x**2)
    liquid_height = r + cuboid_liquid_height

    if liquid_height > h_cuboid + r:
      raise ValueError("""WARNING: Liquid overflow detected;
      check your labware definition and/or that you are using the right labware.""")

  return liquid_height


def calculate_liquid_volume_container_2segments_square_ubottom(
  x: float,
  h_cuboid: float,
  liquid_height: float
) -> float:
  """ Calculate the volume of liquid in a container with a hemispherical bottom and a cuboidal top.
  The diameter of the hemisphere is equal to the side length of the square base of the cuboid.

  The function calculates the volume based on whether the liquid height is within the hemisphere or
  extends into the cuboid.

  Parameters:
    x: The side length of the square base of the cuboid and diameter of the hemisphere in mm.
    h_cuboid: The height of the cuboid in mm.
    liquid_height: The height of the liquid in the container in mm.

  Returns:
    The volume of the liquid in cubic millimeters.
  """
  if liquid_height > h_cuboid + x/2:
    raise ValueError("""WARNING: Liquid overflow detected;
    check your labware definiton and/or that you are using the right labware.""")

  r = x / 2  # Radius of the hemisphere
  full_hemisphere_volume = (2/3) * math.pi * r**3

  if liquid_height <= r:
    # Liquid height is within the hemisphere
    # Calculating the sub-volume of the hemisphere using spherical cap volume formula
    h = liquid_height  # Height of the spherical cap
    liquid_volume = (1/3) * math.pi * h**2 * (3*r - h)
  else:
    # Liquid height extends into the cuboid
    # Calculating the volume of the cuboid portion filled with liquid
    cuboid_liquid_height = liquid_height - r
    cuboid_liquid_volume = x**2 * cuboid_liquid_height
    liquid_volume = full_hemisphere_volume + cuboid_liquid_volume

  return liquid_volume


def calculate_liquid_height_in_container_2segments_round_vbottom(
  d: float,
  h_cone: float,
  h_cylinder: float,
  liquid_volume: float
) -> float:
  """ Calculate the height of liquid in a container consisting of an upside-down circular cone at
  the bottom and a cylinder on top. The container has the same diameter for both the cone and the
  cylinder. The function calculates the height based on the given liquid volume.

  Parameters:
    d: The diameter of the base of the cone and cylinder in mm.
    h_cone: The height of the circular cone in mm.
    h_cylinder: The height of the cylinder in mm.
    liquid_volume: The volume of the liquid in the container in cubic millimeters.

  Returns:
    The height of the liquid in the container in mm.
  """

  radius = d / 2
  base_area = math.pi * (radius ** 2)

  # Calculating the full volume of the cone
  full_cone_volume = (1 / 3) * base_area * h_cone

  # Calculate total container volume
  total_container_volume = full_cone_volume + (base_area * h_cylinder)

  # Check for overflow
  if liquid_volume > total_container_volume:
    raise ValueError("WARNING: Liquid overflow detected; check your labware definition and/or that "
                     "you are using the right labware.")

  if liquid_volume <= full_cone_volume:
    # Liquid volume is within the cone
    scale_factor: float = (liquid_volume / full_cone_volume) ** (1 / 3)
    liquid_height = scale_factor * h_cone
  else:
    # Liquid volume extends into the cylinder
    cylinder_liquid_volume = liquid_volume - full_cone_volume
    cylinder_liquid_height = cylinder_liquid_volume / base_area
    liquid_height = h_cone + cylinder_liquid_height

  return liquid_height


def calculate_liquid_volume_container_2segments_round_vbottom(
  d: float,
  h_cone: float,
  h_cylinder: float,
  liquid_height: float
) -> float:
  """ Calculate the volume of liquid in a container with a conical bottom and a cylindrical top. The
  container has the same radius for both the cone and the cylinder.

  The function calculates the volume based on whether the liquid height is within the cone or
  extends into the cylinder.

  Parameters:
    d: The diameter of the base of the cone and cylinder in mm.
    h_cone: The height of the cone in mm.
    h_cylinder: The height of the cylinder in mm.
    liquid_height: The height of the liquid in the container in mm.

  Returns:
    The volume of the liquid in cubic millimeters.
  """
  if liquid_height > h_cone+h_cylinder:
    raise ValueError("""WARNING: Liquid overflow detected;
    check your labware definiton and/or that you are using the right labware.""")

  r = d/2
  # Calculating the full volume of the cone
  full_cone_volume = (1/3) * math.pi * r**2 * h_cone

  if liquid_height <= h_cone:
    # Liquid height is within the cone
    # Calculating the scale factor for the reduced height
    scale_factor = liquid_height / h_cone
    # Calculating the sub-volume of the cone
    liquid_volume = full_cone_volume * (scale_factor ** 3)
  else:
    # Liquid height extends into the cylinder
    # Calculating the volume of the cylinder portion filled with liquid
    cylinder_liquid_height = liquid_height - h_cone
    cylinder_liquid_volume = math.pi * r**2 * cylinder_liquid_height
    # Total liquid volume is the sum of the cone and cylinder volumes
    liquid_volume = full_cone_volume + cylinder_liquid_volume

  return float(liquid_volume)


def calculate_liquid_height_in_container_2segments_round_ubottom(
  d: float,
  h_cylinder: float,
  liquid_volume: float
) -> float:
  """ Calculate the height of liquid in a container consisting of a hemispherical bottom and a
  cylindrical top. The container has the same diameter for both the hemisphere and the cylinder. The
  function calculates the height based on the given liquid volume.

  Parameters:
    d: The diameter of the base of the hemisphere and cylinder in mm.
    h_cylinder: The height of the cylinder in mm.
    liquid_volume: The volume of the liquid in the container in cubic millimeters.

  Returns:
    The height of the liquid in the container in mm.
  """

  radius = d / 2
  hemisphere_volume = (2 / 3) * math.pi * (radius ** 3)
  base_area = math.pi * (radius ** 2)

  # Calculate total container volume
  cylinder_volume = base_area * h_cylinder
  total_container_volume = hemisphere_volume + cylinder_volume

  # Check for overflow
  if liquid_volume > total_container_volume:
    raise ValueError("WARNING: Liquid overflow detected; check your labware definition and/or that "
                     "you are using the right labware.")

  if liquid_volume <= hemisphere_volume:
    # Liquid volume is within the hemisphere
    liquid_height = _height_of_volume_in_spherical_cap(r=radius, liquid_volume=liquid_volume)
  else:
    # Liquid volume extends into the cylinder
    cylinder_liquid_volume = liquid_volume - hemisphere_volume
    cylinder_liquid_height = cylinder_liquid_volume / base_area
    liquid_height = radius + cylinder_liquid_height

  return liquid_height


def calculate_liquid_volume_container_2segments_round_ubottom(
  d: float,
  h_cylinder: float,
  liquid_height: float
) -> float:
  """ Calculate the volume of liquid in a container with a hemispherical bottom and a cylindrical
  top. The container has the same radius for both the hemisphere and the cylinder.

  The function calculates the volume based on whether the liquid height is within the hemisphere or
  extends into the cylinder.

  Parameters:
    d: The diameter of the base of the hemisphere and cylinder in mm.
    h_cylinder: The height of the cylinder in mm.
    liquid_height: The height of the liquid in the container in mm.

  Returns:
    The volume of the liquid in cubic millimeters.
  """
  r = d/2
  if liquid_height > h_cylinder+r:
    raise ValueError("""WARNING: Liquid overflow detected;
    check your labware definiton and/or that you are using the right labware.""")

  # Calculating the full volume of the hemisphere
  full_hemisphere_volume = (2/3) * math.pi * r**3

  if liquid_height <= r:
    # Liquid height is within the hemisphere
    # Calculating the sub-volume of the hemisphere using spherical cap volume formula
    h = liquid_height  # Height of the spherical cap
    liquid_volume = (1/3) * math.pi * h**2 * (3*r - h)
  else:
    # Liquid height extends into the cylinder
    # Calculating the volume of the cylinder portion filled with liquid
    cylinder_liquid_height = liquid_height - r
    cylinder_liquid_volume = math.pi * r**2 * cylinder_liquid_height
    # Total liquid volume is the sum of the hemisphere and cylinder volumes
    liquid_volume = full_hemisphere_volume + cylinder_liquid_volume

  return float(liquid_volume)


def calculate_liquid_height_container_1segment_round_fbottom(
  d: float,
  h_cylinder: float,
  liquid_volume: float
) -> float:
  """ Calculate the height of liquid in a container with a cylindrical shape.

  Parameters:
    d: The diameter of the base of the cylinder in mm.
    h_cylinder: The height of the cylinder in mm.
    liquid_volume: The volume of the liquid in the container in cubic millimeters.

  Returns:
    The height of the liquid in the container in mm.
  """
  r = d / 2
  max_volume = math.pi * r**2 * h_cylinder

  if liquid_volume > max_volume:
    raise ValueError("""WARNING: Liquid overflow detected;
    check your labware definition and/or that you are using the right labware.""")

  liquid_height = liquid_volume / (math.pi * r**2)
  return liquid_height

def calculate_liquid_volume_container_1segment_round_fbottom(
  d: float,
  h_cylinder: float,
  liquid_height: float
) -> float:
  """ Calculate the volume of liquid in a container with a cylindrical shape.

  Parameters:
    d: The diameter of the base of the hemisphere and cylinder in mm.
    h_cylinder: The height of the cylinder in mm.
    liquid_height: The height of the liquid in the container in mm.

  Returns:
    The volume of the liquid in cubic millimeters.
  """
  r = d/2
  if liquid_height > h_cylinder:
    raise ValueError("""WARNING: Liquid overflow detected;
    check your labware definiton and/or that you are using the right labware.""")

  cylinder_liquid_volume = math.pi * r**2 * liquid_height
  return cylinder_liquid_volume


### Example of usage using a lambda function:
# def Rectangular_Reservoir(name: str) -> Plate:
#   """ An 8 well resevoir with a 30mL volume. """
#   WELL_WIDTH = 8.08
#   WELL_LENGTH = 107.4
#   return Plate(
#     name=name,
#     ...
#     items=create_equally_spaced_2d(
#       Well,
#       ...
#       compute_height_from_volume=lambda liquid_volume: _compute_height_from_volume_rectangle(
#         liquid_volume, WELL_LENGTH, WELL_WIDTH
#       ),
#     ),
#   )

def compute_volume_from_height_cylinder(liquid_height: float, well_radius: float) -> float:
  """Compute volume (uL) from height (mm) for a cylinder."""
  return math.pi * (well_radius**2) * liquid_height


def compute_height_from_volume_cylinder(liquid_volume: float, well_radius: float) -> float:
  """Compute height (mm) from volume (uL) for a cylinder."""
  return liquid_volume / (math.pi * (well_radius**2))


def compute_volume_from_height_conical_frustum(liquid_height: float, bottom_radius: float,
                                               top_radius: float) -> float:
  """Compute volume (uL) from height (mm) for a conical frustum."""
  return (1 / 3) * math.pi * liquid_height * \
    (bottom_radius**2 + bottom_radius * top_radius + top_radius**2)


def compute_height_from_volume_conical_frustum(liquid_volume: float, bottom_radius: float,
                                               top_radius: float) -> float:
  """Compute height (mm) from volume (uL) for a conical frustum."""
  return (3 * liquid_volume) / \
    (math.pi * (bottom_radius**2 + bottom_radius * top_radius + top_radius**2))


def compute_volume_from_height_square(liquid_height: float, well_side_length: float) -> float:
  """Compute volume (uL) from height (mm) for a square well."""
  return liquid_height * (well_side_length**2)


def compute_height_from_volume_square(liquid_volume: float, well_side_length: float) -> float:
  """Compute height (mm) from volume (uL) for a square well."""
  return liquid_volume / (well_side_length**2)


def compute_volume_from_height_rectangle(liquid_height: float, well_length: float,
                                         well_width: float) -> float:
  """Compute volume (uL) from height (mm) for a rectangular well."""
  return liquid_height * (well_length * well_width)


def compute_height_from_volume_rectangle(liquid_volume: float, well_length: float,
                                         well_width: float) -> float:
  """Compute height (mm) from volume (uL) for a rectangular well."""
  return liquid_volume / (well_length * well_width)
