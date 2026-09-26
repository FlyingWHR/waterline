// Waterline control panel. Plain ES module, no build. Talks only to this origin's /api/*; addresses come from /api/health.
const VIEM = "https://esm.sh/viem@2.56.9";
const QRLIB = "https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/+esm";
const SCAN = "https://sepolia.etherscan.io";
const CLASSES = { 0: "unknown", 1: "H100 SXM", 2: "H100 PCIe", 3: "A100" }; // filled from /api/gpu-classes at boot
let GPU_TABLE = [];
const REFS = [[108, "A100"], [114, "H100 PCIe"], [132, "H100 SXM"]];
const ENS_KEYS = ["status", "class", "cores", "pct_of_spec", "passes", "degraded", "fails", "humans", "recoveries", "fingerprint", "report"];
const PROVIDER_KEYS = ["status", "gpus", "failed_gpus", "humans", "passes", "fails", "note"];
const TOKEN_KEY = "waterline.agent_token";
const WRITE_PATH = { multibaas: "MultiBaas", rpc: "RPC", "dry-run": "dry run" };
const view = document.getElementById("view");

// ---- small helpers -----------------------------------------------------------------------------------------
function h(tag, props = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k in e && !k.includes("-")) e[k] = v;
    else e.setAttribute(k, v);
  }
  e.append(...kids.flat().filter((k) => k != null && k !== false));
  return e;
}
const S = (tag, attrs = {}, text) => {
  const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (text != null) e.textContent = text;
  return e;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const short = (x) => (x && x.length > 16 ? x.slice(0, 10) + "…" + x.slice(-4) : x || "—");
const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString() : "—");
const ago = (ts) => {
  const m = Math.max(0, Math.round((Date.now() / 1000 - ts) / 60));
  return m < 1 ? "just now" : m < 60 ? `${m} min ago` : m < 2880 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} days ago`;
};
const cls = (c) => CLASSES[c] ?? "unknown";
const scan = (kind, x) => (x ? h("a", { href: `${SCAN}/${kind}/${x}`, target: "_blank", rel: "noopener", className: "mono" }, short(x)) : null);
const txLink = (tx, published) => scan("tx", tx) || h("span", { className: "sub" }, published ? "dry-run, no tx" : "—");
const pill = (text, kind) => h("span", { className: "pill st-" + (kind || String(text).split(" ")[0]) }, text);
const verdictPill = (r) =>
  r.verdict === "pass" ? pill("pass") : r.verdict === "degraded" ? pill("degraded · published", "degraded")
    : r.published ? pill("fail · published", "fail") : pill("fail · awaiting approval", "pending");
// Why a right chip ran slow, from the machine's own telemetry: it explains a degraded check, it never decides one.
function slowCause(hr) {
  const seen = hr?.burn?.reasons_seen || [];
  if (seen.some((x) => /thermal|HW slowdown/.test(x))) return "thermal throttling";
  if (seen.some((x) => /power/.test(x))) return "a power limit";
  if (hr?.device?.mig === "enabled") return "a MIG slice of a shared card";
  return null;
}
const needsApproval = (r) => r.verdict === "fail" && !r.published;
const token = {
  get() { try { return localStorage.getItem(TOKEN_KEY); } catch { return null; } },
  set(v) { try { v ? localStorage.setItem(TOKEN_KEY, v) : localStorage.removeItem(TOKEN_KEY); } catch { /* private mode */ } },
};

async function api(path, body) {
  let r;
  try {
    r = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch {
    throw Object.assign(new Error("Couldn't reach the Waterline API."), { status: 0 });
  }
  const data = await r.json().catch(() => null);
  if (!r.ok) throw Object.assign(new Error(data?.error || `The API answered ${r.status}.`), { status: r.status });
  return data;
}
let healthP;
const getHealth = (fresh) => (fresh || !healthP ? (healthP = api("/api/health").catch((e) => { healthP = null; throw e; })) : healthP);

const head = (kicker, title, ...rest) => h("div", { className: "pagehead" }, h("div", { className: "kicker" }, kicker), h("h2", {}, title), ...rest);
const section = (title, note, ...kids) =>
  h("section", {}, h("div", { className: "sechead" }, h("h3", {}, title), note ? h("span", { className: "count" }, note) : null), ...kids);
const table = (cols, rows) => {
  for (const tr of rows) [...tr.children].forEach((td, i) => cols[i] && td.setAttribute("data-label", cols[i])); // phone layout labels
  return h("div", { className: "tbl" }, h("table", {}, h("thead", {}, h("tr", {}, cols.map((c) => h("th", { scope: "col" }, c)))),
    h("tbody", {}, rows)));
};

// ---- router ------------------------------------------------------------------------------------------------
const routes = { "": [overview, "Overview"], gpus: [gpus, "GPUs"], checks: [checks, "Checks"], check: [checkDetail, "Check"], r: [byHash, "Check"], name: [namePage, "Name"],
  providers: [providersView, "Providers"], leaderboard: [providersView, "Providers"], models: [modelsView, "Models"], world: [worldView, "World ID"], about: [worldView, "World ID"] };
let nav = 0;
async function route(focus) {
  const my = ++nav;
  const [, name = "", arg] = location.hash.split("/");
  const [fn, title] = routes[name] || routes[""];
  const tab = { check: "checks", r: "checks", models: "providers", leaderboard: "providers", about: "world", name: "gpus" }[name] || (name in routes ? name : "");
  for (const a of document.querySelectorAll(".tabs a"))
    a.getAttribute("href") === `#/${tab}` ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current");
  document.title = `${title} · Waterline`;
  sessionBadge();
  view.setAttribute("aria-busy", "true");
  let kids;
  try {
    kids = await fn(arg && decodeURIComponent(arg));
  } catch (e) {
    kids = [head("Error", "Something went wrong"), h("p", { className: "err", role: "alert" }, e.message)];
  }
  if (my !== nav) return;
  view.replaceChildren(...kids);
  view.removeAttribute("aria-busy");
  if (focus) view.focus({ preventScroll: true }), scrollTo(0, 0);
}
addEventListener("hashchange", () => (dlg.open ? dlg.close() : route(true))); // closing the dialog re-routes
// class names first (every table and pill uses them); a failed load keeps the three built in
api("/api/gpu-classes").then((t) => { GPU_TABLE = t; for (const c of t) CLASSES[c.code] = c.name; }).catch(() => {}).finally(() => route(false));

// ---- overview ----------------------------------------------------------------------------------------------
async function overview() {
  const [hl, g, reps] = await Promise.all([getHealth(true), api("/api/gpus"), api("/api/reports?limit=5")]);
  const canvas = h("canvas", { "aria-hidden": "true" });
  requestAnimationFrame(() => terrain(canvas));
  const count = (st) => g.gpus.filter((x) => x.status.startsWith(st)).length;
  const c = hl.chain;
  const sum = (k) => g.gpus.reduce((n, x) => n + (x[k] || 0), 0);
  const checksN = sum("passes") + sum("fails"), humansN = sum("humans");
  const lastGpu = [...g.gpus].sort((a, b) => (b.last_at || 0) - (a.last_at || 0))[0];
  const lastTx = reps.find((r) => r.tx), lastIdx = reps.find((r) => r.indexed_at);
  const tile = (layer, label, big, ...small) =>
    h("div", { className: "tile c-" + layer }, h("div", { className: "label" }, h("i", { className: "dot" }), label), h("b", {}, big), ...small.map((s) => h("span", {}, s)));
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  // only what has happened: a tile appears once there is something real to show
  const proof = [
    checksN && tile("chain", "On the record", plural(checksN, "check"), lastTx ? h("span", {}, "Latest ", scan("tx", lastTx.tx), " · ", ago(lastTx.created_at)) : null),
    lastGpu?.gpu_name && tile("ens", "Named on ENS", lastGpu.gpu_name, h("span", {}, pill(lastGpu.status), " ", h("a", { href: "#/gpus" }, "look it up →"))),
    humansN && tile("world", "Approved with World ID", plural(humansN, "failure report"), "Each one approved by a person before it went public."),
    lastIdx && tile("mb", "Indexed by MultiBaas", ago(lastIdx.indexed_at), h("a", { href: `#/check/${lastIdx.report_id}` }, lastIdx.gpu_name || "the latest check")),
  ].filter(Boolean);
  return [
    h("div", { className: "hero" }, canvas,
      h("div", { className: "hero-copy" }, h("div", { className: "kicker" }, "Proof of Delivered Compute"),
        h("h1", {}, "Waterline"),
        h("p", { className: "lede" }, "Check that the GPU you rent delivers what you pay for: the right chip, at its rated speed.")),
      oneLine()),
    section("How a check works", "sealed work, a deadline, a core count", flowDiagram()),
    ...(proof.length ? [section("Proof so far", c.mode === "live" ? "live on Ethereum Sepolia" : "dry run: nothing is sent to the chain", h("div", { className: "tiles" }, proof))] : []),
    ...(g.gpus.length ? [section("GPUs on record", g.source === "multibaas" ? "from MultiBaas" : "from this API", h("div", { className: "nums" },
      ...[["Checked", g.gpus.length, ""], ["Pass", count("pass") + count("recovered"), "st-pass"], ["Degraded", count("degraded"), "st-degraded"],
        ["Suspect", count("suspect"), "st-suspect"], ["Failed", count("failed"), "st-failed"]]
        .map(([l, n, k]) => h("div", { className: "num" }, h("span", { className: "label" }, l), h("b", { className: k }, String(n))))))] : []),
    section("Recent checks", null, reps.length ? checksTable(reps) : h("p", { className: "empty" }, "No checks yet. Run the agent against a pod to see one here."),
      h("p", {}, h("a", { href: "#/checks" }, "All checks →"))),
  ];
}

// The one-line check a renter pastes into the rented pod's terminal. Nothing of ours is written to the pod.
const POPULAR = ["runpod", "vastai", "lambda", "coreweave", "nebius", "aws", "gcp", "azure"]; // top of the provider picker
function oneLine() {
  // the two variables are pickers inside the command: the provider (known names, or type your own) and the listed GPU
  const fit = (el, text) => { el.style.width = `${Math.max(4, text.length) + 2.2}ch`; };
  const pickCloud = h("select", { id: "ol-cloud", className: "var", required: true, "aria-label": "Your provider",
    onchange: () => {
      reset();
      // the list reads as names (Vast.ai); the command shows the chosen one's label (vastai)
      for (const o of pickCloud.options) if (o.dataset.name) o.label = o.selected ? o.value : o.dataset.name;
      if (pickCloud.value === "other") { pickCloud.replaceWith(other); other.focus(); } else fit(pickCloud, pickCloud.value);
    } },
    h("option", { value: "", disabled: true, selected: true }, "provider"));
  const other = h("input", { id: "ol-cloud-other", className: "var", placeholder: "your-provider", spellcheck: false, autocomplete: "off",
    "aria-label": "Your provider's name, lowercase", oninput: () => { fit(other, other.value || other.placeholder); reset(); },
    // left empty: back to the list (when it loaded)
    onblur: () => { if (other.isConnected && !other.value.trim() && pickCloud.options.length > 1) { pickCloud.value = ""; fit(pickCloud, "provider"); other.replaceWith(pickCloud); } } });
  fit(pickCloud, "provider"); fit(other, other.placeholder);
  api("/api/providers/known").then((ps) => pickCloud.append(
    ...[["Popular", (p) => POPULAR.includes(p.slug)], ["GPU clouds", (p) => p.kind === "cloud"], ["Marketplaces (the host is a third party)", (p) => p.kind === "marketplace"]]
      .map(([label, keep]) => h("optgroup", { label }, ps.filter(keep)
        .sort((a, b) => label === "Popular" ? POPULAR.indexOf(a.slug) - POPULAR.indexOf(b.slug) : a.name.localeCompare(b.name))
        .map((p) => { const o = h("option", { value: p.slug, title: `${p.name}: ${p.slug}` }, p.name); o.dataset.name = p.name; return o; }))),
    h("option", { value: "other" }, "other…"))).catch(() => pickCloud.replaceWith(other));
  const gpu = h("select", { id: "ol-gpu", className: "var", required: true, "aria-label": "The GPU the listing promises", onchange: () => { for (const o of gpu.options) if (o.dataset.name) o.label = o.selected ? o.value : o.dataset.name; fit(gpu, gpu.value); reset(); } },
    h("option", { value: "", disabled: true, selected: true }, "gpu"),
    (GPU_TABLE.length ? GPU_TABLE : [{ slug: "h100", name: "H100 SXM" }, { slug: "h100-pcie", name: "H100 PCIe" }, { slug: "a100", name: "A100" }])
      .map((g) => { const o = h("option", { value: g.slug, title: `${g.name}: ${g.slug}` }, g.name); o.dataset.name = g.name; return o; }));
  fit(gpu, "gpu");
  // a typed name becomes a valid label: lowercase, a-z 0-9 and hyphens (what the API accepts)
  const cloudValue = () => (other.isConnected ? other.value.trim().toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 63) : pickCloud.value);
  const copy = h("button", { type: "button", className: "btn sm", onclick: async () => {
    const [c, g] = [cloudValue(), gpu.value];
    if (!c || !g) { (!c ? (other.isConnected ? other : pickCloud) : gpu).focus(); copy.textContent = !c ? "Pick your provider" : "Pick the GPU"; return; }
    try { await navigator.clipboard.writeText(`curl -fsSL ${location.host}/run | python3 - ${c} ${g}`); copy.textContent = "Copied"; } catch { copy.textContent = "Select and copy"; }
  } }, "Copy");
  function reset() { copy.textContent = "Copy"; }
  return h("div", { className: "try" },
    h("div", { className: "try-head" }, h("span", { className: "label" }, "Check your GPU · in the rented pod"), copy),
    h("div", { className: "oneline" }, h("span", { className: "prompt", "aria-hidden": "true" }, "$"),
      h("code", {}, `curl -fsSL ${location.host}/run | python3 - `, h("span", { className: "pick" }, pickCloud), " ", h("span", { className: "pick" }, gpu))),
    h("p", { className: "sub small" }, "Pick your provider and the GPU its listing promises. Runs from memory; nothing stays on the pod."));
}

