"""Critical tests for KingFisher Presto: communication, command format, and core behavior.

Focus: catch regressions in XML framing (Res/Evt boundaries), Cmd XML format, error handling,
turntable state, and frontend delegation. Assert messages indicate what broke when a test fails.
"""

import asyncio
import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pylabrobot.particle_processing.kingfisher.presto_connection import (
  PrestoConnectionError,
  _find_complete_message,
  format_error_message,
  get_error_code_description,
)
from pylabrobot.particle_processing.kingfisher.kingfisher_backend import (
  KingFisherBackend,
  TurntableLocation,
  _cmd_xml,
)
from pylabrobot.particle_processing.kingfisher.presto_backend import KingFisherPrestoBackend
from pylabrobot.particle_processing.kingfisher.kingfisher import KingFisher


# -----------------------------------------------------------------------------
# Communication: XML message framing (Res/Evt boundaries)
# -----------------------------------------------------------------------------


class TestFindCompleteMessage:
  """_find_complete_message: correct Res/Evt boundaries from HID stream. Break here = framing bug."""

  def test_empty_buffer_returns_none(self):
    result = _find_complete_message(bytearray())
    assert result is None, "Empty buffer must return None, not a phantom message."

  def test_partial_tag_returns_none(self):
    assert _find_complete_message(bytearray(b"<Re")) is None
    assert _find_complete_message(bytearray(b"<Res")) is None
    assert _find_complete_message(bytearray(b"<Evt")) is None

  def test_self_closing_res_returns_message_and_end_index(self):
    msg = b'<Res name="GetStatus" ok="true"/>'
    result = _find_complete_message(bytearray(msg))
    assert result is not None, "COMMUNICATION: complete self-closing Res must be found."
    found, end_idx = result
    assert found == msg, "COMMUNICATION: returned bytes must match full Res."
    assert end_idx == len(msg), "COMMUNICATION: end index must match message length."

  def test_self_closing_evt_returns_message_and_end_index(self):
    msg = b'<Evt name="LoadPlate"/>'
    result = _find_complete_message(bytearray(msg))
    assert result is not None, "COMMUNICATION: complete self-closing Evt must be found."
    found, end_idx = result
    assert found == msg and end_idx == len(msg)

  def test_res_with_body_returns_full_message(self):
    msg = b'<Res name="GetStatus" ok="true"><Status>Idle</Status></Res>'
    result = _find_complete_message(bytearray(msg))
    assert result is not None, "COMMUNICATION: Res with body must be found."
    found, end_idx = result
    assert found == msg and end_idx == len(msg)

  def test_nested_evt_returns_outer_message(self):
    msg = b'<Evt name="ChangePlate"><Evt name="RemovePlate"/></Evt>'
    result = _find_complete_message(bytearray(msg))
    assert result is not None, "COMMUNICATION: nested Evt must return full outer message."
    found, end_idx = result
    assert found == msg and end_idx == len(msg)

  def test_incomplete_nested_returns_none(self):
    buf = bytearray(b'<Evt name="Outer"><Evt name="Inner"/>')
    assert _find_complete_message(buf) is None, "COMMUNICATION: incomplete XML must return None."

  def test_two_messages_returns_first_then_second(self):
    first = b'<Res name="Connect" ok="true"/>'
    second = b'<Evt name="LoadPlate"/>'
    buf = bytearray(first + second)
    result = _find_complete_message(buf)
    assert result is not None
    found, end_idx = result
    assert found == first, "COMMUNICATION: must return first message."
    assert end_idx == len(first)
    del buf[:end_idx]
    result2 = _find_complete_message(buf)
    assert result2 is not None
    found2, end_idx2 = result2
    assert found2 == second and end_idx2 == len(second)

  def test_partial_second_message_returns_first_only(self):
    first = b'<Res name="Connect" ok="true"/>'
    buf = bytearray(first + b'<Evt name="Load')
    result = _find_complete_message(buf)
    assert result is not None
    found, end_idx = result
    assert found == first and end_idx == len(first)


# -----------------------------------------------------------------------------
# Command format: Cmd XML (instrument compatibility)
# -----------------------------------------------------------------------------


