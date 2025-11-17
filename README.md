# GCP Route Management Daemon

A robust, production-ready daemon for automated health monitoring and route management across Google Cloud Platform (GCP) and Cloudflare Magic Transit infrastructure.

## Overview

The GCP Route Management Daemon automatically monitors the health of GCP backend services and BGP sessions, then dynamically adjusts BGP route advertisements and Cloudflare Magic Transit route priorities based on health status. This enables automated failover scenarios and traffic engineering for high-availability network architectures.

### Key Features

- **🏥 Health Monitoring**: Continuous monitoring of GCP backend services and BGP session status
- **🔄 Automated Failover**: Dynamic BGP route advertisement and Cloudflare priority management
- **🛡️ Route Flapping Protection**: Three-layer defense against transient failures causing unnecessary route changes
  - **Layer 1**: Health check hysteresis (sliding window smoothing)
  - **Layer 2**: State verification thresholds (requires consecutive detections)
  - **Layer 3**: Minimum state dwell time (prevents rapid oscillation)
- **👀 Passive Mode**: Run daemon in monitoring-only mode without making route changes
- **🛡️ Resilience Patterns**: Circuit breakers, exponential backoff, and graceful error handling
- **📊 Comprehensive Observability**: Structured logging with correlation IDs and performance metrics
- **⚙️ Production Ready**: Extensive documentation, error handling, and operational tooling
- **🐍 Python 3.12+ Compatible**: Modern authentication with latest Python versions

## Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   GCP Region 1  │    │   GCP Region 2  │    │   Cloudflare    │
│  (Primary)      │    │  (Secondary)    │    │  Magic Transit  │
├─────────────────┤    ├─────────────────┤    ├─────────────────┤
│ Backend Services│◄───┤ Backend Services│    │ Route Priorities│
│ BGP Router      │    │ BGP Router      │    │ Traffic Steering│
│ Health Checks   │    │ BGP Monitoring  │    │ Global Anycast  │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 │
                    ┌────────────────────┐
                    │  Health Check      │
                    │     Daemon         │
                    │                    │
                    │ • State Machine    │
                    │ • Route Manager    │
                    │ • Circuit Breakers │
                    │ • Structured Logs  │
                    └────────────────────┘
```

## State-Based Routing Logic

The daemon uses a state machine to determine routing actions based on health combinations:

| State | Local Health | Remote Health | BGP Status | Primary BGP | Secondary BGP | Cloudflare Priority |
|-------|-------------|---------------|------------|-------------|---------------|-------------------|
| 0 | Any | Any | Any | No Change | No Change | No Change |
| 1 | ✅ Healthy | ✅ Healthy | ✅ UP | Advertise | Withdraw | Primary (100) |
| 2 | ❌ Unhealthy | ✅ Healthy | ✅ UP | Withdraw | Withdraw | Secondary (200) |
| 3 | ✅ Healthy | ❌ Unhealthy | ✅ UP | Advertise | Advertise | Primary (100) |
| 4 | ❌ Unhealthy | ❌ Unhealthy | ✅ UP | Advertise* | Withdraw* | Secondary (200)* |
| 5 | ❌ Unhealthy | ✅ Healthy | ❌ DOWN | Advertise | Withdraw | Secondary (200) |
| 6 | ✅ Healthy | ✅ Healthy | ❌ DOWN | Advertise | Advertise | Primary (100) |

**Notes:**
- **State 0**: Failsafe/default state for unexpected health combinations or monitoring failures. Maintains current advertisements without changes. Triggered when:
  - Health parameters contain unexpected combinations
  - Monitoring APIs return unknown/transient errors (preventing route changes based on unreliable data)
  - Any health status cannot be reliably determined
- **States 2, 3, 4 (*)**: Require verification before action to prevent route flapping:
  - **State 2**: Local unhealthy failover - Requires 2 consecutive detections (configurable)
  - **State 3**: Remote unhealthy detection - Requires 2 consecutive detections (configurable)
  - **State 4**: Both unhealthy (critical) - Requires 2 consecutive detections (configurable)
  - On first detection, no changes are made. Actions only applied after threshold met.
  - This prevents false positives during transient failures and monitoring glitches.

### Route Flapping Protection

The daemon implements a comprehensive three-layer defense system to prevent route flapping caused by transient failures:

#### Layer 1: Health Check Hysteresis (Sliding Window Smoothing)

Filters out transient health check failures before they trigger state changes by tracking a sliding window of recent health check results.

**Core Concepts:**
- **Sliding Window**: Tracks last N health check results (default: 5)
- **Threshold**: Requires X out of N checks healthy to be considered healthy
- **Two Modes**: Symmetric (simple) vs Asymmetric (conservative)

**Configuration:**
```bash
HEALTH_CHECK_WINDOW=5              # Track last 5 health checks (range: 3-10)
HEALTH_CHECK_THRESHOLD=3           # Symmetric mode: need 3/5 healthy (range: 1-10)
ASYMMETRIC_HYSTERESIS=false        # false=symmetric (default), true=asymmetric
```

---

##### Symmetric Mode (ASYMMETRIC_HYSTERESIS=false) - **RECOMMENDED**

**How it works:**
- Uses `HEALTH_CHECK_THRESHOLD` value (default: 3/5)
- Same threshold applies in both directions (healthy ↔ unhealthy)
- Simple majority rule for all transitions

**Examples with 5-check window, threshold=3:**

```
Current: Healthy
Checks:  ✓ ✓ ✓ ✗ ✓  (4/5 healthy)
Result:  Still healthy ✓

