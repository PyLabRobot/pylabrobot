class ChannelHead:

    def __init__(self):
        pass

    def pack_moves(self, moves):
        pass # return (binary channel pattern string, remaining moves)

    def can_move_simul(move1, move2): # both arguments are 4-tuples, (tips, ml, source, dest) 
        tips, mls, sources, dests = zip(move1, move2)
        move_components = zip(sources, tips, dests)
        xy_deltas = (None, None, None)
        for i, (pos1, pos2) in enumerate(zip(*move_components)):
            resource = pos1.parent_resource 
            if resource is not pos2.parent_resource:
                return False
            dx, dy, constraints = resource.alignment_delta(pos1, pos2)
            if not all(c in constraints for c in self.alignment_constraints):
                return False
            alignment_deltas[i] = (dx, dy)
        ad_source, ad_tips, ad_dests = alignment_deltas
        return ad_source == ad_tips == ad_dests


class Independent8Channel(ChannelHead):

    def __init__(self):
        self.alignment_constraints = [DeckResource.align.VERTICAL]


class Standard96Channel(ChannelHead):

    def __init__(self):
        self.alignment_constraints = [DeckResource.align.STD_96]


class HamiltonDevice:
    # Just a structure with some guaranteed pieces

    def __init__(self, interface, heads):
        if not (isinstance(interface, HamiltonInterface) and all((isinstance(h, ChannelHead) for h in heads))
               and isinstance(resource_mgr, LayoutManager)):
            raise TypeError("HamiltonDevice instantiated with wrong types") # todo maybe more informative
        interface.bind_device(self)
        layout_mgr.bind_device(self)
        self.heads = heads
        for head in heads:
            head.bind_device(self)


class HamiltonAction:
    # Defined as a robot action that can be accomplished by some finite sequence of sent OEM commands.
    # HamiltonActions are nestable, and each implements execute() which might call child execute()s.
    # HamiltonActions are either instantiated with a particular HamiltonDevice or are implicitly tied to one
    # execute() is responsible for doing its own error handling.
    # Base-level actions like aspirate and dispense interpret the responses and errors from the robot device
    # A possible() method is implemented to facilitate search over HamiltonActions.

    def possible(self):
        return True

    def execute(self):
        raise NotImplementedError()


class GroupableAction(HamiltonAction):
    pass


class TipPickup(GroupableAction):

    def __init__(self, tip):
        self.tip = tip

    def execute(self):
        id = hammy.send_command(PICKUP, {'labwarePositions':str(tip.parent_resource.layout_name()) + ', ' + str(tip.index) + ';'})
        response = hammy.wait_on_response(id)


class Transfer(GroupableAction):

    def __init__(self, tip, ml, source, dest):
        self.tip = tip
        self.ml = ml
        self.source = source
        self.dest = dest
        self.action_type = HamiltonAction.MOVE

    def execute(self):
        self.pickup.execute()
        self.aspirate.execute()
        self.dispense.execute()

    def __iter__(self):
        return iter((self.tip, self.ml, self.source, self.dest))


class FlyTransfer(Transfer):

    def __init__(self, tip, ml, source, dest):
        self.tip = tip
        self.ml = ml
        self.source = source
        self.dest = dest
        self.action_type = HamiltonAction.MOVE


class Mix(HamiltonAction):

    def __init__(self, tip, ml, target, times):
        self.params = (tip, ml, target, times)
        self.action_type = HamiltonAction.MIX


class HamiltonCoordinator:

    def __init__(self, hamilton_interface, channel_heads):
        if not (isinstance(hamilton_interface, HamiltonInterface) and hamilton_interface.is_open()):
            raise ValueError('Coodinator can only start with an open HamiltonInterface')
        self.queued_actions = []
        self.heads = channel_heads
        hamilton_interface.send_command(None, INITIALIZE, block=True)
        print('Initialized Hamilton')

    def stage(self, action):
        if not isinstance(action, HamiltonAction):
            raise TypeError('Coordinator can only stage or execute HamiltonActions')
        self.queued_actions.append

    def execute(self, specific_actions=None):
        if not self.hamilton_interface.is_open():
            raise ValueError('Coodinator can only execute commands with an open HamiltonInterface')
        if specific_actions is None:
            work_list = self.queued_actions[:]
        else:
            try:
                work_list = iter(specific_actions)
            except TypeError:
                work_list = [specific_actions]
        if not work_list:
            return
        while work_list:
            action = work_list.pop(0)
            if not isinstance(action, HamiltonAction):
                raise TypeError('Coordinator can only stage or execute HamiltonActions')
            if not isinstance(action, GroupableAction):
                action.execute()
                continue
            to_move = tuple([] for h in self.heads)
            for hi, head in enumerate(self.heads):
                for move in work_list:
                    if move.source is first_move.source or move.dest is first_move.dest:
                        continue
                    if head.can_move_simul(first_move, move):
                        to_move[hi].append(move)

    def wait_for_all():
        self.hamilton_interface.block_until_clear()


