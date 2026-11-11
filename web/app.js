/* PrepRight chat client — vanilla JS, no dependencies.
 *
 * STATE MODEL: the SERVER owns conversation history (LangGraph
 * checkpointer, one thread per student). We send only the NEW message and
 * render whatever streams back. Note: visible bubbles reset on reload,
 * but the tutor's memory does not - it resumes from the checkpoint.
 */

const TOOL_LABELS = {
  generate_quiz: "Generating a verified quiz...",
  get_curriculum: "Checking your syllabus...",
  get_student_profile: "Reviewing your progress...",
  record_progress: "Saving your progress...",
  grade_answer: "Checking your answer...",
};

const els = {
  onboarding: document.getElementById("onboarding"),
  obForm: document.getElementById("onboarding-form"),
  obName: document.getElementById("ob-name"),
  obGrade: document.getElementById("ob-grade"),
  chat: document.getElementById("chat"),
  messages: document.getElementById("messages"),
  status: document.getElementById("status"),
  composer: document.getElementById("composer"),
  input: document.getElementById("input"),
  send: document.getElementById("send"),
};

let studentId = null;
let allKids = [];
let studentName = "";
  let streaming = false;
  let pendingQuiz = null;   // buffered until intro text streams

// ---------- onboarding ----------

async function loadStudents() {
  // AUTH SCOPING: only THIS parent's kids. UX rules:
  // 1 kid -> straight into chat; 2+ -> profile CARDS (kids tap, not dropdowns);
  // returning -> last kid remembered; Switch button always available.
  const saved = localStorage.getItem("pr_parent");
  if (!saved) { location.href = "/parent.html"; return; }
  const parent = JSON.parse(saved);
  const students = await (await fetch(`/api/parent/${parent.id}/kids`)).json();
  allKids = students;   // kept for the PIN-gated switcher

  const picked = sessionStorage.getItem("pr_kid");       // deep-link from dashboard
  if (picked) {
    sessionStorage.removeItem("pr_kid");
    const kid = students.find((s) => s.id === Number(picked));
    if (kid) return enterChat(kid.id, kid.name);
  }
  // hide Switch when there's nothing to switch to (found live: one-kid
  // users clicked a button that correctly did nothing, and correctly felt broken)
  if (students.length === 1) return enterChat(students[0].id, students[0].name);
  // SHARED DEVICE RULE: with 2+ kids, ALWAYS show the picker on open.
  // "Remember last kid" would auto-enter the wrong sibling's chat when
  // they share a device at different times - one tap beats wrong memory.
  // Single-kid families skip the picker entirely (zero taps).
  showPicker(students);
}

function showPicker(students) {
  document.getElementById("chat").classList.add("hidden");
  const picker = document.getElementById("picker");
  picker.classList.remove("hidden");
  const cards = document.getElementById("kid-cards");
  cards.innerHTML = "";
  students.forEach((s) => {
    const b = document.createElement("button");
    b.className = "kid-card-btn";
    b.innerHTML = `<span class="avatar">${s.name[0].toUpperCase()}</span>
                   <span>${s.name}</span><small>Class ${s.grade}</small>`;
    b.onclick = () => enterChat(s.id, s.name);
    cards.appendChild(b);
  });
}

els.obForm.onsubmit = async (e) => {
  e.preventDefault();
  const res = await fetch("/api/students", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: els.obName.value, grade: Number(els.obGrade.value) }),
  });
  if (!res.ok) return alert("Could not create student");
  const s = await res.json();
  els.onboarding.classList.add("hidden");
  await loadStudents();
  enterChat(s.id, s.name);
};

function enterChat(id, name) {
  localStorage.setItem("pr_last_kid", id);
  const bar = document.getElementById("picker");
  if (bar) bar.classList.add("hidden");
  // avatar chip: unambiguous who's studying (visual + text + class)
  const kid = allKids.find((s) => s.id === id);
  // standard navbar: avatar + name only; class lives in chat context, not chrome
  document.getElementById("current-kid").innerHTML =
    `<span class="avatar-sm">${name[0].toUpperCase()}</span> <b>${name}</b>`;
  studentId = id;
  studentName = name;
  els.messages.innerHTML = "";
  els.chat.classList.remove("hidden");
  addAi(`Hi ${studentName}! I'm PrepRight, your math buddy. ` +
        `Which chapter shall we work on today?`);

}

// ---------- chat rendering ----------

