"""Editable liquid class and pipette technique store."""

from __future__ import annotations

import os
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from pylabrobot.liquid_handling.backends.agilent.bravo.tips import (
    get_default_tip_id_for_head,
    get_tip_capacity_ul,
    get_tip_definition,
    get_tip_definition_by_id,
    get_tip_id_for_capacity,
)

try:
    from pymongo import MongoClient
except ImportError:  # pragma: no cover
    MongoClient = None


_STORE_PATH = Path(__file__).resolve().parents[1] / "config" / "liquid_classes.yaml"


def _mongo_config() -> tuple[str, str, str, str]:
    uri = os.environ.get("PYBRAVO_LIQUID_MONGO_URI", os.environ.get("PYBRAVO_LABWARE_MONGO_URI", "")).strip()
    database = os.environ.get("PYBRAVO_LIQUID_MONGO_DB", os.environ.get("PYBRAVO_LABWARE_MONGO_DB", "")).strip()
    classes_collection = os.environ.get("PYBRAVO_LIQUID_MONGO_CLASS_COLLECTION", "liquid_classes").strip()
    techniques_collection = os.environ.get("PYBRAVO_LIQUID_MONGO_TECHNIQUE_COLLECTION", "pipette_techniques").strip()
    return uri, database, classes_collection, techniques_collection


def _mongo_enabled() -> bool:
    uri, database, classes_collection, _ = _mongo_config()
    return bool(uri and database and classes_collection and MongoClient is not None)


def _mongo_collections():
    uri, database, classes_collection, techniques_collection = _mongo_config()
    if not (uri and database and classes_collection):
        raise RuntimeError("Liquid class MongoDB is not configured")
    if MongoClient is None:
        raise RuntimeError("pymongo is not installed")
    client = MongoClient(uri, serverSelectionTimeoutMS=3000)
    db = client[database]
    return client, db[classes_collection], db[techniques_collection]


