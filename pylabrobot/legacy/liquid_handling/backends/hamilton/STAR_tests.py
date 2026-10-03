# mypy: disable-error-code="assignment,attr-defined,method-assign,no-any-return,override"

import contextlib
import copy
import datetime
import sys
import unittest
import unittest.mock
from collections import Counter
from dataclasses import replace
from typing import Any, Literal, Optional, TypeVar, cast, overload

import anyio
import pytest

from pylabrobot.concurrency import AsyncExitStackWithShielding
from pylabrobot.io.usb import USB
from pylabrobot.legacy.arms.standard import CartesianCoords
from pylabrobot.legacy.liquid_handling import LiquidHandler
from pylabrobot.legacy.liquid_handling.pipette_batch_scheduling import ChannelBatch
from pylabrobot.legacy.liquid_handling.standard import GripDirection, Mix, Pickup
from pylabrobot.legacy.plate_reading import PlateReader
from pylabrobot.legacy.plate_reading.chatterbox import PlateReaderChatterboxBackend
from pylabrobot.legacy.tip_tracker import TipTracker
from pylabrobot.resources import (
  PLT_CAR_L5AC_A00,
  PLT_CAR_L5MD_A00,
  PLT_CAR_P3AC_A01,
  TIP_CAR_288_C00,
  TIP_CAR_480_A00,
  TIP_CAR_480BC_A00,
  Container,
  Coordinate,
  Lid,
  ResourceHolder,
  ResourceStack,
  TipRack,
  agenbio_1_troughplate_190mL_Fl,
  celltreat_96_wellplate_350uL_Ub,
  cor_96_wellplate_360uL_Fb,
  hamilton_96_tiprack_10uL,
  hamilton_96_tiprack_10uL_filter,
  hamilton_96_tiprack_10uL_NTR,
  hamilton_96_tiprack_50uL,
  hamilton_96_tiprack_50uL_filter,
  hamilton_96_tiprack_50uL_NTR,
  hamilton_96_tiprack_300uL,
  hamilton_96_tiprack_300uL_filter_slim,
  hamilton_96_tiprack_300uL_NTR,
  hamilton_96_tiprack_1000uL,
  hamilton_96_tiprack_1000uL_filter,
  hamilton_mfx_carrier_L5_base,
  hamilton_mfx_resourceholder_ntr,
  hamilton_mfx_tiprackholder_standard,
  hamilton_tip_carrier_L5_ntr_a00,
  no_tip_tracking,
  no_volume_tracking,
  set_tip_tracking,
)
from pylabrobot.resources.barcode import Barcode
from pylabrobot.resources.greiner import Greiner_384_wellplate_28ul_Fb
from pylabrobot.resources.hamilton import (
  STARDeck,
  STARLetDeck,
  hamilton_96_tiprack_300uL_filter,
  hamilton_core_gripper_tool,
)
from pylabrobot.testing.concurrency import AnyioTestBase

from .base import HamiltonLiquidHandler
from .hamilton_base_lifecycle_tests import (
  RecordingReplyRouter,
  RecordingUSB,
)
from .STAR_backend import (
  CommandSyntaxError,
  DriveConfiguration,
  ExtendedConfiguration,
  HamiltonNoTipError,
  HardwareError,
  Head96Information,
  MachineConfiguration,
  PipChannelInformation,
  STARBackend,
  STARFirmwareError,
  UnknownHamiltonError,
  _run_concurrently,
  iSWAPInformation,
  parse_star_fw_string,
)
from .STAR_chatterbox import (
  _DEFAULT_EXTENDED_CONFIGURATION,
  _DEFAULT_ISWAP_INFORMATION,
  _DEFAULT_MACHINE_CONFIGURATION,
  STARChatterboxBackend,
)

T = TypeVar("T")

if sys.version_info >= (3, 11):
  from builtins import BaseExceptionGroup
else:
  from exceptiongroup import BaseExceptionGroup


class TestSTARResponseParsing(unittest.TestCase):
  """Test parsing of response from Hamilton."""

  def setUp(self):
    super().setUp()
    self.star = STARBackend()

  def test_parse_response_params(self):
    parsed = parse_star_fw_string("C0QMid1111", "")
    self.assertEqual(parsed, {"id": 1111})

    parsed = parse_star_fw_string("C0QMid1111", "id####")
    self.assertEqual(parsed, {"id": 1111})

    parsed = parse_star_fw_string("C0QMid1112aaabc", "aa&&&")
    self.assertEqual(parsed, {"id": 1112, "aa": "abc"})

    parsed = parse_star_fw_string("C0QMid1112aa-21", "aa##")
    self.assertEqual(parsed, {"id": 1112, "aa": -21})

    parsed = parse_star_fw_string("C0QMid1113pqABC", "pq***")
    self.assertEqual(parsed, {"id": 1113, "pq": int("ABC", base=16)})

    with self.assertRaises(ValueError):
      # should fail with auto-added id.
      parsed = parse_star_fw_string("C0QMaaabc", "")
      self.assertEqual(parsed, "")

    with self.assertRaises(ValueError):
      parse_star_fw_string("C0QM", "id####")

    with self.assertRaises(ValueError):
      parse_star_fw_string("C0RV", "")

  def test_parse_response_no_errors(self):
    parsed = parse_star_fw_string("C0QMid1111", "")
    self.assertEqual(parsed, {"id": 1111})

    parsed = parse_star_fw_string("C0QMid1111 er00/00", "")
    self.assertEqual(parsed, {"id": 1111})

    parsed = parse_star_fw_string("C0QMid1111 er00/00 P100/00", "")
    self.assertEqual(parsed, {"id": 1111})

  def test_parse_response_master_error(self):
    with self.assertRaises(STARFirmwareError) as ctx:
      self.star.check_fw_string_error("C0QMid1111 er01/30")
    e = ctx.exception
    self.assertEqual(len(e.errors), 1)
    self.assertIn("Master", e.errors)
    self.assertIsInstance(e.errors["Master"], CommandSyntaxError)
    self.assertEqual(e.errors["Master"].message, "Unknown command")

  def test_parse_response_slave_errors(self):
    with self.assertRaises(STARFirmwareError) as ctx:
      self.star.check_fw_string_error("C0QMid1111 er99/00 P100/00 P235/00 P402/98 PG08/76")
    e = ctx.exception
    self.assertEqual(len(e.errors), 3)
    self.assertNotIn("Master", e.errors)
    self.assertNotIn("Pipetting channel 1", e.errors)

    self.assertEqual(e.errors["Pipetting channel 2"].raw_response, "35/00")
    self.assertEqual(e.errors["Pipetting channel 4"].raw_response, "02/98")
    self.assertEqual(e.errors["Pipetting channel 16"].raw_response, "08/76")

    self.assertIsInstance(e.errors["Pipetting channel 2"], UnknownHamiltonError)
    self.assertIsInstance(e.errors["Pipetting channel 4"], HardwareError)
    self.assertIsInstance(e.errors["Pipetting channel 16"], HamiltonNoTipError)

    self.assertEqual(e.errors["Pipetting channel 2"].message, "No error")
    self.assertEqual(
      e.errors["Pipetting channel 4"].message,
      "Unknown trace information code 98",
    )
    self.assertEqual(
      e.errors["Pipetting channel 16"].message,
      "Tip already picked up",
    )

  def test_parse_slave_response_errors(self):
    with self.assertRaises(STARFirmwareError) as ctx:
      self.star.check_fw_string_error("P1OQid1111er30")

    e = ctx.exception
    self.assertEqual(len(e.errors), 1)
    self.assertNotIn("Master", e.errors)
    self.assertIn("Pipetting channel 1", e.errors)
    self.assertIsInstance(e.errors["Pipetting channel 1"], UnknownHamiltonError)
    self.assertEqual(e.errors["Pipetting channel 1"].message, "Unknown command")


def _any_write_and_read_command_call(cmd):
  return unittest.mock.call(
    id_=unittest.mock.ANY,
    cmd=cmd,
    write_timeout=unittest.mock.ANY,
    read_timeout=unittest.mock.ANY,
    wait=unittest.mock.ANY,
  )


def _make_head96_information(star):
  """A representative installed-96-head record (2021 legacy) for command tests."""
  fw = datetime.date(2021, 10, 22)
  return Head96Information(
    fw_version=fw,
    x_offset=368.2,
    supports_clot_monitoring_clld=False,
    stop_disc_type="core_ii",
    instrument_type="legacy",
    head_type="96 head II",
    z_range=star._head96_resolve_z_range("legacy"),
  )


def _stub_mix96_motion(star):
  """Stub the 96-head primitives mix96 orchestrates so tests can assert the arguments it passes
  without touching firmware. Tips present; iSWAP already parked via setUp."""
  star._head96_information = _make_head96_information(star)
  star.head96_request_tip_presence = unittest.mock.AsyncMock(return_value=1)
  for method in (
    "move_all_channels_in_z_safety",
    "head96_move_to_z_safety",
    "head96_move_z",
    "head96_move_x",
    "head96_move_y",
    "head96_move_tool_z",
    "head96_experimental_aspirate",
    "head96_experimental_dispense",
  ):
    setattr(star, method, unittest.mock.AsyncMock())


class _ConcurrentCalls:
  """Record calls and release them in reverse order after every expected call has started."""

  def __init__(self, keys: list[Any]) -> None:
    self.keys = keys
    self.calls: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []
    self.completed: list[Any] = []
    self._all_started = anyio.Event()
    self._finished = {key: anyio.Event() for key in keys}

  async def run(
    self,
    key: Any,
    *args: Any,
    result: Any = None,
    error: Optional[BaseException] = None,
    **kwargs: Any,
  ) -> Any:
    self.calls.append((key, args, kwargs))
    if len(self.calls) == len(self.keys):
      self._all_started.set()
    await self._all_started.wait()
    index = self.keys.index(key)
    if index + 1 < len(self.keys):
      await self._finished[self.keys[index + 1]].wait()
    self.completed.append(key)
    self._finished[key].set()
    if error is not None:
      raise error
    return result


async def _raise_child_cancellation(
  peer_started: anyio.Event,
  cancellation_created: anyio.Event,
  captured: list[BaseException],
) -> None:
  """Raise a cancellation object after leaving the scope that created it."""
  await peer_started.wait()
  cancellation: Optional[BaseException] = None
  with anyio.CancelScope() as cancel_scope:
    cancel_scope.cancel()
    try:
      await anyio.lowlevel.checkpoint()
    except BaseException as error:
      cancellation = error
  assert cancellation is not None
  captured.append(cancellation)
  cancellation_created.set()
  raise cancellation


class TestPipChannelInformationParsing(unittest.TestCase):
  """VW (pip channel hardware-configuration) response parsing.

  Regression coverage for the IndexError on short-form VW replies
  (e.g. ``vw0 0``) returned by some post-2016 firmwares.
  """

  def test_short_form_two_fields_does_not_raise(self):
    """Short 2-field reply (the captured `vw0 0`) should parse to baseline defaults, not raise."""
    result = STARBackend._parse_pip_channel_information("P1VWid0001vw0 0")
    self.assertEqual(
      result,
      PipChannelInformation(
        channel_type="ML_STAR",
        head_type="ML_STAR",
        stop_disc_type="core_i",
        pressure_adc="Renesas_X9268",
      ),
    )

  def test_full_form_four_fields(self):
    """Full 4-field reply should parse each non-baseline code to its mapped value."""
    result = STARBackend._parse_pip_channel_information("P1VWid0001vw1 1 1 1")
    self.assertEqual(
      result,
      PipChannelInformation(
        channel_type="ML_STAR_RPC",
        head_type="ML_STAR_PLE",
        stop_disc_type="core_ii",
        pressure_adc="Analog_Devices_AD5263",
      ),
    )

  def test_head_type_code_two_maps_to_rpc(self):
    """head_type code `2` should map to ML_STAR_RPC (the otherwise-uncovered branch)."""
    result = STARBackend._parse_pip_channel_information("P1VWid0001vw0 2 0 0")
    self.assertEqual(result.head_type, "ML_STAR_RPC")

  def test_three_fields_defaults_only_the_missing_trailing_field(self):
    """Present tokens should be honored; only the absent trailing field should default.

    stop_disc_type is present (-> core_ii); pressure_adc is absent (-> default Renesas).
    """
    result = STARBackend._parse_pip_channel_information("P1VWid0001vw1 1 1")
    self.assertEqual(
      result,
      PipChannelInformation(
        channel_type="ML_STAR_RPC",
        head_type="ML_STAR_PLE",
        stop_disc_type="core_ii",
        pressure_adc="Renesas_X9268",
      ),
    )

  def test_empty_field_list_raises_value_error(self):
    """Zero fields should raise ValueError -- a malformed reply, distinct from a known short form."""
    with self.assertRaises(ValueError):
      STARBackend._parse_pip_channel_information("P1VWid0001vw")

  def test_full_form_parity_across_all_combinations(self):
    """Every 4-field reply should parse identically to the historical logic.

    Only absent fields are newly defaulted; present fields are unchanged. `legacy`
    below is the genuine pre-fix implementation, so this is a real parity oracle.
    """
    import itertools

    def legacy(resp: str) -> PipChannelInformation:
      hw = resp.split("vw")[-1].strip().split()
      return PipChannelInformation(
        channel_type="ML_STAR_RPC" if hw[0] == "1" else "ML_STAR",
        head_type="ML_STAR_PLE" if hw[1] == "1" else "ML_STAR_RPC" if hw[1] == "2" else "ML_STAR",
        stop_disc_type="core_i" if hw[2] == "0" else "core_ii",
        pressure_adc="Analog_Devices_AD5263" if hw[3] == "1" else "Renesas_X9268",
      )

    for a, b, c, d in itertools.product(["0", "1", "2"], repeat=4):
      resp = f"P1VWid0001vw{a} {b} {c} {d}"
      with self.subTest(resp=resp):
        self.assertEqual(STARBackend._parse_pip_channel_information(resp), legacy(resp))


class TestiSWAPForwardKinematics(unittest.TestCase):
  """Geometry of `STARBackend._iswap_fk` (pure FK, no I/O).

  Verifies the canonical (W, T) configurations against the docstring examples
  in `request_iswap_wrist_drive_orientation`:
    - W=FRONT (0)  + T=STRAIGHT  -> arm extends in -y (front of deck)
    - W=LEFT (-90) + T=STRAIGHT  -> arm extends in -x (left of deck)
    - W=RIGHT(+90) + T=STRAIGHT  -> arm extends in +x (right of deck)
    - W=FRONT (0)  + T=RIGHT     -> arm tip ends up to the left (-x) of rot. drive
  Uses factory-default link lengths (138 mm each) and the factory-default
  STRAIGHT angle (~-45 deg). Asserts only on the public pose contract
  (`location` + `rotation.z`).
  """

  L1 = 138.0
  L2 = 138.0
  T_STRAIGHT = -45.0  # factory-default STRAIGHT calibration (EEPROM-dependent in practice)
  Z_OFFSET = STARBackend.iswap_rotation_drive_z_offset_above_finger_mm
  BASE_X, BASE_Y, BASE_Z = 100.0, 500.0, 200.0
  # Gripper jaw width: hardware range ~71-134 mm (drive min/max increments).
  # FK doesn't read this axis, but the joint dict represents full state, so
  # use a realistic mid-range plate-grip value.
  GRIPPER_WIDTH = 90.0

  def _fk(self, w: float, t: float) -> CartesianCoords:
    joints = {
      STARBackend.iSWAPAxis.X: self.BASE_X,
      STARBackend.iSWAPAxis.Y: self.BASE_Y,
      STARBackend.iSWAPAxis.Z: self.BASE_Z,
      STARBackend.iSWAPAxis.ROTATION: w,
      STARBackend.iSWAPAxis.WRIST: t,
      STARBackend.iSWAPAxis.GRIPPER: self.GRIPPER_WIDTH,
    }
    return STARBackend._iswap_fk(
      joints=joints,
      link_1_length=self.L1,
      link_2_length=self.L2,
      wrist_straight_angle=self.T_STRAIGHT,
    )

  def test_front_straight_extends_in_minus_y(self):
    pose = self._fk(w=0.0, t=self.T_STRAIGHT)
    self.assertAlmostEqual(pose.location.x, self.BASE_X, places=6)
    self.assertAlmostEqual(pose.location.y, self.BASE_Y - (self.L1 + self.L2), places=6)
    self.assertAlmostEqual(pose.location.z, self.BASE_Z - self.Z_OFFSET, places=6)
    self.assertAlmostEqual(pose.rotation.z, -90.0, places=6)

  def test_left_straight_extends_in_minus_x(self):
    pose = self._fk(w=-90.0, t=self.T_STRAIGHT)
    self.assertAlmostEqual(pose.location.x, self.BASE_X - (self.L1 + self.L2), places=6)
    self.assertAlmostEqual(pose.location.y, self.BASE_Y, places=6)
    self.assertAlmostEqual(pose.rotation.z, -180.0, places=6)

  def test_front_right_extends_in_minus_x(self):
    """W=FRONT + T=RIGHT (-135 deg motor) -> gripper points to deck-left."""
    pose = self._fk(w=0.0, t=-135.0)
    # Link 1 points -y from base; link 2 is bent right (CW by 90 deg) -> points -x.
    self.assertAlmostEqual(pose.location.x, self.BASE_X - self.L2, places=6)
    self.assertAlmostEqual(pose.location.y, self.BASE_Y - self.L1, places=6)
    self.assertAlmostEqual(pose.rotation.z, -180.0, places=6)

  def test_reverse_folds_arm_back_onto_base_xy(self):
    """T=REVERSE (+135) folds link 2 back 180 deg from link 1 -> tip XY = base XY."""
    pose = self._fk(w=0.0, t=+135.0)
    self.assertAlmostEqual(pose.location.x, self.BASE_X, places=6)
    self.assertAlmostEqual(pose.location.y, self.BASE_Y, places=6)


class TestiSWAPAxisPredicates(unittest.TestCase):
  """Predicates on `STARBackend.iSWAPAxis` classify axes by kinematic role / unit."""

  def test_is_in_kinematic_chain(self):
    Axis = STARBackend.iSWAPAxis
    for a in (Axis.X, Axis.Y, Axis.Z, Axis.ROTATION, Axis.WRIST):
      self.assertTrue(a.is_in_kinematic_chain, f"{a.name} should be in the chain")
    self.assertFalse(Axis.GRIPPER.is_in_kinematic_chain, "GRIPPER should NOT be in the chain")


class TestiSWAPRequestJointState(unittest.IsolatedAsyncioTestCase):
  """`iswap_request_joint_state` composes the per-axis request methods into one dict."""

  def _make_backend(self) -> STARBackend:
    b = STARBackend()
    b._extended_conf = _DEFAULT_EXTENDED_CONFIGURATION
    b.iswap_rotation_drive_request_x = unittest.mock.AsyncMock(return_value=100.0)
    b.iswap_rotation_drive_request_y = unittest.mock.AsyncMock(return_value=500.0)
    b.iswap_rotation_drive_request_z = unittest.mock.AsyncMock(return_value=200.0)
    b.iswap_rotation_drive_request_angle = unittest.mock.AsyncMock(return_value=0.0)
    b.iswap_wrist_drive_request_angle = unittest.mock.AsyncMock(return_value=-45.0)
    b.iswap_gripper_request_width = unittest.mock.AsyncMock(return_value=90.0)
    return b

  async def test_returns_full_axis_dict(self):
    b = self._make_backend()
    joints = await b.iswap_request_joint_state()
    Axis = STARBackend.iSWAPAxis
    self.assertEqual(
      joints,
      {
        Axis.X: 100.0,
        Axis.Y: 500.0,
        Axis.Z: 200.0,
        Axis.ROTATION: 0.0,
        Axis.WRIST: -45.0,
        Axis.GRIPPER: 90.0,
      },
    )


class TestiSWAPRequestPose(unittest.IsolatedAsyncioTestCase):
  """`iswap_request_pose` reads joints + runs FK against the cached link lengths."""

  def _make_backend(self) -> STARBackend:
    b = STARBackend()
    b._extended_conf = _DEFAULT_EXTENDED_CONFIGURATION
    b._iswap_information = _DEFAULT_ISWAP_INFORMATION
    b.iswap_rotation_drive_request_x = unittest.mock.AsyncMock(return_value=100.0)
    b.iswap_rotation_drive_request_y = unittest.mock.AsyncMock(return_value=500.0)
    b.iswap_rotation_drive_request_z = unittest.mock.AsyncMock(return_value=200.0)
    b.iswap_rotation_drive_request_angle = unittest.mock.AsyncMock(return_value=0.0)
    b.iswap_wrist_drive_request_angle = unittest.mock.AsyncMock(return_value=-45.0)
    b.iswap_gripper_request_width = unittest.mock.AsyncMock(return_value=90.0)
    return b

  async def test_front_straight_pose(self):
    """Canonical W=0 / T=-45 / base=(100, 500, 200) -> grip ~ (100, 224, 187), yaw ~ -90°.

    Verifies the full I/O path (per-axis reads -> joint state -> FK -> pose). EEPROM
    STRAIGHT (-8859 incr) maps to ~-45.0007 deg, so the canonical -90° yaw lands at
    -89.999° and grip x picks up a ~0.002 mm offset - this is intentional per-machine
    calibration drift, not an FK bug.
    """
    b = self._make_backend()
    pose = await b.iswap_request_pose()
    self.assertIsInstance(pose, CartesianCoords)
    self.assertAlmostEqual(pose.location.x, 100.0, places=2)
    self.assertAlmostEqual(pose.location.y, 224.0, places=3)
    self.assertAlmostEqual(pose.location.z, 187.0, places=6)
    self.assertAlmostEqual(pose.rotation.z, -90.0, places=2)
    # rotation.x / y are always 0 - the gripper plane stays parallel to the deck.
    self.assertEqual(pose.rotation.x, 0.0)
    self.assertEqual(pose.rotation.y, 0.0)


class TestiSWAPInformationGuard(unittest.TestCase):
  """The `iswap_information` property raises before setup populates it."""

  def test_raises_before_setup(self):
    b = STARBackend()
    self.assertIsNone(b._iswap_information)
    with self.assertRaisesRegex(RuntimeError, "iSWAP information not loaded"):
      _ = b.iswap_information

  def test_returns_record_when_set(self):
    b = STARBackend()
    b._iswap_information = _DEFAULT_ISWAP_INFORMATION
    self.assertIs(b.iswap_information, _DEFAULT_ISWAP_INFORMATION)


