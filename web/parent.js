/* Parent dashboard client — vanilla JS, kid-safe container:
   this page IS the PIN gate; the tutor (/) never requires one. */

let parentId = null;
let authToken = null;

const $ = (id) => document.getElementById(id);

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  if (authToken) headers["Authorization"] = "Bearer " + authToken;
  const res = await fetch(path, {
    headers,
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    // a dead session token (server reset wiped auth_sessions) must fall
    // back to the sign-in gate, not strand a half-loaded dashboard
    if (res.status === 401 && authToken) {
      localStorage.removeItem("pr_parent");
      localStorage.removeItem("pr_last_kid");
      location.reload();
      return new Promise(() => {});   // page is navigating away
    }
    const body = await res.json().catch(() => ({}));
    let d = body.detail;
    if (Array.isArray(d)) d = d.map((e) => e.msg).join("; ");   // 422 arrays
    if (typeof d === "object" && d) d = JSON.stringify(d);      // safety net
    throw new Error(d || `HTTP ${res.status}`);
  }
  return res.json();
}

// ---------- login / signup (same form: try login, fall back to signup) ----------
$("login-form").onsubmit = async (e) => {
  e.preventDefault();
  $("gate-error").classList.add("hidden");
  const name = $("parent-name").value.trim();
  const pin = $("parent-pin").value.trim();
  try {
    // signup is find-or-create (idempotent): existing name + matching PIN
    // logs in, a new name registers. One parent per device is the norm.
    const out = await api("/api/parent/signup",
      { method: "POST", body: { name: name || "Parent", pin } });
    parentId = out.parent.id;
    authToken = out.token || null;
    localStorage.setItem("pr_parent", JSON.stringify({ id: parentId, name: out.parent.name, token: authToken }));
    showDash();
  } catch (err) {
    let msg = err.message;
    if (msg.includes("wrong PIN")) msg = "That PIN doesn’t match this name. Try again or use a new name.";
    else if (msg.includes("4") && msg.includes("digit")) msg = "PIN must be exactly 4 digits.";
    else if (msg.includes("session")) msg = "Session expired — just sign in again.";
    $("gate-error").textContent = msg;
    $("gate-error").classList.remove("hidden");
  }
};

// ---------- dashboard ----------
async function showDash() {
  $("gate").classList.add("hidden");
  $("dash").classList.remove("hidden");
  document.getElementById("signout-btn").style.display = "";
  await loadKids();
}

document.getElementById("signout-btn").onclick = () => {
  localStorage.removeItem("pr_parent");
  localStorage.removeItem("pr_last_kid");
  authToken = null;
  parentId = null;
  location.reload();
};

async function loadKids() {
  let data;
  try {
    data = await api(`/api/parent/${parentId}/dashboard`);
  } catch (err) {
    $("kids").innerHTML = '<p class="muted">Could not load: ' + err.message + '</p>';
    return;
  }
  const wrap = $("kids");
  wrap.innerHTML = "";
  if (!data.kids.length) {
    wrap.innerHTML = '<div class="card" style="text-align:center;padding:40px"><h3>Add your first kid to start</h3><p class="muted">PrepRight needs at least one kid profile before you can start tutoring.</p></div>';
    return;
  }
  for (const kid of data.kids) {
    const el = document.createElement("div");
    el.className = "kid-card card";
    const flags = kid.chapters.map((c) =>
      `<span class="flag ${c.flag}">${flagIcon(c.flag)} ${chapterTitle(c.chapter_id)} ${Math.round(c.accuracy * 100)}%</span>`
    ).join("") || '<span class="muted">No practice yet</span>';
    const scoreColor = kid.prepscore >= 70 ? "var(--accent)" : kid.prepscore >= 40 ? "#f5a623" : "#ff6b6b";
    el.innerHTML = `
      <div class="kid-head">
        <div class="kid-identity">
          <span class="kid-avatar">${kid.name[0].toUpperCase()}</span>
          <div><h3>${kid.name}</h3><small class="muted">Class ${kid.grade} · CBSE</small></div>
        </div>
        <div class="score-display">
          <span class="score-num" style="color:${scoreColor}">${kid.prepscore}</span>
          <span class="score-label">PrepScore</span>
        </div>
      </div>
      <div class="prepscore-bar"><div class="prepscore-fill" style="width:${kid.prepscore}%;background:${scoreColor}"></div></div>
      <div class="flags">${flags}</div>
      <div class="kid-actions">
        <a href="/" class="action-btn primary" onclick="sessionStorage.setItem('pr_kid','${kid.id}')">Start tutoring</a>
        <button class="action-btn" onclick="genReport(${kid.id}, this)">Weekly report</button>
        <button class="action-btn danger" onclick="deleteKid(${kid.id}, '${kid.name}')" title="Remove profile">Remove</button>
      </div>
      <div class="report hidden" id="report-${kid.id}"></div>`;
    wrap.appendChild(el);
  }
}

