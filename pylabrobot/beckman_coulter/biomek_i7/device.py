"""The Biomek i7 as one resource: its frame, its deck, and its two bridges with their pods.

The frame's proportions, in centimetres:

- `BaseXSpan` 188.0 on an i7 (130.0 on the single-pod frame), 91.025 deep, from 17 left of Biomek's
  X origin and 11 behind its Y origin.
- `BaseHeight` 46.355: the cabinet under the deck plate.
- Three 2.667 plates stacked under the deck surface.
- Corner posts 8 x 8 standing `BaseHeight` + 20 - 8 = 58.355 above the plates, carrying the rails the
  bridges ride on, 8 x 8 in section.

The frame here is the device resource's own: origin at the front left bottom corner of the cabinet, millimetres, Y towards the back.
"""

import logging
import os
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from pylabrobot.beckman_coulter.biomek_i7 import targeting, units
from pylabrobot.beckman_coulter.biomek_i7.deck import (
  FRAME_X,
  FRAME_Y,
  BiomekI7Deck,
  deck_x,
  deck_y,
)
from pylabrobot.beckman_coulter.biomek_i7.defaults import GENERIC_CONFIGURATION, GENERIC_RECORDING
from pylabrobot.beckman_coulter.biomek_i7.driver import pod_geometry as g
from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import (
  DeviceConfiguration,
  PodConfiguration,
)
from pylabrobot.beckman_coulter.biomek_i7.driver.features.chassis import Chassis
from pylabrobot.beckman_coulter.biomek_i7.driver.features.door import Door
from pylabrobot.beckman_coulter.biomek_i7.driver.features.gripper import Gripper
from pylabrobot.beckman_coulter.biomek_i7.driver.features.head96 import (
  DEFAULT_TRASH as HEAD96_TRASH,
)
from pylabrobot.beckman_coulter.biomek_i7.driver.features.head96 import Head96
from pylabrobot.beckman_coulter.biomek_i7.driver.features.pipettes import (
  DEFAULT_TRASH as SPAN8_TRASH,
)
from pylabrobot.beckman_coulter.biomek_i7.driver.features.pipettes import Pipettes
from pylabrobot.beckman_coulter.biomek_i7.driver.features.pod import Pod
from pylabrobot.beckman_coulter.biomek_i7.driver.master import BiomekI7Driver
from pylabrobot.beckman_coulter.biomek_i7.driver.planning.obstacles import Obstacles, obstacle_part
from pylabrobot.beckman_coulter.biomek_i7.driver.planning.pod_model import OPEN, Part
from pylabrobot.beckman_coulter.biomek_i7.driver.simulator import (
  RECORDING_ENV,
  sense_recorded_tips,
  simulate_grips,
  simulated_boards,
)
from pylabrobot.beckman_coulter.biomek_i7.resource_model import cm, pod_model
from pylabrobot.beckman_coulter.biomek_i7.resource_model.alps import BiomekALP, BiomekPosition
from pylabrobot.beckman_coulter.biomek_i7.resource_model.heads import PROBES
from pylabrobot.beckman_coulter.biomek_i7.resource_model.pods import (
  GripperModel,
  gripper_model,
  place_head96,
  place_span8_channel,
  span8_channels,
)
from pylabrobot.beckman_coulter.biomek_i7.resource_model.structure import (
  BiomekObstacle,
  FrameToDevice,
  merge_obstacles,
  structure_resources,
)
from pylabrobot.resources.beckman_coulter.tips import beckman_coulter_biomek_tip_1070uL
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.rotation import Rotation
from pylabrobot.utils.linalg import matrix_vector_multiply_3x3

logger = logging.getLogger(__name__)

BASE_X_SPAN = cm(188.0)  # BaseXSpan default on an i7
BASE_Y_SPAN = cm(91.025)
BASE_LEFT_OF_X0 = cm(17.0)  # the base starts 17 cm left of Biomek's X origin
BASE_BEHIND_Y0 = cm(11.0)  # and 11 cm behind its Y origin
CABINET_HEIGHT = cm(46.355)  # BaseHeight
PLATE_THICKNESS = cm(2.667)
PLATES = 3
POST_SECTION = cm(8.0)
POST_HEIGHT = cm(46.355 + 20.0 - 8.0)
RAIL_SECTION = cm(8.0)
# How near the grip's GR the gripper is taken to be at it, degrees: GR is read back from counts.
GRIP_ANGLE_TOLERANCE = 0.5

DECK_Z = CABINET_HEIGHT + PLATES * PLATE_THICKNESS
# The height the bridges ride at. The posts and rails are not drawn, so the device's own extent ends
# at the deck surface and the bridges and pods stand above it.
RAIL_TOP_Z = DECK_Z + POST_HEIGHT + RAIL_SECTION
SIZE_Z = DECK_Z

