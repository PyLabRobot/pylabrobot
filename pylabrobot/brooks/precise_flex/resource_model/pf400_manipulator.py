"""The PreciseFlex 400's manipulator: its base plate, the Z joint's column and carriage, its links.

Each part is a cuboid in its own frame, located by its left front bottom corner, in mm. Sizes are
measured off the manufacturer's model. A link lies along its x from the joint it turns on to the
joint the next member turns on; its cross-section and hubs are the same on both reaches, and the
length between its joints is what the controller reports.
"""

from pylabrobot.brooks.precise_flex.kinematics import ARM_LINKS_EXTENDED, ARM_LINKS_STANDARD
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.manipulator import LinkBody
from pylabrobot.resources.resource import Resource

BASE_PLATE_SIZE = (201.0, 235.0, 9.6)

# The J2 shoulder axis within the base plate: at its front face, centred across it, at the height
# of the tool flange with the Z drive at 0 (Brooks' IGES). The controller reports from here.
SHOULDER_AXIS = Coordinate(BASE_PLATE_SIZE[0], BASE_PLATE_SIZE[1] / 2, 77.57)

Z_COLUMN_SIZE_XY = (124.5, 181.5)
# Where the column stands on the base plate's top face, centred across it.
Z_COLUMN_LOCATION = Coordinate(
  9.0, (BASE_PLATE_SIZE[1] - Z_COLUMN_SIZE_XY[1]) / 2, BASE_PLATE_SIZE[2]
)
# The datasheet's robot height, from the floor to the column's top, for each Z travel it is sold with.
ROBOT_HEIGHTS = {400.0: 679.0, 750.0: 1029.0, 1160.0: 1439.0}

# The carriage the J1 drive rides up the column: the housing the arm turns in and the bracket under
# it against the column, which is what the drive carries and what is seen of it.
Z_CARRIAGE_SIZE = (122.5, 110.0, 73.5)
# Where it rides: at the column's front face, centred across it. Z is whatever the drive reports.
Z_CARRIAGE_LOCATION_XY = (Z_COLUMN_SIZE_XY[0], (Z_COLUMN_SIZE_XY[1] - Z_CARRIAGE_SIZE[1]) / 2)
# How far the carriage's underside, its bracket's, stands above the flange plane the drive reports.
# The housing starts 17.0 higher, just above link 1.
Z_CARRIAGE_ABOVE_FLANGE_PLANE = 88.8
# The point the drive reports, within the carriage: the shoulder axis, at the flange plane below it.
Z_CARRIAGE_REFERENCE_POINT = Coordinate(
  SHOULDER_AXIS.x - Z_COLUMN_LOCATION.x - Z_CARRIAGE_LOCATION_XY[0],
  Z_CARRIAGE_SIZE[1] / 2,
  -Z_CARRIAGE_ABOVE_FLANGE_PLANE,
)


def z_column_height(z_travel: float) -> float:
  """How tall the column is on an arm whose Z drive reaches `z_travel` mm.

  Measured from the base plate's top face, where the column stands, to its own top. The column is
  the one sold for the nearest Z travel, as an arm's soft limits stand a little past it.

  Args:
    z_travel: the highest the Z drive reaches, in mm: the top of its soft limits.

  Returns:
    The height, in mm.
  """
  sold = min(ROBOT_HEIGHTS, key=lambda travel: abs(travel - z_travel))
  return ROBOT_HEIGHTS[sold] - Z_COLUMN_LOCATION.z


def base_plate(name: str = "pf400_base_plate") -> Resource:
  """The base plate the arm stands on.

  Args:
    name: what to call this one.

  Returns:
    The base plate.
  """
  return Resource(
    name=name,
    size_x=BASE_PLATE_SIZE[0],
    size_y=BASE_PLATE_SIZE[1],
    size_z=BASE_PLATE_SIZE[2],
    category="base_plate",
    model="brooks_pf400_base_plate",
  )


