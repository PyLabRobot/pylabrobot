"""
This module defines the Barcode class, which represents a barcode associated with a resource.
It includes attributes for the barcode data, symbology, and its position on the resource.
"""

from dataclasses import dataclass
from typing import Literal

BarcodePosition = Literal["right", "front", "left", "back", "bottom", "top"]


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

  @staticmethod
  def deserialize(data: dict) -> "Barcode":
    return Barcode(
      data=data["data"],
      symbology=data["symbology"],
      position_on_resource=data["position_on_resource"],
    )

  def __str__(self) -> str:
    return f'Barcode(data="{self.data}", symbology="{self.symbology}", position_on_resource="{self.position_on_resource}")'
