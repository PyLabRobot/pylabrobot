"""BindIt .bdz protocol file reader/writer for KingFisher Presto and Duo.

Binary format:

  [0   : 61  ]  BindIt header (exactly 61 bytes)
  [61  : 61+S1]  gzip stream #1 → Properties XML
  [61+S1 : +8 ]  8-byte separator: b'\\x01\\x00\\x00\\x01' + LE uint32(S2)
  [+8  : +8+S2]  gzip stream #2 → ExportedData XML
  [after S2   ]  trailing section (42 + len(protocol_name) bytes)

Where S1, S2 are the compressed sizes of each gzip stream.

Header layout:
  [0:4]   magic = B6 75 1C F2
  [4:6]   LE uint16 = 1            (format version)
  [6:8]   LE uint16 = 0x000A       (constant)
  [8:12]  LE uint32 = 1            (constant)
  [12:16] LE uint32 = 27           (constant)
  [16:18] LE uint16 = 15           (length of vendor string)
  [18:33] "BindIt Software"
  [33:35] LE uint16 = 8            (length of version string)
  [35:43] "4.0.0.45"
  [43:45] LE uint16 = 11           (constant)
  [45:49] LE uint32 = 1            (constant)
  [49:53] LE uint32 = file_size - 63   (content size from byte 63 to EOF)
  [53:56] 3 zero bytes             (constant)
  [56]    0x01                     (constant)
  [57:61] LE uint32 = S1           (compressed size of gzip stream #1)

Trailing section (after stream #2):
  [0:4]           LE uint32 = 2                  (constant)
  [4:8]           LE uint32 = len(name) + 24     (size indicator)
  [8:12]          LE uint32 = params_type        (InstrumentParameters type; 713=Presto, 706=Duo)
  [12:26]         14 zero bytes                  (constant)
  [26:30]         LE uint32 = 1                  (constant)
  [30:32]         LE uint16 = len(protocol_name)
  [32:32+nlen]    protocol_name (ASCII)
  [32+nlen:42+nlen] b'\\x01\\x00\\x01\\x00' + 6 zero bytes  (constant 10 bytes)

Speed preset UUIDs (predefined by BindIt, instrument recognises by UUID):
  SPEED_SLOW   = 6e89445e-98b2-43c5-8ae5-c37ed517f506
  SPEED_NORMAL = 563b24fa-2eb7-4497-928b-5e91b740a01e
  SPEED_FAST   = 2e7c9f99-d2c0-4baf-b04c-979e0ee3de00
  SPEED_VFAST  = c220b7c5-b952-4b62-a960-4928ee0a2ede
  SPEED_MAX    = abdb4a51-cbc4-4d8f-925f-f4a46224a254
  SPEED_NONE   = ffffffff-ffff-ffff-ffff-ffffffffffff

Plate type UUIDs (predefined by BindIt):
  PLATE_96_DWP = 8b7d7c98-275f-4285-8129-5f8ed46fb01e  (96-well deep well plate)
  PLATE_24_DWP = 0d419a5c-1dc6-425f-8d9d-0b18481869cc  (24-well deep well plate)
  PLATE_96_WP  = 5d3a0092-8ae3-4d66-a17c-0ba1d569f115  (96-well standard plate)
"""

from __future__ import annotations

import gzip
import io
import re
import struct
import uuid
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET


# ── Binary format constants ──────────────────────────────────────────────────

BINDIT_MAGIC   = b"\xb6\x75\x1c\xf2"
BINDIT_VENDOR  = "BindIt Software"
BINDIT_VERSION = "4.0.0.45"
GZIP_XFL_BYTE  = 0x04   # "fastest compression" — matches BindIt output
GZIP_OS_BYTE   = 0x00   # OS byte in gzip header — matches BindIt output
STREAM_SEP_PREFIX = b"\x01\x00\x00\x01"  # 4-byte constant prefix of the inter-stream separator

# ── Instrument variant ───────────────────────────────────────────────────────

class InstrumentVariant(Enum):
    """Selects which KingFisher instrument the protocol targets."""
    PRESTO = "presto"
    DUO    = "duo"


# ── Instrument-specific constants ────────────────────────────────────────────

KF_PRESTO_INSTRUMENT_TYPE_ID     = "9da3c7a3-bfb4-455e-b1c6-86f668e44ed0"
KF_PRESTO_INSTRUMENT_PARAMS_TYPE = 713

KF_DUO_INSTRUMENT_TYPE_ID        = "754481d7-4624-41fe-9fa9-41f070dded54"
KF_DUO_INSTRUMENT_PARAMS_TYPE    = 706

# ── Speed preset UUIDs ───────────────────────────────────────────────────────

# Presto speed presets
SPEED_SLOW   = "6e89445e-98b2-43c5-8ae5-c37ed517f506"
SPEED_NORMAL = "563b24fa-2eb7-4497-928b-5e91b740a01e"
SPEED_FAST   = "2e7c9f99-d2c0-4baf-b04c-979e0ee3de00"
SPEED_VFAST  = "c220b7c5-b952-4b62-a960-4928ee0a2ede"
SPEED_MAX    = "abdb4a51-cbc4-4d8f-925f-f4a46224a254"
SPEED_NONE   = "ffffffff-ffff-ffff-ffff-ffffffffffff"

# Duo speed presets (observed from duo_example_protocol.bdz)
DUO_SPEED_SLOW   = "6e89445e-98b2-43c5-8ae5-c37ed517f506"
DUO_SPEED_NORMAL = "563b24fa-2eb7-4497-928b-5e91b740a01e"
DUO_SPEED_FAST   = "abdb4a51-cbc4-4d8f-925f-f4a46224a254"

