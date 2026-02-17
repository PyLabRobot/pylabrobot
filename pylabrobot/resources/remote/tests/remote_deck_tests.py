"""Tests for the remote deck system (server, client, proxies, trackers)."""

import threading
import time
import unittest
from collections import OrderedDict

import uvicorn

from pylabrobot.resources.coordinate import Coordinate
from pylabrobot.resources.container import Container
from pylabrobot.resources.deck import Deck
from pylabrobot.resources.hamilton.tip_creators import (
    HamiltonTip,
    hamilton_tip_300uL,
    hamilton_tip_1000uL_filter,
)
from pylabrobot.resources.plate import Lid, Plate
from pylabrobot.resources.resource import Resource
from pylabrobot.resources.rotation import Rotation
from pylabrobot.resources.tip import Tip
from pylabrobot.resources.tip_rack import TipRack, TipSpot
from pylabrobot.resources.tip_tracker import set_tip_tracking
from pylabrobot.resources.trash import Trash
from pylabrobot.resources.volume_tracker import set_volume_tracking
from pylabrobot.resources.well import Well

from pylabrobot.resources.remote import deck_service_pb2 as pb2
from pylabrobot.resources.remote.client import RemoteDeck
from pylabrobot.resources.remote.proxies import (
    ContainerProxy,
    LidProxy,
    PlateProxy,
    ResourceProxy,
    TipRackProxy,
    TipSpotProxy,
    TrashProxy,
    WellProxy,
    create_proxy,
)
from pylabrobot.resources.remote.remote_trackers import (
    RemoteTipTracker,
    RemoteVolumeTracker,
    _tip_from_proto,
)
from pylabrobot.resources.remote.server import (
    _resource_to_data,
    _resource_to_tree,
    _tip_to_proto,
    create_app,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_deck() -> Deck:
    """Build a small deck with a plate (8 wells), tip rack (8 spots), and trash."""
    deck = Deck(size_x=1000, size_y=500, size_z=200, name="test_deck")

    wells = OrderedDict()
    for i in range(8):
        key = f"A{i + 1}"
        wells[key] = Well(
            name=key, size_x=9.0, size_y=9.0, size_z=10.5,
            bottom_type="flat", cross_section_type="circle",
            max_volume=300.0, material_z_thickness=0.5,
        )
        wells[key].location = Coordinate(x=i * 10, y=0, z=0)
    plate = Plate(
        name="plate_01", size_x=127.0, size_y=85.0, size_z=14.0,
        ordered_items=wells, plate_type="skirted",
    )
    deck.assign_child_resource(plate, location=Coordinate(100, 50, 0))

    spots = OrderedDict()
    for i in range(8):
        key = f"A{i + 1}"
        spots[key] = TipSpot(
            name=key, size_x=9.0, size_y=9.0,
            make_tip=hamilton_tip_300uL, size_z=0.0,
        )
        spots[key].location = Coordinate(x=i * 10, y=0, z=0)
    tip_rack = TipRack(
        name="tip_rack_01", size_x=122.0, size_y=82.0, size_z=60.0,
        ordered_items=spots, with_tips=True,
    )
    deck.assign_child_resource(tip_rack, location=Coordinate(300, 50, 0))

    trash = Trash(name="trash_01", size_x=100.0, size_y=100.0, size_z=50.0)
    deck.assign_child_resource(trash, location=Coordinate(500, 50, 0))

    return deck


class _ServerFixture:
    """Spin up a uvicorn server in a background thread and tear it down after."""

    def __init__(self, deck: Deck, port: int):
        self.deck = deck
        self.port = port
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def start(self):
        app = create_app(self.deck)
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        time.sleep(0.8)

    def stop(self):
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=3)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


# ---------------------------------------------------------------------------
# Unit tests: serialization helpers
# ---------------------------------------------------------------------------