class TestChatterboxiSWAPSetup(unittest.IsolatedAsyncioTestCase):
  """The STAR chatterbox lifespan populates `_iswap_information` from the
  default record (or a constructor override) when the iSWAP is installed."""

  @staticmethod
  def _make_chatterbox(**kwargs) -> STARChatterboxBackend:
    cb = STARChatterboxBackend(**kwargs)
    cb.set_deck(STARLetDeck())
    cb.set_heads({index: TipTracker(thing=f"channel {index}") for index in range(8)})
    return cb

  async def test_default_record_assigned_when_iswap_installed(self):
    cb = self._make_chatterbox()
    with self.assertWarns(DeprecationWarning):
      await cb.setup(skip_iswap=False)
    self.assertIs(cb.iswap_information, _DEFAULT_ISWAP_INFORMATION)

  async def test_constructor_override_takes_precedence(self):
    custom = iSWAPInformation(
      fw_version="custom-test",
      rotation_drive_x_offset=50.0,
      rotation_drive_y_max=700.0,
      link_1_length=140.0,
      link_2_length=140.0,
      rotation_drive_predefined_increments={
        STARBackend.RotationDriveOrientation.LEFT: -29000,
        STARBackend.RotationDriveOrientation.FRONT: 0,
        STARBackend.RotationDriveOrientation.RIGHT: 29000,
        STARBackend.RotationDriveOrientation.PARKED_RIGHT: 29500,
      },
      wrist_drive_predefined_increments={
        STARBackend.WristDriveOrientation.RIGHT: -26000,
        STARBackend.WristDriveOrientation.STRAIGHT: -8800,
        STARBackend.WristDriveOrientation.LEFT: 8800,
        STARBackend.WristDriveOrientation.REVERSE: 26000,
      },
    )
    cb = self._make_chatterbox(iswap_information=custom)
    with self.assertWarns(DeprecationWarning):
      await cb.setup()
    self.assertIs(cb.iswap_information, custom)
    self.assertEqual(cb.iswap_information.fw_version, "custom-test")
    self.assertEqual(cb.iswap_information.link_1_length, 140.0)

  async def test_skipped_when_iswap_not_installed(self):
    # Build an extended_conf with iSWAP NOT installed.
    no_iswap_conf = copy.deepcopy(_DEFAULT_EXTENDED_CONFIGURATION)
    no_iswap_conf.left_x_drive = copy.deepcopy(no_iswap_conf.left_x_drive)
    no_iswap_conf.left_x_drive.iswap_installed = False
    cb = self._make_chatterbox(extended_configuration=no_iswap_conf)
    with self.assertWarns(DeprecationWarning):
      await cb.setup()
    self.assertIsNone(cb._iswap_information)
    with self.assertRaisesRegex(RuntimeError, "iSWAP information not loaded"):
      _ = cb.iswap_information


class TestHead96DriveDefaults(unittest.IsolatedAsyncioTestCase):
  """The Y/Z drive speed/acceleration defaults are read from the machine into mutable STARBackend
  attributes at setup (and are user-overridable); the dispensing/squeezer factory facts stay on the
  frozen Head96Information record."""

  async def _setup_cb(self) -> STARChatterboxBackend:
    cb = STARChatterboxBackend()  # mocks a 2023 (2013+) head
    cb.set_deck(STARLetDeck())
    cb.set_heads({index: TipTracker(thing=f"channel {index}") for index in range(8)})
    with self.assertWarns(DeprecationWarning):
      await cb.setup()
    return cb

  async def test_setup_seeds_yz_defaults_from_machine(self):
    """setup() seeds the mutable Y/Z defaults from the machine registers; dispensing/squeezer stay
    on the frozen record with their 2013+ firmware values."""
    cb = await self._setup_cb()
    info = cb._head96_information
    assert info is not None
    # Y/Z defaults: read from the machine into mutable backend attributes.
    self.assertAlmostEqual(cb.head96_y_drive_speed_default, 390.62, places=2)
    self.assertAlmostEqual(cb.head96_y_drive_acceleration_default, 546.88, places=2)
    self.assertEqual(cb.head96_z_drive_speed_default, 85.0)
    self.assertEqual(cb.head96_z_drive_acceleration_default, 400.0)
    # Dispensing/squeezer factory defaults still live on the frozen record.
    self.assertEqual(info.dispensing_drive_speed_default, 261.1)
    self.assertAlmostEqual(info.dispensing_drive_acceleration_default, 17406.84, places=2)
    self.assertAlmostEqual(info.squeezer_drive_speed_default, 15.86, places=2)
    self.assertAlmostEqual(info.squeezer_drive_acceleration_default, 62.6, places=2)

  async def test_yz_default_is_user_overridable_and_range_checked(self):
    """A machine-seeded Y/Z default can be reassigned; an out-of-range value is rejected."""
    cb = await self._setup_cb()
    cb.head96_y_drive_speed_default = 100.0
    self.assertEqual(cb.head96_y_drive_speed_default, 100.0)
    with self.assertRaises(ValueError):
      cb.head96_y_drive_speed_default = 10_000.0  # outside y_speed_range


class TestHead96CrashRecovery(unittest.IsolatedAsyncioTestCase):
  """head96_move_stop_disk_z retracts the head to Z-safety on a firmware error then re-raises, and
  the retract - which routes back through the same primitive - cannot recurse."""

  async def asyncSetUp(self):
    self.cb = STARChatterboxBackend()
    self.cb.set_deck(STARLetDeck())
    self.cb.set_heads({index: TipTracker(thing=f"channel {index}") for index in range(8)})
    await self.cb.setup()
    assert self.cb._head96_information is not None
    z_min, z_max = self.cb._head96_information.z_range
    self.z_target = round((z_min + z_max) / 2, 1)
    self.move_za = f"{self.cb._head96_z_drive_mm_to_increment(self.z_target):05}"
    self.z_safety_za = f"{self.cb._head96_z_drive_mm_to_increment(z_max):05}"
    # head96_move_stop_disk_z snapshots the current Z speed/accel (to restore after the move); these
    # tests only exercise the ZA crash-retract path, so stub the reads with in-range values and the
    # restore writes (AA) so that send_command receives only the ZA moves under test.
    self.cb.head96_request_z_speed = unittest.mock.AsyncMock(return_value=85.0)
    self.cb.head96_request_z_acceleration = unittest.mock.AsyncMock(return_value=400.0)
    self.cb._head96_set_z_speed = unittest.mock.AsyncMock()
    self.cb._head96_set_z_acceleration = unittest.mock.AsyncMock()
    self.cb.send_command = unittest.mock.AsyncMock()

  def _crash(self, message):
    return STARFirmwareError(
      errors={
        "CoRe 96 Head": UnknownHamiltonError(
          message=message, trace_information=62, raw_response=message, raw_module="H0"
        )
      },
      raw_response=message,
    )

  async def test_crash_retracts_to_z_safety_then_reraises(self):
    """A ZA firmware error retracts the head to z_range[1] (a second ZA) before the original error
    propagates."""
    original = self._crash("z drive movement error")
    # ZA #1 is the move (crashes); ZA #2 is the safety retract (succeeds).
    self.cb.send_command.side_effect = [original, {}]

    with self.assertRaises(STARFirmwareError) as ctx:
      await self.cb.head96_move_stop_disk_z(self.z_target)

    self.assertIs(ctx.exception, original)
    za_targets = [call.kwargs["za"] for call in self.cb.send_command.await_args_list]
    self.assertEqual(za_targets, [self.move_za, self.z_safety_za])

  async def test_retract_that_also_crashes_does_not_recurse(self):
    """If the safety retract itself errors, exactly two ZA moves are sent (no recursion) and the
    ORIGINAL error re-raises, not the retract's."""
    original = self._crash("original crash")
    retract_err = self._crash("retract crash")
    # ZA #1 (move) and ZA #2 (retract) both crash; the retract must not recurse into a third ZA.
    self.cb.send_command.side_effect = [original, retract_err]

    with self.assertRaises(STARFirmwareError) as ctx:
      await self.cb.head96_move_stop_disk_z(self.z_target)

    self.assertIs(ctx.exception, original)
    self.assertEqual(self.cb.send_command.await_count, 2)


class TestiSWAPYMaxBootstrap(unittest.IsolatedAsyncioTestCase):
  """`_iswap_rotation_drive_request_y_max` runs during setup, before
  `iswap_information` exists, so it must not read it (regression: it used to,
  raising "iSWAP information not loaded" mid-`set_up_iswap`)."""

  async def test_y_max_works_before_iswap_information_set(self):
    b = STARBackend()
    b._extended_conf = _DEFAULT_EXTENDED_CONFIGURATION
    # Leave _iswap_information unset, exactly as it is during set_up_iswap().
    self.assertIsNone(b._iswap_information)
    b.send_command = unittest.mock.AsyncMock(return_value={"py": [0] * 10})

    # The regression was a raise ("iSWAP information not loaded"); a clean return
    # is the assertion.
    await b._iswap_rotation_drive_request_y_max()


class RecordingSTARBackend(STARBackend):
  """Record the STAR initialization protocol while using the real base lifespan."""

  def __init__(
    self,
    fail_at: Optional[str] = None,
    failure: Optional[BaseException] = None,
    block_at: Optional[str] = None,
  ) -> None:
    super().__init__(read_timeout=1, packet_read_timeout=1)
    self.events: list[str] = []
    io = RecordingUSB(self.events)
    self.io = cast(USB, io)
    self._reply_router = RecordingReplyRouter(io, cast(Any, self), self.events)
    self.fail_at = fail_at
    self.failure = failure
    self.block_at = block_at
    self.block_started = anyio.Event()
    self.set_deck(STARLetDeck())
    self.set_heads({index: TipTracker(thing=f"channel {index}") for index in range(8)})

  @overload
  async def _record(self, event: str) -> None: ...

  @overload
  async def _record(self, event: str, result: T) -> T: ...

  async def _record(self, event: str, result: Any = None) -> Any:
    self.events.append(event)
    if event == self.fail_at:
      if self.failure is not None:
        raise self.failure
      raise RuntimeError(f"{event} failed")
    if event == self.block_at:
      self.block_started.set()
      await anyio.sleep_forever()
    return result

  async def request_machine_configuration(self) -> MachineConfiguration:
    return await self._record("machine.configuration", _DEFAULT_MACHINE_CONFIGURATION)

  async def request_extended_configuration(self) -> ExtendedConfiguration:
    return await self._record("extended.configuration", _DEFAULT_EXTENDED_CONFIGURATION)

  async def request_instrument_initialization_status(self) -> bool:
    return await self._record("instrument.status", False)

  async def pre_initialize_instrument(self) -> None:
    await self._record("instrument.initialize")

  async def request_tip_presence(self) -> list[Optional[bool]]:
    return await self._record("pip.tip_presence", [False] * 8)

  async def initialize_pip(self) -> None:
    await self._record("pip.initialize")

  async def channels_request_y_minimum_spacing(self) -> list[float]:
    return await self._record("pip.spacing", [9.0] * 8)

  async def request_pip_channel_version(self, channel: int) -> str:
    assert channel == 0
    return await self._record("pip.version", "2023-01-01")

  async def _pip_channel_request_configuration(self, channel: int) -> PipChannelInformation:
    await self._record(f"pip.configuration.{channel}")
    return PipChannelInformation(
      channel_type="ML_STAR",
      head_type="ML_STAR",
      stop_disc_type="core_i",
      pressure_adc="Renesas_X9268",
    )

  async def request_autoload_initialization_status(self) -> bool:
    return await self._record("autoload.status", False)

  async def initialize_autoload(self) -> None:
    await self._record("autoload.initialize")

  async def park_autoload(self) -> None:
    await self._record("autoload.park")

  async def request_iswap_initialization_status(self) -> bool:
    return await self._record("iswap.status", False)

  async def initialize_iswap(self) -> None:
    await self._record("iswap.initialize")

  async def park_iswap(self, **kwargs: Any) -> None:
    await self._record("iswap.park")

  async def _iswap_rotation_drive_request_predefined_increments(self) -> dict[str, int]:
    return await self._record(
      "iswap.rotation_increments", {"left": -1, "front": 0, "right": 1, "parking": 2}
    )

  async def _iswap_wrist_drive_request_predefined_increments(self) -> dict[str, int]:
    return await self._record(
      "iswap.wrist_increments", {"right": -1, "straight": 0, "left": 1, "reverse": 2}
    )

  async def request_iswap_version(self) -> str:
    return await self._record("iswap.version", "test")

  async def _iswap_rotation_drive_request_x_offset(self) -> float:
    return await self._record("iswap.x_offset", 1.0)

  async def _iswap_rotation_drive_request_y_max(self) -> float:
    return await self._record("iswap.y_max", 2.0)

  async def iswap_request_link_1_length(self) -> float:
    return await self._record("iswap.link_1", 3.0)

  async def iswap_request_link_2_length(self) -> float:
    return await self._record("iswap.link_2", 4.0)

  async def request_core_96_head_initialization_status(self) -> bool:
    return await self._record("core96.status", False)

  async def initialize_core_96_head(self, **kwargs: Any) -> None:
    await self._record("core96.initialize")

  async def head96_request_firmware_version(self) -> datetime.date:
    return await self._record("core96.version", datetime.date(2023, 1, 1))

  async def _head96_request_configuration(self) -> tuple[str, str, str]:
    return await self._record("core96.configuration", ("0", "1", "1"))

  async def head96_request_type(self) -> str:
    return await self._record("core96.type", "96 head II")

  async def _head96_request_x_offset(self) -> float:
    return await self._record("core96.x_offset", 365.0)

  async def _head96_probe_z_max(self) -> float:
    return await self._record("core96.z_max", 342.5)

  async def head96_request_y_speed(self) -> float:
    return await self._record("core96.y_speed", 100.0)

  async def head96_request_y_acceleration(self) -> float:
    return await self._record("core96.y_acceleration", 100.0)

  async def head96_request_z_speed(self) -> float:
    return await self._record("core96.z_speed", 100.0)

  async def head96_request_z_acceleration(self) -> float:
    return await self._record("core96.z_acceleration", 100.0)


def _module_events(events: list[str], module: str) -> list[str]:
  return [event for event in events if event.startswith(f"{module}.")]


def _exception_is_reachable(error: Optional[BaseException], target: BaseException) -> bool:
  """Return whether an exception is reachable through groups or context links."""
  pending = [error] if error is not None else []
  seen: set[int] = set()
  while pending:
    current = pending.pop()
    if current is target:
      return True
    if id(current) in seen:
      continue
    seen.add(id(current))
    if isinstance(current, BaseExceptionGroup):
      pending.extend(current.exceptions)
    if current.__context__ is not None:
      pending.append(current.__context__)
  return False


class TestSTARLifecycle(AnyioTestBase):
  async def test_lifespan_preserves_setup_transcript_and_publishes_channels(self) -> None:
    backend = RecordingSTARBackend()

    async with backend:
      assert backend.num_channels == 8
      assert backend.setup_done
      transcript = [
        event for event in backend.events if not event.startswith(("transport.", "reader."))
      ]
      common_prefix = [
        "machine.configuration",
        "extended.configuration",
        "instrument.status",
        "instrument.initialize",
        "pip.tip_presence",
      ]
      assert transcript[: len(common_prefix)] == common_prefix
      assert _module_events(backend.events, "pip") == [
        "pip.tip_presence",
        "pip.initialize",
        "pip.spacing",
        "pip.version",
        *[f"pip.configuration.{index}" for index in range(8)],
      ]
      assert _module_events(backend.events, "autoload") == [
        "autoload.status",
        "autoload.initialize",
        "autoload.park",
      ]
      assert _module_events(backend.events, "iswap") == [
        "iswap.status",
        "iswap.initialize",
        "iswap.park",
        "iswap.rotation_increments",
        "iswap.wrist_increments",
        "iswap.version",
        "iswap.x_offset",
        "iswap.y_max",
        "iswap.link_1",
        "iswap.link_2",
      ]
      assert _module_events(backend.events, "core96") == [
        "core96.status",
        "core96.initialize",
        "core96.version",
        "core96.configuration",
        "core96.type",
        "core96.x_offset",
        "core96.z_max",
        "core96.y_speed",
        "core96.y_acceleration",
        "core96.z_speed",
        "core96.z_acceleration",
      ]
      expected_concurrent_events = [
        *_module_events(backend.events, "autoload"),
        *_module_events(backend.events, "pip")[1:],
        *_module_events(backend.events, "iswap"),
        *_module_events(backend.events, "core96"),
      ]
      assert Counter(transcript[len(common_prefix) :]) == Counter(expected_concurrent_events)

    assert not backend.setup_done
    assert (
      backend.events.index("reader.stop")
      < backend.events.index("reader.exit")
      < backend.events.index("transport.exit")
    )

  async def test_setup_options_are_forwarded_with_false_defaults(self) -> None:
    backend = RecordingSTARBackend()
    async with backend.lifespan(
      skip_instrument_initialization=True,
      skip_pip=True,
      skip_autoload=True,
      skip_iswap=True,
      skip_core96_head=True,
    ):
      pass

    assert "instrument.initialize" not in backend.events
    assert _module_events(backend.events, "pip") == ["pip.tip_presence"]
    assert _module_events(backend.events, "autoload") == []
    assert _module_events(backend.events, "iswap") == []
    assert _module_events(backend.events, "core96") == []

  @pytest.mark.parametrize(
    "kwargs",
    [
      {"unknown": False},
      {"skip_instrument_initialization": 1},
      {"skip_pip": None},
      {"skip_autoload": "false"},
      {"skip_iswap": 0},
      {"skip_core96_head": object()},
    ],
  )
  async def test_invalid_options_fail_before_transport_entry(self, kwargs: dict[str, Any]) -> None:
    backend = RecordingSTARBackend()
    with pytest.raises(TypeError):
      async with backend.lifespan(**kwargs):
        pytest.fail("invalid entry reached the body")
    assert "transport.enter" not in backend.events

  @pytest.mark.parametrize(
    "phase", ["machine.configuration", "iswap.initialize", "core96.z_acceleration"]
  )
  async def test_firmware_failure_preserves_exact_exception_at_each_phase(self, phase: str) -> None:
    firmware_error = STARFirmwareError(errors={}, raw_response=phase)
    backend = RecordingSTARBackend(fail_at=phase, failure=firmware_error)
    with pytest.raises(STARFirmwareError) as error_info:
      async with backend:
        pytest.fail("failed entry reached the body")

    assert error_info.value is firmware_error
    assert (
      backend.events.index("reader.stop")
      < backend.events.index("reader.exit")
      < backend.events.index("transport.exit")
    )
    assert not backend.setup_done

  async def test_genuinely_concurrent_failures_remain_grouped(self) -> None:
    autoload_error = STARFirmwareError(errors={}, raw_response="autoload")
    pip_error = STARFirmwareError(errors={}, raw_response="pip")

    class ConcurrentFailureSTARBackend(RecordingSTARBackend):
      def __init__(self) -> None:
        super().__init__()
        self.ready_count = 0
        self.both_ready = anyio.Event()

      async def _raise_when_both_ready(self, error: STARFirmwareError) -> None:
        self.ready_count += 1
        if self.ready_count == 2:
          self.both_ready.set()
        with anyio.CancelScope(shield=True):
          await self.both_ready.wait()
          raise error

      async def request_autoload_initialization_status(self) -> bool:
        await self._raise_when_both_ready(autoload_error)
        raise AssertionError("unreachable")

      async def initialize_pip(self) -> None:
        await self._raise_when_both_ready(pip_error)

    backend = ConcurrentFailureSTARBackend()
    with pytest.raises(BaseExceptionGroup) as error_info:
      async with backend:
        pytest.fail("failed entry reached the body")

    assert len(error_info.value.exceptions) == 2
    assert autoload_error in error_info.value.exceptions
    assert pip_error in error_info.value.exceptions

  async def test_lifespan_body_preserves_singleton_exception_group(self) -> None:
    backend = RecordingSTARBackend()
    body_error = RuntimeError("body failed")
    body_group = BaseExceptionGroup("body failure", [body_error])

    with pytest.raises(BaseExceptionGroup) as error_info:
      async with backend:
        raise body_group

    observed_group = error_info.value
    while observed_group.message != body_group.message:
      assert len(observed_group.exceptions) == 1
      observed_group = observed_group.exceptions[0]
      assert isinstance(observed_group, BaseExceptionGroup)
    assert type(observed_group) is type(body_group)
    assert observed_group.exceptions == (body_error,)

  async def test_setup_failure_does_not_unwrap_cleanup_exception_group(self) -> None:
    setup_error = STARFirmwareError(errors={}, raw_response="machine.configuration")
    cleanup_error = RuntimeError("transport cleanup failed")
    cleanup_group = BaseExceptionGroup("transport cleanup failure", [cleanup_error])
    backend = RecordingSTARBackend(
      fail_at="machine.configuration",
      failure=setup_error,
    )
    cast(RecordingUSB, backend.io).exit_error = cleanup_group

    with pytest.raises(BaseExceptionGroup) as error_info:
      async with backend:
        pytest.fail("failed entry reached the body")

    assert error_info.value is cleanup_group
    assert error_info.value.exceptions == (cleanup_error,)
    assert _exception_is_reachable(error_info.value.__context__, setup_error)

  async def test_cancellation_during_entry_cleans_up_transport(self) -> None:
    backend = RecordingSTARBackend(block_at="iswap.initialize")

    async def enter() -> None:
      async with backend:
        pytest.fail("cancelled entry reached the body")

    async with anyio.create_task_group() as task_group:
      task_group.start_soon(enter)
      await backend.block_started.wait()
      task_group.cancel_scope.cancel()

    assert (
      backend.events.index("reader.stop")
      < backend.events.index("reader.exit")
      < backend.events.index("transport.exit")
    )
    assert not backend.is_active

  async def test_sequential_reuse_repeats_setup_and_resets_setup_done(self) -> None:
    backend = RecordingSTARBackend()
    for _ in range(2):
      async with backend:
        assert backend.setup_done
        assert backend.num_channels == 8
      assert not backend.setup_done

    assert backend.events.count("machine.configuration") == 2
    assert backend.events.count("transport.enter") == 2
    assert backend.events.count("transport.exit") == 2

  async def test_autoload_overlaps_ordered_arm_setup(self) -> None:
    class ConcurrentSTARBackend(RecordingSTARBackend):
      def __init__(self) -> None:
        super().__init__()
        self.autoload_started = anyio.Event()
        self.pip_started = anyio.Event()

      async def request_autoload_initialization_status(self) -> bool:
        self.events.append("autoload.started")
        self.autoload_started.set()
        await self.pip_started.wait()
        return False

      async def initialize_pip(self) -> None:
        await self.autoload_started.wait()
        self.events.append("pip.initialize")
        self.pip_started.set()

    backend = ConcurrentSTARBackend()
    with anyio.fail_after(1):
      async with backend:
        pass

    arm_events = [
      event.split(".", 1)[0]
      for event in backend.events
      if event in {"pip.initialize", "iswap.initialize", "core96.initialize"}
    ]
    assert arm_events == ["pip", "iswap", "core96"]
    assert backend.events.index("autoload.started") < backend.events.index("pip.initialize")


