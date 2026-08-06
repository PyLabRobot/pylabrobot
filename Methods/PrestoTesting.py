"""Run the BeadRegeneration protocol on a KingFisher Presto.

Recreates the BindIt protocol described in the BeadRegeneration run report:

  Plate layout (96-well deep-well plates):
    - TipComb:      Wash Buffer, 150 µL/well
    - NaOHWash1:    0.1 M NaOH, 1000 µL/well
    - NaOHWash2:    0.1 M NaOH, 1000 µL/well
    - PBS:          PBS, 1000 µL/well
    - Ethanol:      20% Ethanol, 500 µL/well

  Steps (Tip1):
    - Mix1:    1 min medium mix, no bead release, collect beads
    - NaOH1:  15 min medium mix, release beads, collect beads
    - NaOH2:  15 min medium mix, release beads, collect beads
    - PBS:     2 min medium mix, release beads, collect beads
    - Ethanol: 1 min medium mix, release beads, no collection

Usage (Jupyter or async script)::

    from Methods.PrestoTesting import run_bead_regeneration
    await run_bead_regeneration()

Or from the command line::

    python Methods/PrestoTesting.py
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Optional

from pylabrobot.particle_processing.kingfisher import KingFisher, KingFisherPrestoBackend
from pylabrobot.particle_processing.kingfisher.bdz import (
  BdzProtocol,
  InstrumentVariant,
  MixStep,
  ProtocolTip,
  Reagent,
  SamplePlate,
  SPEED_NORMAL,
  write_bdz,
)

PROTOCOL_NAME = "BeadRegeneration"


def build_bead_regeneration_protocol() -> BdzProtocol:
  """Build the BeadRegeneration :class:`BdzProtocol` for KingFisher Presto."""
  return BdzProtocol(
    name=PROTOCOL_NAME,
    description="Magnetic bead regeneration wash protocol.",
    variant=InstrumentVariant.PRESTO,
    tips=[
      ProtocolTip(
        name="Tip1",
        sample_plates=[
          SamplePlate("TipComb", reagent=Reagent("Wash Buffer", 150)),
          SamplePlate("NaOHWash1", reagent=Reagent("0.1M NaOH", 1000)),
          SamplePlate("NaOHWash2", reagent=Reagent("0.1M NaOH", 1000)),
          SamplePlate("PBS", reagent=Reagent("PBS", 1000)),
          SamplePlate("Ethanol", reagent=Reagent("20% Ethanol", 500)),
        ],
        steps=[
          MixStep(
            name="Mix1",
            shake_duration_s=60,
            shake_speed=SPEED_NORMAL,
            release_beads_enabled=False,
            collect_beads_enabled=True,
            collect_count=3,
            collect_time_s=1,
            sample_plate_index=0,
          ),
          MixStep(
            name="NaOH1",
            shake_duration_s=15 * 60,
            shake_speed=SPEED_NORMAL,
            release_beads_enabled=True,
            collect_beads_enabled=True,
            collect_count=3,
            collect_time_s=1,
            sample_plate_index=1,
          ),
          MixStep(
            name="NaOH2",
            shake_duration_s=15 * 60,
            shake_speed=SPEED_NORMAL,
            release_beads_enabled=True,
            collect_beads_enabled=True,
            collect_count=3,
            collect_time_s=1,
            sample_plate_index=2,
          ),
          MixStep(
            name="PBS",
            shake_duration_s=2 * 60,
            shake_speed=SPEED_NORMAL,
            release_beads_enabled=True,
            collect_beads_enabled=True,
            collect_count=3,
            collect_time_s=1,
            sample_plate_index=3,
          ),
          MixStep(
            name="Ethanol",
            shake_duration_s=60,
            shake_speed=SPEED_NORMAL,
            release_beads_enabled=True,
            collect_beads_enabled=False,
            sample_plate_index=4,
          ),
        ],
      ),
    ],
  )


async def drive_protocol_run(kf: KingFisher) -> str:
  """Wait for protocol completion, acknowledging plate-interaction events.

  Returns the terminal event name: ``Ready``, ``Aborted``, or ``Error``.
  """
  while True:
    name, evt, ack = await kf.next_event()
    if name in ("LoadPlate", "RemovePlate", "ChangePlate", "Pause"):
      plate = evt.get("plate", "") if evt is not None else ""
      print(f"Action required: {name}" + (f" (plate={plate})" if plate else ""))
      print("  Load/remove plates as prompted on the instrument, then press Continue.")
      if ack is not None:
        await ack()
      continue
    if name == "Error":
      status = await kf.get_status()
      print(
        f"Protocol error: code={status.get('error_code')} "
        f"text={status.get('error_text')!r}"
      )
      if ack is not None:
        await ack()
      return name
    if name in ("Ready", "Aborted"):
      print(f"Protocol finished: {name}")
      return name


async def run_bead_regeneration(
  *,
  serial_number: Optional[str] = None,
  upload: bool = True,
  save_bdz_path: Optional[Path] = None,
  initialize_turntable: bool = True,
) -> str:
  """Connect to Presto, upload (optional), and run BeadRegeneration.

  Args:
    serial_number: USB serial when multiple Presto units are connected.
    upload: Upload the protocol before starting. Set False if the protocol
      is already stored on the instrument under :data:`PROTOCOL_NAME`.
    save_bdz_path: Optional path to write the generated ``.bdz`` file.
    initialize_turntable: Passed to :meth:`KingFisher.setup`; may move the
      turntable to establish known slot positions.

  Returns:
    Terminal event name from :func:`drive_protocol_run`.
  """
  protocol = build_bead_regeneration_protocol()

  if save_bdz_path is not None:
    save_bdz_path.write_bytes(write_bdz(protocol))
    print(f"Wrote protocol to {save_bdz_path}")

  backend = KingFisherPrestoBackend(serial_number=serial_number)
  kf = KingFisher(backend=backend)

  await kf.setup(initialize_turntable=initialize_turntable)
  print(f"Connected to {kf.instrument} (serial {kf.serial}, firmware {kf.version})")

  names, mem_used = await kf.list_protocols()
  print(f"Instrument memory used: {mem_used}%")
  print(f"Protocols on instrument: {names}")

  if upload or PROTOCOL_NAME not in names:
    print(f"Uploading protocol '{PROTOCOL_NAME}'...")
    await kf.upload_protocol(PROTOCOL_NAME, protocol)
    info = await kf.get_protocol_duration(PROTOCOL_NAME)
    print(f"Uploaded. Total duration: {info.get('total_duration', 'N/A')}")
    for tip in info.get("tips", []):
      step_names = [s["name"] for s in tip.get("steps", [])]
      print(f"  {tip['name']}: {step_names}")
  else:
    print(f"Using existing protocol '{PROTOCOL_NAME}' on instrument.")

  print("Starting BeadRegeneration...")
  await kf.start_protocol(PROTOCOL_NAME)
  result = await drive_protocol_run(kf)
  await kf.stop()
  return result


def _parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
    "--serial",
    help="KingFisher Presto USB serial number (required when multiple units are connected).",
  )
  parser.add_argument(
    "--no-upload",
    action="store_true",
    help="Skip upload; assume BeadRegeneration is already on the instrument.",
  )
  parser.add_argument(
    "--save-bdz",
    type=Path,
    metavar="PATH",
    help="Write the generated .bdz file to PATH (useful for BindIt inspection).",
  )
  parser.add_argument(
    "--no-init-turntable",
    action="store_true",
    help="Do not initialize turntable position on connect.",
  )
  return parser.parse_args()


def main() -> None:
  args = _parse_args()
  result = asyncio.run(
    run_bead_regeneration(
      serial_number=args.serial,
      upload=not args.no_upload,
      save_bdz_path=args.save_bdz,
      initialize_turntable=not args.no_init_turntable,
    )
  )
  if result != "Ready":
    raise SystemExit(1)


if __name__ == "__main__":
  main()
