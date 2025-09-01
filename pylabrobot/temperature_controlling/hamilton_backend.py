import re
import warnings
from typing import Dict, Optional, Union, Literal

# Reuse your existing interface & box classes:
# - HamiltonHeaterShakerInterface
# - HamiltonHeaterShakerBox
# If they're in another module, import them instead of redefining.


class HamiltonHeaterCoolerBackend:
  """Backend for Hamilton Heater Cooler (HHC) via a Heater Shaker Box/STAR interface.

  Notes:
    - Uses the same async send method: interface.send_hhs_command(index, command, **kwargs)
    - Temperature range asserted: (0, 105] °C
    - 'index' is the TCC/box device slot (0-based or 1-based per your setup)
  """

  # Capability flags (align with your ecosystem conventions if present)
  @property
  def supports_active_cooling(self) -> bool:
    return True

  @property
  def supports_locking(self) -> bool:
    return False

  def __init__(self, index: int, interface) -> None:
    assert index >= 0, "Device index must be non-negative"
    self.index = index
    self.interface = interface

  # ---------- Lifecycle ----------

  async def setup(self):
    """Verify HHC is present and initialize if needed."""
    await self._check_type_is_hhc()
    await self._initialize_if_needed()

  async def stop(self):
    """No persistent state to tear down on the device."""
    pass

  def serialize(self) -> dict:
    warnings.warn("The interface is not serialized.")
    return {
      "type": "HamiltonHeaterCoolerBackend",
      "index": self.index,
      "interface": None,  # TODO: implement serialization if needed
    }

  # ---------- Public API (HHC feature set) ----------

  async def check_type_is_hhc(self):
    """Compatibility wrapper with the feature list signature."""
    await self._check_type_is_hhc()

  async def initialize_hhc(self) -> str:
    """Compatibility wrapper; initializes if needed and returns a short info string."""
    return await self._initialize_if_needed()

  async def start_temperature_control_at_hhc(
    self,
    temp: Union[float, int, str],
  ):
    """Start temperature regulation."""
    return await self.set_temperature(temp)

  async def get_temperature_at_hhc(self) -> Dict[str, float]:
    """Return current temperatures from both sensors."""
    temps = await self._get_current_temperatures()
    return {"middle_T": temps["middle"], "edge_T": temps["edge"]}

  async def query_whether_temperature_reached_at_hhc(self) -> bool:
    """Return True if target temperature reached, per 'QD' == 0 convention."""
    return await self.temperature_reached()

  async def stop_temperature_control_at_hhc(self):
    """Stop temperature regulation (turn heating/cooling off)."""
    return await self.deactivate()

  # ---------- High-level helpers ----------

  async def set_temperature(self, temperature: Union[float, int, str]):
    """Set target temperature in °C (0 < T <= 105)."""
    await self._check_type_is_hhc()
    temp_str = self._format_temp(temperature)
    # tb/tc are included per your reference; semantics TBD
    return await self._send("TA", ta=temp_str, tb="1800", tc="0020")

  async def deactivate(self):
    """Turn off temperature regulation."""
    await self._check_type_is_hhc()
    return await self._send("TO")

  async def get_current_temperature(self) -> float:
    """Return middle sensor temperature in °C."""
    temps = await self._get_current_temperatures()
    return temps["middle"]

  async def get_edge_temperature(self) -> float:
    """Return edge sensor temperature in °C."""
    temps = await self._get_current_temperatures()
    return temps["edge"]

  async def temperature_reached(self) -> bool:
    """Check if the device reports that setpoint is reached (QD == 0)."""
    await self._check_type_is_hhc()
    resp = await self._send("QD")
    # Typical responses contain 'qd0' or 'qd1' or 'qd=0' patterns
    code = self._extract_flag(resp, key="qd", default=None)
    if code is None:
      # Fallback: presence-based heuristic
      return "qd0" in resp
    return code == 0

  # ---------- Private helpers ----------

  async def _check_type_is_hhc(self):
    """Query firmware and validate that this device is an HHC."""
    fw = await self._send("RF")
    if "Hamilton Heater Cooler" not in fw:
      raise ValueError(
        f"Device index {self.index} is not a Hamilton Heater Cooler. "
        f"Reported: {fw!r}. Check your device index and connections."
      )

  async def _initialize_if_needed(self) -> str:
    """Run QU (probe), QW (init state), LI (init) if not initialized."""
    try:
      await self._send("QU")
    except TimeoutError as exc:
      raise ValueError(
        f"No Hamilton Heater Cooler found at index {self.index}. "
        f"Verify wiring/USB and device address. Original error: {exc}"
      ) from exc

    await self._check_type_is_hhc()

    # Query init status; expect something like 'qw1' or key/value pairs
    qw_resp = await self._send("QW")
    init_flag = self._extract_flag(qw_resp, key="qw", default=None)

    if init_flag == 1:
      return "HHC already initialized"

    # If unknown or not initialized, attempt initialize
    await self._send("LI")
    return f"HHC at index {self.index} initialized."

  async def _get_current_temperatures(self) -> Dict[str, float]:
    """Parse middle/edge temps from RT reply (tenths of °C -> °C)."""
    await self._check_type_is_hhc()
    resp = await self._send("RT")

    # Try a few formats robustly:
    # 1) key-value like 'rt+0423 +0410'
    # 2) trailing '+#### +####'
    # 3) tokens with 'rt' then two signed tenths
    # Prefer the last two +#### groups in the response.
    plus_groups = re.findall(r"\+?(-?\d{3,4})", resp)
    if len(plus_groups) >= 2:
      v1, v2 = plus_groups[-2], plus_groups[-1]
      middle = int(v1) / 10.0
      edge = int(v2) / 10.0
      return {"middle": middle, "edge": edge}

    # Fallback: try to split after 'rt'
    if "rt" in resp:
      tail = resp.split("rt", 1)[1]
      nums = re.findall(r"[-+]?\d{3,4}", tail)
      if len(nums) >= 2:
        middle = int(nums[0]) / 10.0
        edge = int(nums[1]) / 10.0
        return {"middle": middle, "edge": edge}

    raise ValueError(f"Unable to parse temperatures from RT response: {resp!r}")

  def _format_temp(self, t: Union[float, int, str]) -> str:
    """Convert °C to firmware units (tenths), 4 digits, with sanity checks."""
    if isinstance(t, (float, int)):
      assert 0 < float(t) <= 105, "Temperature must be 0 < T <= 105 °C"
      return f"{round(float(t) * 10):04d}"
    # String path: trust caller but still try to sanity-check if numeric
    try:
      tv = float(t)
      assert 0 < tv <= 105, "Temperature must be 0 < T <= 105 °C"
    except Exception:
      pass
    return str(t)

  async def _send(self, command: str, **kwargs) -> str:
    """Convenience wrapper for the shared interface."""
    return await self.interface.send_hhs_command(index=self.index, command=command, **kwargs)

  @staticmethod
  def _extract_flag(text: str, key: str, default: Optional[int]) -> Optional[int]:
    """Find patterns like 'key1', 'key=1', 'key:1', 'key01' and return int(value)."""
    # Common shapes: 'qw1', 'qw=1', 'qw:1', 'qw 1', 'qw01'
    m = re.search(rf"\b{re.escape(key)}\s*[:=\s]?\s*(-?\d+)\b", text)
    if m:
      return int(m.group(1))
    # Also accept compact form like 'qw0' within tokens
    m2 = re.search(rf"\b{re.escape(key)}\s*(-?\d+)\b", text)
    if m2:
      return int(m2.group(1))
    m3 = re.search(rf"{re.escape(key)}(-?\d+)", text)
    if m3:
      return int(m3.group(1))
    return default
