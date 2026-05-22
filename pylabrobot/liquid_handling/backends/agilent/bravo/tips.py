from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
import uuid

import yaml

from pylabrobot.liquid_handling.backends.agilent.bravo.types import HeadType


@dataclass(frozen=True)
class TipDefinition:
    tip_id: str
    capacity_ul: float
    label: str
    length_mm: float | None
    source: str
    model_3d: str | None = None
    compatible_heads: tuple[str, ...] = ()


_SHORT_TIP_OPTIONS = (
    TipDefinition("st_10ul", 10.0, "10 uL", 19.9, "measured", "/labware-assets/tips/d10.gltf"),
    TipDefinition("st_15ul", 15.0, "15 uL", None, "vendor-source-option", "/labware-assets/tips/d10.gltf"),
    TipDefinition("st_30ul", 30.0, "30 uL", 26.1, "vendor-source-comment", "/labware-assets/tips/d30.gltf"),
    TipDefinition("st_50ul", 50.0, "50 uL", None, "vendor-source-option", "/labware-assets/tips/d30.gltf"),
    TipDefinition("st_51ul", 51.0, "51 uL", None, "vendor-source-option", "/labware-assets/tips/d30.gltf"),
    TipDefinition("st_70ul", 70.0, "70 uL", None, "vendor-source-option", "/labware-assets/tips/d30.gltf"),
)

_LONG_TIP_OPTIONS = (
    TipDefinition("lt_200ul", 200.0, "200 uL", None, "vendor-source-option"),
    TipDefinition("lt_250ul", 250.0, "250 uL", None, "vendor-source-default"),
)

_PIN_TOOL_OPTIONS = (
    TipDefinition("pin_fp1cb", 0.0, "FP1CB", None, "vendor-source-option"),
    TipDefinition("pin_fp1n", 0.0, "FP1N", None, "vendor-source-option"),
    TipDefinition("pin_fp1t", 0.0, "FP1T", None, "vendor-source-option"),
)

_HEAD_TIP_OPTIONS: dict[HeadType, tuple[TipDefinition, ...]] = {
    HeadType.HT_384_D_70: _SHORT_TIP_OPTIONS,
    HeadType.HT_384_D_70_S2: _SHORT_TIP_OPTIONS,
    HeadType.HT_96_D_70: _SHORT_TIP_OPTIONS,
    HeadType.HT_96_D_70_S2: _SHORT_TIP_OPTIONS,
    HeadType.HT_16_D_ST: _SHORT_TIP_OPTIONS,
    HeadType.HT_8_D_LT: _LONG_TIP_OPTIONS,
    HeadType.HT_96_D_200: _LONG_TIP_OPTIONS,
    HeadType.HT_96_D_200_S2: _LONG_TIP_OPTIONS,
    HeadType.HT_96_PINTOOL: _PIN_TOOL_OPTIONS,
    HeadType.HT_384_PINTOOL: _PIN_TOOL_OPTIONS,
    HeadType.HT_1536_PINTOOL: _PIN_TOOL_OPTIONS,
}
_STORE_PATH = Path(__file__).resolve().parents[1] / "config" / "tips.yaml"


def _is_close_capacity(value: object, capacity_ul: float) -> bool:
    try:
        return abs(float(value) - float(capacity_ul)) < 1e-6
    except Exception:
        return False


def _iter_all_tip_definitions() -> Iterable[TipDefinition]:
    seen: set[str] = set()
    for tip in load_tip_definitions():
        if tip.tip_id in seen:
            continue
        seen.add(tip.tip_id)
        yield tip
    for items in _HEAD_TIP_OPTIONS.values():
        for tip in items:
            if tip.tip_id in seen:
                continue
            seen.add(tip.tip_id)
            yield tip


def _normalize_head_type(head_type: HeadType | str) -> HeadType | None:
    if isinstance(head_type, HeadType):
        return head_type
    try:
        return HeadType[str(head_type)]
    except Exception:
        return None