// Safe markdown-lite for AI bubbles: escape ALL HTML first, then turn a
// few markdown constructs into tags. Escaping-before-replacing means an
// LLM can never inject markup/script into the page (prompt-injection XSS).
function renderRich(text) {
  let t = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  // LaTeX scrub: models break the no-LaTeX contract occasionally;
  // the renderer de-Latex-ifies so the student never sees \( \) garbage.
  t = t
    .replace(/\\\[|\\\]|\\\(|\\\)/g, "")          // \( \) \[ \] delimiters
    .replace(/\\frac\{([^{}]+)\}\{([^{}]+)\}/g, "($1)/($2)")
    .replace(/\\sqrt\{([^{}]+)\}/g, "√($1)")
    .replace(/\\times/g, "×").replace(/\\cdot/g, "·")
    .replace(/\\pi/g, "π").replace(/\\leq/g, "≤").replace(/\\geq/g, "≥")
    .replace(/\^\{([^{}]+)\}/g, "^$1")             // x^{2} -> x^2
    .replace(/x\^2\b/g, "x²").replace(/\^2\b/g, "²");
  // strip markdown tables (model sometimes uses them despite the prompt)
  t = t.replace(/^\|.*\|$/gm, function(row) {
    var cells = row.split("|").filter(function(c) { return c.trim(); });
    return cells.join(" · ");
  });
  t = t
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/\n/g, "<br>");
  return t;
}

function bubble(role, text = "") {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  if (role === "ai") div.innerHTML = renderRich(text); // AI: markdown-lite
  else div.textContent = text;                          // user input: never rendered as HTML
  els.messages.appendChild(div);
  els.messages.scrollTop = els.messages.scrollHeight;
  return div;
}
const addUser = (t) => bubble("user", t);
const addAi = (t) => bubble("ai", t);

function addToolChip(toolName) {
  const div = document.createElement("div");
  div.className = "msg ai tool-chip";
  div.textContent = TOOL_LABELS[toolName] || `Working: ${toolName}...`;
  els.messages.appendChild(div);
  els.messages.scrollTop = els.messages.scrollHeight;
  return div;
}

// ---------- streaming ----------

async function send(presetText) {
  const text = (presetText != null ? String(presetText) : els.input.value).trim();
  if (!text || streaming || !studentId) return;

  addUser(text);
  els.input.value = "";
  setStreaming(true);

  const aiBubble = bubble("ai");
  let received = "";
  let toolChip = null;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ student_id: studentId, message: text }),
    });
    if (!res.ok || !res.body) throw new Error(`server ${res.status}`);

    // SSE over POST: parse the byte stream by hand (EventSource is GET-only).
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE events are separated by a blank line (\n\n)
      const parts = buffer.split("\n\n");
      buffer = parts.pop(); // incomplete tail stays buffered

      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data: ")) continue;
        const payload = JSON.parse(line.slice(6));

        if (payload.error) {
          // if real content already streamed, keep it and append the warning
          // (a late error must not wipe a good reply)
          if (received) {
            received += `\n\n(ended early: ${payload.error.slice(0, 80)})`;
            aiBubble.innerHTML = renderRich(received);
            continue;
          }
          throw new Error(payload.error);
        }
        if (payload.done) {
          pendingQuiz = null;
          pendingQuiz = null;
          var chip = document.getElementById("quiz-chip");
          if (chip) chip.disabled = false;
          continue;
        }
        if (payload.quiz) {
          // RENDER IMMEDIATELY: cards show as soon as generated.
          // Text filter (in delta handler) removes the model's duplicate.
          pendingQuiz = true;  // flag only, for the text filter
          renderStructuredQuiz(payload.quiz);
          var chip = document.getElementById("quiz-chip");
          if (chip) chip.disabled = true;
          continue;
        }
        if (payload.grade) {
          // Color the original quiz cards: green = correct, red = wrong pick
          applyGradeToCards(payload.grade.verdicts);
          continue;
        }
        if (payload.tool) {
          if (toolChip) toolChip.remove();
          toolChip = addToolChip(payload.tool);
          continue;
        }
        if (payload.delta) {
          if (toolChip) { toolChip.remove(); toolChip = null; }
          received += payload.delta;
          var display = received;
          if (pendingQuiz) {
            var lines = received.split("\n");
            display = lines.filter(function(line) {
              var t = line.trim();
              return t && !/^\d+[.)]/.test(t) && !/^[A-D]\)/.test(t) && !/^Question \d/.test(t);
            }).join("\n");
          }
          aiBubble.innerHTML = renderRich(display || "...");
          els.messages.scrollTop = els.messages.scrollHeight;
        }
      }
    }

    if (toolChip) toolChip.remove();
    if (!received) aiBubble.textContent = "(no reply — try again)";
    else finalizeAiBubble(aiBubble, received); // maybe turn options into buttons
  } catch (err) {
    aiBubble.textContent = `⚠️ ${err.message}`;
  } finally {
    setStreaming(false);
  }
}

