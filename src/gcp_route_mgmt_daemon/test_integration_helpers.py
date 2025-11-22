"""
Integration Test Helpers for Workload Identity Federation

This module provides utility functions for manual integration testing of
Workload Identity Federation in real GCP environments. These helpers are
designed to be run manually in test environments to validate authentication
configuration.

These are NOT unit tests - they make real API calls to GCP and require
proper authentication to be configured.

Usage:
    python -m gcp_route_mgmt_daemon.test_integration_helpers

Environment Variables Required:
    - GCP_PROJECT: GCP project ID to test against
    - USE_WORKLOAD_IDENTITY: Set to 'true' to test Workload Identity
    - GOOGLE_APPLICATION_CREDENTIALS: Path to service account key (if not using Workload Identity)

Author: Nathan Bray
Version: 1.0
Last Modified: 2025
"""

import os
import sys
import logging
from typing import Tuple, Optional

# Configure logging for integration tests
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_workload_identity_connectivity() -> Tuple[bool, str]:
    """
    Test Workload Identity / Application Default Credentials connectivity.

    This function attempts to authenticate using Workload Identity and make
    a simple GCP API call to validate that authentication is working correctly.

    Returns:
        Tuple[bool, str]: (success, message)
            - success: True if authentication and API call succeeded
            - message: Human-readable result message

    Example:
        success, message = test_workload_identity_connectivity()
        if success:
            print(f"✓ {message}")
        else:
            print(f"✗ {message}")
    """
    try:
        from gcp_route_mgmt_daemon import gcp as gcp_mod

        logger.info("Testing Workload Identity / Application Default Credentials...")

        # Get project from environment
        project_id = os.getenv('GCP_PROJECT')
        if not project_id:
            return False, "GCP_PROJECT environment variable not set"

        # Build client with Workload Identity
        logger.info("Building GCP client with Workload Identity...")
        compute = gcp_mod.build_compute_client(use_workload_identity=True)

        # Test API call - get project info
        logger.info(f"Testing API call: getting project info for {project_id}...")
        project_resp = compute.projects().get(project=project_id).execute()
        project_name = project_resp.get('name', project_id)

        success_msg = (
            f"Workload Identity authentication successful!\n"
            f"  Project: {project_name} ({project_id})\n"
            f"  Authentication: Application Default Credentials\n"
            f"  API connectivity: Validated"
        )
        return True, success_msg

    except Exception as e:
        error_msg = f"Workload Identity authentication failed: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return False, error_msg


def test_service_account_connectivity(creds_path: Optional[str] = None) -> Tuple[bool, str]:
    """
    Test service account key file authentication connectivity.

    Args:
        creds_path: Path to service account JSON key file.
            If None, uses GOOGLE_APPLICATION_CREDENTIALS environment variable.

    Returns:
        Tuple[bool, str]: (success, message)

    Example:
        success, message = test_service_account_connectivity('/path/to/key.json')
        print(message)
    """
    try:
        from gcp_route_mgmt_daemon import gcp as gcp_mod

        # Get credentials path
        if creds_path is None:
            creds_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')

        if not creds_path:
            return False, "Service account key path not provided and GOOGLE_APPLICATION_CREDENTIALS not set"

        logger.info(f"Testing service account key file authentication: {creds_path}...")

        # Get project from environment
        project_id = os.getenv('GCP_PROJECT')
        if not project_id:
            return False, "GCP_PROJECT environment variable not set"

        # Build client with service account key
        logger.info("Building GCP client with service account key...")
        compute = gcp_mod.build_compute_client(creds_path=creds_path)

        # Test API call
        logger.info(f"Testing API call: getting project info for {project_id}...")
        project_resp = compute.projects().get(project=project_id).execute()
        project_name = project_resp.get('name', project_id)

        success_msg = (
            f"Service account authentication successful!\n"
            f"  Project: {project_name} ({project_id})\n"
            f"  Credentials: {creds_path}\n"
            f"  API connectivity: Validated"
        )
        return True, success_msg

    except Exception as e:
        error_msg = f"Service account authentication failed: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return False, error_msg


