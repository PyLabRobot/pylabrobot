"""Plate-aware Flex action requests and resource-tree transfer checks."""

from dataclasses import dataclass
from typing import Optional

from pylabrobot.resources import Plate, PlateCarrier, PlateHolder, Resource

from .connection import FlexError
from .protocol import FlexBoundProtocol, FlexCommand, FlexPrompt


@dataclass(frozen=True, kw_only=True)
class FlexAction(FlexPrompt):
  """A load, unload or intervention at the single accessible carousel station.

  ``plate`` is the bound object, or None for a pause without a designated target.
  ``holder`` is the persistent presented tray. ``label`` contains instructions;
  ``additions`` contains required reagent additions. Source line numbers are
  one-based and refer to the original script identified by ``source_sha256``.

  A handler must finish its work, update modeled transfers and clear the motion
  envelope before returning True. Resource assignment alone is not completion.
  """

  command: FlexCommand
  source_sha256: str
  plate_name: Optional[str]
  plate: Optional[Plate]
  holder: PlateHolder


def create_actions(
  protocol: FlexBoundProtocol, carousel: PlateCarrier, commands: tuple[FlexCommand, ...]
) -> dict[int, FlexAction]:
  """Resolve all script actions before any execution commands are transmitted."""
  by_line = {command.line_number: command for command in commands}
  names = {plate.id: plate.name for plate in protocol.protocol.plates}
  return {
    prompt.line_number: FlexAction(
      line_number=prompt.line_number,
      action=prompt.action,
      label=prompt.label,
      processing_slot=prompt.processing_slot,
      loading_slot=prompt.loading_slot,
      additions=prompt.additions,
      plate_id=prompt.plate_id,
      command=by_line[prompt.line_number],
      source_sha256=protocol.protocol.sha256,
      plate_name=names[prompt.plate_id] if prompt.plate_id is not None else None,
      plate=protocol.plates_by_id[prompt.plate_id] if prompt.plate_id is not None else None,
      holder=carousel[prompt.loading_slot],
    )
    for prompt in protocol.prompts()
  }


def expected_inventory(
  action: FlexAction, before: tuple[Optional[Resource], ...]
) -> tuple[Optional[Resource], ...]:
  """Check action prerequisites and return a temporary expected post-action snapshot.

  This is an execution expectation, not a second occupancy model. Actual contents
  are always read from the carousel resource tree. Preloaded matching plates are
  allowed; loading still requires explicit handler or instrument completion.
  """
  plate = action.plate
  if plate is None:
    return before
  slot = action.loading_slot - 1
  occupant = before[slot]
  context = f"Line {action.line_number}: {action.action} {action.plate_name!r}, tray {slot + 1}"
  if action.action == "load":
    if occupant is not None and occupant is not plate:
      raise FlexError(f"{context}: holder is occupied by {occupant.name!r}")
    if any(resource is plate for i, resource in enumerate(before) if i != slot):
      raise FlexError(f"{context}: bound plate is assigned to a different Flex tray")
    return before[:slot] + (plate,) + before[slot + 1 :]
  if occupant is not plate:
    raise FlexError(f"{context}: expected bound plate is not in the presented holder")
  if action.action == "unload":
    return before[:slot] + (None,) + before[slot + 1 :]
  return before
