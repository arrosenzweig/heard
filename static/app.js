// Heard - client
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pretty = (t) => String(t || "").replace(/^custom:/, "").replace(/_/g, " ");
const paras = (s) => String(s || "").split(/\n+/).filter(Boolean).map((p) => `<p>${esc(p)}</p>`).join("");
const fmtTime = (t) => new Date(t * 1000).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

let BOOT = null, PROFILE = null, LAST = null;
let BELIEFS = {};        // person: {topic: value | {position:'own_words', ...}}
let CH_BELIEFS = {};     // church: {topic: position}
let CUSTOM = [];         // person: [{text, theme, kind}]

const CADENCE = [
  { id: "24", label: "Every day" }, { id: "72", label: "Every few days" },
  { id: "168", label: "Once a week" }, { id: "custom", label: "Custom…" },
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
  // The 🔊 button: same human voice and pacing as voice mode.
  if (!("speechSynthesis" in window)) { alert("Your browser can't read aloud."); return; }
  const wasOn = btn.classList.contains("on");
  stopSpeaking(); $$(".tts.on").forEach((b) => b.classList.remove("on"));
  if (wasOn) return;
  btn.classList.add("on");
  sayAloud(text).then(() => btn.classList.remove("on"));
}

// =================================================================== TABS
function go(tab) {
  $$("nav button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  $$(".tab").forEach((s) => s.classList.toggle("on", s.id === tab));
  if (tab === "checkins") loadCheckins();
  if (tab === "fellowship") goSub(SUB);
  if (tab === "churches") loadChurches();
  if (tab === "listing") renderListing();
  if (tab === "settings") { if (isPerson()) renderSettings(); loadStats(); fillVoicePicker(); }
  window.scrollTo({ top: 0 });
}
$$("nav button").forEach((b) => (b.onclick = () => go(b.dataset.tab)));
const isPerson = () => !PROFILE || PROFILE.kind !== "church";

// Church accounts manage a listing; people use the companion features.
function applyMode() {
  const church = !isPerson();
  $$("nav button[data-for]").forEach((b) => b.classList.toggle("hidden", b.dataset.for !== (church ? "church" : "person")));
  $("#personSettings").classList.toggle("hidden", church);
  $$(".personOnly").forEach((x) => x.classList.toggle("hidden", church));
  go(church ? "listing" : "talk");
}

// ================================================================== CHIPS
function chips(el, items, selected, { multi = true, onChange } = {}) {
  el.innerHTML = "";
  items.forEach((it) => {
    const c = document.createElement("span");
    c.className = "chip" + (selected.includes(it.id) ? " on" : "") + (it.seeker ? " seeker" : "");
    c.textContent = it.label; c.dataset.id = it.id;
    c.onclick = () => {
      if (!multi) $$(".chip", el).forEach((x) => x !== c && x.classList.remove("on"));
      c.classList.toggle("on");
      onChange && onChange();
    };
    el.appendChild(c);
  });
}
const chosen = (el) => $$(".chip.on", el).map((c) => c.dataset.id);

// Check-in cadence: presets, or any number of days.
function cadence(chipsEl, rowEl, daysEl, hours) {
  const preset = ["24", "72", "168"].includes(String(hours)) ? String(hours) : "custom";
  const sync = () => rowEl.classList.toggle("hidden", chosen(chipsEl)[0] !== "custom");
  chips(chipsEl, CADENCE, [preset], { multi: false, onChange: sync });
  if (preset === "custom") daysEl.value = Math.max(1, Math.round((hours || 48) / 24));
  sync();
}
function cadenceHours(chipsEl, daysEl) {
  const v = chosen(chipsEl)[0] || "24";
  if (v !== "custom") return +v;
  const d = Math.max(1, Math.min(60, parseInt(daysEl.value, 10) || 1));
  return d * 24;
}

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
  $("#churchTrad").innerHTML = '<option value="">Any tradition</option>' +
    TRAD_ITEMS.filter((t) => !t.seeker && t.id !== "other").map((t) => `<option value="${t.id}">${esc(t.label)}</option>`).join("");
  $("#fTrad").innerHTML = '<option value="">Any</option>' +
    TRAD_ITEMS.filter((t) => !t.seeker && t.id !== "other").map((t) => `<option value="${t.id}">${esc(t.label)}</option>`).join("");
  $("#peopleTheme").innerHTML = '<option value="">Any struggle</option>' +
    BOOT.themes.map((t) => `<option value="${t}">${pretty(t)}</option>`).join("");
  $("#vTrans").innerHTML = BOOT.translations.map((t) => `<option value="${t.code}">${t.code} - ${esc(t.label)}</option>`).join("");
  attachMics();
  if (!PROFILE || !PROFILE.onboarded) startOnboarding();
  else { applyMode(); if (isPerson()) { await loadChat(); refreshDue(); } }
}

const DETAIL_FIELDS = [["Age", "age"], ["Gender", "gender"], ["Marital", "marital"], ["Years", "faith_years"]];
function fillDetails(prefix, p) {
  DETAIL_FIELDS.forEach(([id, key]) => { const el = document.getElementById(prefix + id); if (el) el.value = p[key] ?? ""; });
}
function readDetails(prefix) {
  const o = {};
  DETAIL_FIELDS.forEach(([id, key]) => { o[key] = document.getElementById(prefix + id)?.value || null; });
  return o;
}
const GENDER_L = { woman: "Woman", man: "Man" };
const MARITAL_L = { single: "Single", dating: "Dating", engaged: "Engaged", married: "Married", separated: "Separated", divorced: "Divorced", widowed: "Widowed" };
const FAITH_L = { exploring: "Still exploring faith", lt1: "Christian under 1 year", "1_5": "Christian 1–5 years", "5_10": "Christian 5–10 years", "10_20": "Christian 10–20 years", "20_plus": "Christian 20+ years" };

// ================================================================ SETUP
// The ONLY place Heard asks about faith. Branches by role on the first screen.
const FLOWS = {
  person: ["role", "p_about", "p_details", "p_faith", "p_topics", "p_carry", "p_checkin"],
  church: ["role", "c_about", "c_faith", "c_support", "c_topics", "c_bio"],
};
let flow = "person", obIdx = 0, topicIdx = 0, editing = false;
const stepName = () => FLOWS[flow][obIdx];
const topicSteps = { p_topics: "#obTopics", c_topics: "#ocTopics" };

