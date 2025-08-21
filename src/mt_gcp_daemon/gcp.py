import os
import logging
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(os.getenv("LOGGER_NAME", "HEALTH_CHECK_DAEMON"))

def build_compute_client(creds_path: str):
    """
    Initializes the Google Compute Engine client using service account credentials.

    Args:
        creds_path (str): Path to the JSON key file.

    Returns:
        googleapiclient.discovery.Resource: Authorized Compute Engine client.
    
    Raises:
        FileNotFoundError: If the credentials file doesn't exist.
    """
    if not os.path.exists(creds_path):
        raise FileNotFoundError(f"GCP credentials file not found: {creds_path}")
    creds = service_account.Credentials.from_service_account_file(creds_path)
    return build('compute', 'v1', credentials=creds, cache_discovery=False)


def validate_gcp_connectivity(project: str, regions: list[str], compute):
    """
    Validates GCP API connectivity by querying project and region resources.

    Args:
        project (str): GCP project ID.
        regions (list[str]): List of region names to validate.
        compute: Authorized Compute Engine client.

    Raises:
        HttpError: If access is denied or project/region does not exist.
    """
    logger.info("Validating GCP connectivity...")
    resp = compute.projects().get(project=project).execute()
    logger.info(f"GCP connectivity validated for project: {resp.get('name', project)}")
    for region in regions:
        compute.regions().get(project=project, region=region).execute()
        logger.info(f"Validated access to region: {region}")


def backend_services_healthy(project, region, compute_client):
    """
    Returns a closure that checks if all backends in the specified region are healthy.

    Args:
        project (str): GCP project ID.
        region (str): GCP region name.
        compute_client: Authorized Compute Engine client.

    Returns:
        Callable[[], bool]: Function that returns `True` if all backends are healthy.
    """
    def _check():
        try:
            request = compute_client.regionBackendServices().list(project=project, region=region)
            response = request.execute()
            items = response.get('items', [])
            if not items:
                logger.info(f"No backend services found in {project}/{region}")
                return True  # Treat no services as healthy
            for bsvc in items:
                bname = bsvc['name']
                for backend in bsvc.get('backends', []):
                    health = compute_client.regionBackendServices().getHealth(
                        project=project,
                        region=region,
                        backendService=bname,
                        body={"group": backend['group']}
                    ).execute()
                    if health == {"kind": "compute#backendServiceGroupHealth"}:
                        logger.warning(f"Incomplete health response for {bname} ({project}/{region})")
                        return False
                    for h in health.get('healthStatus', []):
                        if h.get('healthState') != 'HEALTHY':
                            logger.warning(f"Unhealthy backend: {backend['group']} in {bname} ({project}/{region})")
                            return False
            return True
        except HttpError as e:
            if e.resp.status in [403, 404]:
                logger.error(f"Permanent error checking backend health for {project}/{region}: {e}")
                raise
            logger.warning(f"Transient error checking backend health for {project}/{region}: {e}")
            return False
        except Exception as e:
            logger.exception(f"Unexpected error backend health {project}/{region}: {e}")
            return False
    return _check


def router_bgp_sessions_healthy(project, region, router, compute_client):
    """
    Returns a closure that checks the health of BGP sessions on a given Cloud Router.

    Args:
        project (str): GCP project ID.
        region (str): GCP region.
        router (str): Name of the Cloud Router.
        compute_client: Authorized Compute Engine client.

    Returns:
        Callable[[], Tuple[bool, dict]]:
            A callable that returns (any_peer_up: bool, peer_statuses: dict).
    """
    def _check():
        try:
            status_resp = compute_client.routers().getRouterStatus(
                project=project, region=region, router=router
            ).execute()
            peers = status_resp.get('result', {}).get('bgpPeerStatus', [])
            peer_statuses = {peer['name']: peer['status'] for peer in peers}
            if not peers:
                logger.warning(f"No BGP peers found for router {router} in {project}/{region}")
                return False, {}
            any_up = any(peer.get('status') == 'UP' for peer in peers)
            logger.info(f"[{project}/{region}/{router}] BGP peer statuses: {peer_statuses}")
            return any_up, peer_statuses
        except HttpError as e:
            if e.resp.status in [403, 404]:
                logger.error(f"Permanent error checking BGP sessions for {router} in {project}/{region}: {e}")
                raise
            logger.warning(f"Transient error checking BGP sessions for {router} in {project}/{region}: {e}")
            return False, {}
        except Exception as e:
            logger.exception(f"Unexpected error BGP session status {router} {project}/{region}: {e}")
            return False, {}
    return _check


def update_bgp_advertisement(project, region, router, prefix, compute_client, advertise=True):
    """
    Returns a closure that performs BGP route advertisement or withdrawal.

    Args:
        project (str): GCP project ID.
        region (str): GCP region.
        router (str): Name of the Cloud Router.
        prefix (str): IP prefix to advertise or withdraw.
        compute_client: Authorized Compute Engine client.
        advertise (bool): If True, adds the prefix; if False, removes it.

    Returns:
        Callable[[], bool]: Function that performs the update and returns success status.
    """
    def _update():
        try:
            router_data = compute_client.routers().get(project=project, region=region, router=router).execute()
            custom = router_data.get('bgp', {}).get('advertisedIpRanges', [])
            already = any(r.get('range') == prefix for r in custom)

            if advertise and not already:
                custom.append({'range': prefix})
                action = "Adding"
            elif not advertise and already:
                custom = [r for r in custom if r.get('range') != prefix]
                action = "Withdrawing"
            else:
                return True  # No change needed

            patch_body = {'bgp': {'advertisedIpRanges': custom}}
            op = compute_client.routers().patch(
                project=project, region=region, router=router, body=patch_body
            ).execute()
            logger.info(f"{action} prefix {prefix} [{project}/{region}] op={op.get('name')}")
            return True
        except HttpError as e:
            if e.resp.status in [403, 404]:
                logger.error(f"Permanent error updating BGP advertisement for {prefix} in {project}/{region}: {e}")
                raise
            logger.warning(f"Transient error updating BGP advertisement for {prefix} in {project}/{region}: {e}")
            return False
        except Exception as e:
            logger.exception(f"Unexpected error updating BGP advertisement for {prefix} in {project}/{region}: {e}")
            return False
    return _update
