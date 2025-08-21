import logging
import requests
import os

# Logger used for all Cloudflare-related operations
logger = logging.getLogger(os.getenv("LOGGER_NAME", "HEALTH_CHECK_DAEMON"))


def validate_cloudflare_connectivity(account_id: str, token: str):
    """
    Validates that the provided Cloudflare API token is valid and has access
    to the Magic Transit route API for the specified account.

    This function performs two checks:
    1. Verifies the token is authorized via the /tokens/verify endpoint.
    2. Verifies access to the Magic Transit routes list endpoint.

    Args:
        account_id (str): Cloudflare account ID.
        token (str): Cloudflare API token (must be scoped for Magic Transit access).

    Raises:
        HTTPError: If either API request returns a non-2xx status.
        RuntimeError: If the Cloudflare response indicates failure.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # Validate token
    verify_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/tokens/verify"
    r = requests.get(verify_url, headers=headers, timeout=10)
    r.raise_for_status()

    # Validate access to routes list
    list_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/magic/routes"
    r2 = requests.get(list_url, headers=headers, timeout=10)
    r2.raise_for_status()
    data = r2.json()

    if not data.get('success'):
        raise RuntimeError(f"Cloudflare API error: {data.get('errors')}")

    logger.info(f"Cloudflare connectivity OK. Found {len(data.get('result', {}).get('routes', []))} routes.")


def update_routes_by_description_bulk(account_id: str, token: str, desc_substring: str, desired_priority: int):
    """
    Performs a bulk update of Magic Transit routes that match a specific description substring.
    If the route's current priority differs from `desired_priority`, it will be updated.

    Args:
        account_id (str): Cloudflare account ID.
        token (str): Cloudflare API token.
        desc_substring (str): Substring to match against route descriptions.
        desired_priority (int): New priority value to assign to matching routes.

    Returns:
        bool: True if successful or no changes were needed.

    Raises:
        HTTPError: If Cloudflare API requests fail.
        RuntimeError: If the API responds with an error status.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # Retrieve all routes
    list_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/magic/routes"
    r = requests.get(list_url, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()

    if not data.get('success'):
        raise RuntimeError(f"Cloudflare API error on list: {data.get('errors')}")

    routes = data['result']['routes']
    updates = []

    # Filter and queue updates
    for route in routes:
        desc = route.get('description', '')
        if desc_substring and desc_substring in desc:
            if route.get('priority') != desired_priority:
                updates.append({
                    "id": route['id'],
                    "prefix": route.get('prefix'),
                    "nexthop": route.get('nexthop'),
                    "priority": desired_priority
                })
                logger.info(f"Queue route {route['id']} ({desc}) -> priority {desired_priority}")

    # No update needed
    if not updates:
        logger.debug("No Cloudflare route changes needed.")
        return True

    # Bulk update
    payload = {"routes": updates}
    put_resp = requests.put(list_url, headers=headers, json=payload, timeout=60)
    put_resp.raise_for_status()
    result = put_resp.json()

    if result.get('success'):
        logger.info(f"Bulk update successful: {result.get('result', {}).get('modified', 0)} routes modified")
        return True

    raise RuntimeError(f"Bulk update errors: {result.get('errors')}")