function startOnboarding() {
  const p = PROFILE || {};
  // person fields
  $("#obHandle").value = p.handle || ""; $("#obCity").value = p.city || "";
  fillDetails("ob", p);
  chips($("#obTrads"), TRAD_ITEMS, p.tradition || []);
  chips($("#obFighting"), THEME_ITEMS, p.fighting || []);
  chips($("#obOvercame"), THEME_ITEMS, p.overcame || []);
  cadence($("#obCadence"), $("#obDaysRow"), $("#obDays"), p.checkin_hours || 24);
  BELIEFS = { ...(p.beliefs || {}) };
  CUSTOM = [...(p.custom_struggles || [])];
  renderTopics($("#obTopics"));
  renderCustom($("#obCustom"));
  // church fields
  chips($("#ocTrads"), TRAD_ITEMS.filter((t) => !t.seeker), []);
  chips($("#ocMinistries"), THEME_ITEMS, []);
  CH_BELIEFS = {};
  renderChurchTopics();
  $("#onboard").classList.remove("hidden");
  document.body.classList.add("locked");
  flow = "person"; obIdx = 0; topicIdx = 0; editing = false;
  showStep();
}
// Re-open the church flow, filled in, to edit an existing listing.
function editChurch() {
  const c = PROFILE;
  $("#ocName").value = c.handle || ""; $("#ocCity").value = c.city || "";
  $("#ocAddr").value = c.address || ""; $("#ocTimes").value = c.service_times || "";
  $("#ocWeb").value = c.website || ""; $("#ocBio").value = c.bio || "";
  $("#ocSeeker").checked = !!c.seeker_friendly;
  chips($("#ocTrads"), TRAD_ITEMS.filter((t) => !t.seeker), parseList(c.tradition));
  chips($("#ocMinistries"), THEME_ITEMS, parseList(c.ministries));
  CH_BELIEFS = { ...(c.beliefs || {}) };
  renderChurchTopics();
  $("#onboard").classList.remove("hidden"); document.body.classList.add("locked");
  flow = "church"; obIdx = 1; topicIdx = 0; editing = true;
  showStep();
}

function showStep() {
  const name = stepName();
  $$(".ob-step").forEach((s) => s.classList.toggle("on", s.dataset.step === name));
  // one contested question at a time
  const sel = topicSteps[name];
  if (sel) {
    const items = $$(".topic", $(sel));
    items.forEach((t, i) => t.classList.toggle("on", i === topicIdx));
    $(name === "p_topics" ? "#obQCount" : "#ocQCount").textContent = `Question ${topicIdx + 1} of ${items.length}`;
  }
  const steps = FLOWS[flow].length;
  const nTopics = BOOT.topics.length;
  const frac = sel ? topicIdx / nTopics : 0;
  $("#obBar").style.width = `${((obIdx + frac + 1) / steps) * 100}%`;
  $("#obBack").style.visibility = obIdx === 0 || (editing && obIdx === 1) ? "hidden" : "visible";
  $("#obNext").classList.toggle("hidden", name === "role");
  $("#obSkip").classList.toggle("hidden", !sel);
  const last = obIdx === steps - 1;
  $("#obNext").textContent = last ? (flow === "church" ? (editing ? "Save listing" : "Publish our listing") : "Start using Heard")
    : sel ? "Next question" : "Continue";
  if (sel && topicIdx === nTopics - 1) $("#obNext").textContent = "Continue";
  $(".ob-card").scrollTop = 0;
}

$$(".role").forEach((b) => (b.onclick = () => { flow = b.dataset.role; obIdx = 1; topicIdx = 0; showStep(); }));
$("#obBack").onclick = () => {
  if (topicSteps[stepName()] && topicIdx > 0) { topicIdx--; showStep(); return; }
  obIdx = Math.max(0, obIdx - 1);
  if (topicSteps[stepName()]) topicIdx = BOOT.topics.length - 1;
  showStep();
};
$("#obSkip").onclick = () => { topicIdx = 0; obIdx++; showStep(); };
$("#obNext").onclick = async () => {
  const name = stepName();
  if (name === "p_about" && !$("#obHandle").value.trim()) { $("#obHandle").focus(); return; }
  if (name === "p_details") {
    const age = parseInt($("#obAge").value, 10);
    if (!age) { alert("Please add your age. Fellowship connects adults only, so Heard needs it to keep minors safe."); $("#obAge").focus(); return; }
    if (age < (BOOT.options?.min_age || 13)) { alert("Heard is for people 13 and older."); return; }
  }
  if (name === "p_faith" && !chosen($("#obTrads")).length && !$("#obTradOther").value.trim()) {
    alert('Pick at least one, or choose "I am exploring."'); return;
  }
  if (name === "c_about" && !$("#ocName").value.trim()) { $("#ocName").focus(); return; }
  if (topicSteps[name] && topicIdx < BOOT.topics.length - 1) { topicIdx++; showStep(); return; }
  if (obIdx < FLOWS[flow].length - 1) { obIdx++; topicIdx = 0; showStep(); return; }
  $("#obNext").disabled = true;
  try {
    if (flow === "person") await finishPerson(); else await finishChurch();
  } catch (e) { alert("Couldn't save: " + e.message); $("#obNext").disabled = false; return; }
  $("#obNext").disabled = false;
  $("#onboard").classList.add("hidden"); document.body.classList.remove("locked");
  applyMode();
  if (isPerson()) { await loadChat(); refreshDue(); }
};
async function finishPerson() {
  await saveProfile({
    ...readDetails("ob"),
    handle: $("#obHandle").value, city: $("#obCity").value,
    tradition: chosen($("#obTrads")), tradition_other: $("#obTradOther").value,
    beliefs: cleanBeliefs(), overcame: chosen($("#obOvercame")), fighting: chosen($("#obFighting")),
    custom_struggles: CUSTOM, checkin_hours: cadenceHours($("#obCadence"), $("#obDays")),
    open_to_contact: $("#obOpen").checked, contact_method: $("#obMethod").value,
    contact_value: $("#obContact").value, bio: $("#obBio").value,
  });
}
async function finishChurch() {
  const r = await api("/api/church", {
    as_account: true,
    handle: $("#ocName").value, city: $("#ocCity").value, address: $("#ocAddr").value,
    service_times: $("#ocTimes").value, website: $("#ocWeb").value, bio: $("#ocBio").value,
    tradition: chosen($("#ocTrads")), seeker_friendly: $("#ocSeeker").checked,
    ministries: chosen($("#ocMinistries")), beliefs: CH_BELIEFS,
  });
  PROFILE = r.profile; PROFILE.tradition = parseList(PROFILE.tradition);
}

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
// Church positions: one question at a time, same questions as people answer.
function renderChurchTopics() {
  const box = $("#ocTopics");
  box.innerHTML = BOOT.topics.map((t) => {
    const cur = CH_BELIEFS[t.id] || "";
    const opts = [["position_a", t.position_a], ["position_b", t.position_b], ["somewhere_between", "Somewhere in between"], ["", "Not stated"]];
    return `<div class="topic" data-topic="${t.id}"><div class="q">${esc(t.question)}</div><div class="opts">${opts.map(([v, l]) =>
      `<label class="opt ${cur === v ? "on" : ""}"><input type="radio" name="oc_${t.id}" value="${v}" ${cur === v ? "checked" : ""}> ${esc(l)}</label>`).join("")}</div></div>`;
  }).join("");
  $$(".topic", box).forEach((div) => $$("input", div).forEach((inp) => (inp.onchange = () => {
    $$(".opt", div).forEach((o) => o.classList.toggle("on", o.contains(inp) && inp.checked));
    if (inp.value) CH_BELIEFS[div.dataset.topic] = inp.value; else delete CH_BELIEFS[div.dataset.topic];
  })));
}

