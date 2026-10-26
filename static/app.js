// Heard - client
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pretty = (t) => String(t || "").replace(/_/g, " ");
const paras = (s) => String(s || "").split(/\n+/).filter(Boolean).map((p) => `<p>${esc(p)}</p>`).join("");
const fmtTime = (t) => new Date(t * 1000).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });

let BOOT = null, PROFILE = null, LAST = null;
let BELIEFS = {};

const CADENCE = [
  { id: "24", label: "Every day" }, { id: "72", label: "Every few days" }, { id: "168", label: "Once a week" },
];

async function api(path, body, method) {
  const opt = body === undefined ? { method: method || "GET" } :
    { method: method || "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(j.error || r.status), { data: j, status: r.status });
  return j;
}
const parseList = (v) => {
  if (Array.isArray(v)) return v;
  try { const x = JSON.parse(v); return Array.isArray(x) ? x : x ? [x] : []; } catch { return v ? [v] : []; }
};

// ================================================================== VOICE
// Speech-to-text: the browser's own recognizer (Chrome / Edge). Free, no key.
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
function dictate(el, btn) {
  if (!SR) { alert("Voice input needs Chrome or Edge. You can type instead."); return; }
  if (btn._rec) { btn._rec.stop(); return; }
  const rec = new SR();
  rec.lang = "en-US"; rec.interimResults = true; rec.continuous = true;
  const base = el.value ? el.value.trim() + " " : "";
  let finalText = "";
  rec.onresult = (e) => {
    let interim = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const t = e.results[i][0].transcript;
      if (e.results[i].isFinal) finalText += t + " "; else interim += t;
    }
    el.value = base + finalText + interim;
    el.dispatchEvent(new Event("input"));
  };
  rec.onerror = (e) => { if (e.error === "not-allowed") alert("Microphone permission was blocked. Allow it in your browser to speak."); };
  rec.onend = () => { btn.classList.remove("rec"); btn.textContent = "🎙"; btn.title = "Speak"; btn._rec = null; };
  btn.classList.add("rec"); btn.textContent = "■"; btn.title = "Stop";
  btn._rec = rec; rec.start();
}
// Every textarea gets a mic, including ones rendered later.
function attachMics(root = document) {
  $$("textarea", root).forEach((ta) => {
    if (ta.dataset.mic) return;
    ta.dataset.mic = "1";
    let wrap = ta.parentElement;
    if (!wrap.classList.contains("field")) {
      wrap = document.createElement("div"); wrap.className = "field";
      ta.parentNode.insertBefore(wrap, ta); wrap.appendChild(ta);
    }
    const b = document.createElement("button");
    b.type = "button"; b.className = "mic"; b.textContent = "🎙"; b.title = SR ? "Speak" : "Voice needs Chrome or Edge";
    if (!SR) b.classList.add("off");
    b.onclick = () => dictate(ta, b);
    wrap.appendChild(b);
  });
}
new MutationObserver(() => attachMics()).observe(document.body, { childList: true, subtree: true });

// Text-to-speech: read a reply aloud. On-device in every major browser.
function speak(btn, text) {
  if (!("speechSynthesis" in window)) { alert("Your browser can't read aloud."); return; }
  if (speechSynthesis.speaking) { speechSynthesis.cancel(); $$(".tts.on").forEach((b) => b.classList.remove("on")); if (btn.classList.contains("on")) return; }
  const u = new SpeechSynthesisUtterance(text);
  u.rate = 0.97;
  u.onend = () => btn.classList.remove("on");
  btn.classList.add("on");
  speechSynthesis.speak(u);
}

