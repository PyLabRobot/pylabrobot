from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Dict, Mapping, Optional, Protocol, TypedDict, runtime_checkable

from pylabrobot.capabilities.electroporation.standard import ElectroporationProtocol
from pylabrobot.io.binary import Reader, Writer
from pylabrobot.io.serial import Serial

try:
  import serial.tools.list_ports

  _HAS_LIST_PORTS = True
except ImportError:
  _HAS_LIST_PORTS = False


@runtime_checkable
class _SerialLike(Protocol):
  async def setup(self) -> None:
    pass

  async def stop(self) -> None:
    pass

  async def write(self, data: bytes) -> None:
    pass

  async def read(self, num_bytes: int = 1) -> bytes:
    pass

  async def readline(self) -> bytes:
    pass


class _ProgramEntry(TypedDict):
  name: str
  size: int


class FileTransferControl:
  """Protocol Manager style USB-serial control for the BTX Gemini X2.

  This control owns the PM shell path only: stored user protocols, SD-card access,
  log retrieval, and device metadata. It does not drive the RSI touchscreen workflow.
  """

  USB_VID = 0x1FE9
  USB_PID = 0x5101
  SUPPORTED_USB_IDS = {
    (0x1FE9, 0x5101),
    (0x1FE9, 0x5201),
  }

  METHOD_PAYLOAD_BYTES = 104
  METHOD_NAME_BYTES = 28
  UI_PROTOCOL_NAME_BYTES = 15
  METHOD_PROTOCOL_TYPES = {"exponential": 0, "square": 1}
  FIELD_TRAILING_RESERVED_BYTES = METHOD_PAYLOAD_BYTES - 76

  def __init__(
    self,
    port: Optional[str] = None,
    vid: int = USB_VID,
    pid: int = USB_PID,
    baudrate: int = 9600,
    timeout: float = 1.0,
    write_timeout: float = 1.0,
    supported_usb_ids: Optional[set[tuple[int, int]]] = None,
    serial_io: Optional[_SerialLike] = None,
  ) -> None:
    self._serial: Optional[_SerialLike] = serial_io
    self._serial_io_injected = serial_io is not None
    self._port = port
    self._vid = vid
    self._pid = pid
    self._baudrate = baudrate
    self._timeout = timeout
    self._write_timeout = write_timeout
    self._supported_usb_ids = (
      set(supported_usb_ids)
      if supported_usb_ids is not None
      else set(self.SUPPORTED_USB_IDS) | {(vid, pid)}
    )

  @property
  def port(self) -> Optional[str]:
    return self._port

  async def setup(self) -> None:
    """Open the Gemini USB-serial port, autodiscovering it when needed."""
    if not self._serial_io_injected:
      if self._port is None:
        self._port = self._resolve_port()
      self._serial = Serial(
        human_readable_device_name="BTX Gemini X2 FileTransferControl",
        port=self._port,
        baudrate=self._baudrate,
        timeout=self._timeout,
        write_timeout=self._write_timeout,
      )

    serial_dev = self._require_serial()
    await serial_dev.setup()
    resolved_port = getattr(serial_dev, "port", None)
    if isinstance(resolved_port, str):
      self._port = resolved_port

  async def stop(self) -> None:
    """Close the Gemini USB-serial port."""
    await self._require_serial().stop()

  async def list_protocols_with_size(self) -> list[_ProgramEntry]:
    """List user protocols currently stored on the Gemini."""
    isprog_response = await self.send_text_command("isprog")
    isprog_error = self._extract_error(isprog_response)
    if isprog_error is not None and "unknown command" not in isprog_response.lower():
      self._require_no_error(isprog_response, "isprog")

    response = await self.send_text_command('cat "*.BTX"')
    self._require_no_error(response, 'cat "*.BTX"')
    return self._parse_program_table(response)

  async def list_protocols(self) -> list[str]:
    """Return only the stored Gemini user protocol names."""
    return [row["name"] for row in await self.list_protocols_with_size()]

  async def get_protocol(self, protocol_name: str) -> Dict[str, Any]:
    """Fetch and decode a stored protocol payload by name."""
    name = self._sanitize_protocol_name(protocol_name)
    command = f'sendmtd "{name}"'
    response = await self.send_text_command(command)
    self._require_no_error(response, command)

    payload_hex, payload = self._extract_method_payload(response)
    decoded = self._decode_method_payload(payload)
    return self._operation_result(
      "get_protocol",
      name,
      payload_hex=payload_hex,
      payload_bytes=len(payload),
      decoded=decoded,
      response=response,
    )

  async def add_protocol(
    self,
    protocol_name: str,
    protocol: ElectroporationProtocol | Mapping[str, Any],
    overwrite: bool = False,
  ) -> Dict[str, Any]:
    """Transfer a new user protocol to the Gemini over the PM serial interface."""
    name = self._sanitize_new_protocol_name(protocol_name)
    existing = await self.list_protocols()
    exists_before = name in existing

    if exists_before and not overwrite:
      raise FileExistsError(f'Protocol "{name}" already exists. Use overwrite=True to replace it.')
    if exists_before and overwrite:
      await self.delete_protocol(name)

    payload = self._build_method_payload(name, protocol)
    payload_hex = payload.hex().upper()

    meth_command = f"meth {payload_hex}"
    meth_response = await self.send_text_command(meth_command)
    self._require_no_error(meth_response, meth_command)

    mend_response = await self.send_text_command("mend")
    self._require_no_error(mend_response, "mend")

    exists_after = name in await self.list_protocols()
    if not exists_after:
      raise RuntimeError(f'Protocol "{name}" was not visible after transfer.')

    decoded = self._decode_method_payload(payload)
    return self._operation_result(
      "add_protocol",
      name,
      overwrite=overwrite,
      exists_before=exists_before,
      exists_after=exists_after,
      payload_hex=payload_hex,
      decoded=decoded,
      responses={"meth": meth_response, "mend": mend_response},
    )

  async def delete_protocol(self, protocol_name: str, missing_ok: bool = False) -> Dict[str, Any]:
    """Delete a stored user protocol from the Gemini."""
    name = self._sanitize_protocol_name(protocol_name)
    exists_before = name in await self.list_protocols()

    if not exists_before:
      if not missing_ok:
        raise FileNotFoundError(f'Protocol "{name}" is not present on the device.')
      return self._operation_result(
        "delete_protocol",
        name,
        deleted=False,
        exists_before=False,
        exists_after=False,
      )

    command = f'delm "{name}"'
    response = ""
    for _ in range(8):
      response = await self.send_text_command(command)
      self._require_no_error(response, command)
      if name not in await self.list_protocols():
        break

    exists_after = name in await self.list_protocols()
    if exists_after:
      raise RuntimeError(f'Protocol "{name}" still exists after repeated delete attempts.')

    return self._operation_result(
      "delete_protocol",
      name,
      deleted=True,
      exists_before=True,
      exists_after=False,
      response=response,
    )

  async def list_sd_dir(self, sd_path: str) -> list[str]:
    """List entries in an SD-card directory path."""
    normalized = self._normalize_sd_path(sd_path)
    command = f"sddir {normalized}"
    response = await self.send_text_command(command)
    self._require_no_error(response, command)
    return self._parse_sd_dir_listing(response, command)

  async def fetch_sd_file(self, sd_path: str) -> str:
    """Read a text file from the Gemini SD card."""
    normalized = self._normalize_sd_path(sd_path)
    command = f"sdsend {normalized}"
    response = await self.send_text_command(command)
    self._require_no_error(response, command)
    return self._strip_sd_file_response(response, command)

  async def list_log_files(self, root: str = "\\BTXDATA") -> list[str]:
    """Recursively enumerate BTX run log files under ``BTXDATA``."""
    normalized_root = self._normalize_sd_path(root)
    log_paths: list[str] = []

    for month in await self.list_sd_dir(normalized_root):
      if not re.fullmatch(r"\d{4}-\d{2}", month):
        continue
      month_path = self._join_sd_path(normalized_root, month)
      for day in await self.list_sd_dir(month_path):
        if not re.fullmatch(r"\d{6}", day):
          continue
        day_path = self._join_sd_path(month_path, day)
        for entry in await self.list_sd_dir(day_path):
          if re.fullmatch(r"[^\\/:*?\"<>|]+\.(TXT|txt)", entry):
            log_paths.append(self._join_sd_path(day_path, entry))

    log_paths.sort()
    return log_paths

  async def get_version(self) -> str:
    """Return the Gemini software version string."""
    return await self._read_single_value_command("version")

  async def get_serial_number(self) -> str:
    """Return the Gemini serial number."""
    return await self._read_single_value_command("sn")

  async def get_device_time(self) -> str:
    """Return the current date/time reported by the Gemini."""
    return await self._read_single_value_command("time")

  async def get_comm_stats(self) -> Dict[str, int]:
    """Return the device communication counters from ``status``/``stat``."""
    response = await self.send_text_command("status")
    error = self._extract_error(response)
    if error is not None and "unknown command" in response.lower():
      response = await self.send_text_command("stat")
    self._require_no_error(response, "status/stat")

    stats: Dict[str, int] = {}
    for line in self._response_lines(response):
      if ":" not in line:
        continue
      key, value = line.split(":", maxsplit=1)
      key = key.strip()
      value = value.strip()
      if key in {"status", "stat"}:
        continue
      if value.isdigit():
        stats[key] = int(value)
    return stats

  def parse_run_log(self, text: str) -> Dict[str, Any]:
    """Parse a BTX run log into the small summary used by the Gemini backend."""
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    fields = self._parse_log_fields(cleaned)

    date_text = self._field_text(fields, "date")
    time_text = self._field_text(fields, "time")
    date_time = self._field_text(fields, "date_time")
    if date_time is None and date_text is not None and time_text is not None:
      date_time = f"{date_text} {time_text}"

    summary = {
      "date_time": date_time,
      "protocol_name": self._field_text(fields, "protocol_name"),
      "protocol_type": self._field_text(fields, "protocol_type"),
      "pulse_amplitude_volts": self._field_number(fields, "pulse_amplitude", cast_type=int),
      "plate_columns": self._field_number(fields, "plate_columns", cast_type=int),
      "pulse_1_voltage_volts": self._field_number(fields, "pulse_1_voltage", cast_type=float),
      "pulse_1_time_constant_us": self._field_number(
        fields, "pulse_1_time_constant", cast_type=int
      ),
      "pulse_1_total_load_ohms": self._field_number(fields, "pulse_1_total_load", cast_type=int),
      "protocol_result": self._field_text(fields, "protocol_result"),
      "status_code": self._field_hex(fields, "status") or self._field_hex(fields, "status_code"),
      "status_message": self._field_text(fields, "status_message")
      or self._field_suffix(fields, "status", separator="-"),
    }
    return {"summary": summary, "text": text}

  async def write_raw(self, data: bytes) -> None:
    """Write raw bytes to the Gemini serial interface."""
    await self._require_serial().write(data)

  async def read_raw(self, num_bytes: int = 1) -> bytes:
    """Read raw bytes from the Gemini serial interface."""
    return await self._require_serial().read(num_bytes=num_bytes)

  async def readline_raw(self) -> bytes:
    """Read one raw line from the Gemini serial interface."""
    return await self._require_serial().readline()

  async def send_text_command(self, command: str) -> str:
    """Send one PM shell command and return the prompt-terminated response text."""
    await self.write_raw((command + "\r\n").encode("utf-8"))
    response = await self._read_until_prompt()
    return response.decode("utf-8", errors="replace")

  def _require_serial(self) -> _SerialLike:
    if self._serial is None:
      raise RuntimeError("Serial device not initialized. Call setup() first.")
    return self._serial

  def _operation_result(self, operation: str, protocol_name: str, **details: Any) -> Dict[str, Any]:
    return {
      "operation": operation,
      "timestamp_utc": self._now_utc_iso(),
      "protocol": protocol_name,
      **details,
    }

  def _resolve_port(self) -> str:
    if not _HAS_LIST_PORTS:
      raise RuntimeError(
        "pyserial is required for BTX port autodiscovery. Install with: pip install pylabrobot[btx]"
      )

    ports = serial.tools.list_ports.comports()
    btx_ports = [p for p in ports if (p.vid, p.pid) in self._supported_usb_ids]
    if len(btx_ports) == 0:
      raise RuntimeError(
        "No BTX Gemini found with supported VID:PID pairs: "
        f"{sorted(self._supported_usb_ids)}. "
        "If connected, provide the serial port explicitly (e.g., /dev/cu.usbmodem...)."
      )
    if len(btx_ports) > 1:
      available_ports = [f"{p.device} ({hex(p.vid)}:{hex(p.pid)})" for p in btx_ports]
      raise RuntimeError(
        f"Multiple BTX Gemini devices found: {available_ports}. Please specify the port explicitly."
      )

    detected = btx_ports[0]
    if detected.vid is not None:
      self._vid = detected.vid
    if detected.pid is not None:
      self._pid = detected.pid
    return str(detected.device)

  async def _read_single_value_command(self, command: str) -> str:
    response = await self.send_text_command(command)
    self._require_no_error(response, command)
    lines = [line for line in self._response_lines(response) if line not in {command, ":"}]
    return lines[0] if len(lines) > 0 else ""

  async def _read_until_prompt(self, read_size: int = 512, max_reads: int = 24) -> bytes:
    chunks: list[bytes] = []
    got_any = False
    for _ in range(max_reads):
      chunk = await self.read_raw(num_bytes=read_size)
      if len(chunk) == 0:
        if got_any:
          break
        await asyncio.sleep(0.05)
        continue
      got_any = True
      chunks.append(chunk)
      if chunk.endswith(b":"):
        break
      await asyncio.sleep(0.03)
    return b"".join(chunks)

  def _response_lines(self, response: str) -> list[str]:
    return [line.strip() for line in response.splitlines()]

  def _extract_error(self, response: str) -> Optional[str]:
    for line in self._response_lines(response):
      line_l = line.lower()
      if line_l.startswith("command error:"):
        return line
      if line_l.startswith("error:"):
        return line
      if line_l in {"argument error", "delete failed", "get method failed"}:
        return line
      if line_l.startswith("failed:"):
        continue
      if "failed" in line_l and "successful" not in line_l:
        return line
    return None

  def _require_no_error(self, response: str, command: str) -> None:
    error = self._extract_error(response)
    if error is not None:
      raise RuntimeError(f"BTX command failed ({command}): {error}")

  def _parse_program_table(self, response: str) -> list[_ProgramEntry]:
    programs: list[_ProgramEntry] = []
    for line in self._response_lines(response):
      if (
        line == ""
        or line == ":"
        or line == "isprog"
        or line.startswith('cat "*.BTX"')
        or line.startswith("Method name")
        or line.startswith("----")
      ):
        continue
      if "file(s) using" in line:
        break
      if line.startswith("Error:"):
        raise RuntimeError(line)

      parts = line.split()
      if len(parts) >= 2 and parts[-1].isdigit():
        programs.append({"name": " ".join(parts[:-1]), "size": int(parts[-1])})
      elif len(parts) >= 1:
        programs.append({"name": parts[0], "size": 0})
    return programs

  def _normalize_sd_path(self, sd_path: str) -> str:
    path = sd_path.strip().replace("/", "\\")
    if not path.startswith("\\"):
      path = "\\" + path
    path = re.sub(r"\\+", r"\\", path)
    return path.rstrip("\\") or "\\"

  def _join_sd_path(self, *parts: str) -> str:
    cleaned = [part.strip().strip("\\/") for part in parts if part.strip()]
    if len(cleaned) == 0:
      return "\\"
    return "\\" + "\\".join(cleaned)

  def _parse_sd_dir_listing(self, response: str, command: str) -> list[str]:
    return [line for line in self._response_lines(response) if line not in {"", ":", command}]

  def _strip_sd_file_response(self, response: str, command: str) -> str:
    lines = response.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if len(lines) > 0 and lines[0].strip() == command:
      lines = lines[1:]
    while len(lines) > 0 and lines[-1].strip() == "":
      lines.pop()
    if len(lines) > 0 and lines[-1].strip() == ":":
      lines.pop()
    return "\n".join(lines).strip("\n")

  def _parse_log_fields(self, cleaned: str) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}
    current_block: list[str] = []

    for line in cleaned.splitlines():
      stripped = line.rstrip()
      if stripped:
        current_block.append(stripped)
        continue
      if len(current_block) > 0:
        self._parse_tabular_log_block(current_block, fields)
        current_block = []
    if len(current_block) > 0:
      self._parse_tabular_log_block(current_block, fields)

    for line in [line.strip() for line in cleaned.splitlines() if line.strip()]:
      if "\t" in line:
        continue
      match = re.match(r"^([^:]+):\s*(.+)$", line)
      if match is not None:
        self._store_log_field(fields, match.group(1), match.group(2).strip())

    return fields

  def _normalize_log_key(self, key: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    return {
      "date_mm_dd_yyyy": "date",
      "time_hhmmss": "time",
      "pulse_amplitude_v": "pulse_amplitude",
      "pulse_1_voltage_v": "pulse_1_voltage",
      "pulse_1_voltage": "pulse_1_voltage",
      "pulse_1_time_constant_us": "pulse_1_time_constant",
      "pulse_1_time_constant": "pulse_1_time_constant",
      "pulse_1_total_load_ohms": "pulse_1_total_load",
      "pulse_1_total_load": "pulse_1_total_load",
    }.get(normalized, normalized)

  def _store_log_field(self, fields: Dict[str, Any], key: str, value: str) -> None:
    normalized_key = self._normalize_log_key(key)
    existing = fields.get(normalized_key)
    if existing is None:
      fields[normalized_key] = value
    elif isinstance(existing, list):
      existing.append(value)
    else:
      fields[normalized_key] = [existing, value]

  # BTX emits both verbose "Key: Value" logs and tabular exports; this block parser keeps a
  # single normalized summary shape for both.
  def _parse_tabular_log_block(self, block: list[str], fields: Dict[str, Any]) -> None:
    if len(block) == 0:
      return

    idx = 0
    while idx < len(block):
      line = block[idx]
      if idx + 1 < len(block) and "\t" in line and "\t" in block[idx + 1] and ":" not in line:
        headers = [token.strip() for token in line.split("\t") if token.strip()]
        values = [token.strip() for token in block[idx + 1].split("\t") if token.strip()]
        self._store_tabular_header_rows(fields, headers, values)
        idx += 2
        continue

      if "\t" not in line or ":" not in line:
        idx += 1
        continue

      tokens = [token.strip() for token in line.split("\t") if token.strip()]
      self._store_tabular_inline_pairs(fields, tokens)
      idx += 1

  def _store_tabular_header_rows(
    self,
    fields: Dict[str, Any],
    headers: list[str],
    values: list[str],
  ) -> None:
    if len(headers) == 0 or len(values) == 0:
      return

    if headers[0] == "DC Pulses" and values[0].lower().startswith("pulse "):
      pulse_label = values[0]
      for header, value in zip(headers[1:], values[1:]):
        self._store_log_field(fields, f"{pulse_label} {header}", value)
      return

    for header, value in zip(headers, values):
      self._store_log_field(fields, header, value)

    if headers[:2] == ["Protocol Result", "Status Code"] and len(values) > 2:
      self._store_log_field(fields, "Status Message", " ".join(values[2:]))

  def _store_tabular_inline_pairs(self, fields: Dict[str, Any], tokens: list[str]) -> None:
    if len(tokens) < 2:
      return

    token_idx = 1 if tokens[0].endswith(":") and len(tokens) >= 3 else 0
    while token_idx + 1 < len(tokens):
      key = tokens[token_idx]
      value = tokens[token_idx + 1]
      if not key.endswith(":") or value.endswith(":"):
        token_idx += 1
        continue
      self._store_log_field(fields, key[:-1], value)
      token_idx += 2

  def _field_text(self, fields: Mapping[str, Any], key: str) -> Optional[str]:
    value = fields.get(key)
    if isinstance(value, list):
      return str(value[-1]) if len(value) > 0 else None
    if value is None:
      return None
    return str(value)

  def _field_number(
    self,
    fields: Mapping[str, Any],
    key: str,
    cast_type: type[int] | type[float],
  ) -> Optional[int | float]:
    value = self._field_text(fields, key)
    if value is None:
      return None
    pattern = r"-?\d+" if cast_type is int else r"-?\d+(?:\.\d+)?"
    match = re.search(pattern, value)
    if match is None:
      return None
    return cast_type(match.group(0))

  def _field_hex(self, fields: Mapping[str, Any], key: str) -> Optional[str]:
    value = self._field_text(fields, key)
    if value is None:
      return None
    match = re.search(r"0x[0-9A-Fa-f.]+", value)
    if match is None:
      return None
    return match.group(0)

  def _field_suffix(self, fields: Mapping[str, Any], key: str, separator: str) -> Optional[str]:
    value = self._field_text(fields, key)
    if value is None or separator not in value:
      return None
    return value.split(separator, maxsplit=1)[1].strip()

  def _sanitize_protocol_name(self, protocol_name: str) -> str:
    name = protocol_name.strip()
    if len(name) == 0:
      raise ValueError("Protocol name cannot be empty.")
    if '"' in name or "\n" in name or "\r" in name:
      raise ValueError("Protocol name cannot contain quotes or newlines.")
    try:
      encoded = name.encode("ascii")
    except UnicodeEncodeError as exc:
      raise ValueError("Protocol name must be ASCII.") from exc
    if len(encoded) > self.METHOD_NAME_BYTES:
      raise ValueError(
        f"Protocol name must be <= {self.METHOD_NAME_BYTES} ASCII bytes, got {len(encoded)}."
      )
    return name

  def _sanitize_new_protocol_name(self, protocol_name: str) -> str:
    name = self._sanitize_protocol_name(protocol_name)
    encoded = name.encode("ascii")
    if len(encoded) > self.UI_PROTOCOL_NAME_BYTES:
      raise ValueError(
        "New protocol names must be <= "
        f"{self.UI_PROTOCOL_NAME_BYTES} ASCII bytes for Gemini UI compatibility, "
        f"got {len(encoded)}."
      )
    return name

  def _encode_protocol_name(self, protocol_name: str) -> bytes:
    return protocol_name.encode("ascii").ljust(self.METHOD_NAME_BYTES, b"\x00")

  def _protocol_parameters(
    self,
    protocol: ElectroporationProtocol | Mapping[str, Any],
  ) -> Mapping[str, Any]:
    if isinstance(protocol, ElectroporationProtocol):
      return protocol.as_parameters()
    return protocol

  def _parameter_value(self, parameters: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
      value = parameters.get(key)
      if value is not None:
        return value
    return None

  def _coerce_int_parameter(self, parameters: Mapping[str, Any], *keys: str) -> Optional[int]:
    value = self._parameter_value(parameters, *keys)
    if value is None:
      return None
    if isinstance(value, bool):
      raise ValueError(f"Parameter {keys[0]} must be numeric, not bool.")
    if isinstance(value, float):
      if not value.is_integer():
        raise ValueError(f"Parameter {keys[0]} must be an integer value, got {value}.")
      return int(value)
    return int(value)

  def _coerce_float_parameter(self, parameters: Mapping[str, Any], *keys: str) -> Optional[float]:
    value = self._parameter_value(parameters, *keys)
    if value is None:
      return None
    if isinstance(value, bool):
      raise ValueError(f"Parameter {keys[0]} must be numeric, not bool.")
    result = float(value)
    if not isfinite(result):
      raise ValueError(f"Parameter {keys[0]} must be finite, got {value}.")
    return result

  def _normalize_protocol_parameters(
    self,
    protocol: ElectroporationProtocol | Mapping[str, Any],
  ) -> Dict[str, Any]:
    parameters = self._protocol_parameters(protocol)
    common = self._normalize_common_protocol_parameters(parameters)
    if common["protocol_type"] == "square":
      return self._normalize_square_protocol(parameters, common)
    return self._normalize_exponential_protocol(parameters, common)

  def _normalize_common_protocol_parameters(self, parameters: Mapping[str, Any]) -> Dict[str, Any]:
    protocol_type = str(parameters.get("protocol_type", "exponential")).lower()
    if protocol_type not in self.METHOD_PROTOCOL_TYPES:
      allowed = ", ".join(sorted(self.METHOD_PROTOCOL_TYPES))
      raise ValueError(f"Unsupported protocol_type={protocol_type!r}. Allowed: {allowed}.")

    amplitude_volts = self._coerce_int_parameter(parameters, "pulse_amplitude_volts", "voltage")
    if amplitude_volts is None:
      raise ValueError("Missing pulse amplitude. Use pulse_amplitude_volts (or voltage).")
    self._validate_amplitude_volts(amplitude_volts)

    pulse_count = self._coerce_int_parameter(parameters, "pulse_count") or 1
    pulse_interval_seconds = self._coerce_float_parameter(
      parameters, "pulse_interval_seconds", "pulse_interval_sec", "interval_seconds"
    )
    if pulse_interval_seconds is None:
      pulse_interval_seconds = 0.0

    gap_mm = self._coerce_float_parameter(parameters, "gap_mm", "electrode_gap_mm", "electrode_gap")
    if gap_mm is None:
      raise ValueError("Missing electrode gap. Use gap_mm (or electrode_gap_mm).")
    self._validate_gap_mm(gap_mm)

    return {
      "protocol_type": protocol_type,
      "pulse_amplitude_volts": amplitude_volts,
      "pulse_count": pulse_count,
      "pulse_interval_seconds": pulse_interval_seconds,
      "electrode_gap_mm": gap_mm,
      "pulse_interval_ms": 0,
    }

  def _normalize_square_protocol(
    self,
    parameters: Mapping[str, Any],
    common: Dict[str, Any],
  ) -> Dict[str, Any]:
    amplitude_volts = common["pulse_amplitude_volts"]
    pulse_count = common["pulse_count"]
    pulse_interval_seconds = common["pulse_interval_seconds"]

    self._validate_square_pulse_count(amplitude_volts, pulse_count)
    self._validate_square_pulse_interval_seconds(pulse_count, pulse_interval_seconds)

    duration_us = self._coerce_int_parameter(parameters, "duration_us", "pulse_duration_us")
    if duration_us is None:
      raise ValueError("Square protocols require duration_us (or pulse_duration_us).")
    self._validate_square_duration_us(amplitude_volts, duration_us)

    return {
      **common,
      "pulse_duration_us": duration_us,
      "pulse_interval_ms": int(round(pulse_interval_seconds * 1000)),
    }

  def _normalize_exponential_protocol(
    self,
    parameters: Mapping[str, Any],
    common: Dict[str, Any],
  ) -> Dict[str, Any]:
    pulse_count = common["pulse_count"]
    pulse_interval_seconds = common["pulse_interval_seconds"]
    if pulse_count != 1 or abs(pulse_interval_seconds) > 1e-9:
      raise ValueError(
        "Exponential protocols currently support only pulse_count=1 in this backend. "
        "The Gemini X2 manual mentions up to 2 pulses depending on amplitude limit, "
        "but the PM payload/current-limit behavior is not documented well enough to "
        "support that safely. Use pulse_count=1 and omit pulse_interval_seconds."
      )

    amplitude_volts = common["pulse_amplitude_volts"]
    resistance_ohms = self._coerce_int_parameter(parameters, "resistance_ohms", "resistance")
    if resistance_ohms is None:
      raise ValueError("Exponential protocols require resistance_ohms.")
    self._validate_exponential_resistance_ohms(amplitude_volts, resistance_ohms)

    capacitance_uf = self._coerce_int_parameter(parameters, "capacitance_uf", "capacitance")
    if capacitance_uf is None:
      raise ValueError("Exponential protocols require capacitance_uf.")
    self._validate_exponential_capacitance_uf(amplitude_volts, capacitance_uf)

    return {
      **common,
      "resistance_ohms": resistance_ohms,
      "capacitance_uf": capacitance_uf,
    }

  def _validate_amplitude_volts(self, amplitude_volts: int) -> None:
    if 5 <= amplitude_volts <= 500:
      return
    if 505 <= amplitude_volts <= 3000 and (amplitude_volts % 5) == 0:
      return
    raise ValueError(
      "pulse_amplitude_volts must be 5..500 in 1 V steps or 505..3000 in 5 V steps, "
      f"got {amplitude_volts}."
    )

  def _validate_gap_mm(self, gap_mm: float) -> None:
    if gap_mm <= 0:
      raise ValueError(f"gap_mm must be > 0, got {gap_mm}.")

  def _validate_square_duration_us(self, amplitude_volts: int, duration_us: int) -> None:
    if duration_us <= 0:
      raise ValueError(f"duration_us must be > 0, got {duration_us}.")
    if amplitude_volts <= 500:
      if 10 <= duration_us <= 999:
        return
      if 1000 <= duration_us <= 999_000 and (duration_us % 1000) == 0:
        return
      raise ValueError(
        "Square-wave LV duration must be 10..999 us or 1..999 ms in 1 ms steps; "
        f"got {duration_us} us."
      )
    if 10 <= duration_us <= 600:
      return
    raise ValueError(
      f"Square-wave HV duration must be 10..600 us in 1 us steps; got {duration_us} us."
    )

  def _validate_square_pulse_count(self, amplitude_volts: int, pulse_count: int) -> None:
    max_pulses = 10 if amplitude_volts <= 500 else 3
    if 1 <= pulse_count <= max_pulses:
      return
    raise ValueError(
      f"Square-wave pulse_count must be 1..{max_pulses} at {amplitude_volts} V, got {pulse_count}."
    )

  def _validate_square_pulse_interval_seconds(
    self,
    pulse_count: int,
    pulse_interval_seconds: float,
  ) -> None:
    if pulse_count == 1:
      if abs(pulse_interval_seconds) <= 1e-9:
        return
      raise ValueError(
        "Square-wave pulse_interval_seconds must be 0 or omitted when pulse_count=1, "
        f"got {pulse_interval_seconds}."
      )
    if not 0.1 <= pulse_interval_seconds <= 10.0:
      raise ValueError(
        "Square-wave pulse_interval_seconds must be 0.1..10.0 s for multiple pulsing, "
        f"got {pulse_interval_seconds}."
      )
    step_value = round(pulse_interval_seconds * 10)
    if abs((step_value / 10.0) - pulse_interval_seconds) > 1e-9:
      raise ValueError(
        f"Square-wave pulse_interval_seconds must use 0.1 s steps, got {pulse_interval_seconds}."
      )

  def _validate_exponential_resistance_ohms(
    self,
    amplitude_volts: int,
    resistance_ohms: int,
  ) -> None:
    min_resistance = 25 if amplitude_volts <= 500 else 50
    if resistance_ohms < min_resistance or resistance_ohms > 1575 or (resistance_ohms % 25) != 0:
      raise ValueError(
        "Exponential resistance_ohms must be "
        f"{min_resistance}..1575 in 25 ohm steps at {amplitude_volts} V, "
        f"got {resistance_ohms}."
      )

  def _validate_exponential_capacitance_uf(self, amplitude_volts: int, capacitance_uf: int) -> None:
    if amplitude_volts <= 500:
      if 25 <= capacitance_uf <= 3275 and (capacitance_uf % 25) == 0:
        return
      raise ValueError(
        f"Exponential LV capacitance_uf must be 25..3275 in 25 uF steps; got {capacitance_uf}."
      )
    if capacitance_uf in {10, 25, 35, 50, 60, 75, 85}:
      return
    raise ValueError(
      "Exponential HV capacitance_uf must be one of {10, 25, 35, 50, 60, 75, 85}; "
      f"got {capacitance_uf}."
    )

  def _build_method_payload(
    self,
    protocol_name: str,
    protocol: ElectroporationProtocol | Mapping[str, Any],
  ) -> bytes:
    name = self._sanitize_new_protocol_name(protocol_name)
    normalized = self._normalize_protocol_parameters(protocol)

    protocol_type_code = self._require_u32(
      self.METHOD_PROTOCOL_TYPES[normalized["protocol_type"]],
      "protocol_type_code",
    )
    pulse_amplitude_volts = self._require_u32(
      normalized["pulse_amplitude_volts"],
      "pulse_amplitude_volts",
    )
    pulse_count = self._require_u32(normalized["pulse_count"], "pulse_count")
    pulse_interval_ms = self._require_u32(normalized["pulse_interval_ms"], "pulse_interval_ms")
    electrode_gap_mm = self._require_f32(normalized["electrode_gap_mm"], "electrode_gap_mm")
    square_duration = 0
    resistance = 0
    capacitance = 0

    if normalized["protocol_type"] == "square":
      square_duration = self._require_u32(normalized["pulse_duration_us"], "pulse_duration_us")
    else:
      resistance = self._require_u32(normalized["resistance_ohms"], "resistance_ohms")
      capacitance = self._require_u32(normalized["capacitance_uf"], "capacitance_uf")

    writer = Writer()
    writer.u32(1)
    writer.raw_bytes(self._encode_protocol_name(name))
    writer.u32(protocol_type_code)
    writer.u32(0)
    writer.u32(pulse_amplitude_volts)
    writer.u32(0)
    writer.u32(square_duration)
    writer.u32(0)
    writer.u32(resistance)
    writer.u32(capacitance)
    writer.u32(pulse_count)
    writer.u32(pulse_interval_ms)
    writer.f32(electrode_gap_mm)
    writer.raw_bytes(b"\x00" * self.FIELD_TRAILING_RESERVED_BYTES)
    payload = writer.finish()
    if len(payload) != self.METHOD_PAYLOAD_BYTES:
      raise RuntimeError(
        f"Built unexpected method payload length {len(payload)} bytes "
        f"(expected {self.METHOD_PAYLOAD_BYTES})."
      )
    return payload

  def _decode_method_payload(self, payload: bytes) -> Dict[str, Any]:
    if len(payload) != self.METHOD_PAYLOAD_BYTES:
      raise ValueError(f"Expected {self.METHOD_PAYLOAD_BYTES} payload bytes, got {len(payload)}.")

    reader = Reader(payload)
    version = reader.u32()
    name_raw = reader.raw_bytes(self.METHOD_NAME_BYTES)
    protocol_type_code = reader.u32()
    reader.u32()
    pulse_amplitude_volts = reader.u32()
    reader.u32()
    pulse_duration_us = reader.u32()
    reader.u32()
    resistance_ohms = reader.u32()
    capacitance_uf = reader.u32()
    pulse_count = reader.u32()
    pulse_interval_ms = reader.u32()
    electrode_gap_mm = reader.f32()

    protocol_type = next(
      (name for name, code in self.METHOD_PROTOCOL_TYPES.items() if code == protocol_type_code),
      f"unknown({protocol_type_code})",
    )
    return {
      "version": version,
      "name": name_raw.split(b"\x00", maxsplit=1)[0].decode("ascii", errors="ignore"),
      "protocol_type_code": protocol_type_code,
      "protocol_type": protocol_type,
      "pulse_amplitude_volts": pulse_amplitude_volts,
      "pulse_duration_us": pulse_duration_us,
      "resistance_ohms": resistance_ohms,
      "capacitance_uf": capacitance_uf,
      "pulse_count": pulse_count,
      "pulse_interval_ms": pulse_interval_ms,
      "pulse_interval_seconds": pulse_interval_ms / 1000.0,
      "electrode_gap_mm": electrode_gap_mm,
    }

  def _extract_method_payload(self, response: str) -> tuple[str, bytes]:
    match = re.search(r"^meth\s+([0-9A-Fa-f]+)$", response, flags=re.MULTILINE)
    if match is None:
      raise RuntimeError(f"Device response did not contain meth payload: {response}")
    payload_hex = match.group(1)
    payload = bytes.fromhex(payload_hex)
    if len(payload) != self.METHOD_PAYLOAD_BYTES:
      raise RuntimeError(
        f"Unexpected method payload length {len(payload)} bytes (expected {self.METHOD_PAYLOAD_BYTES})."
      )
    return payload_hex.upper(), payload

  def _require_u32(self, value: int, field_name: str) -> int:
    if value < 0 or value > 0xFFFFFFFF:
      raise ValueError(f"{field_name} must fit in u32, got {value}.")
    return value

  def _require_f32(self, value: float, field_name: str) -> float:
    if not isfinite(value):
      raise ValueError(f"{field_name} must be a finite float32 value, got {value}.")
    return value

  def _now_utc_iso(self) -> str:
    return datetime.now(timezone.utc).isoformat()