// =================================================== CUSTOM STRUGGLES
// In the user's own words (typed or spoken). Heard suggests the closest
// category so it can choose scripture and match people; the user confirms it.
function renderCustom(box) {
  const id = box.id + "_text";
  box.innerHTML = `
    <div class="customlist">${CUSTOM.map((cs, i) => `<div class="customitem">
      <div class="said">"${esc(cs.text)}"</div>
      <div class="row" style="margin-top:6px">
        <select data-i="${i}" data-f="kind"><option value="fighting" ${cs.kind === "fighting" ? "selected" : ""}>Fighting now</option>
          <option value="overcame" ${cs.kind === "overcame" ? "selected" : ""}>Came through it</option></select>
        <span class="sub tiny">closest to</span>
        <select data-i="${i}" data-f="theme">${BOOT.themes.map((t) => `<option value="${t}" ${cs.theme === t ? "selected" : ""}>${pretty(t)}</option>`).join("")}</select>
        <button class="ghost small" data-rm="${i}">Remove</button></div></div>`).join("")}</div>
    <div class="field"><textarea id="${id}" rows="2" placeholder="e.g. I keep comparing myself to my friends' lives online"></textarea></div>
    <div class="row"><select class="kindNew"><option value="fighting">I'm fighting this</option><option value="overcame">I came through this</option></select>
      <span class="spacer"></span><button class="primary small addCustom">Add</button></div>
    <div class="customwarn"></div>`;
  $$("select[data-i]", box).forEach((s) => (s.onchange = () => { CUSTOM[+s.dataset.i][s.dataset.f] = s.value; }));
  $$("button[data-rm]", box).forEach((b) => (b.onclick = () => { CUSTOM.splice(+b.dataset.rm, 1); renderCustom(box); }));
  $(".addCustom", box).onclick = async (e) => {
    const text = document.getElementById(id).value.trim();
    if (!text) return;
    const btn = e.currentTarget; btn.disabled = true; btn.textContent = "Understanding…";
    try {
      const r = await api("/api/struggle/classify", { text });
      CUSTOM.push({ text, theme: r.theme, kind: $(".kindNew", box).value });
      renderCustom(box);
      if (r.resources?.length) {
        $(".customwarn", box).innerHTML = `<div class="safety">What you described sounds serious. Please reach out now: ${r.resources.map((x) => `<b>${esc(x.name)}</b> ${esc(x.contact)}`).join(" · ")}</div>`;
      }
    } catch (err) { alert("Couldn't reach Heard: " + err.message); btn.disabled = false; btn.textContent = "Add"; }
  };
}

async function saveProfile(body) {
  const r = await api("/api/profile", body);
  PROFILE = r.profile; PROFILE.tradition = parseList(PROFILE.tradition);
}