class TestTipSerialization(unittest.TestCase):
    """Test _tip_to_proto / _tip_from_proto round-trip."""

    def test_plain_tip_round_trip(self):
        tip = Tip(has_filter=False, total_tip_length=59.9,
                  maximal_volume=400.0, fitting_depth=8.0, name="t1")
        proto = _tip_to_proto(tip)
        self.assertEqual(proto.type, "Tip")
        self.assertEqual(proto.name, "t1")
        recovered = _tip_from_proto(proto)
        self.assertIsInstance(recovered, Tip)
        self.assertNotIsInstance(recovered, HamiltonTip)
        self.assertEqual(recovered.maximal_volume, 400.0)
        self.assertEqual(recovered.fitting_depth, 8.0)

    def test_hamilton_tip_round_trip(self):
        tip = hamilton_tip_300uL(name="ht1")
        proto = _tip_to_proto(tip)
        self.assertEqual(proto.type, "HamiltonTip")
        self.assertEqual(proto.tip_size, "STANDARD_VOLUME")
        self.assertEqual(proto.pickup_method, "OUT_OF_RACK")
        recovered = _tip_from_proto(proto)
        self.assertIsInstance(recovered, HamiltonTip)
        self.assertEqual(recovered.maximal_volume, tip.maximal_volume)
        self.assertEqual(recovered.tip_size.name, "STANDARD_VOLUME")

    def test_hamilton_1000uL_filter_round_trip(self):
        tip = hamilton_tip_1000uL_filter(name="ht2")
        proto = _tip_to_proto(tip)
        self.assertEqual(proto.tip_size, "HIGH_VOLUME")
        recovered = _tip_from_proto(proto)
        self.assertIsInstance(recovered, HamiltonTip)
        self.assertEqual(recovered.has_filter, True)


class TestResourceToData(unittest.TestCase):
    """Test _resource_to_data for each resource type."""

    def test_well(self):
        w = Well(name="w", size_x=9, size_y=9, size_z=10,
                 bottom_type="flat", cross_section_type="circle",
                 max_volume=300, material_z_thickness=0.5)
        data = _resource_to_data(w)
        self.assertEqual(data.type, "Well")
        self.assertEqual(data.well_bottom_type, "flat")
        self.assertEqual(data.cross_section_type, "circle")
        self.assertAlmostEqual(data.max_volume, 300.0)
        self.assertAlmostEqual(data.material_z_thickness, 0.5)

    def test_plate_ordering(self):
        wells = OrderedDict()
        for k in ("A1", "A2"):
            wells[k] = Well(name=k, size_x=9, size_y=9, size_z=10)
            wells[k].location = Coordinate(0, 0, 0)
        plate = Plate(name="p", size_x=127, size_y=85, size_z=14,
                      ordered_items=wells, plate_type="non-skirted")
        data = _resource_to_data(plate)
        self.assertEqual(data.type, "Plate")
        self.assertEqual(data.plate_type, "non-skirted")
        self.assertIn("A1", data.ordering)
        self.assertEqual(data.ordering["A1"], "p_A1")

    def test_tipspot_prototype(self):
        ts = TipSpot(name="ts", size_x=9, size_y=9, make_tip=hamilton_tip_300uL)
        data = _resource_to_data(ts)
        self.assertEqual(data.type, "TipSpot")
        self.assertEqual(data.prototype_tip.type, "HamiltonTip")
        self.assertGreater(data.prototype_tip.maximal_volume, 0)

    def test_trash(self):
        t = Trash(name="tr", size_x=100, size_y=100, size_z=50)
        data = _resource_to_data(t)
        self.assertEqual(data.type, "Trash")

    def test_lid(self):
        lid = Lid(name="lid", size_x=127, size_y=85, size_z=10, nesting_z_height=5)
        data = _resource_to_data(lid)
        self.assertEqual(data.type, "Lid")
        self.assertAlmostEqual(data.nesting_z_height, 5.0)


class TestResourceToTree(unittest.TestCase):
    """Test recursive tree serialization."""

    def test_deck_tree(self):
        deck = _make_deck()
        tree = _resource_to_tree(deck)
        self.assertEqual(tree.data.name, "test_deck")
        self.assertEqual(tree.data.type, "Deck")
        child_names = [c.data.name for c in tree.children]
        self.assertIn("plate_01", child_names)
        self.assertIn("tip_rack_01", child_names)
        self.assertIn("trash_01", child_names)

    def test_plate_children_in_tree(self):
        deck = _make_deck()
        tree = _resource_to_tree(deck)
        plate_tree = [c for c in tree.children if c.data.name == "plate_01"][0]
        self.assertEqual(len(plate_tree.children), 8)
        self.assertTrue(all(c.data.type == "Well" for c in plate_tree.children))


# ---------------------------------------------------------------------------
# Unit tests: proxy isinstance checks
# ---------------------------------------------------------------------------

