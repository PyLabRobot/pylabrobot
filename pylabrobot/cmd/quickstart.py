""" Tool to quickly create a new PyHamilton method from a template.

Based on https://github.com/sphinx-doc/sphinx/blob/master/sphinx/cmd/quickstart.py
"""

import argparse
import datetime
import getpass
import os
import re
import shutil
import subprocess
import sys
from typing import Callable, List, NoReturn, Optional

from pyhamilton import PACKAGE_PATH


def color_print(x: str, color: str):
  code = {
    "black": 30,
    "red": 31,
    "green": 32,
    "yellow": 33,
    "blue": 34,
    "magenta": 35,
    "cyan": 36,
    "white": 37
  }[color]
  print(f"\033[{code}m{x}\033[0m")


def get_parser() -> argparse.ArgumentParser:
  description = (
    "Create a new PyHamilton method from a template."
    "\n"
    "This interactive tool will ask you for the basic information about your new method. It will "
    "create a skeleton for your new method in the specified base directory."
  )
  parser = argparse.ArgumentParser(description=description)

  parser.add_argument("--base-dir", help="The root directory of the new method.", type=str)
  parser.add_argument("-n", "--method-name", help="The name of the new method.", type=str)

  parser.add_argument("-l", "--layfile", help="The path of the layfile to use", type=str)

  parser.add_argument("--no-virtual-env",
    help="Whether to create a virtual environment or use the system environment",
    action="store_true", default=False)

  return parser


class ValidationError(Exception):
  """ Raised when a validator fails. """
  pass


def not_empty(x: str):
  if x == "" or x is None:
    raise ValidationError("Please enter a value.")


def is_path(x: str):
  not_empty(x)
  x = os.path.expanduser(x)
  if not os.path.isdir(x):
    raise ValidationError("Please enter a valid path name.")


def is_file(x: str):
  not_empty(x)
  x = os.path.expanduser(x)
  if not os.path.isfile(x):
    raise ValidationError("Please enter a valid file name.")


def yes_or_no(x: str):
  not_empty(x)
  if not x.lower() in ["n", "y"]:
    raise ValidationError("Please enter y or n.")


def do_prompt(
  text: str,
  default: Optional[str] = None,
  validator: Optional[Callable[[str], NoReturn]] = None
):
  while True:
    if default is not None:
      prompt = f"{text} [{default}]: "
    else:
      prompt = f"{text}: "

    value = input(prompt)

    if not value:
      value = default

    if validator is not None:
      try:
        validator(value)
      except ValidationError as e:
        color_print("* " + str(e), "red")
        continue
    break

  return value


def ask_user(d: dict) -> dict:
  """ Ask the user for the values of the given dictionary. """

  if "base-dir" not in d:
    d["base-dir"] = do_prompt("Root directory", ".", is_path)

  if "method-name" not in d:
    d["method-name"] = do_prompt("Method name", None, not_empty)

  if "author" not in d:
    username = getpass.getuser()
    d["author"] = do_prompt("Author", username, not_empty)

  if "layfile" not in d:
    d["layfile"] = do_prompt("Path to layfile", None, is_file)

  return d


def build_method(d: dict, base_dir) -> int:
  """ Build the method. """

  print("\nCreating method...")

  template_path = os.path.join(PACKAGE_PATH, "templates")
  method_template = os.path.join(template_path, "method.py")

  clean_method_name = re.sub(r"[^a-zA-Z0-9_]", "_", d["method-name"])

  base_dir = os.path.abspath(base_dir)

  os.chdir(base_dir)

  print("Creating method directory...")
  method_dir_name = f"{datetime.datetime.now().strftime('%m-%d-%y')}-{clean_method_name}"
  method_dir = os.path.join(base_dir, method_dir_name)
  os.makedirs(method_dir, exist_ok=True)
  os.chdir(method_dir)

  no_virtual_env = d.pop("no_virtual_env")
  if no_virtual_env:
    python_path = sys.executable
  else:
    print("Creating virtual environment...")
    with open(os.devnull, "wb") as devnull:
      python_path = sys.executable
      subprocess.call([python_path, "-m", "virtualenv", "env"],
        stdout=devnull, stderr=subprocess.STDOUT)

      python_path = os.path.join(method_dir, "env", "bin", "python")

  print("Installing PyHamilton...")
  with open(os.devnull, "wb") as devnull:
    subprocess.call([python_path, "-m", "pip", "install", "pyhamilton"],
      stdout=devnull, stderr=subprocess.STDOUT)

  print("Moving lay file...")
  lay_file_name = os.path.basename(d["layfile"])
  layfile_path = os.path.join(base_dir, d["layfile"])
  new_layfile_path = os.path.join(method_dir, lay_file_name)
  shutil.move(layfile_path, new_layfile_path)

  print("Writing method file...")
  with open(method_template, "r", encoding="utf-8") as f:
    c = f.read()

  d["creation_date"] = datetime.datetime.now().strftime("%a %b %d %H:%M:%S %Y")
  c = c.format(**d)

  filename = clean_method_name + ".py"
  with open(filename, "w", encoding="utf-8") as f:
    f.write(c)

  print("Done!\n")
  print("Usage:")
  print(f"  cd {method_dir}")
  if not no_virtual_env:
    print("  source env/bin/activate")
  print( "  # edit the method.py file")
  print(f"  python3 {filename}")

  return 0


def main(argv: List[str] = sys.argv[1:]) -> int: # pylint: disable=dangerous-default-value
  _ = argv

  parser = get_parser()
  try:
    args = parser.parse_args(argv)
  except SystemExit as err:
    return err.code

  d = vars(args)
  d = {k: v for k, v in d.items() if v is not None} # filter out None values

  d = ask_user(d)
  base_dir = d.pop("base-dir")
  return build_method(d, base_dir)


if __name__ == "__main__":
  sys.exit(main(sys.argv[1:]))
