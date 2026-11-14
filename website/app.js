/* Synapse-Router Interactive Routing, SimHash Cache, Circuit Breaker & Aegis G2 Studio */

function esc(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function showToast(msg) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(window.__toastTimer);
  window.__toastTimer = setTimeout(() => el.classList.remove("show"), 2200);
}

function fallbackCopy(text) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  try {
    const ok = document.execCommand("copy");
    showToast(ok ? "Copied to clipboard" : "Copy unavailable in this browser");
  } catch (err) {
    showToast("Copy failed: " + (err && err.message ? err.message : "clipboard blocked"));
  }
  document.body.removeChild(ta);
}

function copyText(text) {
  if (!text) return;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(
      () => showToast("Copied to clipboard"),
      () => fallbackCopy(text)
    );
  } else {
    fallbackCopy(text);
  }
}

document.addEventListener("click", (e) => {
  const copyBtn = e.target.closest("[data-copy]");
  if (copyBtn) {
    copyText(copyBtn.getAttribute("data-copy"));
    return;
  }
  const popBtn = e.target.closest("[data-popover-toggle]");
  if (popBtn) {
    const pop = document.getElementById(popBtn.getAttribute("data-popover-toggle"));
    if (pop) pop.classList.toggle("open");
    return;
  }
  document.querySelectorAll(".cta-popover.open").forEach((pop) => {
    if (!pop.closest(".cta-wrap")?.contains(e.target)) pop.classList.remove("open");
  });
});

const ROUTER_PRESETS = {
  trivial: {
    name: "Classify Support Intent (FAST_LOCAL)",
    prompt: "Classify the user message into billing, bug_report, or feature_request JSON.",
    slaMs: 250,
    budgetCapUsd: 0.05,
    tokensUsed: 420,
    simulate429: false,
    cacheHit: false,
    tier: "FAST_LOCAL",
    model: "gemini-2.5-flash-lite",
    costUsd: 0.0002,
    frontierCostUsd: 0.0075
  },
  cascade: {
    name: "Strict Tool Schema + HTTP 429 Failover (BALANCED)",
    prompt: "Extract invoice line items matching schema and invoke ledger_reconcile with retry safety.",
    slaMs: 900,
    budgetCapUsd: 0.05,
    tokensUsed: 1850,
    simulate429: true,
    cacheHit: false,
    tier: "BALANCED",
    model: "gemini-2.5-flash",
    costUsd: 0.0014,
    frontierCostUsd: 0.0145
  },
  frontier: {
    name: "Formal Invariant Proof (FRONTIER_REASONING)",
    prompt: "Prove deadlock freedom across distributed two-phase commit lock acquisition and generate TLA+ spec.",
    slaMs: 4000,
    budgetCapUsd: 0.15,
    tokensUsed: 6400,
    simulate429: false,
    cacheHit: false,
    tier: "FRONTIER_REASONING",
    model: "claude-3-7-sonnet",
    costUsd: 0.0285,
    frontierCostUsd: 0.0285
  }
};

function evaluateRoute(preset, overrides) {
  const slaMs = overrides.slaMs ?? preset.slaMs;
  const cacheHit = overrides.cacheHit ?? preset.cacheHit;
  const simulate429 = overrides.simulate429 ?? preset.simulate429;
  const budgetCap = overrides.budgetCap ?? preset.budgetCapUsd;

  if (cacheHit) {
    return {
      tier: "SEMANTIC_CACHE_HIT",
      model: "simhash64 (Hamming <= 2)",
      costUsd: 0.0,
      savedUsd: preset.frontierCostUsd,
      aegisPassed: true,
      violations: [],
      hops: ["SimHash64 lookup (0.04 ms) -> Cache Hit"]
    };
  }

  let tier = preset.tier;
  let model = preset.model;
  let costUsd = preset.costUsd;
  const hops = [];

  if (slaMs < 280 && tier === "FRONTIER_REASONING") {
    tier = "BALANCED";
    model = "gemini-2.5-flash";
    costUsd = 0.0014;
    hops.push("Tight SLA (<280ms) -> downgraded to BALANCED");
  } else {
    hops.push(`TaskComplexityClassifier -> ${tier}`);
  }

  if (simulate429) {
    hops.push("EndpointHealthRegistry: HTTP 429 Fast-Trip -> Circuit OPEN (<0.05ms failover)");
    if (tier === "FAST_LOCAL") {
      tier = "BALANCED";
      model = "claude-3-5-haiku";
      costUsd = 0.0016;
    }
  } else {
    hops.push(`Pareto utility winner: ${model}`);
  }

  const violations = [];
  if (costUsd > budgetCap) violations.push("USD_BUDGET_CAP_EXCEEDED");
  if (preset.tokensUsed > 120000) violations.push("TOKEN_CEILING_EXCEEDED");

  return {
    tier,
    model,
    costUsd,
    savedUsd: Math.max(0, preset.frontierCostUsd - costUsd),
    aegisPassed: violations.length === 0,
    violations,
    hops
  };
}