# ── Plate type UUIDs ─────────────────────────────────────────────────────────

PLATE_96_DWP  = "8b7d7c98-275f-4285-8129-5f8ed46fb01e"  # 96-well deep well
PLATE_24_DWP  = "0d419a5c-1dc6-425f-8d9d-0b18481869cc"  # 24-well deep well
PLATE_96_WP   = "5d3a0092-8ae3-4d66-a17c-0ba1d569f115"  # 96-well standard
PLATE_STRIP_12 = "48e32472-cbfb-46a9-bacf-58ba3d7aa2cd"  # 1×12 strip (KF Duo)

# ── Tip type UUIDs ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TipType:
    """A physical KingFisher tip comb type, identified by BindIt's internal UUID.

    The ``id`` field is the value BindIt requires in ``<Tip id="...">``.
    Using an unrecognized UUID causes BindIt to show "Tip definition = None"
    and mark the protocol invalid.

    Use the predefined constants (``TIP_PRESTO_96_DWP``, etc.) rather than
    constructing these directly.
    """

    id: str                              # BindIt catalog identifier
    plate_type_id: str                   # plate type this tip comb is paired with
    instrument_variant: "InstrumentVariant"


# Predefined tip types (observed from BindIt-generated .bdz files)
TIP_PRESTO_96_DWP = TipType(
    id="2ab24e9a-b88e-4d3b-8fc3-babbc5a7c742",
    plate_type_id=PLATE_96_DWP,
    instrument_variant=InstrumentVariant.PRESTO,
)
TIP_PRESTO_24_DWP = TipType(
    id="9d191c9e-73c1-4b8f-9b33-cc32adca62c4",
    plate_type_id=PLATE_24_DWP,
    instrument_variant=InstrumentVariant.PRESTO,
)
TIP_DUO = TipType(
    id="2995fc63-f4be-44db-a3d0-702c866a7d38",
    plate_type_id=PLATE_96_DWP,
    instrument_variant=InstrumentVariant.DUO,
)

_TIP_BY_VARIANT_AND_PLATE: Dict[Tuple[InstrumentVariant, str], TipType] = {
    (InstrumentVariant.PRESTO, PLATE_96_DWP): TIP_PRESTO_96_DWP,
    (InstrumentVariant.PRESTO, PLATE_24_DWP): TIP_PRESTO_24_DWP,
    (InstrumentVariant.DUO,    PLATE_96_DWP): TIP_DUO,
}


# ── Duo row names ─────────────────────────────────────────────────────────────

_DUO_ROW_NAMES: Tuple[str, ...] = ("A", "B", "C", "D", "E", "F", "G", "H")


# ── InstrumentContract ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class InstrumentContract:
    """All variant-specific structural constants for one KingFisher instrument.

    Referenced by the backend (``contract`` property) and consumed by
    ``write_bdz`` / ``read_bdz``.  To support a new instrument variant:
    define a new ``InstrumentContract`` constant — no writer/parser changes needed.
    """

    instrument_type_id: str
    params_type: int                  # trailing section + Properties XML type attr
    row_names: Tuple[str, ...]        # ("Plate",) Presto; ("A".."H") Duo
    speed_presets: Tuple[str, ...]    # valid speed preset UUIDs for this variant
    has_command_script: bool = False  # Duo emits an empty <CommandScript> element
    report_sections: Tuple[str, ...] = (
        "GeneralProtocol", "Carrier", "Dispensed", "StepsData"
    )


PRESTO_CONTRACT = InstrumentContract(
    instrument_type_id = KF_PRESTO_INSTRUMENT_TYPE_ID,
    params_type        = KF_PRESTO_INSTRUMENT_PARAMS_TYPE,
    row_names          = ("Plate",),
    speed_presets      = (SPEED_SLOW, SPEED_NORMAL, SPEED_FAST, SPEED_VFAST, SPEED_MAX),
)

DUO_CONTRACT = InstrumentContract(
    instrument_type_id = KF_DUO_INSTRUMENT_TYPE_ID,
    params_type        = KF_DUO_INSTRUMENT_PARAMS_TYPE,
    row_names          = _DUO_ROW_NAMES,
    speed_presets      = (DUO_SPEED_SLOW, DUO_SPEED_NORMAL, DUO_SPEED_FAST),
    has_command_script = True,
    report_sections    = (
        "GeneralProtocol", "SamplePlate", "Carrier",
        "Dispensed", "StepsData", "LotInfo",
    ),
)

_CONTRACTS: Dict[InstrumentVariant, InstrumentContract] = {
    InstrumentVariant.PRESTO: PRESTO_CONTRACT,
    InstrumentVariant.DUO:    DUO_CONTRACT,
}


# ── Protocol dataclasses ─────────────────────────────────────────────────────