def get_tip_definitions_for_head(head_type: HeadType | str) -> list[TipDefinition]:
    normalized = _normalize_head_type(head_type)
    if normalized is None:
        return []
    loaded = [
        tip for tip in load_tip_definitions()
        if not tip.compatible_heads or normalized.name in tip.compatible_heads
    ]
    if loaded:
        loaded.sort(key=lambda tip: (float(tip.capacity_ul or 0.0), tip.label.lower(), tip.tip_id))
        return loaded
    return list(_HEAD_TIP_OPTIONS.get(normalized, ()))


def get_tip_definition(head_type: HeadType | str, tip_id_or_capacity: str | float | int | None) -> TipDefinition | None:
    if tip_id_or_capacity is None:
        return None
    for tip in get_tip_definitions_for_head(head_type):
        if str(tip.tip_id) == str(tip_id_or_capacity):
            return tip
        if _is_close_capacity(tip_id_or_capacity, tip.capacity_ul):
            return tip
    return None


def get_tip_definition_by_id(tip_id: str | None) -> TipDefinition | None:
    if not tip_id:
        return None
    for tip in _iter_all_tip_definitions():
        if tip.tip_id == str(tip_id):
            return tip
    return None


def get_tip_length_mm(head_type: HeadType | str, tip_id_or_capacity: str | float | int | None) -> float | None:
    tip = get_tip_definition(head_type, tip_id_or_capacity)
    return None if tip is None else tip.length_mm


def get_tip_capacity_ul(head_type: HeadType | str, tip_id_or_capacity: str | float | int | None) -> float:
    tip = get_tip_definition(head_type, tip_id_or_capacity)
    if tip is not None:
        return float(tip.capacity_ul)
    try:
        return float(tip_id_or_capacity or 0.0)
    except Exception:
        return 0.0


def get_tip_id_for_capacity(head_type: HeadType | str, capacity_ul: float | None) -> str | None:
    tip = get_tip_definition(head_type, capacity_ul)
    return None if tip is None else tip.tip_id


def get_default_tip_id_for_head(head_type: HeadType | str) -> str | None:
    normalized = _normalize_head_type(head_type)
    if normalized is None:
        return None
    options = get_tip_definitions_for_head(normalized)
    if not options:
        return None
    preferred_capacity = 200.0 if normalized in {
        HeadType.HT_8_D_LT,
        HeadType.HT_96_D_200,
        HeadType.HT_96_D_200_S2,
    } else 30.0
    match = get_tip_definition(normalized, preferred_capacity)
    return match.tip_id if match is not None else options[0].tip_id


def serialize_tip_options_for_head(head_type: HeadType | str) -> list[dict[str, object]]:
    return [
        {
            **asdict(tip),
        }
        for tip in get_tip_definitions_for_head(head_type)
    ]


def _default_store() -> dict[str, list[dict[str, object]]]:
    tips: list[dict[str, object]] = []
    seen: set[str] = set()
    for head_type, definitions in _HEAD_TIP_OPTIONS.items():
        for tip in definitions:
            if tip.tip_id in seen:
                continue
            seen.add(tip.tip_id)
            item = asdict(tip)
            compatible_heads = sorted(
                ht.name for ht, defs in _HEAD_TIP_OPTIONS.items()
                if any(d.tip_id == tip.tip_id for d in defs)
            )
            item["compatible_heads"] = compatible_heads
            tips.append(item)
    return {"tips": tips}


def _store_path() -> Path:
    return _STORE_PATH


def load_store() -> dict[str, list[dict[str, object]]]:
    path = _store_path()
    if not path.exists():
        store = _default_store()
        save_store(store)
        return store
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    store = {"tips": list(raw.get("tips", []) or [])}
    if not store["tips"]:
        store = _default_store()
        save_store(store)
    return store


