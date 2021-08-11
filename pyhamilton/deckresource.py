"""
Couplings to Hamilton deck layouts.

Module `pyhamilton.deckresource` provides convenience classes and methods for interacting safely with Hamilton's Layout (`.lay`) files. It also implements transformations between well indexes and coordinates for a variety of labware, such as plates and tips.
"""
import string, shutil, os, string, re
from datetime import datetime
from pyhamilton import OEM_LAY_PATH, LAY_BACKUP_DIR
from .oemerr import ResourceUnavailableError


class ResourceType:
    """
    Specifies a type of labware to extract using LayoutManager, and how.

    This class associates a resource class, such as `Plate96`, with either a literal labware identifier (`str`) that appears in the Hamilton Layout (`.lay`) file, or a pair of functions: one that identifies when a text line in a layout file could be assigned this resource, called `test`, and one that parses such a name out of the line, called `extract_name`.

    Typical usage:

    ```
    plate_type = ResourceType(Plate96, 'Cos_96_Rd_0001')
    lmgr = LayoutManager('layout.lay')
    plate = lmgr.assign_unused_resource(plate_type)
    ```

    Or:

    ```
    plate_type = ResourceType(Plate96,
            LayoutManager.line_has_prefixed_name('Cos_96_Rd_'),
            LayoutManager.name_from_line)
    lmgr = LayoutManager('layout.lay')
    plate1 = lmgr.assign_unused_resource(plate_type)
    plate2 = lmgr.assign_unused_resource(plate_type)
    ```

    Args:
      resource_class (class): a class that inherits from `DeckResource`. Instances of this class will be returned from `LayoutManager` when assigning resources, factory-style.
      *name_specifiers (list): This argument is unpacked with the "splat" operator (`*`) to enable polymorphism. One or the other of:
      - (two-argument form) an exact name (`str`) of a labware item that appears in the target layout file, or
      - (three-argument form) `test` and `extract_name` (see usage above):
          * `test`: a function [(`str`) -> `bool`] that identifies Layout file lines (`str`) that could be used to assign resources of this type
          * `extract_name`: a function [(`str`) -> `str`] that gets the desired name out of a line identified with `test`.

    """

    def __init__(self, resource_class, *args):
        self.resource_class = resource_class
        self.not_found_msg = None
        try:
            specific_name, = args
            self.test = lambda line: specific_name in re.split(r'\W', line)
            self.extract_name = lambda line: specific_name
            self.not_found_msg = 'No exact match for name "' + specific_name + '" to assign a resource of type ' + resource_class.__name__
        except ValueError:
            self.test, self.extract_name = args