// =================================================================== TABS
function go(tab) {
  $$("nav button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  $$(".tab").forEach((s) => s.classList.toggle("on", s.id === tab));
  if (tab === "checkins") loadCheckins();
  if (tab === "fellowship") loadConnections();
  if (tab === "churches") loadChurches();
  if (tab === "settings") { renderSettings(); loadStats(); }
  window.scrollTo({ top: 0 });
}
$$("nav button").forEach((b) => (b.onclick = () => go(b.dataset.tab)));

// ================================================================== CHIPS
function chips(el, items, selected, { multi = true } = {}) {
  el.innerHTML = "";
  items.forEach((it) => {
    const c = document.createElement("span");
    c.className = "chip" + (selected.includes(it.id) ? " on" : "") + (it.seeker ? " seeker" : "");
    c.textContent = it.label; c.dataset.id = it.id;
    c.onclick = () => {
      if (!multi) $$(".chip", el).forEach((x) => x !== c && x.classList.remove("on"));
      c.classList.toggle("on");
    };
    el.appendChild(c);
  });
}
const chosen = (el) => $$(".chip.on", el).map((c) => c.dataset.id);

// ============================================================= BOOTSTRAP
let TRAD_ITEMS = [], THEME_ITEMS = [];
async function boot() {
  BOOT = await api("/api/bootstrap");
  PROFILE = BOOT.profile;
  if (PROFILE) PROFILE.tradition = parseList(PROFILE.tradition);
  TRAD_ITEMS = BOOT.traditions.map((t) => ({ id: t.id, label: t.label, seeker: t.seeker }));
  THEME_ITEMS = BOOT.themes.map((t) => ({ id: t, label: pretty(t) }));
  $("#peopleTheme").innerHTML = '<option value="">Choose a struggle…</option>' +
    BOOT.themes.map((t) => `<option value="${t}">${pretty(t)}</option>`).join("");
  $("#vTrans").innerHTML = BOOT.translations.map((t) => `<option value="${t.code}">${t.code} - ${esc(t.label)}</option>`).join("");
  chips($("#chTrads"), TRAD_ITEMS.filter((t) => !t.seeker), []);
  attachMics();
  if (!PROFILE || !PROFILE.onboarded) startOnboarding();
  else { await loadChat(); refreshDue(); }
}

// ============================================================ ONBOARDING
// The ONLY place Heard asks about faith. Everything is editable in Settings.
let obStep = 0;
const OB_STEPS = 5;
function startOnboarding() {
  const p = PROFILE || {};
  $("#obHandle").value = p.handle || ""; $("#obCity").value = p.city || "";
  chips($("#obTrads"), TRAD_ITEMS, p.tradition || []);
  chips($("#obFighting"), THEME_ITEMS, p.fighting || []);
  chips($("#obOvercame"), THEME_ITEMS, p.overcame || []);
  chips($("#obCadence"), CADENCE, [String(p.checkin_hours || 24)], { multi: false });
  BELIEFS = { ...(p.beliefs || {}) };
  renderTopics($("#obTopics"));
  $("#onboard").classList.remove("hidden");
  document.body.classList.add("locked");
  showStep(0);
}
function showStep(i) {
  obStep = i;
  $$(".ob-step").forEach((s) => s.classList.toggle("on", +s.dataset.step === i));
  $("#obBar").style.width = `${((i + 1) / OB_STEPS) * 100}%`;
  $("#obBack").style.visibility = i === 0 ? "hidden" : "visible";
  $("#obSkip").classList.toggle("hidden", i !== 2);
  $("#obNext").textContent = i === OB_STEPS - 1 ? "Start using Heard" : "Continue";
  $(".ob-card").scrollTop = 0;
}
$("#obBack").onclick = () => showStep(Math.max(0, obStep - 1));
$("#obSkip").onclick = () => showStep(obStep + 1);
$("#obNext").onclick = async () => {
  if (obStep === 0 && !$("#obHandle").value.trim()) { $("#obHandle").focus(); return; }
  if (obStep === 1 && !chosen($("#obTrads")).length && !$("#obTradOther").value.trim()) {
    alert("Pick at least one, or choose \"I am exploring.\""); return;
  }
  if (obStep < OB_STEPS - 1) { showStep(obStep + 1); return; }
  $("#obNext").disabled = true;
  await saveProfile({
    handle: $("#obHandle").value, city: $("#obCity").value,
    tradition: chosen($("#obTrads")), tradition_other: $("#obTradOther").value,
    beliefs: cleanBeliefs(), overcame: chosen($("#obOvercame")), fighting: chosen($("#obFighting")),
    checkin_hours: +(chosen($("#obCadence"))[0] || 24),
    open_to_contact: $("#obOpen").checked, contact_method: $("#obMethod").value,
    contact_value: $("#obContact").value, bio: $("#obBio").value,
  });
  $("#obNext").disabled = false;
  $("#onboard").classList.add("hidden");
  document.body.classList.remove("locked");
  await loadChat(); refreshDue(); go("talk");
};

// ======================================================= BELIEF TOPICS
function renderTopics(box) {
  box.innerHTML = "";
  BOOT.topics.forEach((t) => {
    const cur = BELIEFS[t.id];
    const curPos = cur && typeof cur === "object" ? "own_words" : cur || "prefer_not_to_say";
    const opts = [
      ["position_a", t.position_a], ["position_b", t.position_b],
      ["somewhere_between", "Somewhere in between"], ["still_discerning", "Still discerning"],
      ["own_words", "🎙 Let me say it in my own words"], ["prefer_not_to_say", "Prefer not to say"],
    ];
    const div = document.createElement("div");
    div.className = "topic";
    const nm = box.id + "_" + t.id;
    div.innerHTML = `<div class="q">${esc(t.question)}</div><div class="opts">${opts.map(([v, l]) => `
      <label class="opt ${curPos === v ? "on" : ""}"><input type="radio" name="${nm}" value="${v}" ${curPos === v ? "checked" : ""}> ${esc(l)}</label>`).join("")}
      </div><div class="ownbox"></div>`;
    $$("input", div).forEach((inp) => (inp.onchange = () => {
      $$(".opt", div).forEach((o) => o.classList.toggle("on", o.contains(inp) && inp.checked));
      if (inp.value === "own_words") {
        if (!(BELIEFS[t.id] && typeof BELIEFS[t.id] === "object")) BELIEFS[t.id] = { position: "own_words", approved: false };
      } else BELIEFS[t.id] = inp.value;
      renderOwn(t, $(".ownbox", div), nm);
    }));
    box.appendChild(div);
    renderOwn(t, $(".ownbox", div), nm);
  });
}
// Approval loop: speak/type -> simplify -> show back -> approve or redo.
function renderOwn(t, box, nm) {
  const cur = BELIEFS[t.id];
  if (!(cur && typeof cur === "object" && cur.position === "own_words")) { box.innerHTML = ""; return; }
  if (cur.approved) {
    box.innerHTML = `<div class="confirm"><div class="approved">✓ Approved, in your words</div>
      <div class="said">"${esc(cur.summary)}"</div><button class="ghost small">Change this</button></div>`;
    $("button", box).onclick = () => { cur.approved = false; renderOwn(t, box, nm); };
    return;
  }
  const id = "own_" + nm;
  box.innerHTML = `<div class="own"><div class="field"><textarea id="${id}" rows="3"
      placeholder="Say it however it comes. Heard will restate it and ask if it got it right.">${esc(cur.original || "")}</textarea></div>
      <div class="row"><span class="spacer"></span><button class="primary small go">Check my meaning</button></div>
      <div class="pending"></div></div>`;
  $(".go", box).onclick = async (e) => {
    const btn = e.currentTarget; btn.disabled = true; btn.textContent = "Listening closely…";
    try {
      const r = await api("/api/beliefs/simplify", { topic_id: t.id, text: document.getElementById(id).value });
      cur.original = r.original;
      const pend = $(".pending", box);
      if (r.needs_retry) {
        pend.innerHTML = `<p class="warn">${esc(r.unclear || "I couldn't quite follow that. Try saying it another way?")}</p>`;
      } else {
        pend.innerHTML = `<div class="confirm"><b>Here's what I heard. Is this what you mean?</b>
          <div class="said">"${esc(r.summary)}"</div>
          ${r.nuances?.length ? `<div class="sub">Keeping: ${r.nuances.map(esc).join("; ")}</div>` : ""}
          <div class="row"><button class="primary small yes">Yes, that's right</button>
          <button class="ghost small no">No, let me try again</button></div></div>`;
        $(".yes", pend).onclick = () => { Object.assign(cur, { summary: r.summary, nuances: r.nuances || [], approved: true }); renderOwn(t, box, nm); };
        $(".no", pend).onclick = () => { pend.innerHTML = ""; document.getElementById(id).focus(); };
      }
    } catch (err) { alert("Couldn't reach Heard: " + err.message); }
    btn.disabled = false; btn.textContent = "Check my meaning";
  };
}
function cleanBeliefs() {
  // An own-words belief that was never approved is dropped, never saved.
  const clean = {};
  for (const [k, v] of Object.entries(BELIEFS)) {
    if (v && typeof v === "object") {
      if (v.approved) clean[k] = { position: "own_words", summary: v.summary, original: v.original, nuances: v.nuances || [], approved: true };
    } else if (v) clean[k] = v;
  }
  return clean;
}
async function saveProfile(body) {
  const r = await api("/api/profile", body);
  PROFILE = r.profile; PROFILE.tradition = parseList(PROFILE.tradition);
}

// ============================================================== SETTINGS
function renderSettings() {
  const p = PROFILE || {};
  $("#pfHandle").value = p.handle || ""; $("#pfCity").value = p.city || "";
  $("#pfTradOther").value = p.tradition_other || ""; $("#pfBio").value = p.bio || "";
  $("#pfOpen").checked = !!p.open_to_contact;
  $("#pfMethod").value = p.contact_method || ""; $("#pfContact").value = p.contact_value || "";
  chips($("#pfTrads"), TRAD_ITEMS, p.tradition || []);
  chips($("#pfFighting"), THEME_ITEMS, p.fighting || []);
  chips($("#pfOvercame"), THEME_ITEMS, p.overcame || []);
  chips($("#pfCadence"), CADENCE, [String(p.checkin_hours || 24)], { multi: false });
  BELIEFS = { ...(p.beliefs || {}) };
  renderTopics($("#pfTopics"));
}
async function saveSettings() {
  await saveProfile({
    handle: $("#pfHandle").value, city: $("#pfCity").value,
    tradition: chosen($("#pfTrads")), tradition_other: $("#pfTradOther").value,
    beliefs: cleanBeliefs(), bio: $("#pfBio").value,
    overcame: chosen($("#pfOvercame")), fighting: chosen($("#pfFighting")),
    checkin_hours: +(chosen($("#pfCadence"))[0] || 24),
    open_to_contact: $("#pfOpen").checked, contact_method: $("#pfMethod").value, contact_value: $("#pfContact").value,
  });
  $("#pfSaved").classList.remove("hidden"); setTimeout(() => $("#pfSaved").classList.add("hidden"), 2500);
  refreshDue();
}
async function startOver() {
  if (!confirm("Start over as a new user in this browser? You'll go through setup again.")) return;
  await api("/api/session/reset", {});
  location.reload();
}
async function clearChat() {
  if (!confirm("Clear your Talk history? This can't be undone.")) return;
  await api("/api/chat", undefined, "DELETE"); renderThread([]);
}

// ================================================================== TALK
async function loadChat() {
  const r = await api("/api/chat");
  renderThread(r.messages);
}
function renderThread(msgs) {
  const t = $("#thread");
  if (!msgs.length) {
    t.innerHTML = `<div class="empty"><span class="mark big">H</span>
      <h2>${PROFILE?.handle ? "Hi " + esc(PROFILE.handle) + "." : "Hi."} What's pulling at you?</h2>
      <p class="sub">Tough questions, a temptation right now, a doubt you can't shake. Say it plainly. Heard answers from your tradition, with scripture it has checked.</p></div>`;
    return;
  }
  t.innerHTML = msgs.map(bubble).join("");
  t.lastElementChild?.scrollIntoView({ block: "end" });
}
function bubble(m) {
  if (m.role === "user") return `<div class="bubble me">${paras(m.content)}</div>`;
  const x = m.meta || {};
  const say = [m.content, ...(x.verses || []).map((v) => `${v.text} ${v.ref}.`)].join(" ");
  if (x.mode === "crisis" || x.mode === "out_of_scope") {
    return `<div class="bubble them ${x.mode === "crisis" ? "crisis" : ""}"><h3>${esc(x.headline || "")}</h3>${paras(m.content)}
      ${(x.resources || []).map((r) => `<div class="resource"><b>${esc(r.name)}</b><span>${esc(r.contact)}</span></div>`).join("")}
      ${x.mode === "crisis" ? `<p class="sub tiny">US services. Outside the US, contact your local emergency number.</p>` : ""}</div>`;
  }
  const v = x.verification || {};
  return `<div class="bubble them">
    ${x.safety_note ? `<div class="safety">If anyone is in physical danger right now, get help first: ${(x.resources || []).map((r) => `<b>${esc(r.name)}</b> ${esc(r.contact)}`).join(" · ")}</div>` : ""}
    <div class="bhead">${x.tone ? `<span class="tone ${esc(x.tone)}">${esc(x.tone)}</span>` : ""}
      <button class="tts" title="Read aloud" onclick='speak(this, ${JSON.stringify(say).replace(/'/g, "&#39;")})'>🔊</button></div>
    ${paras(m.content)}
    ${(x.verses || []).map((vv) => `<div class="verse"><q>${esc(vv.text)}</q>
      <div class="ref">${esc(vv.ref)} · ${esc(vv.translation)} <span class="verified">✓ verified</span></div>
      ${vv.why ? `<div class="why">${esc(vv.why)}</div>` : ""}</div>`).join("")}
    ${x.steps?.length ? `<h4>For the next few hours</h4><ul class="steps">${x.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>` : ""}
    ${x.commitment ? `<div class="commitbox" data-entry="${esc(x.entry_id || "")}"><p>"${esc(x.commitment)}"</p>
      <div class="row"><select class="cmDue"><option value="60">Check on me in 1 hour</option><option value="180" selected>in 3 hours</option>
      <option value="720">tomorrow morning</option><option value="0">right away (demo)</option></select>
      <button class="primary small" onclick="commit(this)">Commit</button></div></div>` : ""}
    ${x.prayer ? `<div class="prayer">${esc(x.prayer)}</div>` : ""}
    ${x.human_nudge ? `<p class="sub tiny">👤 ${esc(x.human_nudge)}</p>` : ""}
    ${x.matches?.length ? `<div class="mini-matches"><b>Someone who's been here:</b> ${x.matches.slice(0, 2).map((p) =>
      `<a href="#" onclick="go('fellowship');$('#peopleTheme').value='${esc(x.theme)}';loadMatches();return false">${esc(p.handle)}</a> <span class="sub tiny">(${p.relation === "overcame" ? "came through it" : "fighting it too"})</span>`).join(", ")}</div>` : ""}
    ${v.n_claimed ? `<div class="audit">${v.n_kept} of ${v.n_claimed} citations verified${v.n_removed ? ` · ${v.n_removed} removed` : ""}</div>` : ""}
  </div>`;
}
async function sendChat() {
  const ta = $("#chatText");
  const text = ta.value.trim();
  if (!text) return;
  $$(".mic.rec").forEach((b) => b._rec?.stop());
  ta.value = ""; $("#sendBtn").disabled = true;
  const t = $("#thread");
  if ($(".empty", t)) t.innerHTML = "";
  t.insertAdjacentHTML("beforeend", `<div class="bubble me">${paras(text)}</div>`);
  $("#typing").classList.remove("hidden"); $("#typing").scrollIntoView({ block: "end" });
  try {
    LAST = await api("/api/chat", { text });
    $("#traceBox").textContent = JSON.stringify(LAST.trace, null, 2);
    await loadChat();
    refreshDue();
  } catch (e) {
    t.insertAdjacentHTML("beforeend", `<div class="bubble them"><p class="warn">Something went wrong reaching Heard (${esc(e.message)}). Your message wasn't lost; try again.</p></div>`);
    ta.value = text;
  }
  $("#typing").classList.add("hidden"); $("#sendBtn").disabled = false; ta.focus();
}
$("#chatText").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); }
});
async function commit(btn) {
  const box = btn.closest(".commitbox");
  await api("/api/commitment", { text: $("p", box).textContent.replace(/^"|"$/g, ""), entry_id: box.dataset.entry, due_minutes: +$(".cmDue", box).value });
  $(".row", box).innerHTML = `<span class="ok">✓ Committed. Heard will ask how it went in Check-ins.</span>`;
  refreshDue();
}