Current: Healthy
Checks:  ✓ ✓ ✗ ✗ ✗  (2/5 healthy - below threshold)
Result:  Now unhealthy ✗

Current: Unhealthy
Checks:  ✗ ✗ ✓ ✓ ✓  (3/5 healthy - meets threshold)
Result:  Now healthy ✓
```

**Best for:**
- Most production environments (recommended default)
- Balanced protection and recovery speed
- Predictable, easy-to-understand behavior
- Works well with Layer 2 & 3 protections

---

##### Asymmetric Mode (ASYMMETRIC_HYSTERESIS=true) - **ULTRA-CONSERVATIVE**

**How it works:**
- **Ignores** `HEALTH_CHECK_THRESHOLD` setting
- Uses **hardcoded** thresholds (see daemon.py:461,464,485,487):
  - **Stay in current state**: Only needs 2/5 healthy (allows up to 3 failures)
  - **Change to opposite state**: Needs 4/5 healthy (very strong signal)
- Creates "sticky" behavior - resists state changes in both directions

**Examples with 5-check window (thresholds: 2/5 to stay, 4/5 to change):**

```
Scenario 1: Healthy → Unhealthy (HARDER to fail)
Current: Healthy (State 1, 3, or 6)
Checks:  ✓ ✓ ✗ ✗ ✗  (2/5 healthy)
Result:  STILL healthy ✓ (2 meets "stay same" threshold)

Checks:  ✓ ✗ ✗ ✗ ✗  (1/5 healthy)
Result:  Now unhealthy ✗ (fell below 2)

Scenario 2: Unhealthy → Healthy (HARDER to recover)
Current: Unhealthy (State 2, 4, or 5)
Checks:  ✗ ✗ ✓ ✓ ✓  (3/5 healthy)
Result:  STILL unhealthy ✗ (needs 4 to transition)

Checks:  ✗ ✓ ✓ ✓ ✓  (4/5 healthy)
Result:  Now healthy ✓ (met "change state" threshold)
```

**Best for:**
- Extremely flappy networks with frequent transient failures
- Environments where stability is valued over fast recovery
- Situations where Layer 1 symmetric mode alone isn't filtering enough noise

**Trade-offs:**
- ⚠️ **Slower recovery**: Takes longer to detect legitimate failures AND recoveries
- ⚠️ **Less flexible**: Uses hardcoded 2/5 and 4/5 thresholds, ignores `HEALTH_CHECK_THRESHOLD`
- ⚠️ **May be too conservative**: With Layers 2 & 3 already providing protection

---

##### Comparison Table

| Aspect | Symmetric (false) | Asymmetric (true) |
|--------|-------------------|-------------------|
| **Healthy → Unhealthy** | < 3/5 healthy (2 or less) | < 2/5 healthy (1 or less) |
| **Unhealthy → Healthy** | ≥ 3/5 healthy | ≥ 4/5 healthy |
| **Philosophy** | Simple majority | "Sticky" - resist change |
| **Recovery Time** | Faster | Slower |
| **Failover Time** | Faster | Slower |
| **False Positives** | Moderate protection | Maximum protection |
| **Threshold Setting** | Uses `HEALTH_CHECK_THRESHOLD` | Ignores it (hardcoded 2/4) |
| **Recommended** | ✅ Yes (default) | ⚠️ Only for extreme cases |

---

##### Recommendation

**Use `ASYMMETRIC_HYSTERESIS=false` (symmetric mode - default)** because:

1. **Layer 2 & 3 provide additional protection**: State verification thresholds and minimum dwell time already prevent flapping
2. **Faster recovery**: When failures occur, you want to detect and respond quickly
3. **Simpler to tune**: Configurable threshold value that's easy to understand
4. **Better balanced**: Treats legitimate failures and recoveries equally

**Only use `ASYMMETRIC_HYSTERESIS=true` if:**
- Your network experiences extreme, frequent transient failures
- Symmetric mode with Layers 2 & 3 still isn't filtering enough noise
- You're willing to accept slower failover and recovery times for maximum stability

**Example Impact**: Single health check failure ✓✓✓✗✓ = 4/5 healthy → Still considered healthy (both modes)

#### Layer 2: State Verification Thresholds

Requires consecutive state detections before taking action on States 2, 3, and 4:

**States 2 & 3 Verification:**
- **State 2** (Local unhealthy): Requires 2 consecutive detections before withdrawing routes
- **State 3** (Remote unhealthy): Requires 2 consecutive detections before advertising both routes
- On first detection: No BGP/Cloudflare changes, warning logged
- On second consecutive detection: Actions applied after verification

**State 4 Verification (Critical Scenario):**
State 4 represents both regions unhealthy - the most critical scenario requiring extra caution:
1. **First Detection**: Enters "verification mode", no changes made
2. **Second Consecutive Detection**: Verification threshold met, emergency actions applied
3. **Recovery**: Counter resets if any other state detected

**Example**: Brief 30-second failure detected twice → Verification requires ~2 minutes → Prevents premature failover

**Configuration:**
```bash
STATE_2_VERIFICATION_THRESHOLD=2   # Local unhealthy (default: 2)
STATE_3_VERIFICATION_THRESHOLD=2   # Remote unhealthy (default: 2)
STATE_4_VERIFICATION_THRESHOLD=2   # Both unhealthy (default: 2)
```

**Recommended Values:**
- Production (conservative): 3-5 consecutive detections
- Balanced (recommended): 2 consecutive detections
- Development/Testing: 1-2 consecutive detections

#### Layer 3: Minimum State Dwell Time

Enforces minimum time in a state before allowing transitions:

- **Minimum Duration**: Must remain in current state for minimum time (default: 120 seconds / 2 minutes)
- **Exception States**: States 1 (recovery) and 4 (emergency) bypass dwell time for fast response
- **Prevents**: Rapid state oscillation (e.g., 2→1→2→1) even when verification passes

**Example**: State 2 verified at 16:00:00 → Attempt recovery at 16:00:30 → Blocked (need 120s) → Allowed at 16:02:00

**Configuration:**
```bash
MIN_STATE_DWELL_TIME=120                 # 2 minutes minimum (range: 30-600s)
DWELL_TIME_EXCEPTION_STATES=1,4          # States that bypass dwell time
```

#### How All Three Layers Work Together

**Scenario: October 31, 2025 Incident (30-second transient failure)**

**Without protections:**
```
16:15:52 - us-central1: 4/6 backends unhealthy → State 4
         → Routes advertised immediately
