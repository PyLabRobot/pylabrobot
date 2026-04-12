"""Unit tests for TypeRegistry local struct/enum resolution (source_id=2)."""

import unittest

from pylabrobot.liquid_handling.backends.hamilton.tcp.introspection import (
  EnumInfo,
  MethodInfo,
  ParameterType,
  StructInfo,
  TypeRegistry,
)


class TestTypeRegistryLocalResolution(unittest.TestCase):
  def _registry_with_structs(self) -> TypeRegistry:
    s0 = StructInfo(struct_id=0, name="A", fields={}, interface_id=1)
    s1 = StructInfo(struct_id=1, name="B", fields={}, interface_id=1)
    s2 = StructInfo(struct_id=2, name="C", fields={}, interface_id=1)
    return TypeRegistry(
      structs={
        1: {0: s0, 1: s1, 2: s2},
      },
    )

  def test_resolve_struct_local_1based(self):
    reg = self._registry_with_structs()
    self.assertIsNone(reg.resolve_struct(2, 0))
    r1 = reg.resolve_struct(2, 1)
    assert r1 is not None
    self.assertEqual(r1.name, "A")
    r2 = reg.resolve_struct(2, 2)
    assert r2 is not None
    self.assertEqual(r2.name, "B")
    r3 = reg.resolve_struct(2, 3)
    assert r3 is not None
    self.assertEqual(r3.name, "C")

  def test_resolve_struct_legacy_prefers_iface_1_then_other(self):
    s_alt = StructInfo(struct_id=0, name="Alt", fields={}, interface_id=2)
    reg = TypeRegistry(
      structs={
        2: {0: s_alt},
        1: {0: StructInfo(struct_id=0, name="Primary", fields={}, interface_id=1)},
      }
    )
    rp = reg.resolve_struct(2, 1)
    assert rp is not None
    self.assertEqual(rp.name, "Primary")
    reg2 = TypeRegistry(structs={2: {0: s_alt}})
    ra = reg2.resolve_struct(2, 1)
    assert ra is not None
    self.assertEqual(ra.name, "Alt")

  def test_resolve_struct_strict_ho_interface_id(self):
    s_alt = StructInfo(struct_id=0, name="Alt", fields={}, interface_id=2)
    primary = StructInfo(struct_id=0, name="Primary", fields={}, interface_id=1)
    reg = TypeRegistry(
      structs={
        2: {0: s_alt},
        1: {0: primary},
      }
    )
    p = reg.resolve_struct(2, 1, ho_interface_id=1)
    assert p is not None
    self.assertEqual(p.name, "Primary")
    a = reg.resolve_struct(2, 1, ho_interface_id=2)
    assert a is not None
    self.assertEqual(a.name, "Alt")

  def test_resolve_enum_strict_ho_interface_id(self):
    e1_iface1 = EnumInfo(enum_id=0, name="OnIface1", values={})
    e1_iface2 = EnumInfo(enum_id=0, name="OnIface2", values={})
    reg = TypeRegistry(
      enums={
        1: {0: e1_iface1},
        2: {0: e1_iface2},
      }
    )
    e1 = reg.resolve_enum(2, 1, ho_interface_id=1)
    assert e1 is not None
    self.assertEqual(e1.name, "OnIface1")
    e2 = reg.resolve_enum(2, 1, ho_interface_id=2)
    assert e2 is not None
    self.assertEqual(e2.name, "OnIface2")

  def test_resolve_enum_local_1based(self):
    e0 = EnumInfo(enum_id=0, name="E0", values={})
    e1 = EnumInfo(enum_id=1, name="E1", values={})
    reg = TypeRegistry(
      enums={
        1: {0: e0, 1: e1},
      },
    )
    self.assertIsNone(reg.resolve_enum(2, 0))
    ev0 = reg.resolve_enum(2, 1)
    assert ev0 is not None
    self.assertEqual(ev0.name, "E0")
    ev1 = reg.resolve_enum(2, 2)
    assert ev1 is not None
    self.assertEqual(ev1.name, "E1")

  def test_method_signature_uses_method_interface_for_local_struct(self):
    local_s = StructInfo(struct_id=0, name="Iface2Local", fields={}, interface_id=2)
    reg = TypeRegistry(structs={2: {0: local_s}})
    pt = ParameterType(57, source_id=2, ref_id=1)
    method = MethodInfo(
      interface_id=2,
      call_type=0,
      method_id=5,
      name="DoThing",
      parameter_types=[pt],
      parameter_labels=["arg"],
    )
    sig = method.get_signature_string(reg)
    self.assertIn("Iface2Local", sig)
    self.assertIn("[2:5]", sig)


if __name__ == "__main__":
  unittest.main()
