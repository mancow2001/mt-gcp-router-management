#!/usr/bin/env python3
"""
Test Runner for GCP Route Management Daemon

This script runs all unit tests in the src/gcp_route_mgmt_daemon directory
and provides a summary of test results.

Usage:
    python run_tests.py                    # Run all tests
    python run_tests.py test_passive_mode  # Run specific test module
    python run_tests.py -v                 # Verbose output

Author: Nathan Bray
Created: 2025-11-01
"""

import sys
import os
import unittest
import argparse


def discover_and_run_tests(test_pattern=None, verbosity=1):
    """
    Discover and run all unit tests in the project.

    Args:
        test_pattern: Optional pattern to filter test modules (e.g., 'test_passive_mode')
        verbosity: Verbosity level (0=quiet, 1=normal, 2=verbose)

    Returns:
        bool: True if all tests passed, False otherwise
    """
    # Add src to Python path
    src_path = os.path.join(os.path.dirname(__file__), 'src')
    sys.path.insert(0, src_path)

    # Create test suite
    loader = unittest.TestLoader()

    if test_pattern:
        # Run specific test module
        try:
            suite = loader.loadTestsFromName(f'gcp_route_mgmt_daemon.{test_pattern}')
        except Exception as e:
            print(f"Error loading test module '{test_pattern}': {e}")
            return False
    else:
        # Discover all tests in the package
        suite = loader.discover(
            start_dir=os.path.join(src_path, 'gcp_route_mgmt_daemon'),
            pattern='test_*.py',
            top_level_dir=src_path
        )

    # Run tests
    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)

    # Print summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    print(f"Tests run: {result.testsRun}")
    print(f"Successes: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Skipped: {len(result.skipped)}")
    print("=" * 70)

    # Return success status
    return result.wasSuccessful()


def main():
    """Main entry point for test runner."""
    parser = argparse.ArgumentParser(
        description='Run unit tests for GCP Route Management Daemon'
    )
    parser.add_argument(
        'test_module',
        nargs='?',
        default=None,
        help='Specific test module to run (e.g., test_passive_mode)'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Verbose output'
    )
    parser.add_argument(
        '-q', '--quiet',
        action='store_true',
        help='Quiet output (minimal)'
    )

    args = parser.parse_args()

    # Determine verbosity level
    if args.verbose:
        verbosity = 2
    elif args.quiet:
        verbosity = 0
    else:
        verbosity = 1

    # Run tests
    print("=" * 70)
    print("GCP ROUTE MANAGEMENT DAEMON - UNIT TEST RUNNER")
    print("=" * 70)

    if args.test_module:
        print(f"Running test module: {args.test_module}")
    else:
        print("Running all tests...")

    print("=" * 70)
    print()

    success = discover_and_run_tests(args.test_module, verbosity)

    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