class TestCmdXml:
  """_cmd_xml: Cmd XML format per spec. Break here = instrument may reject commands."""

  def test_connect_no_attrs(self):
    out = _cmd_xml("Connect")
    assert out == '<Cmd name="Connect"/>\n', "CMD: Connect with no attrs must match spec."

  def test_connect_with_set_time(self):
    out = _cmd_xml("Connect", setTime="2025-01-01 12:00:00")
    assert 'name="Connect"' in out and 'setTime="2025-01-01 12:00:00"' in out

  def test_get_status(self):
    assert _cmd_xml("GetStatus") == '<Cmd name="GetStatus"/>\n'

  def test_start_protocol_with_protocol_tip_step(self):
    out = _cmd_xml("StartProtocol", protocol="P", tip="T1", step="Step1")
    assert 'name="StartProtocol"' in out and 'protocol="P"' in out and 'tip="T1"' in out and 'step="Step1"' in out

  def test_none_attr_omitted(self):
    out = _cmd_xml("StartProtocol", protocol="P", step=None)
    assert 'protocol="P"' in out and "step=" not in out, "CMD: None attrs must be omitted."

  def test_rotate_nest_position(self):
    out = _cmd_xml("Rotate", nest="1", position="2")
    assert 'name="Rotate"' in out and 'nest="1"' in out and 'position="2"' in out

  def test_acknowledge_and_disconnect(self):
    assert _cmd_xml("Acknowledge") == '<Cmd name="Acknowledge"/>\n'
    assert _cmd_xml("Disconnect") == '<Cmd name="Disconnect"/>\n'


# -----------------------------------------------------------------------------
# Error handling: code lookup and message formatting
# -----------------------------------------------------------------------------


class TestErrorCodes:
  """Error/warning code lookup and format_error_message. Break = wrong user-facing error text."""

  def test_known_error_code_returns_description(self):
    desc = get_error_code_description(5)
    assert desc is not None, "ERROR_CODES: known code 5 (magnets) must have description."
    assert "Magnets" in desc or "magnets" in desc

  def test_unknown_error_code_returns_none(self):
    assert get_error_code_description(99999) is None

  def test_format_error_message_uses_instrument_text_when_present(self):
    out = format_error_message(5, " Custom instrument message ", kind="error")
    assert "Custom instrument message" in out or "instrument message" in out

  def test_format_error_message_uses_standard_desc_when_no_instrument_text(self):
    out = format_error_message(5, None, kind="error")
    assert out and ("Magnets" in out or "magnets" in out or "5" in out)

  def test_format_error_message_unknown_code_yields_unknown_message(self):
    out = format_error_message(99999, None, kind="error")
    assert "Unknown" in out and "99999" in out


# -----------------------------------------------------------------------------
# Backend: turntable state, rotate, load_plate, get_status, get_protocol_time_left
# -----------------------------------------------------------------------------


