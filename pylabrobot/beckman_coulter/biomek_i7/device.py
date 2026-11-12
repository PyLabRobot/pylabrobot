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
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from pylabrobot.beckman_coulter.biomek_i7 import units
from pylabrobot.beckman_coulter.biomek_i7.deck import (
  FRAME_X,
  FRAME_Y,
  BiomekI7Deck,
  biomek_x,
  biomek_y,
  deck_x,
  deck_y,
)
from pylabrobot.beckman_coulter.biomek_i7.driver.features.chassis import Chassis
from pylabrobot.beckman_coulter.biomek_i7.driver.features.door import Door
from pylabrobot.beckman_coulter.biomek_i7.driver.features.pod import Pod
from pylabrobot.beckman_coulter.biomek_i7.driver.master import BiomekI7Driver

from pylabrobot.beckman_coulter.biomek_i7.driver.simulator import RECORDING_ENV, simulated_boards
from pylabrobot.beckman_coulter.biomek_i7.driver import pod_geometry as g
from pylabrobot.beckman_coulter.biomek_i7.defaults import GENERIC_CONFIGURATION, GENERIC_RECORDING
from pylabrobot.beckman_coulter.biomek_i7.driver.configuration import (
  DeviceConfiguration,
  PodConfiguration,
)
from pylabrobot.beckman_coulter.biomek_i7.resource_model import cm, pod_model
from pylabrobot.beckman_coulter.biomek_i7.resource_model.heads import PROBES
from pylabrobot.beckman_coulter.biomek_i7.resource_model.pods import (
  GripperModel,
  gripper_model,
  place_head96,
  place_span8_channel,
  span8_channels,
)
from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.resource import Resource

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


