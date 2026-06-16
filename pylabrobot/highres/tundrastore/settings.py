import json
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class TundraStoreSettings:
  """Client-side view of the TundraStore's on-device settings file.

  The device exposes ~545 calibration/config keys via the ``settings`` command
  as ``NAME = value`` text. Values are kept verbatim as strings in :attr:`raw`
  (the device's own representation); use the typed accessors and named
  properties below for parsed values. Addresses like ``2004007`` are
  Copley/digital-IO register addresses a sensor is wired to.
  """

  raw: Dict[str, str] = field(default_factory=dict)
  serial_number: Optional[str] = None
  firmware_version: Optional[str] = None

  # --- generic typed access -------------------------------------------------

  def get(self, key: str) -> Optional[str]:
    return self.raw.get(key)

  def get_float(self, key: str) -> Optional[float]:
    v = self.raw.get(key)
    try:
      return float(v) if v is not None else None
    except ValueError:
      return None

  def get_int(self, key: str) -> Optional[int]:
    v = self.raw.get(key)
    try:
      return int(v) if v is not None else None
    except ValueError:
      return None

  # --- named accessors for settings the driver reasons about ----------------

  @property
  def height_detect_base(self) -> Optional[float]:
    return self.get_float("HEIGHT_DETECT_BASE")

  @property
  def height_detect_positive_adjustment(self) -> Optional[float]:
    return self.get_float("HEIGHT_DETECT_POSITIVE_ADJUSTMENT")

  @property
  def height_detect_negative_adjustment(self) -> Optional[float]:
    return self.get_float("HEIGHT_DETECT_NEGATIVE_ADJUSTMENT")

  @property
  def height_detect_input_number(self) -> Optional[int]:
    return self.get_int("HEIGHT_DETECT_INPUT_NUMBER")

  @property
  def height_detect_enable_address(self) -> Optional[int]:
    return self.get_int("HEIGHT_DETECT_ENABLE_ADDRESS")

  @property
  def spatula_beam_break_height(self) -> Optional[float]:
    return self.get_float("SPATULA_BEAM_BREAK_HEIGHT")

  @property
  def default_plate_height(self) -> Optional[float]:
    return self.get_float("DEF_PLATE_HEIGHT")

  @property
  def default_plate_thickness(self) -> Optional[float]:
    return self.get_float("DEF_PLATE_THICKNESS")

  def stacker_base(self, bank: int) -> Optional[float]:
    """Z height of stacker base ``bank`` (slot 1 sits here)."""
    return self.get_float(f"STACKER_BASE_{bank}")

  def nest_height(self, nest: int) -> Optional[float]:
    return self.get_float(f"NEST_{nest}_HEIGHT")

  def nest_sense_input(self, nest: int) -> Optional[int]:
    """Digital-IO address of nest ``nest``'s plate-presence sensor."""
    return self.get_int(f"NEST_{nest}_SENSE_INPUT")

  # --- (de)serialization ----------------------------------------------------

  @classmethod
  def from_dict(
    cls, data: Dict, serial: Optional[str] = None, firmware: Optional[str] = None
  ) -> "TundraStoreSettings":
    return cls(
      raw={str(k): str(v) for k, v in data.items()},
      serial_number=serial,
      firmware_version=firmware,
    )

  @classmethod
  def from_lines(
    cls, lines, serial: Optional[str] = None, firmware: Optional[str] = None
  ) -> "TundraStoreSettings":
    """Parse the device's ``NAME = value`` settings lines."""
    raw: Dict[str, str] = {}
    for line in lines:
      if "=" in line:
        key, _, value = line.partition("=")
        raw[key.strip()] = value.strip()
    return cls.from_dict(raw, serial, firmware)

  @classmethod
  def from_json(cls, path: str) -> "TundraStoreSettings":
    with open(path) as f:
      obj = json.load(f)
    if "settings" in obj:
      return cls.from_dict(obj["settings"], obj.get("_serial"), obj.get("_firmware"))
    return cls.from_dict(obj)

  def to_json(self, path: str) -> None:
    with open(path, "w") as f:
      json.dump(
        {
          "_serial": self.serial_number,
          "_firmware": self.firmware_version,
          "settings": self.raw,
        },
        f,
        indent=2,
      )

  def __len__(self) -> int:
    return len(self.raw)

  def __getitem__(self, key: str) -> str:
    return self.raw[key]

  def __contains__(self, key: str) -> bool:
    return key in self.raw
