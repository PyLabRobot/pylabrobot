"""The MPE2's plate states, as its firmware's SPEControl reports them, and its waste containers."""

from typing import Dict, Literal

from pylabrobot.hamilton.mpe2 import commands as cmd

S = cmd.SPEControlState

WasteContainerNumber = Literal[1, 2]

# The plate states by name, as ``MPE2.request_state()`` gives them (the firmware's SPEControlState).
MPE2State = Literal[
  "uninitialized",
  "initializing",
  "ready",
  "filter_plate_placed",
  "clamping_filter_plate_1",
  "filter_plate_clamped",
  "process_filter_plate_to_waste",
  "retrieving_filter_plate_1",
  "collection_plate_placed",
  "retrieving_filter_plate_2",
  "integration_area_plate_stack",
  "process_filter_plate_to_collection",
  "clamping_filter_plate_2",
  "evaporator_in_integration_area",
  "evaporator_entering_process_area",
  "evaporator_in_process_area",
  "priming_ready",
  "priming_filter_plate_placed",
  "priming_filter_plate_clamped",
  "priming_collection_plate_placed",
  "priming_integration_area_plate_stack",
  "dispensing_filter_plate_in_dispensing_area",
  "dispensing_collection_plate_in_dispensing_area",
  "collection_plate_in_process_area",
  "dispensing_integration_area_plate_stack_in_dispensing_area",
  "dispensing_filter_plate_placed",
  "dispensing_collection_plate_placed",
]

WASTE: Dict[int, cmd.WasteContainer] = {
  1: cmd.WasteContainer.CONTAINER_1,
  2: cmd.WasteContainer.CONTAINER_2,
}

# The states with the filter plate clamped, or on its way under the manifold.
CLAMPED = {
  S.CLAMPING_FILTER_PLATE_1,
  S.FILTER_PLATE_CLAMPED,
  # The filter plate stays clamped while the collection plate is placed under it.
  S.COLLECTION_PLATE_PLACED,
  S.PROCESS_FILTER_PLATE_TO_WASTE,
  S.CLAMPING_FILTER_PLATE_2,
  S.PROCESS_FILTER_PLATE_TO_COLLECTION,
  S.COLLECTION_PLATE_IN_PROCESS_AREA,
}
# A retrieve under way: seen only when one was interrupted. The plate is in the MPE2, but whether
# a retrieve may be sent again isn't known.
RETRIEVING = {S.RETRIEVING_FILTER_PLATE_1, S.RETRIEVING_FILTER_PLATE_2}
WITH_COLLECTION_PLATE = {
  S.COLLECTION_PLATE_PLACED,
  S.INTEGRATION_AREA_PLATE_STACK,
  S.COLLECTION_PLATE_IN_PROCESS_AREA,
}


def state_name(state: int) -> str:
  """A control state as a snake_case name ("filter_plate_clamped"); "state_<n>" for one the
  firmware's table doesn't name."""
  try:
    return S(state).name.lower()
  except ValueError:
    return f"state_{state}"