function flagIcon(f) { return f === "strong" ? "●" : f === "struggling" ? "▲" : "◐"; }
function chapterTitle(id) {
  const map = { "g10-c-quadratic-eq": "Quadratics", "g10-c-trigonometry": "Trigonometry",
    "g10-c-real-numbers": "Real Numbers", "g10-c-polynomials": "Polynomials",
    "g10-c-statistics": "Statistics", "g10-c-probability": "Probability",
    "g10-c-intro-trigonometry": "Trigonometry", "g10-c-arithmetic-progressions": "AP" };
  return map[id] || id.replace(/^g\d+-c-/, "").replace(/-/g, " ");
}

async function genReport(kidId, btn) {
  btn.disabled = true; btn.textContent = "Generating…";
  try {
    const r = await api(`/api/parent/${parentId}/report/${kidId}`, { method: "POST" });
    const el = $(`report-${kidId}`);
    el.classList.remove("hidden");
    el.innerHTML = `<h4>Week ${r.week} — PrepScore ${r.prepscore}/100</h4>
      <p><strong>${r.report.headline}</strong></p><p>${r.report.summary}</p>
      <p><strong>Wins:</strong> ${r.report.wins.join(" · ")}</p>
      <p><strong>Focus:</strong> ${r.report.focus_areas.join(" · ")}</p>
      <p><strong>Try this:</strong> ${r.report.suggested_activity}</p>`;
  } catch (err) { btn.textContent = "Failed: " + err.message; setTimeout(function(){ btn.disabled = false; btn.textContent = "Weekly report"; }, 3000); }
  btn.disabled = false; btn.textContent = "📄 Weekly report";
}

// ---------- add kid ----------
$("add-kid-btn").onclick = () => $("kid-modal").classList.remove("hidden");
$("kid-modal").onclick = (e) => { if (e.target === $("kid-modal")) $("kid-modal").classList.add("hidden"); };
$("kid-form").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await api(`/api/parent/${parentId}/kids`, { method: "POST",
      body: { name: $("kid-name").value, grade: Number($("kid-grade").value) } });
    $("kid-modal").classList.add("hidden");
    $("kid-name").value = "";
    loadKids();
  } catch (err) {
    $("pin-error").textContent = "Could not add kid: " + err.message;
  }
};
$("refresh-btn").onclick = loadKids;

// restore session — only if a valid token exists (old sessions without
// tokens came from before auth was added and must re-login)
const saved = localStorage.getItem("pr_parent");
if (saved) {
  const s = JSON.parse(saved);
  if (s.token) {
    parentId = s.id;
    authToken = s.token;
    showDash();
  } else {
    // stale session (no token) — clear and show login
    localStorage.removeItem("pr_parent");
  }
}


async function deleteKid(kidId, name) {
  if (!confirm(`Remove ${name}'s profile and all their progress? This cannot be undone.`)) return;
  await api(`/api/parent/${parentId}/kids/${kidId}`, { method: "DELETE" });
  loadKids();
}
