"""In-process mock of an Inheco ODTC SiLA 1.x device.

Speaks the same SOAP-over-HTTP wire protocol as a real ODTC so the full stack
(``ODTC`` → ``Thermocycler``/``LoadingTray`` → ``ODTCThermocyclerBackend`` →
``ODTCDriver`` → SOAP → *device*) can be exercised end-to-end over a real TCP
socket, without hardware. Mirrors the pattern of
``pylabrobot.centrifuge.highres.mock_server``.

Wire behaviour reproduced:

* Synchronous SOAP responses carry ``<{Command}Result><returnCode>…`` — the same
  shape ``InhecoSiLAInterface._get_return_code_and_message`` reads.
* Commands the device cannot answer instantly (``ExecuteMethod``, ``OpenDoor``,
  ``CloseDoor``) return SiLA return-code ``2`` ("asynchronous") and the device
  later POSTs a ``ResponseEvent`` — carrying the request's ``requestId`` — back to
  the event-receiver URI it was given in ``Reset``.
* ``ReadActualTemperature`` / ``GetParameters`` return data synchronously inside
  ``<ResponseData><Parameter><String>…``.

The mock binds an ephemeral port; point a driver at it with
``ODTCDriver(machine_ip="127.0.0.1", machine_port=server.port, client_ip="127.0.0.1")``
or ``ODTC(odtc_ip="127.0.0.1", odtc_port=server.port, client_ip="127.0.0.1")``.
"""

from __future__ import annotations

import http.server
import threading
import urllib.request
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

from pylabrobot.inheco.sila.soap import (
  SOAP_ENV,
  _localname,
  _xml_to_obj,
  soap_body_payload,
)

# Commands that complete asynchronously: the device acknowledges with return
# code 2, then POSTs a ResponseEvent once the physical action finishes.
_ASYNC_COMMANDS = {"ExecuteMethod", "OpenDoor", "CloseDoor"}

# Default block/lid/etc. sensor readings in °C (overridable via set_temperatures).
_DEFAULT_TEMPERATURES: Dict[str, float] = {
  "Mount": 25.0,
  "Mount_Monitor": 25.0,
  "Lid": 25.0,
  "Lid_Monitor": 25.0,
  "Ambient": 23.0,
  "PCB": 30.0,
  "Heatsink": 28.0,
  "Heatsink_TEC": 28.0,
}


def _build_envelope(payload: ET.Element) -> bytes:
  """Wrap a payload element in a SOAP 1.1 envelope and serialize to bytes."""
  env = ET.Element(f"{{{SOAP_ENV}}}Envelope")
  body = ET.SubElement(env, f"{{{SOAP_ENV}}}Body")
  body.append(payload)
  data: bytes = ET.tostring(env, encoding="utf-8", xml_declaration=False)
  return data


def _response_payload(
  command: str,
  return_code: int = 1,
  message: str = "Success",
  extra: Optional[Dict[str, str]] = None,
  response_data: Optional[Tuple[str, str]] = None,
) -> ET.Element:
  """Build a ``<{command}Response>`` element (localname is what the driver reads)."""
  resp = ET.Element(f"{command}Response")
  result = ET.SubElement(resp, f"{command}Result")
  ET.SubElement(result, "returnCode").text = str(return_code)
  ET.SubElement(result, "message").text = message
  ET.SubElement(result, "duration").text = "PT0S"
  ET.SubElement(result, "deviceClass").text = "0"
  for tag, text in (extra or {}).items():
    ET.SubElement(resp, tag).text = text
  if response_data is not None:
    name, string_content = response_data
    rd = ET.SubElement(resp, "ResponseData")
    param = ET.SubElement(rd, "Parameter", name=name)
    ET.SubElement(param, "String").text = string_content  # ET escapes embedded XML
  return resp


def _response_event_body(request_id: int, return_code: int = 1, message: str = "Success") -> bytes:
  ev = ET.Element("ResponseEvent")
  ET.SubElement(ev, "requestId").text = str(request_id)
  rv = ET.SubElement(ev, "returnValue")
  ET.SubElement(rv, "returnCode").text = str(return_code)
  ET.SubElement(rv, "message").text = message
  return _build_envelope(ev)


# The four data series the ODTC emits per DataEvent, in order.
_DATA_SERIES: List[Tuple[str, str]] = [
  ("Elapsed time", "ms"),
  ("Target temperature", "1/100°C"),
  ("Current temperature", "1/100°C"),
  ("LID temperature", "1/100°C"),
]


