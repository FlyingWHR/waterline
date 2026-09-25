// Waterline control panel. Plain ES module, no build. Talks only to this origin's /api/*; addresses come from /api/health.
const VIEM = "https://esm.sh/viem@2.56.9";
const QRLIB = "https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/+esm";
const SCAN = "https://sepolia.etherscan.io";
const CLASSES = { 0: "unknown", 1: "H100 SXM", 2: "H100 PCIe", 3: "A100" };
const REFS = [[108, "A100"], [114, "H100 PCIe"], [132, "H100 SXM"]];
const ENS_KEYS = ["status", "class", "cores", "passes", "fails", "humans", "fingerprint"];
const TOKEN_KEY = "waterline.agent_token";
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
const cls = (c) => CLASSES[c] ?? "unknown";
const scan = (kind, x) => (x ? h("a", { href: `${SCAN}/${kind}/${x}`, target: "_blank", rel: "noopener", className: "mono" }, short(x)) : null);
const txLink = (tx, published) => scan("tx", tx) || h("span", { className: "sub" }, published ? "dry-run, no tx" : "—");
const pill = (text, kind) => h("span", { className: "pill st-" + (kind || String(text).split(" ")[0]) }, text);
const verdictPill = (r) =>
  r.verdict === "pass" ? pill("pass") : r.published ? pill("fail · published", "fail") : pill("fail · awaiting approval", "pending");
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
const routes = { "": [overview, "Overview"], gpus: [gpus, "GPUs"], checks: [checks, "Checks"], check: [checkDetail, "Check"], about: [about, "Settings"] };
let nav = 0;
async function route(focus) {
  const my = ++nav;
  const [, name = "", arg] = location.hash.split("/");
  const [fn, title] = routes[name] || routes[""];
  const tab = name === "check" ? "checks" : name in routes ? name : "";
  for (const a of document.querySelectorAll(".tabs a"))
    a.getAttribute("href") === `#/${tab}` ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current");
  document.title = `${title} · Waterline`;
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
route(false);

// ---- overview ----------------------------------------------------------------------------------------------
async function overview() {
  const [hl, g, reps] = await Promise.all([getHealth(true), api("/api/gpus"), api("/api/reports?limit=5")]);
  const canvas = h("canvas", { "aria-hidden": "true" });
  requestAnimationFrame(() => terrain(canvas));
  const count = (st) => g.gpus.filter((x) => x.status.startsWith(st)).length;
  const c = hl.chain;
  const tile = (layer, label, big, ...small) =>
    h("div", { className: "tile c-" + layer }, h("div", { className: "label" }, h("i", { className: "dot" }), label), h("b", {}, big), ...small.map((s) => h("span", {}, s)));
  return [
    h("div", { className: "hero" }, canvas, h("div", { className: "kicker" }, "Ethereum Sepolia · ENS · World · MultiBaas"),
      h("h1", {}, "Waterline"),
      h("p", { className: "lede" }, "What every rented GPU really is, checked by the people who rent it. The record lives on the GPU's ENS name, which the host can't edit.")),
    section("How a check works", "one principle: work only the claimed chip can finish in time", flowDiagram()),
    section("System", null, h("div", { className: "tiles" },
      tile("api", "API", "Online", hl.store === "redis" ? "Store: Redis" : "Store: memory (this process only)"),
      tile("chain", "Chain", c.mode === "live" ? "Live" : "Dry run", `Sepolia · chain ${c.chain_id}`,
        c.marks ? h("span", {}, "Marks ", scan("address", c.marks)) : "Marks address not set: reports are logged, not sent"),
      tile("chain", "Reporter", c.reporter_balance_eth == null ? "—" : `${c.reporter_balance_eth.toFixed(4)} ETH`,
        c.reporter ? h("span", {}, "Account ", scan("address", c.reporter)) : "No reporter account set",
        c.mode === "live" && c.reporter_balance_eth == null ? "Balance unknown: the RPC did not answer" : null),
      tile("world", "World", hl.world.mode === "mock" ? "Mock" : "Live", hl.world.mode === "mock" ? "Local stand-in, no real humans" : hl.world.issuer),
      tile("mb", "MultiBaas", hl.multibaas.configured ? "Configured" : "Not configured",
        hl.multibaas.configured ? hl.multibaas.url : "GPU table uses this API's own records"),
      tile("ens", "ENS", hl.ens.parent, hl.ens.universal_resolver ? h("span", {}, "Universal resolver ", scan("address", hl.ens.universal_resolver)) : "Universal resolver not set"))),
    section("GPUs on record", g.source === "multibaas" ? "from MultiBaas" : "from this API", h("div", { className: "nums" },
      ...[["Checked", g.gpus.length, ""], ["Pass", count("pass"), "st-pass"], ["Suspect", count("suspect"), "st-suspect"], ["Failed", count("failed"), "st-failed"]]
        .map(([l, n, k]) => h("div", { className: "num" }, h("span", { className: "label" }, l), h("b", { className: k }, String(n)))))),
    section("Recent checks", null, reps.length ? checksTable(reps) : h("p", { className: "empty" }, "No checks yet. Run the agent against a pod to see one here."),
      h("p", {}, h("a", { href: "#/checks" }, "All checks →"))),
  ];
}

// "How a check works": nodes and wires in HTML, so it wraps to a column on phones instead of being cut off.
function flowDiagram() {
  const node = (layer, name, what) => h("div", { className: "node c-" + layer }, h("b", {}, name), h("small", {}, what));
  const wire = (label, proof) => h("div", { className: "wire" + (proof ? " proof" : ""), "aria-hidden": "true" }, h("span", {}, label));
  const att = (layer, name, what) => h("div", { className: "att c-" + layer }, h("b", {}, name), h("small", {}, what));
  return h("div", { className: "panel" },
    h("div", { className: "flow", role: "img", "aria-label": "Agent starts the profiler in the rented pod. The profiler answers the Waterline API's puzzle. The API records the report on Marks on Sepolia, which answers for the GPU's ENS name. World approves failures at the API; MultiBaas indexes Marks' history." },
      h("div", { className: "stage" }, node("people", "Agent", "renter's laptop")),
      wire("starts over SSH"),
      h("div", { className: "stage" }, node("pod", "Profiler", "in the rented pod")),
      wire("seed ⇄ answer", true),
      h("div", { className: "stage" }, node("api", "Waterline API", "times it, re-checks it"), att("world", "World", "approves failures")),
      wire("records", true),
      h("div", { className: "stage" }, node("chain", "Marks", "contract on Sepolia"), att("mb", "MultiBaas", "indexes history")),
      wire("resolves", true),
      h("div", { className: "stage" }, node("ens", "ENS name", "gpu-….waterline.eth"))),
    h("div", { className: "legend" }, h("span", {}, h("i", { className: "sw-proof" }), "proof path"), h("span", {}, h("i", { className: "sw-att" }), "partner attached to a step")),
    h("p", { className: "sub" }, "Your agent starts the profiler inside your pod. The API gives it a fresh puzzle and a deadline, then re-checks a random slice of the answer. A pass goes straight to Marks and shows on the GPU's ENS name; a failure waits for a person to approve it with World. MultiBaas indexes every report so agents can skip bad GPUs."));
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
  const g = await api("/api/gpus");
  const [form, out, run] = ensLookup();
  const rows = g.gpus.map((x) => h("tr", {},
    h("td", { className: "mono", title: x.node }, x.gpu_name || short(x.node)),
    h("td", {}, cls(x.cls)), h("td", {}, String(x.cores ?? "—")), h("td", {}, String(x.passes)), h("td", {}, String(x.fails)),
    h("td", {}, String(x.humans)), h("td", {}, pill(x.status)), h("td", {}, when(x.last_at)),
    h("td", {}, x.gpu_name ? h("button", { type: "button", className: "btn sm", onclick: () => { run(x.gpu_name); form.scrollIntoView({ block: "center" }); } }, "Look up") : null)));
  return [
    head("GPUs", "GPU health", h("p", { className: "sub" }, "One row per GPU on the public record. A failure needs two different people before the GPU shows as failed.")),
    section("On record", g.source === "multibaas" ? "source: MultiBaas (Reported events on Marks)" : "source: this API's own records",
      g.error ? h("p", { className: "err" }, g.error) : null,
      rows.length ? table(["GPU", "Measured as", "Cores", "Passes", "Fails", "People", "Status", "Last report", ""], rows)
        : h("p", { className: "empty" }, "No GPU is on the record yet.")),
    section("Look up a GPU on ENS", "read live from Sepolia", h("p", { className: "sub" }, "Type a GPU name. Its record is read through the ENS universal resolver, straight from the chain."), form, out),
  ];
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
      if (!hl.ens.universal_resolver) throw new Error("the API has no ENS universal resolver configured");
      const [{ createPublicClient, http }, { sepolia }, { normalize }] = await Promise.all([import(VIEM), import(VIEM + "/chains"), import(VIEM + "/ens")]);
      const client = createPublicClient({ chain: sepolia, transport: http(hl.ens.rpc) });
      const n = normalize(name);
      const vals = await Promise.all(ENS_KEYS.map((k) => client.getEnsText({ name: n, key: "waterline." + k, universalResolverAddress: hl.ens.universal_resolver })));
      if (!vals.some(Boolean)) {
        out.replaceChildren(h("p", { className: "msg" }, `No record for ${n} yet. Nobody has published a check of this GPU, or Marks is not its resolver yet.`));
        return;
      }
      const r = Object.fromEntries(ENS_KEYS.map((k, i) => [k, vals[i] || ""]));
      const row = (k, v) => [h("dt", {}, k), h("dd", {}, v || "—")];
      out.replaceChildren(h("div", { className: "ens" }, h("div", { className: "label" }, "ENS record"), h("div", { className: "nm" }, n), pill(r.status || "unknown"),
        h("dl", { className: "kv" }, row("Measured as", r.class), row("Cores", r.cores), row("Passed checks", r.passes || "0"),
          row("Failure reports", r.fails || "0"), row("People who reported it", r.humans || "0"), row("Timing fingerprint", r.fingerprint))));
    } catch (e) {
      out.replaceChildren(h("p", { className: "err" }, `Couldn't read ${name}: ${e.shortMessage || e.message}`));
    }
  };
  const form = h("form", { className: "inline", onsubmit: (e) => { e.preventDefault(); run(input.value); } },
    h("div", { className: "field" }, h("label", { className: "label", htmlFor: "ens-name" }, "GPU name"), input),
    h("button", { type: "submit", className: "btn primary" }, "Look up"));
  return [form, out, run];
}

