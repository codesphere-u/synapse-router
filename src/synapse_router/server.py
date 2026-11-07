"""JSON-RPC 2.0 Model Context Protocol (MCP) Server for SynapseRouter."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from synapse_router.budget import BudgetExceededError
from synapse_router.classifier import AgentStepRequest
from synapse_router.router import NoHealthyEndpointError, SynapseRouter


MCP_TOOLS_SPEC: List[Dict[str, Any]] = [
    {
        "name": "route_agent_step",
        "description": (
            "Route an AI agent workflow step across FAST_LOCAL, BALANCED, and FRONTIER_REASONING "
            "models using Pareto cost/latency optimization, SimHash semantic caching, circuit-breaker "
            "failover, and confidence-gated cascade escalation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "User or agent step instruction."},
                "system_prompt": {"type": "string", "default": ""},
                "tools_schema": {"type": "array", "items": {"type": "object"}, "default": []},
                "require_json_schema": {"type": "object"},
                "latency_sla_ms": {"type": "number"},
                "max_cost_usd": {"type": "number"},
                "min_confidence": {"type": "number", "default": 0.78},
                "estimated_output_tokens": {"type": "integer", "default": 256},
                "tenant_id": {"type": "string", "default": "default-tenant"},
                "workflow_id": {"type": "string", "default": "default-workflow"},
                "step_id": {"type": "string", "default": "step-0"},
                "bypass_cache": {"type": "boolean", "default": False},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "classify_task_complexity",
        "description": (
            "Compute deterministic complexity score [0.0, 1.0] and recommended model Tier "
            "(FAST_LOCAL, BALANCED, FRONTIER_REASONING) for an agent step."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "system_prompt": {"type": "string", "default": ""},
                "tools_schema": {"type": "array", "items": {"type": "object"}, "default": []},
                "require_json_schema": {"type": "object"},
                "latency_sla_ms": {"type": "number"},
                "estimated_output_tokens": {"type": "integer", "default": 256},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "inspect_endpoint_health",
        "description": (
            "Inspect 3-state circuit breaker status (CLOSED, OPEN, HALF_OPEN), rolling error rates, "
            "HTTP 429 rate-limit counters, and EMA latency for registered model endpoints."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "endpoint_id": {
                    "type": "string",
                    "description": "Optional endpoint ID; omit to inspect all registered endpoints.",
                }
            },
        },
    },
    {
        "name": "check_budget_status",
        "description": (
            "Query tenant and workflow token/USD budget utilization, remaining allowance, and "
            "80% soft-cap tier downgrade state."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tenant_id": {"type": "string", "default": "default-tenant"},
                "workflow_id": {"type": "string", "default": "default-workflow"},
            },
        },
    },
]


class SynapseMCPServer:
    """Stateless/Stateful JSON-RPC 2.0 MCP Server wrapping a SynapseRouter instance."""

    def __init__(self, router: Optional[SynapseRouter] = None) -> None:
        self.router = router or SynapseRouter()

    def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single JSON-RPC 2.0 request dictionary and return a JSON-RPC 2.0 response."""
        req_id = request.get("id")
        if request.get("jsonrpc") != "2.0" or "method" not in request:
            return self._error(req_id, -32600, "Invalid Request: must be JSON-RPC 2.0 with 'method'")

        method = request["method"]
        params = request.get("params") or {}

        try:
            if method == "initialize":
                return self._ok(
                    req_id,
                    {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {"name": "synapse-router-mcp", "version": "1.0.0"},
                        "capabilities": {"tools": {"listChanged": False}},
                    },
                )
            if method == "ping":
                return self._ok(req_id, {"status": "pong"})
            if method == "tools/list":
                return self._ok(req_id, {"tools": MCP_TOOLS_SPEC})
            if method == "tools/call":
                tool_name = params.get("name")
                arguments = params.get("arguments") or {}
                result_payload = self._dispatch_tool(tool_name, arguments)
                return self._ok(
                    req_id,
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(result_payload, sort_keys=True),
                            }
                        ],
                        "structuredContent": result_payload,
                        "isError": False,
                    },
                )
            # Also allow direct method calls by tool name for ergonomic RPC clients
            if method in {
                "route_agent_step",
                "classify_task_complexity",
                "inspect_endpoint_health",
                "check_budget_status",
            }:
                return self._ok(req_id, self._dispatch_tool(method, params))

            return self._error(req_id, -32601, f"Method not found: {method}")
        except (BudgetExceededError, NoHealthyEndpointError, ValueError) as exc:
            return self._error(req_id, -32000, str(exc))
        except Exception as exc:  # pragma: no cover
            return self._error(req_id, -32603, f"Internal error: {exc}")

    def _dispatch_tool(self, tool_name: Optional[str], args: Dict[str, Any]) -> Dict[str, Any]:
        if tool_name == "route_agent_step":
            if "prompt" not in args or not isinstance(args["prompt"], str):
                raise ValueError("Missing required string parameter 'prompt'")
            step_req = AgentStepRequest(
                prompt=args["prompt"],
                system_prompt=args.get("system_prompt", ""),
                tools_schema=args.get("tools_schema", []),
                require_json_schema=args.get("require_json_schema"),
                latency_sla_ms=args.get("latency_sla_ms"),
                max_cost_usd=args.get("max_cost_usd"),
                min_confidence=float(args.get("min_confidence", 0.78)),
                estimated_output_tokens=int(args.get("estimated_output_tokens", 256)),
                tenant_id=args.get("tenant_id", "default-tenant"),
                workflow_id=args.get("workflow_id", "default-workflow"),
                step_id=args.get("step_id", "step-0"),
                bypass_cache=bool(args.get("bypass_cache", False)),
            )
            receipt = self.router.execute_cascade(step_req)
            return receipt.to_dict()

        if tool_name == "classify_task_complexity":
            if "prompt" not in args or not isinstance(args["prompt"], str):
                raise ValueError("Missing required string parameter 'prompt'")
            step_req = AgentStepRequest(
                prompt=args["prompt"],
                system_prompt=args.get("system_prompt", ""),
                tools_schema=args.get("tools_schema", []),
                require_json_schema=args.get("require_json_schema"),
                latency_sla_ms=args.get("latency_sla_ms"),
                estimated_output_tokens=int(args.get("estimated_output_tokens", 256)),
            )
            analysis = self.router.classifier.analyze(step_req)
            data = asdict(analysis)
            data["recommended_tier"] = analysis.recommended_tier.name
            return data

        if tool_name == "inspect_endpoint_health":
            ep_id = args.get("endpoint_id")
            if ep_id:
                snap = self.router.health.snapshot(ep_id)
                out = asdict(snap)
                out["state"] = snap.state.value
                return out
            endpoints_out: Dict[str, Any] = {}
            for registered_id in sorted(self.router.endpoints.keys()):
                snap = self.router.health.snapshot(registered_id)
                item = asdict(snap)
                item["state"] = snap.state.value
                endpoints_out[registered_id] = item
            return {"endpoints": endpoints_out, "cache": self.router.cache.stats()}

        if tool_name == "check_budget_status":
            tenant_id = args.get("tenant_id", "default-tenant")
            workflow_id = args.get("workflow_id", "default-workflow")
            return self.router.budget.get_status(tenant_id=tenant_id, workflow_id=workflow_id)

        raise ValueError(f"Unknown MCP tool: {tool_name}")

    @staticmethod
    def _ok(req_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    def serve_stdio(self) -> None:  # pragma: no cover
        """Run JSON-RPC 2.0 loop over standard input/output."""
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                response = self.handle_request(payload)
            except json.JSONDecodeError:
                response = self._error(None, -32700, "Parse error: invalid JSON")
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
