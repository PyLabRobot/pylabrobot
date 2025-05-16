from typing import List
from pylabrobot.resources import TipSpot
from pylabrobot.liquid_handling import LiquidHandler
from pylabrobot.liquid_handling.errors import ChannelizedError


async def probe_tip_presence_via_pickup(
    lh: LiquidHandler,
    tip_spots: List[TipSpot],
    use_channels: List[int]
) -> List[int]:
    """
    Probe tip presence by attempting pickup on each TipSpot.

    Args:
        lh: The LiquidHandler instance.
        tip_spots: TipSpots to probe.
        use_channels: Channels to use (must match tip_spots length).

    Returns:
        List[int]: 1 if tip is present, 0 otherwise.
    """
    if len(use_channels) != len(tip_spots):
        raise ValueError(
            f"Length mismatch: received {len(use_channels)} channels for "
            f"{len(tip_spots)} tip spots. One channel must be assigned per tip spot."
        )

    presence_flags = [1] * len(tip_spots)
    z_height = tip_spots[0].get_absolute_location(z="top").z + 5

    # Step 1: Cluster tip spots by x-coordinate
    clusters_by_x = {}
    for idx, tip_spot in enumerate(tip_spots):
        x = tip_spot.location.x
        clusters_by_x.setdefault(x, []).append((tip_spot, use_channels[idx], idx))

    sorted_clusters = [clusters_by_x[x] for x in sorted(clusters_by_x)]

    # Step 2: Probe each cluster
    for cluster in sorted_clusters:
        tip_subset, channel_subset, index_subset = zip(*cluster)

        try:
            await lh.pick_up_tips(
                list(tip_subset),
                use_channels=list(channel_subset),
                minimum_traverse_height_at_beginning_of_a_command=z_height,
                z_position_at_end_of_a_command=z_height
            )
        except ChannelizedError as e:
            for ch in e.errors:
                if ch in channel_subset:
                    failed_local_idx = channel_subset.index(ch)
                    presence_flags[index_subset[failed_local_idx]] = 0

        # Step 3: Drop tips immediately after probing
        successful = [
            (spot, ch) for spot, ch, i in cluster
            if presence_flags[i] == 1
        ]
        if successful:
            try:
                await lh.drop_tips(
                    [spot for spot, _ in successful],
                    use_channels=[ch for _, ch in successful],
                    minimum_traverse_height_at_beginning_of_a_command=z_height
                )
            except Exception as e:
                print(f"Warning: drop_tips failed for cluster at x={cluster[0][0].location.x}: {e}")

    return presence_flags
