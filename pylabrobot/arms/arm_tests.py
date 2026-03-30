import unittest
from unittest.mock import AsyncMock, MagicMock

from pylabrobot.arms.arm import GripperArm
from pylabrobot.arms.backend import (
  GripperArmBackend,
  OrientableGripperArmBackend,
)
from pylabrobot.arms.orientable_arm import OrientableArm
from pylabrobot.arms.standard import GripDirection
from pylabrobot.resources import Coordinate, Resource, ResourceHolder


def _assert_location(test, call, x, y, z, places=1):
  """Assert the location kwarg of a mock call matches expected coordinates."""
  loc = call.kwargs["location"]
  test.assertAlmostEqual(loc.x, x, places=places)
  test.assertAlmostEqual(loc.y, y, places=places)
  test.assertAlmostEqual(loc.z, z, places=places)


def _make_deck_with_sites():
  """Create a fictional deck with two sites and a plate.

  Deck: 1000x1000x0 at origin.
  Site A at (100, 100, 50), site B at (100, 300, 50).
  Plate: 120x80x10 assigned to site A.
  """
  deck = Resource("deck", size_x=1000, size_y=1000, size_z=0)

  site_a = ResourceHolder("site_a", size_x=130, size_y=90, size_z=0)
  deck.assign_child_resource(site_a, location=Coordinate(100, 100, 50))

  site_b = ResourceHolder("site_b", size_x=130, size_y=90, size_z=0)
  deck.assign_child_resource(site_b, location=Coordinate(100, 300, 50))

  plate = Resource("plate", size_x=120, size_y=80, size_z=10)
  site_a.assign_child_resource(plate, location=Coordinate(5, 5, 0))

  return deck, site_a, site_b, plate


class TestArm(unittest.IsolatedAsyncioTestCase):
  """Test Arm (ArmBackend, no rotation). E.g. Hamilton core grippers."""

  async def asyncSetUp(self):
    self.mock_backend = MagicMock(spec=GripperArmBackend)
    for method_name in [
      "pick_up_at_location",
      "drop_at_location",
      "move_to_location",
      "open_gripper",
      "close_gripper",
      "is_gripper_closed",
      "halt",
      "park",
    ]:
      setattr(self.mock_backend, method_name, AsyncMock())

    self.deck, self.site_a, self.site_b, self.plate = _make_deck_with_sites()
    self.arm = GripperArm(backend=self.mock_backend, reference_resource=self.deck)

  async def test_pick_up_resource(self):
    # plate center: site_a(100,100,50) + child_loc(5,5,0) + center(60,40,10) = (165, 145, 60)
    # pickup_distance_from_top=2 → z = 60 - 2 = 58
    await self.arm.pick_up_resource(self.plate, pickup_distance_from_top=2)
    call = self.mock_backend.pick_up_at_location.call_args
    _assert_location(self, call, 165, 145, 58)
    # default grip_axis="x" → resource_width is X size = 120
    self.assertAlmostEqual(call.kwargs["resource_width"], 120)

  async def test_drop_resource(self):
    await self.arm.pick_up_resource(self.plate, pickup_distance_from_top=2)
    await self.arm.drop_resource(self.site_b)
    call = self.mock_backend.drop_at_location.call_args
    # site_b(100,300,50) + default_child_loc(0,0,0) + center(60,40,10) = (160, 340, 60)
    # pickup_distance_from_top=2 → z = 60 - 2 = 58
    _assert_location(self, call, 160, 340, 58)
    self.assertEqual(self.plate.parent.name, "site_b")

  async def test_pick_up_at_location(self):
    location = Coordinate(x=100, y=200, z=300)
    await self.arm.pick_up_at_location(location, resource_width=80.0)
    self.mock_backend.pick_up_at_location.assert_called_once_with(
      location=location, resource_width=80.0, backend_params=None
    )

  async def test_drop_at_location(self):
    location = Coordinate(x=100, y=200, z=300)
    await self.arm.pick_up_at_location(location, resource_width=80.0)
    await self.arm.drop_at_location(location)
    self.mock_backend.drop_at_location.assert_called_once_with(
      location=location, resource_width=80.0, backend_params=None
    )

  async def test_move_to_location(self):
    location = Coordinate(x=100, y=200, z=300)
    await self.arm.move_to_location(location)
    self.mock_backend.move_to_location.assert_called_once_with(
      location=location, backend_params=None
    )

  async def test_open_gripper(self):
    await self.arm.open_gripper(gripper_width=50.0)
    self.mock_backend.open_gripper.assert_called_once_with(gripper_width=50.0, backend_params=None)

  async def test_halt(self):
    await self.arm.halt()
    self.mock_backend.halt.assert_called_once()

  async def test_park(self):
    await self.arm.park()
    self.mock_backend.park.assert_called_once()

  async def test_grip_axis_y(self):
    """With grip_axis='y', resource_width should be the Y size."""
    arm_y = GripperArm(backend=self.mock_backend, reference_resource=self.deck, grip_axis="y")
    await arm_y.pick_up_resource(self.plate, pickup_distance_from_top=2)
    call = self.mock_backend.pick_up_at_location.call_args
    # plate size_y=80
    self.assertAlmostEqual(call.kwargs["resource_width"], 80)