// ---- checks ------------------------------------------------------------------------------------------------
function checksTable(reps) {
  return table(["When", "GPU", "Listed as", "Measured as", "Verdict", "Tx", ""], reps.map((r) => h("tr", {},
    h("td", {}, when(r.created_at)),
    h("td", {}, h("a", { href: `#/check/${r.report_id}`, className: "mono", title: "Open this check" }, r.gpu_name || r.report_id)),
    h("td", {}, cls(r.claimed_class)), h("td", {}, cls(r.measured_class)), h("td", {}, verdictPill(r)),
    h("td", {}, txLink(r.tx, r.published)),
    h("td", {}, needsApproval(r) ? h("button", { type: "button", className: "btn sm world", onclick: () => approveFlow(r) }, "Approve with World") : null))));
}

async function checks() {
  const reps = await api("/api/reports?limit=100");
  const pending = reps.filter(needsApproval).length;
  return [
    head("Checks", "Recent checks", h("p", { className: "sub" }, "Passes publish on their own. A failure waits here until a person approves it with World; denying or ignoring it publishes nothing.")),
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
  const r = await api(`/api/reports/${encodeURIComponent(id)}`);
  const p = r.probes || {};
  const kv = (...pairs) => h("dl", { className: "kv" }, pairs.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v ?? "—")]));
  const [form, out] = ensLookup(r.gpu_name);
  const over = r.elapsed_s > r.deadline_s;
  const spec = (k, v) => h("div", {}, h("b", {}, v), h("span", {}, k));
  const listed = cls(r.claimed_class);
  const pct = r.pct_of_spec;
  return [
    head("Check " + short(r.report_id), r.gpu_name || "Unknown GPU",
      h("div", { className: "verdict " + (r.verdict === "pass" ? "st-pass" : "st-fail") }, r.verdict),
      h("p", {}, verdictPill(r), " ", h("span", { className: "sub" }, r.status_text || "")),
      needsApproval(r) ? h("div", {}, h("button", { type: "button", className: "btn world", onclick: () => approveFlow(r) }, "Approve with World")) : null),
    h("section", { className: "test" },
      h("p", { className: "principle" }, "The test: do work only the claimed chip can finish in time, then re-check a random slice of it."),
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
      h("p", { className: "sub small" }, "Effective TOPS counts the whole round trip (generating, hashing, network), so it reads below the chip's peak.")),
    h("div", { className: "detail" },
      h("div", { className: "block" }, h("div", { className: "label" }, "Why"),
        r.reasons?.length ? h("ul", { className: "reasons" }, r.reasons.map((x) => h("li", {}, x))) : h("p", {}, "Every check passed: the work was done in time and the hardware matches the listing."),
        kv(["Listed as", listed], ["Measured as", h("span", { className: r.claimed_class === r.measured_class ? "st-pass" : "st-fail" }, cls(r.measured_class))], ["Cloud", r.cloud], ["When", when(r.created_at)])),
      h("div", { className: "block" }, h("div", { className: "label" }, "Probes"),
        kv(["Cores (SMs)", String(p.sms ?? "—")], ["FP8 maths", p.fp8 == null ? "—" : p.fp8 ? "yes (Hopper)" : "no"],
          ["Clock", p.clock_ghz ? `${p.clock_ghz} GHz` : "—"], ["Copy bandwidth", p.bw_tbs ? `${p.bw_tbs} TB/s` : "—"],
          ["Per-core fingerprint", h("span", { title: p.fingerprint }, short(p.fingerprint))])),
      h("div", { className: "block" }, h("div", { className: "label" }, "Rows re-checked (step, row)"),
        r.samples?.length ? h("ul", { className: "samples" }, r.samples.map(([st, row]) => h("li", {}, `${st}, ${row}`))) : h("p", { className: "sub" }, "—"),
        h("p", { className: "sub small" }, "Picked with the API's secret randomness only after the answer was locked in; 64 entries of each row are recomputed on the API's CPU."))),
    section("Core-count staircase", r.staircase ? `step at ${p.sms} blocks` : null,
      h("p", { className: "sub" }, "One busy block per core. Once there are more blocks than cores, the extra ones wait and the time jumps. Heat can slow a GPU down, but it can't move this step."),
      r.staircase ? staircase(r.staircase, p.sms) : h("p", { className: "empty" }, "The profiler did not send staircase timings for this check.")),
    healthSection(r.health),
    section("On chain", null,
      kv(["Published", r.published ? "yes" : "no"], ["Transaction", txLink(r.tx, r.published)], ["ENS node", h("span", { className: "mono", title: r.node }, short(r.node))], ["GPU name", r.gpu_name]),
      h("div", { className: "label" }, "Live ENS record"), form, out),
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
    h("p", { className: "sub" }, "Standard profiling any renter would run: a sustained matmul burn, NVIDIA's own counters (NVML) and, when installed, the DCGM diagnostic. The host's machine reports these numbers, so they inform you but never decide a pass or fail.",
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

// ---- settings / about --------------------------------------------------------------------------------------
async function about() {
  const hl = await getHealth(true);
  const kv = (...pairs) => h("dl", { className: "kv" }, pairs.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v ?? "—")]));
  const forget = h("button", { type: "button", className: "btn", onclick: () => { token.set(null); forget.textContent = "Forgotten"; forget.disabled = true; } }, "Forget my World login");
  const steps = [
    "The API gives the GPU a random seed and starts a deadline clock.",
    "The GPU multiplies seeded INT8 matrices on its tensor cores, step after step.",
    "Each output row is reduced to a fingerprint, and all fingerprints are hashed into one Merkle root.",
    "The GPU sends the root. Only then does the API pick 8 rows at random and recompute them on its CPU.",
    "Probes measure the core-count staircase, FP8 support, clock and memory speed. They decide the hardware class.",
    "Verdict: pass if the work checks out, in time, on the class the listing promised. Otherwise fail.",
    "A pass is published at once. A failure needs a fresh approval from a person through World.",
    "One person gets one voice per GPU. It takes two different people to mark a GPU as failed.",
  ];
  return [
    head("Settings", "Configuration and how it works"),
    section("Configuration", "read from /api/health", h("div", { className: "detail" },
      h("div", { className: "block" }, h("div", { className: "label" }, "API and chain"),
        kv(["Store", hl.store], ["Chain mode", hl.chain.mode], ["Chain id", String(hl.chain.chain_id)], ["Marks", scan("address", hl.chain.marks) || "not set"],
          ["Reporter", scan("address", hl.chain.reporter) || "not set"], ["Reporter balance", hl.chain.reporter_balance_eth == null ? "—" : `${hl.chain.reporter_balance_eth} ETH`])),
      h("div", { className: "block" }, h("div", { className: "label" }, "Partners"),
        kv(["World", `${hl.world.mode} · ${hl.world.issuer}`], ["MultiBaas", hl.multibaas.configured ? hl.multibaas.url : "not configured"],
          ["ENS parent", hl.ens.parent], ["Universal resolver", scan("address", hl.ens.universal_resolver) || "not set"], ["Public RPC (ENS reads)", hl.ens.rpc])),
      h("div", { className: "block" }, h("div", { className: "label" }, "This browser"),
        h("p", { className: "sub" }, token.get() ? "You are logged in with World here. The login is kept in this browser only." : "Not logged in with World in this browser."),
        h("div", {}, forget)))),
    section("How a check works", null, h("ol", { className: "how" }, steps.map((s) => h("li", {}, h("span", {}, s))))),
    section("What the statuses mean", null, kv(["pass", "At least one check passed and nobody has reported it."],
      ["suspect · 1 of 2 humans", "One person approved a failure report."], ["failed", "Two different people approved failure reports."],
      ["unknown", "No published check yet."])),
  ];
}

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

async function approveFlow(rep) {
  const my = ++flow;
  $w("code").hidden = true;
  $w("what").textContent = `Failure report for ${rep.gpu_name}. It goes on the public record only if you approve it in World App now.`;
  say("");
  dlg.showModal();
  try {
    let tok = token.get();
    if (!tok) {
      $w("title").textContent = "Log in with World";
      const r = await device("/api/world/login/start", {}, "/api/world/login/poll", my, "First, log in: scan the code with World App or open the link.");
      if (!r) return;
      if (r.status !== "approved") return say(`Login ${r.status}. Nothing was published.`, "bad");
      token.set((tok = r.agent_token));
    }
    $w("title").textContent = "Approve with World";
    let r;
    try {
      r = await device("/api/report/approve/start", { report_id: rep.report_id, agent_token: tok }, "/api/report/approve/poll", my,
        "Approve this failure report: scan the code with World App or open the link.");
    } catch (e) {
      if (e.status !== 401) throw e;
      token.set(null);
      return say("Your World login has expired. Close this and press Approve with World again to log in.", "bad");
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
