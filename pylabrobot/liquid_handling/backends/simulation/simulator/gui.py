import inspect
import json
import os
import traceback

from flask import Flask, jsonify, render_template, request

import pylabrobot.resources as resources_module
from pylabrobot.resources import Resource, STARDeck, STARLetDeck, OTDeck

app = Flask(__name__, template_folder=".", static_folder=".")


@app.route("/")
def hello_world():
  return render_template("welcome.html")


@app.route("/data/<string:filename>")
def get_file_data(filename):
  if not os.path.exists(filename):
    return jsonify({"error": f"File '{filename}' not found.", "not_found": True})

  with open(filename, "r", encoding="utf-8") as f:
    contents = f.read()
    data = json.loads(contents)
    return jsonify(data=data)

@app.route("/editor/<string:filename>")
def editor(filename):
  return render_template("editor.html", filename=filename)


@app.route("/plates")
def plates():
  return jsonify(["Cos_1536_10ul",
    "Cos_1536_10ul_L",
    "Cos_1536_10ul_P",
    "Cos_384_DW",
    "Cos_384_DW_L",
    "Cos_384_DW_P",
    "Cos_384_PCR",
    "Cos_384_PCR_L",
    "Cos_384_PCR_P",
    "Cos_384_Sq",
    "Cos_384_Sq_L",
    "Cos_384_Sq_P",
    "Cos_384_Sq_Rd",
    "Cos_384_Sq_Rd_L",
    "Cos_384_Sq_Rd_P",
    "Cos_96_DW_1mL",
    "Cos_96_DW_1mL_L",
    "Cos_96_DW_1mL_P",
    "Cos_96_DW_2mL",
    "Cos_96_DW_2mL_L",
    "Cos_96_DW_2mL_P",
    "Cos_96_DW_500ul",
    "Cos_96_DW_500ul_L",
    "Cos_96_DW_500ul_P",
    "Cos_96_EZWash",
    "Cos_96_EZWash_L",
    "Cos_96_EZWash_P",
    "Cos_96_FL",
    "Cos_96_Filter",
    "Cos_96_Filter_L",
    "Cos_96_Filter_P",
    "Cos_96_Fl_L",
    "Cos_96_Fl_P",
    "Cos_96_HalfArea",
    "Cos_96_HalfArea_L",
    "Cos_96_HalfArea_P",
    "Cos_96_PCR",
    "Cos_96_PCR_L",
    "Cos_96_PCR_P",
    "Cos_96_ProtCryst",
    "Cos_96_ProtCryst_L",
    "Cos_96_ProtCryst_P",
    "Cos_96_Rd",
    "Cos_96_Rd_L",
    "Cos_96_Rd_P",
    "Cos_96_SpecOps",
    "Cos_96_SpecOps_L",
    "Cos_96_SpecOps_P",
    "Cos_96_UV",
    "Cos_96_UV_L",
    "Cos_96_UV_P",
    "Cos_96_Vb",
    "Cos_96_Vb_L",
    "Cos_96_Vb_P"])

@app.route("/tip_racks")
def tip_racks():
  return jsonify(["Rick", "Rick Plate"])

@app.route("/resource/<resource_id>")
def resource(resource_id):
  resource_classes = [c[0] for c in inspect.getmembers(resources_module)]

  if resource_id not in resource_classes:
    return jsonify({"error": f"Resource '{resource_id}' not found."})

  resource_class = getattr(resources_module, resource_id)
  resource_name = request.args.get("name")
  resource = resource_class(name=resource_name)
  return jsonify(resource.serialize())


@app.route("/editor/<string:filename>/save", methods=["POST"])
def save(filename):
  data = request.get_json()

  try:
    deck = Resource.deserialize(data)
  except Exception as e:
    traceback.print_exc()
    return jsonify({"error": str(e), "success": False}), 400

  with open(filename, "w", encoding="utf-8") as f:
    serialized = deck.serialize()
    serialized_data = json.dumps(serialized, indent=2)
    f.write(serialized_data)

  return jsonify({"success": True})


@app.route("/create", methods=["POST"])
def create():
  data = request.get_json()

  if not "type" in data:
    return jsonify({"error": "No type specified.", "success": False}), 400

  # Get a deck from the submitted data, either from a file or create a new deck.
  if data["type"] == "from_file":
    deck_data = data["deck"]
    try:
      deck = Resource.deserialize(deck_data)
    except Exception as e: # pylint: disable=broad-exception-caught
      traceback.print_exc()
      return jsonify({"error": str(e), "success": False}), 400
  elif data["type"] == "new_deck":
    deck_type = data["deck_type"]
    if deck_type == "hamilton-star":
      deck = STARDeck()
    elif deck_type == "hamilton-starlet":
      deck = STARLetDeck()
    elif deck_type == "opentrons-ot2":
      deck = OTDeck()
    else:
      return jsonify({"error": f"Unknown deck type '{deck_type}'.", "success": False}), 400
  else:
    return jsonify({"error": f"Unknown type '{data['type']}'.", "success": False}), 400

  # Save the deck to a file.
  filename = data["filename"]
  if filename is None:
    return jsonify({"error": "No filename specified.", "success": False}), 400
  with open(filename, "w", encoding="utf-8") as f:
    serialized = deck.serialize()
    serialized_data = json.dumps(serialized, indent=2)
    f.write(serialized_data)

  return jsonify({"success": True})


# catch all for static
@app.route("/<path:path>")
def static_proxy(path):
  return app.send_static_file(path)


if __name__ == "__main__":
  app.run(host="0.0.0.0", port=5001, debug=True)
