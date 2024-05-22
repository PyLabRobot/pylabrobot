# General use functions for calculating the height of
# a liquid inside different container geometries

import math


def _height_of_volume_in_spherical_cap(
  r: float,
  liquid_volume: float
) -> float:
  def volume_of_spherical_cap(h: float):
    return (1/3) * math.pi * h**2 * (3*r - h)

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
  """
  Calculate the height of liquid in a container consisting of an upside-down
  square pyramid at the bottom and a cuboid on top. The container has the
  same x and y dimensions for both the pyramid and the cuboid.

  The function calculates the height based on the given liquid volume.

  Parameters:
    x (float): The base length of the square pyramid and cube in mm.
    y (float): The base width of the square pyramid and cube in mm.
    h_pyramid (float): The height of the square pyramid in mm.
    h_cube (float): The height of the cube in mm.
    liquid_volume (float): The volume of the liquid in the container in cubic millimeters.

  Returns:
    float: The height of the liquid in the container in mm.
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


def calculate_liquid_height_in_container_2segments_square_ubottom(
  x: float,
  h_cuboid: float,
  liquid_volume: float
) -> float:
  """
  Calculate the height of liquid in a container with a hemispherical bottom and a cuboidal top.
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


def calculate_liquid_height_in_container_2segments_round_vbottom(
  d: float,
  h_cone: float,
  h_cylinder: float,
  liquid_volume: float
) -> float:
  """
  Calculate the height of liquid in a container consisting of an upside-down circular cone at the
  bottom and a cylinder on top. The container has the same diameter for both the cone and the
  cylinder. The function calculates the height based on the given liquid volume.

  Parameters:
    d (float): The diameter of the base of the cone and cylinder in mm.
    h_cone (float): The height of the circular cone in mm.
    h_cylinder (float): The height of the cylinder in mm.
    liquid_volume (float): The volume of the liquid in the container in cubic millimeters.

  Returns:
    float: The height of the liquid in the container in mm.
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


def calculate_liquid_height_in_container_2segments_round_ubottom(
  d: float,
  h_cylinder: float,
  liquid_volume: float
) -> float:
  """
  Calculate the height of liquid in a container consisting of a hemispherical bottom and a
  cylindrical top. The container has the same diameter for both the hemisphere and the cylinder. The
  function calculates the height based on the given liquid volume.

  Parameters:
    d (float): The diameter of the base of the hemisphere and cylinder in mm.
    h_cylinder (float): The height of the cylinder in mm.
    liquid_volume (float): The volume of the liquid in the container in cubic millimeters.

  Returns:
    float: The height of the liquid in the container in mm.
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


def calculate_liquid_height_container_1segment_round_fbottom(
  d: float,
  h_cylinder: float,
  liquid_volume: float
) -> float:
  """
  Calculate the height of liquid in a container with a cylindrical shape.

  Parameters:
    d (float): The diameter of the base of the cylinder in mm.
    h_cylinder (float): The height of the cylinder in mm.
    liquid_volume (float): The volume of the liquid in the container in cubic millimeters.

  Returns:
    float: The height of the liquid in the container in mm.
  """
  r = d / 2
  max_volume = math.pi * r**2 * h_cylinder

  if liquid_volume > max_volume:
    raise ValueError("""WARNING: Liquid overflow detected;
    check your labware definition and/or that you are using the right labware.""")

  liquid_height = liquid_volume / (math.pi * r**2)
  return liquid_height
