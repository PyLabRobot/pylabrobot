import inspect
import json
import os
import traceback

from flask import Flask, jsonify, render_template, request

import pylabrobot.resources as resources_module
from pylabrobot.resources import Resource, STARDeck, STARLetDeck, OTDeck, Deck

print("!" * 80)
print("I am not sure if the GUI still works. If you are interested in using this, please get in "
      "touch on forums.pylabrobot.org")
print("!" * 80)

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


@app.route("/resources")
def list_resources():
  return jsonify(
    plates=[
      "Cos_1536_10ul",
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
      "Cos_96_Vb_P",
    ],
    plate_carriers=[
      "PLT_CAR_L4HD",
      "PLT_CAR_L5AC",
      "PLT_CAR_L5AC_A00",
      "PLT_CAR_L5FLEX_AC",
      "PLT_CAR_L5FLEX_AC_A00",
      "PLT_CAR_L5FLEX_MD",
      "PLT_CAR_L5FLEX_MD_A00",
      "PLT_CAR_L5MD",
      "PLT_CAR_L5MD_A00",
      "PLT_CAR_L5PCR",
      "PLT_CAR_L5PCR_A00",
      "PLT_CAR_L5PCR_A01",
      "PLT_CAR_P3AC_A00",
      "PLT_CAR_P3AC_A01",
      "PLT_CAR_P3HD",
      "PLT_CAR_P3MD",
      "PLT_CAR_P3MD_A00",
      "PLT_CAR_P3MD_A01",
    ],
    tip_carriers=[
      "TIP_CAR_120BC_4mlTF_A00",
      "TIP_CAR_120BC_5mlT_A00",
      "TIP_CAR_288_A00",
      "TIP_CAR_288_B00",
      "TIP_CAR_288_C00",
      "TIP_CAR_288_HTF_A00",
      "TIP_CAR_288_HTF_B00",
      "TIP_CAR_288_HTF_C00",
      "TIP_CAR_288_HT_A00",
      "TIP_CAR_288_HT_B00",
      "TIP_CAR_288_HT_C00",
      "TIP_CAR_288_LTF_A00",
      "TIP_CAR_288_LTF_B00",
      "TIP_CAR_288_LTF_C00",
      "TIP_CAR_288_LT_A00",
      "TIP_CAR_288_LT_B00",
      "TIP_CAR_288_LT_C00",
      "TIP_CAR_288_STF_A00",
      "TIP_CAR_288_STF_B00",
      "TIP_CAR_288_STF_C00",
      "TIP_CAR_288_ST_A00",
      "TIP_CAR_288_ST_B00",
      "TIP_CAR_288_ST_C00",
      "TIP_CAR_288_TIP_50ulF_C00",
      "TIP_CAR_288_TIP_50ul_C00",
      "TIP_CAR_384BC_A00",
      "TIP_CAR_384BC_HTF_A00",
      "TIP_CAR_384BC_HT_A00",
      "TIP_CAR_384BC_LTF_A00",
      "TIP_CAR_384BC_LT_A00",
      "TIP_CAR_384BC_STF_A00",
      "TIP_CAR_384BC_ST_A00",
      "TIP_CAR_384BC_TIP_50ulF_A00",
      "TIP_CAR_384BC_TIP_50ul_A00",
      "TIP_CAR_384_A00",
      "TIP_CAR_384_HT_A00",
      "TIP_CAR_384_LTF_A00",
      "TIP_CAR_384_LT_A00",
      "TIP_CAR_384_STF_A00",
      "TIP_CAR_384_ST_A00",
      "TIP_CAR_384_TIP_50ulF_A00",
      "TIP_CAR_384_TIP_50ul_A00",
      "TIP_CAR_480",
      "TIP_CAR_480BC_A00",
      "TIP_CAR_480BC_HTF_A00",
      "TIP_CAR_480BC_HT_A00",
      "TIP_CAR_480BC_LTF_A00",
      "TIP_CAR_480BC_LT_A00",
      "TIP_CAR_480BC_PiercingTip150ulFilter_A00",
      "TIP_CAR_480BC_PiercingTips_A00",
      "TIP_CAR_480BC_STF_A00",
      "TIP_CAR_480BC_ST_A00",
      "TIP_CAR_480BC_SlimTips300ulFilter_A00",
      "TIP_CAR_480BC_SlimTips_A00",
      "TIP_CAR_480BC_TIP_50ulF_A00",
      "TIP_CAR_480BC_TIP_50ul_A00",
      "TIP_CAR_480_A00",
      "TIP_CAR_480_HTF_A00",
      "TIP_CAR_480_HT_A00",
      "TIP_CAR_480_LTF_A00",
      "TIP_CAR_480_LT_A00",
      "TIP_CAR_480_STF_A00",
      "TIP_CAR_480_ST_A00",
      "TIP_CAR_480_TIP_50ulF_A00",
      "TIP_CAR_480_TIP_50ul_A00",
      "TIP_CAR_72_4mlTF_C00",
      "TIP_CAR_72_5mlT_C00",
      "TIP_CAR_96BC_4mlTF_A00",
      "TIP_CAR_96BC_5mlT_A00",
      "TIP_CAR_NTR_A00",
    ],
    tip_racks=[
      "FourmlTF_L",
      "FourmlTF_P",
      "FivemlT_L",
      "FivemlT_P",
      "HTF_L",
      "HTF_P",
      "HT_L",
      "HT_P",
      "LTF_L",
      "LTF_P",
      "LT_L",
      "LT_P",
      "STF_L",
      "STF_P",
      "ST_L",
      "ST_P",
    ])

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
  # Passing data to a PLR class validates data, but the output should be the same as what we receive
  data = request.get_json()

  # Save deck
  deck_data = data["deck"]
  try:
    deck = Deck.deserialize(deck_data)
  except Exception as e:
    traceback.print_exc()
    return jsonify({"error": str(e), "success": False}), 400

  with open(filename, "w", encoding="utf-8") as f:
    serialized = deck.serialize()
    serialized_data = json.dumps(serialized, indent=2)
    f.write(serialized_data)

  # Save state
  state_data = data["state"]
  deck.load_state(state_data)
  state_filename = filename.replace(".json", "_state.json")
  deck.save_state_to_file(state_filename)

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


@app.route("/recents")
def recents():
  # list all json files in the current directory
  files = [f for f in os.listdir(".") if f.endswith(".json")]
  return jsonify(files=files)


# catch all for static
@app.route("/<path:path>")
def static_proxy(path):
  return app.send_static_file(path)


def main():
  app.run(host="0.0.0.0", port=5001, debug=True)


if __name__ == "__main__":
  main()