class TestSTAROperationConcurrency(AnyioTestBase):
  async def test_minimum_spacing_is_concurrent_and_ordered_by_channel(self) -> None:
    backend = STARBackend()
    backend._num_channels = 3
    calls = _ConcurrentCalls([0, 1, 2])

    async def request(channel_idx: int) -> float:
      return await calls.run(channel_idx, result=[1.5, 2.5, 3.5][channel_idx])

    backend.channel_request_y_minimum_spacing = request
    with anyio.fail_after(1):
      result = await backend.channels_request_y_minimum_spacing()

    assert result == [1.5, 2.5, 3.5]
    assert {key for key, _, _ in calls.calls} == {0, 1, 2}
    assert calls.completed == [2, 1, 0]

  async def test_minimum_spacing_preserves_operation_exception(self) -> None:
    backend = STARBackend()
    backend._num_channels = 2
    error = RuntimeError("spacing failed")
    calls = _ConcurrentCalls([0, 1])

    async def request(channel_idx: int) -> float:
      return await calls.run(channel_idx, result=1.0, error=error if channel_idx == 1 else None)

    backend.channel_request_y_minimum_spacing = request
    with pytest.raises(RuntimeError) as error_info:
      await backend.channels_request_y_minimum_spacing()
    assert error_info.value is error
    assert {key for key, _, _ in calls.calls} == {0, 1}

  async def test_minimum_spacing_defers_exact_child_cancellation_until_sibling_finishes(
    self,
  ) -> None:
    backend = STARBackend()
    backend._num_channels = 2
    peer_started = anyio.Event()
    cancellation_created = anyio.Event()
    sibling_completed = anyio.Event()
    captured: list[BaseException] = []

    async def request(channel_idx: int) -> float:
      if channel_idx == 0:
        await _raise_child_cancellation(peer_started, cancellation_created, captured)
        raise AssertionError("unreachable")
      peer_started.set()
      await cancellation_created.wait()
      await anyio.lowlevel.checkpoint()
      sibling_completed.set()
      return 2.5

    backend.channel_request_y_minimum_spacing = request
    observed: Optional[BaseException] = None
    try:
      await backend.channels_request_y_minimum_spacing()
    except BaseException as error:
      observed = error

    assert observed is captured[0]
    assert sibling_completed.is_set()

  async def test_cycle_counts_are_concurrent_and_ordered_by_channel(self) -> None:
    backend = STARBackend()
    backend._num_channels = 3
    calls = _ConcurrentCalls([0, 1, 2])
    results = [
      {
        "tip_pick_up_cycles": index,
        "tip_discard_cycles": index + 10,
        "aspiration_cycles": index + 20,
        "dispensing_cycles": index + 30,
      }
      for index in range(3)
    ]

    async def request(channel_idx: int) -> STARBackend.ChannelCycleCounts:
      return await calls.run(channel_idx, result=results[channel_idx])

    backend.channel_request_cycle_counts = request
    with anyio.fail_after(1):
      result = await backend.channels_request_cycle_counts()

    assert result == results
    assert {key for key, _, _ in calls.calls} == {0, 1, 2}
    assert calls.completed == [2, 1, 0]

  async def test_cycle_counts_preserve_operation_exception(self) -> None:
    backend = STARBackend()
    backend._num_channels = 2
    error = RuntimeError("cycle count failed")
    calls = _ConcurrentCalls([0, 1])

    async def request(channel_idx: int) -> STARBackend.ChannelCycleCounts:
      result: STARBackend.ChannelCycleCounts = {
        "tip_pick_up_cycles": 0,
        "tip_discard_cycles": 0,
        "aspiration_cycles": 0,
        "dispensing_cycles": 0,
      }
      return await calls.run(channel_idx, result=result, error=error if channel_idx == 1 else None)

    backend.channel_request_cycle_counts = request
    with pytest.raises(RuntimeError) as error_info:
      await backend.channels_request_cycle_counts()
    assert error_info.value is error
    assert {key for key, _, _ in calls.calls} == {0, 1}

  async def test_prepare_batched_moves_all_idle_channels_concurrently(self) -> None:
    backend = STARBackend()
    backend.set_deck(STARLetDeck())
    backend._num_channels = 3
    calls = _ConcurrentCalls([1, 2])

    async def request_tip_presence() -> list[bool]:
      return [True, True, True]

    async def request_tip_length(channel_idx: int) -> float:
      assert channel_idx == 0
      return 8.0

    async def move_idle(**kwargs: Any) -> None:
      await calls.run(kwargs["channel_idx"], **kwargs)

    async def position_active(positions: dict[int, float]) -> None:
      assert positions == {0: 210.0}

    backend.request_tip_presence = request_tip_presence
    backend.request_tip_len_on_channel = request_tip_length
    backend.move_channel_stop_disk_z = move_idle
    backend.position_channels_in_z_direction = position_active
    with unittest.mock.patch(f"{STARBackend.__module__}.plan_batches", return_value=[]):
      with anyio.fail_after(1):
        result = await backend._prepare_batched(
          containers=[cast(Container, object())],
          use_channels=[0],
          min_traverse_height_at_beginning_of_command=210.0,
        )

    assert result == ([0], [8.0], [])
    assert {key for key, _, _ in calls.calls} == {1, 2}
    assert all(kwargs["z"] == backend.MAXIMUM_CHANNEL_Z_POSITION for _, _, kwargs in calls.calls)
    assert calls.completed == [2, 1]

  async def test_prepare_batched_preserves_idle_move_exception(self) -> None:
    backend = STARBackend()
    backend.set_deck(STARLetDeck())
    backend._num_channels = 3
    error = RuntimeError("idle move failed")
    calls = _ConcurrentCalls([1, 2])

    async def move_idle(**kwargs: Any) -> None:
      channel_idx = kwargs["channel_idx"]
      await calls.run(channel_idx, error=error if channel_idx == 2 else None, **kwargs)

    backend.request_tip_presence = unittest.mock.AsyncMock(return_value=[True, True, True])
    backend.request_tip_len_on_channel = unittest.mock.AsyncMock(return_value=8.0)
    backend.move_channel_stop_disk_z = move_idle
    with unittest.mock.patch(f"{STARBackend.__module__}.plan_batches", return_value=[]):
      with pytest.raises(RuntimeError) as error_info:
        await backend._prepare_batched(
          containers=[cast(Container, object())],
          use_channels=[0],
          min_traverse_height_at_beginning_of_command=210.0,
        )
    assert error_info.value is error
    assert {key for key, _, _ in calls.calls} == {1, 2}

  async def test_lld_runs_batch_members_concurrently_and_keeps_indexed_results(self) -> None:
    backend = STARBackend()
    calls = _ConcurrentCalls([2, 0])

    async def detect(**kwargs: Any) -> None:
      await calls.run(kwargs["channel_idx"], **kwargs)

    backend._move_z_drive_to_liquid_surface_using_clld = detect
    backend.request_pip_height_last_lld = unittest.mock.AsyncMock(
      return_value=[100.0, 101.0, 102.0]
    )
    batch = ChannelBatch(x_position=1.0, indices=[1, 0], channels=[2, 0])
    with anyio.fail_after(1):
      result = await backend._run_lld_on_channel_batch(
        batch=batch,
        containers=[cast(Container, object()), cast(Container, object())],
        tip_lengths=[8.0, 8.0],
        z_cavity_bottom=[10.0, 20.0],
        z_top=[30.0, 40.0],
        lld_mode=[backend.LLDMode.GAMMA, backend.LLDMode.GAMMA],
        search_speed=7.5,
        n_replicates=1,
      )

    assert result == {1: [102.0], 0: [100.0]}
    assert calls.completed == [0, 2]
    assert {key for key, _, _ in calls.calls} == {0, 2}
    assert {key: kwargs for key, _, kwargs in calls.calls} == {
      2: {
        "channel_idx": 2,
        "lowest_immers_pos": 20.0,
        "start_pos_search": 45.0,
        "channel_speed": 7.5,
      },
      0: {
        "channel_idx": 0,
        "lowest_immers_pos": 10.0,
        "start_pos_search": 35.0,
        "channel_speed": 7.5,
      },
    }

  async def test_lld_preserves_returned_operation_exception(self) -> None:
    backend = STARBackend()
    error = RuntimeError("lld failed")
    calls = _ConcurrentCalls([0, 1])

    async def detect(**kwargs: Any) -> None:
      channel_idx = kwargs["channel_idx"]
      await calls.run(channel_idx, error=error if channel_idx == 1 else None, **kwargs)

    backend._move_z_drive_to_liquid_surface_using_clld = detect
    backend.request_pip_height_last_lld = unittest.mock.AsyncMock(return_value=[10.0, 20.0])
    batch = ChannelBatch(x_position=1.0, indices=[0, 1], channels=[0, 1])
    with pytest.raises(RuntimeError) as error_info:
      await backend._run_lld_on_channel_batch(
        batch=batch,
        containers=[cast(Container, object()), cast(Container, object())],
        tip_lengths=[8.0, 8.0],
        z_cavity_bottom=[0.0, 0.0],
        z_top=[10.0, 10.0],
        lld_mode=[backend.LLDMode.GAMMA, backend.LLDMode.GAMMA],
        search_speed=5.0,
        n_replicates=1,
      )
    assert error_info.value is error
    assert {key for key, _, _ in calls.calls} == {0, 1}

  async def test_lld_defers_exact_child_cancellation_instead_of_using_empty_result_slot(
    self,
  ) -> None:
    backend = STARBackend()
    peer_started = anyio.Event()
    cancellation_created = anyio.Event()
    sibling_completed = anyio.Event()
    captured: list[BaseException] = []

    async def detect(**kwargs: Any) -> None:
      if kwargs["channel_idx"] == 0:
        await _raise_child_cancellation(peer_started, cancellation_created, captured)
        raise AssertionError("unreachable")
      peer_started.set()
      await cancellation_created.wait()
      await anyio.lowlevel.checkpoint()
      sibling_completed.set()

    backend._move_z_drive_to_liquid_surface_using_clld = detect
    backend.request_pip_height_last_lld = unittest.mock.AsyncMock(return_value=[10.0, 20.0])
    batch = ChannelBatch(x_position=1.0, indices=[0, 1], channels=[0, 1])
    observed: Optional[BaseException] = None
    try:
      await backend._run_lld_on_channel_batch(
        batch=batch,
        containers=[cast(Container, object()), cast(Container, object())],
        tip_lengths=[8.0, 8.0],
        z_cavity_bottom=[0.0, 0.0],
        z_top=[10.0, 10.0],
        lld_mode=[backend.LLDMode.GAMMA, backend.LLDMode.GAMMA],
        search_speed=5.0,
        n_replicates=1,
      )
    except BaseException as error:
      observed = error

    assert observed is captured[0]
    assert sibling_completed.is_set()
    backend.request_pip_height_last_lld.assert_not_awaited()

  async def test_empty_tips_forwards_arguments_to_concurrent_channel_commands(self) -> None:
    backend = STARBackend()
    backend._num_channels = 4
    calls = _ConcurrentCalls([3, 1])

    async def empty_tip(**kwargs: Any) -> None:
      await calls.run(kwargs["channel_idx"], **kwargs)

    backend.empty_tip = empty_tip
    with anyio.fail_after(1):
      await backend.empty_tips(
        channels=[3, 1],
        vol=123.0,
        flow_rate=45.0,
        acceleration=678.0,
        current_limit=4,
        reset_dispensing_drive_after=False,
      )

    assert calls.completed == [1, 3]
    assert {key: kwargs for key, _, kwargs in calls.calls} == {
      3: {
        "channel_idx": 3,
        "vol": 123.0,
        "flow_rate": 45.0,
        "acceleration": 678.0,
        "current_limit": 4,
        "reset_dispensing_drive_after": False,
      },
      1: {
        "channel_idx": 1,
        "vol": 123.0,
        "flow_rate": 45.0,
        "acceleration": 678.0,
        "current_limit": 4,
        "reset_dispensing_drive_after": False,
      },
    }

  async def test_empty_tips_preserves_channel_exception(self) -> None:
    backend = STARBackend()
    backend._num_channels = 2
    error = RuntimeError("empty failed")
    calls = _ConcurrentCalls([0, 1])

    async def empty_tip(**kwargs: Any) -> None:
      channel_idx = kwargs["channel_idx"]
      await calls.run(channel_idx, error=error if channel_idx == 1 else None, **kwargs)

    backend.empty_tip = empty_tip
    with pytest.raises(RuntimeError) as error_info:
      await backend.empty_tips(channels=[0, 1])
    assert error_info.value is error
    assert {key for key, _, _ in calls.calls} == {0, 1}

  async def test_empty_tips_defers_exact_child_cancellation_until_sibling_finishes(self) -> None:
    backend = STARBackend()
    backend._num_channels = 2
    peer_started = anyio.Event()
    cancellation_created = anyio.Event()
    sibling_completed = anyio.Event()
    captured: list[BaseException] = []

    async def empty_tip(**kwargs: Any) -> None:
      if kwargs["channel_idx"] == 0:
        await _raise_child_cancellation(peer_started, cancellation_created, captured)
        raise AssertionError("unreachable")
      peer_started.set()
      await cancellation_created.wait()
      await anyio.lowlevel.checkpoint()
      sibling_completed.set()

    backend.empty_tip = empty_tip
    observed: Optional[BaseException] = None
    try:
      await backend.empty_tips(channels=[0, 1])
    except BaseException as error:
      observed = error

    assert observed is captured[0]
    assert sibling_completed.is_set()

  async def test_mix96_moves_x_and_y_concurrently_with_original_arguments(self) -> None:
    backend = STARBackend()
    _stub_mix96_motion(backend)
    calls = _ConcurrentCalls(["x", "y"])

    async def move_x(*args: Any, **kwargs: Any) -> None:
      await calls.run("x", *args, **kwargs)

    async def move_y(*args: Any, **kwargs: Any) -> None:
      await calls.run("y", *args, **kwargs)

    backend.head96_move_x = move_x
    backend.head96_move_y = move_y
    with anyio.fail_after(1):
      await backend.mix96_at_coordinate(
        Mix(volume=50.0, repetitions=0, flow_rate=100.0),
        a1_coordinate=Coordinate(500.0, 150.0, 100.0),
        blowout_air_volume=0.0,
      )

    assert calls.completed == ["y", "x"]
    assert {key: (args, kwargs) for key, args, kwargs in calls.calls} == {
      "x": ((500.0,), {"acceleration_level": 1}),
      "y": ((150.0,), {}),
    }

  async def test_mix96_preserves_axis_exception(self) -> None:
    backend = STARBackend()
    _stub_mix96_motion(backend)
    error = RuntimeError("y move failed")
    calls = _ConcurrentCalls(["x", "y"])

    async def move_x(*args: Any, **kwargs: Any) -> None:
      await calls.run("x", *args, **kwargs)

    async def move_y(*args: Any, **kwargs: Any) -> None:
      await calls.run("y", *args, error=error, **kwargs)

    backend.head96_move_x = move_x
    backend.head96_move_y = move_y
    with pytest.raises(RuntimeError) as error_info:
      await backend.mix96_at_coordinate(
        Mix(volume=50.0, repetitions=0, flow_rate=100.0),
        a1_coordinate=Coordinate(500.0, 250.0, 100.0),
        blowout_air_volume=0.0,
      )
    assert error_info.value is error
    assert {key for key, _, _ in calls.calls} == {"x", "y"}

  async def test_parent_cancellation_cancels_owned_channel_requests_and_propagates(self) -> None:
    backend = STARBackend()
    backend._num_channels = 2
    all_started = anyio.Event()
    blocker = anyio.Event()
    started: list[int] = []
    cancelled: list[int] = []
    invocation_cancelled: list[BaseException] = []
    invocation_completed = anyio.Event()

    async def request(channel_idx: int) -> float:
      started.append(channel_idx)
      if len(started) == 2:
        all_started.set()
      try:
        await blocker.wait()
      except anyio.get_cancelled_exc_class():
        cancelled.append(channel_idx)
        raise
      return float(channel_idx)

    async def invoke() -> None:
      try:
        await backend.channels_request_y_minimum_spacing()
      except anyio.get_cancelled_exc_class() as error:
        invocation_cancelled.append(error)
        raise
      invocation_completed.set()

    backend.channel_request_y_minimum_spacing = request
    async with anyio.create_task_group() as task_group:
      task_group.start_soon(invoke)
      await all_started.wait()
      task_group.cancel_scope.cancel()

    assert len(invocation_cancelled) == 1
    assert set(cancelled) == {0, 1}
    assert not invocation_completed.is_set()

  async def test_return_exceptions_never_returns_custom_fatal_base_exception(self) -> None:
    class FatalOperationError(BaseException):
      pass

    fatal = FatalOperationError("fatal operation outcome")

    async def fail() -> None:
      raise fatal

    async def succeed() -> str:
      return "completed"

    with pytest.raises(BaseExceptionGroup) as error_info:
      await _run_concurrently([fail(), succeed()], return_exceptions=True)

    assert _exception_is_reachable(error_info.value, fatal)


class TestSTARTiming(AnyioTestBase):
  async def test_core96_idle_poll_sleeps_before_each_probe_until_success(self, monkeypatch) -> None:
    backend = STARBackend()
    clock = iter([0.0, 0.0, 5.0])
    sleeps: list[float] = []
    commands: list[tuple[str, str, int]] = []
    busy = STARFirmwareError(
      errors={
        "CoRe 96 Head": CommandSyntaxError(
          message="No parallel processes permitted",
          trace_information=40,
          raw_response="40",
          raw_module="H0",
        )
      },
      raw_response="busy",
    )

    async def sleep(duration: float) -> None:
      sleeps.append(duration)

    async def send_command(module: str, command: str, read_timeout: int) -> None:
      commands.append((module, command, read_timeout))
      if len(commands) == 1:
        raise busy

    monkeypatch.setattr(anyio, "current_time", lambda: next(clock))
    monkeypatch.setattr(anyio, "sleep", sleep)
    backend.send_command = send_command
    await backend._core96_wait_for_idle(timeout=10.0, poll_interval=5.0)

    assert sleeps == [5.0, 5.0]
    assert commands == [("C0", "EV", 10), ("C0", "EV", 10)]

  async def test_core96_idle_poll_stops_when_elapsed_time_reaches_timeout(
    self, monkeypatch
  ) -> None:
    backend = STARBackend()
    clock = iter([0.0, 0.0, 5.0, 10.0])
    sleeps: list[float] = []
    busy = STARFirmwareError(
      errors={
        "CoRe 96 Head": CommandSyntaxError(
          message="No parallel processes permitted",
          trace_information=40,
          raw_response="40",
          raw_module="H0",
        )
      },
      raw_response="busy",
    )

    async def sleep(duration: float) -> None:
      sleeps.append(duration)

    async def send_command(**kwargs: Any) -> None:
      assert kwargs == {"module": "C0", "command": "EV", "read_timeout": 10}
      raise busy

    monkeypatch.setattr(anyio, "current_time", lambda: next(clock))
    monkeypatch.setattr(anyio, "sleep", sleep)
    backend.send_command = send_command
    with pytest.raises(TimeoutError, match="did not become idle"):
      await backend._core96_wait_for_idle(timeout=10.0, poll_interval=5.0)
    assert sleeps == [5.0, 5.0]

  async def test_mix96_settles_once_before_retracting(self, monkeypatch) -> None:
    backend = STARBackend()
    _stub_mix96_motion(backend)
    events: list[tuple[str, float]] = []

    async def sleep(duration: float) -> None:
      events.append(("sleep", duration))

    async def move_tool_z(z: float, speed: float) -> None:
      events.append(("z", z))

    monkeypatch.setattr(anyio, "sleep", sleep)
    backend.head96_move_tool_z = move_tool_z
    await backend.mix96_at_coordinate(
      Mix(volume=50.0, repetitions=0, flow_rate=100.0),
      a1_coordinate=Coordinate(500.0, 250.0, 100.0),
      blowout_air_volume=0.0,
      settling_time=2.5,
    )

    assert events == [("z", 105.0), ("z", 100.0), ("sleep", 2.5), ("z", 105.0)]

  async def test_carrier_wait_sleeps_between_presence_checks_and_stops_when_complete(
    self, monkeypatch
  ) -> None:
    backend = STARBackend()
    deck = STARLetDeck()
    deck.assign_child_resource(TIP_CAR_480_A00(name="tips"), rails=1)
    backend.set_deck(deck)
    detections = iter([[], [2]])
    presence_checks = 0
    sleeps: list[float] = []
    indicators: list[tuple[list[bool], list[bool]]] = []

    async def request_presence() -> list[int]:
      nonlocal presence_checks
      presence_checks += 1
      return next(detections)

    async def set_indicators(bit_pattern: list[bool], blink_pattern: list[bool]) -> None:
      indicators.append((bit_pattern, blink_pattern))

    async def sleep(duration: float) -> None:
      sleeps.append(duration)

    backend.request_presence_of_carriers_on_deck = request_presence
    backend.set_loading_indicators = set_indicators
    monkeypatch.setattr(anyio, "sleep", sleep)
    await backend.verify_and_wait_for_carriers(check_interval=0.25)

    assert presence_checks == 2
    assert sleeps == [0.25]
    assert len(indicators) == 2
    assert any(indicators[0][0]) and any(indicators[0][1])
    assert indicators[1] == ([False] * 54, [False] * 54)


class TestSTARChatterboxLifecycle(AnyioTestBase):
  async def test_chatterbox_entry_is_resource_free_and_reusable(self) -> None:
    backend = STARChatterboxBackend()
    backend.set_deck(STARLetDeck())
    backend.set_heads({index: TipTracker(thing=f"channel {index}") for index in range(8)})
    events: list[str] = []
    io = RecordingUSB(events)
    backend.io = cast(USB, io)
    router = RecordingReplyRouter(io, cast(Any, backend), events)
    backend._reply_router = router

    for _ in range(2):
      async with backend:
        assert backend.setup_done
        assert backend.num_channels == 8
        assert backend.iswap_information is _DEFAULT_ISWAP_INFORMATION
      assert not backend.setup_done
      assert events == []
      assert router.run_count == 0


class TestSTARUSBComms(AnyioTestBase):
  """Test that USB data is parsed correctly."""

  async def _enter_lifespan(self, stack: AsyncExitStackWithShielding, **kwargs: Any) -> None:
    self.star = STARBackend(read_timeout=1, packet_read_timeout=1)
    self.star.set_deck(STARLetDeck())
    self.star.set_heads({index: TipTracker(thing=f"channel {index}") for index in range(8)})
    io = RecordingUSB([])
    self.star.io = cast(USB, io)
    self.star._reply_router = RecordingReplyRouter(io, cast(Any, self.star), [])
    await HamiltonLiquidHandler._enter_lifespan(self.star, stack)

  async def test_send_command_correct_response(self):
    io = cast(RecordingUSB, self.star.io)
    await io.publish("C0QMid0001")
    resp = await self.star.send_command("C0", command="QM", fmt="id####")
    assert resp == {"id": 1}

  async def test_send_command_wrong_id(self):
    io = cast(RecordingUSB, self.star.io)
    await io.publish("C0QMid0002")
    with pytest.raises(TimeoutError):
      await self.star.send_command("C0", command="QM", fmt="id####")

  async def test_send_command_plaintext_response(self):
    io = cast(RecordingUSB, self.star.io)
    await io.publish("this is plaintext")
    with pytest.raises(TimeoutError):
      await self.star.send_command("C0", command="QM", fmt="id####")


class STARTestBackend(STARBackend):
  """Provide initialized STAR state without acquiring hardware resources."""

  async def _enter_lifespan(
    self, stack: AsyncExitStackWithShielding, **backend_kwargs: Any
  ) -> None:
    if backend_kwargs:
      raise TypeError("STARTestBackend does not accept lifespan options")
    self._validate_lifespan_context()
    self._num_channels = 8
    self._machine_conf = _DEFAULT_MACHINE_CONFIGURATION
    if self._extended_conf is None:
      self._extended_conf = _DEFAULT_EXTENDED_CONFIGURATION
    self._core_parked = True
    self._iswap_parked = True
    self._head96_installed = True
    self._setup_done = True
    stack.callback(self._reset_setup_state)

  def _reset_setup_state(self) -> None:
    self._setup_done = False
    self._num_channels = None