// ============================================================ CHECK-INS
async function refreshDue() {
  if (!PROFILE?.onboarded) return;
  try {
    const r = await api("/api/checkins");
    const n = r.struggles.filter((s) => s.due).length + r.due_commitments.length;
    $("#dueBadge").textContent = n; $("#dueBadge").classList.toggle("hidden", !n);
  } catch { /* badge is best-effort */ }
}
function spark(series) {
  if (!series.length) return `<p class="sub tiny">Your trend appears after your first check-in.</p>`;
  const W = 300, H = 70, n = series.length, x = (i) => n === 1 ? W / 2 : 10 + i * (W - 20) / (n - 1), y = (p) => H - 10 - (p - 1) * (H - 20) / 4;
  const color = { overcoming: "#5d7a63", holding: "#b8892f", deeper: "#a8463f" };
  return `<svg class="spark" viewBox="0 0 ${W} ${H}"><text x="2" y="10" class="ax">pull 5</text><text x="2" y="${H - 2}" class="ax">1</text>
    <polyline fill="none" stroke="#cdbfae" stroke-width="2" points="${series.map((s, i) => `${x(i)},${y(s.pull)}`).join(" ")}"/>
    ${series.map((s, i) => `<circle cx="${x(i)}" cy="${y(s.pull)}" r="5" fill="${color[s.trend] || "#999"}"><title>${fmtTime(s.t)} · pull ${s.pull} · ${s.trend}</title></circle>`).join("")}</svg>
    <div class="legend"><span class="d o"></span>overcoming <span class="d h"></span>holding <span class="d x"></span>deeper</div>`;
}
async function loadCheckins() {
  const r = await api("/api/checkins");
  const every = { 24: "every day", 72: "every few days", 168: "once a week" }[r.hours] || `every ${r.hours} hours`;
  $("#ciList").innerHTML = r.struggles.length ? r.struggles.map((s) => {
    const last = s.last ? `<span class="trend ${esc(s.last.trend)}">${esc(s.last.trend)}</span> last time` : "";
    const form = s.due ? `<div class="ciform" data-theme="${esc(s.theme)}">
        ${s.questions.map((q) => q.type === "text"
          ? `<div class="q">${esc(q.q)}</div><div class="field"><textarea name="note" rows="2" placeholder="Type or speak"></textarea></div>`
          : `<div class="q">${esc(q.q)}</div><div class="chips" data-q="${q.id}">${q.options.map((o) => `<span class="chip" data-id="${o.v}">${esc(o.label)}</span>`).join("")}</div>`).join("")}
        <div class="row"><span class="spacer"></span><button class="primary" onclick="submitCheckin(this)">Check in</button></div>
        <div class="ciout"></div></div>`
      : `<p class="sub">Checked in. Next one ${s.next_at ? fmtTime(s.next_at) : "soon"} (${every}).</p>`;
    return `<div class="card"><div class="row" style="margin:0"><h2 style="margin:0">${esc(s.label)}</h2><span class="spacer"></span>${last}</div>
      ${spark(s.series)}${form}</div>`;
  }).join("") : `<div class="card"><p class="sub">Nothing to check in on yet. Add struggles you're fighting in Settings, or talk to Heard and it will start checking in on what comes up.</p></div>`;
  $$(".ciform .chips").forEach((c) => $$(".chip", c).forEach((ch) => (ch.onclick = () => {
    $$(".chip", c).forEach((x) => x.classList.remove("on")); ch.classList.add("on");
  })));
  // Commitments
  const dueIds = new Set(r.due_commitments.map((c) => c.id));
  const open = r.commitments.filter((c) => !dueIds.has(c.id));
  const os = r.outcome_stats, oc = os.counts || {};
  $("#cmList").innerHTML =
    r.due_commitments.map((c) => `<div class="commitbox"><p>"${esc(c.text)}"</p><div class="sub">How did it go?</div>
      <div class="row">${[["held", "I held to it"], ["partial", "Partly"], ["slipped", "I slipped"]].map(([v, l]) =>
        `<button class="${v === "held" ? "primary" : "ghost"} small" onclick="outcome('${c.id}','${v}')">${l}</button>`).join("")}</div></div>`).join("") +
    open.map((c) => `<div class="commitbox muted"><p>"${esc(c.text)}"</p><div class="sub tiny">Heard asks ${fmtTime(c.due)}</div></div>`).join("") +
    (os.total ? `<p class="sub" style="margin-top:12px">So far: <b>${oc.held || 0}</b> held · <b>${oc.partial || 0}</b> partly · <b>${oc.slipped || 0}</b> slipped</p>` : "") ||
    `<p class="sub">No commitments yet. When Heard suggests one in Talk, tap Commit.</p>`;
  refreshDue();
}
async function submitCheckin(btn) {
  const f = btn.closest(".ciform");
  const pick = (q) => $(`.chips[data-q="${q}"] .chip.on`, f)?.dataset.id;
  const body = { theme: f.dataset.theme, pull: +pick("pull"), acted: pick("acted"), direction: pick("direction"), note: $("textarea", f).value };
  if (!body.pull || !body.acted || !body.direction) { alert("Answer the three quick questions first."); return; }
  btn.disabled = true; btn.textContent = "…";
  try {
    const r = await api("/api/checkin", body);
    $("#traceBox").textContent = JSON.stringify(r.trace, null, 2);
    const out = $(".ciout", f);
    if (r.mode === "crisis") {
      out.innerHTML = `<div class="bubble them crisis"><h3>This sounds like more than a check-in should carry.</h3>
        <p>Please reach out to one of these right now. They're free, confidential, and trained for exactly this.</p>
        ${r.resources.map((x) => `<div class="resource"><b>${esc(x.name)}</b><span>${esc(x.contact)}</span></div>`).join("")}</div>`;
      return;
    }
    out.innerHTML = `<div class="ciresult"><span class="trend ${esc(r.trend)}">${esc(r.trend)}</span>
      <p>${esc(r.reply)}</p>
      ${r.escalate ? `<div class="escalate"><b>Two check-ins in a row, it's been pulling you deeper.</b> This is the moment to bring a person in.
        <div class="row"><button class="primary small" onclick="go('fellowship');$('#peopleTheme').value='${esc(r.theme)}';loadMatches()">Find someone who came through it</button>
        <button class="ghost small" onclick="go('churches')">Find a church community</button></div></div>` : ""}
      <button class="ghost small" onclick="loadCheckins()">Done</button></div>`;
    $$(".chips, .q, .field, .row", f).forEach((el) => el.closest(".ciresult") || (el.style.display = "none"));
  } catch (e) { alert("Couldn't save: " + e.message); btn.disabled = false; btn.textContent = "Check in"; }
}
async function outcome(id, result) {
  const note = result === "slipped" ? (prompt("Want to say what happened? (optional) Slipping isn't the end of the story.") || "") : "";
  await api("/api/outcome", { commitment_id: id, result, note });
  loadCheckins();
}