class LayoutManager:
    """Optionally activates a Hamilton layout and helps access its contents.

    A `LayoutManager` manages the consistent assignment of `DeckResource` objects to items in a Hamilton Layout file (`.lay`). A `LayoutManager` must be used to set the active pyhamilton layout file, but use of this class is strictly optional when sending `pyhamilton` commands using `send_command`; names may be passed as string literals in commands instead if they are known in advance. The advantage to specifying all labware using `ResourceManager` is that resource names are verified to be present in the active layout file at runtime, and guaranteed never used more than once, both of which are necessary to avoid silent Hamilton errors.

    Example usage:

    ```
    lmgr = LayoutManager('layout.lay')
    plate = lmgr.assign_unused_resource(ResourceType(Plate24, 'plate_0'))
    culture_reservoir = lmgr.assign_unused_resource(ResourceType(Plate96, 'culture'))
    inducer_tips = lmgr.assign_unused_resource(ResourceType(Tip96, 'inducer_tips'))
    ```
    """

    _managers = {}
    @staticmethod
    def get_manager(checksum):
        """Return a `LayoutManager` previously instantiated for a layout file that has the specified checksum.

        Typically used when accessing the same layout file from multiple "threads" in the same process (using the `threading` module) to prevent name double-counting.

        Args:
          checksum (str): a checksum found at the end of a Hamilton Layout (`.lay`) file.
        """
        return LayoutManager._managers[checksum]

    @staticmethod
    def initial_printable(line, start=0):
        if not line:
            return ''
        end = start
        while end < len(line) and line[end] in string.printable:
            end += 1
        return line[start:end]

    @staticmethod
    def layline_objid(line):
        keys = 'ObjId', 'LabwareName'
        if 'Labware' in LayoutManager.layline_first_field(line):
            keys = 'Id', *keys
        for key in keys:
            try:
                start = line.index(key) + len(key) + 1
                return LayoutManager.initial_printable(line, start)
            except ValueError:
                pass
        else:
            return None

    @staticmethod
    def layline_first_field(line):
        return LayoutManager.initial_printable(line)

    @staticmethod
    def field_starts_with(field, prefix):
        try:
            return field.index(prefix) == 0
        except ValueError:
            return False

    @staticmethod
    def name_from_line(line):
        field = LayoutManager.layline_objid(line)
        if field:
            return field
        return LayoutManager.layline_first_field(line)

    @staticmethod
    def line_has_prefixed_name(prefix):
        def has_prefix(line):
            return LayoutManager.field_starts_with(LayoutManager.name_from_line(line), prefix)
        return has_prefix

    @staticmethod
    def _read_layfile_lines(layfile_path):
        buff = ''
        lines = []
        with open(layfile_path, 'rb') as f:
            for c in f.read():
                try:
                    c = bytes([c]).decode('utf-8')
                except UnicodeDecodeError:
                    continue
                buff += c
                if c in '\n\r\t':
                    lines.append(buff.strip())
                    buff = ''
        if buff:
            lines.append(buff)
        return lines

    @staticmethod
    def _layfile_checksum(layfile_path):
        lay_lines = LayoutManager._read_layfile_lines(layfile_path)
        return lay_lines[-1].split('checksum=')[1].split('$$')[0]

    @staticmethod
    def layfiles_equal(lay_path_1, lay_path_2):
        return LayoutManager._layfile_checksum(lay_path_1) == LayoutManager._layfile_checksum(lay_path_2)

    def __init__(self, layfile_path, install=True):
        self.lines = self._read_layfile_lines(layfile_path)
        self.resources = {}
        self.checksum = self._layfile_checksum(layfile_path)
        self._managers[self.checksum] = self
        if install and not LayoutManager.layfiles_equal(layfile_path, OEM_LAY_PATH):
                print('BACKING UP AND INSTALLING NEW LAYFILE')
                shutil.copy2(layfile_path, os.path.join(LAY_BACKUP_DIR, datetime.today().strftime('%Y%m%d_%H%M%S_') + os.path.basename(layfile_path)))
                shutil.copy2(layfile_path, OEM_LAY_PATH)

    def assign_unused_resource(self, restype, order_key=None, reverse=False):
        """Create a new deck resource after finding and assigning an unused name that matches the resource type.

        This method searches through the layout file for one new layout name that matches the given resource type. It reserves this layout name permanently so that no later calls to `assign_unused_resource` can create a deck resource with the same layout name. Returns a `DeckResource`.

        Args:
          restype (ResourceType): The resource type, which consists of a resource class (descendent of `DeckResource`) and some string pattern matching functions to identify the desired layout names.
          order_key (Callable[[DeckResource], Comparable]): Optional; when multiple layout names match, specifies a function of one argument that is used to extract a comparison key from each candidate `DeckResource` object. The arg-min or arg-max of `order_key` will be returned, depending on `reverse`. By default, lexicographic order by layout name is used, which is suitable for most use cases, e.g. plates with layout names "pcr-plate-a", "pcr-plate-b", "pcr-plate-c", ... will be returned in the expected order.
          reverse (bool): Optional; use reverse-lexicographic order for layout names, useful for e.g. plate stacking applications, or reverse the order imposed by `order_key` if it is given.
        Returns:

          A new instance of the resource class (descendent of `DeckResource`) from the given `ResourceType` `restype`.

        Raises:
          ResourceUnavailableError: no names in the layout file that have not already been assigned match the resource type

        """
        if order_key is None:
            order_key = lambda r: r.layout_name()
        if not isinstance(restype, ResourceType):
            raise TypeError('Must provide a ResourceType to be assigned')
        matching_ress = []
        for line in self.lines:
            if restype.test(line):
                match_name = restype.extract_name(line)
                if match_name in self.resources:
                    continue
                matching_ress.append(restype.resource_class(match_name))
        if not matching_ress:
            msg = restype.not_found_msg or 'No unassigned resource of type ' + restype.resource_class.__name__ + ' available'
            raise ResourceUnavailableError(msg)
        choose = max if reverse else min
        new_res = choose(matching_ress, key=order_key)
        self.resources[new_res.layout_name()] = new_res
        return new_res