@dataclass
class Reagent:
    """A reagent assigned to a plate group (whole-plate for Presto, per-row for Duo).

    Both Presto and Duo ``.bdz`` files populate ``<Containers>`` with reagent
    names and volumes.  For Presto the Container maps to a whole-plate group;
    for Duo it maps to a per-row group.
    """
    name: str
    volume_ul: float
    color: str = "ffff0000"           # ARGB hex (red)
    reagent_type: str = "Other"
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class WellGroup:
    """One ``<Group>`` in a plate's ``<Wells>`` — co-locates the group UUID with its reagent.

    Presto: one group per plate, name ``"Plate"``.
    Duo:    eight groups per plate, names ``"A"``–``"H"``.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    reagent: Optional[Reagent] = None


@dataclass(init=False)
class SamplePlate:
    """One sample plate position referenced by a protocol tip.

    A :class:`ProtocolTip` can reference multiple sample plates — each
    :class:`MixStep` selects which plate it acts on via ``sample_plate_index``.
    For single-step protocols the default of one plate is sufficient.

    Reagents are stored per well-group inside ``groups``:

    - Presto default: ``{"Plate": WellGroup()}`` — one whole-plate group.
    - Duo round-trip: ``{"A": WellGroup(...), ..., "H": WellGroup(...)}`` — eight row groups.
    - Duo from scratch: pass ``groups={"A": WellGroup(reagent=...), ...}`` for per-row
      reagents, or pass ``reagent=`` for a single reagent placed on row A.

    The convenience ``reagent=`` init argument populates ``groups["Plate"].reagent``
    (Presto) or appears on row A when the writer expands to Duo rows.
    Read back via the ``reagent`` property (returns the first non-None reagent found).
    """

    name: str
    plate_type_id: str
    id: str
    groups: Dict[str, WellGroup]

    def __init__(
        self,
        name: str = "Sample Plate",
        plate_type_id: str = PLATE_96_DWP,
        id: Optional[str] = None,
        groups: Optional[Dict[str, WellGroup]] = None,
        reagent: Optional[Reagent] = None,
    ) -> None:
        self.name = name
        self.plate_type_id = plate_type_id
        self.id = id if id is not None else str(uuid.uuid4())
        self.groups = groups if groups is not None else {"Plate": WellGroup()}
        if reagent is not None and not any(g.reagent for g in self.groups.values()):
            self.groups.setdefault("Plate", WellGroup()).reagent = reagent

    @property
    def reagent(self) -> Optional[Reagent]:
        """Primary reagent: row A for Duo, ``"Plate"`` group for Presto.

        Returns the first non-None reagent found, checking ``"A"`` then ``"Plate"``
        first for predictable priority ordering.
        """
        for key in ("A", "Plate"):
            if key in self.groups and self.groups[key].reagent:
                return self.groups[key].reagent
        return next((g.reagent for g in self.groups.values() if g.reagent), None)


@dataclass
class MixStep:
    """A Mix step — the primary step type in KingFisher protocols.

    The instrument shakes a sample plate against a tip plate, optionally
    releasing beads beforehand and collecting them afterwards.

    ``well_group_name`` controls which well group the step addresses:
    ``"Plate"`` = whole-plate (Presto always; Duo whole-plate steps).
    Row letter ``"A"``–``"H"`` = row-targeted step (Duo only).
    """

    name: str
    shake_duration_s: int = 30
    shake_speed: str = SPEED_NORMAL
    loop_count: int = 1
    pause_tip_position: str = "AboveSurface"

    release_beads_enabled: bool = True
    release_beads_duration_s: int = 0
    release_beads_speed: str = SPEED_SLOW

    collect_beads_enabled: bool = True
    collect_count: int = 3
    collect_time_s: int = 1

    heating_enabled: bool = False
    temperature: int = 37
    preheat: bool = False

    pause_enabled: bool = False
    pause_message: str = ""

    postmix_enabled: bool = False
    postmix_duration_s: int = 0
    postmix_speed: str = SPEED_NORMAL

    post_temperature_enabled: bool = False
    post_temperature: int = 10

    # Index into ProtocolTip.sample_plates for the plate this step acts on.
    sample_plate_index: int = 0

    # Well group targeted by this step.  "Plate" = whole-plate (Presto default and Duo
    # whole-plate); row letter "A"–"H" for Duo row-targeted steps.
    well_group_name: str = "Plate"


@dataclass
class ProtocolTip:
    """One tip position (Tip1, Tip2, ...) in the protocol.

    Each Tip contains a sequence of Mix steps and a list of sample plates.
    Each :class:`MixStep` selects which sample plate it acts on via
    ``sample_plate_index``.  The default is one auto-generated plate.

    Example — three-plate purification::

        ProtocolTip(
            name="Tip1",
            sample_plates=[
                SamplePlate("MagBind", reagent=Reagent("Binding Buffer", 825)),
                SamplePlate("Wash",    reagent=Reagent("Wash Buffer", 900)),
                SamplePlate("Elution", reagent=Reagent("Elution Buffer", 50)),
            ],
            steps=[
                MixStep(name="Bind",  shake_duration_s=300, sample_plate_index=0),
                MixStep(name="Wash",  shake_duration_s=120, sample_plate_index=1),
                MixStep(name="Elute", shake_duration_s=60,  sample_plate_index=2),
            ],
        )
    """

    name: str
    steps: List[MixStep] = field(default_factory=list)

    # Sample plates referenced by this tip; one per logical plate position.
    sample_plates: List[SamplePlate] = field(
        default_factory=lambda: [SamplePlate()]
    )

    # Tip plate identifiers — auto-generated; type controls PlateLayout plateTypeID.
    tip_plate_id:      str = field(default_factory=lambda: str(uuid.uuid4()))
    tip_groups:        Dict[str, WellGroup] = field(
        default_factory=lambda: {"Plate": WellGroup()}
    )
    tip_id:            Optional[str] = None  # None → auto-resolved in write_bdz from variant + plate_type_id
    tip_persistent_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    plate_type_id:     str = PLATE_96_DWP   # tip plate type


@dataclass
class BdzProtocol:
    """A complete KingFisher protocol, readable/writable as .bdz.

    Example — minimal single-step protocol::

        from pylabrobot.particle_processing.kingfisher.bdz import (
            BdzProtocol, ProtocolTip, MixStep, write_bdz
        )

        protocol = BdzProtocol(
            name="my-wash",
            tips=[
                ProtocolTip(
                    name="Tip1",
                    steps=[MixStep(name="Wash1", shake_duration_s=60)],
                )
            ],
        )
        bdz_bytes = write_bdz(protocol)
        with open("my-wash.bdz", "wb") as f:
            f.write(bdz_bytes)
    """

    name: str
    tips: List[ProtocolTip] = field(default_factory=list)

    protocol_id: str   = field(default_factory=lambda: str(uuid.uuid4()))
    run_id:      str   = field(default_factory=lambda: str(uuid.uuid4()))
    creator:     str   = "pylabrobot"
    kit_name:    str   = ""
    description: str   = ""
    is_executable: bool = True

    # ISO 8601 UTC timestamp; auto-generated if None
    timestamp: Optional[str] = None

    variant: InstrumentVariant = InstrumentVariant.PRESTO


# ── Low-level binary helpers ──────────────────────────────────────────────────

def _read_one_gzip_stream(data: bytes, start: int):
    """Read exactly one gzip stream using raw DEFLATE (avoids multi-stream issues).

    Returns (decompressed_bytes, end_offset) where end_offset is the byte
    position immediately after the 8-byte gzip trailer of this stream.
    """
    if data[start:start+2] != b"\x1f\x8b":
        raise ValueError(f"Expected gzip magic at offset {start}, got {data[start:start+2].hex()}")
    flags = data[start + 3]
    pos   = start + 10      # skip 10-byte gzip header
    if flags & 4:
        xlen = struct.unpack_from("<H", data, pos)[0]; pos += 2 + xlen
    if flags & 8:
        while data[pos] != 0: pos += 1
        pos += 1
    if flags & 16:
        while data[pos] != 0: pos += 1
        pos += 1
    if flags & 2:
        pos += 2
    dobj    = zlib.decompressobj(wbits=-15)
    content = dobj.decompress(data[pos:])
    end     = len(data) - len(dobj.unused_data) + 8   # + 8-byte gzip trailer
    return content, end


def _gzip_compress(data: bytes) -> bytes:
    """Gzip-compress data using the same header parameters as BindIt.

    BindIt uses: CM=08 FLG=00 MTIME=00000000 XFL=04 OS=00.
    The XFL=04 byte indicates "fastest compression" (level ~1–3).
    """
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, compresslevel=4) as gf:
        gf.write(data)
    out    = bytearray(buf.getvalue())
    out[8] = GZIP_XFL_BYTE
    out[9] = GZIP_OS_BYTE
    return bytes(out)


def _build_header(file_size: int, s1_compressed_size: int) -> bytes:
    """Build the 61-byte BindIt header."""
    vendor_b  = BINDIT_VENDOR.encode("ascii")
    version_b = BINDIT_VERSION.encode("ascii")
    out = bytearray()
    out += BINDIT_MAGIC
    out += struct.pack("<H", 1)             # format_version
    out += struct.pack("<H", 0x000A)        # constant
    out += struct.pack("<I", 1)             # constant
    out += struct.pack("<I", 27)            # constant
    out += struct.pack("<H", len(vendor_b))
    out += vendor_b
    out += struct.pack("<H", len(version_b))
    out += version_b
    out += struct.pack("<H", 11)            # constant
    out += struct.pack("<I", 1)             # constant
    out += struct.pack("<I", file_size - 63)  # content size from byte 63 to EOF
    out += b"\x00\x00\x00"                 # 3 zero bytes (constant)
    out += b"\x01"                         # constant byte
    out += struct.pack("<I", s1_compressed_size)
    assert len(out) == 61, f"Header must be 61 bytes, got {len(out)}"
    return bytes(out)


def _build_trailing_section(name: str, contract: InstrumentContract) -> bytes:
    """Build the trailing section (after stream #2)."""
    name_b = name.encode("ascii")
    out = bytearray()
    out += struct.pack("<I", 2)                  # constant
    out += struct.pack("<I", len(name_b) + 24)   # size indicator
    out += struct.pack("<I", contract.params_type)
    out += b"\x00" * 14                          # 14 zero bytes
    out += struct.pack("<I", 1)                  # constant
    out += struct.pack("<H", len(name_b))
    out += name_b
    out += b"\x01\x00\x01\x00" + b"\x00" * 6    # 10 constant trailing bytes
    return bytes(out)


# ── XML builders ─────────────────────────────────────────────────────────────

def _build_properties_xml(protocol: BdzProtocol, contract: InstrumentContract) -> bytes:
    """Generate the Properties XML (gzip stream #1)."""
    ts = protocol.timestamp or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.0000000Z"
    )
    xml = (
        f'<Properties version="1">'
        f'<ExportedObject name="{protocol.name}" id="{protocol.protocol_id}">'
        f"<InstrumentTypeId>{contract.instrument_type_id}</InstrumentTypeId>"
        f"<CreatorName>{protocol.creator}</CreatorName>"
        f"<Timestamp>{ts}</Timestamp>"
        f"<ExecutionTime>0001-01-01T00:00:00</ExecutionTime>"
        f"</ExportedObject>"
        f"<Flags><FactoryData>false</FactoryData></Flags>"
        f'<InstrumentParameters type="{contract.params_type}" '
        f'oemTypeId="00000000-0000-0000-0000-000000000000">'
        f"<ProtocolType>1</ProtocolType>"
        f"</InstrumentParameters>"
        f"</Properties>"
    )
    return xml.encode("utf-8")


