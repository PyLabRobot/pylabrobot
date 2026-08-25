import unittest
from inspect import isabstract
from typing import List, TypeVar, cast
from unittest.mock import AsyncMock, patch

from pylabrobot.hettich.centrifuge import (
  ACK,
  ENQ,
  EOT,
  ETX,
  NAK,
  STX,
  HettichCentrifugeError,
  HettichCommandError,
  HettichCommunicationError,
  HettichCooledRoboticCentrifuge,
  HettichMikro220RoboticCentrifuge,
  HettichRoboticCentrifuge,
  HettichRotanta460RoboticCentrifuge,
  HettichRotina380RRoboticCentrifuge,
  HettichRotina380RoboticCentrifuge,
  MIKRO_220_ROBOTIC_ROTORS,
)
from pylabrobot.io.serial import Serial


def enquiry_reply(parameter: str, value: int, address: str = "]") -> bytes:
  body = bytes([STX]) + parameter.encode("ascii") + f"={value:04X}".encode("ascii") + bytes([ETX])
  return bytes([ord(address)]) + body + bytes([HettichRoboticCentrifuge._bcc(body[1:])])


HettichCentrifugeT = TypeVar("HettichCentrifugeT", bound=HettichRoboticCentrifuge)


def make_model_device(
  replies: List[bytes],
  device_class: type[HettichCentrifugeT],
  **kwargs,
) -> HettichCentrifugeT:
  io = AsyncMock(spec=Serial)
  io.port = "FAKE"
  pending = list(replies)
  rx = bytearray()

  async def write(data: bytes) -> None:
    if data != bytes([EOT]) and pending:
      rx.extend(pending.pop(0))

  async def read(num_bytes: int = 1) -> bytes:
    output = bytes(rx[:num_bytes])
    del rx[:num_bytes]
    return output

  io.write.side_effect = write
  io.read.side_effect = read
  with patch("pylabrobot.hettich.centrifuge.Serial", return_value=io):
    device = device_class(port="FAKE", timeout=0.2, poll_interval=0, **kwargs)
  return device


def make_device(replies: List[bytes], **kwargs) -> HettichMikro220RoboticCentrifuge:
  return make_model_device(replies, HettichMikro220RoboticCentrifuge, **kwargs)


def writes(device: HettichRoboticCentrifuge) -> AsyncMock:
  return cast(AsyncMock, device.io.write)


def telegrams(device: HettichRoboticCentrifuge) -> List[bytes]:
  return [call.args[0] for call in writes(device).call_args_list if call.args[0] != bytes([EOT])]


def telegram_parameters(device: HettichRoboticCentrifuge) -> List[bytes]:
  """Return each ENQUIRY or SELECT parameter from recorded wire frames."""
  return [frame[3:8] if frame[2] == STX else frame[2:7] for frame in telegrams(device)]