16:16:22 - us-central1: All healthy → State 2
         → Routes withdrawn immediately
Result: Route flapping ❌
```

**With all protections:**
```
Layer 1 (Hysteresis): Health checks ✓✓✓✗✓
         → 4/5 healthy → Absorbs single failure
         → No state change triggered ✓

OR (if failures persist)

Layer 2 (Verification): State 4 detected
         → Cycle 1: Pending verification (1/2) - No action
         → Cycle 2: Verified (2/2) - Actions applied

Layer 3 (Dwell Time): Recovery detected after 30s
         → Time in state: 30s < 120s minimum
         → Transition blocked ✓
         → Must wait until 2 minutes elapsed

Result: No route flapping ✅
```

**Expected Impact**: 90-95% reduction in transient failure-induced route flapping

## Passive Mode

Passive mode allows the daemon to run and perform all health checks without making any route changes. This is useful for:
- **Testing and Validation**: Verify daemon behavior before enabling route updates
- **Dry-Run Operations**: Monitor health without affecting production traffic
- **Observability**: Collect health metrics and logs without infrastructure changes
- **Troubleshooting**: Investigate issues while preventing automated changes

### How It Works

When passive mode is enabled (`RUN_PASSIVE=TRUE`):
- ✅ **Health checks continue**: GCP backend services and BGP sessions are monitored
- ✅ **State determination works**: The state machine calculates routing decisions
- ✅ **Logs are generated**: All operational and structured logs are created
- ❌ **BGP updates skipped**: No route advertisements are modified
- ❌ **Cloudflare updates skipped**: No priority changes are made

### Configuration

Set the `RUN_PASSIVE` environment variable in your `.env` file:

```bash
# Enable passive mode (monitoring only, no route updates)
RUN_PASSIVE=TRUE

# Disable passive mode (normal operation with route updates)
RUN_PASSIVE=FALSE
```

**Default Behavior**: If `RUN_PASSIVE` is not set, it defaults to `FALSE` (normal operation).

### Usage Example

**1. Start in Passive Mode for Testing:**
```bash
# Set RUN_PASSIVE=TRUE in .env
echo "RUN_PASSIVE=TRUE" >> .env

# Start daemon
python -m gcp_route_mgmt_daemon
```

**Sample Output:**
```
Daemon main loop starting with 60s check interval
Passive mode: ENABLED - monitoring only, no route updates
Monitoring regions - Local: us-central1, Remote: us-east4
⚠️  PASSIVE MODE ENABLED - Daemon will monitor but NOT update any routes
   To enable route updates, set RUN_PASSIVE=FALSE in .env file
```

**2. Review Logs and Verify Behavior:**
```bash
# Watch logs in real-time
tail -f /var/log/radius_healthcheck_daemon.log

# Check structured logs for state transitions
jq '.[] | select(.event_type == "state_transition")' \
   /var/log/radius_healthcheck_daemon_structured.json
```

**3. Switch to Active Mode:**
```bash
# Update .env file
sed -i 's/RUN_PASSIVE=TRUE/RUN_PASSIVE=FALSE/' .env