def _iso_duration(seconds: int) -> str:
    """Convert seconds to ISO 8601 duration string (e.g. PT30S, PT1M30S)."""
    if seconds == 0:
        return "PT0S"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    parts = "PT"
    if h:
        parts += f"{h}H"
    if m:
        parts += f"{m}M"
    if s:
        parts += f"{s}S"
    return parts


def _build_mix_step_xml(step: MixStep, sample_plate_id: str) -> ET.Element:
    """Build the <Mix> XML element for one mix step."""
    mix_el = ET.Element("Mix", name=step.name, enabled="true")
    ET.SubElement(mix_el, "Image").text = "Mix"

    ET.SubElement(mix_el, "Precollect", enabled="false")

    rel = ET.SubElement(mix_el, "ReleaseBeads",
                        enabled=str(step.release_beads_enabled).lower())
    ET.SubElement(rel, "Duration").text = _iso_duration(step.release_beads_duration_s)
    ET.SubElement(rel, "Speed").text    = step.release_beads_speed

    mixing_el = ET.SubElement(mix_el, "Mixing")
    shakes_el = ET.SubElement(mixing_el, "Shakes")
    ET.SubElement(
        shakes_el, "Shake",
        duration=_iso_duration(step.shake_duration_s),
        speed=step.shake_speed,
    )
    ET.SubElement(mixing_el, "LoopCount").text        = str(step.loop_count)
    ET.SubElement(mixing_el, "PauseTipPosition").text = step.pause_tip_position

    pause_el = ET.SubElement(mix_el, "Pause",
                             enabled=str(step.pause_enabled).lower())
    ET.SubElement(pause_el, "Message").text = step.pause_message

    heat_el = ET.SubElement(mix_el, "Heating",
                            enabled=str(step.heating_enabled).lower())
    ET.SubElement(heat_el, "Temperature").text = str(step.temperature)
    ET.SubElement(heat_el, "Preheat").text     = str(step.preheat).lower()

    post_el = ET.SubElement(mix_el, "Postmix",
                            enabled=str(step.postmix_enabled).lower())
    ET.SubElement(post_el, "Duration").text = _iso_duration(step.postmix_duration_s)
    ET.SubElement(post_el, "Speed").text    = step.postmix_speed

    col_el = ET.SubElement(mix_el, "CollectBeads",
                           enabled=str(step.collect_beads_enabled).lower())
    ET.SubElement(col_el, "Count").text       = str(step.collect_count)
    ET.SubElement(col_el, "CollectTime").text = _iso_duration(step.collect_time_s)

    post_temp = ET.SubElement(mix_el, "PostTemperature",
                              enabled=str(step.post_temperature_enabled).lower())
    ET.SubElement(post_temp, "Temperature").text = str(step.post_temperature)

    # Plate reference: sample plate for this step, well group targeted by this step
    plates_el = ET.SubElement(mix_el, "Plates")
    ET.SubElement(plates_el, "Plate",
                  id=sample_plate_id,
                  wellGroup=step.well_group_name)

    ET.SubElement(mix_el, "LegacyParameters")
    ET.SubElement(mix_el, "Steps")

    return mix_el