class TestTurntableState:
  """Turntable state and rotate/load_plate. Break = wrong slot/location or state not updated."""

  def test_initial_state_is_unknown(self):
    backend = KingFisherPrestoBackend()
    assert backend.get_turntable_state() == {1: None, 2: None}

  def test_rotate_sends_correct_nest_and_position_for_loading(self):
    backend = KingFisherPrestoBackend()
    evt_ready = ET.fromstring('<Evt name="Ready"/>')
    with patch.object(backend._conn, "send_without_response", new_callable=AsyncMock) as mock_send, patch.object(
      backend._conn, "get_event", new_callable=AsyncMock, side_effect=[evt_ready]
    ):
      asyncio.run(backend.rotate(position=1, location="loading"))
    call_xml = mock_send.call_args[0][0]
    assert 'nest="1"' in call_xml and 'position="2"' in call_xml, "BACKEND: rotate(position=1, loading) -> nest=1, position=2."
    assert backend.get_turntable_state() == {1: "loading", 2: "processing"}

  def test_rotate_sends_correct_nest_and_position_for_processing(self):
    backend = KingFisherPrestoBackend()
    evt_ready = ET.fromstring('<Evt name="Ready"/>')
    with patch.object(backend._conn, "send_without_response", new_callable=AsyncMock), patch.object(
      backend._conn, "get_event", new_callable=AsyncMock, side_effect=[evt_ready]
    ):
      asyncio.run(backend.rotate(position=1, location="processing"))
    assert backend.get_turntable_state() == {1: "processing", 2: "loading"}

  def test_rotate_on_error_does_not_update_state_and_raises(self):
    backend = KingFisherPrestoBackend()
    evt_error = ET.fromstring('<Evt name="Error"><Error code="5">Turntable position error.</Error></Evt>')
    with patch.object(backend._conn, "send_without_response", new_callable=AsyncMock), patch.object(
      backend._conn, "get_event", new_callable=AsyncMock, side_effect=[evt_error]
    ), patch.object(backend, "error_acknowledge", new_callable=AsyncMock):
      with pytest.raises(PrestoConnectionError):
        asyncio.run(backend.rotate(position=1, location="loading"))
    assert backend.get_turntable_state() == {1: None, 2: None}

  def test_load_plate_when_unknown_raises(self):
    backend = KingFisherPrestoBackend()
    with pytest.raises(ValueError, match="Turntable state unknown"):
      asyncio.run(backend.load_plate())

  def test_load_plate_when_known_rotates_correct_slot_to_processing(self):
    backend = KingFisherPrestoBackend()
    backend._position_at_processing = 1
    evt_ready = ET.fromstring('<Evt name="Ready"/>')
    with patch.object(backend._conn, "send_without_response", new_callable=AsyncMock) as mock_send, patch.object(
      backend._conn, "get_event", new_callable=AsyncMock, side_effect=[evt_ready]
    ):
      asyncio.run(backend.load_plate())
    call_xml = mock_send.call_args[0][0]
    assert 'nest="2"' in call_xml and 'position="1"' in call_xml
    assert backend.get_turntable_state() == {1: "loading", 2: "processing"}

  def test_stop_clears_turntable_state(self):
    backend = KingFisherPrestoBackend()
    backend._position_at_processing = 1
    with patch.object(backend._conn, "send_command", new_callable=AsyncMock), patch.object(
      backend._conn, "stop", new_callable=AsyncMock
    ):
      asyncio.run(backend.stop())
    assert backend.get_turntable_state() == {1: None, 2: None}


class TestBackendGetStatusAndTimeLeft:
  """Backend get_status and get_protocol_time_left parsing. Break = wrong status/time_left dicts."""

  def test_get_status_parses_ok_status_error(self):
    backend = KingFisherPrestoBackend()
    res_xml = '<Res name="GetStatus" ok="true"><Status>Idle</Status></Res>'
    with patch.object(backend._conn, "send_command", new_callable=AsyncMock) as mock_send:
      mock_send.return_value = ET.fromstring(res_xml)
      result = asyncio.run(backend.get_status())
    assert result["ok"] is True, "BACKEND: get_status must set ok from Res."
    assert result["status"] == "Idle"
    assert "GetStatus" in mock_send.call_args[0][0]

  def test_get_status_parses_error_code_and_description(self):
    backend = KingFisherPrestoBackend()
    res_xml = '<Res name="GetStatus" ok="false"><Status>In error</Status><Error code="5">Magnets position error.</Error></Res>'
    with patch.object(backend._conn, "send_command", new_callable=AsyncMock) as mock_send:
      mock_send.return_value = ET.fromstring(res_xml)
      result = asyncio.run(backend.get_status())
    assert result["ok"] is False, "BACKEND: get_status must set ok=False when Res has Error."
    assert result.get("error_code") == 5
    assert result.get("error_code_description") or "Magnets" in str(result.get("error_text", ""))

  def test_get_protocol_time_left_parses_time_left_and_time_to_pause(self):
    backend = KingFisherPrestoBackend()
    res_xml = '<Res name="GetProtocolTimeLeft" ok="true"><TimeLeft value="PT2M42S"/><TimeToPause value="PT30S"/></Res>'
    with patch.object(backend._conn, "send_command", new_callable=AsyncMock) as mock_send:
      mock_send.return_value = ET.fromstring(res_xml)
      result = asyncio.run(backend.get_protocol_time_left(protocol="MyProtocol"))
    assert result["time_left"] == "PT2M42S", "BACKEND: get_protocol_time_left must parse TimeLeft."
    assert result["time_to_pause"] == "PT30S"
    assert "GetProtocolTimeLeft" in mock_send.call_args[0][0] and "MyProtocol" in mock_send.call_args[0][0]