# Restart daemon
sudo systemctl restart gcp-route-mgmt
```

### Monitoring Passive Mode

All structured log events include a `passive_mode` flag for monitoring:

```json
{
  "event_type": "health_check_cycle",
  "configuration": {
    "passive_mode": true
  },
  "operation_results": {
    "bgp_updates_skipped": true,
    "cloudflare_updates_skipped": true
  }
}
```

**Alert on Unexpected Passive Mode:**
```bash
# GCP Cloud Logging query
jsonPayload.configuration.passive_mode=true
```

This helps detect when the daemon is accidentally running in passive mode in production.

## Installation

### Prerequisites

- **Python 3.12+** (required for modern GCP authentication)
- GCP Service Account with appropriate permissions
- Cloudflare API token with Magic Transit access
- Network access to GCP and Cloudflare APIs

### Required GCP IAM Permissions

Your service account needs these roles or equivalent permissions:

```yaml
# Minimal required permissions
- roles/compute.viewer                    # Read backend services and router status
- roles/compute.networkAdmin              # Modify BGP advertisements

# Specific permissions for custom roles:
- compute.backendServices.get
- compute.backendServices.getHealth
- compute.routers.get
- compute.routers.getRouterStatus
- compute.routers.update
- compute.regions.get
- compute.projects.get
```

### Cloudflare API Permissions

API token requires:
- **Account:Read** - For token verification
- **Zone:Zone Settings:Edit** - For Magic Transit route management

### Installation Steps

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd gcp-route-mgmt
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Install the package:**
   ```bash
   pip install -e .
   ```

4. **Run the automated installer (Linux):**
   ```bash
   sudo ./installer.sh
   ```

## Configuration

### Environment Variables

Create a `.env` file with the following configuration:

```bash
# Logging Configuration
LOGGER_NAME=CENTRAL_RAD_HC
LOG_LEVEL=INFO
LOG_FILE=/var/log/radius_healthcheck_daemon.log
LOG_MAX_BYTES=10485760
LOG_BACKUP_COUNT=5
ENABLE_GCP_LOGGING=false

# Structured Logging
ENABLE_STRUCTURED_CONSOLE=false
ENABLE_STRUCTURED_FILE=true
STRUCTURED_LOG_FILE=/var/log/radius_healthcheck_daemon_structured.json

# GCP Configuration
GCP_PROJECT=your-project-id
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
LOCAL_GCP_REGION=us-central1
REMOTE_GCP_REGION=us-east4

# BGP Configuration
BGP_PEER_PROJECT=your-bgp-project-id
LOCAL_BGP_REGION=us-central1
LOCAL_BGP_ROUTER=primary-router
REMOTE_BGP_REGION=us-east4
REMOTE_BGP_ROUTER=secondary-router

# Network Prefixes
PRIMARY_PREFIX=10.137.245.0/25
SECONDARY_PREFIX=10.137.19.0/25

# Cloudflare Configuration
CLOUDFLARE_ACCOUNT_ID=your-account-id
CLOUDFLARE_API_TOKEN=your-api-token
DESCRIPTION_SUBSTRING=datacenter-routes
CLOUDFLARE_PRIMARY_PRIORITY=100
CLOUDFLARE_SECONDARY_PRIORITY=200

# Daemon Settings
CHECK_INTERVAL_SECONDS=60

# Retry Configuration - Per-service retry limits for different operation types
# Health checks are read-only and can retry more aggressively
MAX_RETRIES_HEALTH_CHECK=5    # Backend service health checks (default: 5)
MAX_RETRIES_BGP_CHECK=4        # BGP session status checks (default: 4)
MAX_RETRIES_BGP_UPDATE=2       # BGP advertisement updates - modifies state, retry less (default: 2)
MAX_RETRIES_CLOUDFLARE=3       # Cloudflare API calls (default: 3)
MAX_RETRIES=3                  # Legacy fallback (deprecated, use per-service retries above)

INITIAL_BACKOFF_SECONDS=1
MAX_BACKOFF_SECONDS=60
CIRCUIT_BREAKER_THRESHOLD=5
CIRCUIT_BREAKER_TIMEOUT_SECONDS=300

# --- Route Flapping Protection: State Verification Thresholds ---
# Require consecutive state detections before taking action (prevents transient failures)

# State 2 = Local unhealthy, remote healthy (local failover)
STATE_2_VERIFICATION_THRESHOLD=2   # Valid range: 1-10, Default: 2

# State 3 = Remote unhealthy, local healthy (advertise both routes)
STATE_3_VERIFICATION_THRESHOLD=2   # Valid range: 1-10, Default: 2

# State 4 = Both regions unhealthy (critical scenario - most conservative)
STATE_4_VERIFICATION_THRESHOLD=2   # Valid range: 1-10, Default: 2

# --- Route Flapping Protection: Health Check Hysteresis ---
# Smooth out transient health check failures using sliding window

HEALTH_CHECK_WINDOW=5              # Track last N health checks (range: 3-10, default: 5)
HEALTH_CHECK_THRESHOLD=3           # Need X/N healthy to be "healthy" (range: 1-10, default: 3)
ASYMMETRIC_HYSTERESIS=false        # Different thresholds for up vs down (default: false)