// "How a check works": nodes and wires in HTML, so it wraps to a column on phones instead of being cut off.
// Isometric glyphs for the diagram: small extruded boxes in the step's colour (top lit, sides in shade). Drawn back to front.
const SVGNS = "http://www.w3.org/2000/svg";
function isoGlyph(boxes) {
  const P = (x, y, z) => `${(32 + (x - y) * 0.866).toFixed(1)},${(36 + (x + y) * 0.5 - z).toFixed(1)}`;
  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("viewBox", "0 0 64 56"); svg.setAttribute("class", "glyph"); svg.setAttribute("aria-hidden", "true");
  for (const [x, y, z, w, d, hgt] of boxes) {
    const [x1, y1, z1] = [x + w, y + d, z + hgt];
    for (const [cls, pts] of [["side", [P(x1, y, z), P(x1, y1, z), P(x1, y1, z1), P(x1, y, z1)]],
                              ["front", [P(x, y1, z), P(x1, y1, z), P(x1, y1, z1), P(x, y1, z1)]],
                              ["top", [P(x, y, z1), P(x1, y, z1), P(x1, y1, z1), P(x, y1, z1)]]]) {
      const f = document.createElementNS(SVGNS, "polygon");
      f.setAttribute("points", pts.join(" ")); f.setAttribute("class", cls);
      svg.append(f);
    }
  }
  return svg;
}
const GLYPHS = {
  people: [[-12, -9, 0, 24, 18, 2], [-12, -11, 0, 24, 2, 15]], // a laptop: base and screen
  pod: [[-13, -10, 0, 26, 20, 4], [-5, -4, 4, 10, 8, 4]], // a GPU board with its die
  api: [[-10, -8, 0, 20, 16, 5], [-10, -8, 7, 20, 16, 5], [-10, -8, 14, 20, 16, 5]], // a server stack
  chain: [[-15, -2, 0, 12, 12, 12], [2, -11, 0, 12, 12, 12]], // two linked blocks
  ens: [[-4, -14, 12, 8, 8, 7], [-12, 0, 0, 8, 8, 7], [4, 0, 0, 8, 8, 7]], // a name and its children
  index: [[-12, -8, 0, 22, 14, 3], [-10, -8, 5, 22, 14, 3], [-8, -8, 10, 22, 14, 3], [-6, -8, 15, 22, 14, 3]], // an index: pages of history
};

function flowDiagram() {
  const node = (layer, name, what) => h("div", { className: "node c-" + layer }, isoGlyph(GLYPHS[layer]), h("b", {}, name), h("small", {}, what));
  const wire = (label, proof) => h("div", { className: "wire" + (proof ? " proof" : ""), "aria-hidden": "true" }, h("span", {}, label));
  const att = (layer, name, what) => h("div", { className: "att c-" + layer }, h("b", {}, name), h("small", {}, what));
  return h("div", { className: "panel" },
    h("div", { className: "flow", role: "img", "aria-label": "Agent starts the profiler in the rented pod. The profiler answers the Waterline API's puzzle. The API records the report on Marks on Sepolia, which answers for the GPU's ENS name and rolls it up to its provider's name. World approves failures at the API; MultiBaas indexes Marks' history." },
      h("div", { className: "stage" }, node("people", "Agent", "renter's laptop")),
      wire("starts over SSH"),
      h("div", { className: "stage" }, node("pod", "Profiler", "in the rented pod")),
      wire("seed ⇄ answer", true),
      h("div", { className: "stage" }, node("api", "Waterline API", "times it, re-checks it"), att("world", "World", "approves failures")),
      wire("records", true),
      h("div", { className: "stage" }, node("chain", "Marks", "contract on Sepolia"), att("mb", "MultiBaas", "indexes history")),
      wire("resolves", true),
      h("div", { className: "stage" }, node("ens", "ENS names", "one per GPU"), att("ens", "rolls up to", "its provider's name"))),
    h("div", { className: "legend" }, h("span", {}, h("i", { className: "sw-proof" }), "proof path"), h("span", {}, h("i", { className: "sw-att" }), "attached to a step")),
    h("div", { className: "cards partners", "aria-label": "Built on" },
      tipCard("ens", isoGlyph(GLYPHS.ens), "ENSv2", "Every GPU gets a name",
        "Marks resolves gpu-….<provider>.waterline.eth and <provider>.waterline.eth as a wildcard resolver: nothing is registered per GPU. ENSv2 roles decide who writes: the reporter writes records, a provider may write only its own note."),
      tipCard("world", worldGlyph(), "World ID for Agents", "A person behind every failure",
        "No failure goes onchain without a person: a fresh World approval per report, or a bounded mandate that lets their agents report. Either way one person is one voice per GPU and per provider, pseudonymous in public."),
      tipCard("mb", isoGlyph(GLYPHS.index), "Curvegrid MultiBaas", "History agents can query",
        "MultiBaas builds and sends each Marks transaction (we sign it), indexes every Reported and ProviderTally event, and calls our webhook when a check is indexed. Agents query it directly to skip bad GPUs and providers."),
      tipCard("chain", isoGlyph(GLYPHS.chain), "Ethereum Sepolia", "Where the record lives",
        "Marks, one contract, holds every verdict, the per-GPU and per-provider tallies and the evidence hash of the latest check, and answers the ENS names.")),
    h("div", { className: "cards rules" },
      tipCard("pass", null, "A pass needs real silicon", "Passes publish at once",
        "Correct answers, re-graded by us on a random slice, inside the deadline: that takes the work itself on a real GPU, so a pass needs no one's approval."),
      tipCard("degraded", null, "Chip class is heat-proof", "Slow is degraded, not failed",
        "Heat, power caps and sharing slow a chip but can't remove cores or FP8. A wrong chip fails; the right chip running slow is degraded, published with its numbers."),
      tipCard("fail", null, "A failure needs people, and rolls up", "Two people mark a GPU failed",
        "One World ID is one voice on the GPU and on its provider. Two different people mark a GPU failed; a person counts once per provider however many GPUs they report, so renaming a chip doesn't clean the provider.")));
}

// A card: icon, name, a short line, and the detail on hover or keyboard focus (tap on touch).
function tipCard(tone, glyph, name, intro, more) {
  return h("div", { className: "tip c-" + tone, tabIndex: 0 },
    glyph, h("b", {}, name), h("span", { className: "intro" }, intro), h("span", { className: "more" }, more));
}
function worldGlyph() { // an orb and its orbit
  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("viewBox", "0 0 64 56"); svg.setAttribute("class", "glyph"); svg.setAttribute("aria-hidden", "true");
  for (const [tag, attrs] of [["circle", { cx: 32, cy: 28, r: 15, class: "front" }], ["ellipse", { cx: 32, cy: 28, rx: 25, ry: 8, class: "ring" }],
                              ["circle", { cx: 26, cy: 22, r: 5, class: "top" }]]) {
    const e = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    svg.append(e);
  }
  return svg;
}

// Perspective wireframe: rows recede to a horizon, amplitude and opacity grow toward the viewer, one mint
// contour is the waterline. ~30 fps; one still frame (redrawn on resize) with reduced motion. Stops off screen.
function terrain(canvas) {
  const ctx = canvas.getContext("2d");
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const ROWS = 44, COLS = 240, FAR = 0.2, WATER = 29, t0 = performance.now();
  const lift = (x, z) => 15 * Math.sin(x * 0.009 + z * 0.05) + 9 * Math.sin(x * 0.021 - z * 0.31) + 11 * Math.cos(z * 0.47 + x * 0.0037) + 13 * Math.sin((x + z * 38) * 0.0052);
  let last = -Infinity;
  const draw = (now) => {
    const w = canvas.clientWidth, H = canvas.clientHeight, dpr = Math.min(devicePixelRatio || 1, 2);
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(H * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(H * dpr); }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, H);
    const t = still ? 0 : (now - t0) / 1000, horizon = H * 0.06, span = w / FAR, grid = [];
    for (let r = 0; r < ROWS; r++) {
      const p = r / (ROWS - 1), s = FAR + (1 - FAR) * p, y0 = horizon + (H - horizon) * p ** 1.8, pts = new Float32Array(COLS * 2);
      for (let c = 0; c < COLS; c++) {
        const wx = (c / (COLS - 1) - 0.5) * span;
        pts[2 * c] = w / 2 + wx * s;
        pts[2 * c + 1] = y0 - lift(wx, r - t * 1.6) * s * 1.15;
      }
      grid.push(pts);
    }
    const line = (get, n) => { ctx.beginPath(); for (let i = 0; i < n; i++) { const [x, y] = get(i); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); } ctx.stroke(); };
    ctx.lineWidth = 0.7; // receding lines toward the vanishing point, faint
    for (let c = 0; c < COLS; c += 10) {
      ctx.strokeStyle = "rgba(126,196,210,.07)";
      line((r) => [grid[r][2 * c], grid[r][2 * c + 1]], ROWS);
    }
    grid.forEach((pts, r) => {
      const p = r / (ROWS - 1), water = r === WATER;
      ctx.strokeStyle = water ? "rgba(95,227,185,.95)" : `rgba(126,196,210,${(0.05 + 0.4 * p).toFixed(3)})`;
      ctx.lineWidth = water ? 1.5 : 0.8;
      line((c) => [pts[2 * c], pts[2 * c + 1]], COLS);
    });
  };
  const frame = (now) => {
    if (!canvas.isConnected) return;
    requestAnimationFrame(frame);
    if (now - last < 33) return; // ~30 fps is plenty
    last = now;
    draw(now);
  };
  if (still) {
    draw(0);
    const ro = new ResizeObserver(() => (canvas.isConnected ? draw(0) : ro.disconnect()));
    ro.observe(canvas);
  } else requestAnimationFrame(frame);
}

// ---- GPUs --------------------------------------------------------------------------------------------------
async function gpus() {
  const [g, pv] = await Promise.all([api("/api/gpus"), api("/api/providers").catch(() => ({ providers: [] }))]);
  const [form, out, run] = ensLookup();
  const look = (name) => { run(name); form.scrollIntoView({ block: "center" }); };
  const rows = g.gpus.map((x) => h("tr", {},
    h("td", { className: "mono", title: x.node }, x.gpu_name || short(x.node)),
    h("td", {}, cls(x.cls)), h("td", {}, String(x.cores ?? "—")), h("td", {}, String(x.passes)), h("td", {}, String(x.degraded ?? "—")), h("td", {}, String(x.fails)),
    h("td", {}, String(x.humans)), h("td", {}, pill(x.status)), h("td", {}, when(x.last_at)),
    h("td", {}, x.gpu_name ? h("button", { type: "button", className: "btn sm", onclick: () => look(x.gpu_name) }, "Look up") : null)));
  return [
    head("GPUs", "GPU health", h("p", { className: "sub" }, "Every GPU on record. Two different people mark a GPU failed; two passes after that bring it back as recovered.")),
    ...(g.gpus.length ? [section("The name tree", "every level keeps its own score",
      h("p", { className: "sub" }, "Each GPU is named under its provider, and one World approval counts on both: two different people mark a GPU failed, and a person counts once per provider however many of its GPUs they report. A renamed chip gets a new GPU name, not a clean provider. Open any name for its record, read live from ENS."),
      nameTree(pv, g.gpus, look))] : []),
    section("On record", g.source === "multibaas" ? "source: MultiBaas (Reported events on Marks)" : "source: this API's own records",
      g.error ? h("p", { className: "err" }, g.error) : null,
      rows.length ? table(["GPU", "Measured as", "Cores", "Passes", "Degraded", "Fails", "People", "Status", "Last report", ""], rows)
        : h("p", { className: "empty" }, "No GPU is on the record yet.")),
    section("Look up on ENS", "read live from Sepolia", h("p", { className: "sub" }, "A GPU or provider name (e.g. cloud-b), read live from Sepolia through the ENS Universal Resolver."), form, out),
  ];
}