def _add_plate_wells(
    plate_el: ET.Element,
    groups: Dict[str, WellGroup],
    contract: InstrumentContract,
) -> None:
    """Add ``<Wells>`` children to a ``<Plate>`` element.

    Iterates ``contract.row_names`` to determine the group layout:

    - Presto (``row_names=("Plate",)``): one group covering all 8 rows.
    - Duo (``row_names=("A".."H")``): 8 individual row groups (1 row each).

    Group UUIDs come from *groups* when available.  For Duo protocols built
    from scratch with only a ``"Plate"`` default group, row A seeds its UUID
    from that group; remaining rows get fresh UUIDs.
    """
    wells_el = ET.SubElement(plate_el, "Wells")
    # Seed ID: use "Plate" group's UUID when expanding a default Presto plate to Duo rows
    seed_id = groups.get("Plate", WellGroup()).id
    multi_row = len(contract.row_names) > 1
    n_rows = "1" if multi_row else "8"

    for row_idx, row_name in enumerate(contract.row_names):
        if row_name in groups:
            wg = groups[row_name]
        elif row_idx == 0 and "Plate" in groups and multi_row:
            # Expand default "Plate" group to Duo row A, seeding UUID from "Plate"
            wg = WellGroup(id=seed_id, reagent=groups["Plate"].reagent)
        else:
            wg = WellGroup()
        grp = ET.SubElement(wells_el, "Group", ID=wg.id, name=row_name)
        ET.SubElement(grp, "Region", x="0", y=str(row_idx), columns="12", rows=n_rows)


