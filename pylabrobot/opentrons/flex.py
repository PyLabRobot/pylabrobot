import logging
import uuid
from typing import Any, Dict, List, Optional, Type, cast

from pylabrobot.opentrons.flex_gripper import FlexGripper
from pylabrobot.opentrons.flex_head import FlexHead1, FlexHead8, FlexHead96, _FlexHead
from pylabrobot.opentrons.robot import OpentronsError, OpentronsRobot
from pylabrobot.opentrons.transport import OpentronsTransport
from pylabrobot.resources import Resource
from pylabrobot.resources.opentrons.flex_deck import FlexDeck
from pylabrobot.resources.trash import Trash

logger = logging.getLogger(__name__)

_OT_NAMESPACE = "opentrons"
_OT_VERSION = 1

_TIP_RACK_MAP = {
  "flex_96_tiprack_50ul": "opentrons_flex_96_tiprack_50ul",
  "flex_96_tiprack_200ul": "opentrons_flex_96_tiprack_200ul",
  "flex_96_tiprack_1000ul": "opentrons_flex_96_tiprack_1000ul",
  "flex_96_tiprack_20ul": "opentrons_flex_96_tiprack_20ul",
  "flex_96_filtertiprack_50ul": "opentrons_flex_96_filtertiprack_50ul",
  "flex_96_filtertiprack_200ul": "opentrons_flex_96_filtertiprack_200ul",
  "flex_96_filtertiprack_1000ul": "opentrons_flex_96_filtertiprack_1000ul",
  "flex_96_filtertiprack_20ul": "opentrons_flex_96_filtertiprack_20ul",
}

# Discovered pipette channel count -> matching head class.
_CHANNELS_TO_HEAD: Dict[int, Type[_FlexHead]] = {
  1: FlexHead1,
  8: FlexHead8,
  96: FlexHead96,
}


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
    self.left: Optional[_FlexHead] = None
    self.right: Optional[_FlexHead] = None
    self.head96: Optional[_FlexHead] = None
    self.gripper: Optional[FlexGripper] = None
    self._heads: List[_FlexHead] = []

  async def _model_setup(self) -> None:
    await self.home()

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
      head = head_cls(self, pip.mount, pipette_id, pip.channels)

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

  async def _ensure_labware_loaded(self, resource: Resource) -> str:
    """Load labware into the Flex run if not already loaded."""
    name = getattr(resource, "name", str(resource))
    if name in self._loaded_labware:
      return self._loaded_labware[name]

    slot = self.deck.get_slot(resource)
    if slot is None:
      raise OpentronsError(
        "Resource not on deck",
        f"'{name}' is not on a deck slot. Use deck.assign_child_at_slot(resource, slot='C1').",
      )

    load_name = self._ot_load_name(resource)
    labware_id = uuid.uuid4().hex[:12]

    result = await self._execute_command(
      "loadLabware",
      {
        "loadName": load_name,
        "location": {"slotName": slot},
        "namespace": _OT_NAMESPACE,
        "version": _OT_VERSION,
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
    was never loaded into the run; a re-add later loads fresh at its new slot.
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
    slot = self.deck.get_slot(resource)
    if slot is not None:
      self.deck.unassign_child_at_slot(slot)
    logger.info("Labware '%s' marked moved off-deck", name)

  @staticmethod
  def _ot_load_name(resource: Resource) -> str:
    """Resolve a PLR resource to its Opentrons labware load name."""
    if hasattr(resource, "ot_load_name"):
      return cast(str, resource.ot_load_name)

    name_lower = getattr(resource, "name", "").lower()

    for key, ot_name in _TIP_RACK_MAP.items():
      if key in name_lower:
        return ot_name

    if name_lower.startswith("opentrons_"):
      return name_lower

    raise OpentronsError(
      "Cannot determine Opentrons load name",
      f"'{name_lower}' — set resource.ot_load_name = 'opentrons_flex_96_tiprack_50ul' "
      f"or use a standard Flex labware name.",
    )
