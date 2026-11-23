"""Mandatory, protocol-specific PLR plate designation without real hardware."""

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

from pylabrobot.resources import Plate, Resource, thermo_TS_kingfisher_96_wellplate_2000uL_Vb

from .actions import FlexAction
from .flex import KingFisherFlex
from .protocol import FlexBoundProtocol, FlexProtocolError, read_flex_bdz
from .test_helpers import HIDTestCase, bdz_bytes


class PlateBindingTests(HIDTestCase):
  """Bindings select physical resources before execution can issue any commands."""

  async def asyncSetUp(self) -> None:
    """Create two named BDZ roles and two independently named PLR resources."""
    self.mock_hid()
    self.flex = KingFisherFlex(timeout=5, operation_timeout=5)
    self.flex.io = self.io
    self.protocol = read_flex_bdz(
      bdz_bytes(
        'MOR 7\nPAU 1 "Bead Plate"\nMOR 6\nPAU 1 "Wash Plate"\n'
        'PAU 2 "Wash Plate"\nMOR 7\nPAU 2 "Bead Plate"\n',
        plates=(("beads-id", "Bead Plate"), ("wash-id", "Wash Plate")),
      )
    )
    self.beads = thermo_TS_kingfisher_96_wellplate_2000uL_Vb("physical_beads_001")
    self.wash = thermo_TS_kingfisher_96_wellplate_2000uL_Vb("physical_wash_002")

  async def asyncTearDown(self) -> None:
    """Close only the mocked connection."""
    await self.flex.stop()

  async def test_name_binding_resolves_ids_without_renaming_or_assigning(self) -> None:
    """Caller dictionary edits cannot change a prepared run's object associations."""
    requested = {"Wash Plate": self.wash, "Bead Plate": self.beads}
    bound = self.protocol.bind_plates(requested)
    self.assertIs(bound.protocol, self.protocol)
    self.assertIs(bound.plates_by_id["beads-id"], self.beads)
    self.assertIs(bound.plates_by_id["wash-id"], self.wash)
    requested.clear()
    self.assertEqual(len(bound.plates_by_id), 2)
    with self.assertRaises(TypeError):
      bound.plates_by_id["wash-id"] = self.beads  # type: ignore[index]
    self.assertEqual(self.beads.name, "physical_beads_001")
    self.assertEqual(self.wash.name, "physical_wash_002")
    self.assertIsNone(self.beads.parent)
    self.assertIsNone(self.wash.parent)
    self.io.setup.assert_not_awaited()
    self.io.write.assert_not_awaited()

  async def test_all_required_roles_must_be_designated_before_any_execution(self) -> None:
    """Missing, mistyped, unknown and duplicate-object bindings cannot start a run."""
    await self.flex.setup()
    await self.flex.initialize()
    self.flex.reconcile_inventory()
    self.flex.reconcile_inventory()
    before = list(self.commands)
    cases: tuple[tuple[dict[str, object], str], ...] = (
      ({}, "Missing.*Bead Plate.*Wash Plate"),
      ({"Bead Plate": self.beads}, "Missing.*Wash Plate"),
      ({"Bead Plate": self.beads, "wash plate": self.wash}, "Missing.*Wash Plate.*Unknown"),
      (
        {"Bead Plate": self.beads, "Wash Plate": self.wash, "Unused": self.wash},
        "Unknown.*Unused",
      ),
      ({"Bead Plate": self.beads, "Wash Plate": self.beads}, "same PLR Plate"),
      ({"Bead Plate": self.beads, "Wash Plate": None}, "Wash Plate.*PLR Plate"),
      ({"Bead Plate": self.beads, "Wash Plate": Resource("object", 1, 1, 1)}, "PLR Plate"),
      ({"beads-id": self.beads, "wash-id": self.wash}, "Missing.*Unknown"),
    )
    for mapping, error in cases:
      with self.subTest(error=error):
        with self.assertRaisesRegex(FlexProtocolError, error):
          await self.flex.run_protocol(
            self.protocol.bind_plates(cast(Mapping[str, Plate], mapping))
          )
        self.assertEqual(self.commands, before)
        self.assertEqual(self.flex.state, "ready")
        self.assertTrue(self.flex.inventory_verified)

  async def test_unbound_protocol_path_or_bytes_cannot_bypass_binding(self) -> None:
    """Legacy argument forms fail before USB access or initialization checks."""
    for source in (self.protocol, self.protocol.original_bytes, "file.bdz", Path("file.bdz")):
      with self.subTest(kind=type(source).__name__):
        with self.assertRaisesRegex(FlexProtocolError, "bindings are required"):
          await self.flex.run_protocol(cast(FlexBoundProtocol, source))
    self.io.setup.assert_not_awaited()
    self.io.write.assert_not_awaited()

  async def test_duplicate_or_empty_bdz_names_require_correction(self) -> None:
    """Name-based designation cannot silently choose between identically named roles."""
    for name, error in (("Bead Plate", "duplicate.*Bead Plate"), (" ", "nonempty")):
      with self.subTest(name=name):
        protocol = read_flex_bdz(bdz_bytes(plates=(("one", "Bead Plate"), ("two", name))))
        with self.assertRaisesRegex(FlexProtocolError, error):
          protocol.bind_plates({"Bead Plate": self.beads, name: self.wash})
    self.io.write.assert_not_awaited()

  async def test_separate_exports_keep_their_own_roles_and_resources(self) -> None:
    """Even equal IDs in different files do not reuse another run's resource mapping."""
    first = self.protocol.bind_plates({"Bead Plate": self.beads, "Wash Plate": self.wash})
    other = read_flex_bdz(
      bdz_bytes(plates=(("beads-id", "Elution Plate"), ("wash-id", "Sample Plate")))
    )
    second = other.bind_plates({"Elution Plate": self.wash, "Sample Plate": self.beads})
    self.assertIs(first.plates_by_id["beads-id"], self.beads)
    self.assertIs(second.plates_by_id["beads-id"], self.wash)
    self.assertIs(first.protocol, self.protocol)
    self.assertIs(second.protocol, other)
    with self.assertRaisesRegex(FlexProtocolError, "Missing.*Elution Plate.*Sample Plate"):
      other.bind_plates({"Bead Plate": self.beads, "Wash Plate": self.wash})

  async def test_bound_run_preserves_wire_commands_and_confirmation_modes(self) -> None:
    """Designating resources does not alter the BDZ's motion or pause semantics."""
    bound = self.protocol.bind_plates({"Bead Plate": self.beads, "Wash Plate": self.wash})
    self.physical_flex = self.flex
    self.plate_bindings = {"Bead Plate": self.beads, "Wash Plate": self.wash}
    await self.flex.setup()
    await self.flex.initialize()
    self.flex.reconcile_inventory()
    for host_confirmed in (False, True):
      with self.subTest(host_confirmed=host_confirmed):
        before = len(self.commands)
        seen = []

        async def confirm(prompt: FlexAction) -> bool:
          """Model the synthetic operator transfer before confirming completion."""
          seen.append(prompt)
          self.complete_transfer(prompt)
          return True

        await self.flex.run_protocol(bound, handle_action=confirm if host_confirmed else None)
        script = [
          command.text
          for command in self.protocol.preflight()
          if not host_confirmed or command.opcode != "PAU"
        ]
        self.assertEqual(
          self.commands[before:],
          ["EAK", "MOI 0", "MOS 0", "MOM 0", "MOH 0 100", "TEM 0 300"]
          + script
          + ['TXT "END_OF_RUN"', "TEM 0 300", "TEM 0 300"],
        )
        self.assertEqual(len(seen), 4 if host_confirmed else 0)
        self.assertIsNone(self.beads.parent)
        self.assertIsNone(self.wash.parent)

  async def test_binding_does_not_bypass_whole_protocol_preflight(self) -> None:
    """A fully designated but unsupported script still cannot start."""
    invalid = replace(self.protocol, script=self.protocol.script + "REC 0\n")
    bound = invalid.bind_plates({"Bead Plate": self.beads, "Wash Plate": self.wash})
    with self.assertRaises(FlexProtocolError):
      await self.flex.run_protocol(bound)
    self.io.write.assert_not_awaited()