class TestProxyIsinstance(unittest.TestCase):
    """Proxy objects must pass the isinstance checks that backends rely on."""

    def _data(self, **kw) -> pb2.ResourceData:
        defaults = dict(name="x", size_x=1, size_y=1, size_z=1)
        defaults.update(kw)
        return pb2.ResourceData(**defaults)

    def test_well_proxy(self):
        proxy = create_proxy(None, self._data(type="Well", category="well"))
        self.assertIsInstance(proxy, Well)
        self.assertIsInstance(proxy, Container)
        self.assertIsInstance(proxy, Resource)

    def test_plate_proxy(self):
        proxy = create_proxy(None, self._data(type="Plate", ordering={"A1": "x_A1"}))
        self.assertIsInstance(proxy, Plate)
        self.assertIsInstance(proxy, Resource)

    def test_tipspot_proxy(self):
        proto_tip = pb2.TipData(type="Tip", has_filter=False,
                                total_tip_length=50, maximal_volume=300,
                                fitting_depth=8, name="t")
        proxy = create_proxy(None, self._data(type="TipSpot", prototype_tip=proto_tip))
        self.assertIsInstance(proxy, TipSpot)
        self.assertIsInstance(proxy, Resource)

    def test_tiprack_proxy(self):
        proxy = create_proxy(None, self._data(type="TipRack", ordering={"A1": "x_A1"}))
        self.assertIsInstance(proxy, TipRack)

    def test_trash_proxy(self):
        proxy = create_proxy(None, self._data(type="Trash"))
        self.assertIsInstance(proxy, Trash)
        self.assertIsInstance(proxy, Container)

    def test_lid_proxy(self):
        proxy = create_proxy(None, self._data(type="Lid", nesting_z_height=5))
        self.assertIsInstance(proxy, Lid)

    def test_unknown_type_falls_back_to_resource(self):
        proxy = create_proxy(None, self._data(type="SomeCarrier"))
        self.assertIsInstance(proxy, Resource)
        self.assertNotIsInstance(proxy, Plate)


# ---------------------------------------------------------------------------
# Integration tests: full server ↔ client round-trip
# ---------------------------------------------------------------------------

_PORT = 18_123  # avoid collisions with common ports


class TestRemoteDeckConnection(unittest.TestCase):
    """Connect to a server, verify the tree is built correctly."""

    @classmethod
    def setUpClass(cls):
        set_volume_tracking(True)
        set_tip_tracking(True)
        cls.local_deck = _make_deck()
        cls.fixture = _ServerFixture(cls.local_deck, _PORT)
        cls.fixture.start()
        cls.remote_deck = RemoteDeck.connect(cls.fixture.url)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.stop()

    # -- tree structure --

    def test_deck_name_and_size(self):
        self.assertEqual(self.remote_deck.name, "test_deck")
        self.assertAlmostEqual(self.remote_deck._size_x, 1000)

    def test_children_names(self):
        names = sorted(c.name for c in self.remote_deck.children)
        self.assertEqual(names, ["plate_01", "tip_rack_01", "trash_01"])

    def test_all_resources_count(self):
        # deck itself is not counted, but plate(1) + 8 wells + tip_rack(1) + 8 spots + trash(1) = 19
        self.assertEqual(len(self.remote_deck.get_all_resources()), 19)

    def test_get_resource_deep(self):
        well = self.remote_deck.get_resource("plate_01_A1")
        self.assertIsInstance(well, Well)

    def test_has_resource(self):
        self.assertTrue(self.remote_deck.has_resource("plate_01"))
        self.assertTrue(self.remote_deck.has_resource("tip_rack_01_A1"))
        self.assertFalse(self.remote_deck.has_resource("nonexistent"))

    # -- isinstance --

    def test_isinstance_plate(self):
        self.assertIsInstance(self.remote_deck.get_resource("plate_01"), Plate)

    def test_isinstance_well(self):
        self.assertIsInstance(self.remote_deck.get_resource("plate_01_A1"), Well)

    def test_isinstance_tiprack(self):
        self.assertIsInstance(self.remote_deck.get_resource("tip_rack_01"), TipRack)

    def test_isinstance_tipspot(self):
        self.assertIsInstance(self.remote_deck.get_resource("tip_rack_01_A1"), TipSpot)

    def test_isinstance_trash(self):
        self.assertIsInstance(self.remote_deck.get_resource("trash_01"), Trash)

    # -- bracket notation --

    def test_plate_getitem(self):
        plate = self.remote_deck.get_resource("plate_01")
        items = plate["A1:A4"]
        self.assertEqual(len(items), 4)
        self.assertTrue(all(isinstance(w, Well) for w in items))

    def test_tiprack_getitem(self):
        tr = self.remote_deck.get_resource("tip_rack_01")
        items = tr["A1:A4"]
        self.assertEqual(len(items), 4)
        self.assertTrue(all(isinstance(t, TipSpot) for t in items))


