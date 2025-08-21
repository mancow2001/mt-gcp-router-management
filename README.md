# project_docs_extract — MT GCP DAEMON

## Overview

This daemonized Python service monitors Google Cloud Platform (GCP) load balancer backend health across a **local** and a **remote** region, 
tracks remote BGP session state, and then **programmatically toggles traffic direction** by:
- Adjusting **BGP advertisements** in GCP (per-region, per-router) to promote or withdraw prefixes.
- Updating **Cloudflare Magic Transit** route priorities (via bulk update by description substring) so the correct site is preferred.

It uses:
- **Circuit breakers** (`circuit.py`) to avoid thrashing unstable dependencies, with threshold + timeout.
- **Exponential backoff & retry** around network calls to GCP and Cloudflare.
- **Config dataclass** (`config.py`) with validation and `.env` support (via `python-dotenv`).
- **Google Cloud Logging** integration (optional) for centralized logs.
- A **single main loop** (`daemon.py`) that performs health checks and actions every `CHECK_INTERVAL_SECONDS`.

## Directory Structure

```text
project_docs_extract/
    __MACOSX/
        mt-gcp-daemon-modularized/
            src/
                mt_gcp_daemon/
                mt_gcp_daemon.egg-info/
            systemd/
    mt-gcp-daemon-modularized/
        README.md
        key.json
        pyproject.toml
        requirements.txt
        src/
            mt_gcp_daemon/
                __init__.py
                __main__.py
                circuit.py
                cloudflare.py
                config.py
                daemon.py
                gcp.py
                logging_setup.py
                state.py
            mt_gcp_daemon.egg-info/
                PKG-INFO
                SOURCES.txt
                dependency_links.txt
                entry_points.txt
                requires.txt
                top_level.txt

```
## Configuration (.env)

Set these environment variables (or put them in a `.env` file):