class STARCommandCatcher(STARTestBackend):
  """Mock backend for star that catches commands instead of sending them to a machine."""

  def __init__(self):
    super().__init__()
    self.commands = []

  async def send_command(  # type: ignore
    self,
    module,
    command,
    auto_id=True,
    tip_pattern=None,
    fmt="",
    read_timeout=0,
    write_timeout=0,
    **kwargs,
  ):
    cmd, _ = self._assemble_command(
      module=module, command=command, auto_id=auto_id, tip_pattern=tip_pattern, **kwargs
    )
    self.commands.append(cmd)


class TestSTARCommandCatcherLifecycle(AnyioTestBase):
  async def test_command_catcher_is_resource_free_and_reusable(self) -> None:
    backend = STARCommandCatcher()
    backend.set_deck(STARLetDeck())
    backend.set_heads({index: TipTracker(thing=f"channel {index}") for index in range(8)})
    events: list[str] = []
    io = RecordingUSB(events)
    backend.io = cast(USB, io)
    router = RecordingReplyRouter(io, cast(Any, backend), events)
    backend._reply_router = router

    for _ in range(2):
      async with backend:
        assert backend.setup_done
        assert backend.num_channels == 8
      assert not backend.setup_done
      assert events == []
      assert router.run_count == 0


class TestSTARCoreGripperRegistration(unittest.IsolatedAsyncioTestCase):
  """Grippers require an existing firmware definition."""

  async def test_named_deck_gripper_pickup(self):
    """Named deck mounts and their tools remain usable by the legacy driver."""
    backend = STARCommandCatcher()
    deck = STARDeck(name="custom_deck")
    lh = LiquidHandler(backend=backend, deck=deck)
    await lh.setup()
    try:
      await backend.pick_up_core_gripper_tools(front_channel=7)
      self.assertTrue(any("C0ZT" in command for command in backend.commands))
    finally:
      await lh.stop()

  async def test_gripper_uses_existing_definition_and_refuses_registration(self):
    backend = STARCommandCatcher()
    tool = hamilton_core_gripper_tool("gripper")
    self.assertEqual(await backend.get_or_assign_tip_type_index(tool), 14)
    self.assertEqual(backend.commands, [])

    backend._tip_type_indices.clear()
    with self.assertRaisesRegex(AssertionError, "No firmware definition for CO-RE gripper"):
      await backend.get_or_assign_tip_type_index(tool)
    self.assertEqual(backend.commands, [])