function applyGradeToCards(verdicts) {
  // Color the quiz cards: green = correct pick, green = right answer (shown),
  // red = wrong pick. Uses the last set of .q-block elements in the chat.
  var blocks = document.querySelectorAll(".q-block");
  verdicts.forEach(function(v) {
    var block = blocks[v.question - 1];
    if (!block) return;
    var buttons = block.querySelectorAll(".opt-btn");
    buttons.forEach(function(btn) {
      btn.disabled = true;
      var letter = btn.textContent.trim().charAt(0);
      var optionText = btn.textContent.trim().substring(3).trim();
      if (letter === v.chosen) {
        btn.classList.add(v.correct ? "correct" : "wrong");
      } else if (optionText === v.right_option) {
        btn.classList.add("correct");  // always show the right answer
      }
    });
  });
}

function setStreaming(on) {
  streaming = on;
  els.send.disabled = on;
  els.input.disabled = on;
  els.status.classList.toggle("hidden", !on);
  if (!on) els.input.focus();
}

// ---------- quiz cards ------------------------
// After a reply finishes:
// - ONE question with options  -> instant-feedback card (learn mode):
//   tap an option -> sends that letter -> graded -> coaching streams back
// - MULTIPLE "Question N" blocks -> batch card (quiz mode, exam style):
//   select an option per question, then Submit sends ALL answers as one
//   message ("My answers: 1:A 2:C ...") -> graded together + review.
// The ANSWER KEY never reaches the browser in either mode: grading happens
// server-side via the grade_answer tool.

const OPTION_RE = /^([A-D])\)\s+(.+)$/gm;
const QHEADER_RE = /^\s*(?:\*\*)?Question\s+(\d+)\s*(?:\*\*)?\s*:?\s*(.*)$/i;

function finalizeAiBubble(div, rawText) {
  // split into question blocks by "Question N" headers
  const blocks = [];
  let preamble = [];
  let current = null;
  for (const line of rawText.split("\n")) {
    const h = line.match(QHEADER_RE);
    if (h) {
      current = { num: h[1], lines: h[2] ? [h[2]] : [] };
      blocks.push(current);
    } else if (current) {
      current.lines.push(line);
    } else {
      preamble.push(line);
    }
  }

  const withOptions = blocks.filter((b) => countOptions(b.lines.join("\n")) >= 2);

  if (withOptions.length >= 2) {
    renderBatchCard(div, preamble.join("\n"), withOptions);
  } else if (withOptions.length === 1) {
    renderSingleCard(div, preamble.concat(blocks.slice(0, blocks.indexOf(withOptions[0])).flatMap(b => b.lines)).join("\n"), withOptions[0]);
  } // else: plain text - already rendered by the streaming path
}

function countOptions(text) {
  const re = new RegExp(OPTION_RE.source, "gm");
  let n = 0;
  while (re.exec(text) !== null) n++;
  return n;
}

function renderSingleCard(div, preText, block) {
  const body = block.lines.join("\n");
  const stripped = body
    .replace(new RegExp(OPTION_RE.source, "gm"), "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  div.innerHTML = renderRich([preText, `**Question ${block.num}**`, stripped].filter(Boolean).join("\n\n"));

  const box = document.createElement("div");
  box.className = "opt-box";
  parseOptions(body).forEach((o) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "opt-btn";
    b.textContent = `${o.letter})  ${o.text}`;
    b.addEventListener("click", () => {
      if (streaming) return;
      box.querySelectorAll(".opt-btn").forEach((x) => (x.disabled = true));
      b.classList.add("picked");
      els.input.value = "";
      send(o.letter); // instant feedback path
    });
    box.appendChild(b);
  });
  div.appendChild(box);
  els.messages.scrollTop = els.messages.scrollHeight;
}