class ResourceIterItem:

    def __init__(self, resource, index):
        self.parent_resource = resource
        self.index = index
        self.history = []


class Tip(ResourceIterItem):
    pass


class Vessel(ResourceIterItem):

    ADD = 0
    REMOVE = 1

    def record_removal(self, ml, dest=None):
        if dest is not None and not isinstance(dest, Vessel):
            raise ValueError('Sources and destinations in Vessel contents records must be Vessels')
        self.history.append((Vessel.REMOVE, ml, dest))

    def record_addition(self, ml, source):
        if not isinstance(source, Vessel):
            raise ValueError('Sources and destinations in Vessel contents records must be Vessels')
        self.history.append((Vessel.ADD, ml, source))

    def current_volume(self):
        return sum((ml if direction == Vessel.ADD else -ml) for direction, ml, _ in self.history)


class DeckResource:

    class align:
        VERTICAL = 'v'
        STD_96 = 'std_96'

    class types:
        TIP, VESSEL = range(2)

    def __init__(self, layout_name):
        raise NotImplementedError()

    def _alignment_delta(self, int_start, int_end):
        raise NotImplementedError() # (delta x, delta y, alignment properties list)

    def _assert_idx_in_range(self, idx_or_vessel):
        if isinstance(idx_or_vessel, Vessel):
            idx = idx_or_vessel.index
        else:
            idx = idx_or_vessel
        if not 0 <= idx < self._num_items:
            raise ValueError('Index ' + str(idx) + ' not in range for resource')

    def layout_name(self):
        """The layout name of this specific deck resource.

        Returns:
          The name (`str`) associated with this specific deck resource in the Hamilton Layout (`.lay`) file it came from.
        """
        return self._layout_name # default; override if needed. (str) 

    def position_id(self, idx):
        """The identifier used for one of a sequence of positions inside this labware.

        For labware with multiple positions, each position has a different identity, usually represented as a short string that will match the identifier scheme for this resource in the Hamilton Layout file it came from. The identifiers will usually be familiar from a laboratory setting.

        Examples

        - 96-well plates have 96 positions, each identified with a letter and a number like `'D4'`. For a `Plate96` instance named `plate`, `plate.position_id(0)` is `'A1'`, `plate.position_id(1)` is `'B1'`, and `plate.position_id(95)` is `'H12'`.
        - Hamilton racks of 96 tips have 96 positions, identified with integer strings like `'87'` that start with `'1'` at the top left tip and increase down columns (8 positions each) first. For a `Tip96` instance named `tips`, `tips.position_id(0)` is `'1'`, `tips.position_id(1)` is `'2'`, and `tips.position_id(95)` is `'96'`.

        Args:
          idx (int): the index into the sequence of positions. Note: `idx` is zero-indexed across all labware according to python convention, while most real-world labware positions are 1-indexed.

        Returns:
          The identifier (`str`) associated with the position `idx` specific deck resource in the Hamilton Layout (`.lay`) file it came from.

        Raises:
          NotImplementedError: The deck resource does not have positions.
        """
        raise NotImplementedError()

    def alignment_delta(self, start, end):
        args = {'start':start, 'end':end}
        for pos in args:
            if isinstance(args[pos], Vessel):
                if args[pos].parent_resource is not self:
                    raise ValueError('Positions provided as vessels, '
                            'but do not belong to this resource')
                args[pos] = start.index
            else:
                try:
                    args[pos] += 0
                except TypeError:
                    raise ValueError('Positions provided for delta must be integers or vessels')
            self._assert_idx_in_range(args[pos])
        return self._alignment_delta(args['start'], args['end'])

    def __iter__(self):
        for i in self._items:
            yield i