def _build_exported_data_xml(protocol: BdzProtocol, contract: InstrumentContract) -> bytes:
    """Generate the ExportedData XML (gzip stream #2)."""
    run_id = protocol.run_id

    root = ET.Element(
        "ExportedData",
        attrib={
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
        },
    )
    proto_wrapper = ET.SubElement(root, "Protocol")
    run_el = ET.SubElement(proto_wrapper, "Run", ID=run_id)

    # ── PlateLayout ──
    layout_el = ET.SubElement(
        run_el, "PlateLayout",
        ID=str(uuid.uuid4()),
        Name="No name",
    )
    ET.SubElement(layout_el, "Description")
    ET.SubElement(layout_el, "PlateTemplates")
    plates_layout = ET.SubElement(layout_el, "Plates")

    seen_plate_ids: set = set()
    for tip in protocol.tips:
        # Tip plate
        if tip.tip_plate_id not in seen_plate_ids:
            seen_plate_ids.add(tip.tip_plate_id)
            plate_el = ET.SubElement(
                plates_layout, "Plate",
                id=tip.tip_plate_id, name="Tip Plate",
                plateTypeID=tip.plate_type_id,
            )
            _add_plate_wells(plate_el, tip.tip_groups, contract)
        # Sample plates
        for sp in tip.sample_plates:
            if sp.id in seen_plate_ids:
                continue
            seen_plate_ids.add(sp.id)
            plate_el = ET.SubElement(
                plates_layout, "Plate",
                id=sp.id, name=sp.name, plateTypeID=sp.plate_type_id,
            )
            _add_plate_wells(plate_el, sp.groups, contract)

    # ── RunSpecificInformation ──
    rsi = ET.SubElement(run_el, "RunSpecificInformation", Locked="false")
    si  = ET.SubElement(rsi, "SampleInformation")
    dim = ET.SubElement(si, "Dimensions")
    ET.SubElement(dim, "Width").text  = "0"
    ET.SubElement(dim, "Height").text = "0"
    ET.SubElement(si, "Samples")
    ci = ET.SubElement(rsi, "ConsumableInformation")
    ET.SubElement(ci, "Consumables")

    # ── RunDef ──
    run_def = ET.SubElement(run_el, "RunDef")
    ui_rel  = ET.SubElement(run_def, "UIResultRelations")
    exec_el = ET.SubElement(ui_rel, "ExecutedRunInformation")
    ET.SubElement(exec_el, "StartExecution")
    ET.SubElement(exec_el, "ExecutorName")
    ET.SubElement(exec_el, "Warnings")
    ET.SubElement(exec_el, "Errors")
    instr_el = ET.SubElement(ui_rel, "InstrumentInformation")
    ET.SubElement(instr_el, "InstrumentType").text = "00000000-0000-0000-0000-000000000000"
    ET.SubElement(instr_el, "eInstrumentName")
    ET.SubElement(instr_el, "eInstrumentVersion")
    ET.SubElement(instr_el, "eInstrumentSerialNumber")
    sw_el = ET.SubElement(ui_rel, "SoftwareInformation")
    ET.SubElement(sw_el, "Version")
    ET.SubElement(ui_rel, "LaboratoryInformation")

    # ── DXReports ──
    dx_el  = ET.SubElement(run_el, "DXReports")
    sel_el = ET.SubElement(dx_el, "SelectedReports")
    if contract.has_command_script:
        pip_rpt = ET.SubElement(sel_el, "Report", ReportType="PipettingReport")
        pip_sec = ET.SubElement(pip_rpt, "SelectedSections")
        for s in ("GeneralPipetting", "Consumables", "SamplePlate",
                  "SampleList", "Carrier", "Dispensed"):
            ET.SubElement(pip_sec, "ReportSection").text = s
    rpt_el = ET.SubElement(sel_el, "Report", ReportType="ProtocolReport")
    sec_el = ET.SubElement(rpt_el, "SelectedSections")
    for section in contract.report_sections:
        ET.SubElement(sec_el, "ReportSection").text = section

    # ── Protocol ──
    is_exec = "true" if protocol.is_executable else "false"
    p_el    = ET.SubElement(
        run_el, "Protocol",
        name=protocol.name,
        ID=run_id,
        locked="false",
        IsExecutable=is_exec,
        enabled="true",
    )
    ET.SubElement(p_el, "KitName").text      = protocol.kit_name
    ET.SubElement(p_el, "RemovePlateMessage")
    ET.SubElement(p_el, "ProtocolType").text = "Normal"

    # Containers — one per WellGroup that has a reagent, across all sample plates
    containers_el = ET.SubElement(p_el, "Containers")
    for tip in protocol.tips:
        for sp in tip.sample_plates:
            for _group_name, wg in sp.groups.items():
                if wg.reagent is None:
                    continue
                reagent = wg.reagent
                c_el = ET.SubElement(containers_el, "Container",
                                     id=reagent.id, groupId=wg.id)
                contents_el = ET.SubElement(c_el, "Contents")
                ET.SubElement(
                    contents_el, "Reagent",
                    id=str(uuid.uuid5(uuid.NAMESPACE_DNS, reagent.id)),
                    name=reagent.name,
                    volume=str(int(reagent.volume_ul)),
                    color=reagent.color,
                    type=reagent.reagent_type,
                )

    ET.SubElement(p_el, "LegacySpeeds")
    ET.SubElement(p_el, "InstrumentTypeID").text = contract.instrument_type_id
    ET.SubElement(p_el, "Description").text      = protocol.description

    # ── Steps / Tips ──
    steps_el = ET.SubElement(p_el, "Steps")
    for tip in protocol.tips:
        tip_type = _TIP_BY_VARIANT_AND_PLATE.get((protocol.variant, tip.plate_type_id))
        actual_tip_id = tip.tip_id or (tip_type.id if tip_type else str(uuid.uuid4()))
        tip_el = ET.SubElement(
            steps_el, "Tip",
            name=tip.name,
            id=actual_tip_id,
            persistentID=tip.tip_persistent_id,
            enabled="true",
        )
        tip_plates_el = ET.SubElement(tip_el, "Plates")
        ET.SubElement(tip_plates_el, "Plate",
                      id=tip.tip_plate_id, wellGroup="Plate")
        ET.SubElement(tip_plates_el, "Plate",
                      id=tip.tip_plate_id, wellGroup="Plate")
        ET.SubElement(tip_el, "LegacyParameters")

        tip_steps_el = ET.SubElement(tip_el, "Steps")
        for step in tip.steps:
            idx = min(step.sample_plate_index, len(tip.sample_plates) - 1)
            sample_plate_id = tip.sample_plates[idx].id
            tip_steps_el.append(_build_mix_step_xml(step, sample_plate_id))

    # ── RunLog ──
    ET.SubElement(root, "RunLog")

    # ── CommandScript (Duo only — empty; instrument compiles from steps) ──
    if contract.has_command_script:
        cs_el = ET.SubElement(root, "CommandScript")
        cs_el.text = ""

    return ET.tostring(root, encoding="unicode").encode("utf-8")


