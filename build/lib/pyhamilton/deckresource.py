import string, shutil, os, string, re
from datetime import datetime
from pyhamilton import OEM_LAY_PATH, LAY_BACKUP_DIR
from .oemerr import ResourceUnavailableError


class ResourceType:

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

    _managers = {}
    @staticmethod
    def get_manager(checksum):
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
        return self._layout_name # default; override if needed. (str) The name of this specific deck resource in the .lay file

    def position_id(self, idx):
        raise NotImplementedError() # (str) The position id according to the .lay definition associated with the (zero-indexed) idx

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