class Standard96(DeckResource):
    """Labware types with 96 positions that use a letter-number id scheme like `'A1'`.
    """

    def well_coords(self, idx):
        self._assert_idx_in_range(idx)
        return int(idx)//8, int(idx)%8

    def _alignment_delta(self, start, end):
        [self._assert_idx_in_range(p) for p in (start, end)]
        xs, ys = self.well_coords(start)
        xe, ye = self.well_coords(end)
        return (xe - xs, ye - ys, [DeckResource.align.STD_96]
                                  + ([DeckResource.align.VERTICAL] if xs == xe and ys != ye else []))

    def position_id(self, idx):
        x, y = self.well_coords(idx)
        return 'ABCDEFGH'[y] + str(x + 1)


class Tip96(Standard96):

    def __init__(self, layout_name):
        self._layout_name = layout_name
        self._num_items = 96
        self.resource_type = DeckResource.types.TIP
        self._items = [Tip(self, i) for i in range(self._num_items)]

    def position_id(self, idx): # tips use 1-indexed int ids descending columns first
        self._assert_idx_in_range(idx)
        return str(idx + 1) # switch to standard advance through row first


class Plate96(Standard96):

    def __init__(self, layout_name):
        self._layout_name = layout_name
        self._num_items = 96
        self.resource_type = DeckResource.types.VESSEL
        self._items = [Vessel(self, i) for i in range(self._num_items)]


class Plate24(DeckResource):

    def __init__(self, layout_name):
        self._layout_name = layout_name
        self._num_items = 24
        self.resource_type = DeckResource.types.VESSEL
        self._items = [Vessel(self, i) for i in range(self._num_items)]

    def well_coords(self, idx):
        self._assert_idx_in_range(idx)
        return int(idx)//4, int(idx)%4

    def _alignment_delta(self, start, end):
        [self._assert_idx_in_range(p) for p in (start, end)]
        xs, ys = self.well_coords(start)
        xe, ye = self.well_coords(end)
        return (xe - xs, ye - ys, [DeckResource.align.VERTICAL] if xs == xe and ys != ye else [])

    def position_id(self, idx):
        x, y = self.well_coords(idx)
        return 'ABCD'[y] + str(x + 1)


class Plate12(DeckResource):

    def __init__(self, layout_name):
        self._layout_name = layout_name
        self._num_items = 12
        self.resource_type = DeckResource.types.VESSEL
        self._items = [Vessel(self, i) for i in range(self._num_items)]

    def well_coords(self, idx):
        self._assert_idx_in_range(idx)
        return int(idx)//3, int(idx)%3

    def _alignment_delta(self, start, end):
        [self._assert_idx_in_range(p) for p in (start, end)]
        xs, ys = self.well_coords(start)
        xe, ye = self.well_coords(end)
        return (xe - xs, ye - ys, [DeckResource.align.VERTICAL] if xs == xe and ys != ye else [])

    def position_id(self, idx):
        x, y = self.well_coords(idx)
        return 'ABC'[y] + str(x + 1)

class Plate6(DeckResource):

    def __init__(self, layout_name):
        self._layout_name = layout_name
        self._num_items = 6
        self.resource_type = DeckResource.types.VESSEL
        self._items = [Vessel(self, i) for i in range(self._num_items)]

    def well_coords(self, idx):
        self._assert_idx_in_range(idx)
        return int(idx)//2, int(idx)%2

    def _alignment_delta(self, start, end):
        [self._assert_idx_in_range(p) for p in (start, end)]
        xs, ys = self.well_coords(start)
        xe, ye = self.well_coords(end)
        return (xe - xs, ye - ys, [DeckResource.align.VERTICAL] if xs == xe and ys != ye else [])

    def position_id(self, idx):
        x, y = self.well_coords(idx)
        return 'AB'[y] + str(x + 1)