// The ENS name tree: waterline.eth, its providers, their GPUs. Every node is a real name Marks resolves, with its own score;
// performance comes from the same waterline.pct_of_spec the chain stores, so reading the tree is comparing the clouds.
function nameTree(pv, gpus, look) {
  const groups = new Map(pv.providers.map((p) => [p.provider_node, { p, gpus: [] }]));
  for (const x of gpus) {
    if (!groups.has(x.provider_node)) groups.set(x.provider_node, { p: { provider_node: x.provider_node, name: x.gpu_name?.split(".").slice(1).join(".") }, gpus: [] });
    groups.get(x.provider_node).gpus.push(x);
  }
  const median = (xs) => { xs = xs.filter((v) => v != null).sort((a, b) => a - b); return xs.length ? xs[(xs.length - 1) >> 1] : null; };
  const people = (n) => `${n} ${n === 1 ? "person" : "people"}`;
  const name = (label, full) => full ? h("a", { className: "tn", href: `#/name/${full}`, title: `${full}: its page, read live from ENS` }, label) : h("span", { className: "tn" }, label);
  const provider = ({ p, gpus: gs }) => {
    const full = p.name || (gs[0]?.gpu_name || "").split(".").slice(1).join(".");
    const pct = median(gs.map((x) => x.pct_of_spec));
    return h("li", {},
      h("div", { className: "tnode" }, name(full || short(p.provider_node), full),
        h("span", { className: "facts" }, `${p.failed_gpus ?? 0} of ${p.gpus ?? gs.length} failed · ${people(p.humans ?? 0)}`, pct != null ? ` · median ${num(pct)}% of rating` : "")),
      h("ul", {}, gs.map((x) => h("li", {},
        h("div", { className: "tnode" }, name((x.gpu_name || short(x.node)).split(".")[0], x.gpu_name), pill(x.status),
          h("span", { className: "facts" }, cls(x.cls),
            x.listed_class && x.listed_class !== x.cls ? h("span", { className: "st-fail" }, ` listed as ${cls(x.listed_class)}`) : "",
            x.pct_of_spec != null ? ` · ${num(x.pct_of_spec)}% of rating` : "", x.humans ? ` · ${people(x.humans)}` : ""))))));
  };
  return h("div", { className: "tree" },
    h("div", { className: "tnode root" }, h("b", { className: "mono" }, "waterline.eth"), h("span", { className: "facts" }, "resolver: Marks · nothing below is registered, all of it resolves onchain")),
    h("ul", {}, [...groups.values()].map(provider)));
}

// #/name/<name>: the page of one ENS name, a GPU or a provider. The facts are read live from ENS; history and the
// subtree come from the API. The record itself stays raw onchain; this is its human view.
async function namePage(full) {
  const hl = await getHealth();
  const n = String(full || "").toLowerCase();
  if (!n.endsWith("." + hl.ens.parent)) throw new Error(`Not a name under ${hl.ens.parent}.`);
  const isProvider = n.split(".").length === 3;
  const [g, pv, reps] = await Promise.all([api("/api/gpus"), api("/api/providers").catch(() => ({ providers: [] })), api("/api/reports?limit=500")]);
  const mine = reps.filter((r) => isProvider ? r.gpu_name?.endsWith("." + n) : r.gpu_name === n);
  const keys = isProvider ? PROVIDER_KEYS : ENS_KEYS;
  const facts = h("div", { className: "namefacts" }, h("p", { className: "sub" }, "Reading the record from Sepolia…"));
  const statusBox = h("div", { className: "namestatus" });
  const slow = new Promise((_, no) => setTimeout(() => no(new Error("Sepolia didn't answer in 15 s; reload to try again")), 15000));
  Promise.race([ensReader(n).then(async ({ text }) =>
    Object.fromEntries(await Promise.all(keys.map(async (k) => [k, (await text("waterline." + k)) || ""])))), slow]).then((v) => {
    if (!v.status || v.status === "unknown") { facts.replaceChildren(h("p", { className: "sub" }, `No published check for ${n} yet.`)); return; }
    statusBox.replaceChildren(isProvider ? h("p", { className: "lede-s" }, v.status) : pill(v.status));
    const tile = (k, val) => h("div", {}, h("b", {}, val || "0"), h("span", {}, k));
    facts.replaceChildren(h("div", { className: "specline" }, ...(isProvider
      ? [tile("GPUs", v.gpus), tile("failed now", v.failed_gpus), tile("people who reported", v.humans), tile("passes", v.passes), tile("degraded", v.degraded), tile("fails", v.fails)]
      : [tile("measured as", v.class), tile("cores", v.cores), tile("% of rating", v.pct_of_spec && `${v.pct_of_spec}%`), tile("passes", v.passes), tile("degraded", v.degraded), tile("fails", v.fails), tile("people", v.humans), tile("recoveries", v.recoveries)])),
      isProvider && v.note ? h("p", { className: "quote" }, `“${v.note}” `, h("span", { className: "sub" }, "the provider's note: its words, not part of the record")) : null,
      !isProvider && v.fingerprint ? h("p", { className: "sub small" }, "Timing fingerprint ", h("span", { className: "mono" }, short(v.fingerprint)), " · report hash ", v.report ? h("a", { href: `#/r/${v.report}`, className: "mono", title: "Open the check this hash commits to" }, short(v.report)) : "—", mine[0] ? h("span", {}, " · ", h("a", { href: `#/check/${mine[0].report_id}` }, "verify it on the latest check")) : null) : null);
  }).catch((e) => facts.replaceChildren(h("p", { className: "err" }, `Couldn't read ${n} from ENS: ${e.shortMessage || e.message}`)));
  const provNode = pv.providers.find((x) => x.name === (isProvider ? n : n.split(".").slice(1).join(".")));
  return [
    head(isProvider ? "ENS name · provider" : "ENS name · GPU", h("span", { className: "mono namehead" }, n), statusBox,
      !isProvider && mine[0]?.uuid ? h("p", { className: "sub small mono", title: "NVIDIA UUID, as the host's driver reports it" }, `card ${mine[0].uuid}`) : null,
      h("p", { className: "sub" }, isProvider ? "Every GPU this provider rents out is named under it; its score is the roll-up of theirs, each person counted once."
        : h("span", {}, "Named under ", h("a", { href: `#/name/${n.split(".").slice(1).join(".")}` }, n.split(".").slice(1).join(".")), ". Resolved by Marks, written only by its reporter role."))),
    section("The record", "read live from ENS on Sepolia", facts),
    ...(isProvider ? [section("Its GPUs", "each with its own record", nameTree({ providers: provNode ? [provNode] : [] }, g.gpus.filter((x) => x.gpu_name?.endsWith("." + n)), () => {}))] : []),
    ...(!isProvider && mine.some((x) => x.series) ? [section("Periodic series", "the latest, check by check", seriesStrip(mine, mine.find((x) => x.series).series))] : []),
    section("History", `${mine.length} check${mine.length === 1 ? "" : "s"}`, mine.length ? checksTable(mine) : h("p", { className: "empty" }, "No checks through this API yet.")),
  ];
}

// Text records of one ENS name, read in the browser through the universal resolver (viem).
async function ensReader(name) {
  const hl = await getHealth();
  if (!hl.ens.universal_resolver) throw new Error("the API has no ENS universal resolver configured");
  const [{ createPublicClient, http }, { sepolia }, { normalize }] = await Promise.all([import(VIEM), import(VIEM + "/chains"), import(VIEM + "/ens")]);
  const client = createPublicClient({ chain: sepolia, transport: http(hl.ens.rpc) });
  const n = normalize(name);
  return { n, text: (key) => client.getEnsText({ name: n, key, universalResolverAddress: hl.ens.universal_resolver }) };
}

// "On chain": how it was written, whether MultiBaas saw it, and the evidence behind it (reportHash).
function onChain(r, kv) {
  const dry = r.via === "dry-run" || (r.published && !r.tx);
  const written = !r.published ? h("span", { className: "sub" }, needsApproval(r) ? "Not published: waiting for a human approval" : "Not published")
    : dry ? h("span", { className: "st-suspect" }, "Not on chain yet (dry run)") : h("span", {}, "Published via ", WRITE_PATH[r.via] || r.via || "—");
  const indexed = r.indexed ? h("span", { className: "st-pass" }, `Indexed by MultiBaas ✓ `, h("span", { className: "sub" }, when(r.indexed_at)))
    : h("span", { className: "sub" }, r.published && !dry ? "Not indexed yet (waiting for the MultiBaas webhook)" : "—");
  const out = h("p", { className: "msg", "aria-live": "polite" });
  const verify = async () => {
    if (!r.published) return out.replaceChildren("Not published yet, so there is no on-chain record to compare with.");
    if (dry) return out.replaceChildren("Not on chain yet (dry run): there is no on-chain record to compare with.");
    out.className = "msg";
    out.replaceChildren(`Reading waterline.report of ${r.gpu_name} from Sepolia…`);
    try {
      const { text } = await ensReader(r.gpu_name);
      const onchain = ((await text("waterline.report")) || "").toLowerCase();
      if (!onchain) out.replaceChildren("The name has no waterline.report yet. The transaction may still be confirming; try again shortly.");
      else if (onchain === r.report_hash.toLowerCase()) { out.className = "st-pass"; out.replaceChildren("Matches the on-chain record ✓"); }
      else { out.className = "st-suspect"; out.replaceChildren(`Doesn't match: the name now shows ${short(onchain)}, likely a newer check. This report's hash stays in its Reported event.`); }
    } catch (e) {
      out.className = "err";
      out.replaceChildren(`Couldn't read ${r.gpu_name}: ${e.shortMessage || e.message}`);
    }
  };
  const evidence = r.report_hash
    ? h("div", { className: "evidence" },
      h("span", { className: "mono hash", title: r.report_hash }, r.report_hash),
      h("div", { className: "row" },
        h("a", { className: "btn sm", href: `/api/reports/${encodeURIComponent(r.report_id)}/evidence`, download: `waterline-report-${r.report_id}.json` }, "Download evidence"),
        h("button", { type: "button", className: "btn sm primary", onclick: verify }, "Verify")), out,
      h("p", { className: "sub small" }, "keccak256 of the downloaded file is the report hash. Verify reads waterline.report from the GPU's ENS name, straight from the chain."))
    : h("span", { className: "sub" }, "This check was made before reports carried an evidence hash.");
  return section("On chain", null, kv(["Written", written], ["Transaction", txLink(r.tx, r.published)], ["MultiBaas", indexed],
    ["ENS node", h("span", { className: "mono", title: r.node }, short(r.node))], ["GPU name", r.gpu_name], ["Evidence", evidence]));
}

// Returns [form, output, run(name)]. Reads the waterline.* text records through the universal resolver.
function ensLookup(initial = "") {
  const input = h("input", { type: "text", id: "ens-name", value: initial, placeholder: "gpu-91c0ab12.cloud-b.waterline.eth", autocomplete: "off", spellcheck: false });
  const out = h("div", { "aria-live": "polite" });
  const run = async (raw) => {
    const hl = await getHealth();
    let name = raw.trim().toLowerCase();
    if (!name) return;
    if (!name.endsWith(".eth")) name += "." + hl.ens.parent;
    input.value = name;
    out.replaceChildren(h("p", { className: "msg" }, `Reading ${name} from Sepolia…`));
    try {
      const { text, n } = await ensReader(name);
      const isProvider = n.split(".").length === 3; // <cloud>.waterline.eth
      const keys = isProvider ? PROVIDER_KEYS : ENS_KEYS;
      const vals = await Promise.all(keys.map((k) => text("waterline." + k)));
      const r = Object.fromEntries(keys.map((k, i) => [k, vals[i] || ""]));
      if (!vals.some(Boolean) || r.status === "unknown") {
        out.replaceChildren(h("p", { className: "msg" }, `No published check for ${n} yet.`));
        return;
      }
      const row = (k, v) => [h("dt", {}, k), h("dd", {}, v || "—")];
      out.replaceChildren(isProvider
        ? h("div", { className: "ens" }, h("div", { className: "label" }, "ENS record · provider"), h("div", { className: "nm" }, n), h("p", {}, r.status),
          h("dl", { className: "kv" }, row("GPUs checked", r.gpus), row("Failed right now", r.failed_gpus || "0"), row("People who reported", r.humans || "0"),
            row("Passed checks", r.passes || "0"), row("Failure reports", r.fails || "0"), row("Provider's note", r.note ? `“${r.note}” (written by the provider, not part of the record)` : "")))
        : h("div", { className: "ens" }, h("div", { className: "label" }, "ENS record"), h("div", { className: "nm" }, n), pill(r.status || "unknown"),
          h("dl", { className: "kv" }, row("Measured as", r.class), row("Cores", r.cores), row("Of its rating", r.pct_of_spec ? `${r.pct_of_spec}%` : ""),
            row("Passed checks", r.passes || "0"), row("Failure reports", r.fails || "0"), row("People who reported it", r.humans || "0"),
            row("Recoveries", r.recoveries || "0"), row("Timing fingerprint", r.fingerprint),
            row("Latest check", r.report && h("a", { href: `#/r/${r.report}`, className: "mono", title: r.report }, short(r.report))))));
    } catch (e) {
      out.replaceChildren(h("p", { className: "err" }, `Couldn't read ${name}: ${e.shortMessage || e.message}`));
    }
  };
  const form = h("form", { className: "inline", onsubmit: (e) => { e.preventDefault(); run(input.value); } },
    h("div", { className: "field" }, h("label", { className: "label", htmlFor: "ens-name" }, "GPU or provider name"), input),
    h("button", { type: "submit", className: "btn primary" }, "Look up"));
  return [form, out, run];
}