class TestOrientableArm(unittest.IsolatedAsyncioTestCase):
  """Test OrientableArm coordinate computation with fictional resources."""

  async def asyncSetUp(self):
    self.mock_backend = MagicMock(spec=OrientableGripperArmBackend)
    for method_name in [
      "pick_up_at_location",
      "drop_at_location",
      "move_to_location",
    ]:
      setattr(self.mock_backend, method_name, AsyncMock())

    self.deck, self.site_a, self.site_b, self.plate = _make_deck_with_sites()
    self.arm = OrientableArm(backend=self.mock_backend, reference_resource=self.deck)

  async def test_pick_up_front(self):
    await self.arm.pick_up_resource(
      self.plate, pickup_distance_from_top=2, direction=GripDirection.FRONT
    )
    call = self.mock_backend.pick_up_at_location.call_args
    _assert_location(self, call, 165, 145, 58)
    self.assertAlmostEqual(call.kwargs["direction"], 0.0)
    # FRONT → X width = 120
    self.assertAlmostEqual(call.kwargs["resource_width"], 120)

  async def test_pick_up_right(self):
    await self.arm.pick_up_resource(
      self.plate, pickup_distance_from_top=2, direction=GripDirection.RIGHT
    )
    call = self.mock_backend.pick_up_at_location.call_args
    self.assertAlmostEqual(call.kwargs["direction"], 90.0)
    # RIGHT → Y width = 80
    self.assertAlmostEqual(call.kwargs["resource_width"], 80)

  async def test_drop_at_location(self):
    location = Coordinate(x=100, y=200, z=300)
    await self.arm.pick_up_at_location(location, resource_width=80.0, direction=0.0)
    await self.arm.drop_at_location(location, direction=180.0)
    self.mock_backend.drop_at_location.assert_called_once_with(
      location=location, direction=180.0, resource_width=80.0, backend_params=None
    )

  async def test_move_to_location(self):
    location = Coordinate(x=100, y=200, z=300)
    await self.arm.move_to_location(location, direction=90.0)
    self.mock_backend.move_to_location.assert_called_once_with(
      location=location, direction=90.0, backend_params=None
    )

  async def test_move_plate(self):
    """Pick from site_a, drop at site_b."""
    await self.arm.pick_up_resource(
      self.plate, pickup_distance_from_top=2, direction=GripDirection.FRONT
    )
    await self.arm.drop_resource(self.site_b, direction=GripDirection.FRONT)
    drop_call = self.mock_backend.drop_at_location.call_args
    _assert_location(self, drop_call, 160, 340, 58)
    self.assertEqual(self.plate.parent.name, "site_b")