# --- Route Flapping Protection: Minimum State Dwell Time ---
# Enforce minimum time in state before allowing transitions

MIN_STATE_DWELL_TIME=120                # Minimum seconds in state (range: 30-600, default: 120)
DWELL_TIME_EXCEPTION_STATES=1,4         # States that bypass dwell time (default: 1,4)

# Passive Mode - When set to TRUE, daemon runs but skips all route updates
# This is useful for testing or when you want to monitor without making changes
# Set to FALSE (default) to enable route updates
RUN_PASSIVE=FALSE

# API Timeout Configuration (seconds) - Adjust based on network latency and environment
# Higher values provide more tolerance for slow networks
# Lower values fail faster to prevent hanging operations
# Valid range: 5-300 seconds
GCP_API_TIMEOUT=30                      # General GCP API operations (default: 30)
GCP_BACKEND_HEALTH_TIMEOUT=45           # Backend health checks (default: 45)
GCP_BGP_OPERATION_TIMEOUT=60            # BGP advertisement updates (default: 60)
CLOUDFLARE_API_TIMEOUT=10               # Cloudflare API requests (default: 10)
CLOUDFLARE_BULK_TIMEOUT=60              # Cloudflare bulk updates (default: 60)
```

### Configuration Validation

The daemon validates all configuration on startup:

```bash
python -m gcp_route_mgmt_daemon.config
```

## Usage

### Running the Daemon

**Development/Testing:**
```bash
python -m gcp_route_mgmt_daemon
```

**Production (systemd service):**
```bash
sudo systemctl start gcp-route-mgmt
sudo systemctl enable gcp-route-mgmt
```

### Monitoring and Logs

The daemon provides multiple logging outputs:

1. **Console Logs**: Human-readable operational messages
2. **Regular Log File**: Standard application logs
3. **Structured JSON Logs**: Machine-readable events for analysis
4. **GCP Cloud Logging**: Centralized logging (if enabled)

**View structured logs:**
```bash
# Pretty-print JSON logs
tail -f /var/log/radius_healthcheck_daemon_structured.json | jq '.'

# Filter specific event types
jq '.[] | select(.event_type == "health_check_cycle")' /var/log/radius_healthcheck_daemon_structured.json

# Find state transitions
jq '.[] | select(.event_type == "state_transition")' /var/log/radius_healthcheck_daemon_structured.json
```

## Observability

### Structured Logging

All operational events are logged with structured data:

- **Correlation IDs**: Track related events across health check cycles
- **Performance Metrics**: Duration tracking for all operations
- **Error Context**: Detailed error information for debugging
- **State Transitions**: Complete audit trail of routing decisions

### Key Event Types

- `health_check_cycle`: Complete health check results
- `state_transition`: Routing state changes
- `bgp_advertisement_change`: BGP route modifications
- `cloudflare_route_update`: Cloudflare priority changes
- `circuit_breaker_event`: Resilience pattern activations
- `connectivity_test`: Startup validation results

### GCP Cloud Logging Queries

```bash
# All BGP advertisement changes
jsonPayload.event_type="bgp_advertisement_change"

# Failed operations
jsonPayload.result="failure"

# Events for specific health check cycle
jsonPayload.correlation_id="hc-1692622462-abc12345"

# Circuit breaker opens
jsonPayload.event_type="circuit_breaker_event" AND jsonPayload.operation="opened"
```

## Monitoring and Alerting

### Recommended Alerts

1. **Circuit Breaker Opens**
   ```bash
   jsonPayload.event_type="circuit_breaker_event" AND jsonPayload.operation="opened"
   ```

2. **Repeated BGP Failures**
   ```bash
   jsonPayload.event_type="bgp_advertisement_change" AND jsonPayload.result="failure"
   ```

3. **Frequent State Transitions**
   ```bash
   jsonPayload.event_type="state_transition"
   # Rate: > 5 transitions in 10 minutes
   ```

4. **Health Check Failures**
   ```bash
   jsonPayload.event_type="health_check_result" AND jsonPayload.result="failure"
   ```

### Performance Monitoring

Monitor these metrics from structured logs:

- **Cycle Duration**: `health_check_cycle.duration_ms`
- **API Response Times**: Individual operation `duration_ms`
- **Error Rates**: `result="failure"` events
- **State Stability**: Frequency of `state_transition` events

## Troubleshooting

### Common Issues

**1. Configuration Validation Failures**
```bash
# Check configuration
python -c "
from src.gcp_route_mgmt_daemon.config import Config, validate_configuration
cfg = Config()
errors = validate_configuration(cfg)
if errors:
    print('Errors:', errors)
else:
    print('Configuration valid')
"
```

**2. GCP Connectivity Issues**
```bash
# Test GCP connectivity
gcloud auth application-default print-access-token
gcloud compute backend-services list --project=your-project --filter="region:us-central1"
```

**3. Cloudflare API Issues**
```bash
# Test Cloudflare connectivity
curl -X GET "https://api.cloudflare.com/client/v4/accounts/YOUR_ACCOUNT_ID/tokens/verify" \
  -H "Authorization: Bearer YOUR_API_TOKEN"