// ---- checks ------------------------------------------------------------------------------------------------
// Periodic checks (--every): the same rental re-checked at jittered intervals. A tag in tables, and a timeline where a
// throttle or a swapped card shows as a break in the line.
const seriesTag = (r) => r.series ? h("span", { className: "stag", title: `Periodic series ${r.series}` }, `↻ #${r.seq}`) : null;

function seriesStrip(all, id, current) {
  const xs = all.filter((r) => r.series === id).sort((a, b) => (a.seq || 0) - (b.seq || 0));
  const top = Math.max(100, ...xs.map((r) => r.pct_of_spec || 0));
  return h("div", { className: "strip", role: "list", "aria-label": `Periodic series ${id}` }, xs.map((r) =>
    h("a", { role: "listitem", href: `#/check/${r.report_id}`, className: "tick st-" + r.verdict + (r.report_id === current ? " here" : ""),
      title: `#${r.seq} · ${r.verdict} · ${when(r.created_at)}${r.pct_of_spec != null ? ` · ${num(r.pct_of_spec)}% of rating` : ""}` },
      h("i", { style: `height:${r.pct_of_spec ? Math.max(8, (r.pct_of_spec / top) * 100) : 100}%` }),
      h("span", {}, `#${r.seq}`))));
}

// #/r/<hash>: open a check from the hash in its GPU's waterline.report record (or a Reported event's reportHash)
async function byHash(hash) {
  const r = await api(`/api/reports/by-hash/${encodeURIComponent(hash)}`);
  history.replaceState(null, "", `#/check/${r.report_id}`);
  return checkDetail(r.report_id);
}

function checksTable(reps) {
  return table(["When", "GPU", "Listed as", "Measured as", "Verdict", "Tx", "Indexed", ""], reps.map((r) => h("tr", {},
    h("td", {}, when(r.created_at)),
    h("td", {}, h("a", { href: `#/check/${r.report_id}`, className: "mono", title: "Open this check" }, r.gpu_name || r.report_id)),
    h("td", {}, cls(r.claimed_class)), h("td", {}, cls(r.measured_class)), h("td", {}, verdictPill(r), seriesTag(r)),
    h("td", {}, txLink(r.tx, r.published)),
    h("td", {}, r.indexed ? h("span", { className: "st-pass", title: `Indexed by MultiBaas ${when(r.indexed_at)}` }, "✓") : h("span", { className: "sub", title: "Not indexed by MultiBaas yet" }, "—")),
    h("td", {}, needsApproval(r) ? h("button", { type: "button", className: "btn sm world", title: "Approve with World", onclick: () => approveFlow(r) }, "Approve") : null))));
}

async function checks() {
  const reps = await api("/api/reports?limit=100");
  const pending = reps.filter(needsApproval).length;
  return [
    head("Checks", "Recent checks", h("p", { className: "sub" }, "Passes and degraded results publish at once. Failures wait here for a World approval; deny or ignore, and nothing is published.")),
    section("All checks", pending ? `${pending} waiting for approval` : `${reps.length} shown`,
      reps.length ? checksTable(reps) : h("p", { className: "empty" }, "No checks yet."),
      h("div", {}, h("button", { type: "button", className: "btn", onclick: () => route(false) }, "Refresh"))),
  ];
}

// Numbers: 3 significant digits with an SI prefix ("8.8 T"), or plain with separators.
const si = (x, d = 3) => { const u = ["", " K", " M", " G", " T", " P"]; let i = 0; while (x >= 1000 && i < u.length - 1) { x /= 1000; i++; } return `${+x.toPrecision(d)}${u[i]}`; };
const num = (x) => (x == null ? "—" : x >= 100 ? Math.round(x).toLocaleString() : x >= 1 ? x.toFixed(1) : String(+x.toPrecision(2)));
const gauge = (label, value, pct, tone, aria) =>
  h("div", { className: "gauge" }, h("div", { className: "gauge-hd" }, h("span", { className: "label" }, label), h("b", { className: tone || "" }, value)),
    h("div", { className: "bar " + (tone === "st-fail" ? "over" : ""), role: "img", "aria-label": aria }, h("i", { style: `width:${Math.max(0.5, Math.min(100, pct || 0))}%` })));

async function checkDetail(id) {
  const [r, cmp, recent] = await Promise.all([api(`/api/reports/${encodeURIComponent(id)}`),
    api(`/api/compare/${encodeURIComponent(id)}`).catch(() => null), // the check still shows if comparing fails
    api("/api/reports?limit=500").catch(() => [])]);
  const seriesOf = recent;
  const p = r.probes || {};
  const kv = (...pairs) => h("dl", { className: "kv" }, pairs.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v ?? "—")]));
  const [form, out] = ensLookup(r.gpu_name);
  const over = r.elapsed_s > r.deadline_s;
  const spec = (k, v) => h("div", {}, h("b", {}, v), h("span", {}, k));
  const listed = cls(r.claimed_class);
  const pct = r.pct_of_spec;
  // the three cases renters meet: the listed chip at speed, the listed chip delivering too little, another chip
  const rating = pct == null ? "" : `${num(pct)}% of its rating`;
  const diagnosis = r.claimed_class !== r.measured_class ? `Different chip: listed as ${listed}, measures as ${cls(r.measured_class)}.`
    : over ? `Right chip, delivering too little: it missed the deadline${rating ? ` at ${rating}` : ""}. ${slowCause(r.health) ? `The machine reports ${slowCause(r.health)} (reported, not verified).` : "The machine reports no cause; sharing or a power cap are common ones."}`
    : r.verdict === "pass" ? `Right chip, done in time${rating ? `, delivering ${rating}` : ""}.`
    : "Right chip, but its answer didn't check out.";
  return [
    head("Check " + short(r.report_id), r.gpu_name || "Unknown GPU",
      h("div", { className: "verdict st-" + r.verdict }, r.verdict),
      h("p", { className: "diagnosis" }, diagnosis),
      r.fingerprint_changed ? h("p", { className: "sub" }, "Its timing fingerprint changed since the last check. Noted, not judged: it can mean a different card behind the name.") : null,
      h("p", {}, verdictPill(r), " ", h("span", { className: "sub" }, r.status_text || "")),
      needsApproval(r) ? h("div", {}, h("button", { type: "button", className: "btn world", onclick: () => approveFlow(r) }, "Approve with World")) : null),
    h("section", { className: "test" },
      h("p", { className: "principle" }, "The test: sealed work under a deadline, a random slice re-graded, and a core count."),
      h("div", { className: "specline" },
        spec("Matrix", r.n ? `${r.n.toLocaleString()} × ${r.n.toLocaleString()} INT8` : "—"),
        spec("Steps", String(r.steps ?? "—")),
        spec("Total operations", r.n ? `${si(2 * r.n ** 3, 2)} ops × ${r.steps}` : "—"),
        spec("Time / deadline", h("span", {}, h("span", { className: over ? "st-fail" : "st-pass" }, `${r.elapsed_s ?? "—"} s`), ` / ${r.deadline_s ?? "—"} s`)),
        spec("Effective", r.effective_tops == null ? "—" : `${num(r.effective_tops)} TOPS`),
        spec("Rows re-checked", `${r.samples?.length ?? 0} × 64 entries`)),
      h("div", { className: "gauges" },
        gauge("Time used of the deadline", `${r.elapsed_s ?? "—"} s of ${r.deadline_s ?? "—"} s`, r.deadline_s ? (r.elapsed_s / r.deadline_s) * 100 : 0,
          over ? "st-fail" : "st-pass", `${r.elapsed_s} of ${r.deadline_s} seconds`),
        r.spec_tops ? gauge(`Of the ${listed}'s rating (${r.spec_tops.toLocaleString()} TOPS dense INT8)`, pct == null ? "—" : `${num(pct)}%`, pct, "",
          `${num(r.effective_tops)} TOPS is ${num(pct)} percent of ${r.spec_tops} TOPS`) : null),
      h("p", { className: "sub small" }, "Effective TOPS includes generation, hashing and network, so it reads below peak.")),
    h("div", { className: "detail" },
      h("div", { className: "block" }, h("div", { className: "label" }, "Why"),
        r.reasons?.length ? h("ul", { className: "reasons" }, r.reasons.map((x) => h("li", {}, x))) : h("p", {}, "All checks passed: done in time, and the hardware matches the listing."),
        kv(["Listed as", listed], ["Measured as", h("span", { className: r.claimed_class === r.measured_class ? "st-pass" : "st-fail" }, cls(r.measured_class))], ["Cloud", r.cloud], ["When", when(r.created_at)],
          ...(r.uuid ? [["Card", h("span", { className: "mono", title: "NVIDIA UUID, as the host's driver reports it; the GPU's name is its first 8 hex digits" }, r.uuid)]] : []),
          ...(r.listing ? [["Listing (reporter's words)", h("span", { className: "quote" }, r.listing)]] : []),
          ...(r.listing_reads_as?.class ? [["Listing reads as", h("span", { className: r.listing_reads_as.contradicts ? "st-fail" : "st-pass" },
            `${cls(r.listing_reads_as.class)} (${r.listing_reads_as.source}) · ${r.listing_reads_as.contradicts ? "contradicts the claim; reported anyway" : "matches the claim"}`)]] : []),
          ...(r.provider_voter ? [["Reported by", h("span", {}, h("span", { className: "mono", title: "pseudonymous: the same person gets the same id for this provider, never a name" }, "person " + short("0x" + r.provider_voter)),
            ` · ${r.reporter_reports} report${r.reporter_reports === 1 ? "" : "s"} on ${r.cloud}`, r.approved_via === "mandate" ? h("span", { className: "world-c", title: `mandate ${r.mandate_id}, valid until ${when(r.mandate_expires_at)}` }, ` · reported by their agent under a World mandate ${when(r.approved_at)}`)
              : r.approved_at ? ` · approved with World ${when(r.approved_at)}` : "")]] : []))),
      h("div", { className: "block" }, h("div", { className: "label" }, "Probes"),
        kv(["Cores (SMs)", String(p.sms ?? "—")], ["FP8 maths", p.fp8 == null ? "—" : p.fp8 ? "yes (Hopper)" : "no"],
          ["Clock", p.clock_ghz ? `${p.clock_ghz} GHz` : "—"], ["Copy bandwidth", p.bw_tbs ? `${p.bw_tbs} TB/s` : "—"],
          ["Per-core fingerprint", h("span", { title: p.fingerprint }, short(p.fingerprint))])),
      h("div", { className: "block" }, h("div", { className: "label" }, "Rows re-checked (step, row)"),
        r.samples?.length ? h("ul", { className: "samples" }, r.samples.map(([st, row]) => h("li", {}, `${st}, ${row}`))) : h("p", { className: "sub" }, "—"),
        h("p", { className: "sub small" }, "Drawn with the API's secret randomness after the answer was sealed; 64 entries per row recomputed on the API's CPU."))),
    section("Core-count staircase", r.staircase ? `step at ${p.sms} blocks` : null,
      h("p", { className: "sub" }, "One busy block per core: past the core count, blocks queue and the time jumps. Heat slows a chip; it can't move this step."),
      r.staircase ? staircase(r.staircase, p.sms) : h("p", { className: "empty" }, "The profiler did not send staircase timings for this check.")),
    ...(r.series ? [section("Periodic series", `↻ check #${r.seq} of series ${r.series}`,
      h("p", { className: "sub" }, "The same rental, re-checked at jittered intervals so the host can't time it. Each bar is one check: its height is the share of the rating it delivered, its colour the verdict."),
      seriesStrip(seriesOf, r.series, r.report_id))] : []),
    perfSection(r, cmp),
    healthSection(r.health),
    onChain(r, kv),
    section("Live ENS record", null, form, out),
  ];
}

// ---- health report (advisory) -------------------------------------------------------------------------------
const RED_REASONS = ["HW slowdown", "HW thermal", "HW power brake"], AMBER_REASONS = ["SW thermal", "power cap"];