// ============================================================ FELLOWSHIP
function personCard(p) {
  return `<div class="card person"><div class="avatar">${esc((p.handle || "?")[0].toUpperCase())}</div><div style="flex:1">
    <b>${esc(p.handle)}</b>${p.synthetic ? `<span class="demo" title="Demo profile, not a real person">demo</span>` : ""}
    <div class="meta">${esc(p.city || "")}${p.tradition ? " · " + parseList(p.tradition).map(pretty).join(" / ") : ""}</div>
    ${p.bio ? `<p style="margin:6px 0">${esc(p.bio)}</p>` : ""}
    ${(p.overcame || []).map((t) => `<span class="tag">came through ${esc(pretty(t))}</span>`).join("")}
    ${(p.fighting || []).map((t) => `<span class="tag fight">fighting ${esc(pretty(t))}</span>`).join("")}
    ${p.reasons ? `<div class="reasons">${p.reasons.map(esc).join(" · ")}</div>` : ""}
    <div class="row">${p.open_to_contact ? `<button class="primary small" onclick="connectTo('${p.id}', this)">Ask to connect</button>` : `<span class="sub">Not taking contact requests right now</span>`}</div>
  </div></div>`;
}
async function loadMatches() {
  const theme = $("#peopleTheme").value;
  if (!theme) { alert("Choose a struggle first."); return; }
  const r = await api("/api/matches?theme=" + encodeURIComponent(theme));
  $("#peopleList").innerHTML = r.matches.length ? r.matches.map(personCard).join("")
    : `<div class="card"><p class="sub">No one has listed this one yet. As more people join, they'll show up here.</p></div>`;
}
async function loadBrowse() {
  const theme = $("#peopleTheme").value;
  const r = await api("/api/browse" + (theme ? "?theme=" + encodeURIComponent(theme) : ""));
  $("#peopleList").innerHTML = r.people.map(personCard).join("") || `<div class="card"><p class="sub">No profiles yet.</p></div>`;
}
async function connectTo(id, btn) {
  await api("/api/connect", { to_id: id, theme: $("#peopleTheme").value });
  btn.outerHTML = `<span class="ok">Request sent. You'll see their contact once they accept.</span>`;
  loadConnections();
}
async function loadConnections() {
  if (!PROFILE) return;
  const r = await api("/api/connections");
  $("#connList").innerHTML = r.connections.length ? r.connections.map((c) => `
    <div class="row" style="justify-content:space-between"><span><b>${esc(c.other_handle)}</b> · ${esc(pretty(c.theme))} · ${c.direction} · <i>${esc(c.status)}</i></span>
    ${c.direction === "received" && c.status === "pending" ? `<span><button class="primary small" onclick="decide('${c.id}','accepted')">Accept</button>
      <button class="ghost small" onclick="decide('${c.id}','declined')">Decline</button> <button class="ghost small" onclick="decide('${c.id}','blocked')">Block</button></span>` : ""}
    ${c.status === "accepted" ? `<button class="ghost small" onclick="reveal('${c.id}', this)">Show contact</button>` : ""}</div>`).join("")
    : `<p class="sub">No connections yet.</p>`;
}
async function decide(id, status) { await api(`/api/connect/${id}/decide`, { status }); loadConnections(); }
async function reveal(id, btn) {
  try { const r = await api("/api/contact/" + id); btn.outerHTML = `<span class="ok">${esc(r.method)}: ${esc(r.value)}${r.synthetic ? " (demo number)" : ""}</span>`; }
  catch (e) { alert(e.data?.message || "Not available yet."); }
}