```

**4. Permission Issues**
```bash
# Check log file permissions
ls -la /var/log/radius_healthcheck_daemon*
sudo chmod 755 /var/log
```

### Debug Mode

Run with debug logging:
```bash
LOG_LEVEL=DEBUG python -m gcp_route_mgmt_daemon
```

### Correlation ID Tracing

Use correlation IDs to trace issues across health check cycles:
```bash
# Find all events for a specific cycle
grep "hc-1692622462-abc12345" /var/log/radius_healthcheck_daemon.log

# Or in structured logs
jq '.[] | select(.correlation_id == "hc-1692622462-abc12345")' /var/log/radius_healthcheck_daemon_structured.json
```

## Development

### Project Structure

```
gcp-route-mgmt/
├── src/gcp_route_mgmt_daemon/
│   ├── __init__.py
│   ├── __main__.py              # Entry point
│   ├── config.py                # Configuration management
│   ├── daemon.py                # Main daemon logic
│   ├── state.py                 # State machine logic
│   ├── structured_events.py     # Structured logging
│   ├── logging_setup.py         # Log configuration
│   ├── circuit.py               # Circuit breaker patterns
│   ├── gcp.py                   # GCP API integration
│   ├── cloudflare.py            # Cloudflare API integration
│   ├── test_states.py           # Unit tests for state logic
│   ├── test_circuit.py          # Unit tests for circuit breaker
│   ├── test_gcp.py              # Unit tests for GCP integration
│   ├── test_cloudflare.py       # Unit tests for Cloudflare integration
│   ├── test_passive_mode.py     # Unit tests for passive mode
│   └── test_structured_logging.py # Unit tests for structured logging
├── requirements.txt
├── installer.sh
├── run_tests.py                 # Test runner utility
├── pyproject.toml
├── TESTING.md                   # Testing documentation
└── README.md
```

### Testing

The project includes comprehensive unit tests covering all critical components with **265 total tests**.

**Test Files:**
- `test_states.py` - State machine, state verification, route flapping protections (60 tests)
- `test_circuit.py` - Circuit breaker pattern and exponential backoff (47 tests)
- `test_gcp.py` - GCP API integration, Python 3.12+ compatibility (44 tests)
- `test_cloudflare.py` - Cloudflare API route management (41 tests)
- `test_passive_mode.py` - Passive mode functionality (25 tests)
- `test_config.py` - Configuration loading, validation, route flapping config (24 tests)
- `test_structured_logging.py` - Structured logging and ActionResult.SKIPPED (24 tests)

**Quick Start:**

```bash
# Run all tests (260 total)
cd src
../venv/bin/python3 -m unittest discover -s gcp_route_mgmt_daemon -p "test_*.py"

# Run specific test module
../venv/bin/python3 -m unittest gcp_route_mgmt_daemon.test_states        # 55 tests
../venv/bin/python3 -m unittest gcp_route_mgmt_daemon.test_config        # 24 tests

# Run with verbose output
../venv/bin/python3 -m unittest gcp_route_mgmt_daemon.test_states -v
```

**Test Results:**
```
..................................................
----------------------------------------------------------------------
Ran 265 tests in ~15s

