import logging
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple, Type, cast

from pylabrobot.opentrons.flex_gripper import FlexGripper
from pylabrobot.opentrons.catalogue import is_catalogue_labware
from pylabrobot.opentrons.flex_head import FlexHead1, FlexHead8, FlexHead96, _FlexHead
from pylabrobot.opentrons.flex_wire import slot_wire_location
from pylabrobot.opentrons.labware_definitions import (
  build_container_definition,
  build_movable_labware_definition,
  build_plate_definition,
  build_tip_rack_definition,
)
from pylabrobot.opentrons.robot import OpentronsError, OpentronsRobot
from pylabrobot.opentrons.transport import OpentronsTransport
from pylabrobot.resources import Container, Plate, Resource, TipRack
from pylabrobot.resources.opentrons.flex_deck import FlexDeck
from pylabrobot.resources.trash import Trash

logger = logging.getLogger(__name__)

_OT_NAMESPACE = "opentrons"

# Catalogue definition revision per load name -- see _ot_catalogue_identity.
_OT_VERSION = 1
_OT_CATALOGUE_VERSIONS = {
  "appliedbiosystemsmicroamp_384_wellplate_40ul": 3,
  "armadillo_96_wellplate_200ul_pcr_full_skirt": 2,
  "biorad_384_wellplate_50ul": 2,
  "biorad_96_wellplate_200ul_pcr": 2,
  "corning_12_wellplate_6.9ml_flat": 2,
  "corning_384_wellplate_112ul_flat": 2,
  "corning_48_wellplate_1.6ml_flat": 2,
  "corning_96_wellplate_360ul_flat": 2,
  "nest_1_reservoir_195ml": 2,
  "nest_96_wellplate_100ul_pcr_full_skirt": 2,
  "nest_96_wellplate_200ul_flat": 2,
  "nest_96_wellplate_2ml_deep": 2,
  "opentrons_96_wellplate_200ul_pcr_full_skirt": 2,
}

# Discovered pipette channel count -> matching head class.
_CHANNELS_TO_HEAD: Dict[int, Type[_FlexHead]] = {
  1: FlexHead1,
  8: FlexHead8,
  96: FlexHead96,
}


def _has_pipettable_geometry(resource: Resource) -> bool:
  """Whether a real, well-bearing definition can be built from this resource."""
  return isinstance(resource, (Plate, TipRack, Container))


def _not_pipettable_error(resource: Resource) -> OpentronsError:
  """The refusal for a resource no pipettable definition can be built from."""
  return OpentronsError(
    "Cannot build an Opentrons labware definition",
    f"'{resource.name}' ({type(resource).__name__}) has no Opentrons load name, and a "
    "definition can only be built from the geometry of a Plate, TipRack, or Container. "
    "Set resource.ot_load_name to an official Opentrons load name. The gripper can still "
    "move it -- only pipetting needs real well geometry.",
  )


def _warn_grip_distance_discarded(
  name: str, grip_distance_from_top: Optional[float], why: str
) -> None:
  """Log a caller's grip distance being dropped, naming why it cannot apply."""
  if grip_distance_from_top is None:
    return
  logger.warning(
    "grip_distance_from_top=%s ignored for '%s': %s.", grip_distance_from_top, name, why
  )