class BiomekI7Device(Resource):
  """A Biomek i7: its frame, a deck, and a bridge per pod, driven through `driver` (STAR's
  `STARDevice`; `BiomekI7(...)` builds one, as `STAR(...)` does).

  Each pod's model (`pod_models[side]`) rides its bridge (`bridges[side]`), which is the pod's
  `resource` once the driver has found it (STAR's `x_arm.resource`). The models are built from the
  instrument's declared geometry when the device is made, and each bridge follows its pod's X as
  the driver moves it, placed as Biomek's collision model places it, so the resource tree takes up
  the X the rail guard counts. The rest follows the pod's other axes (`_show_pod`): the
  Multichannel's carriage its Y and its head (an `NChannelPipette`) its Z, each Span-8 probe's
  channel its own Y and Z, and the gripper (a `MechanicalGripper`) GY, GZ, GR and GG. Each bridge
  marks the point its pod's X names (`resource_model.pods.pod_model`). `update_bridge_location`,
  `update_pod_location` and `update_probe_location` place the models by hand; nothing moves.
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
    if deck is None:
      # The default deck the declared configuration lays out, else an empty one.
      deck = BiomekI7Deck.from_configuration(driver.declared)
    self.deck = deck
    driver.deck = self.deck  # the operations work on this deck (STAR's `driver.deck`)
    self.assign_child_resource(self.deck, location=Coordinate(DECK_X, DECK_Y, DECK_Z))

    self.bridges: Dict[str, Resource] = {}
    self.pod_models: Dict[str, Resource] = {}
    # Each pod's gripper (from its declared model), posed from its axes.
    self.gripper_models: Dict[str, GripperModel] = {}
    self._unfollow: List[Callable[[], None]] = []
    self._build_declared_models(driver.declared)
    self.state_json = state_json
    if state_json is not None and os.path.exists(state_json):
      self.load_state_from_file(state_json)  # the tips on the declared heads' shafts
    driver.on_tips_changed = self._tips_changed
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

  async def setup(
    self,
    skip_device_initialization: bool = False,
    *,
    skip_left_x_arm: bool = False,
    skip_right_x_arm: bool = False,
    force_initialize: bool = False,
    park: bool = True,
  ) -> None:
    """Bring the instrument up. This homes the arms, then parks the pods, as Biomek's Home All
    does (`park`).

    Args:
      skip_device_initialization: as `BiomekI7Driver.setup` takes it.
      skip_left_x_arm: as `BiomekI7Driver.setup` takes it.
      skip_right_x_arm: as `BiomekI7Driver.setup` takes it.
      force_initialize: as `BiomekI7Driver.setup` takes it.
      park: park every pod that was brought up (`park`).
    """
    await self.driver.setup(
      skip_device_initialization=skip_device_initialization,
      skip_left_x_arm=skip_left_x_arm,
      skip_right_x_arm=skip_right_x_arm,
      force_initialize=force_initialize,
    )
    self._follow_driver()
    skipped = self.driver.skipped_sides(
      skip_left_x_arm=skip_left_x_arm, skip_right_x_arm=skip_right_x_arm
    )
    if park:
      await self.park(skip=skipped)

  async def park(self, skip: Iterable[str] = ()) -> None:
    """Park the pods as Biomek does (`Pod.park`), the left pod first, as each pod's park is at its
    own end of the rail. `skip`: sides to leave as they are."""
    for pod in sorted(self.driver.arms, key=lambda p: p.side != "left"):
      if pod.side in skip:
        continue
      await pod.park()

  async def pause(self) -> None:
    """Pause for access, as Biomek's Pause step: the pods' drive power is cut and PAUSED shown,
    so the light curtain may be broken. Move the pods out of the way first if needed
    (`move_aside`, `park`). See `BiomekI7Driver.pause`."""
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
      if pod.declared is not None:
        self._unfollow.append(pod.add_position_listener(self._show_pod))
        self._show_pod(pod)

  def _tips_changed(self) -> None:
    """A head's tips changed: save the tree's state, when there is a `state_json`."""
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
    """(Re)build a side's gripper on its bridge, posed as parked until the driver says otherwise."""
    old = self.gripper_models.pop(side, None)
    if old is not None:
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
    """Pose a pod's gripper model from its axes (the rotation centre: `pod_geometry`)."""
    d, model = pod.declared, self.gripper_models.get(pod.side)
    if d is None or model is None:
      return
    v = {a: self._axis_value(pod, a) for a in ("GY", "GZ", "GR", "GG")}
    if any(value is None for value in v.values()):
      return
    gy, gz, gr, gg = (float(v[a]) for a in ("GY", "GZ", "GR", "GG"))  # type: ignore[arg-type]
    model.pose(self._gripper_centre(pod.side, d, gy, gz), gr, gg)

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

  def traverse_clearance(
    self, side: str, x: Tuple[float, float], y: Tuple[float, float], tip_length: float = 0.0
  ) -> float:
    """The lowest height, mm above the deck, a pod's channels may travel at over the area `x` by
    `y` (deck x and y ranges, mm), for `Pod.move_over`'s `clearance`: the top of the tallest
    resource there, plus the pod's `AdditionalSafeHeight`, plus `tip_length` (mm the tips reach
    below the channels' bottom). Its Biomek-unit counterpart is `_traverse_clearance`."""
    bx = sorted(units.to_cm(units.biomek_x(v)) for v in x)
    by = sorted(units.to_cm(units.biomek_y(v)) for v in y)
    clearance = self._traverse_clearance(
      side, (bx[0], bx[1]), (by[0], by[1]), units.to_cm(tip_length)
    )
    return units.to_mm(clearance)

  def _traverse_clearance(
    self, side: str, x: Tuple[float, float], y: Tuple[float, float], tip_length: float = 0.0
  ) -> float:
    """The lowest height (Biomek Z, cm) a pod's channels may travel at over the area `x` by `y`
    (Biomek X/Y, cm), for `Pod._move_over`'s `clearance`: the top of the tallest resource on the
    deck there, plus the pod's `AdditionalSafeHeight`, plus `tip_length` (cm the tips reach below
    the channels' bottom). Biomek's Z is the deck model's z in cm (design.md I9). The deck itself,
    its ALPs' frames included, counts; the pods' own models do not."""
    pod = self.driver.arm(side)
    if pod.declared is None:
      raise RuntimeError(f"{pod} has no declared configuration")
    top = 0.0
    for resource in self.deck.get_all_children():
      loc = resource.get_location_wrt(self.deck)
      left, right = biomek_x(loc.x) / 10, biomek_x(loc.x + resource.get_size_x()) / 10
      back, front = biomek_y(loc.y + resource.get_size_y()) / 10, biomek_y(loc.y) / 10
      if left < x[1] and x[0] < right and back < y[1] and y[0] < front:
        top = max(top, (loc.z + resource.get_size_z()) / 10)
    return top + pod.declared.geometry.additional_safe_height + tip_length

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
    driver = BiomekI7Driver(
      declared_configuration_json=declared_configuration_json, boards=boards
    )
  elif driver is None:
    driver = BiomekI7Driver(declared_configuration_json=declared_configuration_json)
  return BiomekI7Device(driver=driver, deck=deck, name=name, state_json=state_json)