OK
```

For detailed testing documentation, see [TESTING.md](TESTING.md).

**Test Coverage:**

**State Machine & Route Flapping Protection Tests (test_states.py - 55 tests):**
- All 7 state codes (0-6) with health condition combinations
- **NEW**: State 2 & 3 verification logic (8 tests)
- State 4 verification logic with configurable threshold (5 tests)
- **NEW**: Health check hysteresis - sliding window smoothing (9 tests)
  - Symmetric and asymmetric modes
  - Transient failure absorption
  - Sustained failure detection
- **NEW**: Minimum state dwell time enforcement (7 tests)
  - Transition blocking logic
  - Exception state handling (States 1 and 4)
  - Boundary conditions
- **NEW**: Integration tests - all three protection layers (3 tests)
- State transitions and verification reset behavior
- Edge cases and counter reset behavior

**Configuration Tests (test_config.py - 24 tests):**
- Configuration loading from environment variables
- **NEW**: Route flapping protection configuration (10 tests)
  - State 2/3/4 verification threshold validation
  - Health check hysteresis parameters validation
  - Minimum state dwell time validation
  - Constraint checking (threshold < window size)
- State verification threshold configuration (range: 1-10)
- Per-service retry configuration (health checks, BGP, Cloudflare)
- API timeout configuration validation
- Configuration validation (ranges, types, required fields)
- Backward compatibility with legacy MAX_RETRIES setting
- Default value handling when variables not set

**GCP API Tests (test_gcp.py - 44 tests):**
- **Python 3.12+ compatible** - modern authentication without deprecated methods
- Client initialization with service account credentials
- Credential file validation (not found, not readable, invalid JSON)
- Connectivity validation with permission checking
- Backend service health checks (all states: HEALTHY, UNHEALTHY, DRAINING, TIMEOUT, UNKNOWN)
- BGP session monitoring (UP, DOWN, and various states)
- Incomplete API response handling
- **HTTP error handling**:
  - Permanent errors (403, 404): Re-raised for immediate attention
  - Known transient errors (429, 500, 502, 503, 504): Return unknown health → State 0
  - Unknown error codes: Return unknown health → State 0 (safe default)
- Structured logging integration

**Cloudflare API Tests (test_cloudflare.py):**
- API connectivity and token validation
- Route filtering by description substring
- Bulk priority updates with optimization (no-op when already at priority)
- Empty routes and no-match scenarios
- HTTP error handling (401, 403, 404, 422, 429, 5xx)
- Timeout and connection error handling
- Case-sensitive matching validation

**Passive Mode Tests (test_passive_mode.py):**
- Configuration loading with `RUN_PASSIVE` environment variable
- Default behavior (defaults to FALSE when not set)
- Skip updates flag behavior in passive and active modes
- BGP and Cloudflare update skipping in passive mode
- Health check continuation in passive mode
- State determination in passive mode
- Logging and structured event generation
- Integration with State 4 verification logic
- Full passive mode health check cycle

**Structured Logging Tests (test_structured_logging.py):**
- ActionResult enum values (SUCCESS, FAILURE, NO_CHANGE, SKIPPED)
- Log level selection based on result type
- StructuredEvent dataclass creation and validation
- Event logging with dict and dataclass formats
- Correlation ID tracking across events
- Health check cycle result determination (SUCCESS/FAILURE/SKIPPED)
- Passive mode result handling (SKIPPED)
- Consecutive error tracking with skipped operations
- Event field validation and type checking

### Code Quality

The codebase follows Python best practices:

- **Type hints** throughout for better IDE support
- **Comprehensive documentation** for all modules and functions
- **Error handling** with specific exception types
- **Logging standards** with structured events
- **Configuration validation** with helpful error messages
- **Unit tests** for critical state machine logic

## Production Deployment

### Systemd Service

The installer script automatically creates a systemd service at `/etc/systemd/system/gcp-route-mgmt.service`:

```ini
[Unit]
Description=MT GCP Healthcheck & Cloudflare Failover Daemon
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/gcp-route-mgmt
Environment=PYTHONPATH=/opt/gcp-route-mgmt/src:$PYTHONPATH
EnvironmentFile=/opt/gcp-route-mgmt/.env
ExecStart=/opt/gcp-route-mgmt/venv/bin/python -u -m gcp_route_mgmt_daemon
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### Log Rotation

Configure log rotation in `/etc/logrotate.d/gcp-route-mgmt`:

```
/var/log/radius_healthcheck_daemon*.log /var/log/radius_healthcheck_daemon*.json {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
```

### Uninstalling

To uninstall the daemon:

```bash
sudo ./installer.sh --uninstall
```

## Security Considerations

- **Credentials**: Store service account keys securely, never in version control
- **Permissions**: Use least-privilege IAM roles
- **Network**: Ensure firewall rules allow API access
- **Monitoring**: Watch for unauthorized API usage
- **Rotation**: Regularly rotate service account keys

## Version Information

- **Version**: 0.5.1
- **Python**: 3.10+ required (tested on 3.12-3.14)
- **Last Updated**: November 2025
- **Author**: Caffeineoverflow (Nathan Bray)

### Recent Updates

**Version 0.5.1 (November 2025)**
- 🛡️ **Unknown Health Handling**: Enhanced error handling for monitoring API failures
  - **State 0 Protection**: When monitoring APIs return unknown/transient errors, system triggers State 0 (no route changes)
  - **Safe Default Behavior**: Unknown HTTP error codes (e.g., 432, 400) return unknown health status instead of marking services unhealthy
  - **Three-Tier Error Classification**:
    - Permanent errors (403, 404): Re-raised immediately for configuration issues
    - Known transient errors (429, 500, 502, 503, 504): Return unknown health → State 0
    - Unknown error codes: Return unknown health → State 0 (safe default)
  - **Prevents Route Flapping**: Control plane failures (monitoring APIs) don't trigger data plane changes (route updates)
  - **Comprehensive Logging**: All unknown errors logged with full details (error code, router, project/region, exception)
  - **Applies to Both Health Checks**: Backend service health and BGP session monitoring use consistent error handling
- 🐛 **Critical Bug Fix**: State 0 Route Change Prevention
  - **Issue**: State 0 was incorrectly withdrawing BGP prefixes despite being designed to maintain current state
  - **Root Cause**: Missing State 0 check in `skip_updates` logic and `None` being treated as `False` in BGP update function
  - **Fix #1**: Added explicit State 0 check to skip all route updates when in failsafe mode
  - **Fix #2**: Added `None` handling in `update_bgp_advertisement()` to return immediately without API calls
  - **Impact**: State 0 now correctly maintains routing state during monitoring failures, preventing unintended route withdrawals
  - **Verification**: All BGP and Cloudflare updates properly skipped with `bgp_updates_skipped: true` and clear logging
