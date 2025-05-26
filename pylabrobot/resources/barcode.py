"""
This module defines the Barcode class, which represents a barcode associated with a resource.
It includes attributes for the barcode data, symbology, and its position on the resource.
"""

from typing import Literal
from dataclasses import dataclass

BarcodePosition = Literal["right", "front", "left", "back", "bottom", "top"]

@dataclass
class Barcode:
  data: str
  symbology: str
  position_on_resource: BarcodePosition