// ============================================================== SETTINGS
function renderSettings() {
  const p = PROFILE || {};
  $("#pfHandle").value = p.handle || ""; $("#pfCity").value = p.city || "";
  fillDetails("pf", p);
  $("#pfTradOther").value = p.tradition_other || ""; $("#pfBio").value = p.bio || "";
  $("#pfOpen").checked = !!p.open_to_contact;
  $("#pfMethod").value = p.contact_method || ""; $("#pfContact").value = p.contact_value || "";
  chips($("#pfTrads"), TRAD_ITEMS, p.tradition || []);
  chips($("#pfFighting"), THEME_ITEMS, p.fighting || []);
  chips($("#pfOvercame"), THEME_ITEMS, p.overcame || []);
  cadence($("#pfCadence"), $("#pfDaysRow"), $("#pfDays"), p.checkin_hours || 24);
  BELIEFS = { ...(p.beliefs || {}) };
  CUSTOM = [...(p.custom_struggles || [])];
  renderTopics($("#pfTopics"));
  renderCustom($("#pfCustom"));
}
async function saveSettings() {
  await saveProfile({
    ...readDetails("pf"),
    handle: $("#pfHandle").value, city: $("#pfCity").value,
    tradition: chosen($("#pfTrads")), tradition_other: $("#pfTradOther").value,
    beliefs: cleanBeliefs(), bio: $("#pfBio").value,
    overcame: chosen($("#pfOvercame")), fighting: chosen($("#pfFighting")), custom_struggles: CUSTOM,
    checkin_hours: cadenceHours($("#pfCadence"), $("#pfDays")),
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
  const say = speechFor(m.content, x.verses);
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
      `<a href="#" onclick="go('fellowship');return false">${esc(p.handle)}</a> <span class="sub tiny">(${p.relation === "overcame" ? "came through it" : "fighting it too"})</span>`).join(", ")}</div>` : ""}
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
  if (!PROFILE?.onboarded || !isPerson()) return;
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
function everyLabel(h) {
  return { 24: "every day", 72: "every few days", 168: "once a week" }[h] || `every ${Math.round(h / 24)} days`;
}
async function loadCheckins() {
  const r = await api("/api/checkins");
  const every = everyLabel(r.hours);
  $("#ciList").innerHTML = r.struggles.length ? r.struggles.map((s) => {
    const last = s.last ? `<span class="trend ${esc(s.last.trend)}">${esc(s.last.trend)}</span> last time` : "";
    const form = s.due ? `<div class="row cistart"><button class="primary" onclick="startCheckinChat(this)">Talk it through</button>
        <button class="ghost" onclick="showQuick(this)">Quick check-in</button>
        <span class="sub tiny">Talk it through is a short conversation. Quick is three taps.</span></div>
      <div class="cichat hidden"></div>
      <div class="ciform hidden" data-theme="${esc(s.theme)}">
        ${s.questions.map((q) => q.type === "text"
          ? `<div class="q">${esc(q.q)}</div><div class="field"><textarea name="note" rows="2" placeholder="Type or speak"></textarea></div>`
          : `<div class="q">${esc(q.q)}</div><div class="chips" data-q="${q.id}">${q.options.map((o) => `<span class="chip" data-id="${o.v}">${esc(o.label)}</span>`).join("")}</div>`).join("")}
        <div class="row"><span class="spacer"></span><button class="primary" onclick="submitCheckin(this)">Check in</button></div>
        <div class="ciout"></div></div>`
      : `<p class="sub">Checked in. Next one ${s.next_at ? fmtTime(s.next_at) : "soon"} (${every}).</p>`;
    return `<div class="card cicard" data-theme="${esc(s.theme)}"><div class="row" style="margin:0"><h2 style="margin:0">${esc(s.label)}</h2><span class="spacer"></span>${last}</div>
      ${spark(s.series)}${form}</div>`;
  }).join("") : `<div class="card"><p class="sub">Nothing to check in on yet. Add struggles you're fighting in Settings, or talk to Heard and it will start checking in on what comes up.</p></div>`;
  $$(".ciform .chips").forEach((c) => $$(".chip", c).forEach((ch) => (ch.onclick = () => {
    $$(".chip", c).forEach((x) => x.classList.remove("on")); ch.classList.add("on");
  })));
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
    const mt = r.match_theme || "";
    out.innerHTML = `<div class="ciresult"><span class="trend ${esc(r.trend)}">${esc(r.trend)}</span>
      <p>${esc(r.reply)}</p>
      ${r.escalate ? `<div class="escalate"><b>Two check-ins in a row, it's been pulling you deeper.</b> This is the moment to bring a person in.
        <div class="row"><button class="primary small" onclick="go('fellowship');$('#peopleTheme').value='${esc(mt)}';${mt ? "loadMatches()" : ""}">Find someone who came through it</button>
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
let SUB = "suggested";
$$(".subtabs button").forEach((b) => (b.onclick = () => goSub(b.dataset.sub)));
function goSub(name) {
  SUB = name;
  $$(".subtabs button").forEach((b) => b.classList.toggle("on", b.dataset.sub === name));
  $$("#fellowship .subpanel").forEach((p) => p.classList.toggle("on", p.id === "sub-" + name));
  $("#fFilters").classList.toggle("hidden", name === "connections");
  if (name === "suggested") loadSuggestions();
  if (name === "find") loadBrowse();
  if (name === "connections") loadConnections();
}
// Age gauge: two handles on one track.
function syncAge(e) {
  const lo = $("#fAgeMin"), hi = $("#fAgeMax");
  if (+lo.value > +hi.value) { if (e?.target === lo) hi.value = lo.value; else lo.value = hi.value; }
  $("#fAgeLabel").textContent = `${lo.value} – ${hi.value}${+hi.value === 99 ? "+" : ""}`;
}
["#fAgeMin", "#fAgeMax"].forEach((id) => $(id).addEventListener("input", syncAge));
function filterQuery() {
  const q = new URLSearchParams();
  if ($("#fGender").value) q.set("gender", $("#fGender").value);
  if ($("#fMarital").value) q.set("marital", $("#fMarital").value);
  if ($("#fTrad").value) q.set("tradition", $("#fTrad").value);
  if ($("#fYears").value) q.set("faith_years", $("#fYears").value);
  if (+$("#fAgeMin").value > 18) q.set("age_min", $("#fAgeMin").value);
  if (+$("#fAgeMax").value < 99) q.set("age_max", $("#fAgeMax").value);
  return q;
}
function applyFilters() { goSub(SUB === "connections" ? "suggested" : SUB); }
function resetFilters() {
  ["#fGender", "#fMarital", "#fTrad", "#fYears"].forEach((id) => ($(id).value = ""));
  $("#fAgeMin").value = 18; $("#fAgeMax").value = 99; syncAge(); applyFilters();
}
function showGate(r) {
  const g = $("#fGate");
  if (r.gate) {
    g.innerHTML = `<p>${esc(r.message)}</p>${r.gate === "age_required" ? `<button class="primary small" onclick="go('settings')">Add my age</button>` : ""}`;
    g.classList.remove("hidden"); $("#fFilters").classList.add("hidden");
    return true;
  }
  g.classList.add("hidden");
  return false;
}
function detailLine(p) {
  const bits = [GENDER_L[p.gender], p.age ? String(p.age) : null, MARITAL_L[p.marital], FAITH_L[p.faith_years]].filter(Boolean);
  return bits.length ? `<div class="detailline">${bits.map(esc).join(" · ")}</div>` : "";
}
function personCard(p, forLabel) {
  return `<div class="card person"><div class="avatar">${esc((p.handle || "?")[0].toUpperCase())}</div><div style="flex:1">
    <b>${esc(p.handle)}</b>${p.synthetic ? `<span class="demo" title="Demo profile, not a real person">demo</span>` : ""}
    ${forLabel ? `<span class="forlabel">for ${esc(forLabel)}</span>` : ""}
    <div class="meta">${esc(p.city || "")}${p.tradition ? " · " + parseList(p.tradition).map(pretty).join(" / ") : ""}</div>
    ${detailLine(p)}
    ${p.bio ? `<p style="margin:6px 0">${esc(p.bio)}</p>` : ""}
    ${(p.overcame || []).map((t) => `<span class="tag">came through ${esc(pretty(t))}</span>`).join("")}
    ${(p.fighting || []).map((t) => `<span class="tag fight">fighting ${esc(pretty(t))}</span>`).join("")}
    ${p.reasons ? `<div class="reasons">${p.reasons.map(esc).join(" · ")}</div>` : ""}
    <div class="row">${p.open_to_contact ? `<button class="primary small" onclick="connectTo('${p.id}', this, '${esc(p.for_theme || "")}')">Ask to connect</button>` : `<span class="sub">Not taking contact requests right now</span>`}</div>
  </div></div>`;
}
async function loadSuggestions() {
  if (!isPerson()) return;
  const r = await api("/api/suggestions?" + filterQuery().toString());
  if (showGate(r)) { $("#suggestList").innerHTML = ""; return; }
  $("#fFilters").classList.remove("hidden");
  $("#suggestList").innerHTML = r.suggestions.length
    ? r.suggestions.map((p) => personCard(p, p.for_label)).join("")
    : `<p class="sub">${r.fighting.length ? "No one matches what you're fighting with these filters yet. Try widening them." : "Add the struggles you're fighting in Settings and Heard will suggest people here."}</p>`;
}
async function loadMatches() {
  const theme = $("#peopleTheme").value;
  if (!theme) return loadBrowse();
  const r = await api("/api/matches?" + new URLSearchParams([...filterQuery(), ["theme", theme]]).toString());
  if (showGate(r)) { $("#peopleList").innerHTML = ""; return; }
  $("#peopleList").innerHTML = r.matches.length ? r.matches.map((p) => personCard(p)).join("")
    : `<div class="card"><p class="sub">No one matches that struggle with these filters yet.</p></div>`;
}
async function loadBrowse() {
  const q = filterQuery();
  if ($("#peopleTheme").value) q.set("theme", $("#peopleTheme").value);
  const r = await api("/api/browse?" + q.toString());
  if (showGate(r)) { $("#peopleList").innerHTML = ""; return; }
  $("#peopleList").innerHTML = r.people.map((p) => personCard(p)).join("") || `<div class="card"><p class="sub">No one matches these filters yet.</p></div>`;
}
async function connectTo(id, btn, theme) {
  try { await api("/api/connect", { to_id: id, theme: theme || $("#peopleTheme").value }); }
  catch { alert("That connection isn't available."); return; }
  btn.outerHTML = `<span class="ok">Request sent. You'll see their contact once they accept.</span>`;
  loadConnections();
}
async function loadConnections() {
  if (!PROFILE || !isPerson()) return;
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
function churchCard(c) {
  const m = c.match;
  const badge = m && m.pct != null ? `<div class="matchpct ${m.pct >= 75 ? "hi" : m.pct >= 45 ? "mid" : "lo"}"><b>${m.pct}%</b><span>match</span></div>` : "";
  const ministries = parseList(c.ministries);
  return `<div class="card church ${c.best ? "best" : ""}">
    ${c.best ? `<div class="bestbadge">★ Best match for you</div>` : ""}
    <div class="row" style="margin:0;align-items:flex-start"><div style="flex:1">
      <b>${esc(c.handle)}</b>${c.synthetic ? `<span class="demo">demo</span>` : ""}
      <div class="sub">${esc(c.city || "")} · ${parseList(c.tradition).map(pretty).join(" / ")}</div></div>${badge}</div>
    ${m && m.reasons.length ? `<div class="reasons">${m.reasons.map(esc).join(" · ")}</div>` : ""}
    ${m && m.notes.length ? `<div class="sub tiny">${m.notes.map(esc).join(" · ")}</div>` : ""}
    ${c.bio ? `<p>${esc(c.bio)}</p>` : ""}
    ${ministries.length || c.seeker_friendly ? `<div>${ministries.map((t) => `<span class="tag">support for ${esc(pretty(t))}</span>`).join("")}${c.seeker_friendly ? `<span class="tag">welcomes people exploring faith</span>` : ""}</div>` : ""}
    ${c.service_times ? `<div style="margin-top:8px">🕊 ${esc(c.service_times)}</div>` : ""}
    ${c.address ? `<div class="sub">${esc(c.address)}</div>` : ""}
    ${c.website ? `<div><a href="${esc(c.website)}" target="_blank" rel="noopener">${esc(c.website)}</a></div>` : ""}</div>`;
}
async function loadChurches() {
  const city = $("#churchCity").value || PROFILE?.city || "";
  if (!$("#churchCity").value && city) $("#churchCity").value = city;
  const q = new URLSearchParams();
  if (city) q.set("city", city);
  if ($("#churchTrad").value) q.set("tradition", $("#churchTrad").value);
  const r = await api("/api/churches?" + q.toString());
  $("#matchMethod").textContent = r.method || "";
  $("#churchList").innerHTML = r.churches.length ? r.churches.map(churchCard).join("")
    : `<div class="card"><p class="sub">No churches match that search${city ? " in " + esc(city) : ""} yet.</p></div>`;
}
function renderListing() {
  if (isPerson()) return;
  $("#listingPreview").innerHTML = churchCard({ ...PROFILE, match: null }) +
    `<div class="card"><h3 style="margin-top:0">Where you stand</h3>${Object.keys(PROFILE.beliefs || {}).length
      ? BOOT.topics.filter((t) => (PROFILE.beliefs || {})[t.id]).map((t) => {
          const v = PROFILE.beliefs[t.id];
          return `<p class="sub"><b>${esc(t.question)}</b> ${esc(v === "position_a" ? t.position_a : v === "position_b" ? t.position_b : "Somewhere in between")}</p>`;
        }).join("")
      : `<p class="sub">Not stated. Adding positions helps people with shared convictions find you.</p>`}</div>`;
}

// ======================================================== UNDER THE HOOD
async function loadStats() {
  const s = await api("/api/stats");
  $("#statsBox").innerHTML = `<div class="stats">
    <div class="stat"><b>${s.runs}</b><span>runs recorded</span></div>
    <div class="stat"><b>${s.avg_tokens_per_run.toLocaleString()}</b><span>avg tokens / run</span></div>
    <div class="stat"><b>${s.total_tokens.toLocaleString()}</b><span>total tokens</span></div>
    <div class="stat"><b>$${s.usd_per_run.toFixed(5)}</b><span>est. cost / run</span></div></div>
    ${s.price_verified ? "" : `<p class="warn">Token counts are measured. The dollar rate is a placeholder until the real price is configured.</p>`}`;
}
async function lookupVerse() {
  const r = await api(`/api/verse?ref=${encodeURIComponent($("#vRef").value)}&t=${$("#vTrans").value}`);
  $("#vOut").innerHTML = r.found ? `<div class="verse"><q>${esc(r.verse.text)}</q><div class="ref">${esc(r.verse.ref)} · ${esc(r.verse.translation)}</div></div>`
    : `<p class="warn">No such verse in ${esc($("#vTrans").value)}. If a model had cited this, Heard's verifier would have removed it.</p>`;
}


// ============================================================ VOICE MODE
// Hands-free conversation, like a phone call: listen -> send when you pause ->
// Heard speaks its reply -> listen again. Browser speech only (no key, no
// cost). Listening needs Chrome or Edge; Edge has the most natural voices.
let VOICE = null;   // {kind:'talk'|'checkin', ...} while voice mode is on

// Voice choice. Edge ships Microsoft's neural "Natural" voices; the
// Multilingual ones (Ava, Andrew, Emma, Brian) sound the most human. A voice
// the user picks in Settings always wins.
const VOICE_KEY = "heardVoice";
function englishVoices() {
  return ("speechSynthesis" in window ? speechSynthesis.getVoices() : []).filter((v) => /^en(-|_)/i.test(v.lang));
}
function voiceRank(v) {
  const n = v.name;
  if (/multilingual/i.test(n) && /natural/i.test(n)) return 1;
  if (/natural/i.test(n) && /en-us/i.test(v.lang)) return 2;
  if (/natural/i.test(n)) return 3;
  if (/google us english/i.test(n)) return 4;
  if (/samantha|aria|jenny|guy|allison|ava/i.test(n)) return 5;
  if (/en-us/i.test(v.lang)) return 6;
  return 7;
}
function bestVoice() {
  const vs = englishVoices();
  const saved = localStorage.getItem(VOICE_KEY);
  return vs.find((v) => v.name === saved) || vs.sort((a, b) => voiceRank(a) - voiceRank(b))[0] || null;
}
if ("speechSynthesis" in window) speechSynthesis.onvoiceschanged = () => { bestVoice(); fillVoicePicker(); };

// Speaking like a person: sentence by sentence, with short pauses between
// sentences and longer ones between paragraphs, at a slightly unhurried pace.
// (Chunking also avoids Chrome cutting off long utterances.)
let SPEAK_TOKEN = 0;
function stopSpeaking() { SPEAK_TOKEN++; if ("speechSynthesis" in window) speechSynthesis.cancel(); }
function chunks(text) {
  const out = [];
  String(text || "").split(/\n\s*\n|\n/).map((p) => p.trim()).filter(Boolean).forEach((para, pi) => {
    const sents = para.match(/[^.!?…]+[.!?…]+["”’)\]]*|[^.!?…]+$/g) || [para];
    sents.map((x) => x.trim()).filter(Boolean).forEach((x, si) => out.push({ text: x, pause: si === 0 && pi > 0 ? 450 : 170 }));
  });
  return out;
}
const RATE_KEY = "heardRate";
const savedRate = () => Math.min(1.3, Math.max(0.7, parseFloat(localStorage.getItem(RATE_KEY)) || 0.95));
function sayAloud(text, opts = {}) {
  return new Promise(async (resolve) => {
    if (!("speechSynthesis" in window) || !text) return resolve();
    stopSpeaking();
    const token = SPEAK_TOKEN;
    const v = opts.voice || bestVoice();
    const rate = opts.rate || savedRate();
    for (const c of chunks(text)) {
      if (token !== SPEAK_TOKEN) return resolve();
      await new Promise((r) => setTimeout(r, c.pause));
      await new Promise((r) => {
        const u = new SpeechSynthesisUtterance(c.text);
        if (v) { u.voice = v; u.lang = v.lang; }
        u.rate = rate; u.pitch = 1.0;
        u.onend = r; u.onerror = r;
        speechSynthesis.speak(u);
      });
    }
    resolve();
  });
}

// "Psalms 23:4" -> "Psalm 23, verse 4"; "I Corinthians 10:13" -> "First Corinthians, chapter 10, verse 13"
function spokenRef(ref) {
  const m = String(ref).match(/^(.*?)\s+(\d+):(\d+)/);
  if (!m) return ref;
  let book = m[1].replace(/^III\s/, "Third ").replace(/^II\s/, "Second ").replace(/^I\s/, "First ")
    .replace(/^3\s/, "Third ").replace(/^2\s/, "Second ").replace(/^1\s/, "First ")
    .replace(/^Revelation of John$/, "Revelation");
  if (/^Psalms?$/.test(book)) return `Psalm ${m[2]}, verse ${m[3]}`;
  return `${book}, chapter ${m[2]}, verse ${m[3]}`;
}
// What Heard says aloud for one reply: the reflection, then verses introduced
// the way a friend would share them.
function speechFor(content, verses) {
  const parts = [content];
  (verses || []).forEach((v, i) => {
    parts.push(`${i === 0 ? "Here's a verse for you" : "And this one"}, from ${spokenRef(v.ref)}.`);
    parts.push(v.text.replace(/[“”]/g, '"'));
  });
  return parts.filter(Boolean).join("\n\n");
}

// Settings: listen to each voice, choose one, and set the speaking speed.
const SAMPLE = "Hi, I'm Heard. Whatever you're carrying today, you don't have to carry it alone. Tell me what's on your heart.";
let SHOW_ALL_VOICES = false;
function voiceLabel(v) {
  // "Microsoft Ava Online (Natural) - English (United States)" -> "Ava · Natural · United States"
  const name = v.name.replace(/^Microsoft\s+/i, "").replace(/^Google\s+/i, "Google ");
  const m = name.match(/^(.*?)\s*(Online)?\s*(\(Natural\))?\s*-\s*(.*)$/i);
  if (!m) return name;
  const region = (m[4].match(/\((.*)\)/) || [, m[4]])[1];
  return `${m[1].trim().replace(/Multilingual$/, " (Multilingual)")}${m[3] ? " · Natural" : ""} · ${region}`;
}
function renderVoiceList() {
  const box = document.getElementById("voiceList");
  if (!box) return;
  const all = englishVoices().sort((a, b) => voiceRank(a) - voiceRank(b));
  const natural = all.filter((v) => voiceRank(v) <= 3);
  const shown = SHOW_ALL_VOICES || natural.length === 0 ? all : natural;
  const cur = bestVoice()?.name;
  $("#voiceMore").textContent = SHOW_ALL_VOICES ? "Show natural voices only" : `Show all voices (${all.length})`;
  $("#voiceMore").classList.toggle("hidden", natural.length === 0 || natural.length === all.length);
  box.innerHTML = shown.length ? shown.map((v, i) => `<div class="voicerow ${v.name === cur ? "sel" : ""}">
      <div class="vname">${voiceRank(v) <= 3 ? "★ " : ""}${esc(voiceLabel(v))}</div>
      <button class="ghost small" data-listen="${i}">▶ Listen</button>
      ${v.name === cur ? `<span class="ok">✓ Selected</span>` : `<button class="primary small" data-pick="${i}">Select</button>`}
    </div>`).join("") : `<p class="sub">This browser has no English voices available.</p>`;
  $$("button[data-listen]", box).forEach((b) => (b.onclick = () => sayAloud(SAMPLE, { voice: shown[+b.dataset.listen] })));
  $$("button[data-pick]", box).forEach((b) => (b.onclick = () => { pickVoice(shown[+b.dataset.pick].name); renderVoiceList(); }));
}
function toggleAllVoices() { SHOW_ALL_VOICES = !SHOW_ALL_VOICES; renderVoiceList(); }
function pickVoice(name) { localStorage.setItem(VOICE_KEY, name); }
function rateWord(r) { return r < 0.85 ? "Slower" : r < 0.93 ? "A little slower" : r <= 1.04 ? "Natural" : r <= 1.15 ? "A little faster" : "Faster"; }
function renderRate() {
  const el = document.getElementById("voiceRate");
  if (!el) return;
  el.value = savedRate();
  const show = () => ($("#rateLabel").textContent = `${rateWord(+el.value)} (${(+el.value).toFixed(2)}×)`);
  el.oninput = () => { localStorage.setItem(RATE_KEY, el.value); show(); };
  show();
}
function previewRate() { sayAloud(SAMPLE, { rate: +$("#voiceRate").value }); }
function fillVoicePicker() { renderVoiceList(); renderRate(); }
function listenOnce() {
  // Resolves with what was said, ending automatically at a natural pause.
  return new Promise((resolve, reject) => {
    if (!SR) return reject(new Error("Voice mode needs Chrome or Edge."));
    const rec = new SR();
    rec.lang = "en-US"; rec.interimResults = true; rec.continuous = false;
    let finalText = "";
    rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        if (e.results[i].isFinal) finalText += e.results[i][0].transcript + " ";
        else interim += e.results[i][0].transcript;
      }
      if (VOICE?.onInterim) VOICE.onInterim(finalText + interim);
    };
    rec.onerror = (e) => (e.error === "no-speech" ? resolve("") : reject(new Error(e.error)));
    rec.onend = () => resolve(finalText.trim());
    if (VOICE) VOICE.rec = rec;
    rec.start();
  });
}
async function voiceLoop() {
  // Runs until VOICE is cleared. Two silent rounds in a row end it.
  let silent = 0;
  while (VOICE) {
    VOICE.setState("Listening…");
    let said = "";
    try { said = await listenOnce(); } catch (e) { alert(e.message); stopVoice(); return; }
    if (!VOICE) return;
    if (!said) { if (++silent >= 2) { stopVoice(); return; } continue; }
    silent = 0;
    VOICE.setState("Thinking…");
    const reply = await VOICE.send(said);
    if (!VOICE) return;
    if (reply?.stop) { VOICE.setState("Speaking…"); await sayAloud(reply.text); stopVoice(); return; }
    VOICE.setState("Speaking…");
    await sayAloud(reply?.text || "");
  }
}
function stopVoice() {
  if (!VOICE) return;
  try { VOICE.rec?.abort(); } catch { /* already stopped */ }
  stopSpeaking();
  const v = VOICE; VOICE = null; v.onStop?.();
}

// Talk: voice mode
function toggleTalkVoice() {
  if (VOICE) { stopVoice(); return; }
  if (!SR) { alert("Voice mode needs Chrome or Edge. Edge has the most natural voices."); return; }
  const btn = $("#talkVoice"), st = $("#talkVoiceState");
  btn.classList.add("rec"); btn.textContent = "■ End voice mode";
  VOICE = {
    kind: "talk",
    setState: (s) => (st.textContent = s),
    onInterim: (t) => ($("#chatText").value = t),
    send: async (text) => {
      $("#chatText").value = text;
      await sendChat();
      const r = await api("/api/chat");
      const last = r.messages.filter((m) => m.role === "assistant").at(-1);
      if (!last) return { text: "" };
      const x = last.meta || {};
      if (x.mode === "crisis") {
        return { stop: true, text: `${x.headline || ""} ${last.content} ${(x.resources || []).map((r) => r.name + ". " + r.contact + ".").join(" ")}` };
      }
      return { text: speechFor(last.content, x.verses) };
    },
    onStop: () => { btn.classList.remove("rec"); btn.textContent = "🎧 Voice mode"; st.textContent = "Enter to send · Shift+Enter for a new line"; },
  };
  voiceLoop();
}

// ===================================================== CHECK-IN CONVERSATION
const PULL_WORD = { 1: "barely there", 2: "noticeable", 3: "strong", 4: "very strong", 5: "overwhelming" };
const ACT_WORD = { none: "didn't act on it", once: "acted on it once", several: "acted on it more than once" };
const DIR_WORD = { stronger: "getting stronger", holding: "holding steady", slipping: "slipping" };

function showQuick(btn) {
  const card = btn.closest(".cicard");
  $(".cistart", card).classList.add("hidden");
  $(".ciform", card).classList.remove("hidden");
}
async function startCheckinChat(btn) {
  const card = btn.closest(".cicard"), theme = card.dataset.theme, box = $(".cichat", card);
  $(".cistart", card).classList.add("hidden");
  box.classList.remove("hidden");
  box.innerHTML = `<div class="cithread"></div>
    <div class="field"><textarea rows="2" placeholder="Answer however you like. Type, or tap the mic."></textarea></div>
    <div class="row"><button class="ghost voicebtn">🎧 Voice mode</button><span class="sub tiny vstate"></span>
      <span class="spacer"></span><button class="primary send">Send</button></div>
    <div class="cidone"></div>`;
  box._msgs = [];
  box._theme = theme;
  const thread = $(".cithread", box);
  thread.innerHTML = `<div class="bubble them typing"><span></span><span></span><span></span></div>`;
  const o = await api("/api/checkin/start", { theme });
  box._msgs.push({ role: "assistant", content: o.text });
  thread.innerHTML = `<div class="bubble them">${paras(o.text)}</div>`;
  $(".send", box).onclick = () => checkinSend(box);
  $("textarea", box).addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); checkinSend(box); } });
  $(".voicebtn", box).onclick = () => toggleCheckinVoice(box, o.text);
}
async function checkinSend(box, spoken) {
  const ta = $("textarea", box);
  const text = (spoken ?? ta.value).trim();
  if (!text || box._done) return null;
  ta.value = "";
  const thread = $(".cithread", box);
  box._msgs.push({ role: "user", content: text });
  thread.insertAdjacentHTML("beforeend", `<div class="bubble me">${paras(text)}</div><div class="bubble them typing"><span></span><span></span><span></span></div>`);
  const r = await api("/api/checkin/chat", { theme: box._theme, messages: box._msgs });
  $(".typing", thread)?.remove();
  $("#traceBox").textContent = JSON.stringify(r.trace, null, 2);
  if (r.mode === "crisis") {
    box._done = true;
    const say = "This sounds like more than a check-in should carry. Please reach out to one of these right now. " + r.resources.map((x) => x.name + ". " + x.contact + ".").join(" ");
    thread.insertAdjacentHTML("beforeend", `<div class="bubble them crisis"><h3>This sounds like more than a check-in should carry.</h3>
      <p>Please reach out to one of these right now. They're free, confidential, and trained for exactly this.</p>
      ${r.resources.map((x) => `<div class="resource"><b>${esc(x.name)}</b><span>${esc(x.contact)}</span></div>`).join("")}</div>`);
    return { stop: true, text: say };
  }
  box._msgs.push({ role: "assistant", content: r.reply });
  thread.insertAdjacentHTML("beforeend", `<div class="bubble them">
    ${r.safety_note ? `<div class="safety">If anyone is in physical danger right now, get help first: ${r.resources.map((x) => `<b>${esc(x.name)}</b> ${esc(x.contact)}`).join(" · ")}</div>` : ""}
    ${paras(r.reply)}</div>`);
  if (r.done) { box._done = true; showProposed(box, r); return { stop: true, text: r.reply + " Here's how I'd log this. Tell me if it's right." }; }
  return { text: r.reply };
}
// "Did I hear you right?" before anything is logged.
function showProposed(box, r) {
  const p = r.proposed;
  box._closing = r.reply;
  $(".cidone", box).innerHTML = `<div class="confirm"><b>Here's how I'd log this. Is it right?</b>
    <div class="said">Pull ${p.pull}/5 (${PULL_WORD[p.pull]}) · ${ACT_WORD[p.acted]} · ${DIR_WORD[p.direction]}</div>
    <div class="row"><button class="primary small yes">Yes, log it</button><button class="ghost small adjust">Adjust</button></div>
    <div class="adjustbox hidden">
      <div class="q">Pull</div><div class="chips" data-q="pull">${[1, 2, 3, 4, 5].map((k) => `<span class="chip ${k === p.pull ? "on" : ""}" data-id="${k}">${k} · ${PULL_WORD[k]}</span>`).join("")}</div>
      <div class="q">Did you act on it?</div><div class="chips" data-q="acted">${Object.entries(ACT_WORD).map(([k, v]) => `<span class="chip ${k === p.acted ? "on" : ""}" data-id="${k}">${v}</span>`).join("")}</div>
      <div class="q">Heading</div><div class="chips" data-q="direction">${Object.entries(DIR_WORD).map(([k, v]) => `<span class="chip ${k === p.direction ? "on" : ""}" data-id="${k}">${v}</span>`).join("")}</div>
      <div class="row"><button class="primary small save">Log it</button></div></div></div>`;
  $$(".adjustbox .chips", box).forEach((c) => $$(".chip", c).forEach((ch) => (ch.onclick = () => { $$(".chip", c).forEach((x) => x.classList.remove("on")); ch.classList.add("on"); })));
  const pick = (q) => $(`.adjustbox .chips[data-q="${q}"] .chip.on`, box)?.dataset.id;
  $(".yes", box).onclick = () => logConversation(box, p);
  $(".adjust", box).onclick = () => $(".adjustbox", box).classList.remove("hidden");
  $(".save", box).onclick = () => logConversation(box, { pull: +pick("pull"), acted: pick("acted"), direction: pick("direction") });
}
async function logConversation(box, vals) {
  const note = box._msgs.filter((m) => m.role === "user").map((m) => m.content).join("\n");
  const r = await api("/api/checkin", { theme: box._theme, ...vals, note, closing: box._closing, conversation: true });
  const mt = r.match_theme || "";
  $(".cidone", box).innerHTML = `<div class="ciresult"><span class="trend ${esc(r.trend)}">${esc(r.trend)}</span> <span class="sub">Logged.</span>
    ${r.escalate ? `<div class="escalate"><b>Two check-ins in a row, it's been pulling you deeper.</b> This is the moment to bring a person in.
      <div class="row"><button class="primary small" onclick="go('fellowship')">Find someone who came through it</button>
      <button class="ghost small" onclick="go('churches')">Find a church community</button></div></div>` : ""}
    <div class="row"><button class="ghost small" onclick="loadCheckins()">Done</button></div></div>`;
  $(".field", box)?.classList.add("hidden"); $(".row", box)?.classList.add("hidden");
  refreshDue();
}
function toggleCheckinVoice(box, opener) {
  const btn = $(".voicebtn", box), st = $(".vstate", box);
  if (VOICE) { stopVoice(); return; }
  if (!SR) { alert("Voice mode needs Chrome or Edge. Edge has the most natural voices."); return; }
  btn.classList.add("rec"); btn.textContent = "■ End voice mode";
  VOICE = {
    kind: "checkin",
    setState: (s) => (st.textContent = s),
    onInterim: (t) => ($("textarea", box).value = t),
    send: (text) => checkinSend(box, text),
    onStop: () => { btn.classList.remove("rec"); btn.textContent = "🎧 Voice mode"; st.textContent = ""; },
  };
  // Speak Heard's opening question first, so it feels like a conversation.
  (async () => { if (box._msgs.length === 1) { st.textContent = "Speaking…"; await sayAloud(opener); } if (VOICE) voiceLoop(); })();
}

boot();
