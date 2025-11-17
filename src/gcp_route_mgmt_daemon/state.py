def determine_state_code(local_healthy, remote_healthy, remote_bgp_up):
    """
    Determines the system state based on the health of the local and remote regions,
    and the BGP (Border Gateway Protocol) session status in the remote region.

    This function returns a state code (int) based on the following logic:

    State Code Mapping:
      0: Any other unexpected combination (failsafe/default - NO CHANGE to advertisements)
         Also returned when any health parameter is None (unknown/monitoring unavailable)
      1: All systems healthy
      2: Local unhealthy, Remote healthy, Remote BGP up
      3: Local healthy, Remote unhealthy, Remote BGP up
      4: Both local and Remote unhealthy, Remote BGP up
      5: Local unhealthy, Remote healthy, BGP down - > Colo Infra and Cloud Infra
      6: Both local, Remote healthy, Remote BGP down - > Colo Infra and Cloud Infra

    Args:
        local_healthy (bool or None): Whether the local region's services are healthy.
            None indicates monitoring is unavailable/unreliable.
        remote_healthy (bool or None): Whether the remote region's services are healthy.
            None indicates monitoring is unavailable/unreliable.
        remote_bgp_up (bool or None): Whether the remote BGP session is up.
            None indicates monitoring is unavailable/unreliable.

    Returns:
        int: A state code representing the health combination.

    Special Handling:
        If any parameter is None (unknown health due to monitoring unavailability),
        returns State 0 to prevent making routing decisions based on unreliable data.
        This maintains current routing state until reliable monitoring data is available.
    """
    # If any health status is unknown/unreliable, return State 0 (no changes)
    # This prevents making routing decisions based on monitoring failures
    if local_healthy is None or remote_healthy is None or remote_bgp_up is None:
        return 0  # Unknown health -> maintain current state

    if local_healthy and remote_healthy and remote_bgp_up:
        return 1
    elif not local_healthy and remote_healthy and remote_bgp_up:
        return 2
    elif local_healthy and not remote_healthy and remote_bgp_up:
        return 3
    elif not local_healthy and not remote_healthy and remote_bgp_up:
        return 4
    elif not local_healthy and remote_healthy and not remote_bgp_up:
        return 5
    elif local_healthy and remote_healthy and not remote_bgp_up:
        return 6
    return 0  # Default Do Nothing


# Mapping of state codes to planned actions.
# Each tuple is in the form: (advertise_primary, advertise_secondary)
# - advertise_primary (bool): Whether the primary BGP prefix should be advertised.
# - advertise_secondary (bool): Whether the secondary BGP prefix should be advertised.
# - None: Indicates no change should be made (State 0 only)
STATE_ACTIONS = {
    0: (None,  None),   # No change: maintain current advertisements (failsafe/default)
    1: (True,  False),  # All good: advertise primary only
    2: (False, False),  # Failover candidate: don't advertise from failed local
    3: (True,  True),   # Redundant: advertise both (for resilience)
    4: (True,  False),  # Last resort: advertise from local despite failures - Now skeptical and will require verification
    5: (True,  False),  # No BGP on remote: fallback to local only
    6: (True,  True),   # BGP down, but services good: advertise both
}