class TestSTARLiquidHandlerCommands(unittest.IsolatedAsyncioTestCase):
  """Test STAR backend for liquid handling."""

  async def asyncSetUp(self):
    self.STAR = STARTestBackend(read_timeout=1)
    self.STAR._write_and_read_command = unittest.mock.AsyncMock()

    self.deck = STARLetDeck()
    self.lh = LiquidHandler(self.STAR, deck=self.deck)

    self.tip_car = TIP_CAR_480_A00(name="tip carrier")
    self.tip_car[1] = self.tip_rack = hamilton_96_tiprack_300uL_filter(name="tip_rack_01")
    self.tip_car[2] = self.tip_rack2 = hamilton_96_tiprack_1000uL_filter(name="tip_rack_02")
    self.deck.assign_child_resource(self.tip_car, track=1)

    self.plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    self.plt_car[0] = self.plate = cor_96_wellplate_360uL_Fb(name="plate_01")
    lid = Lid(
      name="plate_01_lid",
      size_x=self.plate.get_size_x(),
      size_y=self.plate.get_size_y(),
      size_z=10,
      nesting_z_height=10,
    )
    self.plate.assign_child_resource(lid)
    assert self.plate.lid is not None
    self.plt_car[1] = self.other_plate = cor_96_wellplate_360uL_Fb(name="plate_02")
    lid = Lid(
      name="plate_02_lid",
      size_x=self.other_plate.get_size_x(),
      size_y=self.other_plate.get_size_y(),
      size_z=10,
      nesting_z_height=10,
    )
    self.other_plate.assign_child_resource(lid)
    self.deck.assign_child_resource(self.plt_car, track=9)

    class BlueBucket(Container):
      def __init__(self, name: str):
        super().__init__(
          name,
          size_x=123,
          size_y=82,
          size_z=75,
          category="bucket",
          max_volume=123 * 82 * 75,
          material_z_thickness=1,
        )

    self.bb = BlueBucket(name="blue bucket")
    self.deck.assign_child_resource(self.bb, location=Coordinate(425, 141.5, 120 - 1))

    self.maxDiff = None

    self.STAR._num_channels = 8
    self.STAR._machine_conf = _DEFAULT_MACHINE_CONFIGURATION
    self.STAR._extended_conf = _DEFAULT_EXTENDED_CONFIGURATION
    self.STAR._core_parked = True
    self.STAR._iswap_parked = True
    await self.lh.setup()

    set_tip_tracking(enabled=False)

  async def test_teaching_needle_pickup_and_return(self):
    """Teaching needles retain their pickup and return heights in the integrated holder."""
    rack = self.deck.get_resource("teaching_tip_rack")
    await self.lh.pick_up_tips(rack.get_all_items(), use_channels=list(range(8)))
    pickup = next(
      call.kwargs["cmd"]
      for call in self.STAR._write_and_read_command.call_args_list
      if call.kwargs["cmd"].startswith("C0TP")
    )
    parsed_pickup = parse_star_fw_string(pickup, "tp####tz####")
    self.assertEqual((parsed_pickup["tp"], parsed_pickup["tz"]), (1830, 1750))

    self.STAR._write_and_read_command.return_value = (
      "C0TRid0001kz000 000 000 000 000 000 000 000vz000 000 000 000 000 000 000 000"
    )
    await self.lh.return_tips(use_channels=list(range(8)))
    drop = next(
      call.kwargs["cmd"]
      for call in self.STAR._write_and_read_command.call_args_list
      if call.kwargs["cmd"].startswith("C0TR")
    )
    parsed_drop = parse_star_fw_string(drop, "tp####tz####")
    self.assertEqual((parsed_drop["tp"], parsed_drop["tz"]), (1830, 1750))

  async def test_core_read_barcode_success(self):
    """core_read_barcode_of_picked_up_resource should send ZB and return a Barcode."""

    self.STAR._write_and_read_command.return_value = (  # type: ignore
      "C0ZBid0001er00/00bb/08ABCDEFGH"
    )

    barcode = await self.STAR.core_read_barcode_of_picked_up_resource(rails=5)

    # Check command format.
    self.STAR._write_and_read_command.assert_has_calls(  # type: ignore
      [
        _any_write_and_read_command_call(
          "C0ZBid0001cp05zb2200th2750zy1287bd1ma0250 2100 0860 0200mr0mo000 000 000 000 000 000 000",
        )
      ]
    )

    # Check returned barcode object.
    self.assertIsInstance(barcode, Barcode)
    assert barcode is not None
    self.assertEqual(barcode.data, "ABCDEFGH")
    self.assertEqual(barcode.symbology, "code128")
    self.assertEqual(barcode.position_on_resource, "front")

  async def test_core_read_barcode_raises_on_missing_error_section(self):
    """Unexpected response without error section should raise ValueError."""

    self.STAR._write_and_read_command.return_value = (  # type: ignore
      "C0ZBid0001bb/08ABCDEFGH"
    )

    with self.assertRaises(ValueError):
      await self.STAR.core_read_barcode_of_picked_up_resource(rails=5)

  async def test_core_read_barcode_raises_on_invalid_lengths(self):
    """Non-integer / inconsistent bb length fields should raise ValueError."""

    # Invalid bb field (non-integer length).
    self.STAR._write_and_read_command.return_value = (  # type: ignore
      "C0ZBid0001er00/00bb/XXABCDEFGH"
    )

    with self.assertRaises(ValueError):
      await self.STAR.core_read_barcode_of_picked_up_resource(rails=5)

    # Length > 0 but no data present.
    self.STAR._write_and_read_command.return_value = (  # type: ignore
      "C0ZBid0001er00/00bb/08"
    )

    with self.assertRaises(ValueError):
      await self.STAR.core_read_barcode_of_picked_up_resource(rails=5)

  async def test_core_read_barcode_nonzero_error_code_raises_firmware_error(self):
    """Non-zero error code should be surfaced as STARFirmwareError."""

    self.STAR._write_and_read_command.return_value = (  # type: ignore
      "C0ZBid0001er05/30bb/00"
    )

    with self.assertRaises(STARFirmwareError):
      await self.STAR.core_read_barcode_of_picked_up_resource(rails=5)

  async def test_core_read_barcode_no_barcode_raises_value_error(self):
    """bb/00 (no barcode) should raise ValueError so callers can handle it explicitly."""

    self.STAR._write_and_read_command.return_value = (  # type: ignore
      "C0ZBid0001er00/00bb/00"
    )

    with self.assertRaises(ValueError):
      await self.STAR.core_read_barcode_of_picked_up_resource(rails=5)

  async def test_core_read_barcode_manual_input_success(self):
    """When allow_manual_input=True and bb/00, manual input should be used to build a Barcode."""

    self.STAR._write_and_read_command.return_value = (  # type: ignore
      "C0ZBid0001er00/00bb/00"
    )

    with unittest.mock.patch("builtins.input", return_value="MANUAL123"):
      barcode = await self.STAR.core_read_barcode_of_picked_up_resource(
        rails=5,
        allow_manual_input=True,
        labware_description="Cos_96_PCR_0001",
      )

    self.assertIsInstance(barcode, Barcode)
    self.assertEqual(barcode.data, "MANUAL123")
    self.assertEqual(barcode.symbology, "code128")
    self.assertEqual(barcode.position_on_resource, "front")

  async def test_core_read_barcode_manual_input_empty_raises_value_error(self):
    """When allow_manual_input=True and user provides empty input, ValueError should be raised."""

    self.STAR._write_and_read_command.return_value = (  # type: ignore
      "C0ZBid0001er00/00bb/00"
    )

    with unittest.mock.patch("builtins.input", return_value="   "):
      with self.assertRaises(ValueError):
        await self.STAR.core_read_barcode_of_picked_up_resource(
          rails=5,
          allow_manual_input=True,
          labware_description="Cos_96_PCR_0001",
        )

  async def asyncTearDown(self):
    await self.lh.stop()

  async def test_indicator_light(self):
    await self.STAR.set_loading_indicators(bit_pattern=[True] * 54, blink_pattern=[False] * 54)
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0CPid0001cl3FFFFFFFFFFFFFcb00000000000000",
        )
      ]
    )

  def test_ops_to_fw_positions(self):
    """Convert channel positions to firmware positions."""
    tip_a1 = self.tip_rack.get_item("A1")
    tip_f1 = self.tip_rack.get_item("F1")
    tip = self.tip_rack.get_tip("A1")

    op1 = Pickup(resource=tip_a1, tip=tip, offset=Coordinate.zero())
    op2 = Pickup(resource=tip_f1, tip=tip, offset=Coordinate.zero())
    self.assertEqual(
      self.STAR._ops_to_fw_positions((op1,), use_channels=[0]),
      ([1179, 0], [2418, 0], [True, False]),
    )

    self.assertEqual(
      self.STAR._ops_to_fw_positions((op1, op2), use_channels=[0, 1]),
      ([1179, 1179, 0], [2418, 1968, 0], [True, True, False]),
    )

    self.assertEqual(
      self.STAR._ops_to_fw_positions((op1, op2), use_channels=[1, 2]),
      (
        [0, 1179, 1179, 0],
        [0, 2418, 1968, 0],
        [False, True, True, False],
      ),
    )

    # check two operations on the same row, different column.
    tip_a2 = self.tip_rack.get_item("A2")
    op3 = Pickup(resource=tip_a2, tip=tip, offset=Coordinate.zero())
    self.assertEqual(
      self.STAR._ops_to_fw_positions((op1, op3), use_channels=[0, 1]),
      ([1179, 1269, 0], [2418, 2418, 0], [True, True, False]),
    )

    # A1, A2, B1, B2
    tip_b1 = self.tip_rack.get_item("B1")
    op4 = Pickup(resource=tip_b1, tip=tip, offset=Coordinate.zero())
    tip_b2 = self.tip_rack.get_item("B2")
    op5 = Pickup(resource=tip_b2, tip=tip, offset=Coordinate.zero())
    self.assertEqual(
      self.STAR._ops_to_fw_positions((op1, op4, op3, op5), use_channels=[0, 1, 2, 3]),
      (
        [1179, 1179, 1269, 1269, 0],
        [2418, 2328, 2418, 2328, 0],
        [True, True, True, True, False],
      ),
    )

  def test_tip_definition(self):
    pass

  async def test_tip_pickup_01(self):
    await self.lh.pick_up_tips(self.tip_rack["A1", "B1"])
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0TTid0001tt01tf1tl0519tv03600tg2tu0",
        ),
        _any_write_and_read_command_call(
          "C0TPid0002xp01179 01179 00000&yp2418 2328 0000&tm1 1 0&tt01tp2244tz2164th2450td0",
        ),
      ]
    )

  async def test_tip_pickup_56(self):
    await self.lh.pick_up_tips(self.tip_rack["E1", "F1"], use_channels=[4, 5])
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0TTid0001tt01tf1tl0519tv03600tg2tu0",
        ),
        _any_write_and_read_command_call(
          "C0TPid0002xp00000 00000 00000 00000 01179 01179 00000&yp0000 0000 0000 0000 2058 1968 0000&tm0 0 0 0 1 1 0&tt01tp2244tz2164th2450td0",
        ),
      ]
    )

  async def test_tip_drop_56(self):
    await self.test_tip_pickup_56()  # pick up tips first
    self.STAR._write_and_read_command.side_effect = [
      "C0TRid0003kz000 000 000 000 000 000 000 000vz000 000 000 000 000 000 000 000"
    ]
    await self.lh.drop_tips(self.tip_rack["E1", "F1"], use_channels=[4, 5])
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0TRid0003xp00000 00000 00000 00000 01179 01179 00000&yp0000 0000 0000 0000 2058 1968 "
          "0000&tm0 0 0 0 1 1 0&tp2244tz2164th2450te2450ti1",
        )
      ]
    )

  async def test_aspirate56(self):
    self.maxDiff = None
    await self.test_tip_pickup_56()  # pick up tips first
    assert self.plate.lid is not None
    self.plate.lid.unassign()
    for well in self.plate.get_items(["A1", "B1"]):
      well.tracker.set_volume(100 * 1.072)  # liquid class correction
    await self.lh.aspirate(self.plate["A1", "B1"], vols=[100, 100], use_channels=[4, 5])
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0ASid0003at0 0 0 0 0 0 0&tm0 0 0 0 1 1 0&xp00000 00000 00000 "
          "00000 02983 02983 00000&yp0000 0000 0000 0000 1457 1367 0000&th2450te2450lp2000 2000 2000 "
          "2000 2000 2000 2000&ch000 000 000 000 000 000 000&zl1866 1866 1866 1866 1866 1866 1866&"
          "po0100 0100 0100 0100 0100 0100 0100&zu0032 0032 0032 0032 0032 0032 0032&zr06180 06180 "
          "06180 06180 06180 06180 06180&zx1866 1866 1866 1866 1866 1866 1866&ip0000 0000 0000 0000 "
          "0000 0000 0000&it0 0 0 0 0 0 0&fp0000 0000 0000 0000 0000 0000 0000&av01072 01072 01072 "
          "01072 01072 01072 01072&as1000 1000 1000 1000 1000 1000 1000&ta000 000 000 000 000 000 000&"
          "ba0000 0000 0000 0000 0000 0000 0000&oa000 000 000 000 000 000 000&lm0 0 0 0 0 0 0&ll1 1 1 "
          "1 1 1 1&lv1 1 1 1 1 1 1&zo000 000 000 000 000 000 000&ld00 00 00 00 00 00 00&de0020 0020 "
          "0020 0020 0020 0020 0020&wt10 10 10 10 10 10 10&mv00000 00000 00000 00000 00000 00000 00000&"
          "mc00 00 00 00 00 00 00&mp000 000 000 000 000 000 000&ms1000 1000 1000 1000 1000 1000 1000&"
          "mh0000 0000 0000 0000 0000 0000 0000&gi000 000 000 000 000 000 000&gj0gk0lk0 0 0 0 0 0 0&"
          "ik0000 0000 0000 0000 0000 0000 0000&sd0500 0500 0500 0500 0500 0500 0500&se0500 0500 0500 "
          "0500 0500 0500 0500&sz0300 0300 0300 0300 0300 0300 0300&io0000 0000 0000 0000 0000 0000 0"
          "000&",
        )
      ]
    )

  async def test_single_channel_aspiration(self):
    self.lh.update_head_state({0: self.tip_rack.get_tip("A1")})
    assert self.plate.lid is not None
    self.plate.lid.unassign()
    well = self.plate.get_item("A1")
    well.tracker.set_volume(100 * 1.072)  # liquid class correction
    await self.lh.aspirate([well], vols=[100])
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0ASid0001at0 0&tm1 0&xp02983 00000&yp1457 0000&th2450te2450lp2000 2000&ch000 000&zl1866 "
          "1866&po0100 0100&zu0032 0032&zr06180 06180&zx1866 1866&ip0000 0000&it0 0&fp0000 0000&"
          "av01072 01072&as1000 1000&ta000 000&ba0000 0000&oa000 000&lm0 0&ll1 1&lv1 1&zo000 000&"
          "ld00 00&de0020 0020&wt10 10&mv00000 00000&mc00 00&mp000 000&ms1000 1000&mh0000 0000&"
          "gi000 000&gj0gk0lk0 0&ik0000 0000&sd0500 0500&se0500 0500&sz0300 0300&io0000 0000&",
        )
      ]
    )

  async def test_aspirate_rejects_over_tip_capacity(self):
    self.lh.update_head_state({0: self.tip_rack.get_tip("A1")})  # 300 uL filter tip, max 360
    assert self.plate.lid is not None
    self.plate.lid.unassign()
    well = self.plate.get_item("A1")
    # pre-wetting peak (volume + pre_wetting) exceeds the tip max
    well.tracker.set_volume(300)
    with self.assertRaises(ValueError):
      await self.lh.aspirate([well], vols=[100], pre_wetting_volume=[300])
    # transport-air peak (volume + transport_air) exceeds the tip max
    well.tracker.set_volume(300)
    with self.assertRaises(ValueError):
      await self.lh.aspirate([well], vols=[100], transport_air_volume=[300])

  async def test_aspirate_capacity_ignores_blow_out(self):
    self.lh.update_head_state({0: self.tip_rack.get_tip("A1")})  # 300 uL filter tip, max 360
    assert self.plate.lid is not None
    self.plate.lid.unassign()
    well = self.plate.get_item("A1")
    well.tracker.set_volume(200)
    # blow-out counts toward neither peak, so a large blow-out stays within capacity
    await self.lh.aspirate([well], vols=[100], blow_out_air_volume=[300])

  async def test_aspirate_capacity_boundary_is_exclusive(self):
    self.lh.update_head_state({0: self.tip_rack.get_tip("A1")})  # 300 uL filter tip, max 360
    assert self.plate.lid is not None
    self.plate.lid.unassign()
    well = self.plate.get_item("A1")
    # a peak exactly at the tip's maximal volume is allowed; just above it is rejected (>)
    well.tracker.set_volume(400)
    await self.lh.aspirate([well], vols=[360], disable_volume_correction=[True])
    well.tracker.set_volume(400)
    with self.assertRaises(ValueError):
      await self.lh.aspirate([well], vols=[361], disable_volume_correction=[True])

  async def test_single_channel_aspiration_liquid_height(self):
    self.lh.update_head_state({0: self.tip_rack.get_tip("A1")})
    # TODO: Hamilton liquid classes
    assert self.plate.lid is not None
    self.plate.lid.unassign()
    well = self.plate.get_item("A1")
    well.tracker.set_volume(100 * 1.072)  # liquid class correction
    await self.lh.aspirate([well], vols=[100], liquid_height=[10])

    # This passes the test, but is not the real command.
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0ASid0001at0 0&tm1 0&xp02983 00000&yp1457 0000&th2450te2450lp2000 2000&ch000 000&zl1966 "
          "1966&po0100 0100&zu0032 0032&zr06180 06180&zx1866 1866&ip0000 0000&it0 0&fp0000 0000&"
          "av01072 01072&as1000 1000&ta000 000&ba0000 0000&oa000 000&lm0 0&ll1 1&lv1 1&zo000 000&"
          "ld00 00&de0020 0020&wt10 10&mv00000 00000&mc00 00&mp000 000&ms1000 1000&mh0000 0000&"
          "gi000 000&gj0gk0lk0 0&ik0000 0000&sd0500 0500&se0500 0500&sz0300 0300&io0000 0000&",
        )
      ]
    )

  async def test_multi_channel_aspiration(self):
    self.lh.update_head_state({0: self.tip_rack.get_tip("A1"), 1: self.tip_rack.get_tip("B1")})
    # TODO: Hamilton liquid classes
    assert self.plate.lid is not None
    self.plate.lid.unassign()
    wells = self.plate.get_items("A1:B1")
    for well in wells:
      well.tracker.set_volume(100 * 1.072)  # liquid class correction
    await self.lh.aspirate(self.plate["A1:B1"], vols=[100] * 2)

    # This passes the test, but is not the real command.
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0ASid0001at0 0 0&tm1 1 0&xp02983 02983 00000&yp1457 1367 0000&th2450te2450lp2000 2000 2000&"
          "ch000 000 000&zl1866 1866 1866&po0100 0100 0100&zu0032 0032 0032&zr06180 06180 06180&"
          "zx1866 1866 1866&ip0000 0000 0000&it0 0 0&fp0000 0000 0000&av01072 01072 01072&as1000 1000 "
          "1000&ta000 000 000&ba0000 0000 0000&oa000 000 000&lm0 0 0&ll1 1 1&lv1 1 1&zo000 000 000&"
          "ld00 00 00&de0020 0020 0020&wt10 10 10&mv00000 00000 00000&mc00 00 00&mp000 000 000&"
          "ms1000 1000 1000&mh0000 0000 0000&gi000 000 000&gj0gk0lk0 0 0&ik0000 0000 0000&sd0500 0500 "
          "0500&se0500 0500 0500&sz0300 0300 0300&io0000 0000 0000&",
        )
      ]
    )

  async def test_aspirate_single_resource(self):
    self.lh.update_head_state({i: self.tip_rack.get_tip(i) for i in range(5)})
    with no_volume_tracking():
      await self.lh.aspirate(
        [self.bb] * 5,
        vols=[10] * 5,
        use_channels=[0, 1, 2, 3, 4],
        liquid_height=[1] * 5,
      )
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0ASid0001at0 0 0 0 0 0&tm1 1 1 1 1 0&xp04865 04865 04865 04865 04865 00000&yp2098 1962 "
          "1825 1688 1552 0000&th2450te2450lp2000 2000 2000 2000 2000 2000&ch000 000 000 000 000 000&"
          "zl1210 1210 1210 1210 1210 1210&po0100 0100 0100 0100 0100 0100&zu0032 0032 0032 0032 0032 "
          "0032&zr06180 06180 06180 06180 06180 06180&zx1200 1200 1200 1200 1200 1200&ip0000 0000 0000 "
          "0000 0000 0000&it0 0 0 0 0 0&fp0000 0000 0000 0000 0000 0000&av00119 00119 00119 00119 "
          "00119 00119&as1000 1000 1000 1000 1000 1000&ta000 000 000 000 000 000&ba0000 0000 0000 0000 "
          "0000 0000&oa000 000 000 000 000 000&lm0 0 0 0 0 0&ll1 1 1 1 1 1&lv1 1 1 1 1 1&zo000 000 000 "
          "000 000 000&ld00 00 00 00 00 00&de0020 0020 0020 0020 0020 0020&wt10 10 10 10 10 10&mv00000 "
          "00000 00000 00000 00000 00000&mc00 00 00 00 00 00&mp000 000 000 000 000 000&ms1000 1000 "
          "1000 1000 1000 1000&mh0000 0000 0000 0000 0000 0000&gi000 000 000 000 000 000&gj0gk0lk0 0 0 "
          "0 0 0&ik0000 0000 0000 0000 0000 0000&sd0500 0500 0500 0500 0500 0500&se0500 0500 0500 0500 "
          "0500 0500&sz0300 0300 0300 0300 0300 0300&io0000 0000 0000 0000 0000 0000&",
        )
      ]
    )

  async def test_dispense_single_resource(self):
    self.lh.update_head_state({i: self.tip_rack.get_tip(i) for i in range(5)})
    with no_volume_tracking():
      await self.lh.dispense(
        [self.bb] * 5,
        vols=[10] * 5,
        use_channels=[0, 1, 2, 3, 4],
        liquid_height=[1] * 5,
        blow_out=[True] * 5,
        jet=[True] * 5,
      )
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0DSid0001dm1 1 1 1 1 1&tm1 1 1 1 1 0&xp04865 04865 04865 04865 04865 00000&yp2098 1962 "
          "1825 1688 1552 0000&zx1200 1200 1200 1200 1200 1200&lp2000 2000 2000 2000 2000 2000&zl1210 "
          "1210 1210 1210 1210 1210&po0100 0100 0100 0100 0100 0100&ip0000 0000 0000 0000 0000 0000&"
          "it0 0 0 0 0 0&fp0000 0000 0000 0000 0000 0000&zu0032 0032 0032 0032 0032 0032&zr06180 06180 "
          "06180 06180 06180 06180&th2450te2450dv00116 00116 00116 00116 00116 00116&ds1800 1800 1800 "
          "1800 1800 1800&ss0050 0050 0050 0050 0050 0050&rv000 000 000 000 000 000&ta050 050 050 050 "
          "050 050&ba0300 0300 0300 0300 0300 0300&lm0 0 0 0 0 0&dj00zo000 000 000 000 000 000&ll1 1 1 "
          "1 1 1&lv1 1 1 1 1 1&de0010 0010 0010 0010 0010 0010&wt00 00 00 00 00 00&mv00000 00000 00000 "
          "00000 00000 00000&mc00 00 00 00 00 00&mp000 000 000 000 000 000&ms0010 0010 0010 0010 0010 "
          "0010&mh0000 0000 0000 0000 0000 0000&gi000 000 000 000 000 000&gj0gk0",
        )
      ]
    )

  async def test_single_channel_dispense(self):
    self.lh.update_head_state({0: self.tip_rack.get_tip("A1")})
    assert self.plate.lid is not None
    self.plate.lid.unassign()
    with no_volume_tracking():
      await self.lh.dispense(self.plate["A1"], vols=[100], jet=[True], blow_out=[True])
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0DSid0001dm1 1&tm1 0&xp02983 00000&yp1457 0000&zx1866 1866&lp2000 2000&zl1866 1866&po0100 0100&ip0000 0000&it0 0&fp0000 0000&zu0032 0032&zr06180 06180&th2450te2450dv01072 01072&ds1800 1800&ss0050 0050&rv000 000&ta050 050&ba0300 0300&lm0 0&dj00zo000 000&ll1 1&lv1 1&de0010 0010&wt00 00&mv00000 00000&mc00 00&mp000 000&ms0010 0010&mh0000 0000&gi000 000&gj0gk0",
        )
      ]
    )

  async def test_multi_channel_dispense(self):
    self.lh.update_head_state({0: self.tip_rack.get_tip("A1"), 1: self.tip_rack.get_tip("B1")})
    # TODO: Hamilton liquid classes
    assert self.plate.lid is not None
    self.plate.lid.unassign()
    with no_volume_tracking():
      await self.lh.dispense(
        self.plate["A1:B1"],
        vols=[100] * 2,
        jet=[True] * 2,
        blow_out=[True] * 2,
      )

    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0DSid0001dm1 1 1&tm1 1 0&xp02983 02983 00000&yp1457 1367 0000&zx1866 1866 1866&lp2000 2000 "
          "2000&zl1866 1866 1866&po0100 0100 0100&ip0000 0000 0000&it0 0 0&fp0000 0000 0000&zu0032 "
          "0032 0032&zr06180 06180 06180&th2450te2450dv01072 01072 01072&ds1800 1800 1800&"
          "ss0050 0050 0050&rv000 000 000&ta050 050 050&ba0300 0300 0300&lm0 0 0&dj00zo000 000 000&"
          "ll1 1 1&lv1 1 1&de0010 0010 0010&wt00 00 00&mv00000 00000 00000&mc00 00 00&mp000 000 000&"
          "ms0010 0010 0010&mh0000 0000 0000&gi000 000 000&gj0gk0",
        )
      ]
    )

  async def test_core_96_tip_pickup(self):
    await self.lh.pick_up_tips96(self.tip_rack)
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call("C0TTid0001tt01tf1tl0519tv03600tg2tu0"),
        _any_write_and_read_command_call("H0DQid0002dq11281dv13500du00000dr900000dw15"),
        _any_write_and_read_command_call("C0EPid0003xs01179xd0yh2418tt01wu0za2164zh2450ze2450"),
      ]
    )

  async def test_tip_tracking_pick_up96(self):
    set_tip_tracking(enabled=True)
    await self.lh.pick_up_tips96(self.tip_rack)
    set_tip_tracking(enabled=False)

  async def test_core_96_tip_drop(self):
    await self.lh.pick_up_tips96(self.tip_rack)  # pick up tips first
    self.STAR._write_and_read_command.reset_mock()
    await self.lh.drop_tips96(self.tip_rack)
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call("C0ERid0004xs01179xd0yh2418za2164zh2450ze2450"),
      ]
    )

  async def test_core_96_tip_discard(self):
    await self.lh.pick_up_tips96(self.tip_rack)  # pick up tips first
    self.STAR._write_and_read_command.reset_mock()
    await self.lh.discard_tips96()
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call("C0ERid0004xs00420xd1yh1203za2164zh2450ze2450"),
      ]
    )

  async def test_core_96_aspirate(self):
    await self.lh.pick_up_tips96(self.tip_rack2)  # pick up high volume tips
    self.STAR._write_and_read_command.reset_mock()

    # TODO: Hamilton liquid classes
    assert self.plate.lid is not None
    self.plate.lid.unassign()
    await self.lh.aspirate96(self.plate, volume=100, blow_out=True)

    # volume used to be 01072, but that was generated using a non-core liquid class.
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0EAid0004aa0xs02983xd0yh1457zh2450ze2450lz1999zt1866pp0100zm1866zv0032zq06180iw000ix0fh000af01083ag2500vt050bv00000wv00050cm0cs1bs0020wh10hv00000hc00hp000mj000hs1200cwFFFFFFFFFFFFFFFFFFFFFFFFcr000cj0cx0"
        ),
      ]
    )

  async def test_core_96_dispense(self):
    await self.lh.pick_up_tips96(self.tip_rack2)  # pick up high volume tips
    if self.plate.lid is not None:
      self.plate.lid.unassign()
    await self.lh.aspirate96(self.plate, 100, blow_out=True)  # aspirate first
    self.STAR._write_and_read_command.reset_mock()

    with no_volume_tracking():
      await self.lh.dispense96(self.plate, 100, blow_out=True)

    # volume used to be 01072, but that was generated using a non-core liquid class.
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0EDid0005da3xs02983xd0yh1457zm1866zv0032zq06180lz1999zt1866pp0100iw000ix0fh000zh2450ze2450df01083dg1200es0050ev000vt050bv00000cm0cs1ej00bs0020wh00hv00000hc00hp000mj000hs1200cwFFFFFFFFFFFFFFFFFFFFFFFFcr000cj0cx0"
        ),
      ]
    )

  async def test_head96_experimental_aspirate(self):
    self.STAR._head96_information = _make_head96_information(self.STAR)
    self.STAR.head96_request_tip_presence = unittest.mock.AsyncMock(return_value=0)
    self.STAR._write_and_read_command.reset_mock()
    await self.STAR.head96_experimental_aspirate(
      volume=100,
      minimum_height=230,
      surface_following_distance=2,
      flow_rate=50,
      requires_tip=False,  # isolate the wire string from the tip-presence round-trip
    )
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "H0PAid0001pmFFFFFFFFFFFFFFFFFFFFFFFFdj1da05170dv02585dc00000zd0400zh46000to000"
        )
      ]
    )

  async def test_head96_experimental_dispense(self):
    self.STAR._head96_information = _make_head96_information(self.STAR)
    self.STAR.head96_request_tip_presence = unittest.mock.AsyncMock(return_value=0)
    self.STAR._write_and_read_command.reset_mock()
    await self.STAR.head96_experimental_dispense(
      volume=100,
      minimum_height=230,
      stop_back_volume=5,
      surface_following_distance=2,
      flow_rate=50,
      stop_flow_rate=20,
      requires_tip=False,  # isolate the wire string from the tip-presence round-trip
    )
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "H0PBid0001pmFFFFFFFFFFFFFFFFFFFFFFFFdb05170dv02585dd0259ze0400zh46000du01034"
        )
      ]
    )

  async def test_head96_experimental_aspirate_requires_tip(self):
    """requires_tip raises when the head reports no tips."""
    self.STAR._head96_information = _make_head96_information(self.STAR)
    self.STAR.head96_request_tip_presence = unittest.mock.AsyncMock(return_value=0)
    with self.assertRaises(RuntimeError):
      await self.STAR.head96_experimental_aspirate(volume=100, minimum_height=230)

  async def test_head96_experimental_aspirate_default_flow_rate(self):
    """Omitting flow_rate emits the head's default dispensing-drive speed (dv13500)."""
    self.STAR._head96_information = _make_head96_information(self.STAR)
    self.STAR.head96_request_tip_presence = unittest.mock.AsyncMock(return_value=0)
    self.STAR._write_and_read_command.reset_mock()
    await self.STAR.head96_experimental_aspirate(
      volume=100, minimum_height=230, surface_following_distance=2, requires_tip=False
    )
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "H0PAid0001pmFFFFFFFFFFFFFFFFFFFFFFFFdj1da05170dv13500dc00000zd0400zh46000to000"
        )
      ]
    )

  async def test_head96_experimental_dispense_default_flow_rates(self):
    """Omitting flow_rate and stop_flow_rate emits the head default speed (dv13500) and a zero stop
    speed (du00000), with the stop-back and surface-following defaults (dd0000 / ze0000)."""
    self.STAR._head96_information = _make_head96_information(self.STAR)
    self.STAR.head96_request_tip_presence = unittest.mock.AsyncMock(return_value=0)
    self.STAR._write_and_read_command.reset_mock()
    await self.STAR.head96_experimental_dispense(volume=100, minimum_height=230, requires_tip=False)
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "H0PBid0001pmFFFFFFFFFFFFFFFFFFFFFFFFdb05170dv13500dd0000ze0000zh46000du00000"
        )
      ]
    )

  async def test_head96_experimental_aspirate_volume_out_of_range_raises(self):
    """A volume beyond the dispensing-drive range raises before any command is sent."""
    self.STAR._head96_information = _make_head96_information(self.STAR)
    with self.assertRaises(AssertionError):
      await self.STAR.head96_experimental_aspirate(
        volume=100000, minimum_height=230, requires_tip=False
      )

  async def test_head96_experimental_aspirate_tip_bottom_overhang(self):
    """With a tip on, minimum_height is tip-bottom: zh = minimum_height + overhang."""
    self.STAR._head96_information = _make_head96_information(self.STAR)
    self.STAR.head96_request_tip_presence = unittest.mock.AsyncMock(return_value=1)
    self.STAR.head96_request_stop_disk_z = unittest.mock.AsyncMock(return_value=332.0)
    self.STAR.head96_request_position = unittest.mock.AsyncMock(
      return_value=Coordinate(0, 0, 245.0)
    )
    self.STAR._write_and_read_command.reset_mock()
    # overhang = 332 - 245 = 87; zh = (200 + 87) / 0.005 = 57400
    await self.STAR.head96_experimental_aspirate(
      volume=100, minimum_height=200, surface_following_distance=2
    )
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "H0PAid0001pmFFFFFFFFFFFFFFFFFFFFFFFFdj1da05170dv13500dc00000zd0400zh57400to000"
        )
      ]
    )

  async def test_head96_experimental_aspirate_minimum_height_defaults_to_floor(self):
    """Omitting minimum_height with no tip defaults to the firmware Z floor (z_range[0])."""
    self.STAR._head96_information = _make_head96_information(self.STAR)
    self.STAR.head96_request_tip_presence = unittest.mock.AsyncMock(return_value=0)
    self.STAR._write_and_read_command.reset_mock()
    # no tip -> overhang 0 -> minimum_height defaults to z_range[0] = 180.5 mm -> zh 36100
    await self.STAR.head96_experimental_aspirate(volume=100, requires_tip=False)
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "H0PAid0001pmFFFFFFFFFFFFFFFFFFFFFFFFdj1da05170dv13500dc00000zd0000zh36100to000"
        )
      ]
    )

  async def test_head96_probe_z_using_clld_wire_string(self):
    """The 2013+ ZL command assembles in the documented field order with the tip-overhang offset.

    Guards the zc 5-digit width (6 caused firmware er32), the tip-bottom -> stop-disk mapping, the
    zv/zw fields, and approach_speed=None -> head96_z_drive_speed_default. Returns the detected
    surface as a tip-bottom position (stop disk minus overhang).
    """
    self.STAR._head96_information = _make_head96_information(self.STAR)
    self.STAR._head96_z_drive_speed_default = 85.0
    self.STAR.head96_request_tip_presence = unittest.mock.AsyncMock(return_value=1)
    self.STAR.head96_request_last_lld_height = unittest.mock.AsyncMock(return_value=200.0)
    self.STAR._write_and_read_command.reset_mock()
    detected = await self.STAR.head96_probe_z_using_clld(
      tip_len=50.0,  # overhang = 50 - 8 = 42 mm
      lowest_immers_pos=140.0,
      start_pos_search=250.0,
      speed=10.0,
      acceleration=300.0,
      approach_speed=None,  # -> head96_z_drive_speed_default = 85.0
      current_protection_limiter=15,
      lld_sensor="any",
      detection_edge=10,
      detection_drop=2,
      post_detection_dist=2.0,
    )
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "H0ZLid0001zh36400zc58400zi0400zj1lm2gt0010gl0002zv17000zl02000zr060000zw15"
        )
      ]
    )
    self.assertEqual(detected, 158.0)  # 200.0 detected surface - 42 overhang

  async def test_head96_probe_z_using_clld_requires_tip(self):
    """cLLD raises if the head holds no tip, whether tip_len is measured or supplied."""
    self.STAR._head96_information = _make_head96_information(self.STAR)
    self.STAR._head96_z_drive_speed_default = 85.0
    self.STAR.head96_request_tip_presence = unittest.mock.AsyncMock(return_value=0)
    with self.assertRaises(ValueError):
      await self.STAR.head96_probe_z_using_clld()
    with self.assertRaises(ValueError):
      await self.STAR.head96_probe_z_using_clld(tip_len=50.0)

  async def test_head96_probe_z_using_clld_retracts_on_firmware_error(self):
    """A firmware error during the search retracts the head to Z-safety before re-raising."""
    self.STAR._head96_information = _make_head96_information(self.STAR)
    self.STAR._head96_z_drive_speed_default = 85.0
    self.STAR.head96_request_tip_presence = unittest.mock.AsyncMock(return_value=1)
    self.STAR.head96_move_to_z_safety = unittest.mock.AsyncMock()
    self.STAR._write_and_read_command = unittest.mock.AsyncMock(
      side_effect=STARFirmwareError(errors={}, raw_response="H0ZLid0001er32")
    )
    with self.assertRaises(STARFirmwareError):
      await self.STAR.head96_probe_z_using_clld(tip_len=50.0)
    self.STAR.head96_move_to_z_safety.assert_awaited_once()

  async def test_mix96_floor_maps_to_minimum_height_with_offset(self):
    """mix96 sends the resolved tip-bottom floor (well cavity_bottom + offset.z) as the
    experimental command's minimum_height - guards offset.z reaching the floor."""
    _stub_mix96_motion(self.STAR)
    offset_z = 2.0
    await self.STAR.mix96(
      Mix(volume=50, repetitions=1, flow_rate=100),
      resource=self.plate,
      offset=Coordinate(0, 0, offset_z),
    )
    well = self.plate.get_item(0)
    expected_floor = well.get_absolute_location(x="c", y="c", z="cavity_bottom").z + offset_z
    self.assertEqual(
      self.STAR.head96_experimental_aspirate.call_args.kwargs["minimum_height"], expected_floor
    )

  async def test_mix96_stroke_starts_surface_following_above_floor(self):
    """The careful (swap_speed) descent lands at floor + surface_following_distance and that
    distance reaches the aspirate, so the stroke spans [floor, floor+sf], never below floor."""
    _stub_mix96_motion(self.STAR)
    floor_z, sf = 100.0, 8.0
    await self.STAR.mix96_at_coordinate(
      Mix(volume=50, repetitions=1, flow_rate=100, surface_following_distance=sf),
      a1_coordinate=Coordinate(500, 300, floor_z),
      swap_speed=5.0,
    )
    # move_tool_z calls: [0] fast to swap-start, [1] careful to mix_start, [2] exit retract
    careful_descent = self.STAR.head96_move_tool_z.call_args_list[1]
    self.assertEqual(careful_descent.args[0], floor_z + sf)
    self.assertEqual(careful_descent.kwargs["speed"], 5.0)
    self.assertEqual(
      self.STAR.head96_experimental_aspirate.call_args.kwargs["surface_following_distance"], sf
    )

  async def test_mix96_specified_traverse_heights_are_tip_bottom_moves(self):
    """A specified minimum_traverse_height_start/end is a tip-bottom Z (head96_move_tool_z), like
    the rest of the method; only the None default retracts to stop-disk Z safety. Guards against a
    geometric (tip-bottom) traverse height being driven as a stop-disk position."""
    _stub_mix96_motion(self.STAR)
    start_z, end_z = 250.0, 240.0
    await self.STAR.mix96_at_coordinate(
      Mix(volume=50, repetitions=1, flow_rate=100),
      a1_coordinate=Coordinate(500, 300, 100.0),
      minimum_traverse_height_start=start_z,
      minimum_traverse_height_end=end_z,
    )
    self.STAR.head96_move_to_z_safety.assert_not_called()
    tool_z_targets = [call.args[0] for call in self.STAR.head96_move_tool_z.call_args_list]
    self.assertEqual(tool_z_targets[0], start_z)  # first tool move is the start traverse
    self.assertEqual(tool_z_targets[-1], end_z)  # last tool move is the end traverse

  async def test_mix96_zero_blowout_skips_air_gap_calls(self):
    """blowout_air_volume=0 issues no firmware aspirate/dispense for the air gap: every
    experimental aspirate/dispense is a mix-cycle stroke (mix.volume), none a zero-volume blow-out."""
    _stub_mix96_motion(self.STAR)
    await self.STAR.mix96_at_coordinate(
      Mix(volume=50, repetitions=1, flow_rate=100),
      a1_coordinate=Coordinate(500, 300, 100.0),
      blowout_air_volume=0.0,
    )
    asp_vols = [call.args[0] for call in self.STAR.head96_experimental_aspirate.call_args_list]
    disp_vols = [call.args[0] for call in self.STAR.head96_experimental_dispense.call_args_list]
    self.assertEqual(asp_vols, [50])  # one cycle aspirate, no blow-out aspirate
    self.assertEqual(disp_vols, [50])  # one cycle dispense, no blow-out dispense

  async def test_core_96_dispense_quadrant(self):
    """Test that each quadrant of a 384-well plate produces the correct firmware command.

    Before the fix, all quadrants produced identical xs/yh values because the reference well
    was hardcoded to A1 instead of using the actual first well from the quadrant's well list.
    """
    plate_384 = Greiner_384_wellplate_28ul_Fb(name="plate_384")
    self.plt_car[2] = plate_384

    await self.lh.pick_up_tips96(self.tip_rack2)
    if self.plate.lid is not None:
      self.plate.lid.unassign()
    await self.lh.aspirate96(self.plate, volume=100, blow_out=True)

    expected = {
      "tl": "C0EDid0005da2xs02959xd0yh3400zm1912zv0032zq06180lz1999zt1912pp0100iw000ix0fh000zh2450ze2450df00060dg1200es0050ev000vt050bv00000cm0cs1ej00bs0020wh50hv00000hc00hp000mj000hs1200cwFFFFFFFFFFFFFFFFFFFFFFFFcr000cj0cx0",
      "tr": "C0EDid0006da2xs03004xd0yh3400zm1912zv0032zq06180lz1999zt1912pp0100iw000ix0fh000zh2450ze2450df00060dg1200es0050ev000vt050bv00000cm0cs1ej00bs0020wh50hv00000hc00hp000mj000hs1200cwFFFFFFFFFFFFFFFFFFFFFFFFcr000cj0cx0",
      "bl": "C0EDid0007da2xs02959xd0yh3355zm1912zv0032zq06180lz1999zt1912pp0100iw000ix0fh000zh2450ze2450df00060dg1200es0050ev000vt050bv00000cm0cs1ej00bs0020wh50hv00000hc00hp000mj000hs1200cwFFFFFFFFFFFFFFFFFFFFFFFFcr000cj0cx0",
      "br": "C0EDid0008da2xs03004xd0yh3355zm1912zv0032zq06180lz1999zt1912pp0100iw000ix0fh000zh2450ze2450df00060dg1200es0050ev000vt050bv00000cm0cs1ej00bs0020wh50hv00000hc00hp000mj000hs1200cwFFFFFFFFFFFFFFFFFFFFFFFFcr000cj0cx0",
    }

    for quadrant, expected_cmd in expected.items():
      wells = plate_384.get_quadrant(cast(Literal["tl", "tr", "bl", "br"], quadrant))
      self.STAR._write_and_read_command.reset_mock()
      with no_volume_tracking():
        await self.lh.dispense96(wells, volume=6)
      self.STAR._write_and_read_command.assert_has_calls(
        [
          _any_write_and_read_command_call(expected_cmd),
        ]
      )

  async def test_zero_volume_liquid_handling96(self):
    # just test that this does not throw an error
    await self.lh.pick_up_tips96(self.tip_rack)
    assert self.plate.lid is not None
    self.plate.lid.unassign()
    await self.lh.aspirate96(self.plate, 0)
    await self.lh.dispense96(self.plate, 0)

  async def test_iswap(self):
    await self.lh.move_plate(
      self.plate,
      self.plt_car[2],
      pickup_distance_from_top=13.2 - 3.33,
    )
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0001xs03479xd0yj1142yd0zj1874zd0gr1th2800te2800gw4go1308gb1245gt20ga0gc0",
        ),
        _any_write_and_read_command_call(
          "C0PRid0002xs03479xd0yj3062yd0zj1874zd0th2800te2800gr1go1308ga0gc0",
        ),
      ]
    )

  async def test_iswap_plate_reader(self):
    plate_reader = PlateReader(
      name="plate_reader",
      backend=PlateReaderChatterboxBackend(),
      size_x=0,
      size_y=0,
      size_z=0,
    )
    self.deck.assign_child_resource(
      plate_reader, location=Coordinate(1000, 264.7, 200 - 3.03)
    )  # 666: 00002

    await self.lh.move_plate(
      self.plate,
      plate_reader,
      pickup_distance_from_top=8.2 - 3.33,
      pickup_direction=GripDirection.FRONT,
      drop_direction=GripDirection.LEFT,
    )
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0001xs03479xd0yj1142yd0zj1924zd0gr1th2800te2800gw4go1308gb1245gt20ga0gc0",
        ),
        _any_write_and_read_command_call(
          "C0PRid0002xs10427xd0yj3286yd0zj2063zd0th2800te2800gr4go1308ga0gc0",
        ),
      ]
    )
    self.STAR._write_and_read_command.reset_mock()

    assert self.plate.rotation.z == 270
    self.assertAlmostEqual(self.plate.get_absolute_size_x(), 85.48, places=2)
    self.assertAlmostEqual(self.plate.get_absolute_size_y(), 127.76, places=2)

    await self.lh.move_plate(
      plate_reader.get_plate(),
      self.plt_car[0],
      pickup_distance_from_top=8.2 - 3.33,
      pickup_direction=GripDirection.LEFT,
      drop_direction=GripDirection.FRONT,
    )
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0003xs10427xd0yj3286yd0zj2063zd0gr4th2800te2800gw4go1308gb1245gt20ga0gc0",
        ),
        _any_write_and_read_command_call(
          "C0PRid0004xs03479xd0yj1142yd0zj1924zd0th2800te2800gr1go1308ga0gc0",
        ),
      ]
    )

  async def test_iswap_move_lid(self):
    assert self.plate.lid is not None and self.other_plate.lid is not None
    self.other_plate.lid.unassign()  # remove lid from plate
    await self.lh.move_lid(self.plate.lid, self.other_plate)

    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0001xs03479xd0yj1142yd0zj1950zd0gr1th2800te2800gw4go1308gb1245gt20ga0gc0"
        ),
        _any_write_and_read_command_call(
          "C0PRid0002xs03479xd0yj2102yd0zj1950zd0th2800te2800gr1go1308ga0gc0"
        ),
      ]
    )

  async def test_iswap_stacking_area(self):
    stacking_area = ResourceStack("stacking_area", direction="z")
    # for some reason it was like this at some point
    # self.lh.assign_resource(hotel, location=Coordinate(6, 414-63, 217.2 - 100))
    # f.lh.deck.assign_child_resource(hotel, location=Coordinate(6, 414-63, 231.7 - 100 +4.5))
    self.deck.assign_child_resource(stacking_area, location=Coordinate(6, 414, 226.2 - 3.33))

    assert self.plate.lid is not None
    await self.lh.move_lid(self.plate.lid, stacking_area)
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0001xs03479xd0yj1142yd0zj1950zd0gr1th2800te2800gw4go1308gb1245gt20ga0gc0"
        ),
        _any_write_and_read_command_call(
          "C0PRid0002xs00699xd0yj4567yd0zj2305zd0th2800te2800gr1go1308ga0gc0"
        ),
      ]
    )
    self.STAR._write_and_read_command.reset_mock()

    # Move lids back (reverse order)
    await self.lh.move_lid(cast(Lid, stacking_area.get_top_item()), self.plate)
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0003xs00699xd0yj4567yd0zj2305zd0gr1th2800te2800gw4go1308gb1245gt20ga0gc0"
        ),
        _any_write_and_read_command_call(
          "C0PRid0004xs03479xd0yj1142yd0zj1950zd0th2800te2800gr1go1308ga0gc0"
        ),
      ]
    )

  async def test_iswap_stacking_area_2lids(self):
    # for some reason it was like this at some point
    # self.lh.assign_resource(hotel, location=Coordinate(6, 414-63, 217.2 - 100))
    stacking_area = ResourceStack("stacking_area", direction="z")
    self.deck.assign_child_resource(stacking_area, location=Coordinate(6, 414, 226.2 - 3.33))

    assert self.plate.lid is not None and self.other_plate.lid is not None

    await self.lh.move_lid(self.plate.lid, stacking_area)
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0001xs03479xd0yj1142yd0zj1950zd0gr1th2800te2800gw4go1308gb1245gt20ga0gc0"
        ),
        _any_write_and_read_command_call(
          "C0PRid0002xs00699xd0yj4567yd0zj2305zd0th2800te2800gr1go1308ga0gc0"
        ),
      ]
    )
    self.STAR._write_and_read_command.reset_mock()

    await self.lh.move_lid(self.other_plate.lid, stacking_area)
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0003xs03479xd0yj2102yd0zj1950zd0gr1th2800te2800gw4go1308gb1245gt20ga0gc0"
        ),
        _any_write_and_read_command_call(
          "C0PRid0004xs00699xd0yj4567yd0zj2405zd0th2800te2800gr1go1308ga0gc0"
        ),
      ]
    )
    self.STAR._write_and_read_command.reset_mock()

    # Move lids back (reverse order)
    top_item = stacking_area.get_top_item()
    assert isinstance(top_item, Lid)
    await self.lh.move_lid(top_item, self.plate)
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0005xs00699xd0yj4567yd0zj2405zd0gr1th2800te2800gw4go1308gb1245gt20ga0gc0"
        ),
        _any_write_and_read_command_call(
          "C0PRid0006xs03479xd0yj1142yd0zj1950zd0th2800te2800gr1go1308ga0gc0"
        ),
      ]
    )
    self.STAR._write_and_read_command.reset_mock()

    top_item = stacking_area.get_top_item()
    assert isinstance(top_item, Lid)
    await self.lh.move_lid(top_item, self.other_plate)
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0007xs00699xd0yj4567yd0zj2305zd0gr1th2800te2800gw4go1308gb1245gt20ga0gc0"
        ),
        _any_write_and_read_command_call(
          "C0PRid0008xs03479xd0yj2102yd0zj1950zd0th2800te2800gr1go1308ga0gc0"
        ),
      ]
    )
    self.STAR._write_and_read_command.reset_mock()

  async def test_iswap_move_with_intermediate_locations(self):
    self.plt_car[1].resource.unassign()
    await self.lh.move_plate(
      self.plate,
      self.plt_car[1],
      intermediate_locations=[
        self.plt_car[2].get_absolute_location() + Coordinate(50, 0, 50),
        self.plt_car[3].get_absolute_location() + Coordinate(-50, 0, 50),
      ],
    )

    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0001xs03479xd0yj1142yd0zj1874zd0gr1th2800te2800gw4go1308gb1245gt20ga0gc0"
        ),
        _any_write_and_read_command_call("C0PMid0002xs03979xd0yj3062yd0zj2405zd0gr1th2800ga1xe4 1"),
        _any_write_and_read_command_call("C0PMid0003xs02979xd0yj4022yd0zj2405zd0gr1th2800ga1xe4 1"),
        _any_write_and_read_command_call(
          "C0PRid0004xs03479xd0yj2102yd0zj1874zd0th2800te2800gr1go1308ga0gc0"
        ),
      ]
    )

  async def test_discard_tips(self):
    await self.lh.pick_up_tips(self.tip_rack["A1:H1"])
    # {"id": 2, "kz": [000, 000, 000, 000, 000, 000, 000, 000], "vz": [000, 000, 000, 000, 000, 000, 000, 000]}
    self.STAR._write_and_read_command.side_effect = [
      "C0TRid0003kz000 000 000 000 000 000 000 000vz000 000 000 000 000 000 000 000"
    ]
    self.STAR._write_and_read_command.reset_mock()
    await self.lh.discard_tips()
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0TRid0003xp08000 08000 08000 08000 08000 08000 08000 08000yp3427 3337 3247 3157 3067 2977 2887 2797tm1 1 1 1 1 1 1 1tp1970tz1870th2450te2450ti0",
        )
      ]
    )

  async def test_portrait_tip_rack_handling(self):
    deck = STARLetDeck()
    lh = LiquidHandler(self.STAR, deck=deck)
    tip_car = TIP_CAR_288_C00(name="tip carrier")
    tip_car[0] = tr = hamilton_96_tiprack_1000uL(name="tips_01").rotated(z=90)
    assert tr.rotation.z == 90
    assert tr.location == Coordinate(78.3, -3.95, -6.0)
    deck.assign_child_resource(tip_car, track=2)
    await self.lh.stop()
    with self.assertWarns(DeprecationWarning):
      await lh.setup()
    try:
      await lh.pick_up_tips(tr["A4:A1"])
      self.STAR._write_and_read_command.side_effect = [
        "C0TRid0002kz000 000 000 000 000 000 000 000vz000 000 000 000 000 000 000 000"
      ]
      await lh.drop_tips(tr["A4:A1"])

      self.STAR._write_and_read_command.assert_has_calls(
        [
          _any_write_and_read_command_call(
            "C0TPid0002xp01360 01360 01360 01360 00000&yp1380 1290 1200 1110 0000&tm1 1 1 1 0&tt01tp2262tz2162th2450td0"
          ),
          _any_write_and_read_command_call(
            "C0TRid0003xp01360 01360 01360 01360 00000&yp1380 1290 1200 1110 0000&tm1 1 1 1 0&tp2262tz2182th2450te2450ti1"
          ),
        ]
      )
    finally:
      await lh.stop()

  def test_serialize(self):
    serialized = LiquidHandler(backend=STARBackend(), deck=STARLetDeck()).serialize()
    deserialized = LiquidHandler.deserialize(serialized)
    self.assertEqual(deserialized.__class__.__name__, "LiquidHandler")
    self.assertEqual(deserialized.backend.__class__.__name__, "STARBackend")

  async def test_move_core(self):
    self.plt_car[1].resource.unassign()
    await self.lh.move_plate(
      self.plate,
      self.plt_car[1],
      pickup_distance_from_top=13 - 3.33,
      use_arm="core",
      # kwargs specific to pickup and drop
      core_front_channel=7,
      return_core_gripper=True,
    )
    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0ZTid0001xs07975xd0ya1250yb1070pa07pb08tp2350tz2250th2800tt14"
        ),
        _any_write_and_read_command_call(
          "C0ZPid0002xs03479xd0yj1142yv0050zj1876zy0500yo0885yg0825yw15th2800te2800"
        ),
        _any_write_and_read_command_call(
          "C0ZRid0003xs03479xd0yj2102zj1876zi000zy0500yo0885th2800te2800"
        ),
        _any_write_and_read_command_call(
          "C0ZSid0004xs07975xd0ya1250yb1070tp2150tz2050th2800te2800"
        ),
      ]
    )


class STARIswapMovementTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.STAR = STARTestBackend()
    self.STAR._write_and_read_command = unittest.mock.AsyncMock()
    self.deck = STARLetDeck()
    self.lh = LiquidHandler(self.STAR, deck=self.deck)

    self.plt_car = PLT_CAR_L5MD_A00(name="plt_car")
    self.plt_car[0] = self.plate = celltreat_96_wellplate_350uL_Ub(name="plate", with_lid=True)
    self.deck.assign_child_resource(self.plt_car, track=15)

    self.plt_car2 = PLT_CAR_P3AC_A01(name="plt_car2")
    self.deck.assign_child_resource(self.plt_car2, track=3)

    self.STAR._num_channels = 8
    self.STAR._machine_conf = _DEFAULT_MACHINE_CONFIGURATION
    self.STAR._extended_conf = _DEFAULT_EXTENDED_CONFIGURATION
    self.STAR._core_parked = True
    self.STAR._iswap_parked = True
    with self.assertWarns(DeprecationWarning):
      await self.lh.setup()
    self.addAsyncCleanup(self.lh.stop)

  async def test_simple_movement(self):
    await self.lh.move_plate(self.plate, self.plt_car[1])
    await self.lh.move_plate(self.plate, self.plt_car[0])

    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0001xs04829xd0yj1141yd0zj2143zd0gr1th2800te2800gw4go1308gb1245gt20ga0gc0",
        ),
        _any_write_and_read_command_call(
          "C0PRid0002xs04829xd0yj2101yd0zj2143zd0th2800te2800gr1go1308ga0gc0",
        ),
        _any_write_and_read_command_call(
          "C0PPid0003xs04829xd0yj2101yd0zj2143zd0gr1th2800te2800gw4go1308gb1245gt20ga0gc0",
        ),
        _any_write_and_read_command_call(
          "C0PRid0004xs04829xd0yj1141yd0zj2143zd0th2800te2800gr1go1308ga0gc0"
        ),
      ]
    )

  async def test_movement_to_portrait_site_left(self):
    await self.lh.move_plate(self.plate, self.plt_car2[0], drop_direction=GripDirection.LEFT)
    await self.lh.move_plate(self.plate, self.plt_car[0], drop_direction=GripDirection.LEFT)

    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0001xs04829xd0yj1141yd0zj2143zd0gr1th2800te2800gw4go1308gb1245gt20ga0gc0",
        ),
        _any_write_and_read_command_call(
          "C0PRid0002xs02317xd0yj1644yd0zj1884zd0th2800te2800gr4go1308ga0gc0",
        ),
        _any_write_and_read_command_call(
          "C0PPid0003xs02317xd0yj1644yd0zj1884zd0gr1th2800te2800gw4go0881gb0818gt20ga0gc0",
        ),
        _any_write_and_read_command_call(
          "C0PRid0004xs04829xd0yj1141yd0zj2143zd0th2800te2800gr4go0881ga0gc0",
        ),
      ]
    )

  async def test_movement_to_portrait_site_right(self):
    await self.lh.move_plate(self.plate, self.plt_car2[0], drop_direction=GripDirection.RIGHT)
    await self.lh.move_plate(self.plate, self.plt_car[0], drop_direction=GripDirection.RIGHT)

    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0001xs04829xd0yj1141yd0zj2143zd0gr1th2800te2800gw4go1308gb1245gt20ga0gc0",
        ),
        _any_write_and_read_command_call(
          "C0PRid0002xs02317xd0yj1644yd0zj1884zd0th2800te2800gr2go1308ga0gc0",
        ),
        _any_write_and_read_command_call(
          "C0PPid0003xs02317xd0yj1644yd0zj1884zd0gr1th2800te2800gw4go0881gb0818gt20ga0gc0",
        ),
        _any_write_and_read_command_call(
          "C0PRid0004xs04829xd0yj1141yd0zj2143zd0th2800te2800gr2go0881ga0gc0",
        ),
      ]
    )

  async def test_move_lid_across_rotated_resources(self):
    self.plt_car2[0] = plate2 = celltreat_96_wellplate_350uL_Ub(
      name="plate2", with_lid=False
    ).rotated(z=270)
    self.plt_car2[1] = plate3 = celltreat_96_wellplate_350uL_Ub(
      name="plate3", with_lid=False
    ).rotated(z=90)

    assert plate2.get_absolute_location() == Coordinate(x=189.1, y=228.26, z=183.98)
    assert plate3.get_absolute_location() == Coordinate(x=274.21, y=246.5, z=183.98)

    assert self.plate.lid is not None
    await self.lh.move_lid(self.plate.lid, plate2, drop_direction=GripDirection.LEFT)
    assert plate2.lid is not None
    await self.lh.move_lid(plate2.lid, plate3, drop_direction=GripDirection.BACK)
    assert plate3.lid is not None
    await self.lh.move_lid(plate3.lid, self.plate, drop_direction=GripDirection.LEFT)

    self.STAR._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call(
          "C0PPid0001xs04829xd0yj1141yd0zj2242zd0gr1th2800te2800gw4go1308gb1245gt20ga0gc0",
        ),
        _any_write_and_read_command_call(
          "C0PRid0002xs02317xd0yj1644yd0zj1983zd0th2800te2800gr4go1308ga0gc0",
        ),
        _any_write_and_read_command_call(
          "C0PPid0003xs02317xd0yj1644yd0zj1983zd0gr1th2800te2800gw4go0885gb0822gt20ga0gc0",
        ),
        _any_write_and_read_command_call(
          "C0PRid0004xs02317xd0yj3104yd0zj1983zd0th2800te2800gr3go0885ga0gc0",
        ),
        _any_write_and_read_command_call(
          "C0PPid0005xs02317xd0yj3104yd0zj1983zd0gr1th2800te2800gw4go0885gb0822gt20ga0gc0",
        ),
        _any_write_and_read_command_call(
          "C0PRid0006xs04829xd0yj1141yd0zj2242zd0th2800te2800gr4go0885ga0gc0",
        ),
      ]
    )


class STARFoilTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.star = STARTestBackend()
    self.star._write_and_read_command = unittest.mock.AsyncMock()
    self.deck = STARLetDeck()
    self.lh = LiquidHandler(backend=self.star, deck=self.deck)

    tip_carrier = TIP_CAR_480_A00(name="tip_carrier")
    tip_carrier[1] = self.tip_rack = hamilton_96_tiprack_1000uL(name="tip_rack")
    self.deck.assign_child_resource(tip_carrier, track=1)

    plt_carrier = PLT_CAR_L5AC_A00(name="plt_carrier")
    plt_carrier[0] = self.plate = agenbio_1_troughplate_190mL_Fl(name="plate")
    self.well = self.plate.get_well("A1")
    self.deck.assign_child_resource(plt_carrier, track=10)

    self.star._num_channels = 8
    self.star._machine_conf = _DEFAULT_MACHINE_CONFIGURATION
    # setup() is mocked out below, so seed the left X-drive geometry it would normally
    # resolve; the foil ops move channels in X, which is now bounds-checked against x_range.
    self.star._extended_conf = replace(
      _DEFAULT_EXTENDED_CONFIGURATION,
      left_x_drive=replace(
        _DEFAULT_EXTENDED_CONFIGURATION.left_x_drive,
        width=370.0,
        x_range=(95.0, 1337.5),
        workspace_range=(-323.2, 1337.5),
      ),
    )
    self.star._core_parked = True
    self.star._iswap_parked = True
    with self.assertWarns(DeprecationWarning):
      await self.lh.setup()
    self.addAsyncCleanup(self.lh.stop)

    await self.lh.pick_up_tips(self.tip_rack["A1:H1"])

  async def test_pierce_foil_wide(self):
    aspiration_channels = [1, 2, 3, 4, 5, 6]
    hold_down_channels = [0, 7]
    self.star._write_and_read_command.side_effect = [
      "C0JXid0051er00/00",
      "C0RYid0052er00/00ry+1530 +1399 +1297 +1196 +1095 +0994 +0892 +0755",
      "C0JYid0053er00/00",
      "C0RZid0054er00/00rz+2476 +2476 +2476 +2476 +2476 +2476 +2476 +2476",
      "C0JZid0055er00/00",
      "C0RYid0056er00/00ry+1530 +1399 +1297 +1196 +1095 +0994 +0892 +0755",
      "C0JYid0057er00/00",
      "C0KZid0058er00/00",
      "C0KZid0059er00/00",
      "C0RZid0060er00/00rz+2256 +2083 +2083 +2083 +2083 +2083 +2083 +2256",
      "C0RZid0061er00/00rz+2256 +2083 +2083 +2083 +2083 +2083 +2083 +2256",
      "C0JZid0062er00/00",
      "C0ZAid0063er00/00",
    ]
    await self.star.pierce_foil(
      wells=[self.well],
      piercing_channels=aspiration_channels,
      hold_down_channels=hold_down_channels,
      move_inwards=4,
      one_by_one=False,
      spread="wide",
    )
    self.star._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call("C0JXid0003xs03702"),
        _any_write_and_read_command_call("C0RYid0004"),
        _any_write_and_read_command_call("C0JYid0005yp1530 1399 1297 1196 1095 0994 0892 0755"),
        _any_write_and_read_command_call("C0RZid0006"),
        _any_write_and_read_command_call("C0JZid0007zp2476 2083 2083 2083 2083 2083 2083 2476"),
        _any_write_and_read_command_call("C0RYid0008"),
        _any_write_and_read_command_call("C0JYid0009yp1530 1440 1350 1260 1170 1080 0990 0755"),
        _any_write_and_read_command_call("C0KZid0010pn08zj2256"),
        _any_write_and_read_command_call("C0KZid0011pn01zj2256"),
        _any_write_and_read_command_call("C0RZid0012"),
        _any_write_and_read_command_call("C0RZid0013"),
        _any_write_and_read_command_call("C0JZid0014zp2256 2406 2406 2406 2406 2406 2406 2256"),
        _any_write_and_read_command_call("C0ZAid0015"),
      ]
    )

  async def test_pierce_foil_tight(self):
    aspiration_channels = [1, 2, 3, 4, 5, 6]
    hold_down_channels = [0, 7]
    self.star._write_and_read_command.side_effect = [
      "C0JXid0064er00/00",
      "C0RYid0065er00/00ry+1530 +1399 +1297 +1196 +1095 +0994 +0892 +0755",
      "C0JYid0066er00/00",
      "C0RZid0067er00/00rz+2476 +2476 +2476 +2476 +2476 +2476 +2476 +2476",
      "C0JZid0068er00/00",
      "C0RYid0069er00/00ry+1530 +1370 +1280 +1190 +1100 +1010 +0920 +0755",
      "C0JYid0070er00/00",
      "C0KZid0071er00/00",
      "C0KZid0072er00/00",
      "C0RZid0073er00/00rz+2256 +2083 +2083 +2083 +2083 +2083 +2083 +2256",
      "C0RZid0074er00/00rz+2256 +2083 +2083 +2083 +2083 +2083 +2083 +2256",
      "C0JZid0075er00/00",
      "C0ZAid0076er00/00",
    ]
    await self.star.pierce_foil(
      wells=[self.well],
      piercing_channels=aspiration_channels,
      hold_down_channels=hold_down_channels,
      move_inwards=4,
      one_by_one=False,
      spread="tight",
    )
    self.star._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call("C0JXid0003xs03702"),
        _any_write_and_read_command_call("C0RYid0004"),
        _any_write_and_read_command_call("C0JYid0005yp1530 1370 1280 1190 1100 1010 0920 0755"),
        _any_write_and_read_command_call("C0RZid0006"),
        _any_write_and_read_command_call("C0JZid0007zp2476 2083 2083 2083 2083 2083 2083 2476"),
        _any_write_and_read_command_call("C0RYid0008"),
        _any_write_and_read_command_call("C0JYid0009yp1530 1440 1350 1260 1170 1080 0990 0755"),
        _any_write_and_read_command_call("C0KZid0010pn08zj2256"),
        _any_write_and_read_command_call("C0KZid0011pn01zj2256"),
        _any_write_and_read_command_call("C0RZid0012"),
        _any_write_and_read_command_call("C0RZid0013"),
        _any_write_and_read_command_call("C0JZid0014zp2256 2406 2406 2406 2406 2406 2406 2256"),
        _any_write_and_read_command_call("C0ZAid0015"),
      ]
    )

  async def test_pierce_foil_portrait_wide(self):
    self.plate.rotate(z=90)
    aspiration_channels = [1, 2, 3, 4, 5, 6]
    hold_down_channels = [0, 7]
    self.star._write_and_read_command.side_effect = [
      "C0JXid0170er00/00",
      "C0RYid0171er00/00ry+1530 +1399 +1297 +1196 +1095 +0994 +0892 +0755",
      "C0JYid0172er00/00",
      "C0RZid0173er00/00rz+2476 +2476 +2476 +2476 +2476 +2476 +2476 +2476",
      "C0JZid0174er00/00",
      "C0RYid0175er00/00ry+1825 +1735 +1582 +1429 +1275 +1122 +0969 +0755",
      "C0JYid0176er00/00",
      "C0KZid0177er00/00",
      "C0KZid0178er00/00",
      "C0RZid0179er00/00rz+2256 +2083 +2083 +2083 +2083 +2083 +2083 +2256",
      "C0RZid0180er00/00rz+2256 +2083 +2083 +2083 +2083 +2083 +2083 +2256",
      "C0JZid0181er00/00",
      "C0ZAid0182er00/00",
    ]
    await self.star.pierce_foil(
      wells=[self.well],
      piercing_channels=aspiration_channels,
      hold_down_channels=hold_down_channels,
      move_inwards=4,
      one_by_one=False,
      spread="tight",
    )
    self.star._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call("C0JXid0003xs02634"),
        _any_write_and_read_command_call("C0RYid0004"),
        _any_write_and_read_command_call("C0JYid0005yp1667 1577 1487 1397 1307 1217 1127 0755"),
        _any_write_and_read_command_call("C0RZid0006"),
        _any_write_and_read_command_call("C0JZid0007zp2476 2083 2083 2083 2083 2083 2083 2476"),
        _any_write_and_read_command_call("C0RYid0008"),
        _any_write_and_read_command_call("C0JYid0009yp1953 1863 1773 1683 1593 1503 1413 0755"),
        _any_write_and_read_command_call("C0KZid0010pn08zj2256"),
        _any_write_and_read_command_call("C0KZid0011pn01zj2256"),
        _any_write_and_read_command_call("C0RZid0012"),
        _any_write_and_read_command_call("C0RZid0013"),
        _any_write_and_read_command_call("C0JZid0014zp2256 2406 2406 2406 2406 2406 2406 2256"),
        _any_write_and_read_command_call("C0ZAid0015"),
      ]
    )

  async def test_pierce_foil_portrait_tight(self):
    self.plate.rotate(z=90)
    aspiration_channels = [1, 2, 3, 4, 5, 6]
    hold_down_channels = [0, 7]
    self.star._write_and_read_command.side_effect = [
      "C0JXid0183er00/00",
      "C0RYid0184er00/00ry+1953 +1735 +1582 +1429 +1275 +1122 +0969 +0755",
      "C0JYid0185er00/00",
      "C0RZid0186er00/00rz+2476 +2476 +2476 +2476 +2476 +2476 +2476 +2476",
      "C0JZid0187er00/00",
      "C0RYid0188er00/00ry+1953 +1577 +1487 +1397 +1307 +1217 +1127 +0755",
      "C0JYid0189er00/00",
      "C0KZid0190er00/00",
      "C0KZid0191er00/00",
      "C0RZid0192er00/00rz+2256 +2083 +2083 +2083 +2083 +2083 +2083 +2256",
      "C0RZid0193er00/00rz+2256 +2083 +2083 +2083 +2083 +2083 +2083 +2256",
      "C0JZid0194er00/00",
      "C0ZAid0195er00/00",
    ]
    await self.star.pierce_foil(
      wells=[self.well],
      piercing_channels=aspiration_channels,
      hold_down_channels=hold_down_channels,
      move_inwards=4,
      one_by_one=False,
      spread="tight",
    )
    self.star._write_and_read_command.assert_has_calls(
      [
        _any_write_and_read_command_call("C0JXid0003xs02634"),
        _any_write_and_read_command_call("C0RYid0004"),
        _any_write_and_read_command_call("C0JYid0005yp1953 1577 1487 1397 1307 1217 1127 0755"),
        _any_write_and_read_command_call("C0RZid0006"),
        _any_write_and_read_command_call("C0JZid0007zp2476 2083 2083 2083 2083 2083 2083 2476"),
        _any_write_and_read_command_call("C0RYid0008"),
        _any_write_and_read_command_call("C0JYid0009yp1953 1863 1773 1683 1593 1503 1413 0755"),
        _any_write_and_read_command_call("C0KZid0010pn08zj2256"),
        _any_write_and_read_command_call("C0KZid0011pn01zj2256"),
        _any_write_and_read_command_call("C0RZid0012"),
        _any_write_and_read_command_call("C0RZid0013"),
        _any_write_and_read_command_call("C0JZid0014zp2256 2406 2406 2406 2406 2406 2406 2256"),
        _any_write_and_read_command_call("C0ZAid0015"),
      ]
    )


