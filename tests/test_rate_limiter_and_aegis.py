"""Unit tests for Sliding-Window RateGovernor, Nexus Aegis G2 Bridge, MCP tools, and CLI."""

from __future__ import annotations

import pytest

from synapse_router.aegis_bridge import (
    canonical_json_bytes,
    create_aegis_g2_leaf,
    create_aegis_g2_payload,
    hash_leaf_bytes,
)
from synapse_router.classifier import AgentStepRequest
from synapse_router.cli import main
from synapse_router.rate_limiter import RateGovernor
from synapse_router.router import SynapseRouter
from synapse_router.server import SynapseMCPServer


def test_rate_governor_validation() -> None:
    with pytest.raises(ValueError):
        RateGovernor(default_rpm=-1)
    with pytest.raises(ValueError):
        RateGovernor(default_tpm=0)
    with pytest.raises(ValueError):
        RateGovernor(window_seconds=0)

    gov = RateGovernor()
    with pytest.raises(ValueError):
        gov.configure_endpoint("ep1", max_rpm=0, max_tpm=100)


def test_rate_governor_rpm_sliding_window() -> None:
    gov = RateGovernor(default_rpm=3, default_tpm=10_000, window_seconds=60.0)
    t0 = 1000.0

    # 3 allowed requests
    dec1 = gov.record_consumption("ep1", tokens=100, now=t0)
    assert dec1.allowed
    assert dec1.current_rpm == 1

    dec2 = gov.record_consumption("ep1", tokens=100, now=t0 + 10.0)
    assert dec2.allowed
    assert dec2.current_rpm == 2

    dec3 = gov.record_consumption("ep1", tokens=100, now=t0 + 20.0)
    assert dec3.allowed
    assert dec3.current_rpm == 3

    # 4th request check should be rejected
    check = gov.check_rate_limit("ep1", estimated_tokens=100, now=t0 + 25.0)
    assert not check.allowed
    assert "RPM limit exceeded" in check.reason
    assert pytest.approx(check.retry_after_seconds, 0.1) == (t0 + 60.0) - (t0 + 25.0)  # 35s

    # Fast-forward past first event (t0 + 60.1s)
    check_after = gov.check_rate_limit("ep1", estimated_tokens=100, now=t0 + 60.1)
    assert check_after.allowed
    assert check_after.current_rpm == 2


def test_rate_governor_tpm_sliding_window() -> None:
    gov = RateGovernor(default_rpm=100, default_tpm=5000, window_seconds=60.0)
    t0 = 2000.0

    # Consume 4000 tokens
    gov.record_consumption("ep1", tokens=4000, now=t0)

    # Adding 1500 tokens would exceed 5000 TPM
    check = gov.check_rate_limit("ep1", estimated_tokens=1500, now=t0 + 5.0)
    assert not check.allowed
    assert "TPM limit exceeded" in check.reason
    assert check.retry_after_seconds > 0

    # But adding 800 tokens fits within 5000
    check_ok = gov.check_rate_limit("ep1", estimated_tokens=800, now=t0 + 5.0)
    assert check_ok.allowed


def test_rate_governor_endpoint_isolation_and_burst() -> None:
    gov = RateGovernor(default_rpm=10, default_tpm=10_000)
    gov.configure_endpoint("claude-frontier", max_rpm=2, max_tpm=2000, burst_multiplier=2.0)

    t0 = 5000.0
    # Effective max_rpm is 2 * 2.0 = 4
    for i in range(4):
        res = gov.record_consumption("claude-frontier", tokens=100, now=t0 + i)
        assert res.allowed

    # 5th request should be blocked
    dec = gov.check_rate_limit("claude-frontier", now=t0 + 5.0)
    assert not dec.allowed

    # Other endpoints are unaffected
    other_dec = gov.check_rate_limit("local-llama", now=t0 + 5.0)
    assert other_dec.allowed


def test_rate_governor_status_and_reset() -> None:
    gov = RateGovernor(default_rpm=5, default_tpm=1000)
    t0 = 3000.0
    gov.record_consumption("ep1", tokens=600, now=t0)

    st = gov.get_status("ep1", now=t0 + 1.0)
    assert st["endpoint_id"] == "ep1"
    assert st["current_rpm"] == 1
    assert st["current_tpm"] == 600
    assert st["rpm_utilization_pct"] == 20.0
    assert st["tpm_utilization_pct"] == 60.0
    assert not st["saturated"]

    gov.reset("ep1")
    st_reset = gov.get_status("ep1", now=t0 + 1.0)
    assert st_reset["current_rpm"] == 0
    assert st_reset["current_tpm"] == 0

    gov.record_consumption("ep2", tokens=100, now=t0)
    gov.reset()  # clears all
    assert gov.get_status("ep2", now=t0 + 1.0)["current_rpm"] == 0


def test_aegis_canonical_json_and_rfc6962_leaf_hash() -> None:
    obj = {"b": 2, "a": 1, "nested": {"z": 9, "y": 8}}
    canonical = canonical_json_bytes(obj)
    assert canonical == b'{"a":1,"b":2,"nested":{"y":8,"z":9}}'

    digest = hash_leaf_bytes(canonical)
    assert len(digest) == 64
    # Ensure 0x00 domain separation prefix is included
    import hashlib

    expected = hashlib.sha256(b"\x00" + canonical).hexdigest()
    assert digest == expected


def test_aegis_g2_payload_passed() -> None:
    payload = create_aegis_g2_payload(
        task_id="test_task_normal",
        task_prompt="Analyze code repository structure and dependencies",
        cost_usd=0.0035,
        budget_cap_usd=0.05,
        tokens_used=1200,
        model_id="claude-3-5-sonnet",
    )
    assert payload["gate_id"] == "G2_BUDGET_ROUTING"
    assert payload["passed"]
    assert payload["score"] == 1.0
    assert payload["violations"] == []
    assert payload["metrics"]["cost_usd"] == 0.0035