def save_store(store: dict[str, list[dict[str, object]]]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    sorted_items = sorted(
        list(store.get("tips", []) or []),
        key=lambda item: (str(item.get("label") or "").lower(), float(item.get("capacity_ul") or 0.0), str(item.get("tip_id") or "")),
    )
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump({"tips": sorted_items}, fh, sort_keys=False)


def load_tip_definitions() -> list[TipDefinition]:
    tips: list[TipDefinition] = []
    for item in load_store().get("tips", []):
        tip_id = str(item.get("tip_id") or "").strip()
        if not tip_id:
            continue
        tips.append(
            TipDefinition(
                tip_id=tip_id,
                capacity_ul=float(item.get("capacity_ul") or 0.0),
                label=str(item.get("label") or tip_id),
                length_mm=None if item.get("length_mm") in {None, ""} else float(item.get("length_mm")),
                source=str(item.get("source") or "user"),
                model_3d=str(item.get("model_3d") or "") or None,
                compatible_heads=tuple(str(value) for value in list(item.get("compatible_heads") or [])),
            )
        )
    return tips


def list_tip_items() -> list[dict[str, object]]:
    return deepcopy(load_store().get("tips", []))


def create_tip_definition(payload: dict[str, object]) -> dict[str, object]:
    store = load_store()
    item = _normalize_tip_payload(payload, tip_id=str(payload.get("tip_id") or _make_id("tip")))
    _validate_tip_uniqueness(store["tips"], item)
    store["tips"].append(item)
    save_store(store)
    return deepcopy(item)


def patch_tip_definition(tip_id: str, payload: dict[str, object]) -> dict[str, object]:
    store = load_store()
    for index, item in enumerate(store["tips"]):
        if str(item.get("tip_id") or "") != str(tip_id):
            continue
        merged = deepcopy(item)
        merged.update(deepcopy(payload))
        normalized = _normalize_tip_payload(merged, tip_id=str(tip_id))
        _validate_tip_uniqueness(store["tips"], normalized, exclude_id=str(tip_id))
        store["tips"][index] = normalized
        save_store(store)
        return deepcopy(normalized)
    raise KeyError(tip_id)


def delete_tip_definition(tip_id: str) -> None:
    store = load_store()
    before = len(store["tips"])
    store["tips"] = [item for item in store["tips"] if str(item.get("tip_id") or "") != str(tip_id)]
    if len(store["tips"]) == before:
        raise KeyError(tip_id)
    save_store(store)


def _normalize_tip_payload(payload: dict[str, object], *, tip_id: str) -> dict[str, object]:
    normalized_tip_id = str(tip_id or "").strip()
    label = str(payload.get("label") or "").strip()
    if not normalized_tip_id:
        raise ValueError("tip_id is required")
    if not label:
        raise ValueError("label is required")
    compatible_heads = []
    for value in list(payload.get("compatible_heads") or []):
        text = str(value or "").strip()
        if not text:
            continue
        if _normalize_head_type(text) is None:
            raise ValueError(f"Unknown head type: {text}")
        compatible_heads.append(text)
    return {
        "tip_id": normalized_tip_id,
        "label": label,
        "capacity_ul": float(payload.get("capacity_ul") or 0.0),
        "length_mm": None if payload.get("length_mm") in {None, ""} else float(payload.get("length_mm")),
        "source": str(payload.get("source") or "user"),
        "model_3d": str(payload.get("model_3d") or "") or None,
        "compatible_heads": compatible_heads,
    }


def _validate_tip_uniqueness(items: list[dict[str, object]], item: dict[str, object], *, exclude_id: str | None = None) -> None:
    for existing in items:
        existing_id = str(existing.get("tip_id") or "")
        if exclude_id and existing_id == exclude_id:
            continue
        if existing_id == str(item.get("tip_id") or ""):
            raise ValueError(f"Tip ID '{item['tip_id']}' already exists")
        if str(existing.get("label") or "").strip().lower() == str(item.get("label") or "").strip().lower():
            raise ValueError(f"Tip label '{item['label']}' already exists")


def _make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"