def z_column(name: str, height: float) -> Resource:
  """The column the carriage rides, drawn as the one sold nearest its height.

  Args:
    name: what to call this one.
    height: how tall it is, in mm, as `z_column_height` gives it.

  Returns:
    The column.
  """
  sold = min(
    ROBOT_HEIGHTS, key=lambda travel: abs(ROBOT_HEIGHTS[travel] - Z_COLUMN_LOCATION.z - height)
  )
  return Resource(
    name=name,
    size_x=Z_COLUMN_SIZE_XY[0],
    size_y=Z_COLUMN_SIZE_XY[1],
    size_z=height,
    category="z_column",
    model=f"brooks_pf400_z_column_{sold:.0f}mm",
  )


def z_carriage_location(z: float) -> Coordinate:
  """Where the carriage stands on the column when the J1 drive reports `z`.

  A resource is located by its corner, so the reported point is taken out of the reading.

  Args:
    z: where the drive reports the flange plane is, in mm above where it is with the drive at 0.

  Returns:
    The location, on the column.
  """
  return Coordinate(
    Z_CARRIAGE_LOCATION_XY[0],
    Z_CARRIAGE_LOCATION_XY[1],
    SHOULDER_AXIS.z + z - Z_COLUMN_LOCATION.z - Z_CARRIAGE_REFERENCE_POINT.z,
  )


def z_carriage(name: str = "pf400_z_carriage") -> Resource:
  """The carriage the J1 drive rides up the column, and the arm turns in.

  Args:
    name: what to call this one.

  Returns:
    The carriage.
  """
  return Resource(
    name=name,
    size_x=Z_CARRIAGE_SIZE[0],
    size_y=Z_CARRIAGE_SIZE[1],
    size_z=Z_CARRIAGE_SIZE[2],
    category="z_carriage",
    model="brooks_pf400_z_carriage",
  )


# Link 1, shoulder to elbow: how wide and how tall it is, and how far its round hub reaches past the
# joint at each end.
LINK_1_SIZE_YZ = (114.0, 73.4)
LINK_1_HUB_OVERHANG = 57.0

# Link 2, elbow to wrist: a full hub at the elbow and a smaller one at the wrist.
LINK_2_SIZE_YZ = (112.0, 39.2)
LINK_2_HUB_PROXIMAL = 56.0
LINK_2_HUB_DISTAL = 36.0

# How far link 1's underside stands above the flange plane, which link 2's underside lies in.
# Less than link 2 is tall: at the elbow the two hubs nest.
LINK_1_ABOVE_FLANGE_PLANE = 31.5


def _reach(link: int, length: float) -> str:
  """The reach whose link `link` (0 or 1) is nearest `length`: "standard" or "extended"."""
  reaches = {"standard": ARM_LINKS_STANDARD[link], "extended": ARM_LINKS_EXTENDED[link]}
  return min(reaches, key=lambda reach: abs(reaches[reach] - length))


def _link(
  name: str, length: float, size_yz: tuple, hub_proximal: float, hub_distal: float, model: str
) -> LinkBody:
  """A link `length` between its joints, reaching a hub past each."""
  # The joints lie along the bottom face, centred across the link.
  proximal_joint = Coordinate(hub_proximal, size_yz[0] / 2, 0.0)
  return LinkBody(
    name=name,
    size_x=hub_proximal + length + hub_distal,
    size_y=size_yz[0],
    size_z=size_yz[1],
    proximal_joint=proximal_joint,
    distal_joint=proximal_joint + Coordinate(length, 0.0, 0.0),
    model=model,
  )


def link_1(name: str, length: float) -> LinkBody:
  """The first link: the shoulder joint to the elbow joint, drawn as the nearest reach's.

  Args:
    name: what to call this one.
    length: joint to joint, in mm, as the controller reports it.

  Returns:
    The link.
  """
  model = f"brooks_pf400_link_1_{_reach(0, length)}"
  return _link(name, length, LINK_1_SIZE_YZ, LINK_1_HUB_OVERHANG, LINK_1_HUB_OVERHANG, model)


def link_2(name: str, length: float) -> LinkBody:
  """The second link: the elbow joint to the wrist joint, drawn as the nearest reach's.

  Args:
    name: what to call this one.
    length: joint to joint, in mm, as the controller reports it.

  Returns:
    The link.
  """
  model = f"brooks_pf400_link_2_{_reach(1, length)}"
  return _link(name, length, LINK_2_SIZE_YZ, LINK_2_HUB_PROXIMAL, LINK_2_HUB_DISTAL, model)
