import time

from app.schemas.decision import GateSeverity, GateStatus, SafetyGate


def gate(name: str, status: GateStatus, severity: GateSeverity, reason: str) -> SafetyGate:
    return SafetyGate(name=name, status=status, severity=severity, reason=reason, updated_at=time.time())


def pass_gate(name: str, reason: str) -> SafetyGate:
    return gate(name, GateStatus.PASS, GateSeverity.INFO, reason)


def fail_gate(name: str, reason: str, critical: bool = True) -> SafetyGate:
    return gate(name, GateStatus.FAIL, GateSeverity.CRITICAL if critical else GateSeverity.WARNING, reason)


def warning_gate(name: str, reason: str) -> SafetyGate:
    return gate(name, GateStatus.WARNING, GateSeverity.WARNING, reason)


def na_gate(name: str, reason: str) -> SafetyGate:
    return gate(name, GateStatus.NOT_APPLICABLE, GateSeverity.INFO, reason)
