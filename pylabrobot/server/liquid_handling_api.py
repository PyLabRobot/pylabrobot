# mypy: disable-error-code = attr-defined

from typing import List, Tuple, Type, TypeVar, cast

from flask import Blueprint, request, jsonify, current_app
import werkzeug

from pylabrobot.liquid_handling.resources import Deck
from pylabrobot.liquid_handling.standard import (
  PipettingOp,
  Pickup,
  Aspiration,
  Dispense,
  Discard,
)


lh_api = Blueprint("liquid handling", __name__, url_prefix="/api/v1/liquid_handling")


@lh_api.route("/")
def index():
  return "PLR Liquid Handling API"


@lh_api.route("/setup", methods=["POST"])
def setup():
  current_app.lh.setup()
  return jsonify({"status": "running"})

@lh_api.route("/stop", methods=["POST"])
def stop():
  current_app.lh.stop()
  return jsonify({"status": "stopped"})

@lh_api.route("/status", methods=["GET"])
def get_status():
  status = "running" if current_app.lh.setup_finished else "stopped"
  return jsonify({"status": status})


@lh_api.route("/labware", methods=["POST"])
def define_labware():
  try:
    data = request.get_json()
    if not isinstance(data, dict):
      raise werkzeug.exceptions.BadRequest
  except werkzeug.exceptions.BadRequest:
    return jsonify({"error": "json data must be a dict"}), 400

  try:
    deck = Deck.load_from_json(content=data)
    current_app.lh.deck = deck
  except KeyError as e:
    return jsonify({"error": "missing key in json data: " + str(e)}), 400

  return jsonify({"status": "ok"})

class ErrorResponse(Exception):
  def __init__(self, data: dict, status_code: int):
    self.data = data
    self.status_code = status_code

T = TypeVar("T", bound=PipettingOp)
def deserialize_liquid_handling_op_from_request(
  op: Type[T]
) -> Tuple[List[T], List[int]]:
  data = request.get_json()
  if not isinstance(data, dict):
    raise ErrorResponse({"error": "json data must be a list"}, 400)

  serialized_channels = data.get("channels")
  if not isinstance(serialized_channels, list):
    raise ErrorResponse({"error": "'channels' must be a list"}, 400)

  ops = []
  for sc in serialized_channels:
    try:
      resource = current_app.lh.deck.get_resource(sc["resource_name"])
      op_ = op.deserialize(sc, resource=resource)
      ops.append(op_)
    except ValueError:
      raise ErrorResponse({"error": f"resource with name '{sc['resource_name']}' not found"}, 404) \
       from None
    except KeyError as e:
      raise ErrorResponse({"error": f"missing key in json data: {e}"}, 400) from e

  use_channels = data.get("use_channels")
  if use_channels is not None:
    if not isinstance(use_channels, list):
      raise ErrorResponse({"error": "'use_channels' must be a list"}, 400)
    if len(use_channels) != len(ops):
      raise ErrorResponse({"error": "'use_channels' must have the same length as 'pickups'"}, 400)
    for channel in use_channels:
      if not isinstance(channel, int):
        raise ErrorResponse({"error": "'use_channels' must be a list of integers"}, 400)

  return cast(List[T], ops), cast(List[int], use_channels) # right types, but mypy doesn't know

@lh_api.route("/pick-up-tips", methods=["POST"])
def pick_up_tips():
  try:
    pickups, use_channels = deserialize_liquid_handling_op_from_request(Pickup)
  except ErrorResponse as e:
    return jsonify(e.data), e.status_code

  try:
    current_app.lh.pick_up_tips(
      channels=[p.resource for p in pickups],
      offsets=[p.offset for p in pickups],
      use_channels=use_channels
    )
    return jsonify({"status": "ok"})
  except Exception as e: # pylint: disable=broad-except
    return jsonify({"error": str(e)}), 400

@lh_api.route("/discard-tips", methods=["POST"])
def discard_tips():
  try:
    discards, use_channels = deserialize_liquid_handling_op_from_request(Discard)
  except ErrorResponse as e:
    return jsonify(e.data), e.status_code

  try:
    current_app.lh.discard_tips(
      channels=[p.resource for p in discards],
      offsets=[p.offset for p in discards],
      use_channels=use_channels
    )
    return jsonify({"status": "ok"})
  except Exception as e: # pylint: disable=broad-except
    return jsonify({"error": str(e)}), 400

@lh_api.route("/aspirate", methods=["POST"])
def aspirate():
  try:
    aspirations, use_channels = deserialize_liquid_handling_op_from_request(Aspiration)
  except ErrorResponse as e:
    return jsonify(e.data), e.status_code

  try:
    current_app.lh.aspirate(
      wells=[a.resource for a in aspirations],
      vols=[a.volume for a in aspirations],
      offsets=[a.offset for a in aspirations],
      flow_rates=[a.flow_rate for a in aspirations],
      use_channels=use_channels
    )
    return jsonify({"status": "ok"})
  except Exception as e: # pylint: disable=broad-except
    return jsonify({"error": str(e)}), 400

@lh_api.route("/dispense", methods=["POST"])
def dispense():
  try:
    dispenses, use_channels = deserialize_liquid_handling_op_from_request(Dispense)
  except ErrorResponse as e:
    return jsonify(e.data), e.status_code

  try:
    current_app.lh.dispense(
      wells=[d.resource for d in dispenses],
      vols=[d.volume for d in dispenses],
      offsets=[d.offset for d in dispenses],
      flow_rates=[d.flow_rate for d in dispenses],
      use_channels=use_channels
    )
    return jsonify({"status": "ok"})
  except Exception as e: # pylint: disable=broad-except
    return jsonify({"error": str(e)}), 400
