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

// Wireframe terrain with a flat waterline: heights below zero are clipped to the water. Stops when off screen.
function terrain(canvas) {
  const ctx = canvas.getContext("2d");
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let t = 0, last = -Infinity;
  const frame = (now) => {
    if (!canvas.isConnected) return;
    if (!still) requestAnimationFrame(frame);
    if (now - last < 33) return; // ~30 fps is plenty
    last = now;
    const w = canvas.clientWidth, H = canvas.clientHeight, dpr = Math.min(devicePixelRatio || 1, 2);
    if (canvas.width !== Math.round(w * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(H * dpr); }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, H);
    const rows = 16, cols = 64;
    for (let r = 0; r < rows; r++) {
      const z = r / (rows - 1), base = H * (0.12 + 0.86 * z ** 1.5);
      ctx.beginPath();
      for (let c = 0; c < cols; c++) {
        const u = c / (cols - 1);
        const f = Math.sin(u * 9 + r * 0.5 + t) * Math.cos(u * 4 - r * 0.3 + t * 0.6) + 0.45 * Math.sin(u * 17 + r * 0.9 - t);
        const x = w / 2 + (u - 0.5) * w * (0.7 + 0.8 * z), y = base - Math.max(0, f) * (6 + 34 * z);
        c ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      }
      ctx.strokeStyle = `rgba(95,227,185,${0.06 + 0.3 * z})`;
      ctx.lineWidth = 0.8;
      ctx.stroke();
    }
    t += 0.01;
  };
  requestAnimationFrame(frame);
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

async function checkDetail(id) {
  const r = await api(`/api/reports/${encodeURIComponent(id)}`);
  const p = r.probes || {};
  const kv = (...pairs) => h("dl", { className: "kv" }, pairs.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v ?? "—")]));
  const [form, out, run] = ensLookup(r.gpu_name);
  const over = r.elapsed_s > r.deadline_s;
  const pct = r.deadline_s ? Math.min(100, (r.elapsed_s / r.deadline_s) * 100) : 0;
  return [
    head("Check " + short(r.report_id), r.gpu_name || "Unknown GPU",
      h("div", { className: "verdict " + (r.verdict === "pass" ? "st-pass" : "st-fail") }, r.verdict),
      h("p", {}, verdictPill(r), " ", h("span", { className: "sub" }, r.status_text || "")),
      needsApproval(r) ? h("div", {}, h("button", { type: "button", className: "btn world", onclick: () => approveFlow(r) }, "Approve with World")) : null),
    h("div", { className: "detail" },
      h("div", { className: "block" }, h("div", { className: "label" }, "Why"),
        r.reasons?.length ? h("ul", { className: "reasons" }, r.reasons.map((x) => h("li", {}, x))) : h("p", {}, "Every check passed: the work was done in time and the hardware matches the listing."),
        kv(["Listed as", cls(r.claimed_class)], ["Measured as", h("span", { className: r.claimed_class === r.measured_class ? "st-pass" : "st-fail" }, cls(r.measured_class))], ["Cloud", r.cloud], ["When", when(r.created_at)])),
      h("div", { className: "block" }, h("div", { className: "label" }, "Probes"),
        kv(["Cores (SMs)", String(p.sms ?? "—")], ["FP8 maths", p.fp8 == null ? "—" : p.fp8 ? "yes (Hopper)" : "no"],
          ["Clock", p.clock_ghz ? `${p.clock_ghz} GHz` : "—"], ["Copy bandwidth", p.bw_tbs ? `${p.bw_tbs} TB/s` : "—"],
          ["Per-core fingerprint", h("span", { title: p.fingerprint }, short(p.fingerprint))])),
      h("div", { className: "block" }, h("div", { className: "label" }, "Timing"),
        h("p", {}, h("b", { className: over ? "st-fail" : "st-pass" }, `${r.elapsed_s ?? "—"} s`), h("span", { className: "sub" }, ` of a ${r.deadline_s ?? "—"} s deadline`)),
        h("div", { className: "bar" + (over ? " over" : ""), role: "img", "aria-label": `${r.elapsed_s} of ${r.deadline_s} seconds` }, h("i", { style: `width:${pct}%` })),
        kv(["Matrix size n", String(r.n ?? "—")], ["Steps", String(r.steps ?? "—")]),
        h("div", { className: "label" }, "Rows checked (step, row)"),
        r.samples?.length ? h("ul", { className: "samples" }, r.samples.map(([s, row]) => h("li", {}, `${s}, ${row}`))) : h("p", { className: "sub" }, "—"))),
    section("Core-count staircase", r.staircase ? `step at ${p.sms} blocks` : null,
      h("p", { className: "sub" }, "One busy block per core. Once there are more blocks than cores, the extra ones wait and the time jumps. Heat can slow a GPU down, but it can't move this step."),
      r.staircase ? staircase(r.staircase, p.sms) : h("p", { className: "empty" }, "The profiler did not send staircase timings for this check.")),
    section("On chain", null,
      kv(["Published", r.published ? "yes" : "no"], ["Transaction", txLink(r.tx, r.published)], ["ENS node", h("span", { className: "mono", title: r.node }, short(r.node))], ["GPU name", r.gpu_name]),
      h("div", { className: "label" }, "Live ENS record"), form, out),
  ];
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