- `BGP_PEER_PROJECT`
- `CHECK_INTERVAL_SECONDS`
- `CIRCUIT_BREAKER_THRESHOLD`
- `CIRCUIT_BREAKER_TIMEOUT_SECONDS`
- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_PRIMARY_PRIORITY`
- `CLOUDFLARE_SECONDARY_PRIORITY`
- `DESCRIPTION_SUBSTRING`
- `ENABLE_GCP_LOGGING`
- `GCP_PROJECT`
- `GOOGLE_APPLICATION_CREDENTIALS`
- `INITIAL_BACKOFF_SECONDS`
- `LOCAL_BGP_REGION`
- `LOCAL_BGP_ROUTER`
- `LOCAL_GCP_REGION`
- `LOG_BACKUP_COUNT`
- `LOG_FILE`
- `LOG_LEVEL`
- `LOG_MAX_BYTES`
- `MAX_BACKOFF_SECONDS`
- `MAX_RETRIES`
- `PRIMARY_INTERNAL_IP`
- `PRIMARY_PREFIX`
- `REMOTE_BGP_REGION`
- `REMOTE_BGP_ROUTER`
- `REMOTE_GCP_REGION`
- `SECONDARY_INTERNAL_IP`
- `SECONDARY_PREFIX`

## Entrypoint & Runtime

- Entrypoint: `src/mt_gcp_daemon/__main__.py` → `main()`
- Main loop: `src/mt_gcp_daemon/daemon.py` → `run_loop(cfg, compute)`
- Health checks: `src/mt_gcp_daemon/gcp.py` (backend services, BGP advertisement)
- Cloudflare actions: `src/mt_gcp_daemon/cloudflare.py` (bulk route priority update)
- State reduction: `src/mt_gcp_daemon/state.py` (`determine_state_code`)
- Circuit breaker: `src/mt_gcp_daemon/circuit.py` (`CircuitBreaker`)
- Logging: `src/mt_gcp_daemon/logging_setup.py` (console + optional rotating file, optional GCP logging)

## Quick Start

1. Python 3.10+ required.
2. Create venv & install deps:
   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Copy `.env.example` → `.env`, set values (see **Configuration**). Ensure GCP credentials are reachable via `GOOGLE_APPLICATION_CREDENTIALS`.
4. Run:
   ```bash
   python -m mt_gcp_daemon
   ```

## Module Reference

### `mt-gcp-daemon-modularized/src/mt_gcp_daemon/__init__.py`

**Functions**
_None found._

---

### `mt-gcp-daemon-modularized/src/mt_gcp_daemon/__main__.py`
**Imports:** internal: .config, .daemon, .logging_setup external: google.cloud.logging, google.cloud.logging.handlers, logging, sys

**Functions**
- `main()`

---

### `mt-gcp-daemon-modularized/src/mt_gcp_daemon/circuit.py`
**Imports:** external: datetime, logging, threading, time

**Functions**
- `__init__(threshold=5, timeout=300)`
- `call(func, *args, **kwargs)`
- `record_failure()`
- `reset()`
- `exponential_backoff_retry(func, max_retries=3, initial_delay=1.0, max_delay=60.0, backoff_factor=2.0)`

### Class `CircuitBreaker`
**Methods:**
- `__init__(threshold=5, timeout=300)`
- `call(func, *args, **kwargs)`
- `record_failure()`
- `reset()`


---

### `mt-gcp-daemon-modularized/src/mt_gcp_daemon/cloudflare.py`
**Imports:** external: logging, requests

**Functions**
- `validate_cloudflare_connectivity(account_id, token)`
- `update_routes_by_description_bulk(account_id, token, desc_substring, desired_priority)`

---

### `mt-gcp-daemon-modularized/src/mt_gcp_daemon/config.py`
**Imports:** external: dataclasses, dotenv, ipaddress, os
**Env Vars used:** BGP_PEER_PROJECT, CHECK_INTERVAL_SECONDS, CIRCUIT_BREAKER_THRESHOLD, CIRCUIT_BREAKER_TIMEOUT_SECONDS, CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN, CLOUDFLARE_PRIMARY_PRIORITY, CLOUDFLARE_SECONDARY_PRIORITY, DESCRIPTION_SUBSTRING, ENABLE_GCP_LOGGING, GCP_PROJECT, GOOGLE_APPLICATION_CREDENTIALS, INITIAL_BACKOFF_SECONDS, LOCAL_BGP_REGION, LOCAL_BGP_ROUTER, LOCAL_GCP_REGION, LOG_BACKUP_COUNT, LOG_FILE, LOG_LEVEL, LOG_MAX_BYTES, MAX_BACKOFF_SECONDS, MAX_RETRIES, PRIMARY_INTERNAL_IP, PRIMARY_PREFIX, REMOTE_BGP_REGION, REMOTE_BGP_ROUTER, REMOTE_GCP_REGION, SECONDARY_INTERNAL_IP, SECONDARY_PREFIX

**Functions**
- `validate_configuration(cfg)`

### Class `Config`


---

### `mt-gcp-daemon-modularized/src/mt_gcp_daemon/daemon.py`
**Imports:** internal: ., .circuit, .config, .logging_setup, .state external: logging, signal, sys, threading, time

**Functions**
- `signal_handler(signum, frame)`
- `setup_signal_handlers()`
- `run_loop(cfg, compute)`
- `startup(cfg)`

---

### `mt-gcp-daemon-modularized/src/mt_gcp_daemon/gcp.py`
**Imports:** external: google.oauth2, googleapiclient.discovery, googleapiclient.errors, logging, os

**Functions**
- `build_compute_client(creds_path)`
- `validate_gcp_connectivity(project, regions, compute)`
- `backend_services_healthy(project, region, compute_client)`
- `_check()`
- `router_bgp_sessions_healthy(project, region, router, compute_client)`
- `_check()`
- `update_bgp_advertisement(project, region, router, prefix, compute_client, advertise=True)`
- `_update()`

---

### `mt-gcp-daemon-modularized/src/mt_gcp_daemon/logging_setup.py`
**Imports:** external: logging, logging.handlers, os

**Functions**
- `setup_logger(name, level, log_file, max_bytes, backup_count)`

---

### `mt-gcp-daemon-modularized/src/mt_gcp_daemon/state.py`

**Functions**
- `determine_state_code(local_healthy, remote_healthy, remote_bgp_up)`

---

## Operational Guidance

- **Intervals & backoff** are configurable; tune `CHECK_INTERVAL_SECONDS`, `MAX_RETRIES`, and backoff values to your SLOs.
- **Circuit breaker** thresholds should reflect upstream reliability and your blast radius tolerance.
- **Cloudflare**: set `DESCRIPTION_SUBSTRING` to target the correct MT routes for priority flipping.
- **GCP**: set per-site `LOCAL_/REMOTE_` regions/routers and primary/secondary prefixes consistently.
- **Logging**: set `ENABLE_GCP_LOGGING`, `LOG_LEVEL`, and rotate with `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT`.