- 🧪 **Enhanced Test Coverage**:
  - Total test count: **269 tests** (up from 260, all passing on Python 3.12-3.14)
  - Added tests for unknown HTTP error codes and transient error handling
  - Added test for State 0 preventing route changes on monitoring failures
  - Added test for `advertise=None` returning no-op without API calls
  - Updated tests to verify None return values trigger State 0

**Version 0.4.0 (November 2025)**
- 🛡️ **Comprehensive Route Flapping Protection System**: Three-layer defense against transient failures
  - **Layer 1: Health Check Hysteresis (Sliding Window Smoothing)**:
    - Filters transient failures using sliding window before triggering state changes
    - Configurable window size (`HEALTH_CHECK_WINDOW=5`, range: 3-10)
    - Configurable threshold (`HEALTH_CHECK_THRESHOLD=3`, range: 1-10)
    - Supports symmetric and asymmetric modes (`ASYMMETRIC_HYSTERESIS=false`)
    - Absorbs single API timeouts or momentary slow responses
    - 9 new unit tests covering symmetric/asymmetric modes and transient failure absorption
  - **Layer 2: State Verification Thresholds**:
    - Requires consecutive state detections before taking action on States 2, 3, and 4
    - **State 2 Verification** (`STATE_2_VERIFICATION_THRESHOLD=2`): Local unhealthy failover protection
    - **State 3 Verification** (`STATE_3_VERIFICATION_THRESHOLD=2`): Remote unhealthy detection protection
    - **State 4 Verification** (`STATE_4_VERIFICATION_THRESHOLD=2`): Both regions unhealthy (critical scenario)
    - All thresholds configurable (range: 1-10, production recommended: 3-5)
    - Prevents false positives during transient failures and monitoring glitches
    - 13 new unit tests covering all state verification logic and edge cases
  - **Layer 3: Minimum State Dwell Time**:
    - Enforces minimum time in state before allowing transitions
    - Configurable duration (`MIN_STATE_DWELL_TIME=120`, range: 30-600 seconds)
    - Exception states that bypass dwell time (`DWELL_TIME_EXCEPTION_STATES=1,4`)
    - Prevents rapid state oscillation even when verification passes
    - 7 new unit tests covering transition blocking and exception handling
  - **Integration Testing**: 3 comprehensive tests verifying all three layers working together
  - **Expected Impact**: 90-95% reduction in transient failure-induced route flapping
- ⚙️ **Operational Flexibility Improvements**:
  - **Per-Service Retry Configuration**: Different retry strategies for different operation types
    - `MAX_RETRIES_HEALTH_CHECK=5` (read-only, can retry more)
    - `MAX_RETRIES_BGP_CHECK=4` (BGP session status checks)
    - `MAX_RETRIES_BGP_UPDATE=2` (modifies routing state, conservative)
    - `MAX_RETRIES_CLOUDFLARE=3` (respects rate limits)
    - Backward compatible with legacy `MAX_RETRIES` setting
  - **API Timeout Configuration**: Granular timeout control for API operations
    - `GCP_API_TIMEOUT=30` (general GCP operations)
    - `GCP_BACKEND_HEALTH_TIMEOUT=45` (health checks)
    - `GCP_BGP_OPERATION_TIMEOUT=60` (BGP updates)
    - `CLOUDFLARE_API_TIMEOUT=10` (single requests)
    - `CLOUDFLARE_BULK_TIMEOUT=60` (bulk updates)
    - All timeouts configurable in range: 5-300 seconds
- ✨ **New Feature**: Passive mode for monitoring without route updates
  - Added `RUN_PASSIVE` environment variable configuration
  - Comprehensive testing with 25 new unit tests
  - Structured logging integration for passive mode monitoring
  - See [Passive Mode](#passive-mode) section for details
- 🔍 **Enhanced Structured Logging**: ActionResult.SKIPPED for passive mode operations
  - Health check cycles now report SKIPPED result when in passive mode
  - Improved semantic precision in log filtering and monitoring
  - Added 24 unit tests for structured logging functionality
- 🧪 **Enhanced Test Coverage**:
  - Total test count: **265 tests** (all passing on Python 3.12-3.14)
  - `test_states.py`: 60 tests (added 32 tests for route flapping protection + 5 tests for unknown health handling)
  - `test_config.py`: 24 tests (10 tests for route flapping configuration validation)
  - `test_passive_mode.py`: 25 tests (new)
  - `test_structured_logging.py`: 24 tests (new)
  - `test_gcp.py`: 44 tests (Python 3.12+ compatibility)
  - `test_cloudflare.py`: 41 tests
  - `test_circuit.py`: 47 tests
- 🐍 **Python 3.12+ Compatibility**: Modern authentication without deprecated methods
- 📝 **Documentation Updates**:
  - Enhanced README with comprehensive route flapping protection documentation
  - Added TESTING.md with detailed testing guide
  - Updated .env examples and configuration guides

## Support

For issues and questions:
- Check the troubleshooting section above
- Review structured logs for error details
- Use correlation IDs to trace specific issues
- Open GitHub issues for bugs and feature requests

---

**Ready to deploy**: Use the `installer.sh` script for automated installation and systemd service configuration.