// Plain-words problems, worst first: [level, text], level "bad" | "warn".
function healthFlags(hr) {
  const f = [], d = hr.device || {}, m = hr.memory || {}, b = hr.burn || {}, pc = d.pcie || {};
  const add = (level, text) => f.push([level, text]);
  if (pc.gen && pc.max_gen && pc.width && pc.max_width && (pc.gen < pc.max_gen || pc.width < pc.max_width))
    add("warn", `PCIe running at Gen${pc.gen} x${pc.width}; the card supports Gen${pc.max_gen} x${pc.max_width}.`);
  if (d.nvlink?.down) add("warn", `${d.nvlink.down} of ${d.nvlink.up + d.nvlink.down} NVLink links are down.`);
  if (d.mig === "enabled") add("warn", "MIG is on: the card is split into slices, so you may have only part of it.");
  if (d.ecc && !d.ecc.enabled) add("bad", "ECC is off: memory errors can go unnoticed.");
  if (d.ecc && d.ecc.pending !== d.ecc.enabled) add("warn", "The ECC setting changes at the next reboot.");
  const e = m.ecc_errors || {};
  const unc = Math.max(e.volatile?.uncorrected || 0, e.aggregate?.uncorrected || 0);
  if (unc) add("bad", `${unc} uncorrectable memory error${unc > 1 ? "s" : ""} on record.`);
  else if (e.aggregate?.corrected) add("warn", `${e.aggregate.corrected} corrected memory error${e.aggregate.corrected > 1 ? "s" : ""} over the card's life.`);
  const rr = m.remapped_rows;
  if (rr?.failure) add("bad", "Memory row remapping has failed: the card needs service.");
  else if (rr?.pending) add("warn", "Remapped memory rows are waiting for a GPU reset.");
  const rp = m.retired_pages;
  if (rp?.double_bit) add("bad", `${rp.double_bit} memory page${rp.double_bit > 1 ? "s" : ""} retired after double-bit errors.`);
  if (rp?.pending) add("warn", "Retired memory pages are waiting for a GPU reset.");
  const seen = b.reasons_seen || [], hw = seen.filter((x) => RED_REASONS.includes(x)), sw = seen.filter((x) => AMBER_REASONS.includes(x));
  if (hw.length) add("bad", `The hardware slowed itself down during the burn (${hw.join(", ")}).`);
  if (sw.length) add("warn", `Clocks were held back during the burn by: ${sw.join(", ")}.`);
  const t = b.tflops, ps = b.per_second || [];
  if (t && t.mean && t.std / t.mean > 0.05) add("warn", `Throughput swung by ±${Math.round((100 * t.std) / t.mean)}% during the burn.`);
  if (ps.length > 3 && ps.at(-1) < 0.9 * ps[0]) add("warn", `Throughput sagged from ${num(ps[0])} to ${num(ps.at(-1))} TFLOPS during the burn.`);
  if (hr.dcgm?.passed === false) add("bad", `NVIDIA's DCGM diagnostic failed: ${hr.dcgm.tests.filter((x) => x.result === "fail").map((x) => x.name).join(", ") || "see its output"}.`);
  (hr.notes || []).forEach((n) => add("warn", `The profiler noted: ${n}.`));
  return f.sort((a, b2) => (a[0] === b2[0] ? 0 : a[0] === "bad" ? -1 : 1));
}

function sparkline(ys) {
  if (!ys?.length) return null;
  const W = 240, H = 48, lo = Math.min(...ys), hi = Math.max(...ys), pad = (hi - lo) * 0.2 || 1;
  const X = (i) => (ys.length > 1 ? (i / (ys.length - 1)) * (W - 4) + 2 : W / 2), Y = (v) => H - 4 - ((v - lo + pad) / (hi - lo + 2 * pad)) * (H - 8);
  const mean = ys.reduce((a, v) => a + v, 0) / ys.length;
  const svg = S("svg", { viewBox: `0 0 ${W} ${H}`, class: "spark", role: "img", "aria-label": `TFLOPS each second: ${ys.map(num).join(", ")}` });
  svg.append(S("line", { x1: 0, x2: W, y1: Y(mean), y2: Y(mean), class: "mean" }),
    S("polyline", { points: ys.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" "), class: "curve" }),
    ...ys.map((v, i) => S("circle", { cx: X(i), cy: Y(v), r: 1.8 })));
  return svg;
}

function healthSection(hr) {
  const note = "Reported by the machine · advisory, not part of the verdict";
  const wrap = (...kids) => h("section", { className: "advisory", "aria-labelledby": "health-h" },
    h("div", { className: "sechead" }, h("h3", { id: "health-h" }, "Health report"), h("span", { className: "count warnc" }, note)), ...kids);
  if (!hr) return wrap(h("p", { className: "empty" }, "The profiler did not send a health report for this check."));
  const d = hr.device || {}, m = hr.memory || {}, b = hr.burn || {}, t = b.tflops, e = m.ecc_errors || {};
  const kv = (...pairs) => h("dl", { className: "kv" }, pairs.filter(Boolean).flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v ?? "—")]));
  const blk = (label, ...kids) => h("div", { className: "block" }, h("div", { className: "label" }, label), ...kids);
  const val = (x, unit = "") => (x == null ? "—" : `${x}${unit}`);
  const tone = (bad, warn) => (bad ? "st-fail" : warn ? "st-suspect" : "st-pass");
  const errs = (x) => (x ? h("span", { className: tone(x.uncorrected, x.corrected) }, `${val(x.corrected)} corrected · ${val(x.uncorrected)} uncorrected`) : "—");
  const pc = d.pcie || {}, down = pc.gen && pc.max_gen && (pc.gen < pc.max_gen || pc.width < pc.max_width);
  const chips = (b.reasons_seen || []).map((x) => h("span", { className: "pill " + (RED_REASONS.includes(x) ? "st-fail" : AMBER_REASONS.includes(x) ? "st-suspect" : "st-unknown") }, x));
  const flags = healthFlags(hr);
  return wrap(
    h("p", { className: "sub" }, "Standard checks: a sustained burn, NVIDIA's counters (NVML) and DCGM when installed. The host's machine reports them, so they inform but never decide.",
      hr.source === "simulated" ? h("span", {}, " ", pill("simulated · CPU test run", "suspect")) : null),
    h("ul", { className: "flags" }, flags.length ? flags.map(([lv, x]) => h("li", { className: lv === "bad" ? "st-fail" : "st-suspect" }, x))
      : h("li", { className: "st-pass" }, "Nothing unusual reported.")),
    h("div", { className: "detail" },
      blk("Sustained throughput",
        t ? h("p", { className: "big" }, `${num(t.mean)} ± ${num(t.std)}`, h("small", {}, " TFLOPS")) : h("p", { className: "sub" }, "No burn was run."),
        t ? h("p", { className: "sub small" }, `min ${num(t.min)} · max ${num(t.max)} · BF16 ${b.n}×${b.n} matmul for ${b.seconds} s`) : null,
        sparkline(b.per_second)),
      blk("Clocks, heat and power",
        h("div", { className: "chips" }, chips.length ? chips : h("span", { className: "pill st-pass" }, "no throttling seen")),
        kv(["Max temperature", val(b.max_temp_c, " °C")], ["Power", b.max_power_w == null ? "—" : `${num(b.max_power_w)} W of ${val(b.power_limit_w, " W")} limit`],
          ["SM clock", b.min_sm_mhz == null ? "—" : `${b.min_sm_mhz} MHz lowest · ${val(b.max_sm_mhz, " MHz")} max`],
          ["Memory clock", val(b.mem_mhz, " MHz")], ["Utilisation", val(b.util_gpu, "%")])),
      blk("Memory",
        kv(["ECC", d.ecc ? h("span", { className: tone(!d.ecc.enabled, d.ecc.pending !== d.ecc.enabled) }, d.ecc.enabled ? "on" : "off") : "—"],
          ["Errors since boot", errs(e.volatile)], ["Errors, lifetime", errs(e.aggregate)],
          ["Remapped rows", m.remapped_rows ? h("span", { className: tone(m.remapped_rows.failure, m.remapped_rows.pending) },
            `${m.remapped_rows.corrected + m.remapped_rows.uncorrected}${m.remapped_rows.pending ? " · reset pending" : ""}${m.remapped_rows.failure ? " · FAILED" : ""}`) : "—"],
          ["Retired pages", m.retired_pages ? `${m.retired_pages.single_bit + m.retired_pages.double_bit}${m.retired_pages.pending ? " · reset pending" : ""}` : "—"])),
      blk("Links and slicing",
        kv(["PCIe", pc.gen ? h("span", { className: down ? "st-suspect" : "" }, `Gen${pc.gen} x${pc.width ?? "?"}`, h("span", { className: "sub" }, ` of Gen${pc.max_gen ?? "?"} x${pc.max_width ?? "?"}`)) : "—"],
          ["NVLink", d.nvlink ? h("span", { className: d.nvlink.down ? "st-suspect" : "" }, `${d.nvlink.up} up · ${d.nvlink.down} down`) : "none"],
          ["MIG", d.mig ? h("span", { className: d.mig === "enabled" ? "st-suspect" : "" }, d.mig) : "—"])),
      blk("Software and identity",
        kv(["Reports itself as", d.name], ["Driver", d.driver], ["CUDA", d.cuda], ["VBIOS", d.vbios], ["Memory", val(d.memory_gib, " GiB")],
          ["UUID", d.uuid ? h("span", { className: "mono", title: d.uuid }, short(d.uuid)) : "—"])),
      blk("DCGM diagnostic",
        !hr.dcgm?.available ? h("p", { className: "sub" }, "Not available in this pod.")
          : hr.dcgm.tests?.length ? h("ul", { className: "samples" }, hr.dcgm.tests.map((x) => h("li", { className: x.result === "fail" ? "st-fail" : x.result === "pass" ? "st-pass" : "st-unknown" }, `${x.name} · ${x.result}`)))
            : h("p", { className: "sub" }, hr.dcgm.note || "No results."))));
}

// Inline SVG, drawn to scale: blocks launched (x) against kernel time in ms (y).
function staircase(st, sms) {
  const pts = Object.entries(st).map(([k, v]) => [+k, +v]).sort((a, b) => a[0] - b[0]);
  if (pts.length < 2) return h("p", { className: "empty" }, "Not enough staircase points to draw.");
  const W = 720, H = 300, L = 48, R = 12, T = 44, B = 40;
  const x0 = pts[0][0], x1 = pts.at(-1)[0], ymax = Math.max(...pts.map((q) => q[1])) * 1.1 || 1;
  const X = (k) => L + ((k - x0) / (x1 - x0)) * (W - L - R), Y = (ms) => H - B - (ms / ymax) * (H - T - B);
  const base = Math.min(...pts.slice(0, 8).map((q) => q[1]));
  const jump = pts.find((q) => q[1] > 1.5 * base);
  const step = sms || (jump ? jump[0] - 1 : null);
  const svg = S("svg", { viewBox: `0 0 ${W} ${H}`, class: "stair", role: "img",
    "aria-label": `Kernel time against blocks launched, ${x0} to ${x1}. ${step ? `The time jumps after ${step} blocks, so this GPU has ${step} cores.` : "No step found."}` });
  for (let i = 0; i <= 4; i++) {
    const ms = (ymax * i) / 4;
    svg.append(S("line", { x1: L, x2: W - R, y1: Y(ms), y2: Y(ms), class: "grid" }),
      S("text", { x: L - 6, y: Y(ms) + 4, "text-anchor": "end", class: "ax" }, ms.toFixed(ms < 10 ? 1 : 0)));
  }
  REFS.forEach(([k, name], i) => {
    if (k < x0 || k > x1) return;
    svg.append(S("line", { x1: X(k + 0.5), x2: X(k + 0.5), y1: T - 4, y2: H - B, class: "ref" }),
      S("text", { x: X(k + 0.5), y: T - 10 - (i % 2) * 14, "text-anchor": "middle", class: "ax" }, `${k} ${name}`));
  });
  for (let k = Math.ceil(x0 / 16) * 16; k <= x1; k += 16) svg.append(S("text", { x: X(k), y: H - B + 16, "text-anchor": "middle", class: "ax" }, k));
  svg.append(S("text", { x: (L + W - R) / 2, y: H - 4, "text-anchor": "middle", class: "ax" }, "blocks launched"),
    S("text", { x: 4, y: 12, class: "ax" }, "ms"));
  svg.append(S("polyline", { points: pts.map(([k, ms]) => `${X(k).toFixed(1)},${Y(ms).toFixed(1)}`).join(" "), class: "curve" }));
  if (step >= x0 && step <= x1) {
    const sx = X(step + 0.5);
    svg.append(S("line", { x1: sx, x2: sx, y1: T - 4, y2: H - B, class: "step" }),
      S("text", { x: sx - 6, y: H - B - 8, "text-anchor": "end", fill: "var(--pod)", "font-size": "12" }, `step: ${step} cores`));
  }
  return h("div", { className: "panel" }, svg);
}

// ---- performance profile -------------------------------------------------------------------------------------
// Keys, units, trust and methods follow docs/INTERFACES.md ("Performance profile"). Every number shown carries its
// unit, how many runs it came from, its spread, and a trust label.
const METRICS = {
  int8_tops_verified: ["INT8 work, verified", "ops the API asked for ÷ seconds on the API's clock, start to commit. Includes generation, hashing and network, so it is a lower bound."],
  int8_tops: ["INT8 matmul", "torch._int_mm, n = 8192, CUDA events, 5 warm-up runs, median of 20."],
  bf16_tflops: ["BF16 matmul", "torch.matmul in BF16, n = 8192, same timing."],
  fp8_tflops: ["FP8 matmul", "torch._scaled_mm in e4m3, same timing. Empty when the chip has no FP8."],
  hbm_copy_tbs: ["Memory copy", "device-to-device copy over ≥ 4 GiB (far larger than L2), L2 flushed between runs."],
  hbm_read_tbs: ["Memory read", "read-and-reduce over ≥ 4 GiB, same care with L2."],
  h2d_gbs: ["Host to GPU", "pinned host memory, 1 GiB transfers."],
  d2h_gbs: ["GPU to host", "pinned host memory, 1 GiB transfers."],
  launch_us: ["Kernel launch", "empty-kernel round trip, median of 1000."],
  mem_alloc_gib: ["Usable memory", "largest block actually written and read back: catches 40 GB sold as 80 GB."],
  sm_count: ["SMs", "timing staircase, one busy block per SM; independent of what the driver reports."],
  stability_cv: ["Stability", "variation of per-second BF16 throughput over the burn."],
};
const UNITS = { int8_tops_verified: "TOPS", int8_tops: "TOPS", bf16_tflops: "TFLOPS", fp8_tflops: "TFLOPS", hbm_copy_tbs: "TB/s",
  hbm_read_tbs: "TB/s", h2d_gbs: "GB/s", d2h_gbs: "GB/s", launch_us: "µs", mem_alloc_gib: "GiB", sm_count: "SMs", stability_cv: "%" };