// ============================================================== CHURCHES
async function loadChurches() {
  const city = $("#churchCity").value || PROFILE?.city || "";
  if (!$("#churchCity").value && city) $("#churchCity").value = city;
  const r = await api("/api/churches" + (city ? "?city=" + encodeURIComponent(city) : ""));
  $("#churchList").innerHTML = r.churches.length ? r.churches.map((c) => `<div class="card">
    <b>${esc(c.handle)}</b>${c.synthetic ? `<span class="demo">demo</span>` : ""}
    <div class="sub">${esc(c.city || "")} · ${parseList(c.tradition).map(pretty).join(" / ")}</div>
    ${c.bio ? `<p>${esc(c.bio)}</p>` : ""}${c.service_times ? `<div>🕊 ${esc(c.service_times)}</div>` : ""}
    ${c.address ? `<div class="sub">${esc(c.address)}</div>` : ""}
    ${c.website ? `<div><a href="${esc(c.website)}" target="_blank" rel="noopener">${esc(c.website)}</a></div>` : ""}</div>`).join("")
    : `<div class="card"><p class="sub">No churches have registered in ${esc(city || "this area")} yet.</p></div>`;
}
async function registerChurch() {
  const body = { handle: $("#chName").value, city: $("#chCity").value, tradition: chosen($("#chTrads")),
    service_times: $("#chTimes").value, address: $("#chAddr").value, website: $("#chWeb").value, bio: $("#chBio").value };
  try { await api("/api/church", body); alert("Church profile published."); $("#churchCity").value = body.city; loadChurches(); }
  catch { alert("Please add the church name."); }
}

