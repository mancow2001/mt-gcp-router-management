# MT GCP Health Check Daemon

A robust, production-ready daemon for automated health monitoring and route management across Google Cloud Platform (GCP) and Cloudflare Magic Transit infrastructure.

## Overview

The MT GCP Health Check Daemon automatically monitors the health of GCP backend services and BGP sessions, then dynamically adjusts BGP route advertisements and Cloudflare Magic Transit route priorities based on health status. This enables automated failover scenarios and traffic engineering for high-availability network architectures.

### Key Features

- **🏥 Health Monitoring**: Continuous monitoring of GCP backend services and BGP session status
- **🔄 Automated Failover**: Dynamic BGP route advertisement and Cloudflare priority management
- **🛡️ Resilience Patterns**: Circuit breakers, exponential backoff, and graceful error handling
- **📊 Comprehensive Observability**: Structured logging with correlation IDs and performance metrics
- **⚙️ Production Ready**: Extensive documentation, error handling, and operational tooling

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
| 1 | ✅ Healthy | ✅ Healthy | ✅ UP | Advertise | Withdraw | Primary (100) |
| 2 | ❌ Unhealthy | ✅ Healthy | ✅ UP | Withdraw | Withdraw | Secondary (200) |
| 3 | ✅ Healthy | ❌ Unhealthy | ✅ UP | Advertise | Advertise | Primary (100) |
| 4 | ❌ Unhealthy | ❌ Unhealthy | ✅ UP | Advertise | Withdraw | Secondary (200) |
| 5 | ❌ Unhealthy | ✅ Healthy | ❌ DOWN | Advertise | Withdraw | Secondary (200) |
| 6 | ✅ Healthy | ✅ Healthy | ❌ DOWN | Advertise | Advertise | Primary (100) |

## Installation

### Prerequisites

- Python 3.10+
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
   cd mt-gcp-daemon
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
MAX_RETRIES=3
INITIAL_BACKOFF_SECONDS=1
MAX_BACKOFF_SECONDS=60
CIRCUIT_BREAKER_THRESHOLD=5
CIRCUIT_BREAKER_TIMEOUT_SECONDS=300
```

### Configuration Validation

The daemon validates all configuration on startup:

```bash
python -m mt_gcp_daemon.config
```

## Usage

### Running the Daemon

**Development/Testing:**
```bash
python -m mt_gcp_daemon
```

**Production (systemd service):**
```bash
sudo systemctl start mt-gcp-daemon
sudo systemctl enable mt-gcp-daemon
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
from src.mt_gcp_daemon.config import Config, validate_configuration
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
LOG_LEVEL=DEBUG python -m mt_gcp_daemon
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
mt-gcp-daemon/
├── src/mt_gcp_daemon/
│   ├── __init__.py
│   ├── __main__.py              # Entry point
│   ├── config.py                # Configuration management
│   ├── daemon.py                # Main daemon logic
│   ├── state.py                 # State machine logic
│   ├── structured_events.py     # Structured logging
│   ├── logging_setup.py         # Log configuration
│   ├── circuit.py               # Circuit breaker patterns
│   ├── gcp.py                   # GCP API integration
│   └── cloudflare.py           # Cloudflare API integration
├── requirements.txt
├── installer.sh
├── pyproject.toml
└── README.md
```

### Code Quality

The codebase follows Python best practices:

- **Type hints** throughout for better IDE support
- **Comprehensive documentation** for all modules and functions
- **Error handling** with specific exception types
- **Logging standards** with structured events
- **Configuration validation** with helpful error messages

## Production Deployment

### Systemd Service

The installer script automatically creates a systemd service at `/etc/systemd/system/mt-gcp-daemon.service`:

```ini
[Unit]
Description=MT GCP Healthcheck & Cloudflare Failover Daemon
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/mt-gcp-daemon
Environment=PYTHONPATH=/opt/mt-gcp-daemon/src:$PYTHONPATH
EnvironmentFile=/opt/mt-gcp-daemon/.env
ExecStart=/opt/mt-gcp-daemon/venv/bin/python -u -m mt_gcp_daemon
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### Log Rotation

Configure log rotation in `/etc/logrotate.d/mt-gcp-daemon`:

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

- **Version**: 1.1.0
- **Python**: 3.10+ required
- **Last Updated**: August 2025
- **Author**: Caffeineoverflow (Nathan Bray)

## License

MIT License

## Support

For issues and questions:
- Check the troubleshooting section above
- Review structured logs for error details
- Use correlation IDs to trace specific issues
- Open GitHub issues for bugs and feature requests

---

**Ready to deploy**: Use the `installer.sh` script for automated installation and systemd service configuration.