const TRUST = {
  verified: "Verified: from work the API re-graded and timed on its own clock. The host can't inflate it.",
  measured: "Measured: timed by our code inside the pod. A rigged driver could, in principle, lie.",
  reported: "Reported: read from the driver. The host can fake it.",
};
const mname = (k) => METRICS[k]?.[0] || k;
const HEADLINE = ["int8_tops_verified", "int8_tops", "bf16_tflops", "fp8_tflops", "hbm_copy_tbs", "hbm_read_tbs"]; // throughput: higher is faster
// 3 significant digits, thousands separators above 1000; tiny CPU-run numbers stay readable
const fmt = (x) => (x == null || !isFinite(x) ? "—" : Math.abs(x) >= 1000 ? Math.round(x).toLocaleString() : String(+(+x).toPrecision(3)));
const trustChip = (t) => h("span", { className: "pill trust trust-" + t, title: TRUST[t] || "" }, t || "unknown");
const runs = (n) => `n = ${n ?? "?"}${n === 1 ? " run" : " runs"}`;

function flagText(k, x) {
  const ex = x.expected_pct, typ = ex ? `; typical is ${ex[0] === ex[1] ? ex[0] : `${ex[0]}–${ex[1]}`}%` : "";
  if (x.flag === "unstable") return k === "stability_cv" // cv is in percent (docs/METRICS.md)
    ? `Throughput swung by ${fmt(x.value)}% over the burn; a steady card stays under 3%.`
    : `${mname(k)} varied by ±${fmt(x.cv)}% between runs; a steady card stays within ±5%.`;
  if (x.flag === "low") return `${mname(k)} at ${fmt(x.pct_of_spec)}% of rated${typ}.`;
  if (x.flag === "high") return `${mname(k)} at ${fmt(x.pct_of_spec)}% of rated${typ}. The chip may be a bigger model than listed.`;
  return null;
}

// p10–p90 as a small bar on a ±15% window around the median (wider if the spread is wider). Text carries the numbers.
function rangeBar(x) {
  const m = x.median, lo = x.p10 ?? m, hi = x.p90 ?? m;
  if (m == null || !isFinite(m) || m === 0) return null;
  const a = Math.min(lo, m * 0.85), b = Math.max(hi, m * 1.15), P = (v) => (100 * (v - a)) / (b - a);
  return h("div", { className: "rbar", "aria-hidden": "true" },
    h("i", { style: `left:${P(lo)}%; width:${Math.max(1, P(hi) - P(lo))}%` }), h("b", { style: `left:${P(m)}%` }));
}

// % of the rating on 0–120%, the expected band shaded, a tick at 100%.
function pctBar(x) {
  const p = x.pct_of_spec, ex = x.expected_pct, S = (v) => Math.max(0, Math.min(100, (v / 120) * 100));
  if (p == null) return h("span", { className: "sub" }, x.spec == null ? "no rating" : "—");
  const tone = x.flag === "low" || x.flag === "high" ? " warn" : "";
  return h("div", { className: "pct" },
    h("div", { className: "pbar" + tone, role: "img", "aria-label": `${fmt(p)}% of the ${fmt(x.spec)} ${x.unit} rating${ex ? `; typical ${ex[0]} to ${ex[1]}%` : ""}` },
      ex ? h("span", { className: "band", style: `left:${S(ex[0])}%; width:${Math.max(0.8, S(ex[1]) - S(ex[0]))}%` }) : null,
      h("i", { style: `width:${Math.max(0.6, S(p))}%` }), h("em", { style: `left:${S(100)}%` })),
    h("span", { className: "pctv" }, `${fmt(p)}%`, h("small", {}, ` of ${fmt(x.spec)}`)));
}

function metricTable(mt) {
  const keys = [...Object.keys(METRICS).filter((k) => k in mt), ...Object.keys(mt).filter((k) => !(k in METRICS))];
  return table(["Metric", "Median", "Unit", "Spread (p10–p90)", "% of rating", "Trust", "Note"], keys.map((k) => {
    const x = mt[k], note = flagText(k, x);
    return h("tr", { className: x.flag ? "flagged" : "" },
      h("td", { title: METRICS[k]?.[1] || x.method || "" }, mname(k)),
      h("td", { className: "numc" }, x.value == null && x.median == null ? (x.supported === false ? "not supported" : "—") : fmt(x.median ?? x.value)),
      h("td", { className: "sub" }, x.unit || UNITS[k] || ""),
      h("td", {}, h("div", { className: "spread" }, rangeBar(x),
        h("small", {}, x.n > 1 ? `${fmt(x.p10)}–${fmt(x.p90)} · ${runs(x.n)}` : runs(x.n)))),
      h("td", {}, pctBar(x)),
      h("td", {}, trustChip(x.trust)),
      h("td", { className: "note" + (x.flag ? " warnc" : " sub") }, note || (x.flag ? x.flag : x.expected_pct && x.pct_of_spec != null ? "in the typical range" : "")));
  }));
}

const methodsNote = () => h("details", { className: "methods" }, h("summary", {}, "How each number is measured"),
  h("dl", { className: "kv" }, Object.entries(METRICS).flatMap(([k, [name, how]]) => [h("dt", {}, `${name} (${UNITS[k]})`), h("dd", {}, how)])),
  h("p", { className: "sub small" }, "Trust labels: ", trustChip("verified"), " the API re-graded the work and timed it itself · ", trustChip("measured"),
    " timed by our code in the pod · ", trustChip("reported"), " read from the driver."));

// Rated dense INT8 (x) against memory bandwidth (y), both log scales, for every reference model with both figures.
function scatter(models, me, claimed, best) {
  const pts = models.filter((m) => m.int8_tops && m.bw_tbs);
  const phone = matchMedia("(max-width:640px)").matches; // narrower canvas so the labels stay legible
  const W = phone ? 420 : 720, H = phone ? 340 : 400, L = 44, R = 12, T = 18, B = 46, X0 = 100, X1 = 6000, Y0 = 0.25, Y1 = 10;
  const X = (v) => L + (Math.log(v / X0) / Math.log(X1 / X0)) * (W - L - R), Y = (v) => H - B - (Math.log(v / Y0) / Math.log(Y1 / Y0)) * (H - T - B);
  const cm = models.find((m) => m.id === claimed), bm = models.find((m) => m.id === best);
  const aria = `Rated INT8 TOPS against memory bandwidth for ${pts.length} GPU models, log scales.` +
    (me ? ` This GPU measured ${fmt(me.x)} TOPS${me.verified ? " (verified lower bound)" : ""} and ${fmt(me.y)} TB/s.` : "") +
    (cm ? ` Its listing, ${cm.name}, is rated ${fmt(cm.int8_tops)} TOPS and ${fmt(cm.bw_tbs)} TB/s.` : "");
  const svg = S("svg", { viewBox: `0 0 ${W} ${H}`, class: "scatter", role: "img", "aria-label": aria });
  for (const v of [100, 200, 500, 1000, 2000, 5000])
    svg.append(S("line", { x1: X(v), x2: X(v), y1: T, y2: H - B, class: "grid" }), S("text", { x: X(v), y: H - B + 16, "text-anchor": "middle", class: "ax" }, v.toLocaleString()));
  for (const v of [0.25, 0.5, 1, 2, 4, 8])
    svg.append(S("line", { x1: L, x2: W - R, y1: Y(v), y2: Y(v), class: "grid" }), S("text", { x: L - 6, y: Y(v) + 4, "text-anchor": "end", class: "ax" }, v));
  svg.append(S("text", { x: (L + W - R) / 2, y: H - 6, "text-anchor": "middle", class: "ax" }, "rated dense INT8, TOPS (log)"),
    S("text", { x: 4, y: 12, class: "ax" }, "TB/s (log)"));
  const LABEL = new Set([claimed, best, "a100-sxm-80", "b200", "l4", ...(phone ? [] : ["h100-sxm", "mi300x", "rtx-4090", "l40s"])]);
  // Labels are placed after all marks, most important first, each at the first nearby spot that doesn't overlap
  // an earlier label or leave the plot. ponytail: greedy placement, fine for ~10 labels.
  const reqs = [], boxes = [];
  const label = (x, y, text, cls, rank) => reqs.push({ x, y, text, cls, rank });
  const place = ({ x, y, text, cls }) => {
    const w = text.length * (phone ? 6.2 : 7.4) + 4, hgt = 15;
    const spots = [[9, 4, "start"], [9, -13, "start"], [9, 21, "start"], [-9, 4, "end"], [-9, -13, "end"], [-9, 21, "end"], [9, -30, "start"], [-9, 38, "end"]];
    for (const [dx, dy, anchor] of spots) {
      const x0 = anchor === "start" ? x + dx : x + dx - w, y0 = y + dy - 11;
      if (x0 < L || x0 + w > W - R + 20 || y0 < T - 10 || y0 + hgt > H - B) continue;
      if (boxes.some(b => x0 < b.x + b.w && b.x < x0 + w && y0 < b.y + b.h && b.y < y0 + hgt)) continue;
      boxes.push({ x: x0, y: y0, w, h: hgt });
      svg.append(S("text", { x: x + dx, y: y + dy, "text-anchor": anchor, class: cls }, text));
      return;
    }
  };
  for (const m of pts) {
    const x = X(m.int8_tops), y = Y(m.bw_tbs);
    svg.append(S("circle", { cx: x, cy: y, r: 3.5, class: m.id === best ? "pt best" : "pt" }, null));
    if (LABEL.has(m.id) && m.id !== claimed) label(x, y, m.name.replace(/ \(.*\)$/, ""), "lbl", m.id === best ? 2 : 3);
  }
  if (cm?.int8_tops) {
    const x = X(cm.int8_tops), y = Y(cm.bw_tbs);
    svg.append(S("circle", { cx: x, cy: y, r: 9, class: "ring" }));
    label(x + 4, y, `listed: ${cm.name}`, "lbl claimed", 1);
  }
  if (me) {
    const off = me.x < X0 || me.x > X1 || me.y < Y0 || me.y > Y1;
    const x = X(Math.min(X1, Math.max(X0, me.x))), y = Y(Math.min(Y1, Math.max(Y0, me.y)));
    svg.append(S("rect", { x: x - 5, y: y - 5, width: 10, height: 10, class: "me", transform: `rotate(45 ${x} ${y})` }));
    label(x, y, off ? `this GPU: ${fmt(me.x)} TOPS, off the scale` : "this GPU (measured)", "lbl me-l", 0);
  }
  reqs.sort((a, b) => a.rank - b.rank).forEach(place);
  return h("div", { className: "panel" }, svg,
    h("div", { className: "legend" }, h("span", {}, h("i", { className: "sw-me" }), "this GPU, measured"), h("span", {}, h("i", { className: "sw-ring" }), "listed model, rated"),
      h("span", {}, h("i", { className: "sw-pt" }), "reference model, rated"), bm && best !== claimed ? h("span", {}, h("i", { className: "sw-best" }), "closest match") : null),
    h("p", { className: "sub small" }, `${pts.length} of ${models.length} reference models have both an INT8 rating and a bandwidth figure. Ratings are vendor peak figures; a healthy card measures somewhat below them.`));
}

// One dot per check of this model; this check drawn larger. Linear scale over the cohort's own range.
function strip(vals, mine, unit) {
  const W = 320, H = 30, lo = Math.min(...vals, mine), hi = Math.max(...vals, mine), X = (v) => 8 + (hi > lo ? ((v - lo) / (hi - lo)) * (W - 16) : (W - 16) / 2);
  const svg = S("svg", { viewBox: `0 0 ${W} ${H}`, class: "strip", "aria-hidden": "true" });
  svg.append(S("line", { x1: 8, x2: W - 8, y1: H / 2, y2: H / 2, class: "grid" }), ...vals.map((v) => S("circle", { cx: X(v), cy: H / 2, r: 3, class: "pt" })),
    S("circle", { cx: X(mine), cy: H / 2, r: 6, class: "mine" }));
  return h("div", { className: "stripw" }, svg, h("small", { className: "sub" }, `${fmt(lo)} – ${fmt(hi)} ${unit}`));
}