# Where the deck's frame sits in the device's. Biomek X = deck x - FRAME_X, and the base begins
# BASE_LEFT_OF_X0 left of Biomek X 0; Biomek Y = FRAME_Y - deck y, and the base's back edge is
# BASE_BEHIND_Y0 behind Biomek Y 0.
DECK_X = BASE_LEFT_OF_X0 - FRAME_X
DECK_Y = BASE_Y_SPAN - BASE_BEHIND_Y0 - FRAME_Y

# What the instrument's structure is drawn within, Biomek's frame (cm): its base, from just below
# the deck surface to above the pods' tops (`structure_resources`).
STRUCTURE_EXTENT = (
  -BASE_LEFT_OF_X0 / 10,
  (BASE_X_SPAN - BASE_LEFT_OF_X0) / 10,
  -BASE_BEHIND_Y0 / 10,
  (BASE_Y_SPAN - BASE_BEHIND_Y0) / 10,
  -2.0,
  60.0,
)


class BiomekI7Device(Resource):
  """A Biomek i7: its frame, a deck, and a bridge per pod, driven through `driver` (STAR's
  `STARDevice`; `BiomekI7(...)` builds one, as `STAR(...)` does).

  Each pod's model (`pod_models[side]`) rides its bridge (`bridges[side]`), which is the pod's
  `resource` once the driver has found it (STAR's `x_arm.resource`). The models are built from the
  instrument's declared geometry when the device is made, and each bridge follows its pod's X as
  the driver moves it, placed as Biomek's collision model places it, so the resource tree takes up
  the X the rail guard counts. The rest follows the pod's other axes (`_show_pod`): the
  Multichannel's carriage its Y and its head (an `NChannelPipette`) its Z, each Span-8 probe's
  channel its own Y and Z, and the gripper (a `MechanicalGripper`) GY, GZ, GR and GG. The heads
  are the features' resources, so the tips they carry hang from their shafts, as the STAR's do.
  Each bridge marks the point its pod's X names (`resource_model.pods.pod_model`).
  `update_bridge_location`, `update_pod_location` and `update_probe_location` place the models by
  hand; nothing moves.
  """

  def __init__(
    self,
    driver: BiomekI7Driver,
    deck: Optional[BiomekI7Deck] = None,
    name: str = "biomek_i7",
    state_json: Optional[str] = None,
  ):
    """
    Args:
      driver: what talks to the instrument, or a simulated one (`BiomekI7(simulation=True)`).
      deck: the deck. Defaults to the one the declared configuration lays out (`deck_layout`,
        `BiomekI7Deck.from_configuration`), else an empty one (place ALPs with `deck.assign_alp`).
      name: the resource's name.
      state_json: where to keep this resource tree's state between sessions
        (`save_state_to_file`): the tips on each head's shafts, which the 96-head cannot sense.
        Loaded here when it exists, saved whenever a head's tips change.
    """
    self.driver: BiomekI7Driver = driver
    super().__init__(
      name=name,
      size_x=BASE_X_SPAN,
      size_y=BASE_Y_SPAN,
      size_z=SIZE_Z,
      category="device",
      model="beckman_coulter_biomek_i7",
    )
    # Each pod's obstacles (`obstacles`), by pod number, until the tree changes.
    self._obstacles: Dict[int, Obstacles] = {}
    if deck is None:
      # The default deck the declared configuration lays out, else an empty one.
      deck = BiomekI7Deck.from_configuration(driver.declared)
    self.deck = deck
    driver.deck = self.deck  # the operations work on this deck (STAR's `driver.deck`)
    self.assign_child_resource(self.deck, location=Coordinate(DECK_X, DECK_Y, DECK_Z))
    # The instrument's structure around the deck, from the declared configuration's obstacles.
    self.structure: List[BiomekObstacle] = []
    to_device = FrameToDevice(Coordinate(DECK_X, DECK_Y, DECK_Z), FRAME_X, FRAME_Y)
    obstacles = merge_obstacles(p.obstacles for p in driver.declared.pods.values())
    for part, location in structure_resources(obstacles, STRUCTURE_EXTENT, to_device):
      self.assign_child_resource(part, location=location)
      self.structure.append(part)

    self.bridges: Dict[str, Resource] = {}
    self.pod_models: Dict[str, Resource] = {}
    # Each pod's gripper (from its declared model), posed from its axes; what it holds hangs from it.
    self.gripper_models: Dict[str, GripperModel] = {}
    self._unfollow: List[Callable[[], None]] = []
    self._build_declared_models(driver.declared)
    self.state_json = state_json
    if state_json is not None and os.path.exists(state_json):
      self.load_state_from_file(state_json)  # the tips on the declared heads' shafts
    driver.on_tips_changed = self._tips_changed
    driver.obstacles = self.obstacles
    # Any resource assigned or unassigned under the device (labware, lids, tips, what a gripper
    # holds; the callbacks reach up the tree) changes what the pods must clear.
    self.register_did_assign_resource_callback(self.invalidate_obstacles)
    self.register_did_unassign_resource_callback(self.invalidate_obstacles)
    if driver.arms:
      self._follow_driver()  # given a driver already set up

  # -- what the instrument has (read through to the driver) ------------------------
  # `left_x_arm` / `right_x_arm` are the driver's pods, named as STAR names its X-arms;
  # `pod_models` and `bridges` are their models in the resource tree.

  @property
  def left_x_arm(self) -> Optional[Pod]:
    """The left pod, on an instrument that has one (known after `setup`)."""
    return self.driver.left_x_arm

  @property
  def right_x_arm(self) -> Optional[Pod]:
    """The right pod, on an instrument that has one (known after `setup`)."""
    return self.driver.right_x_arm

  @property
  def arms(self) -> List[Pod]:
    """The pods, left first."""
    return self.driver.arms

  @property
  def x_arm(self) -> Pod:
    """The pod, on an instrument with one (`BiomekI7Driver.x_arm`, which raises otherwise)."""
    return self.driver.x_arm

  @property
  def chassis(self) -> Optional[Chassis]:
    """The chassis: the interlock and the chassis's inputs (known after `setup`)."""
    return self.driver.chassis

  @property
  def front_cover(self) -> Optional[Door]:
    """The door, by STAR's name for it (known after `setup`)."""
    return self.driver.front_cover

  @property
  def head96(self) -> Optional[Head96]:
    """The 96-channel head, on an instrument where one pod has it."""
    return self.driver.head96

  @property
  def pipettes(self) -> Optional[Pipettes]:
    """The Span-8 channels, on an instrument where one pod has them."""
    return self.driver.pipettes

  @property
  def gripper(self) -> Optional[Gripper]:
    """The gripper, on an instrument with one pod; with two, reach it as `left_x_arm.gripper` or
    `right_x_arm.gripper`."""
    return self.driver.gripper

  async def setup(
    self,
    skip_device_initialization: bool = False,
    skip_pipettes: bool = False,
    *,
    skip_head96: bool = False,
    skip_left_x_arm: bool = False,
    skip_right_x_arm: bool = False,
    force_initialize: bool = False,
    discard_tips: bool = True,
    precautionary_discard: bool = True,
    park: bool = True,
  ) -> None:
    """Bring the instrument up. This homes the arms, then, as the STAR does, takes off any tips
    left on (`discard_tips`), and parks the pods, as Biomek's Home All does (`park`).

    Args:
      skip_device_initialization: as `BiomekI7Driver.setup` takes it.
      skip_pipettes: as `BiomekI7Driver.setup` takes it.
      skip_head96: as `BiomekI7Driver.setup` takes it.
      skip_left_x_arm: as `BiomekI7Driver.setup` takes it.
      skip_right_x_arm: as `BiomekI7Driver.setup` takes it.
      force_initialize: as `BiomekI7Driver.setup` takes it.
      discard_tips: shuck tips found on a pod that was brought up into its trash: on a Span-8 the
        probes the tip sensor finds a tip on, on the 96-head the tips it records (it has no tip
        sensor). Needs the trash position on the deck.
      precautionary_discard: with `discard_tips`, eject into the 96-head's trash even when no
        tips are recorded, as if the longest tip were on (`head96.discard_any_tips`),
        as the STAR's 96-head initialization ejects into its waste: the head cannot sense tips,
        and a lost state would leave them on unseen. False skips it, for an operator who has
        checked the head is empty. A path to the trash that is not clear for the longest tip
        refuses the move: clear it, or pass False.
      park: park every pod that was brought up (`park`).
    """
    await self.driver.setup(
      skip_device_initialization=skip_device_initialization,
      skip_pipettes=skip_pipettes,
      skip_head96=skip_head96,
      skip_left_x_arm=skip_left_x_arm,
      skip_right_x_arm=skip_right_x_arm,
      force_initialize=force_initialize,
    )
    self._follow_driver()
    skipped = self.driver.skipped_sides(
      skip_pipettes=skip_pipettes, skip_head96=skip_head96,
      skip_left_x_arm=skip_left_x_arm, skip_right_x_arm=skip_right_x_arm,
    )  # fmt: skip
    if discard_tips:
      await self._discard_found_tips(skipped, precautionary_discard)
    if park:
      await self.park(skip=skipped)

  async def _discard_found_tips(self, skipped: Set[str], precautionary: bool = True) -> None:
    """Shuck the tips found on the pods not `skipped` (`setup`'s `discard_tips`). A Span-8 probe
    the sensor finds a tip on that the session did not record is taken to carry the longest
    Biomek tip, so its travel to the trash clears the deck; a recorded tip the sensor does not
    find is forgotten. The 96-head, which has no sensor, discards whatever it may carry when
    `precautionary`, else only the tips recorded."""
    names = set(self.deck.position_names)
    for pod in self.arms:
      if pod.side in skipped:
        continue
      if pod.pipettes is not None and SPAN8_TRASH in names:
        pipettes = pod.pipettes
        states = await pipettes.sense_tip_presence()
        found = [n for n, state in enumerate(states, start=1) if state == "loaded"]
        gone = [n for n, t in enumerate(pipettes.tips, start=1) if t is not None and n not in found]
        if gone:
          pipettes._set_tips({n: None for n in gone})
        if found:
          assumed = beckman_coulter_biomek_tip_1070uL("assumed tip")
          pipettes._set_tips({n: pipettes._carried_tip(n) or assumed for n in found})
          logger.warning("%s: tips found on probes %s; discarding them", pod, found)
          await pipettes.discard_tips(use_channels=[n - 1 for n in found])
      if pod.head96 is not None and HEAD96_TRASH in names:
        if any(pod.head96.tips):
          logger.warning("%s: the 96-head carries tips, as recorded; discarding them", pod)
          await pod.head96.discard_tips()
        elif precautionary:
          await pod.head96.discard_any_tips()

  async def park(self, skip: Iterable[str] = ()) -> None:
    """Park the pods as Biomek does (`Pod.park`; a Span-8 over its wash station,
    `pipettes.park`), the left pod first, as each pod's park is at its own end of the rail. `skip`:
    sides to leave as they are."""
    for pod in sorted(self.driver.arms, key=lambda p: p.side != "left"):
      if pod.side in skip:
        continue
      if pod.pipettes is not None:
        await pod.pipettes.park()
      else:
        await pod.park()

  async def pause(self) -> None:
    """Pause for access, as Biomek's Pause step: the pods' drive power is cut and PAUSED shown,
    so the light curtain may be broken. Move the pods out of the way first if needed
    (`park`, or a move of the pod). See `BiomekI7Driver.pause`."""
    await self.driver.pause()

  async def resume(self, speed: int = 200) -> None:
    """Resume after `pause`: re-arm (the light curtain must be clear), enable, and move the pods
    back to where they were. See `BiomekI7Driver.resume`."""
    await self.driver.resume(speed)

  async def home_all(self, force: bool = True) -> None:
    """Biomek's Home All: bring the instrument up again, by default homing every axis as Biomek
    does. `force=False` homes only what is not homed."""
    await self.driver.initialize(force_initialize=force)

  async def stop(self) -> None:
    """Release the instrument. Moves nothing."""
    for unfollow in self._unfollow:
      unfollow()
    self._unfollow = []
    await self.driver.stop()

  # -- the pods' models ------------------------------------------------------------

  def _build_pod_model(
    self, side: str, module_type: str, declared: Optional[PodConfiguration] = None
  ) -> None:
    """(Re)build a side's bridge and pod, keeping the bridge where it was."""
    old = self.bridges.pop(side, None)
    geometry = declared.geometry if declared is not None else None
    settings = declared.settings if declared is not None else None
    rail, pod = pod_model(side, module_type, BASE_Y_SPAN, geometry, settings)
    if old is not None and old.location is not None:
      x = old.location.x
      self.unassign_child_resource(old)
    elif side == "left":
      x = POST_SECTION
    else:
      x = BASE_X_SPAN - POST_SECTION - rail.get_size_x()
    # The bridges ride on the rails' top faces.
    self.assign_child_resource(rail, location=Coordinate(x, 0, RAIL_TOP_Z))
    self.bridges[side], self.pod_models[side] = rail, pod

  def _follow_driver(self) -> None:
    """Rebuild each pod's model from the driver's pod (its module type and declared geometry) and
    follow its X from now on."""
    for unfollow in self._unfollow:
      unfollow()
    self._unfollow = []
    # The heads' tips, carried over to the rebuilt models (the state the tree saves).
    saved: Dict[str, Dict[str, Any]] = {}
    for bridge in self.bridges.values():
      saved.update(bridge.serialize_all_state())
    for pod in self.driver.arms:
      self._build_pod_model(pod.side, pod.module_type, pod.declared)
      self._build_gripper_model(pod.side, pod.declared)
      if pod.declared is not None:
        self._show_parked(pod.side, pod.declared)
      pod.resource = self.bridges[pod.side]
      self.bridges[pod.side].load_all_state(saved)
      self._give_heads(pod)
      if pod.declared is not None:
        self._unfollow.append(pod.add_position_listener(self._show_pod))
        self._show_pod(pod)

  def _give_heads(self, pod: Pod) -> None:
    """Hand a pod's head or probes to its feature, as the STAR's driver hands each feature its
    resources; the feature takes the tips their shafts carry as its own."""
    model = self.pod_models[pod.side]
    if pod.head96 is not None:
      pod.head96.resource = model.get_resource(f"{model.name}_head")  # type: ignore[assignment]
      pod.head96._adopt_tips()
    if pod.pipettes is not None:
      pod.pipettes.resources = span8_channels(self.bridges[pod.side])
      pod.pipettes._adopt_tips()

  def _tips_changed(self) -> None:
    """A head's tips changed: the obstacles built are dropped, and the tree's state saved when
    there is a `state_json`."""
    self.invalidate_obstacles()
    if self.state_json is not None:
      self.save_state_to_file(self.state_json, indent=1)

  def _build_declared_models(self, configuration: DeviceConfiguration) -> None:
    """Build each declared pod's models, before the instrument is read: the pods the driver will
    find, where the declared configuration describes them (discovery refuses another pod type)."""
    for side, declared in configuration.pods.items():
      self._build_pod_model(side, declared.module_type, declared)
      self._build_gripper_model(side, declared)
      self._show_parked(side, declared)

  def _show_parked(self, side: str, declared: PodConfiguration) -> None:
    """Pose a pod's model as Biomek parks it, before the driver says where it is: its channels at
    the top of their travel less `ZMargin`, a Multichannel's carriage at the park's Y (else the
    back of its travel), a Span-8's probes at the back at their narrowest span."""
    limits, park, geo = declared.limits, declared.park, declared.geometry
    margin = park.get("ZMargin", 0.0)
    if declared.module_type == "multichannel":
      y = park.get("Y", limits.get("Y", (0.0, 0.0))[0])
      z = limits["Z"][1] + margin if "Z" in limits else None
      front = y - geo.mandrel1_y_offset + geo.pod_y_span
      self.update_pod_location(side, deck_y(cm(front)), z=None if z is None else cm(z))
      return
    y1 = limits.get("Y", (0.0, 0.0))[0]
    spacing = self._probe_spacing(declared)
    for probe in range(1, PROBES + 1):
      zs = limits.get(f"Z{probe}", limits.get("Z"))
      if zs is None:
        continue
      y = y1 + (probe - 1) * spacing
      self.update_probe_location(side, probe, deck_y(cm(y)), cm(zs[1] + margin))
    front = y1 - geo.mandrel1_y_offset + geo.pod_y_span
    self.update_pod_location(side, deck_y(cm(front)))

  @staticmethod
  def _probe_spacing(declared: PodConfiguration) -> float:
    """The Span-8's probes' spacing at their narrowest (cm; `PodConfiguration.probe_spacing`)."""
    return declared.probe_spacing()

  def _build_gripper_model(self, side: str, declared: Optional[PodConfiguration]) -> None:
    """(Re)build a side's gripper on its bridge, posed as parked until the driver says otherwise.
    What the old one held is let go, to be hung on the new one when the pod is next shown."""
    old = self.gripper_models.pop(side, None)
    if old is not None:
      parts = {old.gripper.body.name, *(f.name for f in old.gripper.fingers)}
      for child in list(old.gripper.children):
        if child.name not in parts:
          child.unassign()
      for part in (old.upper_hand, old.gripper):
        if part.parent is not None:
          part.unassign()
    if declared is None or not declared.gripper_model:
      return
    gg_range = declared.limits.get("GG", (0.0, 20.0))
    model = gripper_model(f"{side}_gripper", declared.gripper_model, gg_range)
    bridge = self.bridges[side]
    for part in (model.upper_hand, model.gripper):
      bridge.assign_child_resource(part, location=Coordinate.zero())
    self.gripper_models[side] = model
    park, limits = declared.park, declared.limits
    gz_top = limits["GZ"][1] + park.get("GZMargin", 0.0) if "GZ" in limits else 0.0
    model.pose(
      self._gripper_centre(side, declared, park.get("GY", 0.0), gz_top),
      park.get("GR", 90.0),
      park.get("GG", gg_range[0]),
    )

  def _gripper_centre(
    self, side: str, declared: PodConfiguration, gy: float, gz: float
  ) -> Coordinate:
    """Where the gripper's rotation centre is in its bridge's frame (mm), at GY `gy` and GZ `gz`
    (cm), as `pod_geometry` places it: X + `GripperXOffset` - `PadsCenterToRotationCenter`, which
    is a fixed distance from the bridge's left edge (`bridge_x_range`), GY + `GripperYOffset` -
    `FingerCenterOffset`, and the hand's bottom the fingers' depth above GZ + `GripperZOffset`."""
    geo, finger = declared.geometry, declared.gripper_model["Finger"]
    model, bridge = self.gripper_models[side], self.bridges[side]
    at = bridge.location if bridge.location is not None else Coordinate.zero()
    x = geo.gripper_x_offset - finger["PadsCenterToRotationCenter"]
    x += geo.bridge_to_head_x_offset + geo.mandrel1_x_offset
    cy = gy + geo.gripper_y_offset - finger["FingerCenterOffset"]
    cz = gz + geo.gripper_z_offset + model.depth
    return Coordinate(cm(x), DECK_Y + deck_y(cm(cy)) - at.y, DECK_Z + cm(cz) - at.z)

  @staticmethod
  def _axis_counts(pod: Pod, axis: str) -> Optional[int]:
    """An axis's position, counts, as the Master Controller keeps it adjusted for the axis's
    hysteresis (`motion.update_cpos`): the position its targets name, so the frame the operations
    are placed in (`targeting.py`), and known whenever the axis is (the raw position is not after
    a home or a force-limited move). One rule for every part shown, so the bridge, the pod, its
    head or probes and the gripper never disagree."""
    if axis not in pod.cpositions:
      return None
    return pod.cpositions[axis][1]

  @classmethod
  def _axis_value(cls, pod: Pod, axis: str) -> Optional[float]:
    """An axis's position in its unit (`_axis_counts`)."""
    d, counts = pod.declared, cls._axis_counts(pod, axis)
    return None if d is None or counts is None else d.to_value(axis, counts)

  def _show_gripper(self, pod: Pod) -> None:
    """Pose a pod's gripper model from its axes (the rotation centre: `pod_geometry`), and hang
    what it holds from it, its grip point at the pads."""
    d, model = pod.declared, self.gripper_models.get(pod.side)
    if d is None or model is None:
      return
    v = {a: self._axis_value(pod, a) for a in ("GY", "GZ", "GR", "GG")}
    if any(value is None for value in v.values()):
      return
    gy, gz, gr, gg = (float(v[a]) for a in ("GY", "GZ", "GR", "GG"))  # type: ignore[arg-type]
    gripper, plan = pod.gripper, None
    held = gripper.held_resource if gripper is not None else None
    if gripper is not None:
      plan = gripper.held_plan
    # At the grip's angle, to the counts' resolution, the gripper is at that angle: what it holds
    # then stands as it did on the deck, as placing it reckons from.
    if plan is not None and abs((gr - plan.gr + 180) % 360 - 180) < GRIP_ANGLE_TOLERANCE:
      gr = plan.gr
    model.pose(self._gripper_centre(pod.side, d, gy, gz), gr, gg)
    if held is None or plan is None or held.parent is model.gripper:
      return
    # Its rotation in the gripper's frame: on the deck it is `plan.rotation`, with the gripper
    # turned -GR at the grip; it turns with the gripper from there.
    turn = (plan.rotation + plan.gr) % 360
    held.rotation = Rotation(z=turn)
    point = held.preferred_pickup_location
    xy = (
      Coordinate(held.get_size_x() / 2, held.get_size_y() / 2, 0)
      if point is None
      else Coordinate(point.x, point.y, 0)
    ).rotated(Rotation(z=turn))
    pads = model.pads()
    location = Coordinate(pads.x - xy.x, pads.y - xy.y, pads.z - plan.held.grip_offset * 10)
    model.gripper.assign_child_resource(held, location=location)

  def _show_pod(self, pod: Pod) -> None:
    """Put a pod's model where the pod's axes are (an unknown one: where it was).

    - X: the bridge's left edge at `bridge_x_range` in Biomek's X.
    - Y and Z, on a Multichannel: Biomek's Y and Z name mandrel A1 (Y over a plate's well A1, Z the
      bare mandrel's bottom above the deck's surface). The carriage's back edge is `Mandrel1YOffset` behind it and follows Y; the head
      follows Z in it, its flange bottom taken as the mandrels' bottom.
    - On a Span-8, each probe's Y and Z (`Y1`..`Y8`, `Z1`..`Z8`), as the probes are targeted: a
      probe's Y counts from its narrowest place, `S`'s zero span shared out between the eight
      (`_probe_spacing`), and its Z is the bare probe's end. The carriage follows probe 1.
    - The gripper: `_show_gripper`.
    """
    d = pod.declared
    if d is None:
      return
    x = self._axis_value(pod, "X")
    if x is not None:
      low, _ = g.bridge_x_range(d.geometry, x)
      self.update_bridge_location(pod.side, deck_x(cm(low)))
    self._show_gripper(pod)
    geo = d.geometry
    if pod.module_type == "multichannel":
      y, z = self._axis_value(pod, "Y"), self._axis_value(pod, "Z")
      if y is None:
        return
      front = y - geo.mandrel1_y_offset + geo.pod_y_span
      self.update_pod_location(pod.side, deck_y(cm(front)), z=None if z is None else cm(z))
      return
    for probe in range(1, PROBES + 1):
      yc, zc = self._axis_counts(pod, f"Y{probe}"), self._axis_counts(pod, f"Z{probe}")
      if yc is None or zc is None:
        continue
      y = d.probe_y(probe, yc)
      self.update_probe_location(pod.side, probe, deck_y(cm(y)), cm(d.to_value(f"Z{probe}", zc)))
      if probe == 1:
        self.update_pod_location(pod.side, deck_y(cm(y - geo.mandrel1_y_offset + geo.pod_y_span)))

  def obstacles(self, pod_number: int) -> Obstacles:
    """What pod `pod_number`'s moves must clear, in Biomek's frame (cm), from this resource tree
    alone, as Biomek builds it:

    - the instrument's structure as Biomek bounds it (`structure`), each widened by its buffer;
    - one box per deck position (`BiomekPosition`): its footprint, where this pod's calibration puts
      it, from its ALP's base up to the higher of its `min_safe_height` and the top of what stands
      on it (lids and standing tips included);
    - each position's own obstacles (a trash's walls), from below the deck up.

    What a gripper holds is not on the deck, so it is not here. Built once per pod and kept until
    the tree changes (a resource assigned or unassigned anywhere under the device, tips included);
    a resource moved by setting its `location` changes nothing the device hears of: call
    `invalidate_obstacles` after such a change.

    Raises:
      ValueError: for a position turned other than by a multiple of 90 degrees.
    """
    found = self._obstacles.get(pod_number)
    if found is None:
      found = self._obstacles[pod_number] = self._build_obstacles(pod_number)
    return found

  def invalidate_obstacles(self, *_: Any) -> None:
    """Forget the obstacles built for each pod: the next `obstacles` builds them again."""
    self._obstacles.clear()

  def _build_obstacles(self, pod_number: int) -> Obstacles:
    """`obstacles`, built from the tree."""
    structure = tuple(
      obstacle_part(o.name, (o.x1, o.x2), (o.y1, o.y2), (o.z1, o.z2), o.buffer)
      for o in (part.obstacle() for part in self.structure)
    )
    deck_origin = self.deck.get_absolute_location()
    deck: List[Part] = []
    positions = [
      h for alp in self.deck.children for h in alp.children if isinstance(h, BiomekPosition)
    ]
    for position in positions:
      rotation = position.get_absolute_rotation()
      turn = rotation.z % 90
      if min(turn, 90 - turn) > 1e-6:
        raise ValueError(f"{position.name} is turned {rotation.z} degrees")
      frame = (position.get_absolute_location() - deck_origin, rotation.get_rotation_matrix())
      # Where this pod's own calibration puts the position: the frame its parts are modelled in.
      shift = targeting.position_correction(
        self.deck, self.driver.declared, position, pod_number, frame[0]
      )
      floor = frame[0].z
      base = floor - (position.location.z if position.location is not None else 0.0)  # its ALP's
      top = max(
        [floor + position.min_safe_height]
        + [c.get_highest_known_point() - deck_origin.z for c in position.children]
      )
      deck.append(
        _world_box(
          position.name,
          frame,
          (0, position.get_size_x()),
          (0, position.get_size_y()),
          (base, top),
          shift,
        )
      )
      for o in position.obstacles:
        deck.append(
          _world_box(
            f"{position.name} {o.name}",
            frame,
            (o.x0, o.x1),
            (o.y0, o.y1),
            (-OPEN * 10, floor + o.top),
            shift,
          )
        )
    # Labware on no position (straight on the deck or on an ALP) is boxed by its own bounds, with
    # no calibration to correct it by; an operation working in it leaves it out as a position's.
    loose = [r for r in self.deck.children if not isinstance(r, BiomekALP)] + [
      h for alp in self.deck.children if isinstance(alp, BiomekALP)
      for h in alp.children if not isinstance(h, BiomekPosition)
    ]  # fmt: skip
    for resource in loose:
      rotation = resource.get_absolute_rotation()
      frame = (resource.get_absolute_location() - deck_origin, rotation.get_rotation_matrix())
      deck.append(
        _world_box(
          resource.name,
          frame,
          (0, resource.get_size_x()),
          (0, resource.get_size_y()),
          (frame[0].z, resource.get_highest_known_point() - deck_origin.z),
          (0.0, 0.0, 0.0),
        )
      )
    holders = tuple(p.name for p in positions) + tuple(r.name for r in loose)
    return Obstacles(structure, tuple(deck), holders)

  def update_bridge_location(self, side: str, x: float) -> None:
    """Put a side's bridge's left edge at deck x `x` mm, carrying its pod along the rails."""
    rail = self.bridges[side]
    loc = rail.location
    if loc is None:
      raise ValueError(f"{rail.name} is not placed on {self.name}")
    rail.location = Coordinate(DECK_X + x, loc.y, loc.z)

  def update_pod_location(self, side: str, y: float, z: Optional[float] = None) -> None:
    """Put a side's pod's front edge at deck y `y` mm along its bridge, and, on a Multichannel, its
    head's bottom (the mandrels' ends) at deck z `z` mm; the carriage keeps its height.

    Raises:
      ValueError: for a `z` on a pod with no 96-channel head: a Span-8's probes move one by one
        (`update_probe_location`).
    """
    pod = self.pod_models[side]
    loc = pod.location
    if loc is None or pod.parent is None:
      raise ValueError(f"{pod.name} is not on a bridge")
    if z is not None and not any(c.name == f"{pod.name}_head" for c in pod.children):
      raise ValueError(
        f"{pod.name} has no 96-channel head; place its probes with update_probe_location"
      )
    pod.location = Coordinate(loc.x, DECK_Y + y, loc.z)
    if z is not None:
      place_head96(pod, DECK_Z + z - pod.get_location_wrt(self).z)

  def update_probe_location(self, side: str, probe: int, y: float, z: float) -> None:
    """Put a Span-8's probe `probe` (1..8) end at deck y `y` and deck z `z` (mm), on its bridge's
    line: the X the pod names."""
    rail = self.bridges[side]
    channels = span8_channels(rail)
    if not 1 <= probe <= len(channels):
      raise ValueError(f"{rail.name} has no probe {probe}")
    at = rail.location if rail.location is not None else Coordinate.zero()
    ref = getattr(rail, "reference_point", None)
    x = ref.x if ref is not None else channels[probe - 1].reference_point.x  # type: ignore[attr-defined]
    place_span8_channel(channels[probe - 1], x, DECK_Y + y - at.y, DECK_Z + z - at.z)


