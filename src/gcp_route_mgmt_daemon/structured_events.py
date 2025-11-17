import logging
import json
import time
from typing import Dict, Any, Optional
from enum import Enum
from dataclasses import dataclass, asdict

class EventType(Enum):
    """Standard event types for structured logging"""
    BGP_ADVERTISEMENT_CHANGE = "bgp_advertisement_change"
    CLOUDFLARE_ROUTE_UPDATE = "cloudflare_route_update"
    HEALTH_CHECK_RESULT = "health_check_result"
    STATE_TRANSITION = "state_transition"
    CIRCUIT_BREAKER_EVENT = "circuit_breaker_event"
    DAEMON_LIFECYCLE = "daemon_lifecycle"

class ActionResult(Enum):
    """Standard action results"""
    SUCCESS = "success"
    FAILURE = "failure"
    NO_CHANGE = "no_change"
    SKIPPED = "skipped"

@dataclass
class StructuredEvent:
    """Base structure for all structured log events"""
    event_type: str
    timestamp: float
    result: str
    component: str
    operation: str
    details: Dict[str, Any]
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    correlation_id: Optional[str] = None

class StructuredEventLogger:
    """Handles structured logging for daemon events with GCP Cloud Logging optimization"""
    
    def __init__(self, logger_name: str):
        self.logger = logging.getLogger(logger_name)
        self.correlation_id = None
    
    def set_correlation_id(self, correlation_id: str):
        """Set correlation ID for tracking related events across a health check cycle"""
        self.correlation_id = correlation_id
    
    def log_event(self, event) -> None:
        """Log a structured event with consistent schema"""
        # Handle both StructuredEvent dataclass instances and raw dictionaries
        if isinstance(event, StructuredEvent):
            # It's a dataclass instance
            if self.correlation_id:
                event.correlation_id = self.correlation_id
            log_data = {
                "structured_event": True,
                **asdict(event)
            }
        elif isinstance(event, dict):
            # It's a raw dictionary (legacy usage)
            log_data = {
                "structured_event": True,
                **event
            }
            if self.correlation_id:
                log_data["correlation_id"] = self.correlation_id
        else:
            raise TypeError(f"Event must be StructuredEvent dataclass or dict, got {type(event)}")
        
        # Log at appropriate level based on result
        level = logging.INFO
        if isinstance(event, dict):
            result = event.get("result")
        else:
            result = event.result

        if result == ActionResult.FAILURE.value:
            level = logging.ERROR
        elif result == ActionResult.NO_CHANGE.value:
            level = logging.DEBUG
        elif result == ActionResult.SKIPPED.value:
            level = logging.INFO  # Skipped operations are informational, not errors
            
        # Format message for human readability while preserving structure
        if isinstance(event, dict):
            component = event.get("component", "unknown")
            operation = event.get("operation", "unknown")
            result_str = event.get("result", "unknown")
            error_message = event.get("error_message")
        else:
            component = event.component
            operation = event.operation
            result_str = event.result
            error_message = event.error_message
            
        message = f"{component}.{operation}: {result_str}"
        if error_message:
            message += f" - {error_message}"
            
        # Use extra parameter for structured data in GCP Cloud Logging
        self.logger.log(level, message, extra={"json_fields": log_data})
    
    def log_bgp_advertisement(self,
                            project: str,
                            region: str,
                            router: str,
                            prefix: str,
                            action: str,  # "advertise" or "withdraw"
                            result: ActionResult,
                            duration_ms: int = None,
                            operation_id: str = None,
                            error_message: str = None) -> None:
        """Log BGP advertisement changes with full context"""
        
        event = StructuredEvent(
            event_type=EventType.BGP_ADVERTISEMENT_CHANGE.value,
            timestamp=time.time(),
            result=result.value,
            component="gcp_bgp",
            operation=f"{action}_prefix",
            details={
                "gcp_project": project,
                "gcp_region": region,
                "router_name": router,
                "ip_prefix": prefix,
                "action": action,
                "operation_id": operation_id
            },
            duration_ms=duration_ms,
            error_message=error_message
        )
        
        self.log_event(event)
    
    def log_cloudflare_update(self,
                            account_id: str,
                            description_filter: str,
                            desired_priority: int,
                            routes_modified: int,
                            result: ActionResult,
                            duration_ms: int = None,
                            error_message: str = None) -> None:
        """Log Cloudflare route priority updates"""
        
        event = StructuredEvent(
            event_type=EventType.CLOUDFLARE_ROUTE_UPDATE.value,
            timestamp=time.time(),
            result=result.value,
            component="cloudflare",
            operation="update_route_priorities",
            details={
                "account_id": account_id,
                "description_filter": description_filter,
                "desired_priority": desired_priority,
                "routes_modified": routes_modified
            },
            duration_ms=duration_ms,
            error_message=error_message
        )
        
        self.log_event(event)
    
    def log_health_check(self,
                        region: str,
                        service_type: str,  # "backend_services" or "bgp_sessions"
                        healthy: bool,
                        details: Dict[str, Any] = None,
                        duration_ms: int = None) -> None:
        """Log health check results"""
        
        result = ActionResult.SUCCESS if healthy else ActionResult.FAILURE
        
        event = StructuredEvent(
            event_type=EventType.HEALTH_CHECK_RESULT.value,
            timestamp=time.time(),
            result=result.value,
            component="health_check",
            operation=f"check_{service_type}",
            details={
                "region": region,
                "service_type": service_type,
                "healthy": healthy,
                **(details or {})
            },
            duration_ms=duration_ms
        )
        
        self.log_event(event)
    
    def log_state_transition(self,
                           old_state: int,
                           new_state: int,
                           local_healthy: bool,
                           remote_healthy: bool,
                           remote_bgp_up: bool,
                           planned_actions: tuple) -> None:
        """Log system state transitions"""
        
        event = StructuredEvent(
            event_type=EventType.STATE_TRANSITION.value,
            timestamp=time.time(),
            result=ActionResult.SUCCESS.value,
            component="state_machine",
            operation="state_transition",
            details={
                "old_state_code": old_state,
                "new_state_code": new_state,
                "local_healthy": local_healthy,
                "remote_healthy": remote_healthy,
                "remote_bgp_up": remote_bgp_up,
                "planned_primary_advertisement": planned_actions[0],
                "planned_secondary_advertisement": planned_actions[1]
            }
        )
        
        self.log_event(event)
    
    def log_circuit_breaker_event(self,
                                service: str,
                                event_name: str,  # "opened", "closed", "half_open"
                                failure_count: int = None,
                                error_message: str = None) -> None:
        """Log circuit breaker state changes"""
        
        event = StructuredEvent(
            event_type=EventType.CIRCUIT_BREAKER_EVENT.value,
            timestamp=time.time(),
            result=ActionResult.SUCCESS.value,
            component="circuit_breaker",
            operation=event_name,
            details={
                "service": service,
                "failure_count": failure_count
            },
            error_message=error_message
        )
        
        self.log_event(event)