function perfSection(r, cmp) {
  const wrap = (...kids) => h("section", { className: "perf", "aria-labelledby": "perf-h" },
    h("div", { className: "sechead" }, h("h3", { id: "perf-h" }, "Performance"), h("span", { className: "count" }, "every number: unit · runs · spread · trust")), ...kids);
  if (!cmp) return wrap(h("p", { className: "err" }, "Couldn't load the performance comparison for this check."));
  const mt = cmp.metrics || {}, cl = cmp.classification || {}, models = cmp.vs_models || [];
  const byId = Object.fromEntries(models.map((m) => [m.id, m]));
  const listed = byId[cl.claimed], best = byId[cl.best_match], v = mt.int8_tops_verified;
  const sim = r.health?.source === "simulated";
  const headline = v
    ? h("div", { className: "headline" },
      h("p", { className: "big" }, `at least ${fmt(v.median)} TOPS`, h("small", {}, v.pct_of_spec != null && listed ? ` · ${fmt(v.pct_of_spec)}% of the ${listed.name}'s rating (${fmt(v.spec)} TOPS)` : "")),
      h("p", { className: "sub" }, trustChip("verified"), " from re-graded work, timed by the API · dense INT8 · ", runs(v.n),
        sim ? h("span", {}, " · ", pill("CPU test run: tiny matrices, not a GPU number", "suspect")) : null))
    : h("div", { className: "headline" }, h("p", { className: "big" }, "No verified number"),
      h("p", { className: "sub" }, "The work did not pass re-grading in time, so its timing proves nothing about this GPU."));

  const me = (() => {
    const x = mt.int8_tops?.value ?? v?.value, y = mt.hbm_copy_tbs?.value;
    return x > 0 && y > 0 ? { x, y, verified: mt.int8_tops?.value == null } : null;
  })();
  const cands = (cl.candidates || []).map((c) => h("li", {},
    h("b", {}, byId[c.id]?.name || c.id), " ",
    c.id === cl.best_match ? pill("closest", "pass") : null, " ", c.id === cl.claimed ? pill("listed", "unknown") : null, " ",
    h("span", { className: "sub" }, c.why || ""), h("small", { className: "dist" }, ` distance ${fmt(c.distance)}`)));
  const amb = (cl.ambiguous_with || cl.ambiguous || []).map((id) => byId[id]?.name || id);

  const co = cmp.cohort || {}, coName = byId[co.model_id]?.name || "this model";
  const ranks = Object.entries(co.percentiles || {}).filter(([k]) => HEADLINE.includes(k) && co.values?.[k]?.length && mt[k])
    .sort(([a], [b]) => HEADLINE.indexOf(a) - HEADLINE.indexOf(b));
  const cohort = ranks.length
    ? h("ul", { className: "cohort" }, ranks.map(([k, pr]) => h("li", {},
      h("div", {}, h("span", { className: "label" }, mname(k)), " ", trustChip(mt[k].trust)),
      h("p", {}, `Faster than ${Math.round(pr)}% of ${co.values[k].length} other ${coName} checks.`),
      strip(co.values[k], mt[k].median ?? mt[k].value, mt[k].unit || UNITS[k] || ""))))
    : h("p", { className: "empty" }, co.model_id
      ? `Not enough checks of the ${coName} yet (n = ${co.n} other check${co.n === 1 ? "" : "s"}; ranks start at ${co.min_n || 5}).`
      : "No closest model, so there is no cohort to compare with.");

  return wrap(headline,
    Object.keys(mt).length ? metricTable(mt) : h("p", { className: "empty" }, "The profiler sent no metrics for this check."),
    h("p", { className: "sub small" }, "Hover a metric for its method. Medians over repeated runs; bars show p10–p90. The band on % of rating is a healthy card's range."),
    methodsNote(),
    h("div", { className: "block" }, h("div", { className: "label" }, "vs other models"),
      scatter(models, me, cl.claimed, cl.best_match),
      h("div", { className: "label" }, "Closest matches"),
      cands.length ? h("ol", { className: "cands" }, cands) : h("p", { className: "sub" }, "No classification for this check."),
      amb.length ? h("p", { className: "sub" }, `Can't be told apart from ${amb.join(", ")} with what this check measured, so the record doesn't pick one.`) : null,
      cl.claimed && cl.consistent === false ? h("p", { className: "st-fail" }, `The measurements don't fit the listed ${listed?.name || cl.claimed}.`) : null),
    h("div", { className: "block" }, h("div", { className: "label" }, `vs other ${coName} checks`),
      h("p", { className: "sub small" }, `Cohort: checks whose closest match is the ${coName} and whose work passed re-grading.`), cohort));
}

// ---- leaderboard and reference models ------------------------------------------------------------------------
const subtabs = (cur) => h("nav", { className: "subtabs", "aria-label": "Provider views" },
  ...[["#/providers", "Providers"], ["#/models", "Reference models"]].map(([href, t]) => h("a", { href, "aria-current": href === cur ? "page" : null }, t)));

// Providers: the roll-up of every <cloud>.waterline.eth, then how each one's GPUs of one model performed.
async function providersView(arg) {
  const [lb, pv, g] = await Promise.all([api("/api/leaderboard"), api("/api/providers").catch(() => ({ providers: [] })), api("/api/gpus").catch(() => ({ gpus: [] }))]);
  const pcts = new Map();
  for (const x of g.gpus) if (x.pct_of_spec != null) pcts.set(x.provider_node, [...(pcts.get(x.provider_node) || []), x.pct_of_spec]);
  const median = (xs) => { xs = [...(xs || [])].sort((a, b) => a - b); return xs.length ? xs[(xs.length - 1) >> 1] : null; };
  const failedShare = (p) => (p.gpus ? p.failed_gpus / p.gpus : 0);
  const provs = [...pv.providers].sort((a, b) => failedShare(a) - failedShare(b) || (median(pcts.get(b.provider_node)) ?? 0) - (median(pcts.get(a.provider_node)) ?? 0));
  const rollup = provs.map((p) => h("tr", {},
    h("td", {}, p.name ? h("a", { href: `#/name/${p.name}`, className: "mono", title: "Its page: the record read live from ENS" }, p.name) : h("span", { className: "mono" }, short(p.provider_node)),
      p.name && p.listed === false ? h("span", { className: "stag", title: "Not on our list of known providers: the name is whatever the renter typed" }, "unlisted") : null),
    h("td", {}, String(p.gpus ?? 0)), h("td", {}, h("span", { className: p.failed_gpus ? "st-fail" : "" }, String(p.failed_gpus ?? 0))),
    h("td", {}, String(p.humans ?? 0)), h("td", {}, String(p.passes ?? 0)), h("td", {}, String(p.degraded ?? 0)), h("td", {}, String(p.fails ?? 0)),
    h("td", {}, median(pcts.get(p.provider_node)) == null ? "—" : `${num(median(pcts.get(p.provider_node)))}%`)));
  const model = arg || lb.models[0]?.id, info = lb.models.find((m) => m.id === model);
  const rows = lb.rows.filter((x) => x.model === model), ranked = rows.filter((x) => !x.few), few = rows.filter((x) => x.few);
  const pick = h("select", { id: "lb-model", onchange: (e) => (location.hash = `#/providers/${e.target.value}`) },
    lb.models.map((m) => h("option", { value: m.id, selected: m.id === model }, `${m.name} · ${m.n} check${m.n > 1 ? "s" : ""}`)));
  const range = (r, u) => (r && r[0] !== r[1] ? h("small", { className: "sub" }, ` ${fmt(r[0])}–${fmt(r[1])}${u}`) : null);
  const tr = (x) => h("tr", { className: x.few ? "few" : "" },
    h("td", {}, x.rank ? `#${x.rank}` : pill("few checks", "unknown")),
    h("td", { className: "mono" }, x.cloud),
    h("td", {}, String(x.n)),
    h("td", {}, x.median_tops == null ? "—" : h("span", {}, `${fmt(x.median_tops)} TOPS`, range(x.tops_range, ""), h("small", { className: "sub" }, ` · n = ${x.n_verified}`))),
    h("td", {}, x.median_pct_of_spec == null ? "—" : h("span", {}, `${fmt(x.median_pct_of_spec)}%`, range(x.pct_range, "%"))),
    h("td", {}, h("span", {}, `${Math.round(100 * x.pass_rate)}%`, h("small", { className: "sub" }, ` ${Math.round(x.pass_rate * x.n)} of ${x.n}`))));
  const cols = ["Rank", "Provider", "Checks", "Median verified INT8 (range · n)", "Median % of rating (range)", "Pass rate"];
  return [
    head("Providers", "Every provider on record, and what its GPUs deliver", subtabs("#/providers"),
      h("p", { className: "sub" }, "Each provider is an ENS name, <cloud>.waterline.eth, and its GPUs are named under it. Its record is the roll-up of theirs: a person counts once however many of its GPUs they report.")),
    section("All providers", pv.source === "multibaas" ? "source: MultiBaas (ProviderTally events on Marks)" : "source: this API's own records",
      rollup.length ? table(["Provider", "GPUs", "Failed now", "People", "Passes", "Degraded", "Fails", "Median % of rating"], rollup)
        : h("p", { className: "empty" }, "No provider is on the record yet.")),
    section(info ? `By model · ${info.name}` : "By model", info ? `${info.n} checks across ${rows.length} provider${rows.length === 1 ? "" : "s"}` : null,
      lb.models.length ? h("div", { className: "field narrow" }, h("label", { className: "label", htmlFor: "lb-model" }, "Listed model"), pick) : null,
      ranked.length ? table(cols, ranked.map(tr)) : rows.length ? h("p", { className: "empty" }, `No cloud has ${lb.min_n} checks of this model yet, so none is ranked.`) : h("p", { className: "empty" }, "No checks yet. Run the agent against a pod to start the board."),
      few.length ? h("details", { className: "fewbox", open: !ranked.length }, h("summary", {}, `${few.length} cloud${few.length > 1 ? "s" : ""} with fewer than ${lb.min_n} checks, not ranked`),
        table(cols, few.map(tr))) : null,
      h("p", { className: "sub small" }, `Verified INT8 includes the round trip to our API, so it reads below peak and favours nearby pods. Pass rate counts every check, pending failures too. A cloud needs ${lb.min_n} checks to rank.`),
      methodsNote()),
  ];
}

async function modelsView() {
  const d = await api("/api/models");
  const unv = (m, ...fields) => (fields.some((f) => m.unverified.includes(f)) ? h("abbr", { className: "unv", title: "Unverified: derived or third-party, not read from a primary source" }, "*") : null);
  const dense = (m, k) => [m[k] == null ? "—" : fmt(m[k]), unv(m, "dense", "dense." + k)];
  const rows = d.models.map((m) => h("tr", {},
    h("td", { title: m.note || "" }, h("span", {}, m.name), h("small", { className: "mono sub" }, " " + m.id)),
    h("td", {}, String(m.sms), unv(m, "sms")), h("td", {}, m.fp8 ? "yes" : "no"),
    h("td", {}, `${m.mem_gb} GB`, unv(m, "mem_gb")), h("td", {}, `${m.bw_tbs} TB/s`, unv(m, "bw_tbs")),
    h("td", {}, ...dense(m, "int8_tops")), h("td", {}, ...dense(m, "bf16_tflops")), h("td", {}, ...dense(m, "fp8_tflops")),
    h("td", {}, m.sources.map((u, i) => h("a", { href: u, target: "_blank", rel: "noopener", className: "src", title: u }, `[${i + 1}]`)))));
  return [
    head("Providers", "Reference models", subtabs("#/models"),
      h("p", { className: "sub" }, `${d.models.length} reference models from vendor datasheets (${d.generated}). Dense throughput; sparse-only figures are halved.`)),
    section("Spec table", `${d.models.length} models · ${d.confusable_pairs.length} look-alike pairs`,
      table(["Model", "SMs", "FP8", "Memory", "Bandwidth", "INT8 TOPS", "BF16 TFLOPS", "FP8 TFLOPS", "Sources"], rows),
      h("p", { className: "sub small" }, h("abbr", { className: "unv" }, "*"), " unverified: derived or from a third party, not confirmed from a primary source. For AMD, SMs are compute units. GeForce FP8 and BF16 figures use FP32 accumulate, as cuBLAS does.")),
  ];
}

// ---- World ID: this browser's session, and the mandate that lets your agents report ------------------------------
async function mandateNow() {
  const tok = token.get();
  if (!tok) return { tok: null, m: null };
  try {
    return { tok, m: (await api("/api/world/mandate/status", { agent_token: tok })).mandate };
  } catch (e) {
    if (e.status === 401) token.set(null); // the login expired: this browser is logged out
    return { tok: e.status === 401 ? null : tok, m: null };
  }
}
const left = (t) => { const s = t - Date.now() / 1000; return s <= 0 ? "ended" : s >= 5400 ? `${Math.round(s / 3600)} h left` : `${Math.max(1, Math.round(s / 60))} min left`; };
const mandateState = (m) => (!m ? "none" : m.revoked_at ? "revoked" : m.active ? "active" : m.used >= m.max_reports ? "used up" : "ended");