class HettichFrameTests(unittest.TestCase):
  def setUp(self) -> None:
    self.device = make_device([])

  def test_build_enquiry_matches_manual_example(self) -> None:
    self.assertEqual(
      self.device._build_enquiry("00604"),
      bytes([0x04, 0x5D, 0x30, 0x30, 0x36, 0x30, 0x34, 0x05]),
    )

  def test_build_select_matches_manual_example(self) -> None:
    self.assertEqual(
      self.device._build_select("00603", 1500),
      bytes(
        [
          0x04,
          0x5D,
          0x02,
          0x30,
          0x30,
          0x36,
          0x30,
          0x33,
          0x3D,
          0x30,
          0x35,
          0x44,
          0x43,
          0x03,
          0x09,
        ]
      ),
    )

  def test_parse_enquiry_matches_manual_example(self) -> None:
    reply = bytes(
      [0x5D, 0x02, 0x30, 0x30, 0x36, 0x30, 0x34, 0x3D, 0x30, 0x31, 0x46, 0x34, 0x03, 0x7F]
    )
    self.assertEqual(self.device._parse_enquiry_reply(reply, "00604"), 500)

  def test_rejects_invalid_address_and_short_timeout(self) -> None:
    with self.assertRaises(ValueError):
      HettichMikro220RoboticCentrifuge(port="FAKE", address="a")
    with self.assertRaises(ValueError):
      HettichMikro220RoboticCentrifuge(port="FAKE", timeout=0.1)

  def test_protocol_and_cooled_bases_are_abstract(self) -> None:
    self.assertTrue(isabstract(HettichRoboticCentrifuge))
    self.assertTrue(isabstract(HettichCooledRoboticCentrifuge))
    self.assertFalse(isabstract(HettichMikro220RoboticCentrifuge))
    self.assertFalse(isabstract(HettichRotanta460RoboticCentrifuge))
    self.assertFalse(isabstract(HettichRotina380RoboticCentrifuge))
    self.assertFalse(isabstract(HettichRotina380RRoboticCentrifuge))

  def test_mikro_220_robotic_rotor_table(self) -> None:
    self.assertEqual(set(MIKRO_220_ROBOTIC_ROTORS), {"2334", "2394"})
    self.assertEqual(MIKRO_220_ROBOTIC_ROTORS["2334"].maximum_speed, 13_000)
    self.assertEqual(MIKRO_220_ROBOTIC_ROTORS["2334"].maximum_rcf, 18_327)
    self.assertEqual(MIKRO_220_ROBOTIC_ROTORS["2334"].maximum_volume, 2_000)
    self.assertEqual(MIKRO_220_ROBOTIC_ROTORS["2394"].maximum_speed, 13_000)
    self.assertEqual(MIKRO_220_ROBOTIC_ROTORS["2394"].maximum_rcf, 18_516)

  def test_rotor_specification_converts_between_speed_and_rcf(self) -> None:
    rotor = MIKRO_220_ROBOTIC_ROTORS["2394"]
    self.assertEqual(rotor.rcf_at_speed(13_000), 18_516)
    self.assertEqual(rotor.rcf_at_speed(6_500), 4_629)
    self.assertEqual(rotor.speed_for_rcf(18_516), 13_000)
    self.assertEqual(rotor.speed_for_rcf(4_629), 6_500)

  def test_rotor_specification_rejects_values_above_limits(self) -> None:
    rotor = MIKRO_220_ROBOTIC_ROTORS["2394"]
    with self.assertRaisesRegex(ValueError, "13000 rpm"):
      rotor.rcf_at_speed(13_001)
    with self.assertRaisesRegex(ValueError, "18516"):
      rotor.speed_for_rcf(18_517)

  def test_device_uses_configured_rotor_specification(self) -> None:
    device = make_device([], rotor_catalog_number="2334")
    self.assertIs(device.rotor_specification, MIKRO_220_ROBOTIC_ROTORS["2334"])
    self.assertEqual(device.rcf_at_speed(13_000), 18_327)
    self.assertEqual(device.speed_for_rcf(18_327), 13_000)

  def test_device_requires_known_rotor_for_rcf_conversion(self) -> None:
    with self.assertRaisesRegex(ValueError, "unsupported rotor catalog"):
      make_device([], rotor_catalog_number="unknown")
    with self.assertRaisesRegex(HettichCentrifugeError, "rotor_catalog_number"):
      self.device.rcf_at_speed(1_000)


