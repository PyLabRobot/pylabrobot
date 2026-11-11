"""Bringing a pod's axes up, as the Master Controller does it.

Enabling (after the interlock is armed, or a fault): the Master Controller enables a pod's axes in a
fixed order, and on the Multichannel first its Z alone, then stops motion; this order, as its logs
show it, is what `Pod.enable_axes` sends.
"""

from typing import Dict, Tuple

# The order the Master Controller enables every axis in after the interlock is armed, and reads
# their positions in. The Multichannel board is sent one Z enable first.
ENABLE_ORDER: Dict[str, Tuple[str, ...]] = {
  "multichannel": ("GZ", "GR", "GY", "GG", "Z", "D", "X", "Y"),
  "span8": (
    "GR", "GG", "GZ", "D8", "GY", "D6", "D7", "D4", "D5", "D2", "D3", "Z8", "D1", "Z6", "Z7",
    "Z4", "Z5", "Z2", "Z3", "Y8", "Z1", "Y6", "Y7", "Y4", "Y5", "Y2", "Y3", "X", "Y1",
  ),
}  # fmt: skip
ENABLE_PREFIX: Dict[str, Tuple[str, ...]] = {"multichannel": ("Z",), "span8": ()}