class TestSTARTipPickupDropAllSizes(unittest.IsolatedAsyncioTestCase):
  """Test STAR tip pickup and drop Z positions for 10, 50, 300, and 1000 uL tips."""

  async def asyncSetUp(self):
    self.backend = STARTestBackend()
    self.backend._write_and_read_command = unittest.mock.AsyncMock()
    self.backend._num_channels = 8
    self.backend._machine_conf = _DEFAULT_MACHINE_CONFIGURATION
    self.backend._extended_conf = _DEFAULT_EXTENDED_CONFIGURATION
    self.backend._core_parked = True
    self.backend._iswap_parked = True

    self.deck = STARLetDeck()
    self.lh = LiquidHandler(self.backend, deck=self.deck)

    self.tip_car = TIP_CAR_480_A00(name="tip_carrier")
    self.deck.assign_child_resource(self.tip_car, track=1)

    with self.assertWarns(DeprecationWarning):
      await self.lh.setup()
    self.addAsyncCleanup(self.lh.stop)
    set_tip_tracking(enabled=False)

  def _get_tp_tz_from_calls(self, cmd_prefix: str):
    """Extract tp and tz values from mock calls matching the command prefix."""
    for call in self.backend._write_and_read_command.call_args_list:
      cmd = call.kwargs.get("cmd", "")
      if cmd.startswith(cmd_prefix):
        parsed = parse_star_fw_string(cmd, "tp####tz####")
        return parsed.get("tp"), parsed.get("tz")
    return None, None

  async def test_10uL_tips(self):
    from pylabrobot.resources.hamilton.tip_racks import hamilton_96_tiprack_10uL

    tip_rack = hamilton_96_tiprack_10uL("tips")
    self.tip_car[1] = tip_rack

    await self.lh.pick_up_tips(tip_rack["A1"])
    tp, tz = self._get_tp_tz_from_calls("C0TP")
    self.assertEqual(tp, 2224)
    self.assertEqual(tz, 2164)

    self.backend._write_and_read_command.reset_mock()
    self.backend._write_and_read_command.return_value = (
      "C0TRid0001kz000 000 000 000 000 000 000 000vz000 000 000 000 000 000 000 000"
    )
    await self.lh.drop_tips(tip_rack["A1"])
    tp, tz = self._get_tp_tz_from_calls("C0TR")
    self.assertEqual(tp, 2224)
    self.assertEqual(tz, 2144)

    tip_rack.unassign()

  async def test_50uL_tips(self):
    from pylabrobot.resources.hamilton.tip_racks import hamilton_96_tiprack_50uL

    tip_rack = hamilton_96_tiprack_50uL("tips")
    self.tip_car[1] = tip_rack

    await self.lh.pick_up_tips(tip_rack["A1"])
    tp, tz = self._get_tp_tz_from_calls("C0TP")
    self.assertEqual(tp, 2244)
    self.assertEqual(tz, 2164)

    self.backend._write_and_read_command.reset_mock()
    self.backend._write_and_read_command.return_value = (
      "C0TRid0001kz000 000 000 000 000 000 000 000vz000 000 000 000 000 000 000 000"
    )
    await self.lh.drop_tips(tip_rack["A1"])
    tp, tz = self._get_tp_tz_from_calls("C0TR")
    self.assertEqual(tp, 2244)
    self.assertEqual(tz, 2164)

    tip_rack.unassign()

  async def test_300uL_tips(self):
    from pylabrobot.resources.hamilton.tip_racks import hamilton_96_tiprack_300uL

    tip_rack = hamilton_96_tiprack_300uL("tips")
    self.tip_car[1] = tip_rack

    await self.lh.pick_up_tips(tip_rack["A1"])
    tp, tz = self._get_tp_tz_from_calls("C0TP")
    self.assertEqual(tp, 2244)
    self.assertEqual(tz, 2164)

    self.backend._write_and_read_command.reset_mock()
    self.backend._write_and_read_command.return_value = (
      "C0TRid0001kz000 000 000 000 000 000 000 000vz000 000 000 000 000 000 000 000"
    )
    await self.lh.drop_tips(tip_rack["A1"])
    tp, tz = self._get_tp_tz_from_calls("C0TR")
    self.assertEqual(tp, 2244)
    self.assertEqual(tz, 2164)

    tip_rack.unassign()

  async def test_1000uL_tips(self):
    from pylabrobot.resources.hamilton.tip_racks import hamilton_96_tiprack_1000uL

    tip_rack = hamilton_96_tiprack_1000uL("tips")
    self.tip_car[1] = tip_rack

    await self.lh.pick_up_tips(tip_rack["A1"])
    tp, tz = self._get_tp_tz_from_calls("C0TP")
    self.assertEqual(tp, 2264)
    self.assertEqual(tz, 2164)

    self.backend._write_and_read_command.reset_mock()
    self.backend._write_and_read_command.return_value = (
      "C0TRid0001kz000 000 000 000 000 000 000 000vz000 000 000 000 000 000 000 000"
    )
    await self.lh.drop_tips(tip_rack["A1"])
    tp, tz = self._get_tp_tz_from_calls("C0TR")
    self.assertEqual(tp, 2264)
    self.assertEqual(tz, 2184)

    tip_rack.unassign()

  async def test_300uL_filter_slim_tips(self):
    from pylabrobot.resources.hamilton.tip_racks import hamilton_96_tiprack_300uL_filter_slim

    tip_rack = hamilton_96_tiprack_300uL_filter_slim("tips")
    self.tip_car[1] = tip_rack

    # the slim tip has the high volume collar, so it is picked up like a 1000 uL tip
    await self.lh.pick_up_tips(tip_rack["A1"])
    self.assertIn(
      "C0TTid0001tt01tf1tl0870tv03450tg3tu0",
      [call.kwargs.get("cmd") for call in self.backend._write_and_read_command.call_args_list],
    )
    tp, tz = self._get_tp_tz_from_calls("C0TP")
    self.assertEqual(tp, 2264)
    self.assertEqual(tz, 2164)

    self.backend._write_and_read_command.reset_mock()
    self.backend._write_and_read_command.return_value = (
      "C0TRid0001kz000 000 000 000 000 000 000 000vz000 000 000 000 000 000 000 000"
    )
    await self.lh.drop_tips(tip_rack["A1"])
    tp, tz = self._get_tp_tz_from_calls("C0TR")
    self.assertEqual(tp, 2264)
    self.assertEqual(tz, 2184)

    tip_rack.unassign()


class TestSTAR96TipPickupDropAllSizes(unittest.IsolatedAsyncioTestCase):
  """Test 96-head pickup and return commands for all four standard filtered tip sizes."""

  async def asyncSetUp(self):
    """Set up a full-size STAR with mocked communication and a carrier on track 1."""
    self.backend = STARTestBackend()
    self.backend._write_and_read_command = unittest.mock.AsyncMock()
    self.backend._num_channels = 8
    self.backend._machine_conf = _DEFAULT_MACHINE_CONFIGURATION
    self.backend._extended_conf = _DEFAULT_EXTENDED_CONFIGURATION
    self.backend._core_parked = True
    self.backend._iswap_parked = True

    self.deck = STARDeck()
    self.tip_car = TIP_CAR_480_A00("tip_carrier")
    self.deck.assign_child_resource(self.tip_car, track=1)
    self.lh = LiquidHandler(self.backend, deck=self.deck)
    await self.lh.setup()
    self.addAsyncCleanup(self.lh.stop)

  async def _assert_pickup_and_return(self, rack: TipRack) -> None:
    """Assert pickup and return commands at the verified 216.4 mm deposit height."""
    self.tip_car[1] = rack
    with no_tip_tracking():
      await self.lh.pick_up_tips96(rack)
      with self.subTest(operation="pickup"):
        self.backend._write_and_read_command.assert_has_calls(
          [
            _any_write_and_read_command_call("C0EPid0003xs01179xd0yh2418tt01wu0za2164zh2450ze2450"),
          ]
        )

      self.backend._write_and_read_command.reset_mock()
      await self.lh.return_tips96()
      with self.subTest(operation="return"):
        self.backend._write_and_read_command.assert_has_calls(
          [
            _any_write_and_read_command_call("C0ERid0004xs01179xd0yh2418za2164zh2450ze2450"),
          ]
        )

  async def test_10uL_tips(self):
    """Check 10 uL filtered-tip pickup and return with the 96 head."""
    await self._assert_pickup_and_return(cast(TipRack, hamilton_96_tiprack_10uL_filter("tips")))

  async def test_50uL_tips(self):
    """Check 50 uL filtered-tip pickup and return with the 96 head."""
    await self._assert_pickup_and_return(cast(TipRack, hamilton_96_tiprack_50uL_filter("tips")))

  async def test_300uL_tips(self):
    """Check 300 uL filtered-tip pickup and return with the 96 head."""
    await self._assert_pickup_and_return(cast(TipRack, hamilton_96_tiprack_300uL_filter("tips")))

  async def test_1000uL_tips(self):
    """Check 1000 uL filtered-tip pickup and return with the 96 head."""
    await self._assert_pickup_and_return(cast(TipRack, hamilton_96_tiprack_1000uL_filter("tips")))


class TestNestedTipRacksGroundTruth(unittest.IsolatedAsyncioTestCase):
  """The firmware commands sent for Hamilton tip racks, checked command by command."""

  async def asyncSetUp(self):
    self.backend = STARTestBackend()
    self.backend._write_and_read_command = unittest.mock.AsyncMock(
      side_effect=lambda **kwargs: (
        "C0TRid0000kz000 000 000 000 000 000 000 000vz000 000 000 000 000 000 000 000"
        if kwargs["cmd"].startswith("C0TR")
        else None
      )
    )
    self.backend._num_channels = 8
    self.backend._machine_conf = _DEFAULT_MACHINE_CONFIGURATION
    self.backend._extended_conf = _DEFAULT_EXTENDED_CONFIGURATION
    self.backend._core_parked = True
    self.backend._iswap_parked = True
    self.deck = STARDeck()
    self.module = hamilton_mfx_resourceholder_ntr("ntr4_module")
    self.deck.assign_child_resource(
      hamilton_mfx_carrier_L5_base("mfx_carrier", modules={3: self.module}),
      location=Coordinate(932.5, 63, 100),
    )
    self.ntr_carrier = hamilton_tip_carrier_L5_ntr_a00("ntr_carrier")
    self.deck.assign_child_resource(self.ntr_carrier, location=Coordinate(752.5, 63, 100))
    self.lh = LiquidHandler(self.backend, deck=self.deck)
    await self.lh.setup()
    self.addAsyncCleanup(self.lh.stop)

  def _sent(self, prefix: str) -> str:
    """The last command sent that starts with `prefix`, without its id."""
    cmds = [str(c.kwargs["cmd"]) for c in self.backend._write_and_read_command.call_args_list]
    cmd = [c for c in cmds if c.startswith(prefix)][-1]
    return cmd[:4] + cmd[10:]

  async def test_nested_tip_racks_ground_truth(self):
    # tip definition (without its index), start of pick up, channel drop tp/tz (stop disc, ti1)
    tips = {
      "10uL": (hamilton_96_tiprack_10uL_NTR, "tf0tl0219tv00150tg1tu0", "1900", "tp1900tz1820"),
      "50uL": (hamilton_96_tiprack_50uL_NTR, "tf0tl0424tv00650tg2tu0", "1920", "tp1920tz1840"),
      "300uL": (hamilton_96_tiprack_300uL_NTR, "tf0tl0519tv04000tg2tu0", "1920", "tp1920tz1840"),
    }
    # holder, A1 x and A1 y (0.1 mm)
    holders = [
      ("ntr4_module", self.module, "09505", {"10uL": 4340, "50uL": 4340, "300uL": 4340}),
      ("ntr_carrier", None, "07704", {"10uL": 1458, "50uL": 2418, "300uL": 3378}),
    ]
    sites = {"10uL": 0, "50uL": 1, "300uL": 2}
    for holder_name, module, xs, a1_y in holders:
      for size, (rack_fn, definition, tp, drop) in tips.items():
        with self.subTest(holder=holder_name, tip=size):
          rack = rack_fn(f"{holder_name}_{size}")
          holder = module if module is not None else self.ntr_carrier.sites[sites[size]]
          holder.assign_child_resource(rack)
          self.backend._write_and_read_command.reset_mock()
          self.backend._tip_type_indices.clear()  # so each tip definition is sent

          await self.lh.pick_up_tips(rack["A1:H1"])
          xp = " ".join([xs] * 8)
          yp = " ".join(f"{a1_y[size] - 90 * row:04}" for row in range(8))
          tt = self._sent("C0TT")[4:8]
          self.assertEqual(self._sent("C0TT"), f"C0TT{tt}{definition}")
          self.assertEqual(
            self._sent("C0TP"), f"C0TPxp{xp}yp{yp}tm1 1 1 1 1 1 1 1{tt}tp{tp}tz1840th2450td0"
          )
          await self.lh.drop_tips(rack["A1:H1"])
          self.assertEqual(
            self._sent("C0TR"), f"C0TRxp{xp}yp{yp}tm1 1 1 1 1 1 1 1{drop}th2450te2450ti1"
          )

          await self.lh.pick_up_tips96(rack)
          self.assertEqual(
            self._sent("C0EP"), f"C0EPxs{xs}xd0yh{a1_y[size]}{tt}wu0za1840zh2450ze2450"
          )
          await self.lh.drop_tips96(rack)
          self.assertEqual(self._sent("C0ER"), f"C0ERxs{xs}xd0yh{a1_y[size]}za1840zh2450ze2450")
          holder.unassign_child_resource(rack)

  async def test_framed_tip_racks_on_tip_carrier_ground_truth(self):
    carriers = {x: TIP_CAR_480BC_A00(f"tip_carrier_{x}") for x in (437.5, 572.5)}
    for x, carrier in carriers.items():
      self.deck.assign_child_resource(carrier, location=Coordinate(x, 63, 100))
    # rack, carrier x, site, tip definition (without its index), start of pick up, channel drop tp/tz
    # (stop disc, ti1)
    tips = [
      (hamilton_96_tiprack_10uL, 437.5, 0, "tf0tl0219tv00150tg1tu0", "2224", "tp2224tz2144"),
      (hamilton_96_tiprack_50uL, 437.5, 1, "tf0tl0424tv00650tg2tu0", "2244", "tp2244tz2164"),
      (hamilton_96_tiprack_300uL, 572.5, 0, "tf0tl0519tv04000tg2tu0", "2244", "tp2244tz2164"),
      (
        hamilton_96_tiprack_1000uL_filter,
        572.5,
        1,
        "tf1tl0871tv10650tg3tu0",
        "2264",
        "tp2264tz2184",
      ),
      (
        hamilton_96_tiprack_300uL_filter_slim,
        572.5,
        2,
        "tf1tl0870tv03450tg3tu0",
        "2264",
        "tp2264tz2184",
      ),
    ]
    for rack_fn, carrier_x, site, definition, tp, drop in tips:
      with self.subTest(tip=rack_fn.__name__):
        rack = rack_fn(rack_fn.__name__)
        carriers[carrier_x][site] = rack
        self.backend._write_and_read_command.reset_mock()
        self.backend._tip_type_indices.clear()  # so each tip definition is sent

        await self.lh.pick_up_tips(rack["A1:H1"])
        # A1 17.9 mm right of the carrier, 145.8 mm back on site 0, sites 96 mm apart (0.1 mm)
        xs = f"{round(carrier_x * 10) + 179:05}"
        a1_y = 1458 + 960 * site
        xp = " ".join([xs] * 8)
        yp = " ".join(f"{a1_y - 90 * row:04}" for row in range(8))
        tt = self._sent("C0TT")[4:8]
        self.assertEqual(self._sent("C0TT"), f"C0TT{tt}{definition}")
        self.assertEqual(
          self._sent("C0TP"), f"C0TPxp{xp}yp{yp}tm1 1 1 1 1 1 1 1{tt}tp{tp}tz2164th2450td0"
        )
        await self.lh.drop_tips(rack["A1:H1"])
        self.assertEqual(
          self._sent("C0TR"), f"C0TRxp{xp}yp{yp}tm1 1 1 1 1 1 1 1{drop}th2450te2450ti1"
        )

        await self.lh.pick_up_tips96(rack)
        self.assertEqual(self._sent("C0EP"), f"C0EPxs{xs}xd0yh{a1_y}{tt}wu0za2164zh2450ze2450")
        await self.lh.drop_tips96(rack)
        self.assertEqual(self._sent("C0ER"), f"C0ERxs{xs}xd0yh{a1_y}za2164zh2450ze2450")

  async def test_framed_tip_racks_on_mfx_tip_module_ground_truth(self):
    self.deck.unassign_child_resource(self.ntr_carrier)
    modules: dict[int, ResourceHolder] = {
      slot: hamilton_mfx_tiprackholder_standard(f"tip_module_{slot}") for slot in (0, 1, 4)
    }
    self.deck.assign_child_resource(
      hamilton_mfx_carrier_L5_base("tip_module_carrier", modules=modules),
      location=Coordinate(752.5, 63, 100),
    )
    # rack, slot, tip definition (without its index), start of pick up, channel drop tp/tz (stop disc,
    # ti1)
    tips = [
      (hamilton_96_tiprack_10uL, 0, "tf0tl0219tv00150tg1tu0", "2222", "tp2222tz2142"),
      (hamilton_96_tiprack_50uL, 1, "tf0tl0424tv00650tg2tu0", "2242", "tp2242tz2162"),
      (hamilton_96_tiprack_300uL, 4, "tf0tl0519tv04000tg2tu0", "2242", "tp2242tz2162"),
      (hamilton_96_tiprack_1000uL_filter, 0, "tf1tl0871tv10650tg3tu0", "2262", "tp2262tz2182"),
      (hamilton_96_tiprack_300uL_filter_slim, 1, "tf1tl0870tv03450tg3tu0", "2262", "tp2262tz2182"),
    ]
    for rack_fn, slot, definition, tp, drop in tips:
      with self.subTest(tip=rack_fn.__name__):
        rack = rack_fn(rack_fn.__name__)
        modules[slot].assign_child_resource(rack)
        self.backend._write_and_read_command.reset_mock()
        self.backend._tip_type_indices.clear()  # so each tip definition is sent

        await self.lh.pick_up_tips(rack["A1:H1"])
        # A1 146.0 mm back on slot 0, slots 96 mm apart (0.1 mm)
        xs, a1_y = "07705", 1460 + 960 * slot
        xp = " ".join([xs] * 8)
        yp = " ".join(f"{a1_y - 90 * row:04}" for row in range(8))
        tt = self._sent("C0TT")[4:8]
        self.assertEqual(self._sent("C0TT"), f"C0TT{tt}{definition}")
        self.assertEqual(
          self._sent("C0TP"), f"C0TPxp{xp}yp{yp}tm1 1 1 1 1 1 1 1{tt}tp{tp}tz2162th2450td0"
        )
        await self.lh.drop_tips(rack["A1:H1"])
        self.assertEqual(
          self._sent("C0TR"), f"C0TRxp{xp}yp{yp}tm1 1 1 1 1 1 1 1{drop}th2450te2450ti1"
        )

        await self.lh.pick_up_tips96(rack)
        self.assertEqual(self._sent("C0EP"), f"C0EPxs{xs}xd0yh{a1_y}{tt}wu0za2162zh2450ze2450")
        await self.lh.drop_tips96(rack)
        self.assertEqual(self._sent("C0ER"), f"C0ERxs{xs}xd0yh{a1_y}za2162zh2450ze2450")
        modules[slot].unassign_child_resource(rack)