def _data_event_body(
  request_id: int,
  elapsed_ms: int,
  target_c100: int,
  current_c100: int,
  lid_c100: int,
) -> bytes:
  """Build a DataEvent whose ``dataValue`` matches ODTC's nested AnyData layout.

  Temperatures are in 1/100 °C, elapsed time in ms — the raw integer units the
  device reports and ``protocol._parse_data_event_payload`` expects.
  """
  series_root = ET.Element("DataSeriesSet")
  for (name_id, unit), value in zip(
    _DATA_SERIES, (elapsed_ms, target_c100, current_c100, lid_c100)
  ):
    ds = ET.SubElement(series_root, "dataSeries", nameId=name_id, unit=unit)
    ET.SubElement(ds, "integerValue").text = str(int(value))
  inner_xml = ET.tostring(series_root, encoding="unicode")

  outer = ET.Element("DataValue")
  ET.SubElement(outer, "AnyData").text = inner_xml  # escaped by tostring
  data_value = ET.tostring(outer, encoding="unicode")

  ev = ET.Element("DataEvent")
  ET.SubElement(ev, "requestId").text = str(request_id)
  ET.SubElement(ev, "dataValue").text = data_value  # escaped again by tostring
  return _build_envelope(ev)


class MockODTCServer:
  """A minimal in-process Inheco ODTC SiLA 1.x device for tests and demos."""

  def __init__(self, host: str = "127.0.0.1", async_event_delay: float = 0.02) -> None:
    self.host = host
    self.async_event_delay = async_event_delay

    # device state
    self.state: str = "standby"
    self.event_receiver_uri: Optional[str] = None
    self.methods_xml: Optional[str] = None
    self.lock_id: Optional[str] = None
    self.door_open: bool = False
    self.simulation_mode: bool = False
    self.temperatures: Dict[str, float] = dict(_DEFAULT_TEMPERATURES)

    # Test knob: map command name -> (return_code, message) to force a synchronous
    # error response (e.g. {"ExecuteMethod": (9, "Invalid state")}).
    self.error_responses: Dict[str, Tuple[int, str]] = {}

    # Test knob: DataEvents (elapsed_ms, target, current, lid — temps in 1/100 °C)
    # emitted during an ExecuteMethod run, before its completion ResponseEvent.
    self.data_events: List[Tuple[int, int, int, int]] = []
    # When False, ExecuteMethod runs "forever" (no completion ResponseEvent), so a
    # test can read progress deterministically before the method finishes.
    self.auto_complete: bool = True

    # observability for assertions
    self.received_commands: List[Tuple[str, dict]] = []
    self._timers: List[threading.Timer] = []
    self._lock = threading.Lock()

    self._httpd = http.server.ThreadingHTTPServer((host, 0), self._make_handler())
    self._thread: Optional[threading.Thread] = None

  # ------------------------------------------------------------------
  # Lifecycle
  # ------------------------------------------------------------------

  @property
  def port(self) -> int:
    return self._httpd.server_address[1]

  def start(self) -> "MockODTCServer":
    self._thread = threading.Thread(target=self._httpd.serve_forever, name="mock-odtc", daemon=True)
    self._thread.start()
    return self

  def stop(self) -> None:
    for t in self._timers:
      t.cancel()
    self._httpd.shutdown()
    self._httpd.server_close()
    if self._thread is not None:
      self._thread.join(timeout=5)

  def __enter__(self) -> "MockODTCServer":
    return self.start()

  def __exit__(self, *exc: object) -> None:
    self.stop()

  # ------------------------------------------------------------------
  # Test knobs
  # ------------------------------------------------------------------

  def set_temperatures(self, **temps: float) -> None:
    """Override sensor readings, e.g. ``set_temperatures(Mount=95.0, Lid=105.0)``."""
    self.temperatures.update(temps)

  # ------------------------------------------------------------------
  # Command handling
  # ------------------------------------------------------------------

  def _sensor_values_xml(self) -> str:
    root = ET.Element("SensorValues", timestamp="2026-01-01T00:00:00.000000")
    for tag, celsius in self.temperatures.items():
      ET.SubElement(root, tag).text = str(int(round(celsius * 100)))
    return ET.tostring(root, encoding="unicode")

  def _dispatch(self, command: str, params: dict) -> ET.Element:
    """Return the synchronous SOAP response payload for a command."""
    if command in self.error_responses:
      code, message = self.error_responses[command]
      return _response_payload(command, return_code=code, message=message)

    if command == "Reset":
      self.event_receiver_uri = params.get("eventReceiverURI") or self.event_receiver_uri
      self.simulation_mode = bool(params.get("simulationMode", False))
      self.lock_id = None
      self.state = "standby"
      return _response_payload(command)

    if command == "Initialize":
      self.state = "idle"
      return _response_payload(command)

    if command == "GetStatus":
      return _response_payload(command, extra={"state": self.state})

    if command == "SetParameters":
      self._store_methods_xml(params.get("paramsXML"))
      return _response_payload(command)

    if command == "GetParameters":
      xml = self.methods_xml or (
        "<MethodSet><DeleteAllMethods>false</DeleteAllMethods></MethodSet>"
      )
      return _response_payload(command, return_code=3, response_data=("MethodsXML", xml))

    if command == "ReadActualTemperature":
      return _response_payload(command, response_data=("SensorValues", self._sensor_values_xml()))

    if command == "StopMethod":
      self.state = "idle"
      return _response_payload(command)

    if command == "LockDevice":
      self.lock_id = params.get("lockId")
      return _response_payload(command)

    if command == "UnlockDevice":
      self.lock_id = None
      return _response_payload(command)

    if command in _ASYNC_COMMANDS:
      return self._handle_async(command, params)

    # Unknown command: acknowledge so the driver doesn't hang.
    return _response_payload(command)

  def _handle_async(self, command: str, params: dict) -> ET.Element:
    """Acknowledge with return code 2 and schedule the completion ResponseEvent."""
    if command == "ExecuteMethod":
      self.state = "busy"
    elif command == "OpenDoor":
      self.door_open = True
    elif command == "CloseDoor":
      self.door_open = False

    request_id = params.get("requestId")
    if isinstance(request_id, int):
      self._schedule_events(command, request_id)
    return _response_payload(command, return_code=2, message="Accepted")

  def _schedule_events(self, command: str, request_id: int) -> None:
    """Emit any DataEvents (ExecuteMethod only), then the completion ResponseEvent."""
    base = self.async_event_delay
    tick = 0
    if command == "ExecuteMethod":
      for i, data_event in enumerate(self.data_events):
        self._schedule(
          base * (i + 1),
          lambda de=data_event: self._post_event(_data_event_body(request_id, *de)),
        )
      tick = len(self.data_events)

    if command != "ExecuteMethod" or self.auto_complete:

      def _complete() -> None:
        if command == "ExecuteMethod":
          self.state = "idle"
        self._post_event(_response_event_body(request_id))

      self._schedule(base * (tick + 1), _complete)

  def _schedule(self, delay: float, fn) -> None:
    timer = threading.Timer(delay, fn)
    timer.daemon = True
    with self._lock:
      self._timers.append(timer)
    timer.start()

  def _store_methods_xml(self, params_xml: Optional[str]) -> None:
    if not params_xml:
      return
    try:
      param_set = ET.fromstring(params_xml)
    except ET.ParseError:
      return
    for param in param_set.findall(".//Parameter"):
      if param.attrib.get("name") == "MethodsXML":
        string_el = param.find("String")
        if string_el is not None and string_el.text:
          self.methods_xml = string_el.text

  # ------------------------------------------------------------------
  # Event delivery
  # ------------------------------------------------------------------

  def _post_event(self, body: bytes) -> None:
    uri = self.event_receiver_uri
    if not uri:
      return
    req = urllib.request.Request(
      url=uri,
      data=body,
      method="POST",
      headers={"Content-Type": "text/xml; charset=utf-8"},
    )
    try:
      with urllib.request.urlopen(req, timeout=5):
        pass
    except Exception:  # noqa: BLE001 — event delivery is best-effort in the mock
      pass

  # ------------------------------------------------------------------
  # HTTP plumbing
  # ------------------------------------------------------------------

  def _make_handler(self):
    server = self

    class _Handler(http.server.BaseHTTPRequestHandler):
      def log_message(self, *args: object) -> None:  # silence
        return

      def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else b""
        try:
          payload = soap_body_payload(body.decode("utf-8"))
          command = _localname(payload.tag)
          params = _xml_to_obj(payload)
          if not isinstance(params, dict):
            params = {}
          with server._lock:
            server.received_commands.append((command, params))
          response = _build_envelope(server._dispatch(command, params))
        except Exception:  # noqa: BLE001 — always return a well-formed envelope
          response = _build_envelope(_response_payload("Fault", return_code=9, message="Fault"))
        self.send_response(200)
        self.send_header("Content-Type", "text/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    return _Handler