class TestRemoteSpatialRPCs(unittest.TestCase):
    """Spatial method results must match local computation."""

    @classmethod
    def setUpClass(cls):
        cls.local_deck = _make_deck()
        cls.fixture = _ServerFixture(cls.local_deck, _PORT + 1)
        cls.fixture.start()
        cls.remote_deck = RemoteDeck.connect(cls.fixture.url)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.stop()

    def _assert_coord_eq(self, a: Coordinate, b: Coordinate, places=3):
        self.assertAlmostEqual(a.x, b.x, places=places)
        self.assertAlmostEqual(a.y, b.y, places=places)
        self.assertAlmostEqual(a.z, b.z, places=places)

    def test_get_absolute_location(self):
        for name in ("plate_01_A1", "tip_rack_01_A1", "trash_01"):
            local = self.local_deck.get_resource(name).get_absolute_location()
            remote = self.remote_deck.get_resource(name).get_absolute_location()
            self._assert_coord_eq(local, remote)

    def test_get_absolute_location_centered(self):
        local = self.local_deck.get_resource("plate_01_A1").get_absolute_location("c", "c", "b")
        remote = self.remote_deck.get_resource("plate_01_A1").get_absolute_location("c", "c", "b")
        self._assert_coord_eq(local, remote)

    def test_get_location_wrt_deck(self):
        local_well = self.local_deck.get_resource("plate_01_A1")
        remote_well = self.remote_deck.get_resource("plate_01_A1")
        local = local_well.get_location_wrt(self.local_deck, "c", "c", "b")
        remote = remote_well.get_location_wrt(self.remote_deck, "c", "c", "b")
        self._assert_coord_eq(local, remote)

    def test_get_absolute_rotation(self):
        remote_well = self.remote_deck.get_resource("plate_01_A1")
        rot = remote_well.get_absolute_rotation()
        self.assertIsInstance(rot, Rotation)
        self.assertAlmostEqual(rot.x, 0)
        self.assertAlmostEqual(rot.y, 0)
        self.assertAlmostEqual(rot.z, 0)

    def test_get_absolute_size(self):
        local_well = self.local_deck.get_resource("plate_01_A1")
        remote_well = self.remote_deck.get_resource("plate_01_A1")
        self.assertAlmostEqual(local_well.get_absolute_size_x(), remote_well.get_absolute_size_x())
        self.assertAlmostEqual(local_well.get_absolute_size_y(), remote_well.get_absolute_size_y())
        self.assertAlmostEqual(local_well.get_absolute_size_z(), remote_well.get_absolute_size_z())

    def test_get_highest_known_point(self):
        local_val = self.local_deck.get_highest_known_point()
        remote_val = self.remote_deck.get_highest_known_point()
        self.assertAlmostEqual(local_val, remote_val)