# -----------------------------------------------------------------------------
# Frontend: next_event, get_run_state, delegation to backend
# -----------------------------------------------------------------------------


class TestFrontend:
  """Frontend next_event (name, evt, ack), get_run_state, and delegation. Break = API contract changed."""

  def test_next_event_returns_name_evt_ack_for_user_event(self):
    mock_backend = MagicMock()
    evt_load = ET.fromstring('<Evt name="LoadPlate" plate="Plate1"/>')
    mock_backend.get_event = AsyncMock(side_effect=[evt_load])
    mock_backend._setup_finished = True
    presto = KingFisher(backend=mock_backend)
    presto._setup_finished = True
    name, evt, ack = asyncio.run(presto.next_event())
    assert name == "LoadPlate", "FRONTEND: next_event must return event name."
    assert evt is evt_load
    assert ack is not None and callable(ack)

  def test_next_event_attach_when_idle_returns_ready_without_reading_queue(self):
    mock_backend = MagicMock()
    mock_backend.get_status = AsyncMock(
      return_value={"ok": True, "status": "Idle", "error_code": None, "error_text": None, "error_code_description": None}
    )
    mock_backend.get_event = AsyncMock()
    mock_backend._setup_finished = True
    presto = KingFisher(backend=mock_backend)
    presto._setup_finished = True
    name, evt, ack = asyncio.run(presto.next_event(attach=True))
    assert name == "Ready" and evt is None and ack is None
    mock_backend.get_event.assert_not_called()

  def test_get_run_state_idle_has_expected_message(self):
    mock_backend = MagicMock()
    mock_backend.get_status = AsyncMock(
      return_value={"ok": True, "status": "Idle", "error_code": None, "error_text": None, "error_code_description": None}
    )
    mock_backend._setup_finished = True
    presto = KingFisher(backend=mock_backend)
    presto._setup_finished = True
    result = asyncio.run(presto.get_run_state())
    assert result["status"] == "Idle"
    assert "ready" in result["message"].lower() or "next command" in result["message"].lower()

  def test_start_protocol_delegates_to_backend(self):
    mock_backend = MagicMock()
    mock_backend.start_protocol = AsyncMock(return_value=None)
    mock_backend._setup_finished = True
    presto = KingFisher(backend=mock_backend)
    presto._setup_finished = True
    asyncio.run(presto.start_protocol("MyProtocol", tip="Tip1", step="Step1"))
    mock_backend.start_protocol.assert_called_once_with("MyProtocol", tip="Tip1", step="Step1")

  def test_rotate_and_load_plate_delegate_to_backend(self):
    mock_backend = MagicMock()
    mock_backend.rotate = AsyncMock(return_value=None)
    mock_backend.load_plate = AsyncMock(return_value=None)
    mock_backend.get_turntable_state.return_value = {1: "processing", 2: "loading"}
    mock_backend._setup_finished = True
    presto = KingFisher(backend=mock_backend)
    presto._setup_finished = True
    asyncio.run(presto.rotate(position=1, location=TurntableLocation.LOADING))
    mock_backend.rotate.assert_called_once_with(position=1, location=TurntableLocation.LOADING)
    asyncio.run(presto.load_plate())
    mock_backend.load_plate.assert_called_once()

  def test_load_plate_value_error_propagates(self):
    mock_backend = MagicMock()
    mock_backend.load_plate = AsyncMock(side_effect=ValueError("Turntable state unknown; call rotate() first."))
    mock_backend._setup_finished = True
    presto = KingFisher(backend=mock_backend)
    presto._setup_finished = True
    with pytest.raises(ValueError, match="Turntable state unknown"):
      asyncio.run(presto.load_plate())
