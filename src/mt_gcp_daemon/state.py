def determine_state_code(local_healthy, remote_healthy, remote_bgp_up):
    """
    Determines the system state based on the health of the local and remote regions,
    and the BGP (Border Gateway Protocol) session status in the remote region.

    This function returns a state code (int) based on the following logic:
    
    State Code Mapping:
      1: All systems healthy
      2: Local unhealthy, Remote healthy, Remote BGP up
      3: Local healthy, Remote unhealthy, Remote BGP up
      4: Both local and Remote unhealthy, Remote BGP up
      5: Local unhealthy, Remote healthy, BGP down - > Colo Infra and Cloud Infra
      6: Both local, Remote healthy, Remote BGP down - > Colo Infra and Cloud Infra
      0: Any other unexpected combination (failsafe/default)

    Args:
        local_healthy (bool): Whether the local region's services are healthy.
        remote_healthy (bool): Whether the remote region's services are healthy.
        remote_bgp_up (bool): Whether the remote BGP session is up.

    Returns:
        int: A state code representing the health combination.
    """
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
    return 0  # Default/fallback state


# Mapping of state codes to planned actions.
# Each tuple is in the form: (advertise_primary, advertise_secondary)
# - advertise_primary (bool): Whether the primary BGP prefix should be advertised.
# - advertise_secondary (bool): Whether the secondary BGP prefix should be advertised.
STATE_ACTIONS = {
    1: (True,  False),  # All good: advertise primary only
    2: (False, False),  # Failover candidate: don't advertise from failed local
    3: (True,  True),   # Redundant: advertise both (for resilience)
    4: (True,  False),  # Last resort: advertise from local despite failures
    5: (True,  False),  # No BGP on remote: fallback to local only
    6: (True,  True),   # BGP down, but services good: advertise both
}