class HettichProtocolTests(unittest.IsolatedAsyncioTestCase):
  async def test_setup_is_read_only_and_records_identity(self) -> None:
    device = make_model_device(
      [
        enquiry_reply("00685", 0x0001),
        enquiry_reply("00600", 0x1234),
        enquiry_reply("00537", 0xC901),
        enquiry_reply("00636", 0x0112),
      ],
      HettichRotanta460RoboticCentrifuge,
    )
    with patch("pylabrobot.hettich.centrifuge.logger.warning") as warning:
      await device.setup()
    self.assertEqual(device.device_type, "ROTANTA 460 R POS")
    self.assertEqual(device.software_version, "01.12")
    warning.assert_called_once()
    self.assertEqual(
      telegram_parameters(device),
      [b"00685", b"00600", b"00537", b"00636"],
    )
    self.assertTrue(all(frame[-1] == ENQ for frame in telegrams(device)))

  async def test_setup_recognizes_mikro_220_hardware_code(self) -> None:
    device = make_device(
      [
        enquiry_reply("00685", 0x0000),
        enquiry_reply("00600", 0x1234),
        enquiry_reply("00537", 0xE800),
        enquiry_reply("00636", 0x0121),
      ]
    )
    with patch("pylabrobot.hettich.centrifuge.logger.warning") as warning:
      await device.setup()
    self.assertEqual(device.device_type, "MIKRO 220 POS")
    self.assertEqual(device.software_version, "01.21")
    warning.assert_not_called()

  async def test_setup_rejects_unknown_e8_code_without_family_fallback(self) -> None:
    device = make_device(
      [
        enquiry_reply("00685", 0x0000),
        enquiry_reply("00600", 0x1234),
        enquiry_reply("00537", 0xE8FF),
      ]
    )

    with self.assertRaisesRegex(HettichCentrifugeError, "unknown type 0xE8FF"):
      await device.setup()

    cast(AsyncMock, device.io.stop).assert_awaited_once()

  async def test_setup_rejects_a_different_known_model(self) -> None:
    device = make_device(
      [
        enquiry_reply("00685", 0x0000),
        enquiry_reply("00600", 0x1234),
        enquiry_reply("00537", 0xC901),
      ]
    )

    with self.assertRaisesRegex(HettichCentrifugeError, "ROTANTA 460 R POS"):
      await device.setup()

    cast(AsyncMock, device.io.stop).assert_awaited_once()

  async def test_temperature_is_available_on_refrigerated_model(self) -> None:
    device = make_model_device(
      [enquiry_reply("00619", 70)],
      HettichRotanta460RoboticCentrifuge,
    )
    self.assertEqual(await device.request_temperature(), 10.0)

  async def test_enquiry_retries_after_bad_checksum(self) -> None:
    corrupt = bytearray(enquiry_reply("00604", 500))
    corrupt[-1] ^= 0x01
    device = make_device([bytes(corrupt), enquiry_reply("00604", 500)])
    self.assertEqual(await device.request_speed(), 500)
    self.assertEqual(len(telegrams(device)), 2)

  async def test_enquiry_fails_after_three_timeouts(self) -> None:
    device = make_device([b"", b"", b""])
    with self.assertRaises(HettichCommunicationError):
      await device.request_speed()
    self.assertEqual(len(telegrams(device)), 3)

  async def test_nak_reads_and_decodes_siof(self) -> None:
    device = make_device(
      [
        bytes([ord("]"), NAK]),
        enquiry_reply("00685", 0x0080),
      ]
    )
    with self.assertRaisesRegex(HettichCommandError, "improper value or command not allowed"):
      await device._select_parameter("00603", 0xFFFF)
    self.assertEqual(telegram_parameters(device), [b"00603", b"00685"])

  async def test_request_status_decodes_both_state_words(self) -> None:
    device = make_device(
      [
        enquiry_reply("00634", 0x01E4),
        enquiry_reply("00635", 0xA292),
      ]
    )
    status = await device.request_status()
    self.assertEqual(status.phase, "accelerating")
    self.assertTrue(status.status_changed)
    self.assertTrue(status.can_start)
    self.assertEqual(status.program_number, 1)
    self.assertEqual(status.rotor_number, 9)
    self.assertEqual(status.key_lock, "remote")
    self.assertTrue(status.lid_closed)

  async def test_open_hatch_is_noop_when_already_open(self) -> None:
    device = make_device([enquiry_reply("00528", 0xA000)])
    await device.open_hatch()
    self.assertEqual(telegram_parameters(device), [b"00528"])

  async def test_open_hatch_moves_and_waits_for_open_sensor(self) -> None:
    device = make_device(
      [
        enquiry_reply("00528", 0x1800),
        enquiry_reply("00634", 0x0162),
        enquiry_reply("00635", 0xA292),
        bytes([ord("]"), ACK]),
        enquiry_reply("00528", 0xA000),
      ]
    )

    await device.open_hatch()

    frames = telegrams(device)
    self.assertEqual(frames[3], device._build_select("00526", 0x0060))

  async def test_close_hatch_moves_and_waits_for_both_closed_sensors(self) -> None:
    device = make_device(
      [
        enquiry_reply("00528", 0xA000),
        enquiry_reply("00634", 0x0162),
        enquiry_reply("00635", 0xA292),
        bytes([ord("]"), ACK]),
        enquiry_reply("00528", 0x1800),
      ]
    )

    await device.close_hatch()

    frames = telegrams(device)
    self.assertEqual(frames[3], device._build_select("00526", 0x0070))

  async def test_move_to_position_requires_closed_main_lid(self) -> None:
    device = make_device(
      [
        enquiry_reply("00524", 0x1801),
        enquiry_reply("00528", 0x2004),
        enquiry_reply("00634", 0x0162),
        enquiry_reply("00635", 0xA092),
      ]
    )

    with self.assertRaisesRegex(HettichCentrifugeError, "main centrifuge lid"):
      await device.move_to_position(2)

    self.assertTrue(all(frame[-1] == ENQ for frame in telegrams(device)))

  async def test_move_to_position_allows_closed_loading_hatch(self) -> None:
    device = make_device(
      [
        enquiry_reply("00524", 0x1801),
        enquiry_reply("00528", 0x1804),
        enquiry_reply("00634", 0x0162),
        enquiry_reply("00635", 0xA292),
        bytes([ord("]"), ACK]),
        bytes([ord("]"), ACK]),
        enquiry_reply("00528", 0x1803),
        enquiry_reply("00528", 0x1806),
      ]
    )

    await device.move_to_position(2, speed="slow")

    frames = telegrams(device)
    self.assertEqual(frames[4], device._build_select("00524", 0x1802))
    self.assertEqual(frames[5], device._build_select("00526", 0x0001))
    self.assertEqual(telegram_parameters(device)[-2:], [b"00528", b"00528"])

  async def test_end_positioning_is_idempotent_and_can_end_active_mode(self) -> None:
    inactive = make_device([enquiry_reply("00528", 0x1800)])
    await inactive.end_positioning()
    self.assertEqual(telegram_parameters(inactive), [b"00528"])

    active = make_device(
      [
        enquiry_reply("00528", 0x1802),
        enquiry_reply("00634", 0x0162),
        enquiry_reply("00635", 0xA292),
        bytes([ord("]"), ACK]),
        enquiry_reply("00528", 0x1800),
      ]
    )
    await active.end_positioning()
    self.assertEqual(telegrams(active)[3], active._build_select("00526", 0x0080))

  async def test_select_program_is_idempotent_and_selects_a_different_program(self) -> None:
    current = make_device(
      [
        enquiry_reply("00634", 0x0262),
        enquiry_reply("00635", 0xA292),
      ]
    )
    await current.select_program(2)
    self.assertEqual(telegram_parameters(current), [b"00634", b"00635"])

    different = make_device(
      [
        enquiry_reply("00634", 0x0162),
        enquiry_reply("00635", 0xA292),
        bytes([ord("]"), ACK]),
      ]
    )
    await different.select_program(2)
    self.assertEqual(telegrams(different)[-1], different._build_select("00523", 0x0204))

  async def test_live_value_requests_use_their_protocol_parameters(self) -> None:
    device = make_device(
      [
        enquiry_reply("00604", 500),
        enquiry_reply("00605", 13_000),
        enquiry_reply("00602", 17),
      ]
    )

    self.assertEqual(await device.request_speed(), 500)
    self.assertEqual(await device.request_maximum_speed(), 13_000)
    self.assertEqual(await device.request_elapsed_time(), 17)
    self.assertEqual(telegram_parameters(device), [b"00604", b"00605", b"00602"])

  async def test_private_start_spin_checks_state_and_sets_parameters(self) -> None:
    device = make_device(
      [
        enquiry_reply("00634", 0x0162),
        enquiry_reply("00635", 0xA292),
        enquiry_reply("00528", 0x1800),
        enquiry_reply("00605", 5000),
        bytes([ord("]"), ACK]),
        bytes([ord("]"), ACK]),
        bytes([ord("]"), ACK]),
        bytes([ord("]"), ACK]),
      ]
    )
    await device._start_spin(run_time=30, speed=2000)
    frames = telegrams(device)
    self.assertEqual(
      telegram_parameters(device),
      [b"00634", b"00635", b"00528", b"00605", b"00601", b"00603", b"00522", b"00521"],
    )
    self.assertEqual(frames[-4], device._build_select("00601", 30))
    self.assertEqual(frames[-3], device._build_select("00603", 2000))
    self.assertEqual(frames[-2], device._build_select("00522", 1))
    self.assertEqual(frames[-1], device._build_select("00521", 2))

  async def test_private_start_spin_rejects_speed_above_rotor_limit(self) -> None:
    device = make_device(
      [
        enquiry_reply("00634", 0x0162),
        enquiry_reply("00635", 0xA292),
        enquiry_reply("00528", 0x1800),
        enquiry_reply("00605", 5000),
      ]
    )
    with self.assertRaisesRegex(ValueError, "5000 rpm"):
      await device._start_spin(run_time=30, speed=5001)
    self.assertNotIn(b"00521", telegram_parameters(device))

  async def test_private_start_spin_rejects_non_remote_key_position(self) -> None:
    device = make_device(
      [
        enquiry_reply("00634", 0x0162),
        enquiry_reply("00635", 0xA293),
      ]
    )
    with self.assertRaisesRegex(HettichCentrifugeError, "LOCK 2"):
      await device._start_spin(run_time=30, speed=500)
    self.assertNotIn(b"00521", telegram_parameters(device))

  async def test_private_start_spin_ends_positioning_before_start(self) -> None:
    device = make_device(
      [
        enquiry_reply("00634", 0x0162),
        enquiry_reply("00635", 0xA292),
        enquiry_reply("00528", 0x1802),
        bytes([ord("]"), ACK]),
        enquiry_reply("00528", 0x1800),
        enquiry_reply("00634", 0x0162),
        enquiry_reply("00635", 0xA292),
        enquiry_reply("00605", 5000),
        bytes([ord("]"), ACK]),
        bytes([ord("]"), ACK]),
        bytes([ord("]"), ACK]),
        bytes([ord("]"), ACK]),
      ]
    )
    await device._start_spin(run_time=30, speed=2000)
    parameters = telegram_parameters(device)
    self.assertLess(parameters.index(b"00526"), parameters.index(b"00521"))
    self.assertEqual(parameters.count(b"00634"), 2)

  async def test_private_wait_for_standstill_observes_motion_before_returning(self) -> None:
    device = make_device(
      [
        enquiry_reply("00634", 0x01E2),
        enquiry_reply("00635", 0xA292),
        enquiry_reply("00634", 0x01E4),
        enquiry_reply("00635", 0xA292),
        enquiry_reply("00634", 0x01E2),
        enquiry_reply("00635", 0xA292),
      ]
    )
    status = await device._wait_for_standstill(timeout=1, motion_observed=False)
    self.assertEqual(status.phase, "standstill")
    self.assertEqual(telegram_parameters(device).count(b"00634"), 3)

  async def test_spin_counts_duration_from_target_speed(self) -> None:
    device = make_device(
      [
        enquiry_reply("00614", 30),
        enquiry_reply("00634", 0x0162),
        enquiry_reply("00635", 0xA292),
        enquiry_reply("00528", 0x1800),
        enquiry_reply("00605", 5000),
        bytes([ord("]"), ACK]),
        bytes([ord("]"), ACK]),
        bytes([ord("]"), ACK]),
        bytes([ord("]"), ACK]),
        enquiry_reply("00634", 0x01E4),
        enquiry_reply("00635", 0xA292),
        enquiry_reply("00604", 2100),
        enquiry_reply("00634", 0x01E8),
        enquiry_reply("00635", 0xA292),
        enquiry_reply("00604", 2000),
        enquiry_reply("00602", 12),
        bytes([ord("]"), ACK]),
        bytes([ord("]"), ACK]),
        enquiry_reply("00634", 0x01F0),
        enquiry_reply("00635", 0xA292),
        enquiry_reply("00634", 0x01E2),
        enquiry_reply("00635", 0xA292),
      ]
    )

    await device.spin(duration=30, speed=2000, timeout=60)

    frames = telegrams(device)
    run_time_frames = [
      frame for frame in frames if (frame[3:8] if frame[2] == STX else frame[2:7]) == b"00601"
    ]
    self.assertEqual(
      run_time_frames,
      [device._build_select("00601", 60), device._build_select("00601", 42)],
    )
    self.assertEqual(telegram_parameters(device).count(b"00522"), 2)

  async def test_spin_rejects_impossible_target_duration_before_motion(self) -> None:
    device = make_device([enquiry_reply("00614", 30)])

    with self.assertRaisesRegex(ValueError, "59969 seconds"):
      await device.spin(duration=59_970, speed=2_000)

    self.assertEqual(telegram_parameters(device), [b"00614"])
    self.assertNotIn(b"00521", telegram_parameters(device))

  async def test_stop_spin_is_noop_at_standstill(self) -> None:
    device = make_device(
      [
        enquiry_reply("00634", 0x0162),
        enquiry_reply("00635", 0xA292),
      ]
    )
    await device.stop_spin()
    self.assertNotIn(b"00521", telegram_parameters(device))

  async def test_stop_spin_sends_emergency_stop_and_waits_for_standstill(self) -> None:
    device = make_device(
      [
        enquiry_reply("00634", 0x01E8),
        enquiry_reply("00635", 0xA292),
        bytes([ord("]"), ACK]),
        enquiry_reply("00634", 0x01E2),
        enquiry_reply("00635", 0xA292),
      ]
    )

    await device.stop_spin(timeout=1)

    self.assertEqual(telegrams(device)[2], device._build_select("00521", 0x0001))


if __name__ == "__main__":
  unittest.main()
