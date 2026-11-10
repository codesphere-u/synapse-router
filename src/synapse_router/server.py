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
    {
        "name": "synapse_rate_limit_status",
        "description": (
            "Query sliding-window RPM and TPM consumption, ceiling limits, and saturation status for a model endpoint."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "endpoint_id": {"type": "string", "description": "Model endpoint ID to inspect."},
            },
            "required": ["endpoint_id"],
        },
    },
    {
        "name": "synapse_export_aegis_g2",
        "description": (
            "Export canonical RFC 6962 G2_BUDGET_ROUTING attestation leaf for a routing receipt or simulation parameters."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cost_usd": {"type": "number"},
                "budget_cap_usd": {"type": "number"},
                "tokens_used": {"type": "integer"},
                "model_id": {"type": "string"},
                "task_id": {"type": "string"},
                "task_prompt": {"type": "string"},
            },
        },
    },
]


MCP_RESOURCES_SPEC: List[Dict[str, Any]] = [
    {
        "uri": "synapse://endpoints/catalog",
        "name": "Model Endpoints & Routing Tiers Catalog",
        "description": "Registry of configured model endpoints across FAST_LOCAL, BALANCED, and FRONTIER_REASONING tiers with cost, latency SLA, and context limits.",
        "mimeType": "application/json",
    },
    {
        "uri": "synapse://health/summary",
        "name": "Circuit Breakers & Budget Status Summary",
        "description": "Live state of 3-state circuit breakers (CLOSED/OPEN/HALF_OPEN), sliding-window rate limiters, and tenant spend utilization.",
        "mimeType": "application/json",
    },
]

MCP_PROMPTS_SPEC: List[Dict[str, Any]] = [
    {
        "name": "route-task-advisor",
        "description": "Evaluate prompt complexity and recommend the optimal model tier (FAST_LOCAL vs BALANCED vs FRONTIER_REASONING).",
        "arguments": [
            {
                "name": "task_prompt",
                "description": "The user task or agent instruction to evaluate.",
                "required": True,
            },
            {
                "name": "latency_sla_ms",
                "description": "Optional strict latency SLA target in milliseconds.",
                "required": False,
            },
        ],
    },
    {
        "name": "budget-allocation-planner",
        "description": "Formulate a token and USD budget allocation strategy with 80% soft-cap degradation rules.",
        "arguments": [
            {
                "name": "workflow_name",
                "description": "Name or identifier of the agent workflow.",
                "required": True,
            },
            {
                "name": "target_cost_usd",
                "description": "Target ceiling cost in USD.",
                "required": True,
            },
        ],
    },
]


