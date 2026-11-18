"""Logical carousel occupancy and orientation with mocked HID only."""

import asyncio
from dataclasses import replace

from pylabrobot.resources import (
  Plate,
  PlateCarrier,
  PlateHolder,
  Resource,
  ResourceStack,
  thermo_TS_kingfisher_96_wellplate_2000uL_Vb,
)
from pylabrobot.thermo_fisher import KingFisherFlex

from .connection import FlexError
from .protocol import FlexProtocolError, read_flex_bdz
from .test_helpers import HIDTestCase, bdz_bytes


class FlexResourceTests(HIDTestCase):
  """Keep resource identity independent of instrument motion and connection state."""

  async def asyncSetUp(self) -> None:
    """Create a disconnected driver with a plate assigned to printed tray 2."""
    self.mock_hid()
    self.flex = KingFisherFlex("fixture", 0.2, 0.2, name="flex_a")
    self.flex.io = self.io
    self.plate = thermo_TS_kingfisher_96_wellplate_2000uL_Vb("sample")
    self.flex.carousel[2] = self.plate
    self.plate_bindings = {"plate": self.plate}
    self.holders = tuple(self.flex.carousel.sites.values())
    self.protocol = read_flex_bdz(bdz_bytes())

  async def asyncTearDown(self) -> None:
    """Drain mocked connection tasks."""
    await self.flex.stop()

  def assert_occupancy_preserved(self) -> None:
    """Check identity, not only resource equality."""
    for tray, holder in enumerate(self.holders, 1):
      self.assertIs(self.flex.carousel[tray], holder)
      self.assertIs(holder.parent, self.flex.carousel)
    self.assertIs(self.flex.carousel[2].resource, self.plate)
    self.assertIs(self.plate.parent, self.flex.carousel[2])

  def assert_orientation_unknown(self) -> None:
    """No station lookup should survive uncertain motion."""
    self.assertIsNone(self.flex.processing_slot)
    self.assertIsNone(self.flex.loading_slot)
    self.assertIsNone(self.flex.processing_holder)
    self.assertIsNone(self.flex.loading_holder)

  async def ready(self) -> None:
    """Initialize only the mock instrument."""
    await self.flex.setup()
    await self.flex.initialize()
    self.flex.reconcile_inventory()

  async def test_logical_tree_and_normal_assignment(self) -> None:
    """Eight explicit holders use standard assignment without issuing USB commands."""
    self.assertIsInstance(self.flex.carousel, PlateCarrier)
    self.assertEqual(list(self.flex.carousel.sites), list(range(1, 9)))
    self.assertEqual(len(self.flex.carousel.children), 8)
    for tray, holder in self.flex.carousel.sites.items():
      self.assertIsInstance(holder, PlateHolder)
      self.assertEqual(holder.name, f"flex_a_tray_{tray}")
      self.assertIsNone(holder.location)
      self.assertIs(holder.parent, self.flex.carousel)
    self.assert_orientation_unknown()
    self.assertFalse(self.flex.inventory_verified)
    self.flex.reconcile_inventory()
    self.assertTrue(self.flex.inventory_verified)
    self.plate.unassign()
    self.assertIsNone(self.plate.parent)
    self.assertIsNone(self.flex.carousel[2].resource)
    self.assertFalse(self.flex.inventory_verified)
    self.flex.reconcile_inventory()  # Explicitly confirm even an empty tree.
    self.assertTrue(self.flex.inventory_verified)
    self.flex.carousel[3].resource = self.plate
    self.assertIs(self.plate.parent, self.flex.carousel[3])
    self.assertFalse(self.flex.inventory_verified)
    self.plate.unassign()
    self.flex.carousel[4].assign_child_resource(self.plate)
    self.assertIsNone(self.flex.carousel[3].resource)
    self.assertIs(self.plate.parent, self.flex.carousel[4])
    self.flex.carousel[4] = None
    self.assertIsNone(self.plate.parent)
    self.io.setup.assert_not_awaited()
    self.io.write.assert_not_awaited()

  async def test_single_plate_enforced_for_assignment_and_preflight(self) -> None:
    """No stack, non-plate or implicit occupied-holder replacement is accepted."""
    other = thermo_TS_kingfisher_96_wellplate_2000uL_Vb("other")
    self.flex.reconcile_inventory()
    with self.assertRaisesRegex(ValueError, "occupied"):
      self.flex.carousel[2].assign_child_resource(other, reassign=True)
    with self.assertRaisesRegex(ValueError, "occupied"):
      self.flex.carousel[2].check_can_drop_resource_here(other)
    self.assertTrue(self.flex.inventory_verified)
    self.assertIsNone(other.parent)
    self.assertEqual(self.flex.carousel[2].children, [self.plate])
    for resource in (Resource("object", 1, 1, 1), ResourceStack("stack", direction="z")):
      with self.subTest(resource=resource.name):
        with self.assertRaises(TypeError):
          self.flex.carousel[1].assign_child_resource(resource)
        with self.assertRaises(TypeError):
          self.flex.carousel[1].check_can_drop_resource_here(resource)
        self.assertIsNone(resource.parent)
    # Physical plate compatibility belongs to execution, not this logical tree.
    generic_plate = Plate("generic", 1, 1, 1, plate_type="non-skirted", ordered_items={})
    self.flex.carousel[1] = generic_plate
    self.assertIs(self.flex.carousel[1].resource, generic_plate)

  async def test_connection_and_initialization_preserve_occupancy(self) -> None:
    """Homing establishes orientation but does not establish an empty inventory."""
    self.flex.reconcile_inventory()
    await self.flex.setup()
    self.assertFalse(self.flex.inventory_verified)
    self.flex.reconcile_inventory()
    await self.flex.setup()
    self.assertTrue(self.flex.inventory_verified)
    self.responses["MOR 1"] = "MOR\r\n"
    task = asyncio.create_task(self.flex.initialize())
    await self.wait_sent("MOR 1")
    self.assert_orientation_unknown()
    with self.assertRaisesRegex(FlexError, "active"):
      self.flex.reconcile_inventory()
    self.emit("MOR END 0\r\n")
    await task
    self.assertEqual((self.flex.processing_slot, self.flex.loading_slot), (1, 4))
    self.assertTrue(self.flex.inventory_verified)
    self.assert_occupancy_preserved()
    await self.flex.stop()
    self.assert_orientation_unknown()
    self.assertFalse(self.flex.inventory_verified)
    self.assert_occupancy_preserved()
    self.flex.reconcile_inventory()
    await self.flex.setup()
    self.assertFalse(self.flex.inventory_verified)
    self.assert_occupancy_preserved()

  async def test_all_rotations_wait_for_completion_and_preserve_tree(self) -> None:
    """ACK alone cannot publish orientation; all eight offsets include wraparound."""
    await self.ready()
    await self.wait_sent("MOR 1")
    for processing, loading in enumerate((4, 5, 6, 7, 8, 1, 2, 3), 1):
      with self.subTest(processing=processing):
        command = f"MOR {processing}"
        self.responses[command] = "MOR\r\n"
        self.flex.reconcile_inventory()
        task = asyncio.create_task(
          self.flex.run_protocol(
            replace(self.protocol, script=command + "\n").bind_plates(self.plate_bindings)
          )
        )
        await self.wait_sent(command)
        self.assert_orientation_unknown()
        self.assertTrue(self.flex.inventory_verified)
        # A telemetry event after the ACK proves that the reader processed it.
        self.emit("TEM 24 22\r\n")
        async for event in self.flex.events():
          if event.kind == "temperature":
            break
        self.assert_orientation_unknown()
        self.assertFalse(task.done())
        self.emit("MOR END 0\r\n")
        await task
        self.assertEqual(self.flex.processing_slot, processing)
        self.assertEqual(self.flex.loading_slot, loading)
        self.assertIs(self.flex.processing_holder, self.holders[processing - 1])
        self.assertIs(self.flex.loading_holder, self.holders[loading - 1])
        self.assert_occupancy_preserved()

  async def test_failed_initialization_preserves_plates_and_invalidates_certainty(self) -> None:
    """A motion error invalidates reconciled inventory without deleting its record."""
    await self.flex.setup()
    self.flex.reconcile_inventory()
    self.responses["MOR 1"] = "MOR\r\nMOR END 13\r\n"
    with self.assertRaises(FlexError):
      await self.flex.initialize()
    self.assert_orientation_unknown()
    self.assertFalse(self.flex.inventory_verified)
    self.assert_occupancy_preserved()

  async def test_interrupted_rotation_keeps_inventory_record(self) -> None:
    """Cancellation and PC abort cannot establish a new physical orientation."""
    for interruption in ("cancel", "abort", "disconnect"):
      with self.subTest(interruption=interruption):
        await self.ready()
        self.responses["MOR 7"] = "MOR\r\n"
        task = asyncio.create_task(
          self.flex.run_protocol(
            replace(self.protocol, script="MOR 7\n").bind_plates(self.plate_bindings)
          )
        )
        await self.wait_sent("MOR 7")
        if interruption == "cancel":
          task.cancel()
          with self.assertRaises(asyncio.CancelledError):
            await task
        else:
          if interruption == "abort":
            await self.flex.abort()
          else:
            self.incoming.put_nowait(OSError("USB disconnected"))
          with self.assertRaises(FlexError):
            await task
        self.assert_orientation_unknown()
        self.assertFalse(self.flex.inventory_verified)
        self.assert_occupancy_preserved()
        await self.flex.stop()

  async def test_preflight_failure_does_not_invalidate_inventory(self) -> None:
    """Rejected input cannot imply that instrument execution started."""
    await self.ready()
    self.flex.reconcile_inventory()
    with self.assertRaises(FlexProtocolError):
      await self.flex.run_protocol(
        replace(self.protocol, script="REC 0\n").bind_plates(self.plate_bindings)
      )
    self.assertTrue(self.flex.inventory_verified)
    self.assert_occupancy_preserved()

  async def test_instances_have_independent_resources_and_state(self) -> None:
    """Resource names distinguish instruments independently of their USB selection."""
    other = KingFisherFlex(serial_number="second", name="flex_b")
    self.assertNotEqual(self.flex.carousel.name, other.carousel.name)
    for tray in range(1, 9):
      self.assertIsNot(self.flex.carousel[tray], other.carousel[tray])
      self.assertIsNone(other.carousel[tray].resource)
    other.reconcile_inventory()
    await self.ready()
    self.assertTrue(other.inventory_verified)
    self.assertIsNone(other.processing_slot)
    self.assertIsNone(other.loading_slot)
    self.assertTrue(self.flex.inventory_verified)