class TestChannelsMinimumYSpacing(unittest.IsolatedAsyncioTestCase):
  """Test that different channel spacing configurations produce different behavior.

  Real firmware VY responses captured from hardware (GitHub issue #822):
    - 4-channel 18mm single-rail:  P<n>VYid<id>yc194 388 1  (yc[1]=388 → 18.0mm)
    - 8-channel 9mm standard:      P<n>VYid<id>yc000 194 0  (yc[1]=194 → 9.0mm)
  """

  # -- can_reach_position: reachability shrinks with wider spacing ----------------

  async def test_can_reach_4ch_18mm_rejects_position_reachable_at_9mm(self):
    """A position reachable by channel 0 at 9mm spacing is unreachable at 18mm spacing.

    Channel 0 (backmost) min_y = left_arm_min_y_position + sum(spacings[1..3])
      At 9mm:  6 + 9*3 = 33   → y=33 reachable
      At 18mm: 6 + 18*3 = 60  → y=33 unreachable
    """
    backend = STARBackend()
    backend._num_channels = 4
    backend._extended_conf = _DEFAULT_EXTENDED_CONFIGURATION

    backend._channels_minimum_y_spacing = [9.0] * 4
    self.assertTrue(backend.can_reach_position(0, Coordinate(100, 33, 100)))

    backend._channels_minimum_y_spacing = [18.0] * 4
    self.assertFalse(backend.can_reach_position(0, Coordinate(100, 33, 100)))

  async def test_can_reach_4ch_18mm_rejects_back_channel_too_far_back(self):
    """At 18mm spacing, the backmost channel has a lower max_y than at 9mm.

    Channel 3 (frontmost) max_y = pip_maximal_y_position - sum(spacings[0..2])
      At 9mm:  606.5 - 9*3 = 579.5  → y=574 reachable
      At 18mm: 606.5 - 18*3 = 552.5 → y=574 unreachable
    """
    backend = STARBackend()
    backend._num_channels = 4
    backend._extended_conf = _DEFAULT_EXTENDED_CONFIGURATION

    backend._channels_minimum_y_spacing = [9.0] * 4
    self.assertTrue(backend.can_reach_position(3, Coordinate(100, 574, 100)))

    backend._channels_minimum_y_spacing = [18.0] * 4
    self.assertFalse(backend.can_reach_position(3, Coordinate(100, 574, 100)))

  # -- position_channels_in_y_direction: validation rejects tight positions -------

  def _make_star_backend(self, num_channels, spacings):
    """Helper: create a STARBackend with given channel count and spacings, mocking I/O."""
    backend = STARBackend()
    backend._num_channels = num_channels
    backend._channels_minimum_y_spacing = list(spacings)
    backend._extended_conf = _DEFAULT_EXTENDED_CONFIGURATION
    backend.id_ = 0
    backend._write_and_read_command = unittest.mock.AsyncMock()
    backend.get_channels_y_positions = unittest.mock.AsyncMock()
    return backend

  async def test_position_channels_rejects_9mm_gap_when_spacing_is_18mm(self):
    """With make_space=False, channels 9mm apart pass validation at 9mm but are rejected at 18mm."""
    spread_positions = {0: 100.0, 1: 91.0, 2: 82.0, 3: 73.0}

    # At 9mm: channels spaced 9mm apart → valid, JY command is sent.
    backend_9 = self._make_star_backend(4, [9.0] * 4)
    backend_9.get_channels_y_positions.return_value = dict(spread_positions)
    await backend_9.position_channels_in_y_direction(spread_positions, make_space=False)
    self.assertTrue(backend_9._write_and_read_command.called)

    # At 18mm: same positions → rejected.
    backend_18 = self._make_star_backend(4, [18.0] * 4)
    backend_18.get_channels_y_positions.return_value = dict(spread_positions)
    with self.assertRaises(ValueError):
      await backend_18.position_channels_in_y_direction(spread_positions, make_space=False)

  async def test_position_channels_make_space_spreads_wider_at_18mm(self):
    """make_space=True pushes non-target channels further apart at 18mm than at 9mm.

    Move only channel 2 to y=40. make_space adjusts channels 3 (in front of channel 2)
    to respect minimum spacing. At 9mm it pushes channel 3 to 31, at 18mm to 22.
    """
    current = {0: 300.0, 1: 200.0, 2: 100.0, 3: 50.0}
    requested = {2: 40.0}

    # At 9mm: channel 3 must be ≤ 40 - 9 = 31.
    backend_9 = self._make_star_backend(4, [9.0] * 4)
    backend_9.get_channels_y_positions.return_value = dict(current)
    await backend_9.position_channels_in_y_direction(dict(requested), make_space=True)
    cmd_9mm = backend_9._write_and_read_command.call_args.kwargs["cmd"]
    # Channel 3 pushed to 31.0 → 310 increments.
    self.assertIn("0310", cmd_9mm)

    # At 18mm: channel 3 must be ≤ 40 - 18 = 22.
    backend_18 = self._make_star_backend(4, [18.0] * 4)
    backend_18.get_channels_y_positions.return_value = dict(current)
    await backend_18.position_channels_in_y_direction(dict(requested), make_space=True)
    cmd_18mm = backend_18._write_and_read_command.call_args.kwargs["cmd"]
    # Channel 3 pushed to 22.0 → 220 increments.
    self.assertIn("0220", cmd_18mm)

    # The JY commands must differ.
    self.assertNotEqual(cmd_9mm, cmd_18mm)


class TestProbeLiquidHeights(unittest.IsolatedAsyncioTestCase):
  """Tests for probe_liquid_heights: detection dispatch, replicates, error handling."""

  async def asyncSetUp(self):
    self.STAR = STARTestBackend(read_timeout=1)
    self.STAR._write_and_read_command = unittest.mock.AsyncMock()

    self.deck = STARLetDeck()
    self.lh = LiquidHandler(self.STAR, deck=self.deck)

    self.tip_car = TIP_CAR_480_A00(name="tip carrier")
    self.tip_car[1] = self.tip_rack = hamilton_96_tiprack_300uL_filter(name="tip_rack_01")
    self.deck.assign_child_resource(self.tip_car, track=1)

    self.plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    self.plt_car[0] = self.plate = cor_96_wellplate_360uL_Fb(name="plate_01")
    self.deck.assign_child_resource(self.plt_car, track=9)

    self.STAR._num_channels = 8
    self.STAR._machine_conf = _DEFAULT_MACHINE_CONFIGURATION
    self.STAR._extended_conf = _DEFAULT_EXTENDED_CONFIGURATION
    self.STAR._core_parked = True
    self.STAR._iswap_parked = True
    await self.lh.setup()

    set_tip_tracking(enabled=False)

  async def asyncTearDown(self):
    await self.lh.stop()

  def _put_tips_on_channels(self, channels):
    tip = self.tip_rack.get_tip("A1")
    self.lh.update_head_state({ch: tip for ch in channels})

  def _standard_mocks(self, detect_side_effect=None):
    """Return a context manager stack with standard mocks for probe_liquid_heights."""
    mocks = {}

    if detect_side_effect is None:
      detect_side_effect = unittest.mock.AsyncMock(return_value=None)
    mocks["detect"] = unittest.mock.patch.object(
      self.STAR, "_move_z_drive_to_liquid_surface_using_clld", detect_side_effect
    )
    mocks["plld"] = unittest.mock.patch.object(
      self.STAR,
      "_search_for_surface_using_plld",
      new_callable=unittest.mock.AsyncMock,
      return_value=None,
    )
    mocks["pip_height"] = unittest.mock.patch.object(
      self.STAR,
      "request_pip_height_last_lld",
      new_callable=unittest.mock.AsyncMock,
      return_value=list(range(12)),
    )
    mocks["tip_len"] = unittest.mock.patch.object(
      self.STAR,
      "request_tip_len_on_channel",
      new_callable=unittest.mock.AsyncMock,
      return_value=59.9,
    )
    mocks["tip_presence"] = unittest.mock.patch.object(
      self.STAR,
      "request_tip_presence",
      new_callable=unittest.mock.AsyncMock,
      return_value={i: True for i in range(8)},
    )
    mocks["z_safety"] = unittest.mock.patch.object(
      self.STAR,
      "move_all_channels_in_z_safety",
      new_callable=unittest.mock.AsyncMock,
    )
    mocks["move_x"] = unittest.mock.patch.object(
      self.STAR,
      "move_channel_x",
      new_callable=unittest.mock.AsyncMock,
    )
    mocks["pos_y"] = unittest.mock.patch.object(
      self.STAR,
      "position_channels_in_y_direction",
      new_callable=unittest.mock.AsyncMock,
    )
    mocks["backmost_y"] = unittest.mock.patch.object(
      self.STAR.extended_conf,
      "pip_maximal_y_position",
      606.5,
    )
    return mocks

  async def test_single_well_returns_height(self):
    well = self.plate.get_item("A1")
    self._put_tips_on_channels([0])

    mocks = self._standard_mocks()
    with contextlib.ExitStack() as stack:
      for v in mocks.values():
        stack.enter_context(v)
      result = await self.STAR.probe_liquid_heights(containers=[well], use_channels=[0])

    # request_pip_height_last_lld returns list(range(12)), so channel 0 gets height 0.
    # relative = 0 - cavity_bottom_z
    self.assertEqual(len(result), 1)
    self.assertAlmostEqual(result[0], 0 - well.get_absolute_location("c", "c", "cavity_bottom").z)

  async def test_n_replicates(self):
    well = self.plate.get_item("A1")
    self._put_tips_on_channels([0])

    mock_detect = unittest.mock.AsyncMock(return_value=None)
    mocks = self._standard_mocks(detect_side_effect=mock_detect)
    with contextlib.ExitStack() as stack:
      for v in mocks.values():
        stack.enter_context(v)
      await self.STAR.probe_liquid_heights(containers=[well], use_channels=[0], n_replicates=3)

    self.assertEqual(mock_detect.await_count, 3)

  async def test_no_liquid_detected_returns_zero(self):
    well = self.plate.get_item("A1")
    self._put_tips_on_channels([0])

    error = STARFirmwareError(
      errors={
        "Pipetting channel 1": UnknownHamiltonError(
          message="No liquid level found",
          trace_information=0,
          raw_response="no liquid level found",
          raw_module="P1",
        )
      },
      raw_response="no liquid level found",
    )

    async def raise_error(**kwargs):
      raise error

    mocks = self._standard_mocks(
      detect_side_effect=unittest.mock.AsyncMock(side_effect=raise_error)
    )
    with contextlib.ExitStack() as stack:
      for v in mocks.values():
        stack.enter_context(v)
      result = await self.STAR.probe_liquid_heights(containers=[well], use_channels=[0])

    self.assertEqual(result[0], 0.0)

  async def test_inconsistent_replicates_raises(self):
    well = self.plate.get_item("A1")
    self._put_tips_on_channels([0])

    error = STARFirmwareError(
      errors={
        "Pipetting channel 1": UnknownHamiltonError(
          message="No liquid level found",
          trace_information=0,
          raw_response="no liquid level found",
          raw_module="P1",
        )
      },
      raw_response="no liquid level found",
    )

    call_count = 0

    async def side_effect(**kwargs):
      nonlocal call_count
      call_count += 1
      if call_count == 1:
        return None
      raise error

    mocks = self._standard_mocks(
      detect_side_effect=unittest.mock.AsyncMock(side_effect=side_effect)
    )
    with contextlib.ExitStack() as stack:
      for v in mocks.values():
        stack.enter_context(v)
      with self.assertRaises(RuntimeError):
        await self.STAR.probe_liquid_heights(containers=[well], use_channels=[0], n_replicates=2)

  async def test_pressure_lld_mode(self):
    well = self.plate.get_item("A1")
    self._put_tips_on_channels([0])

    mocks = self._standard_mocks()
    with contextlib.ExitStack() as stack:
      entered = {k: stack.enter_context(v) for k, v in mocks.items()}
      await self.STAR.probe_liquid_heights(
        containers=[well],
        use_channels=[0],
        lld_mode=self.STAR.LLDMode.PRESSURE,
      )

    entered["plld"].assert_awaited_once()

  async def test_duplicate_channels_serialize_measurements(self):
    """Same physical channel probing two wells in one call: results don't collide."""
    well_a = self.plate.get_item("A1")
    well_b = self.plate.get_item("B1")
    self._put_tips_on_channels([0])

    mocks = self._standard_mocks()
    with contextlib.ExitStack() as stack:
      for v in mocks.values():
        stack.enter_context(v)
      result = await self.STAR.probe_liquid_heights(
        containers=[well_a, well_b],
        use_channels=[0, 0],
      )

    # Two jobs, one result each, keyed by job index not channel.
    self.assertEqual(len(result), 2)
    self.assertAlmostEqual(result[0], 0 - well_a.get_absolute_location("c", "c", "cavity_bottom").z)
    self.assertAlmostEqual(result[1], 0 - well_b.get_absolute_location("c", "c", "cavity_bottom").z)


class TestChatterboxLastLLDHeights(unittest.IsolatedAsyncioTestCase):
  """The chatterbox's last-LLD query reports the heights its simulated sensing produced.

  `STARChatterboxBackend` simulates sensing in `_run_lld_on_channel_batch`, computing absolute
  heights from each container's volume tracker. `request_pip_height_last_lld` must report those
  same heights, under the base method's contract: one absolute height (mm) per channel, indexed
  by physical channel index.
  """

  PROBE_VOLUME = 150.0  # uL

  async def asyncSetUp(self):
    self.backend = STARChatterboxBackend()
    self.deck = STARLetDeck()
    self.lh = LiquidHandler(self.backend, deck=self.deck)

    self.tip_car = TIP_CAR_480_A00(name="tip carrier")
    self.tip_car[1] = self.tip_rack = hamilton_96_tiprack_300uL_filter(name="tip_rack_01")
    self.deck.assign_child_resource(self.tip_car, track=1)

    self.plt_car = PLT_CAR_L5AC_A00(name="plate carrier")
    self.plt_car[0] = self.plate = cor_96_wellplate_360uL_Fb(name="plate_01")
    self.deck.assign_child_resource(self.plt_car, track=9)

    await self.lh.setup()

  async def asyncTearDown(self):
    await self.lh.stop()

  async def test_reports_no_measurement_before_any_lld(self):
    heights = await self.backend.request_pip_height_last_lld()

    self.assertEqual(len(heights), self.backend.num_channels)
    self.assertTrue(all(isinstance(h, float) for h in heights))
    self.assertEqual(heights, [0.0] * self.backend.num_channels)

  async def test_latches_simulated_height_at_physical_channel(self):
    # Channel 3, not 0: `_run_lld_on_channel_batch` keys its result by job index
    # (`batch.indices`), but the query is indexed by physical channel (`batch.channels`).
    # With a single job on channel 0 the two coincide and the distinction goes untested.
    channel = 3
    well = self.plate.get_item("D1")
    await self.lh.pick_up_tips(self.tip_rack["D1"], use_channels=[channel])
    well.tracker.set_volume(self.PROBE_VOLUME)

    await self.backend.probe_liquid_heights(containers=[well], use_channels=[channel])

    expected = well.get_absolute_location(
      "c", "c", "cavity_bottom"
    ).z + well.compute_height_from_volume(self.PROBE_VOLUME)
    heights = await self.backend.request_pip_height_last_lld()

    self.assertEqual(len(heights), self.backend.num_channels)
    self.assertAlmostEqual(heights[channel], expected)
    # Channels that did not probe still report no measurement. Index 0 in particular is where
    # the measurement would land if job indices were mistaken for physical channel indices.
    self.assertEqual(
      [h for i, h in enumerate(heights) if i != channel],
      [0.0] * (self.backend.num_channels - 1),
    )


class TestXArmGeometry(unittest.IsolatedAsyncioTestCase):
  """setup() resolves QM/RU/UA firmware onto the X-drive DriveConfigurations."""

  async def asyncSetUp(self):
    self.lh = LiquidHandler(STARChatterboxBackend(), deck=STARLetDeck())
    with self.assertWarns(DeprecationWarning):
      await self.lh.setup()
    self.addAsyncCleanup(self.lh.stop)

  async def test_single_left_arm(self):
    self.assertIsNotNone(self.lh.backend.extended_conf.left_x_drive.workspace_range)
    self.assertIsNone(self.lh.backend.extended_conf.right_x_drive)

  async def test_left_arm_fields(self):
    left = self.lh.backend.extended_conf.left_x_drive
    self.assertEqual(left.reference_point, "center")  # QM width -> center rail
    # x_range comes from RU, workspace_range from UA: request_extended_configuration routes
    # the two firmware sources into distinct slots (not covered by the parse/branch tests).
    assert left.x_range is not None and left.workspace_range is not None
    self.assertEqual(left.x_range[0], 95.0)
    self.assertEqual(left.workspace_range[0], -323.2)

  def test_model_and_reference_by_width(self):
    dual = DriveConfiguration(width=370.0)
    self.assertEqual(dual.model, "hamilton_legacy_star_dual_rail_arm")
    self.assertEqual(dual.reference_point, "center")
    # 246.0 is an illustrative <300 width; no single-rail dump exists yet to measure one.
    single = DriveConfiguration(width=246.0)
    self.assertEqual(single.model, "hamilton_legacy_star_single_right_rail_arm")
    self.assertEqual(single.reference_point, "right")


class TestXArmReachByDeck(unittest.IsolatedAsyncioTestCase):
  """A STAR reaches farther in X than a STARLet: an X past the STARLet's right edge
  is reachable on a STAR but rejected on a STARLet."""

  async def test_x_past_starlet_edge_reachable_on_star_only(self):
    star = LiquidHandler(STARChatterboxBackend(), deck=STARDeck())
    with self.assertWarns(DeprecationWarning):
      await star.setup()
    self.addAsyncCleanup(star.stop)
    starlet = LiquidHandler(STARChatterboxBackend(), deck=STARLetDeck())
    with self.assertWarns(DeprecationWarning):
      await starlet.setup()
    self.addAsyncCleanup(starlet.stop)

    star_range = star.backend.extended_conf.left_x_drive.x_range
    starlet_range = starlet.backend.extended_conf.left_x_drive.x_range
    assert star_range is not None and starlet_range is not None
    self.assertGreater(star_range[1], starlet_range[1])

    x = (starlet_range[1] + star_range[1]) / 2  # past the STARLet edge, within STAR reach
    star.backend._check_x_arm_reachable(x)  # reachable on STAR: no raise
    with self.assertRaises(ValueError):
      starlet.backend._check_x_arm_reachable(x)


class TestXArmRangeQueries(unittest.IsolatedAsyncioTestCase):
  """RU/UA parse the firmware replies observed on real machines."""

  def setUp(self):
    self.star = STARBackend()
    self.star.send_command = unittest.mock.AsyncMock()

  async def test_maximal_ranges_of_x_drives(self):
    self.star.send_command.return_value = "C0RUid0002er00/00ru00950 13402 30000 30000"
    ranges = await self.star.request_maximal_ranges_of_x_drives()
    self.assertEqual(ranges, {"left": (95.0, 1340.2), "right": (3000.0, 3000.0)})

  async def test_working_envelopes_per_arm(self):
    self.star.send_command.return_value = "C0UAid0001er00/00ua5952 0000 -03232 +15172 +30000 +30000"
    wraps = await self.star.request_working_envelopes_per_arm()
    self.assertEqual(wraps, {"left": (595.2, (-323.2, 1517.2)), "right": (0.0, (3000.0, 3000.0))})


class TestXArmRangeEnforcement(unittest.IsolatedAsyncioTestCase):
  """move_channel_x / experimental_x_arm_move reject targets outside the arm's x_range."""

  def setUp(self):
    self.star = STARBackend()
    # setup() normally resolves this from firmware; seed the left X-drive geometry so the
    # reachability checks have a range to enforce against, without needing a deck-mounted
    # arm or tracker.
    self.star._extended_conf = replace(
      _DEFAULT_EXTENDED_CONFIGURATION,
      left_x_drive=replace(
        _DEFAULT_EXTENDED_CONFIGURATION.left_x_drive,
        width=370.0,
        x_range=(95.0, 1337.5),
        workspace_range=(-323.2, 1337.5),
      ),
    )
    self.star.send_command = unittest.mock.AsyncMock(return_value={})

  async def test_experimental_x_arm_move_rejects_out_of_range(self):
    # x_range is (95.0, 1337.5): both ends reject, and no wire command is sent.
    for x in (94.0, 1400.0):
      with self.assertRaises(ValueError):
        await self.star.experimental_x_arm_move(x)
    self.star.send_command.assert_not_awaited()

  async def test_experimental_x_arm_move_in_range_sends_command(self):
    # A target inside x_range passes the guard and reaches the wire unchanged.
    await self.star.experimental_x_arm_move(500.0)
    self.star.send_command.assert_awaited_once_with(
      module="X0", command="XP", la="05000", lr="3", lw="7"
    )

  async def test_move_channel_x_rejects_out_of_range(self):
    with self.assertRaises(ValueError):
      await self.star.move_channel_x(0, 1400.0)
    self.star.send_command.assert_not_awaited()

  async def test_rejects_target_on_absent_right_arm(self):
    # The default single-arm STAR has no right X-arm, so a right-arm target is rejected.
    with self.assertRaises(ValueError):
      self.star._check_x_arm_reachable(400.0, "right")