# ── XML parsers ───────────────────────────────────────────────────────────────

def _parse_iso_duration(s: str) -> int:
    """Parse an ISO 8601 duration string to total seconds. E.g. 'PT1M30S' → 90."""
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s.strip())
    if not m:
        return 0
    h, mi, sec = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + sec


def _parse_protocol(props_xml: bytes, exported_xml: bytes) -> BdzProtocol:
    """Reconstruct a BdzProtocol from the two decompressed gzip stream payloads."""
    # ── Properties XML (stream #1) ──
    props = ET.fromstring(props_xml)
    obj   = props.find("ExportedObject")
    name        = obj.get("name", "") if obj is not None else ""
    protocol_id = obj.get("id", str(uuid.uuid4())) if obj is not None else str(uuid.uuid4())
    creator     = (props.findtext("ExportedObject/CreatorName") or "").strip()
    timestamp   = (props.findtext("ExportedObject/Timestamp") or "").strip() or None

    # Detect variant from InstrumentParameters[@type]
    ip_el = props.find("InstrumentParameters")
    params_type = int(ip_el.get("type", "0")) if ip_el is not None else 0
    if params_type == KF_DUO_INSTRUMENT_PARAMS_TYPE:
        variant = InstrumentVariant.DUO
    else:
        variant = InstrumentVariant.PRESTO
    contract = _CONTRACTS[variant]

    # ── ExportedData XML (stream #2) ──
    exp = ET.fromstring(exported_xml)
    run_el = exp.find(".//Run")
    run_id = run_el.get("ID", str(uuid.uuid4())) if run_el is not None else str(uuid.uuid4())

    proto_el      = exp.find(".//Protocol")
    kit_name      = (proto_el.findtext("KitName") or "").strip() if proto_el is not None else ""
    description   = (proto_el.findtext("Description") or "").strip() if proto_el is not None else ""
    is_executable = (proto_el.get("IsExecutable", "true")).lower() == "true" if proto_el is not None else True

    # Build plate_id → (name, plate_type_id, groups: Dict[str, WellGroup]) from PlateLayout.
    # All <Group> elements are captured so Duo row group UUIDs are preserved on round-trips.
    plate_map: Dict[str, tuple] = {}
    for plate_el in exp.findall(".//PlateLayout/Plates/Plate"):
        pid   = plate_el.get("id", "")
        pname = plate_el.get("name", "Plate")
        ptype = plate_el.get("plateTypeID", PLATE_96_DWP)
        groups: Dict[str, WellGroup] = {}
        for grp_el in plate_el.findall(".//Group"):
            gname = grp_el.get("name", "Plate")
            gid   = grp_el.get("ID", str(uuid.uuid4()))
            groups[gname] = WellGroup(id=gid)
        if not groups:
            groups = {"Plate": WellGroup()}
        plate_map[pid] = (pname, ptype, groups)

    # Build group_id → Reagent map from Containers
    reagent_by_group: Dict[str, Reagent] = {}
    for c_el in exp.findall(".//Containers/Container"):
        r_el = c_el.find("Contents/Reagent")
        if r_el is not None:
            group_id = c_el.get("groupId", "")
            reagent_by_group[group_id] = Reagent(
                name=r_el.get("name", ""),
                volume_ul=float(r_el.get("volume", "0")),
                color=r_el.get("color", "ffff0000"),
                reagent_type=r_el.get("type", "Other"),
                id=r_el.get("id", str(uuid.uuid4())),
            )

    def _bool(el, attr: str, default: bool = False) -> bool:
        if el is None: return default
        return (el.get(attr, str(default))).lower() == "true"

    def _text_int(el, tag: str, default: int = 0) -> int:
        if el is None: return default
        t = el.findtext(tag)
        return int(t) if t and t.strip().isdigit() else default

    def _text_str(el, tag: str, default: str = "") -> str:
        if el is None: return default
        return (el.findtext(tag) or default).strip()

    # Parse each Tip
    tips: List[ProtocolTip] = []
    for tip_el in exp.findall(".//Protocol/Steps/Tip"):
        tip_name          = tip_el.get("name", "")
        tip_id            = tip_el.get("id", str(uuid.uuid4()))
        tip_persistent_id = tip_el.get("persistentID", str(uuid.uuid4()))

        tip_plates_xml = tip_el.findall("Plates/Plate")
        tip_plate_id   = tip_plates_xml[0].get("id", str(uuid.uuid4())) if tip_plates_xml else str(uuid.uuid4())
        _tip_pname, tip_type_id, tip_plate_groups = plate_map.get(
            tip_plate_id, ("Tip Plate", PLATE_96_DWP, {"Plate": WellGroup()})
        )

        # Collect all unique sample plate IDs referenced across steps, in order
        sample_plate_ids_ordered: List[str] = []
        sample_plate_id_set: set = set()
        for mix_el in tip_el.findall("Steps/Mix"):
            sp_el = mix_el.find("Plates/Plate")
            if sp_el is not None:
                pid = sp_el.get("id", "")
                if pid and pid not in sample_plate_id_set:
                    sample_plate_ids_ordered.append(pid)
                    sample_plate_id_set.add(pid)

        if not sample_plate_ids_ordered:
            sample_plate_ids_ordered = [str(uuid.uuid4())]

        # Build SamplePlate objects, filling in reagents from reagent_by_group
        sample_plates: List[SamplePlate] = []
        for pid in sample_plate_ids_ordered:
            pname, ptype, groups = plate_map.get(
                pid, ("Sample Plate", PLATE_96_DWP, {"Plate": WellGroup()})
            )
            # Attach reagents to groups by matching group UUID
            filled_groups: Dict[str, WellGroup] = {}
            for gname, wg in groups.items():
                reagent = reagent_by_group.get(wg.id)
                filled_groups[gname] = WellGroup(id=wg.id, reagent=reagent)
            sample_plates.append(SamplePlate(
                name=pname,
                plate_type_id=ptype,
                id=pid,
                groups=filled_groups,
            ))

        # Map plate_id → index for step assignment
        plate_id_to_idx = {sp.id: i for i, sp in enumerate(sample_plates)}

        steps: List[MixStep] = []
        for mix_el in tip_el.findall("Steps/Mix"):
            shake_el = mix_el.find("Mixing/Shakes/Shake")
            rel_el   = mix_el.find("ReleaseBeads")
            col_el   = mix_el.find("CollectBeads")
            heat_el  = mix_el.find("Heating")
            pause_el = mix_el.find("Pause")
            post_el  = mix_el.find("Postmix")
            ptemp_el = mix_el.find("PostTemperature")

            sp_el          = mix_el.find("Plates/Plate")
            sp_id          = sp_el.get("id", "") if sp_el is not None else ""
            well_group_name = sp_el.get("wellGroup", "Plate") if sp_el is not None else "Plate"
            sp_idx         = plate_id_to_idx.get(sp_id, 0)

            steps.append(MixStep(
                name=mix_el.get("name", ""),
                shake_duration_s=_parse_iso_duration(shake_el.get("duration", "PT0S")) if shake_el is not None else 0,
                shake_speed=shake_el.get("speed", SPEED_NORMAL) if shake_el is not None else SPEED_NORMAL,
                loop_count=_text_int(mix_el.find("Mixing"), "LoopCount", 1),
                pause_tip_position=_text_str(mix_el.find("Mixing"), "PauseTipPosition", "AboveSurface"),
                release_beads_enabled=_bool(rel_el, "enabled", True),
                release_beads_duration_s=_parse_iso_duration(_text_str(rel_el, "Duration", "PT0S")),
                release_beads_speed=_text_str(rel_el, "Speed", SPEED_SLOW),
                collect_beads_enabled=_bool(col_el, "enabled", True),
                collect_count=_text_int(col_el, "Count", 3),
                collect_time_s=_parse_iso_duration(_text_str(col_el, "CollectTime", "PT1S")),
                heating_enabled=_bool(heat_el, "enabled"),
                temperature=_text_int(heat_el, "Temperature", 37),
                preheat=_bool(heat_el, "enabled") and (_text_str(heat_el, "Preheat", "false") == "true"),
                pause_enabled=_bool(pause_el, "enabled"),
                pause_message=_text_str(pause_el, "Message"),
                postmix_enabled=_bool(post_el, "enabled"),
                postmix_duration_s=_parse_iso_duration(_text_str(post_el, "Duration", "PT0S")),
                postmix_speed=_text_str(post_el, "Speed", SPEED_NORMAL),
                post_temperature_enabled=_bool(ptemp_el, "enabled"),
                post_temperature=_text_int(ptemp_el, "Temperature", 10),
                sample_plate_index=sp_idx,
                well_group_name=well_group_name,
            ))

        tips.append(ProtocolTip(
            name=tip_name,
            steps=steps,
            sample_plates=sample_plates,
            tip_plate_id=tip_plate_id,
            tip_groups=tip_plate_groups,
            tip_id=tip_id,
            tip_persistent_id=tip_persistent_id,
            plate_type_id=tip_type_id,
        ))

    return BdzProtocol(
        name=name,
        tips=tips,
        protocol_id=protocol_id,
        run_id=run_id,
        creator=creator,
        kit_name=kit_name,
        description=description,
        is_executable=is_executable,
        timestamp=timestamp,
        variant=variant,
    )


