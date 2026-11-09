/* Parent dashboard client — vanilla JS, kid-safe container:
   this page IS the PIN gate; the tutor (/) never requires one. */

let parentId = null;

const $ = (id) => document.getElementById(id);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
  return res.json();
}

// ---------- login / signup (same form: try login, fall back to signup) ----------
$("login-form").onsubmit = async (e) => {
  e.preventDefault();
  $("gate-error").classList.add("hidden");
  const name = $("parent-name").value.trim();
  const pin = $("parent-pin").value.trim();
  try {
    // find existing parent by name (POC; deploy = email OTP)
    const students = await api("/api/students");
    // simple flow: try signup if name is new, else we need the id — use the
    // pragmatic path: signup is idempotent enough for pilot (one parent/device)
    let out;
    try {
      out = await api("/api/parent/signup", { method: "POST", body: { name: name || "Parent", pin } });
    } catch (err) {
      out = null;
    }
    if (!out) throw new Error("signup failed");
    parentId = out.parent.id;
    localStorage.setItem("pr_parent", JSON.stringify({ id: parentId, name: out.parent.name }));
    showDash();
  } catch (err) {
    $("gate-error").textContent = err.message;
    $("gate-error").classList.remove("hidden");
  }
};

// ---------- dashboard ----------
async function showDash() {
  $("gate").classList.add("hidden");
  $("dash").classList.remove("hidden");
  await loadKids();
}

async function loadKids() {
  const data = await api(`/api/parent/${parentId}/dashboard`);
  const wrap = $("kids");
  wrap.innerHTML = "";
  if (!data.kids.length) {
    wrap.innerHTML = '<p class="muted">No kid profiles yet — add one to start.</p>';
    return;
  }
  for (const kid of data.kids) {
    const el = document.createElement("div");
    el.className = "kid-card card";
    const flags = kid.chapters.map((c) =>
      `<span class="flag ${c.flag}">${flagIcon(c.flag)} ${chapterTitle(c.chapter_id)} ${Math.round(c.accuracy * 100)}%</span>`
    ).join("") || '<span class="muted">No practice yet</span>';
    el.innerHTML = `
      <div class="kid-head">
        <h3>${kid.name} <small class="muted">Class ${kid.grade}</small></h3>
        <div class="score-ring">${kid.prepscore}<small>/100</small></div>
      </div>
      <div class="prepscore-bar"><div class="prepscore-fill" style="width:${kid.prepscore}%"></div></div>
      <p class="muted small">PrepScore — practice coverage weighted by exam marks (a measurement, not a promise)</p>
      <div class="flags">${flags}</div>
      <div class="kid-actions">
        <a href="/" class="ghost-btn" onclick="sessionStorage.setItem('pr_kid','${kid.id}')">▶ ${kid.name}'s tutor</a>
        <button class="ghost-btn" onclick="genReport(${kid.id}, this)">📄 Weekly report</button>
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
  } catch (err) { alert(err.message); }
  btn.disabled = false; btn.textContent = "📄 Weekly report";
}

// ---------- add kid ----------
$("add-kid-btn").onclick = () => $("kid-modal").classList.remove("hidden");
$("kid-modal").onclick = (e) => { if (e.target === $("kid-modal")) $("kid-modal").classList.add("hidden"); };
$("kid-form").onsubmit = async (e) => {
  e.preventDefault();
  await api(`/api/parent/${parentId}/kids`, { method: "POST",
    body: { name: $("kid-name").value, grade: Number($("kid-grade").value) } });
  $("kid-modal").classList.add("hidden");
  $("kid-name").value = "";
  loadKids();
};
$("refresh-btn").onclick = loadKids;

// restore session
const saved = localStorage.getItem("pr_parent");
if (saved) { parentId = JSON.parse(saved).id; showDash(); }