def BiomekI7(
  deck: Optional[BiomekI7Deck] = None,
  simulation: bool = False,
  declared_configuration_json: Optional[str] = None,
  driver: Optional[BiomekI7Driver] = None,
  name: str = "biomek_i7",
  simulator_recording: Optional[str] = None,
  state_json: Optional[str] = None,
  simulate_motion_time: bool = False,
  motion_time_scale: float = 0.25,
) -> BiomekI7Device:
  """A Biomek i7 (`BiomekI7Device`) and what drives it, as `STAR(...)` builds a `STARDevice`, with
  its arguments in the same order.

  Args:
    deck: the deck. Defaults to the one the declared configuration lays out (`deck_layout`,
      `BiomekI7Deck.from_configuration`), else an empty one (place ALPs with `deck.assign_alp`).
    simulation: drive a simulated instrument instead (`driver/simulator.py`): powered up, not
      configured, not homed. Ignored when a driver is given.
    declared_configuration_json: the instrument's declared configuration (its calibration, axis scales
      and limits, deck positions), as `DeviceConfiguration.from_biomek` makes it from its Biomek
      files, for the driver this builds. Required for the instrument; in simulation, the package's
      generic one when None. Ignored when a driver is given.
    simulator_recording: the cold-start recording a simulated instrument answers identity and
      sensor reads from; `$BIOMEK_I7_SIMULATOR_RECORDING` when None, else the package's generic
      one (`defaults/biomek_i7_generic_coldstart.json`).
    driver: what talks to the instrument, instead of the one this builds.
    name: the resource's name.
    state_json: where the device keeps its tree's state between sessions, the tips on each head
      among it (`BiomekI7Device`'s `state_json`).
    simulate_motion_time: whether a simulated move takes time at all, as the STAR's and the
      Prep's simulators take it: the time the instrument's drives would take (from each move's
      distance, speed and acceleration), so a viewer shows each step. Off, every move answers at
      once.
    motion_time_scale: the share of the instrument's time a move takes when it does: a quarter,
      so a step is watched rather than waited for. 1.0 keeps the instrument's time.

  Two modes, as the STAR's: the instrument (`BiomekI7Driver`, from its own declared
  configuration; nothing is opened until `setup`), or a simulated one (`simulation=True`). A
  simulated instrument given no configuration is the package's generic one (`defaults/`): a
  Biomek i7 with one instrument's physical values, no serial numbers and no measured hysteresis.
  The instrument is never driven from it.
  """
  if driver is not None and simulation:
    logger.warning("both a driver and simulation given; driving the driver")
  if driver is None and simulation:
    if declared_configuration_json is None:
      logger.info("simulating a generic Biomek i7 (%s)", GENERIC_CONFIGURATION.name)
      declared_configuration_json = str(GENERIC_CONFIGURATION)
    if simulator_recording is None and not os.environ.get(RECORDING_ENV):
      simulator_recording = str(GENERIC_RECORDING)
    tables = DeviceConfiguration.load(declared_configuration_json).motor_tables
    boards = simulated_boards(
      motor_tables=tables,
      path=simulator_recording,
      time_scale=motion_time_scale if simulate_motion_time else 0.0,
    )
    driver = BiomekI7Driver(declared_configuration_json=declared_configuration_json, boards=boards)
    sense_recorded_tips(driver, boards)  # the simulator does not model tips
    simulate_grips(driver, boards)  # nor labware between the fingers
  elif driver is None:
    driver = BiomekI7Driver(declared_configuration_json=declared_configuration_json)
  return BiomekI7Device(driver=driver, deck=deck, name=name, state_json=state_json)


def _world_box(
  name: str,
  frame: Tuple[Coordinate, List[List[float]]],
  x: Tuple[float, float],
  y: Tuple[float, float],
  z: Tuple[float, float],
  shift: Tuple[float, float, float],
) -> Part:
  """The box `x` by `y` (mm along a frame's axes, from its origin; `frame`: that origin on the deck
  and its rotation matrix), `z` (mm from the deck), in Biomek's frame (cm), moved by `shift`
  (cm)."""
  origin, matrix = frame
  xs, ys = [], []
  for px in x:
    for py in y:
      d = matrix_vector_multiply_3x3(matrix, (px, py, 0.0))
      xs.append(units.biomek_x(origin.x + d[0]) / 10)
      ys.append(units.biomek_y(origin.y + d[1]) / 10)
  dx, dy, dz = shift
  return obstacle_part(
    name,
    (min(xs) + dx, max(xs) + dx),
    (min(ys) + dy, max(ys) + dy),
    (z[0] / 10 + dz, z[1] / 10 + dz),
  )
