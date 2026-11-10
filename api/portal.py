"""Interactive Developer Portal & Playground for Synapse Router.

Renders an ultra-fast, zero-dependency, responsive HTML5 developer interface
when a user or browser visits the endpoint with Accept: text/html.
"""

from __future__ import annotations

import html
import json
from typing import Any, Dict, List


def render_developer_portal(
    server_name: str,
    server_version: str,
    tagline: str,
    endpoint_url: str,
    github_url: str,
    tools: List[Dict[str, Any]],
    resources: List[Dict[str, Any]],
    prompts: List[Dict[str, Any]],
    sample_tool_call: Dict[str, Any],
) -> str:
    """Generate a self-contained HTML developer dashboard and playground."""

    safe_name = html.escape(server_name)
    safe_version = html.escape(server_version)
    safe_tagline = html.escape(tagline)
    safe_endpoint = html.escape(endpoint_url)
    safe_github = html.escape(github_url)

    tools_json = json.dumps(tools, indent=2)
    resources_json = json.dumps(resources, indent=2)
    prompts_json = json.dumps(prompts, indent=2)
    sample_call_json = json.dumps(sample_tool_call, indent=2)

    tool_cards = []
    for t in tools:
        t_name = html.escape(t.get("name", ""))
        t_desc = html.escape(t.get("description", ""))
        props = t.get("inputSchema", {}).get("properties", {})
        req = set(t.get("inputSchema", {}).get("required", []))
        param_badges = "".join(
            f'<span class="badge param {"req" if p in req else "opt"}">{html.escape(p)}{"*" if p in req else ""}</span>'
            for p in props.keys()
        )
        tool_cards.append(
            f"""
            <div class="card item-card" data-tool="{t_name}">
              <div class="card-head">
                <span class="mono font-bold text-accent">{t_name}</span>
                <button class="btn btn-sm btn-ghost" onclick="loadToolCall('{t_name}')">Test &rarr;</button>
              </div>
              <p class="text-sm text-dim">{t_desc}</p>
              <div class="param-row">{param_badges or '<span class="text-xs text-muted">No parameters</span>'}</div>
            </div>
            """
        )
    tools_html = "".join(tool_cards)

    res_cards = []
    for r in resources:
        r_uri = html.escape(r.get("uri", ""))
        r_name = html.escape(r.get("name", ""))
        r_desc = html.escape(r.get("description", ""))
        r_mime = html.escape(r.get("mimeType", "application/json"))
        res_cards.append(
            f"""
            <div class="card item-card">
              <div class="card-head">
                <span class="mono font-bold text-accent">{r_uri}</span>
                <button class="btn btn-sm btn-ghost" onclick="loadResourceRead('{r_uri}')">Read &rarr;</button>
              </div>
              <div class="text-xs text-muted mono">{r_name} &bull; {r_mime}</div>
              <p class="text-sm text-dim">{r_desc}</p>
            </div>
            """
        )
    resources_html = "".join(res_cards)

    prompt_cards = []
    for p in prompts:
        p_name = html.escape(p.get("name", ""))
        p_desc = html.escape(p.get("description", ""))
        args = p.get("arguments", [])
        arg_badges = "".join(
            f'<span class="badge param {"req" if a.get("required") else "opt"}">{html.escape(a.get("name", ""))}{"*" if a.get("required") else ""}</span>'
            for a in args
        )
        prompt_cards.append(
            f"""
            <div class="card item-card">
              <div class="card-head">
                <span class="mono font-bold text-accent">{p_name}</span>
                <button class="btn btn-sm btn-ghost" onclick="loadPromptGet('{p_name}')">Render &rarr;</button>
              </div>
              <p class="text-sm text-dim">{p_desc}</p>
              <div class="param-row">{arg_badges or '<span class="text-xs text-muted">No arguments</span>'}</div>
            </div>
            """
        )
    prompts_html = "".join(prompt_cards)

    claude_code_cmd = f"claude mcp add --transport http {safe_name} {safe_endpoint}"
    claude_desktop_cfg = json.dumps({"mcpServers": {safe_name: {"type": "http", "url": safe_endpoint}}}, indent=2)
    cursor_cfg = json.dumps({"mcpServers": {safe_name: {"type": "http", "url": safe_endpoint}}}, indent=2)
    smithery_cmd = f"npx -y @smithery/cli install @codesphere-u/{safe_name} --client claude"
    stdio_cmd = f"uvx --from git+{safe_github}.git {safe_name} mcp"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{safe_name} &bull; MCP Server & Live Playground</title>
  <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220%22><text y=%2226%22 font-size=%2226%22>🧠</text></svg>">
  <style>
    :root {{
      --bg: #0b0f19;
      --card-bg: #121826;
      --card-border: #1e293b;
      --text: #e2e8f0;
      --text-dim: #94a3b8;
      --text-muted: #64748b;
      --accent: #38bdf8;
      --accent-glow: rgba(56, 189, 248, 0.15);
      --emerald: #10b981;
      --emerald-glow: rgba(16, 185, 129, 0.15);
      --purple: #a855f7;
      --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
      --font-mono: ui-monospace, SFMono-Regular, "JetBrains Mono", Menlo, Consolas, monospace;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: var(--bg);
      color: var(--text);
      font-family: var(--font-sans);
      line-height: 1.5;
      padding: 24px;
      min-height: 100vh;
    }}
    .mono {{ font-family: var(--font-mono); }}
    .container {{ max-width: 1100px; margin: 0 auto; display: flex; flex-direction: column; gap: 24px; }}
    .header {{
      background: linear-gradient(135deg, #131b2e 0%, #0d121f 100%);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 28px;
      display: flex;
      flex-direction: column;
      gap: 16px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.3);
    }}
    .header-top {{ display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 16px; }}
    .title-group {{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
    h1 {{ font-size: 26px; font-weight: 700; color: #fff; }}
    .badge {{
      display: inline-flex; align-items: center; gap: 6px;
      padding: 4px 10px; border-radius: 9999px;
      font-size: 12px; font-weight: 600;
    }}
    .badge-live {{ background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); }}
    .badge-live::before {{
      content: ""; width: 8px; height: 8px; border-radius: 50%;
      background: #34d399; box-shadow: 0 0 8px #34d399;
      animation: pulse 2s infinite;
    }}
    @keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.4; }} }}
    .badge-proto {{ background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.3); }}
    .badge-ver {{ background: rgba(168, 85, 247, 0.15); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.3); }}
    .links {{ display: flex; gap: 10px; }}
    .btn {{
      display: inline-flex; align-items: center; gap: 6px;
      padding: 8px 14px; border-radius: 6px; font-size: 13px; font-weight: 600;
      cursor: pointer; text-decoration: none; border: 1px solid var(--card-border);
      background: #1e293b; color: #fff; transition: all 0.2s;
    }}
    .btn:hover {{ background: #334155; border-color: #475569; }}
    .btn-accent {{ background: #0284c7; border-color: #0369a1; }}
    .btn-accent:hover {{ background: #0369a1; }}
    .btn-sm {{ padding: 4px 10px; font-size: 12px; }}
    .btn-ghost {{ background: transparent; border-color: transparent; color: var(--accent); }}
    .btn-ghost:hover {{ background: var(--accent-glow); }}
    .grid-stats {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
    }}
    .stat-card {{
      background: var(--card-bg); border: 1px solid var(--card-border);
      border-radius: 8px; padding: 14px 18px;
    }}
    .stat-val {{ font-size: 22px; font-weight: 700; color: #fff; }}
    .stat-lbl {{ font-size: 12px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.05em; }}
    .card {{
      background: var(--card-bg); border: 1px solid var(--card-border);
      border-radius: 10px; padding: 20px;
    }}
    .tabs {{ display: flex; gap: 6px; border-bottom: 1px solid var(--card-border); padding-bottom: 8px; margin-bottom: 16px; flex-wrap: wrap; }}
    .tab-btn {{
      background: transparent; border: none; color: var(--text-dim);
      padding: 8px 14px; font-size: 13px; font-weight: 600; cursor: pointer;
      border-radius: 6px; transition: all 0.2s;
    }}
    .tab-btn:hover {{ color: #fff; background: rgba(255,255,255,0.05); }}
    .tab-btn.active {{ color: #fff; background: #1e293b; }}
    .snippet-box {{
      position: relative; background: #070a12; border: 1px solid #1e293b;
      border-radius: 8px; padding: 16px; overflow-x: auto;
    }}
    .snippet-box pre {{ margin: 0; color: #e2e8f0; font-family: var(--font-mono); font-size: 13px; white-space: pre-wrap; word-break: break-all; }}
    .copy-btn {{
      position: absolute; top: 10px; right: 10px;
      background: #1e293b; border: 1px solid #334155;
      color: #94a3b8; padding: 4px 10px; border-radius: 4px;
      font-size: 11px; cursor: pointer; font-family: var(--font-sans);
    }}
    .copy-btn:hover {{ color: #fff; background: #334155; }}
    .copy-btn.copied {{ background: #065f46; color: #34d399; border-color: #047857; }}
    .playground-grid {{
      display: grid; grid-template-columns: 1fr 1fr; gap: 16px;
    }}
    @media (max-width: 800px) {{
      .playground-grid {{ grid-template-columns: 1fr; }}
    }}
    .pane {{ display: flex; flex-direction: column; gap: 10px; }}
    .pane-head {{ display: flex; justify-content: space-between; align-items: center; }}
    .pane-title {{ font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-dim); }}
    textarea.code-editor {{
      width: 100%; min-height: 280px; background: #070a12;
      border: 1px solid var(--card-border); border-radius: 8px;
      padding: 14px; font-family: var(--font-mono); font-size: 12px;
      color: #e2e8f0; resize: vertical; outline: none; line-height: 1.4;
    }}
    textarea.code-editor:focus {{ border-color: var(--accent); }}
    .response-box {{
      min-height: 280px; max-height: 500px; background: #070a12;
      border: 1px solid var(--card-border); border-radius: 8px;
      padding: 14px; font-family: var(--font-mono); font-size: 12px;
      color: #38bdf8; overflow: auto; white-space: pre;
    }}
    .preset-bar {{ display: flex; gap: 6px; flex-wrap: wrap; }}
    .card-list {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }}
    .item-card {{ display: flex; flex-direction: column; gap: 8px; background: #0d1322; }}
    .card-head {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; }}
    .text-accent {{ color: var(--accent); }}
    .text-sm {{ font-size: 13px; }}
    .text-xs {{ font-size: 11px; }}
    .text-dim {{ color: var(--text-dim); }}
    .text-muted {{ color: var(--text-muted); }}
    .param-row {{ display: flex; gap: 4px; flex-wrap: wrap; margin-top: 4px; }}
    .param {{
      font-family: var(--font-mono); font-size: 10px; padding: 2px 6px;
      border-radius: 4px;
    }}
    .param.req {{ background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.3); }}
    .param.opt {{ background: rgba(148, 163, 184, 0.1); color: #94a3b8; border: 1px solid rgba(148, 163, 184, 0.2); }}
    .footer {{
      display: flex; justify-content: space-between; align-items: center;
      padding: 16px 4px; color: var(--text-muted); font-size: 12px;
      flex-wrap: wrap; gap: 12px; border-top: 1px solid #1e293b;
    }}
  </style>
</head>
<body>
  <div class="container">
    <!-- Header -->
    <header class="header">
      <div class="header-top">
        <div>
          <div class="title-group">
            <h1>{safe_name}</h1>
            <span class="badge badge-ver">v{safe_version}</span>
            <span class="badge badge-live">Operational</span>
            <span class="badge badge-proto">MCP 2024-11-05</span>
          </div>
          <p class="text-sm text-dim" style="margin-top: 6px;">{safe_tagline}</p>
        </div>
        <div class="links">
          <a class="btn" href="{safe_github}" target="_blank" rel="noopener">GitHub</a>
          <a class="btn" href="https://glama.ai/mcp" target="_blank" rel="noopener">Glama Gateway</a>
          <a class="btn" href="https://smithery.ai" target="_blank" rel="noopener">Smithery</a>
        </div>
      </div>
      <div class="grid-stats">
        <div class="stat-card">
          <div class="stat-val text-accent">{len(tools)}</div>
          <div class="stat-lbl">Tools Registered</div>
        </div>
        <div class="stat-card">
          <div class="stat-val" style="color: var(--emerald);">{len(resources)}</div>
          <div class="stat-lbl">Resources Available</div>
        </div>
        <div class="stat-card">
          <div class="stat-val" style="color: var(--purple);">{len(prompts)}</div>
          <div class="stat-lbl">Prompt Templates</div>
        </div>
        <div class="stat-card">
          <div class="stat-val" style="color: #f59e0b;">&lt; 1 ms</div>
          <div class="stat-lbl">Engine Latency</div>
        </div>
      </div>
    </header>

    <!-- Quick Connect Box -->
    <section class="card">
      <h2 style="font-size: 16px; margin-bottom: 12px; color: #fff;">1-Click Client Configuration</h2>
      <div class="tabs">
        <button class="tab-btn active" onclick="switchTab(this, 'tab-claude-code')">Claude Code</button>
        <button class="tab-btn" onclick="switchTab(this, 'tab-cursor')">Cursor</button>
        <button class="tab-btn" onclick="switchTab(this, 'tab-windsurf')">Windsurf</button>
        <button class="tab-btn" onclick="switchTab(this, 'tab-desktop')">Claude Desktop</button>
        <button class="tab-btn" onclick="switchTab(this, 'tab-smithery')">Smithery CLI</button>
        <button class="tab-btn" onclick="switchTab(this, 'tab-stdio')">Local Stdio</button>
      </div>

      <div id="tab-claude-code" class="tab-pane">
        <div class="snippet-box">
          <pre>{html.escape(claude_code_cmd)}</pre>
          <button class="copy-btn" onclick="copySnippet(this, `{claude_code_cmd}`)">Copy Command</button>
        </div>
        <p class="text-xs text-muted" style="margin-top: 8px;">Run directly in your terminal to register in Claude Code project/global config.</p>
      </div>

      <div id="tab-cursor" class="tab-pane" style="display: none;">
        <div class="snippet-box">
          <pre>{html.escape(cursor_cfg)}</pre>
          <button class="copy-btn" onclick="copySnippet(this, `{cursor_cfg}`)">Copy JSON</button>
        </div>
        <p class="text-xs text-muted" style="margin-top: 8px;">Paste into your project's <code>.cursor/mcp.json</code> or user settings.</p>
      </div>

      <div id="tab-windsurf" class="tab-pane" style="display: none;">
        <div class="snippet-box">
          <pre>{html.escape(cursor_cfg)}</pre>
          <button class="copy-btn" onclick="copySnippet(this, `{cursor_cfg}`)">Copy JSON</button>
        </div>
        <p class="text-xs text-muted" style="margin-top: 8px;">Paste into <code>~/.codeium/windsurf/mcp_config.json</code>.</p>
      </div>

      <div id="tab-desktop" class="tab-pane" style="display: none;">
        <div class="snippet-box">
          <pre>{html.escape(claude_desktop_cfg)}</pre>
          <button class="copy-btn" onclick="copySnippet(this, `{claude_desktop_cfg}`)">Copy JSON</button>
        </div>
        <p class="text-xs text-muted" style="margin-top: 8px;">Paste into <code>claude_desktop_config.json</code>.</p>
      </div>

      <div id="tab-smithery" class="tab-pane" style="display: none;">
        <div class="snippet-box">
          <pre>{html.escape(smithery_cmd)}</pre>
          <button class="copy-btn" onclick="copySnippet(this, `{smithery_cmd}`)">Copy Command</button>
        </div>
        <p class="text-xs text-muted" style="margin-top: 8px;">Installs via the Smithery registry orchestrator.</p>
      </div>

      <div id="tab-stdio" class="tab-pane" style="display: none;">
        <div class="snippet-box">
          <pre>{html.escape(stdio_cmd)}</pre>
          <button class="copy-btn" onclick="copySnippet(this, `{stdio_cmd}`)">Copy Command</button>
        </div>
        <p class="text-xs text-muted" style="margin-top: 8px;">Runs locally without any installation via uvx.</p>
      </div>
    </section>

    <!-- Interactive Playground -->
    <section class="card">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; flex-wrap: wrap; gap: 8px;">
        <h2 style="font-size: 16px; color: #fff;">Interactive JSON-RPC Playground</h2>
        <div class="preset-bar">
          <button class="btn btn-sm" onclick="loadPreset('init')">initialize</button>
          <button class="btn btn-sm" onclick="loadPreset('tools_list')">tools/list</button>
          <button class="btn btn-sm" onclick="loadPreset('sample_call')">tools/call</button>
          <button class="btn btn-sm" onclick="loadPreset('res_list')">resources/list</button>
          <button class="btn btn-sm" onclick="loadPreset('prompts_list')">prompts/list</button>
        </div>
      </div>

      <div class="playground-grid">
        <div class="pane">
          <div class="pane-head">
            <span class="pane-title">Request (JSON-RPC 2.0)</span>
            <button class="btn btn-sm btn-accent" id="send-btn" onclick="sendJsonRpc()">Send Request &rarr;</button>
          </div>
          <textarea id="rpc-request" class="code-editor">{html.escape(sample_call_json)}</textarea>
        </div>
        <div class="pane">
          <div class="pane-head">
            <span class="pane-title">Response</span>
            <span id="rpc-meta" class="text-xs mono text-muted">Ready</span>
          </div>
          <pre id="rpc-response" class="response-box">// Click "Send Request" to test live serverless execution</pre>
        </div>
      </div>
    </section>

    <!-- Capabilities Catalog -->
    <section class="card">
      <h2 style="font-size: 16px; margin-bottom: 14px; color: #fff;">Registered Tools ({len(tools)})</h2>
      <div class="card-list">{tools_html}</div>
    </section>

    <section class="card">
      <h2 style="font-size: 16px; margin-bottom: 14px; color: #fff;">Exposed Resources ({len(resources)})</h2>
      <div class="card-list">{resources_html}</div>
    </section>

    <section class="card">
      <h2 style="font-size: 16px; margin-bottom: 14px; color: #fff;">Prompts Catalog ({len(prompts)})</h2>
      <div class="card-list">{prompts_html}</div>
    </section>

    <!-- Footer -->
    <footer class="footer">
      <div>&copy; 2026 CodeSphere Collective &bull; Released under the MIT License</div>
      <div class="mono">Transport: Streamable HTTP (<code>POST /mcp</code>) + Legacy SSE (<code>GET /sse</code>)</div>
    </footer>
  </div>

  <script>
    const TOOLS = {tools_json};
    const RESOURCES = {resources_json};
    const PROMPTS = {prompts_json};
    const SAMPLE_CALL = {sample_call_json};

    function switchTab(btn, paneId) {{
      btn.parentElement.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      btn.closest('section').querySelectorAll('.tab-pane').forEach(p => p.style.display = 'none');
      document.getElementById(paneId).style.display = 'block';
    }}

    function copySnippet(btn, text) {{
      navigator.clipboard.writeText(text).then(() => {{
        const old = btn.innerText;
        btn.innerText = 'Copied!';
        btn.classList.add('copied');
        setTimeout(() => {{
          btn.innerText = old;
          btn.classList.remove('copied');
        }}, 2000);
      }});
    }}

    function loadPreset(kind) {{
      const req = document.getElementById('rpc-request');
      if (kind === 'init') {{
        req.value = JSON.stringify({{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {{"protocolVersion": "2024-11-05", "capabilities": {{}}, "clientInfo": {{"name": "playground", "version": "1.0.0"}}}}}}, null, 2);
      }} else if (kind === 'tools_list') {{
        req.value = JSON.stringify({{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}}, null, 2);
      }} else if (kind === 'sample_call') {{
        req.value = JSON.stringify(SAMPLE_CALL, null, 2);
      }} else if (kind === 'res_list') {{
        req.value = JSON.stringify({{"jsonrpc": "2.0", "id": 3, "method": "resources/list"}}, null, 2);
      }} else if (kind === 'prompts_list') {{
        req.value = JSON.stringify({{"jsonrpc": "2.0", "id": 4, "method": "prompts/list"}}, null, 2);
      }}
    }}

    function loadToolCall(name) {{
      const tool = TOOLS.find(t => t.name === name);
      const props = (tool && tool.inputSchema && tool.inputSchema.properties) || {{}};
      const args = {{}};
      for (const [k, v] of Object.entries(props)) {{
        if (v.type === 'string') args[k] = v.default || 'test-sample';
        else if (v.type === 'integer' || v.type === 'number') args[k] = v.default || 10;
        else if (v.type === 'boolean') args[k] = v.default || false;
        else if (v.type === 'array') args[k] = [];
        else args[k] = {{}};
      }}
      document.getElementById('rpc-request').value = JSON.stringify({{
        "jsonrpc": "2.0",
        "id": 10,
        "method": "tools/call",
        "params": {{ "name": name, "arguments": args }}
      }}, null, 2);
      document.getElementById('send-btn').scrollIntoView({{ behavior: 'smooth', block: 'center' }});
    }}

    function loadResourceRead(uri) {{
      document.getElementById('rpc-request').value = JSON.stringify({{
        "jsonrpc": "2.0",
        "id": 20,
        "method": "resources/read",
        "params": {{ "uri": uri }}
      }}, null, 2);
      document.getElementById('send-btn').scrollIntoView({{ behavior: 'smooth', block: 'center' }});
    }}

    function loadPromptGet(name) {{
      const p = PROMPTS.find(x => x.name === name);
      const args = {{}};
      if (p && p.arguments) {{
        for (const a of p.arguments) {{
          args[a.name] = 'example-value';
        }}
      }}
      document.getElementById('rpc-request').value = JSON.stringify({{
        "jsonrpc": "2.0",
        "id": 30,
        "method": "prompts/get",
        "params": {{ "name": name, "arguments": args }}
      }}, null, 2);
      document.getElementById('send-btn').scrollIntoView({{ behavior: 'smooth', block: 'center' }});
    }}

    async function sendJsonRpc() {{
      const btn = document.getElementById('send-btn');
      const meta = document.getElementById('rpc-meta');
      const respBox = document.getElementById('rpc-response');
      const raw = document.getElementById('rpc-request').value;

      btn.disabled = true;
      btn.innerText = 'Sending...';
      meta.innerText = 'Executing...';
      const t0 = performance.now();

      try {{
        const res = await fetch('/mcp', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: raw
        }});
        const dur = (performance.now() - t0).toFixed(1);
        const data = await res.json();
        meta.innerHTML = `<span style="color: var(--emerald);">${{res.status}} OK</span> &bull; ${{dur}} ms`;
        respBox.textContent = JSON.stringify(data, null, 2);
      }} catch (err) {{
        const dur = (performance.now() - t0).toFixed(1);
        meta.innerHTML = `<span style="color: #ef4444;">Error</span> &bull; ${{dur}} ms`;
        respBox.textContent = String(err);
      }} finally {{
        btn.disabled = false;
        btn.innerText = 'Send Request →';
      }}
    }}
  </script>
</body>
</html>
"""