// ======================================================== UNDER THE HOOD
async function loadStats() {
  const s = await api("/api/stats");
  $("#statsBox").innerHTML = `<div class="stats">
    <div class="stat"><b>${s.runs}</b><span>runs recorded</span></div>
    <div class="stat"><b>${s.avg_tokens_per_run.toLocaleString()}</b><span>avg tokens / run</span></div>
    <div class="stat"><b>${s.total_tokens.toLocaleString()}</b><span>total tokens</span></div>
    <div class="stat"><b>$${s.usd_per_run.toFixed(5)}</b><span>est. cost / run</span></div></div>
    <p class="sub" style="margin-top:12px">Provider: ${esc(s.provider)} · model: ${esc(s.model)}</p>
    ${s.price_verified ? "" : `<p class="warn">Token counts are measured. The dollar rate is a placeholder until the real price is configured.</p>`}`;
}
async function lookupVerse() {
  const r = await api(`/api/verse?ref=${encodeURIComponent($("#vRef").value)}&t=${$("#vTrans").value}`);
  $("#vOut").innerHTML = r.found ? `<div class="verse"><q>${esc(r.verse.text)}</q><div class="ref">${esc(r.verse.ref)} · ${esc(r.verse.translation)}</div></div>`
    : `<p class="warn">No such verse in ${esc($("#vTrans").value)}. If a model had cited this, Heard's verifier would have removed it.</p>`;
}

boot();