def test_authentication_auto_detection() -> Tuple[bool, str]:
    """
    Test authentication mode auto-detection based on configuration.

    This tests the logic that chooses between Workload Identity and
    service account key based on environment variables.

    Returns:
        Tuple[bool, str]: (success, message)
    """
    try:
        use_workload_identity = os.getenv('USE_WORKLOAD_IDENTITY', 'false').lower() == 'true'
        has_credentials = os.getenv('GOOGLE_APPLICATION_CREDENTIALS') is not None

        logger.info("Testing authentication mode auto-detection...")
        logger.info(f"  USE_WORKLOAD_IDENTITY: {use_workload_identity}")
        logger.info(f"  Has GOOGLE_APPLICATION_CREDENTIALS: {has_credentials}")

        if use_workload_identity:
            logger.info("Auto-detection result: Using Workload Identity")
            return test_workload_identity_connectivity()
        elif has_credentials:
            logger.info("Auto-detection result: Using service account key file")
            return test_service_account_connectivity()
        else:
            logger.info("Auto-detection result: No authentication configured")
            return False, (
                "No authentication configured. Set either:\n"
                "  - USE_WORKLOAD_IDENTITY=true (for Workload Identity)\n"
                "  - GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json (for service account key)"
            )

    except Exception as e:
        error_msg = f"Auto-detection test failed: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return False, error_msg


def validate_workload_identity_environment() -> Tuple[bool, str]:
    """
    Validate that the environment is properly configured for Workload Identity.

    Checks for common configuration issues in GKE, GCE, and local environments.

    Returns:
        Tuple[bool, str]: (is_valid, validation_message)
    """
    issues = []
    warnings = []

    logger.info("Validating Workload Identity environment configuration...")

    # Check if running in GKE
    if os.path.exists('/var/run/secrets/kubernetes.io/serviceaccount'):
        logger.info("✓ Detected GKE environment (Kubernetes service account token found)")

        # Check for Workload Identity annotation
        try:
            with open('/var/run/secrets/kubernetes.io/serviceaccount/namespace', 'r') as f:
                namespace = f.read().strip()
                logger.info(f"  Namespace: {namespace}")
        except Exception as e:
            warnings.append(f"Could not read Kubernetes namespace: {e}")

    # Check if running in GCE
    elif os.path.exists('/var/run/google.internal'):
        logger.info("✓ Detected GCE environment (metadata service available)")

    # Local environment
    else:
        logger.info("Detected local/external environment")

        # Check for gcloud ADC
        adc_path = os.path.expanduser('~/.config/gcloud/application_default_credentials.json')
        if os.path.exists(adc_path):
            logger.info(f"✓ Found Application Default Credentials: {adc_path}")
        else:
            warnings.append(
                "Application Default Credentials not found. "
                "Run 'gcloud auth application-default login' to set up ADC for local testing."
            )

    # Check environment variables
    if os.getenv('USE_WORKLOAD_IDENTITY') == 'true':
        logger.info("✓ USE_WORKLOAD_IDENTITY is set to 'true'")
    else:
        warnings.append("USE_WORKLOAD_IDENTITY is not set to 'true'")

    if os.getenv('GCP_PROJECT'):
        logger.info(f"✓ GCP_PROJECT is set: {os.getenv('GCP_PROJECT')}")
    else:
        issues.append("GCP_PROJECT environment variable is not set")

    # Summarize results
    if issues:
        return False, "Environment validation failed:\n" + "\n".join(f"  ✗ {issue}" for issue in issues)
    elif warnings:
        return True, "Environment validation passed with warnings:\n" + "\n".join(f"  ⚠ {warning}" for warning in warnings)
    else:
        return True, "✓ Environment validation passed - ready for Workload Identity"


def run_all_integration_tests():
    """
    Run all integration tests and print results.

    This is the main entry point for manual integration testing.
    """
    print("=" * 80)
    print("Workload Identity Federation - Integration Test Suite")
    print("=" * 80)
    print()

    # Test 1: Environment validation
    print("Test 1: Environment Validation")
    print("-" * 80)
    valid, message = validate_workload_identity_environment()
    print(message)
    print()

    # Test 2: Auto-detection
    print("Test 2: Authentication Auto-Detection")
    print("-" * 80)
    success, message = test_authentication_auto_detection()
    print(message)
    print()

    # Test 3: Explicit Workload Identity (if enabled)
    if os.getenv('USE_WORKLOAD_IDENTITY', 'false').lower() == 'true':
        print("Test 3: Explicit Workload Identity Test")
        print("-" * 80)
        success, message = test_workload_identity_connectivity()
        print(message)
        print()

    # Test 4: Service Account Key (if available)
    if os.getenv('GOOGLE_APPLICATION_CREDENTIALS'):
        print("Test 4: Service Account Key Test")
        print("-" * 80)
        success, message = test_service_account_connectivity()
        print(message)
        print()

    print("=" * 80)
    print("Integration tests complete")
    print("=" * 80)


if __name__ == '__main__':
    """
    Run integration tests when module is executed directly.

    Usage:
        # Test Workload Identity
        USE_WORKLOAD_IDENTITY=true GCP_PROJECT=my-project python -m gcp_route_mgmt_daemon.test_integration_helpers

        # Test service account key
        GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json GCP_PROJECT=my-project python -m gcp_route_mgmt_daemon.test_integration_helpers
    """
    run_all_integration_tests()