class TestRemoteTrackers(unittest.TestCase):
    """Volume and tip tracker RPCs."""

    @classmethod
    def setUpClass(cls):
        set_volume_tracking(True)
        set_tip_tracking(True)
        cls.local_deck = _make_deck()
        # Give the first well some initial volume
        cls.local_deck.get_resource("plate_01_A1").tracker.set_volume(200.0)
        cls.fixture = _ServerFixture(cls.local_deck, _PORT + 2)
        cls.fixture.start()
        cls.remote_deck = RemoteDeck.connect(cls.fixture.url)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.stop()

    # -- volume tracker --

    def test_volume_tracker_initial_state(self):
        well = self.remote_deck.get_resource("plate_01_A1")
        self.assertAlmostEqual(well.tracker.get_used_volume(), 200.0)
        self.assertAlmostEqual(well.tracker.get_free_volume(), 100.0)

    def test_volume_tracker_is_disabled(self):
        well = self.remote_deck.get_resource("plate_01_A1")
        self.assertFalse(well.tracker.is_disabled)

    def test_volume_remove_and_commit(self):
        well = self.remote_deck.get_resource("plate_01_A2")
        # A2 starts at 0. Add 100, commit, then remove 40, commit.
        self.local_deck.get_resource("plate_01_A2").tracker.set_volume(100.0)
        self.assertAlmostEqual(well.tracker.get_used_volume(), 100.0)

        well.tracker.remove_liquid(40.0)
        self.assertAlmostEqual(well.tracker.get_used_volume(), 60.0)
        well.tracker.commit()
        self.assertAlmostEqual(well.tracker.get_used_volume(), 60.0)

    def test_volume_add_and_rollback(self):
        well = self.remote_deck.get_resource("plate_01_A3")
        self.local_deck.get_resource("plate_01_A3").tracker.set_volume(50.0)
        well.tracker.add_liquid(20.0)
        self.assertAlmostEqual(well.tracker.get_used_volume(), 70.0)
        well.tracker.rollback()
        self.assertAlmostEqual(well.tracker.get_used_volume(), 50.0)

    # -- tip tracker --

    def test_tip_tracker_has_tip(self):
        ts = self.remote_deck.get_resource("tip_rack_01_A1")
        self.assertTrue(ts.tracker.has_tip)

    def test_tip_tracker_is_disabled(self):
        ts = self.remote_deck.get_resource("tip_rack_01_A1")
        self.assertFalse(ts.tracker.is_disabled)

    def test_tip_remove_and_add(self):
        ts = self.remote_deck.get_resource("tip_rack_01_A2")
        self.assertTrue(ts.tracker.has_tip)
        ts.tracker.remove_tip()
        ts.tracker.commit()
        self.assertFalse(ts.tracker.has_tip)
        ts.tracker.add_tip()
        # add_tip commits by default
        self.assertTrue(ts.tracker.has_tip)

    def test_get_tip_returns_hamilton_tip(self):
        ts = self.remote_deck.get_resource("tip_rack_01_A1")
        tip = ts.get_tip()
        self.assertIsInstance(tip, HamiltonTip)
        self.assertAlmostEqual(tip.maximal_volume, 400.0)
        self.assertFalse(tip.has_filter)


class TestRemotePlateFeatures(unittest.TestCase):
    """Plate-specific RPCs (has_lid, etc.)."""

    @classmethod
    def setUpClass(cls):
        cls.local_deck = _make_deck()
        cls.fixture = _ServerFixture(cls.local_deck, _PORT + 3)
        cls.fixture.start()
        cls.remote_deck = RemoteDeck.connect(cls.fixture.url)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.stop()

    def test_has_lid_false(self):
        plate = self.remote_deck.get_resource("plate_01")
        self.assertFalse(plate.has_lid())

    def test_has_lid_true_after_adding(self):
        plate = self.remote_deck.get_resource("plate_01")
        lid = Lid(name="test_lid", size_x=127, size_y=85, size_z=10, nesting_z_height=5)
        self.local_deck.get_resource("plate_01").assign_child_resource(lid)
        self.assertTrue(plate.has_lid())
        # Clean up
        self.local_deck.get_resource("plate_01").unassign_child_resource(lid)

    def test_well_material_z_thickness_local(self):
        well = self.remote_deck.get_resource("plate_01_A1")
        self.assertAlmostEqual(well.material_z_thickness, 0.5)


# ---------------------------------------------------------------------------
# Integration test: full LiquidHandler cycle through remote deck
# ---------------------------------------------------------------------------

class TestRemoteDeckWithLiquidHandler(unittest.IsolatedAsyncioTestCase):
    """Run a full pick_up → aspirate → dispense → drop cycle through a RemoteDeck."""

    @classmethod
    def setUpClass(cls):
        set_volume_tracking(False)
        set_tip_tracking(False)
        cls.local_deck = _make_deck()
        cls.fixture = _ServerFixture(cls.local_deck, _PORT + 4)
        cls.fixture.start()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.stop()

    async def test_full_cycle(self):
        try:
            from pylabrobot.liquid_handling import LiquidHandler
            from pylabrobot.liquid_handling.backends.chatterbox import LiquidHandlerChatterboxBackend
        except ImportError:
            self.skipTest("liquid_handling extras not installed")

        deck = RemoteDeck.connect(self.fixture.url)
        lh = LiquidHandler(LiquidHandlerChatterboxBackend(num_channels=8), deck=deck)
        await lh.setup()

        tips = deck.get_resource("tip_rack_01")
        plate = deck.get_resource("plate_01")

        await lh.pick_up_tips(tips["A1"])
        await lh.aspirate(plate["A1"], vols=[50.0])
        await lh.dispense(plate["A2"], vols=[50.0])
        await lh.drop_tips(tips["A1"])

        await lh.stop()


if __name__ == "__main__":
    unittest.main()