class SynapseMCPServer:
    """Stateless/Stateful JSON-RPC 2.0 MCP Server wrapping a SynapseRouter instance."""

    def __init__(self, router: Optional[SynapseRouter] = None) -> None:
        self.router = router or SynapseRouter()

    def get_resource_definitions(self) -> List[Dict[str, Any]]:
        return list(MCP_RESOURCES_SPEC)

    def get_prompt_definitions(self) -> List[Dict[str, Any]]:
        return list(MCP_PROMPTS_SPEC)

    def _read_resource(self, uri: str) -> Dict[str, Any]:
        if uri == "synapse://endpoints/catalog":
            endpoints_info = []
            for ep_id, ep in sorted(self.router.endpoints.items()):
                endpoints_info.append({
                    "endpoint_id": ep_id,
                    "provider": ep.provider,
                    "tier": ep.tier.name,
                    "cost_per_1k_input_tokens": ep.cost_per_1k_input,
                    "cost_per_1k_output_tokens": ep.cost_per_1k_output,
                    "p50_latency_ms": ep.p50_latency_ms,
                    "p95_latency_ms": ep.p95_latency_ms,
                    "context_window_tokens": ep.context_window,
                })
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps({"endpoints_count": len(endpoints_info), "endpoints": endpoints_info}, indent=2),
                    }
                ]
            }
        if uri == "synapse://health/summary":
            health_summary = {}
            for ep_id in sorted(self.router.endpoints.keys()):
                snap = self.router.health.snapshot(ep_id)
                health_summary[ep_id] = {
                    "state": snap.state.value,
                    "consecutive_failures": snap.consecutive_failures,
                    "rolling_error_rate": round(snap.rolling_error_rate, 4) if snap.rolling_error_rate is not None else 0.0,
                    "ema_latency_ms": round(snap.ema_latency_ms, 2) if snap.ema_latency_ms is not None else 0.0,
                    "is_available": snap.is_available,
                }
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps({"health": health_summary}, indent=2),
                    }
                ]
            }
        raise KeyError(f"Unknown resource URI: {uri}")

    def _get_prompt(self, name: str, arguments: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        args = arguments or {}
        if name == "route-task-advisor":
            task_prompt = args.get("task_prompt", "")
            sla = args.get("latency_sla_ms", "Not specified")
            return {
                "description": "Analyze prompt complexity and suggest optimal routing tier",
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": (
                                "Analyze the following agent task and recommend the best model routing tier "
                                "(FAST_LOCAL, BALANCED, or FRONTIER_REASONING):\n\n"
                                f"Task Prompt: {task_prompt}\n"
                                f"Latency SLA: {sla} ms\n\n"
                                "1. Break down required reasoning vs extraction.\n"
                                "2. Estimate input/output token volume.\n"
                                "3. Recommend model endpoint and fallback cascade."
                            ),
                        },
                    }
                ],
            }
        if name == "budget-allocation-planner":
            wf = args.get("workflow_name", "unnamed-workflow")
            target = args.get("target_cost_usd", "10.00")
            return {
                "description": "Formulate token/cost budget allocation plan",
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": (
                                f"Design a budget allocation plan for workflow '{wf}' with a cost ceiling of ${target}.\n"
                                "1. Allocate token quotas across high-frequency and low-frequency steps.\n"
                                "2. Set up the 80% soft-cap degradation rules.\n"
                                "3. Define circuit breaker thresholds for HTTP 429 and endpoint failovers."
                            ),
                        },
                    }
                ],
            }
        raise KeyError(f"Unknown prompt: {name}")

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
                        "serverInfo": {"name": "synapse-router-mcp", "version": "1.1.0"},
                        "capabilities": {
                            "tools": {"listChanged": False},
                            "resources": {"subscribe": False, "listChanged": False},
                            "prompts": {"listChanged": False},
                        },
                    },
                )
            if method == "ping":
                return self._ok(req_id, {"status": "pong"})
            if method == "tools/list":
                return self._ok(req_id, {"tools": MCP_TOOLS_SPEC})
            if method == "resources/list":
                return self._ok(req_id, {"resources": self.get_resource_definitions()})
            if method == "resources/read":
                uri = params.get("uri")
                if not uri or not isinstance(uri, str):
                    return self._error(req_id, -32602, "Missing 'uri' parameter.")
                return self._ok(req_id, self._read_resource(uri))
            if method == "prompts/list":
                return self._ok(req_id, {"prompts": self.get_prompt_definitions()})
            if method == "prompts/get":
                prompt_name = params.get("name")
                prompt_args = params.get("arguments")
                if not prompt_name or not isinstance(prompt_name, str):
                    return self._error(req_id, -32602, "Missing prompt 'name'.")
                return self._ok(req_id, self._get_prompt(prompt_name, prompt_args))
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
                "synapse_rate_limit_status",
                "synapse_export_aegis_g2",
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

        if tool_name == "synapse_rate_limit_status":
            endpoint_id = args.get("endpoint_id", "claude-3-5-sonnet")
            return self.router.rate_governor.get_status(endpoint_id)

        if tool_name == "synapse_export_aegis_g2":
            from synapse_router.aegis_bridge import create_aegis_g2_leaf
            return create_aegis_g2_leaf(
                receipt_or_eval=args,
                task_id=args.get("task_id", "synapse_routing"),
                task_prompt=args.get("task_prompt", ""),
                cost_usd=float(args.get("cost_usd", 0.0)),
                budget_cap_usd=float(args.get("budget_cap_usd", 1.0)),
                tokens_used=int(args.get("tokens_used", 0)),
                model_id=str(args.get("model_id", "claude-3-5-sonnet")),
            )

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