function renderBatchCard(div, preText, blocks) {
  div.innerHTML = renderRich(preText.trim());
  const selections = new Map(); // question num -> letter
  const submitBtn = document.createElement("button");
  submitBtn.type = "button";
  submitBtn.className = "opt-btn submit-all";
  submitBtn.textContent = "Submit all answers";
  submitBtn.disabled = true;

  blocks.forEach((block) => {
    const body = block.lines.join("\n");
    const stripped = body
      .replace(new RegExp(OPTION_RE.source, "gm"), "")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
    const q = document.createElement("div");
    q.className = "q-block";
    q.innerHTML = `<div class="q-title">Question ${block.num}</div>` +
                  renderRich(stripped);
    const box = document.createElement("div");
    box.className = "opt-box";
    parseOptions(body).forEach((o) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "opt-btn";
      b.textContent = `${o.letter})  ${o.text}`;
      b.addEventListener("click", () => {
        if (streaming) return;
        box.querySelectorAll(".opt-btn").forEach((x) => x.classList.remove("picked"));
        b.classList.add("picked");
        selections.set(block.num, o.letter);
        submitBtn.disabled = selections.size < blocks.length;
      });
      box.appendChild(b);
    });
    q.appendChild(box);
    div.appendChild(q);
  });

  submitBtn.addEventListener("click", () => {
    if (streaming || submitBtn.disabled) return;
    div.querySelectorAll(".opt-btn").forEach((x) => (x.disabled = true));
    const answers = blocks.map((b) => `${b.num}:${selections.get(b.num)}`).join(" ");
    els.input.value = "";
    send(`My answers: ${answers}`);
  });
  const submitBox = document.createElement("div");
  submitBox.className = "opt-box";
  submitBox.appendChild(submitBtn);
  div.appendChild(submitBox);
  els.messages.scrollTop = els.messages.scrollHeight;
}

function parseOptions(text) {
  const re = new RegExp(OPTION_RE.source, "gm");
  const opts = [];
  let m;
  while ((m = re.exec(text)) !== null) opts.push({ letter: m[1], text: m[2] });
  return opts;
}

// Structured quiz: server-sent questions rendered directly - the
// model never retypes them, so hallucinated/reformatted quizzes are
// structurally impossible.
function renderStructuredQuiz(quiz) {
  if (!quiz.questions || quiz.questions.length === 0) return;  // safety: no empty cards
  const holder = document.createElement("div");
  holder.className = "msg ai";
  const title = quiz.chapter ? `**Quiz - ${quiz.chapter}** (difficulty ${quiz.difficulty}/5)` : "**Quiz**";
  holder.innerHTML = renderRich(title);
  const LETTERS = ["A", "B", "C", "D"];
  renderBatchCard(holder, "", quiz.questions.map((q) => ({
    num: q.num,
    lines: [q.text, ...q.options.map((text, i) => `${LETTERS[i]}) ${text}`)],
  })));
  els.messages.appendChild(holder);
  els.messages.scrollTop = els.messages.scrollHeight;
}

els.composer.onsubmit = (e) => {
  e.preventDefault();
  send();
};

// (Switch button removed: kid mode has ONE exit - For Parents.
//  The parent dashboard IS the kid switcher, per Khan Academy Kids pattern.)

// ---------- PIN modal (prompt()/alert() are dead in webviews) ----------
function askPin(title) {
  return new Promise((resolve) => {
    const m = document.getElementById("pin-modal");
    const input = document.getElementById("pin-input");
    const err = document.getElementById("pin-error");
    document.getElementById("pin-title").textContent = title;
    input.value = ""; err.classList.add("hidden");
    m.classList.remove("hidden"); input.focus();
    const done = (val) => { m.classList.add("hidden"); resolve(val); };
    document.getElementById("pin-submit").onclick = () => done(input.value.trim() || null);
    input.onkeydown = (e) => { if (e.key === "Enter") done(input.value.trim() || null); };
    m.onclick = (e) => { if (e.target === m) done(null); };
  });
}
function pinError() {
  document.getElementById("pin-error").classList.remove("hidden");
  document.getElementById("pin-error").textContent = "Wrong PIN";
  document.getElementById("pin-modal").classList.remove("hidden");
}

// gear: PIN-gated jump to the parent dashboard (same device, kid-safe exit)
document.getElementById("parent-btn").onclick = async () => {
  const pin = await askPin("Parent PIN");
  if (!pin) return;
  const parent = JSON.parse(localStorage.getItem("pr_parent") || "{}");
  const res = await fetch("/api/parent/login", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ parent_id: parent.id, pin }),
  });
  if (res.ok) location.href = "/parent.html";
  else pinError();
};

// quick-quiz chip (always available, next to input)
document.getElementById("quiz-chip").onclick = () => {
  if (!streaming && studentId) send("quiz me on a chapter I should practice");
};

// ---------- boot ----------

loadStudents();
