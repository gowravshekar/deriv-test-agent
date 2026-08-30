"""Configurable guardrail sets (edit here without touching pair fixtures)."""

from __future__ import annotations

# Rollback-like actions stay allowed under change freeze without extra escalate
# beyond their own risk — they are preferred mitigation paths.
ROLLBACK_ACTIONS: set[str] = {
    "roll_back_recent_deploy",
    "roll_back_model",
}

# Traffic / routing changes escalate to needs_approval when change_freeze_required.
TRAFFIC_ALTERING_ACTIONS: set[str] = {
    "shift_traffic",
    "fail_over_read_path",
    "switch_provider_route",
    "throttle_non_critical_consumers",
    "route_to_previous_policy",
    "pause_low_priority_orders",
}

# Map alternate phrasings onto canonical action names before runbook checks.
ACTION_ALIASES: dict[str, str] = {
    "bypass risk checks": "bypass_risk_checks",
    "bypass_risk": "bypass_risk_checks",
    "force accept orders": "force_accept_orders",
    "disable authentication": "disable_authentication",
    "disable_auth": "disable_authentication",
    "skip reconciliation": "skip_reconciliation",
    "skip_reconcil": "skip_reconciliation",
    "auto approve all": "auto_approve_all_transactions",
    "auto_approve": "auto_approve_all_transactions",
    "disable risk scoring": "disable_risk_scoring",
    "drop live ticks": "drop_live_ticks",
    "mark withdrawals complete": "mark_withdrawals_complete_without_confirmation",
}

# Category keyword buckets for novel / paraphrased forbidden actions.
FORBIDDEN_CATEGORY_TOKENS: dict[str, tuple[str, ...]] = {
    "bypass_risk": (
        "bypass_risk",
        "disable_risk",
        "auto_approve",
        "force_accept",
        "skip_risk",
    ),
    "skip_reconciliation": (
        "skip_reconcil",
        "mark_complete_without",
        "skip_settlement",
        "without_confirmation",
    ),
    "disable_auth": (
        "disable_auth",
        "disable_authentication",
        "extend_session_validity",
    ),
    "skip_audit": (
        "skip_audit",
        "bypass_audit",
        "disable_audit",
    ),
}