def _store_path() -> Path:
    configured = os.environ.get("PYBRAVO_LIQUID_CLASS_STORE_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return _STORE_PATH


def _empty_store() -> dict[str, Any]:
    return {
        "version": 1,
        "liquid_classes": [],
        "pipette_techniques": [],
    }


def _sorted_store(store: dict[str, Any]) -> dict[str, Any]:
    store["liquid_classes"] = sorted(
        list(store.get("liquid_classes", []) or []),
        key=lambda item: (
            str(item.get("machine_id") or "").lower(),
            str(item.get("head_type") or "").lower(),
            str(item.get("tip_id") or "").lower(),
            float(item.get("tip_capacity_ul") or 0.0),
            str(item.get("name") or "").lower(),
        ),
    )
    store["pipette_techniques"] = sorted(
        list(store.get("pipette_techniques", []) or []),
        key=lambda item: str(item.get("name") or "").lower(),
    )
    return store


def _write_store(store: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(_sorted_store(store), fh, sort_keys=False)


def load_store() -> dict[str, Any]:
    if _mongo_enabled():
        try:
            store = _load_store_from_mongo()
            _write_store(store)
            return store
        except Exception:
            pass

    path = _store_path()
    if path.exists():
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        store = _empty_store()
        store.update(raw)
        store["liquid_classes"] = [
            _normalize_liquid_class_payload(item, liquid_class_id=str(item.get("liquid_class_id") or _make_id("liq")))
            for item in list(store.get("liquid_classes", []) or [])
            if item.get("name")
        ]
        return _sorted_store(store)

    store = _empty_store()
    _write_store(store)
    return store


def save_store(store: dict[str, Any]) -> None:
    if _mongo_enabled():
        _write_store_to_mongo(store)
    _write_store(store)


def list_liquid_classes(
    *,
    machine_id: str | None = None,
    head_type: str | None = None,
    tip_id: str | None = None,
    tip_capacity_ul: float | None = None,
) -> list[dict[str, Any]]:
    items = deepcopy(load_store()["liquid_classes"])
    return [
        item for item in items
        if (machine_id is None or str(item.get("machine_id") or "") == str(machine_id))
        and (head_type is None or str(item.get("head_type") or "") == str(head_type))
        and (
            tip_id is None
            or str(item.get("tip_id") or "") == str(tip_id)
            or (
                not item.get("tip_id")
                and tip_capacity_ul is not None
                and abs(float(item.get("tip_capacity_ul") or 0.0) - float(tip_capacity_ul)) < 1e-6
            )
        )
        and (
            tip_capacity_ul is None
            or abs(float(item.get("tip_capacity_ul") or 0.0) - float(tip_capacity_ul)) < 1e-6
        )
    ]


def list_pipette_techniques() -> list[dict[str, Any]]:
    return deepcopy(load_store()["pipette_techniques"])


def get_liquid_class(
    name: str,
    *,
    machine_id: str,
    head_type: str,
    tip_id: str | None = None,
    tip_capacity_ul: float,
) -> dict[str, Any] | None:
    for item in load_store()["liquid_classes"]:
        if (
            str(item.get("name") or "") == str(name)
            and str(item.get("machine_id") or "") == str(machine_id)
            and str(item.get("head_type") or "") == str(head_type)
            and (
                (tip_id and str(item.get("tip_id") or "") == str(tip_id))
                or (
                    (not tip_id or not item.get("tip_id"))
                    and abs(float(item.get("tip_capacity_ul") or 0.0) - float(tip_capacity_ul)) < 1e-6
                )
            )
        ):
            return deepcopy(item)
    return None


def get_pipette_technique(name: str) -> dict[str, Any] | None:
    for item in load_store()["pipette_techniques"]:
        if str(item.get("name") or "") == str(name):
            return deepcopy(item)
    return None


def create_liquid_class(payload: dict[str, Any]) -> dict[str, Any]:
    store = load_store()
    item = _normalize_liquid_class_payload(payload, liquid_class_id=_make_id("liq"))
    _validate_liquid_class_uniqueness(store["liquid_classes"], item)
    store["liquid_classes"].append(item)
    save_store(store)
    return deepcopy(item)


def patch_liquid_class(liquid_class_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    store = load_store()
    for index, item in enumerate(store["liquid_classes"]):
        if item.get("liquid_class_id") != liquid_class_id:
            continue
        merged = deepcopy(item)
        merged.update(deepcopy(payload))
        merged = _normalize_liquid_class_payload(merged, liquid_class_id=liquid_class_id)
        _validate_liquid_class_uniqueness(store["liquid_classes"], merged, exclude_id=liquid_class_id)
        store["liquid_classes"][index] = merged
        save_store(store)
        return deepcopy(merged)
    raise KeyError(liquid_class_id)


def delete_liquid_class(liquid_class_id: str) -> None:
    store = load_store()
    before = len(store["liquid_classes"])
    store["liquid_classes"] = [
        item for item in store["liquid_classes"] if item.get("liquid_class_id") != liquid_class_id
    ]
    if len(store["liquid_classes"]) == before:
        raise KeyError(liquid_class_id)
    save_store(store)


def create_pipette_technique(payload: dict[str, Any]) -> dict[str, Any]:
    store = load_store()
    item = _normalize_pipette_technique_payload(payload, technique_id=_make_id("tech"))
    _validate_pipette_technique_uniqueness(store["pipette_techniques"], item)
    store["pipette_techniques"].append(item)
    save_store(store)
    return deepcopy(item)


def patch_pipette_technique(technique_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    store = load_store()
    for index, item in enumerate(store["pipette_techniques"]):
        if item.get("technique_id") != technique_id:
            continue
        merged = deepcopy(item)
        merged.update(deepcopy(payload))
        merged = _normalize_pipette_technique_payload(merged, technique_id=technique_id)
        _validate_pipette_technique_uniqueness(store["pipette_techniques"], merged, exclude_id=technique_id)
        store["pipette_techniques"][index] = merged
        save_store(store)
        return deepcopy(merged)
    raise KeyError(technique_id)


def delete_pipette_technique(technique_id: str) -> None:
    store = load_store()
    before = len(store["pipette_techniques"])
    store["pipette_techniques"] = [
        item for item in store["pipette_techniques"] if item.get("technique_id") != technique_id
    ]
    if len(store["pipette_techniques"]) == before:
        raise KeyError(technique_id)
    save_store(store)


def _load_store_from_mongo() -> dict[str, Any]:
    client, classes_collection, techniques_collection = _mongo_collections()
    try:
        liquid_classes = list(classes_collection.find({}))
        pipette_techniques = list(techniques_collection.find({}))
    finally:
        client.close()
    return _sorted_store(
        {
            "version": 1,
            "liquid_classes": [
                _normalize_liquid_class_payload(_mongo_to_item(doc, "liquid_class_id"), liquid_class_id=str(doc.get("liquid_class_id") or ""))
                for doc in liquid_classes if doc.get("name")
            ],
            "pipette_techniques": [_mongo_to_item(doc, "technique_id") for doc in pipette_techniques if doc.get("name")],
        }
    )


def _write_store_to_mongo(store: dict[str, Any]) -> None:
    client, classes_collection, techniques_collection = _mongo_collections()
    try:
        desired_ids = set()
        for item in store.get("liquid_classes", []):
            doc = deepcopy(item)
            desired_ids.add(str(doc.get("liquid_class_id") or ""))
            classes_collection.replace_one({"liquid_class_id": doc["liquid_class_id"]}, doc, upsert=True)
        if desired_ids:
            classes_collection.delete_many({"liquid_class_id": {"$nin": list(desired_ids)}})
        else:
            classes_collection.delete_many({})

        desired_technique_ids = set()
        for item in store.get("pipette_techniques", []):
            doc = deepcopy(item)
            desired_technique_ids.add(str(doc.get("technique_id") or ""))
            techniques_collection.replace_one({"technique_id": doc["technique_id"]}, doc, upsert=True)
        if desired_technique_ids:
            techniques_collection.delete_many({"technique_id": {"$nin": list(desired_technique_ids)}})
        else:
            techniques_collection.delete_many({})
    finally:
        client.close()


def _mongo_to_item(doc: dict[str, Any], key_name: str) -> dict[str, Any]:
    item = deepcopy(doc)
    item.pop("_id", None)
    item[key_name] = str(item.get(key_name) or "")
    return item


def _normalize_liquid_class_payload(payload: dict[str, Any], *, liquid_class_id: str) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    machine_id = str(payload.get("machine_id") or "").strip()
    head_type = str(payload.get("head_type") or "").strip()
    if not name:
        raise ValueError("Liquid class name is required")
    if not machine_id:
        raise ValueError("machine_id is required")
    if not head_type:
        raise ValueError("head_type is required")
    tip_id = str(payload.get("tip_id") or "").strip()
    tip_capacity_ul = float(payload.get("tip_capacity_ul") or 0.0)
    if not tip_id:
        tip_id = get_tip_id_for_capacity(head_type, tip_capacity_ul) or get_default_tip_id_for_head(head_type) or ""
    resolved_tip = get_tip_definition_by_id(tip_id) or get_tip_definition(head_type, tip_capacity_ul)
    if resolved_tip is not None:
        tip_id = resolved_tip.tip_id
        tip_capacity_ul = float(resolved_tip.capacity_ul)
    aspirate = deepcopy(payload.get("aspirate") or {})
    dispense = deepcopy(payload.get("dispense") or {})
    equation = deepcopy(payload.get("equation") or {})
    control_points = _normalize_control_points(
        equation.get("control_points"),
        tip_capacity_ul=tip_capacity_ul,
        legacy_coefficients=equation.get("coefficients"),
    )
    return {
        "liquid_class_id": liquid_class_id,
        "name": name,
        "description": str(payload.get("description") or ""),
        "machine_id": machine_id,
        "head_type": head_type,
        "tip_id": tip_id,
        "tip_capacity_ul": tip_capacity_ul,
        "aspirate": {
            "w_velocity_ul_s": float(aspirate.get("w_velocity_ul_s") or 100.0),
            "w_acceleration_ul_s2": float(aspirate.get("w_acceleration_ul_s2") or 500.0),
            "post_delay_ms": int(aspirate.get("post_delay_ms") or 0),
            "z_in_velocity_mm_s": float(aspirate.get("z_in_velocity_mm_s") or 100.0),
            "z_in_acceleration_mm_s2": float(aspirate.get("z_in_acceleration_mm_s2") or 500.0),
            "z_out_velocity_mm_s": float(aspirate.get("z_out_velocity_mm_s") or 100.0),
            "z_out_acceleration_mm_s2": float(aspirate.get("z_out_acceleration_mm_s2") or 500.0),
        },
        "dispense": {
            "w_velocity_ul_s": float(dispense.get("w_velocity_ul_s") or 40.0),
            "w_acceleration_ul_s2": float(dispense.get("w_acceleration_ul_s2") or 8.0),
            "post_delay_ms": int(dispense.get("post_delay_ms") or 0),
            "z_in_velocity_mm_s": float(dispense.get("z_in_velocity_mm_s") or 40.0),
            "z_in_acceleration_mm_s2": float(dispense.get("z_in_acceleration_mm_s2") or 250.0),
            "z_out_velocity_mm_s": float(dispense.get("z_out_velocity_mm_s") or 5.0),
            "z_out_acceleration_mm_s2": float(dispense.get("z_out_acceleration_mm_s2") or 5.0),
        },
        "equation": {
            "control_points": control_points,
        },
    }


def _normalize_control_points(
    raw_points: Any,
    *,
    tip_capacity_ul: float,
    legacy_coefficients: Any = None,
) -> list[dict[str, float]]:
    points: list[dict[str, float]] = []
    if isinstance(raw_points, list):
        for item in raw_points:
            if not isinstance(item, dict):
                continue
            desired = float(item.get("desired_ul") or 0.0)
            commanded = float(item.get("commanded_ul") or desired)
            points.append({"desired_ul": desired, "commanded_ul": commanded})
    elif isinstance(legacy_coefficients, list):
        coeffs = [float(value) for value in legacy_coefficients]
        max_volume = max(1.0, float(tip_capacity_ul or 0.0))
        samples = [0.0, max_volume]
        if max_volume > 10.0:
            samples.insert(1, max_volume / 2.0)
        for desired in samples:
            commanded = 0.0
            for exponent, coefficient in enumerate(coeffs):
                commanded += coefficient * (desired ** exponent)
            points.append({"desired_ul": float(desired), "commanded_ul": float(commanded)})
    if not points:
        max_volume = max(1.0, float(tip_capacity_ul or 0.0))
        points = [
            {"desired_ul": 0.0, "commanded_ul": 0.0},
            {"desired_ul": max_volume, "commanded_ul": max_volume},
        ]
    points.sort(key=lambda item: (float(item["desired_ul"]), float(item["commanded_ul"])))
    deduped: list[dict[str, float]] = []
    for point in points:
        desired = max(0.0, float(point["desired_ul"]))
        commanded = max(0.0, float(point["commanded_ul"]))
        if deduped and abs(deduped[-1]["desired_ul"] - desired) < 1e-9:
            deduped[-1] = {"desired_ul": desired, "commanded_ul": commanded}
        else:
            deduped.append({"desired_ul": desired, "commanded_ul": commanded})
    if len(deduped) == 1:
        desired = deduped[0]["desired_ul"]
        deduped.insert(0, {"desired_ul": 0.0, "commanded_ul": 0.0})
        if desired <= 0.0:
            deduped.append({"desired_ul": max(1.0, float(tip_capacity_ul or 0.0)), "commanded_ul": max(1.0, float(tip_capacity_ul or 0.0))})
    return deduped


def _normalize_pipette_technique_payload(payload: dict[str, Any], *, technique_id: str) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("Technique name is required")
    motion_type = str(payload.get("motion_type") or "circular_orbit").strip() or "circular_orbit"
    if motion_type != "circular_orbit":
        raise ValueError("Only circular_orbit is supported")
    z_phase = str(payload.get("z_phase") or "both").strip() or "both"
    if z_phase not in {"enter", "exit", "both"}:
        raise ValueError("z_phase must be enter, exit, or both")
    segments = max(4, int(payload.get("segments") or 12))
    return {
        "technique_id": technique_id,
        "name": name,
        "description": str(payload.get("description") or ""),
        "motion_type": motion_type,
        "radius_mm": max(0.0, float(payload.get("radius_mm") or 0.5)),
        "segments": segments,
        "clockwise": bool(payload.get("clockwise", True)),
        "apply_on_aspirate": bool(payload.get("apply_on_aspirate", True)),
        "apply_on_dispense": bool(payload.get("apply_on_dispense", False)),
        "z_phase": z_phase,
    }


def _validate_liquid_class_uniqueness(items: list[dict[str, Any]], item: dict[str, Any], *, exclude_id: str | None = None) -> None:
    for existing in items:
        if exclude_id and existing.get("liquid_class_id") == exclude_id:
            continue
        if (
            str(existing.get("name") or "").lower() == str(item.get("name") or "").lower()
            and str(existing.get("machine_id") or "") == str(item.get("machine_id") or "")
            and str(existing.get("head_type") or "") == str(item.get("head_type") or "")
            and (
                str(existing.get("tip_id") or "") == str(item.get("tip_id") or "")
                or (
                    not existing.get("tip_id")
                    and not item.get("tip_id")
                    and abs(float(existing.get("tip_capacity_ul") or 0.0) - float(item.get("tip_capacity_ul") or 0.0)) < 1e-6
                )
            )
        ):
            raise ValueError("A liquid class with this machine/head/tip/name already exists")


def _validate_pipette_technique_uniqueness(items: list[dict[str, Any]], item: dict[str, Any], *, exclude_id: str | None = None) -> None:
    for existing in items:
        if exclude_id and existing.get("technique_id") == exclude_id:
            continue
        if str(existing.get("name") or "").lower() == str(item.get("name") or "").lower():
            raise ValueError("A pipette technique with this name already exists")


def _make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"