function initHomePage() {
  const sel = document.getElementById("quickRouteSelect");
  const out = document.getElementById("quickRouteOut");
  if (!sel || !out) return;

  function renderQuick() {
    const preset = ROUTER_PRESETS[sel.value] || ROUTER_PRESETS.trivial;
    const res = evaluateRoute(preset, {});
    out.innerHTML = `
      <div class="finding-item pass">
        <div class="finding-head">
          <strong>${esc(preset.name)}</strong>
          <span class="sev-pill ok">${esc(res.tier)} &middot; $${res.costUsd.toFixed(4)} USD</span>
        </div>
        <p style="margin:4px 0 0;font-size:0.86rem;color:var(--muted);">
          Selected: <code>${esc(res.model)}</code> &middot; Saved vs Frontier: <strong>$${res.savedUsd.toFixed(4)} USD</strong> &middot; Aegis G2: <strong>${res.aegisPassed ? "PASS" : "FAIL"}</strong>
        </p>
      </div>
    `;
  }

  sel.addEventListener("change", renderQuick);
  renderQuick();
}

function initStudioPage() {
  const chips = document.querySelectorAll("[data-preset]");
  const promptEl = document.getElementById("studioPrompt");
  const slaEl = document.getElementById("studioSla");
  const budgetEl = document.getElementById("studioBudget");
  const cacheEl = document.getElementById("studioCacheHit");
  const tripEl = document.getElementById("studioSimulate429");
  const runBtn = document.getElementById("studioRunBtn");
  const outEl = document.getElementById("studioOutput");
  const aegisOut = document.getElementById("studioAegisOut");
  if (!promptEl || !outEl) return;

  let currentKey = "trivial";

  function loadPreset(key) {
    currentKey = key;
    const p = ROUTER_PRESETS[key];
    if (!p) return;
    chips.forEach((c) => c.classList.toggle("active", c.getAttribute("data-preset") === key));
    promptEl.value = p.prompt;
    if (slaEl) slaEl.value = p.slaMs;
    if (budgetEl) budgetEl.value = p.budgetCapUsd;
    if (cacheEl) cacheEl.checked = p.cacheHit;
    if (tripEl) tripEl.checked = p.simulate429;
    renderRoute();
  }

  function renderRoute() {
    const p = ROUTER_PRESETS[currentKey] || ROUTER_PRESETS.trivial;
    const res = evaluateRoute(p, {
      slaMs: slaEl ? Number(slaEl.value) : p.slaMs,
      budgetCap: budgetEl ? Number(budgetEl.value) : p.budgetCapUsd,
      cacheHit: cacheEl ? cacheEl.checked : false,
      simulate429: tripEl ? tripEl.checked : false
    });

    outEl.innerHTML = `
      <div class="finding-item ${res.aegisPassed ? "pass" : "high"}">
        <div class="finding-head">
          <strong>Tier: ${esc(res.tier)} &middot; ${esc(res.model)}</strong>
          <span class="sev-pill ${res.aegisPassed ? "ok" : "high"}">${res.aegisPassed ? "Aegis G2 Passed" : "Budget Violation"}</span>
        </div>
        <p style="margin:4px 0 8px;font-size:0.86rem;color:var(--muted);">
          Step Cost: <strong>$${res.costUsd.toFixed(4)} USD</strong> &middot; Frontier Baseline: <strong>$${p.frontierCostUsd.toFixed(4)} USD</strong> &middot; Saved: <strong>$${res.savedUsd.toFixed(4)} USD</strong>
        </p>
        <ul style="margin:0;padding-left:18px;font-size:0.84rem;color:var(--muted);">
          ${res.hops.map((h) => `<li>${esc(h)}</li>`).join("")}
        </ul>
      </div>
    `;

    if (aegisOut) {
      const leaf = {
        gate_id: "G2_BUDGET_ROUTING",
        rfc6962_prefix: "0x00",
        passed: res.aegisPassed,
        score: res.aegisPassed ? 1.0 : 0.0,
        model_id: res.model,
        cost_usd: Number(res.costUsd.toFixed(4)),
        budget_cap_usd: Number((budgetEl ? Number(budgetEl.value) : p.budgetCapUsd).toFixed(4)),
        tokens_used: p.tokensUsed,
        violations: res.violations
      };
      aegisOut.textContent = JSON.stringify(leaf, null, 2);
    }
  }

  chips.forEach((c) => c.addEventListener("click", () => loadPreset(c.getAttribute("data-preset"))));
  if (runBtn) runBtn.addEventListener("click", renderRoute);
  [slaEl, budgetEl, cacheEl, tripEl].forEach((el) => {
    if (el) el.addEventListener("change", renderRoute);
  });

  loadPreset("trivial");
}

function initRulesPage() {
  const searchInput = document.getElementById("rulesSearch");
  const catSelect = document.getElementById("rulesCategory");
  const cards = document.querySelectorAll("[data-rule-card]");
  const countEl = document.getElementById("rulesCount");
  if (!searchInput || !catSelect || !cards.length) return;

  function filterRules() {
    const q = searchInput.value.trim().toLowerCase();
    const cat = catSelect.value;
    let shown = 0;
    cards.forEach((card) => {
      const match = (cat === "all" || card.getAttribute("data-cat") === cat) && (!q || card.textContent.toLowerCase().includes(q));
      card.style.display = match ? "" : "none";
      if (match) shown++;
    });
    if (countEl) countEl.textContent = `${shown} of ${cards.length} rules & tools shown`;
  }

  searchInput.addEventListener("input", filterRules);
  catSelect.addEventListener("change", filterRules);
  filterRules();
}

document.addEventListener("DOMContentLoaded", () => {
  initHomePage();
  initStudioPage();
  initRulesPage();
});
