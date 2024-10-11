import datetime


class Parameter:
  def __init__(self, parameter, default, name, desc, t, min=None, max=None):
    self.parameter = parameter
    self.default = default
    self.name = name.replace(" ", "_").lower()
    self.desc = desc
    self.t = t
    self.min = min
    self.max = max

  def formatted_type(self):
    if self.t is int:
      return "int"
    if self.t is str:
      return "str"
    if self.t is bool:
      return "bool"
    if self.t is datetime.datetime:
      return "datetime.datetime"
    else:
      return "TODO:"

  def formatted_default(self):
    if type(self.default) is str:
      return f'"{self.default}"'
    return self.default

  def func_decl_line(self):
    print(f"  {self.name}: {self.formatted_type()} = {self.formatted_default()}")

  def doc_line(self):
    docline = f"{self.name}: {self.desc}."
    if self.t is int:
      if self.min is not None and self.max is not None:
        docline += f" Must be between {self.min} and {self.max}."
      elif self.max is not None:
        docline += f" Must be smaller than {self.max}."
      elif self.min is not None:
        docline += f" Must be bigger than {self.min}."
    if self.default is not None:
      docline += f" Default {self.formatted_default()}."
    print("    " + docline)

  def assertion_line(self):
    if self.t is int and (self.min is not None or self.max is not None):
      print(f'  _assert_clamp({self.name}, {self.min}, {self.max}, "{self.name}")')

  def value_line(self):
    print(f"    {self.parameter}={self.name},")


class Command:
  def __init__(self, dev, cmd, name, desc=None, args=[]):
    self.dev = dev
    self.cmd = cmd
    self.name = name
    self.desc = desc
    self.args = args

  def header(self):
    if len(self.args) == 0:
      print(f"def {self.funcname()}(self):")
      return

    nl = "\n"
    print(f"def {self.funcname()}(")
    print(f"  self,")
    for arg in self.args:
      arg.func_decl_line()
    print(f"):")

  def funcname(self):
    return self.name.replace("-", "_").replace(" ", "_").lower()

  def docstring(self):
    nl = "\n"

    if len(self.args) == 0 and self.desc is None:
      print(f'  """ {self.name} """')
      return

    print(f'  """ {self.name}\n')
    if self.desc is not None:
      print(f"  {self.desc}\n")

    if len(self.args) > 0:
      print("  Args:")
      for arg in self.args:
        arg.doc_line()

    print(f'  """')

  def assertions(self):
    for arg in self.args:
      arg.assertion_line()
    print()

  def command(self):
    if len(self.args) == 0:
      print(f'  return self.send_command(device="{self.dev}", command="{self.cmd}")')
      return

    print("  return self.send_command(")
    print('    module="{self.dev}",')
    print('    command="{self.cmd}",')
    for arg in self.args:
      arg.value_line()
    print("  )")

  def export(self):
    nl = "\n"
    self.header()
    self.docstring()
    print()
    self.assertions()
    self.command()


if __name__ == "__main__":
  # TODO: Update this for each command.
  cmd = Command(
    name="CommandName",
    cmd="RW",
    dev="C0",
    desc=None,
    args=[
      Parameter(
        parameter="ph",
        default=0,
        name="Parameter name",
        desc="Parameter description.",
        t=int,
        min=0,
        max=4,
      )
    ],
  )
  cmd.export()