// the header badge: who is logged in here, and whether their agents may report
async function sessionBadge() {
  const el = document.getElementById("session");
  const { tok, m } = await mandateNow();
  el.hidden = false;
  el.className = "session" + (tok ? " on" : "");
  el.replaceChildren(h("i", { "aria-hidden": "true" }),
    !tok ? "Log in with World" : m?.active ? `World ID · mandate ${m.used}/${m.max_reports} · ${left(m.expires_at)}` : "World ID · no mandate");
}

async function worldView() {
  const [{ tok, m }, hl] = await Promise.all([mandateNow(), getHealth(true).catch(() => null)]);
  const state = mandateState(m);
  const sessionBox = h("div", { className: "block" }, h("div", { className: "label world-c" }, "This browser"),
    tok ? h("p", {}, pill("logged in", "pass"), " with World. Pseudonymous: the record only ever sees a per-GPU and a per-provider id, never who you are.")
        : h("p", {}, pill("not logged in", "unknown"), " Log in once to approve failure reports, or to let your agents report them."),
    h("div", { className: "row" }, tok
      ? h("button", { type: "button", className: "btn", onclick: () => { token.set(null); route(false); } }, "Log out")
      : h("button", { type: "button", className: "btn world", onclick: () => loginFlow() }, "Log in with World")));
  const hours = h("select", { id: "md-hours" }, [[1, "1 hour"], [8, "8 hours"], [24, "24 hours"], [72, "3 days"]].map(([v, t]) => h("option", { value: v, selected: v === 24 }, t)));
  const max = h("select", { id: "md-max" }, [5, 20, 50, 100].map((v) => h("option", { value: v, selected: v === 20 }, `${v} reports`)));
  const grant = h("button", { type: "button", className: "btn world", disabled: !tok, onclick: () => mandateFlow(+hours.value, +max.value) },
    m?.active ? "Replace with World" : "Grant with World");
  const mandateBox = h("div", { className: "block mandate" }, h("div", { className: "label world-c" }, "Agent mandate"),
    h("p", {}, pill(state, { active: "pass", none: "unknown", revoked: "fail" }[state] || "degraded"),
      m ? ` since ${when(m.created_at)} · until ${when(m.expires_at)}${m.active ? ` · ${left(m.expires_at)}` : ""}` : " Your agents ask you before every failure report."),
    m ? gauge("Failure reports used", `${m.used} of ${m.max_reports}`, (m.used / m.max_reports) * 100, "", `${m.used} of ${m.max_reports} reports used`) : null,
    m?.active ? h("div", { className: "row" }, h("button", { type: "button", className: "btn", onclick: async () => {
      await api("/api/world/mandate/revoke", { agent_token: tok }); route(false); } }, "Revoke now")) : null,
    h("div", { className: "grant" },
      h("div", { className: "field" }, h("label", { className: "label", htmlFor: "md-hours" }, "Lasts"), hours),
      h("div", { className: "field" }, h("label", { className: "label", htmlFor: "md-max" }, "Covers up to"), max), grant),
    h("p", { className: "sub small" }, tok ? ["Agents use it through the login saved by ", h("code", {}, "python -m agent login"), ", or grant it there: ",
      h("code", {}, "python -m agent mandate --hours 24 --max 20"), "."] : "Log in first."));
  return [
    head("World ID", "One person, many agents, one voice",
      h("p", { className: "sub" }, "A failure accuses a provider, so it needs a real person. Approve reports one at a time with World App, or grant your agents a mandate: one approval, a time limit and a report budget. Either way you count once per GPU and once per provider.")),
    section("Your session", tok ? (m?.active ? "agents may report" : "reports wait for you") : "not logged in", h("div", { className: "detail" }, sessionBox, mandateBox)),
    section("How people count", "the same rules with or without a mandate", h("ol", { className: "how" }, [
      "One voice per person per GPU, and once per provider however many of its GPUs you report. Twenty agents under one person are still one voice.",
      "Two different people mark a GPU failed. One report makes it suspect.",
      "Every report carries the listing in the reporter's words. When Jev reads it as another GPU, only you can report anyway; a mandate never does.",
      "A mandate is bounded (hours, reports) and revocable, and reports made under it say so on the check.",
      "Two passes after the last failure mark a GPU recovered; its history stays public.",
    ].map((x) => h("li", {}, h("span", {}, x))))),
    section("What the statuses mean", null, kvList(["pass", "At least one check passed and nobody has reported it."],
      ["suspect · 1 of 2 humans", "One person reported a failure. A second person who checks it and reports makes it failed."], ["failed", "Two different people reported failures."],
      ["degraded", "Latest check: right chip, correct answers, too slow (heat, a power cap or sharing). Published with its numbers; never counts toward failed."],
      ["recovered", "Failure reports, then two passes after the last one. The reports stay in its history."],
      ["unknown", "No published check yet."],
      ["provider (cloud-b.waterline.eth)", "GPUs failed now, and people who reported any of them (each once). Descriptive: GPUs are judged one by one."])),
    section("For providers", "your name, your voice, never your score", h("p", { className: "sub" },
      "Your GPUs are named under yours, e.g. cloud-b.waterline.eth. Only a check can change a score. Two ways back from a failure: renters' passes (two mark a GPU recovered) and your own note (ask us for the note role; it changes no number).")),
    section("For agents and integrations", "read it anywhere", h("p", { className: "sub" },
      "Read any name on ENS (waterline.status, class, cores, pct_of_spec, passes, degraded, fails, humans, report; providers add gpus, failed_gpus, note), query Reported and ProviderTally on MultiBaas, or GET /api/gpus and /api/providers."),
      hl ? kvList(["Marks contract", scan("address", hl.chain.marks) || "—"], ["ENS parent", hl.ens.parent], ["Indexed by", hl.multibaas.configured ? "Curvegrid MultiBaas" : "—"]) : null),
  ];
}
const kvList = (...pairs) => h("dl", { className: "kv" }, pairs.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v ?? "—")]));

// ---- World: log in once, then approve a failure report ------------------------------------------------------
const dlg = document.getElementById("world");
const $w = (id) => document.getElementById("world-" + id);
let flow = 0;
const say = (text, tone = "", ...extra) => { const s = $w("status"); s.className = "status " + tone; s.replaceChildren(text, ...extra); };
$w("close").addEventListener("click", () => dlg.close());
dlg.addEventListener("close", () => { flow++; route(false); }); // cancels any polling and refreshes the page

async function showCode(d) {
  $w("usercode").textContent = d.user_code;
  $w("link").href = d.verification_uri_complete;
  $w("code").hidden = false;
  const box = $w("qr");
  box.replaceChildren();
  try {
    const { default: qrcode } = await import(QRLIB);
    const q = qrcode(0, "M");
    q.addData(d.verification_uri_complete);
    q.make();
    box.append(h("img", { src: q.createDataURL(4, 0), alt: "" }));
  } catch {
    box.hidden = true; // the link and the code still work
  }
}

// Device grant: show code + QR, poll until the person decides. Returns the final poll answer, or null if closed.
async function device(startPath, body, pollPath, my, hint) {
  const d = await api(startPath, body);
  await showCode(d);
  say(hint + " Waiting for World…");
  const until = Date.now() + d.expires_in * 1000;
  while (Date.now() < until) {
    await sleep(2000);
    if (my !== flow) return null;
    let r;
    try {
      r = await api(pollPath, { device_id: d.device_id });
    } catch (e) {
      if (e.status === 503) { say("World is not answering right now. Still waiting…"); continue; }
      throw e;
    }
    if (my !== flow) return null;
    if (r.status !== "pending") { $w("code").hidden = true; return r; }
  }
  $w("code").hidden = true;
  return { status: "expired" };
}

// A failure accuses the provider of misselling this GPU. Before World is asked, the reporter sees the accusation,
// states what they rented in the listing's own words, and accepts that the report is tied to their World ID.
function confirmReport(rep, my) {
  return new Promise((done) => {
    const text = h("textarea", { id: "world-listing", rows: 3, placeholder: "https://… or the listing text, e.g. 1x H100 80GB SXM5 · $2.49/h" });
    const ack = h("input", { type: "checkbox", id: "world-ack" });
    const go = h("button", { type: "button", className: "btn primary", disabled: true }, "Continue to World");
    const ready = () => { go.disabled = !(ack.checked && text.value.trim().length >= 8); };
    text.addEventListener("input", ready); ack.addEventListener("change", ready);
    const box = h("div", { className: "confirm" },
      h("p", {}, "You are reporting: ", h("b", {}, `listed as ${cls(rep.claimed_class)}`), " · ", h("b", { className: "st-fail" }, `measures as ${cls(rep.measured_class)}`), "."),
      h("label", { className: "label", htmlFor: "world-listing" }, "The listing you rented (URL or text)"), text,
      h("label", { className: "check", htmlFor: "world-ack" }, ack, " I rented this GPU from this listing. This report is tied to my World ID and shown with the listing."),
      go);
    $w("what").after(box);
    const end = (v) => { box.remove(); done(v); };
    go.addEventListener("click", () => end(text.value.trim()));
    dlg.addEventListener("close", () => end(null), { once: true });
    text.focus();
    if (my !== flow) end(null);
  });
}

function reportAnyway(message, my) {
  return new Promise((done) => {
    const yes = h("button", { type: "button", className: "btn" }, "Report anyway");
    const no = h("button", { type: "button", className: "btn primary" }, "Don't report");
    const box = h("div", { className: "confirm" }, h("p", { className: "st-suspect" }, message), h("div", { className: "row" }, no, yes));
    say("");
    $w("what").after(box);
    const end = (v) => { box.remove(); done(v && my === flow); };
    yes.addEventListener("click", () => end(true));
    no.addEventListener("click", () => end(false));
    dlg.addEventListener("close", () => end(false), { once: true });
  });
}

async function login(my) {
  $w("title").textContent = "Log in with World";
  const r = await device("/api/world/login/start", {}, "/api/world/login/poll", my, "Scan the code with World App or open the link.");
  if (!r) return null;
  if (r.status !== "approved") { say(`Login ${r.status}.`, "bad"); return null; }
  token.set(r.agent_token);
  return r.agent_token;
}

async function loginFlow() {
  const my = ++flow;
  $w("code").hidden = true;
  $w("what").textContent = "Log in once in this browser. Nothing is published by logging in.";
  say("");
  dlg.showModal();
  try {
    if (await login(my)) say("Logged in.", "good");
  } catch (e) { if (my === flow) say(e.message, "bad"); }
}

async function mandateFlow(hours, max) {
  const my = ++flow;
  $w("code").hidden = true;
  $w("title").textContent = "Grant a mandate";
  $w("what").textContent = `Let your agents report up to ${max} failures in the next ${hours} h, as your one voice per GPU. You can revoke it at any time.`;
  say("");
  dlg.showModal();
  try {
    const r = await device("/api/world/mandate/start", { agent_token: token.get(), hours, max_reports: max }, "/api/world/mandate/poll", my,
      "Approve with World App: scan the code or open the link.");
    if (!r) return;
    if (!r.mandate) return say(r.status_text || `${r.status}: no mandate.`, "bad");
    say(`Granted: up to ${r.mandate.max_reports} reports until ${when(r.mandate.expires_at)}.`, "good");
  } catch (e) {
    if (e.status === 401) { token.set(null); return say("Your World login has expired. Close this and log in again.", "bad"); }
    if (my === flow) say(e.message, "bad");
  }
}

async function approveFlow(rep) {
  const my = ++flow;
  $w("code").hidden = true;
  $w("what").textContent = `Report ${rep.gpu_name}? It goes public only with your World approval.`;
  say("");
  dlg.showModal();
  try {
    const tok = token.get() || (await login(my));
    if (!tok) return;
    $w("title").textContent = "Report this GPU";
    const listing = await confirmReport(rep, my);
    if (!listing) return;
    $w("title").textContent = "Approve with World";
    let r;
    const start = (extra) => device("/api/report/approve/start", { report_id: rep.report_id, agent_token: tok, listing, ...extra },
      "/api/report/approve/poll", my, "Approve this failure report: scan the code with World App or open the link.");
    try {
      r = await start({});
    } catch (e) {
      if (e.status === 409 && /report anyway/.test(e.message)) {  // Jev read the listing as another GPU than reported
        if (!(await reportAnyway(e.message, my))) return say("Not reported. Nothing was published.", "bad");
        r = await start({ report_anyway: true });
      } else if (e.status === 401) {
        token.set(null);
        return say("Your World login has expired. Close this and press Approve again to log in.", "bad");
      } else throw e;
    }
    if (!r) return;
    if (r.status === "denied") return say("Denied. Nothing was published.", "bad");
    if (r.status === "expired") return say("Expired. Nothing was published.", "bad");
    if (!r.published) return say(r.status_text || "Approved, but nothing was published.", "bad");
    const g = await api("/api/gpus").catch(() => null);
    const now = g?.gpus.find((x) => x.node === rep.node);
    say("Published. ", "good", r.status_text || "", " ", r.tx ? h("a", { href: `${SCAN}/tx/${r.tx}`, target: "_blank", rel: "noopener" }, "View the transaction") : "(dry run: no transaction sent)",
      now ? h("span", {}, ". GPU status now: ", pill(now.status)) : "");
  } catch (e) {
    if (my === flow) say(e.message, "bad"); // API messages are plain sentences (409 already reported, 503 World down, ...)
  }
}
