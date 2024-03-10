# General use functions for calculating the volume of different container geometries

import math


def calculate_liquid_volume_container_2segments_square_vbottom(
  x: float,
  y: float,
  h_pyramid: float,
  h_cube: float,
  liquid_height: float
) -> float:
  """
  Calculate the volume of liquid in a container consisting of an upside-down
  square pyramid at the bottom and a cuboid on top. The container has the
  same x and y dimensions for both the pyramid and the cuboid.

  The function calculates the volume based on whether the liquid height is within
  the pyramid or extends into the cuboid.

  Parameters:
    x (float): The base length of the square pyramid and cube in mm.
    y (float): The base width of the square pyramid and cube in mm.
    h_pyramid (float): The height of the square pyramid in mm.
    h_cube (float): The height of the cube in mm.
    liquid_height (float): The height of the liquid in the container in mm.

  Returns:
    float: The volume of the liquid in cubic millimeters.
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


def calculate_liquid_volume_container_2segments_square_ubottom(
  x: float,
  h_cuboid: float,
  liquid_height: float
) -> float:
  """
  Calculate the volume of liquid in a container with a hemispherical bottom and a cuboidal top.
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


def calculate_liquid_volume_container_2segments_round_vbottom(
  d: float,
  h_cone: float,
  h_cylinder: float,
  liquid_height: float
) -> float:
  """
  Calculate the volume of liquid in a container with a conical bottom and
  a cylindrical top. The container has the same radius for both the cone and
  the cylinder.

  The function calculates the volume based on whether the liquid height is
  within the cone or extends into the cylinder.

  Parameters:
    d (float): The diameter of the base of the cone and cylinder in mm.
    h_cone (float): The height of the cone in mm.
    h_cylinder (float): The height of the cylinder in mm.
    liquid_height (float): The height of the liquid in the container in mm.

  Returns:
    float: The volume of the liquid in cubic millimeters.
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


def calculate_liquid_volume_container_2segments_round_ubottom(
  d: float,
  h_cylinder: float,
  liquid_height: float
) -> float:
  """
  Calculate the volume of liquid in a container with a hemispherical bottom
  and a cylindrical top. The container has the same radius for both the
  hemisphere and the cylinder.

  The function calculates the volume based on whether the liquid height is
  within the hemisphere or extends into the cylinder.

  Parameters:
    d (float): The diameter of the base of the hemisphere and cylinder in mm.
    h_cylinder (float): The height of the cylinder in mm.
    liquid_height (float): The height of the liquid in the container in mm.

  Returns:
    float: The volume of the liquid in cubic millimeters.
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