class OpentronsFlex(OpentronsRobot):
  """Opentrons Flex liquid handler (plain class, post-#1180 architecture).

  A device shell: it owns the deck, deck-scoped labware loading, and the
  discover-then-compose lifecycle that builds mount-addressed head
  sub-objects (``left``/``right``/``head96``). Liquid-handling ops live on
  the heads, not here — see :mod:`pylabrobot.opentrons.flex_head`.
  """

  def __init__(
    self,
    deck: FlexDeck,
    host: str,
    port: int = 31950,
    transport: Optional[OpentronsTransport] = None,
  ) -> None:
    super().__init__(host=host, port=port, transport=transport)
    self.deck = deck
    self._loaded_labware: Dict[str, str] = {}
    # resource.name -> (namespace, load_name, version) of an uploaded custom definition.
    self._defined_labware: Dict[str, Tuple[str, str, int]] = {}
    # Names loaded under the non-pipettable movable stub. Tracked separately so
    # a pipetting op refuses them even on a load-cache hit.
    self._stub_labware: Set[str] = set()
    self.left: Optional[_FlexHead] = None
    self.right: Optional[_FlexHead] = None
    self.head96: Optional[_FlexHead] = None
    self.gripper: Optional[FlexGripper] = None
    self._heads: List[_FlexHead] = []

  async def _create_run(self) -> str:
    # labwareIds and uploaded definitions are both run-scoped server-side, so
    # a new run must not serve cached identities from a previous one.
    run_id = await super()._create_run()
    self._loaded_labware.clear()
    self._defined_labware.clear()
    self._stub_labware.clear()
    return run_id

  async def _model_setup(self) -> None:
    """Discover and compose heads. Homing is setup()'s own step, so that a
    caller can ask what is mounted without moving the robot."""
    # Discover ALL mounted pipettes (not just the first — _discover_pipette
    # only surfaces one) and compose the matching head per mount. The base
    # setup() no longer discovers/loads a pipette itself (that would double
    # `loadPipette` the first mount), so this is the only place a Flex loads
    # its pipettes.
    instruments_data = await self._get_instruments()
    pipettes = self._parse_pipettes(instruments_data)

    if not pipettes:
      raise OpentronsError("No pipette detected", f"{self.host}:{self.port}")

    if any(pip.channels == 96 for pip in pipettes) and len(pipettes) > 1:
      raise OpentronsError(
        "Impossible instrument combination",
        "A 96-channel head cannot be mounted alongside another pipette on a Flex.",
      )

    for pip in pipettes:
      pipette_id = await self._load_pipette(pip.pipette_name, pip.mount)
      head_cls = _CHANNELS_TO_HEAD.get(pip.channels)
      if head_cls is None:
        raise OpentronsError(
          "Unsupported pipette channel count",
          f"{pip.channels} channels (mount '{pip.mount}') has no matching FlexHead.",
        )
      head = head_cls(self, pip.mount, pipette_id, pip.channels, pip.pipette_model)

      if pip.channels == 96:
        self.head96 = head
      elif pip.mount == "left":
        self.left = head
      elif pip.mount == "right":
        self.right = head
      else:
        raise OpentronsError("Unknown mount", f"mount '{pip.mount}' is neither 'left' nor 'right'.")
      self._heads.append(head)

    for head in self._heads:
      await head._on_setup()

    # The gripper (extension mount) is optional: compose it when discovery
    # reports one, leave ``self.gripper`` None otherwise.
    gripper_model = self._parse_gripper(instruments_data)
    if gripper_model is not None:
      self.gripper = FlexGripper(self, gripper_model)
      logger.info("Discovered gripper on the extension mount (model: %s)", gripper_model)

  def _parse_gripper(self, instruments_data: Dict[str, Any]) -> Optional[str]:
    """Parse the /instruments response for a mounted gripper.

    Returns the gripper's model string, or ``None`` when none is mounted.
    Separate from ``_parse_pipettes``, which filters to
    ``instrumentType == 'pipette'`` and knows nothing about grippers.
    """
    for instrument in instruments_data.get("data", []):
      if instrument.get("instrumentType") == "gripper":
        return cast(str, instrument.get("instrumentModel", "unknown"))
    return None

  async def stop(self) -> None:
    # Drop any mounted tips to the trash BEFORE parking/disconnecting, so the
    # robot is never left holding tips. A failure here must not block the
    # home/cancel/disconnect that follows.
    try:
      trash: Optional[Trash] = self.deck.get_trash_area()
    except ValueError:
      trash = None
    if trash is not None:
      for head in reversed(self._heads):
        try:
          if any(tip is not None for tip in head.get_mounted_tips()):
            await head.discard_tips(trash)
        except Exception:
          logger.warning(
            "Dropping tips on stop failed for the %s head; continuing to disconnect.",
            head.mount,
            exc_info=True,
          )
    for head in reversed(self._heads):
      await head._on_stop()
    await super().stop()  # homes the gantry, then cancels the run + disconnects

  async def _ensure_labware_loaded(
    self,
    resource: Resource,
    *,
    allow_stub: bool = False,
    grip_distance_from_top: Optional[float] = None,
  ) -> str:
    """Load labware into the Flex run if not already loaded.

    ``allow_stub`` opts into the non-pipettable movable stub definition for a
    resource no real definition can be built from (a lid, an adapter, a tube
    rack). Only the gripper passes it: the stub's single fake well is
    somewhere to grip, not somewhere to pipette, and a zero-depth well at the
    labware's own bottom would put a tip on the deck. Every other caller gets
    an ``OpentronsError`` for such a resource, before any wire command --
    including when the gripper already loaded it earlier in the run.

    ``grip_distance_from_top`` feeds an uploaded custom definition's grip
    height and is honored on the FIRST load only; a cache hit (already
    loaded, or definition already uploaded) reuses the stored identity
    unchanged, and labware resolving to an official Opentrons load name
    ignores it entirely (the catalogue definition owns the grip height).
    Every discard is logged.
    """
    name = getattr(resource, "name", str(resource))
    if not allow_stub and name in self._stub_labware:
      raise _not_pipettable_error(resource)
    if name in self._loaded_labware:
      _warn_grip_distance_discarded(
        name,
        grip_distance_from_top,
        "it is already loaded in this run, and the grip height rides the definition it "
        "was loaded with",
      )
      return self._loaded_labware[name]

    slot = self.deck.get_slot(resource)
    if slot is None:
      raise OpentronsError(
        "Resource not on deck",
        f"'{name}' is not on a deck slot. Use deck.assign_child_at_slot(resource, slot='C1').",
      )

    try:
      load_name, version = self._ot_catalogue_identity(resource)
    except OpentronsError:
      # No official Opentrons definition: build one from the resource's PLR
      # geometry, upload it, and load by the uploaded definition's identity.
      namespace, load_name, version = await self._define_custom_labware(
        resource, grip_distance_from_top, allow_stub
      )
    else:
      namespace = _OT_NAMESPACE
      _warn_grip_distance_discarded(
        name,
        grip_distance_from_top,
        f"it loads the Opentrons catalogue definition '{load_name}', whose grip height is "
        "the vendor's to state (the robot grips at mid-height when it states none)",
      )
    labware_id = uuid.uuid4().hex[:12]

    result = await self._execute_command(
      "loadLabware",
      {
        "loadName": load_name,
        "location": slot_wire_location(slot),
        "namespace": namespace,
        "version": version,
        "labwareId": labware_id,
        "displayName": name,
      },
    )
    labware_id = cast(str, result.get("result", {}).get("labwareId", labware_id))

    self._loaded_labware[name] = labware_id
    logger.info(
      "Loaded labware '%s' at slot %s -> ID: %s (OT: %s)",
      name,
      slot,
      labware_id,
      load_name,
    )
    return labware_id

  async def labware_moved_off_deck(self, resource: Resource) -> None:
    """Tell the robot an EXTERNAL agent (human or lab transporter) removed labware.

    A logical move, not a gripper motion: ``moveLabware`` with strategy
    ``manualMoveWithoutPause`` drops the labware from the robot-server's deck
    model, freeing its slot. Without this the slot stays occupied server-side
    and a later load into it fails with ``LocationIsOccupiedError``. The
    PLR-side deck slot is freed too. No wire command is sent for labware that
    was never loaded into the run; a re-add later loads fresh at its new slot
    and re-uploads its definition -- a different same-named resource must not
    inherit the departed labware's geometry.
    """
    name = getattr(resource, "name", str(resource))
    if name in self._loaded_labware:
      await self._execute_command(
        "moveLabware",
        {
          "labwareId": self._loaded_labware[name],
          "newLocation": "offDeck",
          "strategy": "manualMoveWithoutPause",
        },
      )
      del self._loaded_labware[name]
    self._defined_labware.pop(name, None)
    self._stub_labware.discard(name)
    slot = self.deck.get_slot(resource)
    if slot is not None:
      self.deck.unassign_child_at_slot(slot)
    logger.info("Labware '%s' marked moved off-deck", name)

  @staticmethod
  def _ot_catalogue_identity(resource: Resource) -> Tuple[str, int]:
    """Resolve a PLR resource to its Opentrons load name and definition version.

    Catalogue definitions are versioned per load name, and version 1 is the
    OLDEST revision -- for much of the catalogue it predates the Flex and
    declares no gripper grip height, so the robot grips at the labware's
    mid-height rather than where the vendor says. ``_OT_CATALOGUE_VERSIONS``
    therefore pins the EARLIEST revision that states one. Earliest, not
    newest: a robot only holds the revisions its own software shipped with,
    and these have shipped since API 2.14, while their well geometry is
    identical to version 1's -- so the bump changes the grip and nothing else.
    Load names outside that map (every Flex tip rack among them, which ships
    one revision) stay at 1. A resource can override the version for its own
    load name by carrying an ``ot_version``.
    """
    declared = getattr(resource, "ot_load_name", None)
    if declared is not None:
      load_name = cast(str, declared)
      if not is_catalogue_labware(load_name):
        # Not an OpentronsError: that is the caller's signal to synthesize, which
        # would hide a typo behind geometry the operator never asked for.
        raise ValueError(
          f"'{resource.name}' declares ot_load_name '{load_name}', which Opentrons' labware "
          "catalogue does not define. Correct the load name, or drop the attribute to have a "
          "definition built from the resource's own geometry."
        )
    elif resource.model is not None and is_catalogue_labware(resource.model):
      load_name = resource.model
    else:
      raise OpentronsError(
        "Cannot determine Opentrons load name",
        f"'{resource.name}' has no ot_load_name, and its model "
        f"{resource.model!r} is not in Opentrons' catalogue.",
      )

    version = getattr(resource, "ot_version", None)
    if version is None:
      version = _OT_CATALOGUE_VERSIONS.get(load_name, _OT_VERSION)
    return load_name, cast(int, version)

  async def _define_custom_labware(
    self,
    resource: Resource,
    grip_distance_from_top: Optional[float] = None,
    allow_stub: bool = False,
  ) -> Tuple[str, str, int]:
    """Upload a geometry-derived definition for labware with no official Opentrons definition.

    Returns the uploaded definition's (namespace, load_name, version), parsed
    from the robot-server's ``definitionUri`` so the subsequent ``loadLabware``
    references exactly what the server stored. The parsed identity is cached
    (separately from ``_loaded_labware``) until the labware leaves the deck or
    a new run starts, so repeat calls within a stay on deck skip the
    re-upload -- which also means ``grip_distance_from_top`` only shapes the
    FIRST upload.
    """
    name = resource.name
    if name in self._defined_labware:
      _warn_grip_distance_discarded(
        name,
        grip_distance_from_top,
        "its custom definition was already uploaded in this run, with the grip height it "
        "carried then",
      )
      return self._defined_labware[name]

    definition = self._build_labware_definition(resource, grip_distance_from_top, allow_stub)
    assert self.run_id is not None, "No active run. Call setup() first."
    data = await self._post(f"/runs/{self.run_id}/labware_definitions", {"data": definition})
    uri = cast(str, data["data"]["definitionUri"])
    namespace, load_name, version = uri.split("/")
    self._defined_labware[name] = (namespace, load_name, int(version))
    if not _has_pipettable_geometry(resource):
      self._stub_labware.add(name)
    logger.info("Uploaded custom labware definition for '%s': %s", name, uri)
    return self._defined_labware[name]

  @staticmethod
  def _build_labware_definition(
    resource: Resource,
    grip_distance_from_top: Optional[float] = None,
    allow_stub: bool = False,
  ) -> dict:
    """Build the definition matching the resource's type, or raise for unbuildable labware.

    Pipettable types get real-geometry definitions. Any other resource (lid,
    adapter, tube rack, ...) has no wells the robot can pipette, so it builds
    only as the non-pipettable movable stub, which ``allow_stub`` opts into.
    Geometry an Opentrons definition cannot describe (a rotated plate, a
    cavity floor the resource never declared) is refused here, before any
    wire command, rather than uploaded for the robot to act on.
    """
    try:
      if isinstance(resource, Plate):
        return build_plate_definition(resource, grip_distance_from_top)
      if isinstance(resource, TipRack):
        return build_tip_rack_definition(resource, grip_distance_from_top)
      if isinstance(resource, Container):
        return build_container_definition(resource, grip_distance_from_top)
    except ValueError as e:
      raise OpentronsError("Cannot build an Opentrons labware definition", str(e)) from e
    if not allow_stub:
      raise _not_pipettable_error(resource)
    return build_movable_labware_definition(resource, grip_distance_from_top)
