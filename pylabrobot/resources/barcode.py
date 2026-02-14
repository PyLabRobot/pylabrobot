"""
This module defines the Barcode class, which represents a barcode associated with a resource.
It includes attributes for the barcode data, symbology, and its position on the resource.
"""

from dataclasses import dataclass
from typing import Literal

BarcodePosition = Literal["right", "front", "left", "back", "bottom", "top"]

Barcode1DSymbology = Literal[
  "ISBT Standard",
  "Code 128 (Subset B and C)",
  "Code 39",
  "Codebar",
  "Code 2of5 Interleaved",
  "UPC A/E",
  "YESN/EAN 8",
  "Code 93",
  "ANY 1D",  # wildcard for any 1D symbology available, depends on scanner capabilities
]


@dataclass
class Barcode:
  data: str
  symbology: str
  position_on_resource: BarcodePosition

  def serialize(self) -> dict:
    return {
      "data": self.data,
      "symbology": self.symbology,
      "position_on_resource": self.position_on_resource,
    }

  def __str__(self) -> str:
    return f'Barcode(data="{self.data}", symbology="{self.symbology}", position_on_resource="{self.position_on_resource}")'
