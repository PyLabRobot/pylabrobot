# mypy: disable-error-code = attr-defined

import asyncio
import json
import os
import threading
from typing import Any, Coroutine, List, Tuple, Type, TypeVar, Optional, cast

from flask import Blueprint, Flask, request, jsonify, current_app
import werkzeug

from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.backends import LiquidHandlerBackend
from pylabrobot.liquid_handling.standard import (
  PipettingOp,
  Pickup,
  Aspiration,
  Dispense,
  Drop,
)
from pylabrobot.resources import Deck, Tip


lh_api = Blueprint("liquid handling", __name__)


class Task:
  """ A task is a coroutine that runs in a separate thread. Maintains its own event loop and
  status. """

  def __init__(self, co: Coroutine[Any, Any, None]):
    self.status = "queued"
    self.co = co
    self.error: Optional[str] = None

  def run_in_thread(self) -> None:
    """ Run the coroutine in a new thread. """
    def runner():
      self.status = "running"
      loop = asyncio.new_event_loop()
      asyncio.set_event_loop(loop)
      try:
        loop.run_until_complete(self.co)
      except Exception as e: # pylint: disable=broad-except
        self.error = str(e)
        self.status = "error"
      else:
        self.status = "succeeded"

    t = threading.Thread(target=runner)
    t.start()

  def serialize(self, id_: int) -> dict:
    d = {"id": id_, "status": self.status}
    if self.error is not None:
      d["error"] = self.error
    return d

tasks: List[Task] = []

def add_and_run_task(task: Task):
  id_ = len(tasks)
  tasks.append(task)
  task.run_in_thread()
  return task.serialize(id_)


@lh_api.route("/")
def index():
  return "PLR Liquid Handling API"


@lh_api.route("/tasks", methods=["GET"])
def get_tasks():
  return jsonify([{"id": i, "status": t.status} for i, t in enumerate(tasks)])

@lh_api.route("/tasks/<int:id_>", methods=["GET"])
def get_task(id_: int):
  if id_ >= len(tasks):
    return jsonify({"error": "task not found"}), 404
  return tasks[id_].serialize(id_)


@lh_api.route("/setup", methods=["POST"])
async def setup():
  return add_and_run_task(Task(current_app.lh.setup()))

@lh_api.route("/stop", methods=["POST"])
async def stop():
  return add_and_run_task(Task(current_app.lh.stop()))

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
    deck = Deck.deserialize(data=data["deck"])
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
      try:
        resource = current_app.lh.deck.get_resource(sc["resource_name"])
      except ValueError:
        raise ErrorResponse({"error": f"resource with name '{sc['resource_name']}' not found"}, 404) \
          from None
      tip = Tip.deserialize(sc.pop("tip"))
      op_ = op.deserialize(sc, resource=resource, tip=tip)
      ops.append(op_)
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
async def pick_up_tips():
  try:
    pickups, use_channels = deserialize_liquid_handling_op_from_request(Pickup)
  except ErrorResponse as e:
    return jsonify(e.data), e.status_code

  return add_and_run_task(Task(current_app.lh.pick_up_tips(
    tip_spots=[p.resource for p in pickups],
    offsets=[p.offset for p in pickups],
    use_channels=use_channels
  )))

@lh_api.route("/drop-tips", methods=["POST"])
async def drop_tips():
  try:
    drops, use_channels = deserialize_liquid_handling_op_from_request(Drop)
  except ErrorResponse as e:
    return jsonify(e.data), e.status_code

  return add_and_run_task(Task(current_app.lh.drop_tips(
    tip_spots=[p.resource for p in drops],
    offsets=[p.offset for p in drops],
    use_channels=use_channels
  )))

@lh_api.route("/aspirate", methods=["POST"])
async def aspirate():
  try:
    aspirations, use_channels = deserialize_liquid_handling_op_from_request(Aspiration)
  except ErrorResponse as e:
    return jsonify(e.data), e.status_code

  return add_and_run_task(Task(current_app.lh.aspirate(
    resources=[a.resource for a in aspirations],
    vols=[a.volume for a in aspirations],
    offsets=[a.offset for a in aspirations],
    flow_rates=[a.flow_rate for a in aspirations],
    use_channels=use_channels
  )))

@lh_api.route("/dispense", methods=["POST"])
async def dispense():
  try:
    dispenses, use_channels = deserialize_liquid_handling_op_from_request(Dispense)
  except ErrorResponse as e:
    return jsonify(e.data), e.status_code

  return add_and_run_task(Task(current_app.lh.dispense(
    resources=[d.resource for d in dispenses],
    vols=[d.volume for d in dispenses],
    offsets=[d.offset for d in dispenses],
    flow_rates=[d.flow_rate for d in dispenses],
    use_channels=use_channels
  )))


def create_app(lh: LiquidHandler):
  """ Create a Flask app with the given LiquidHandler """
  app = Flask(__name__)
  app.lh = lh
  app.register_blueprint(lh_api)
  return app


def main():
  backend_file = os.environ.get("BACKEND_FILE", "backend.json")
  with open(backend_file, "r", encoding="utf-8") as f:
    data = json.load(f)
    backend = LiquidHandlerBackend.deserialize(data)

  deck_file = os.environ.get("DECK_FILE", "deck.json")
  with open(deck_file, "r", encoding="utf-8") as f:
    data = json.load(f)
    deck = Deck.deserialize(data)

  lh = LiquidHandler(backend=backend, deck=deck)

  app = create_app(lh)
  host = os.environ.get("HOST", "0.0.0.0")
  port = int(os.environ.get("PORT", 5001))
  app.run(debug=True, host=host, port=port)


if __name__ == "__main__":
  main()