# ── Public API ────────────────────────────────────────────────────────────────

def write_bdz(protocol: BdzProtocol) -> bytes:
    """Serialize *protocol* to .bdz binary format.

    Returns raw bytes suitable for writing to a file or uploading to the
    instrument.  The output passes the instrument's validation (error code 24
    = "Invalid protocol file" should not occur).
    """
    contract = _CONTRACTS[protocol.variant]
    props_xml    = _build_properties_xml(protocol, contract)
    exported_xml = _build_exported_data_xml(protocol, contract)

    stream1  = _gzip_compress(props_xml)
    stream2  = _gzip_compress(exported_xml)
    trailing = _build_trailing_section(protocol.name, contract)

    separator = STREAM_SEP_PREFIX + struct.pack("<I", len(stream2))
    body      = stream1 + separator + stream2 + trailing
    file_size = 61 + len(body)

    header = _build_header(file_size, len(stream1))
    return header + body


def read_bdz(data: bytes) -> BdzProtocol:
    """Parse a .bdz file and return a BdzProtocol.

    Raises ValueError if the magic bytes don't match or the structure is malformed.

    Example — load, modify, re-write::

        protocol = read_bdz(Path("existing.bdz").read_bytes())
        protocol.tips[0].steps[0].shake_duration_s = 90
        Path("modified.bdz").write_bytes(write_bdz(protocol))
    """
    if data[:4] != BINDIT_MAGIC:
        raise ValueError(f"Not a BindIt .bdz file (bad magic: {data[:4].hex()!r})")

    props_xml, s1_end = _read_one_gzip_stream(data, 61)

    sep = data[s1_end: s1_end + 8]
    if sep[:4] != STREAM_SEP_PREFIX:
        raise ValueError(f"Unexpected inter-stream separator prefix: {sep[:4].hex()}")

    exported_xml, _ = _read_one_gzip_stream(data, s1_end + 8)
    return _parse_protocol(props_xml, exported_xml)