def test_aegis_g2_payload_budget_exceeded() -> None:
    payload = create_aegis_g2_payload(
        task_id="test_budget_overflow",
        cost_usd=0.08,
        budget_cap_usd=0.05,
        tokens_used=1200,
    )
    assert not payload["passed"]
    assert any("budget_cap_exceeded" in v for v in payload["violations"])
    assert payload["score"] < 1.0


def test_aegis_g2_payload_token_runaway() -> None:
    payload = create_aegis_g2_payload(
        task_id="test_token_runaway",
        cost_usd=0.01,
        budget_cap_usd=0.50,
        tokens_used=150_000,
        token_budget_cap=120_000,
    )
    assert not payload["passed"]
    assert any("token_budget_runaway" in v for v in payload["violations"])


def test_aegis_g2_payload_frontier_model_overprovisioned() -> None:
    payload = create_aegis_g2_payload(
        task_id="test_overprovision",
        task_prompt="ping and uppercase hello world",
        cost_usd=0.0002,
        budget_cap_usd=0.10,
        tokens_used=30,
        model_id="gpt-4o",
    )
    assert not payload["passed"]
    assert any("frontier_model_overprovisioned" in v for v in payload["violations"])


def test_aegis_g2_full_leaf_structure() -> None:
    leaf = create_aegis_g2_leaf(
        task_id="pipeline_audit",
        cost_usd=0.002,
        budget_cap_usd=0.10,
        tokens_used=800,
        model_id="claude-3-5-sonnet",
    )
    assert "payload" in leaf
    assert "leaf_hash" in leaf
    assert leaf["rfc6962_prefix"] == "0x00"
    assert leaf["canonical_bytes_len"] > 0
    assert len(leaf["leaf_hash"]) == 64


def test_router_integration_with_rate_governor_and_aegis() -> None:
    router = SynapseRouter()
    req = AgentStepRequest(
        prompt="Synthesize research brief on multi-agent consensus protocols.",
        estimated_output_tokens=128,
    )
    receipt = router.execute_cascade(req)
    assert receipt.receipt_id.startswith("rcpt-")

    # Rate governor should have recorded consumption for selected endpoint
    st = router.rate_governor.get_status(receipt.selected_endpoint_id)
    assert st["current_rpm"] >= 1
    assert st["current_tpm"] > 0

    # Export Aegis G2 leaf
    leaf = router.export_aegis_g2(receipt, task_prompt=req.prompt)
    assert leaf["payload"]["gate_id"] == "G2_BUDGET_ROUTING"
    assert leaf["payload"]["passed"]
    assert len(leaf["leaf_hash"]) == 64


def test_mcp_server_new_tools() -> None:
    server = SynapseMCPServer()

    # 1. synapse_rate_limit_status
    rate_resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": "rate_1",
            "method": "tools/call",
            "params": {
                "name": "synapse_rate_limit_status",
                "arguments": {"endpoint_id": "claude-3-5-sonnet"},
            },
        }
    )
    assert rate_resp["result"]["structuredContent"]["endpoint_id"] == "claude-3-5-sonnet"

    # 2. synapse_export_aegis_g2
    aegis_resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": "aegis_1",
            "method": "tools/call",
            "params": {
                "name": "synapse_export_aegis_g2",
                "arguments": {
                    "cost_usd": 0.005,
                    "budget_cap_usd": 0.10,
                    "tokens_used": 1500,
                    "model_id": "claude-3-5-sonnet",
                    "task_id": "mcp_audit",
                },
            },
        }
    )
    structured = aegis_resp["result"]["structuredContent"]
    assert structured["payload"]["passed"]
    assert structured["rfc6962_prefix"] == "0x00"
    assert len(structured["leaf_hash"]) == 64


def test_cli_rate_status_and_export_aegis(capsys: pytest.CaptureFixture[str]) -> None:
    # rate-status
    ret = main(["rate-status", "--json"])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "claude-3-5" in captured

    # rate-status human readable
    ret2 = main(["rate-status"])
    assert ret2 == 0

    # export-aegis
    ret3 = main(["export-aegis", "--cost", "0.003", "--budget", "0.05", "--tokens", "900"])
    assert ret3 == 0
    out3 = capsys.readouterr().out
    assert "leaf_hash" in out3


def test_aegis_g2_with_dict_receipt_and_violations() -> None:
    data = {
        "cost_usd": 0.12,
        "budget_cap_usd": 0.05,
        "tokens_used": 130_000,
        "model_id": "claude-3-5-sonnet",
        "task_prompt": "hello ping",
    }
    leaf = create_aegis_g2_leaf(receipt_or_eval=data, token_budget_cap=120_000)
    assert not leaf["payload"]["passed"]
    assert len(leaf["payload"]["violations"]) == 3
    assert leaf["payload"]["score"] == 0.0
    assert len(leaf["leaf_hash"]) == 64


def test_rate_governor_multi_window_drift() -> None:
    gov = RateGovernor(default_rpm=2, window_seconds=60.0)
    t0 = 10_000.0

    gov.record_consumption("drift_ep", tokens=50, now=t0)
    gov.record_consumption("drift_ep", tokens=50, now=t0 + 10.0)
    assert not gov.check_rate_limit("drift_ep", now=t0 + 20.0).allowed

    # Advance 1 hour ahead
    assert gov.check_rate_limit("drift_ep", now=t0 + 3600.0).allowed
    st = gov.get_status("drift_ep", now=t0 + 3600.0)
    assert st["current_rpm"] == 0
    assert st["current_tpm"] == 0
