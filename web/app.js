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
  grade_quiz: "Grading your quiz...",
  lookup_concept: "Finding the right explanation...",
  calculate: "Calculating...",
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

// (onboarding form removed — kid creation is on the parent page)

const HISTORY_BATCH = 50;

function _renderHistoryChunk(msgs, prepend) {
  const firstExisting = prepend ? els.messages.querySelector(".msg") : null;
  for (const m of msgs) {
    // bubble() appends; for prepend we move each in above the oldest row.
    // Every later insert lands before the same anchor, so chunk order holds.
    let el;
    if (m.role === "user") {
      el = addUser(m.content);
      // photo messages: this device kept a thumbnail (localStorage) -> show
      // it; a different device or cleared storage -> honest placeholder,
      // never a broken image
      if (m.content.startsWith("📷")) {
        const d = _getThumb(studentId, m.content);
        const img = document.createElement("img");
        if (d) {
          img.src = d; img.alt = "your notebook photo";
        } else {
          img.remove();
          const ph = document.createElement("div");
          ph.className = "thumb-away";
          ph.textContent = "📷 photo — saved on the device it was taken with";
          el.appendChild(ph);
          if (prepend && el) els.messages.insertBefore(el, firstExisting);
          continue;
        }
        el.appendChild(img);
      }
    }
    else if (m.role === "quiz") {
      // replayed quiz: real cards, read-only (the grading session is gone)
      renderStructuredQuiz(JSON.parse(m.content), true);
      el = els.messages.lastChild;
    } else el = addAi(m.content);
    if (prepend && el) els.messages.insertBefore(el, firstExisting);
  }
}

function _setLoadEarlier(visible, oldestId) {
  let chip = document.getElementById("load-earlier");
  if (!visible) { if (chip) chip.remove(); return; }
  if (!chip) {
    chip = document.createElement("button");
    chip.type = "button";
    chip.id = "load-earlier";
    chip.className = "load-earlier";
    chip.textContent = "↑ Load earlier messages";
    chip.onclick = async () => {
      const beforeId = Number(chip.dataset.oldest);
      const keep = els.messages.scrollHeight - els.messages.scrollTop;  // viewport anchor
      try {
        const res = await fetch(`/api/students/${studentId}/messages` +
                                `?limit=${HISTORY_BATCH}&before_id=${beforeId}`);
        const older = await res.json();
        _renderHistoryChunk(older, true);
        els.messages.scrollTop = els.messages.scrollHeight - keep;      // stay put
        _setLoadEarlier(older.length === HISTORY_BATCH,
                        older.length ? older[0].id : beforeId);
      } catch (e) { /* offline: leave the chip for a retry tap */ }
    };
    els.messages.insertBefore(chip, els.messages.firstChild);
  }
  chip.dataset.oldest = oldestId;
}

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
  // returning student: replay their past conversation instead of a blank
  // chat (the tutor's MEMORY always survived; now the screen keeps up).
  // Old quizzes replay as plain text - their answer keys are gone, so
  // rendering live quiz cards would offer dead buttons.
  fetch(`/api/students/${id}/messages?limit=${HISTORY_BATCH}`).then((r) => r.json()).then((msgs) => {
    if (msgs.length) {
      _renderHistoryChunk(msgs, false);
      // full batch = there may be more history above
      _setLoadEarlier(msgs.length === HISTORY_BATCH, msgs[0].id);
    } else {
      addAi(`Hi ${studentName}! I'm PrepRight, your math buddy. ` +
            `Which chapter shall we work on today?`);
    }
  }).catch(() => {
    addAi(`Hi ${studentName}! I'm PrepRight, your math buddy. ` +
          `Which chapter shall we work on today?`);
  });

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
  if (streaming || !studentId) return;
  // an attached photo re-routes Send: image + typed words go together
  if (dockedPhoto && presetText == null) {
    return _sendWithPhoto(text);
  }
  if (!text) return;

  // a confirmed photo's thumbnail is keyed to the message ACTUALLY sent
  // (the student may have edited the staged text before pressing Send)
  if (pendingPhotoThumb && text.startsWith("📷")) {
    _saveThumb(studentId, text, pendingPhotoThumb);
  }
  pendingPhotoThumb = null;

  addUser(text);
  els.input.value = "";
  _streamTurn(text);
}

// The streaming core: one agent turn for an already-displayed user
// message. send() uses it after adding the bubble; the photo flow uses
// it after the echo-back confirm, where the bubble has been visible
// since the student's Send press.
async function _streamTurn(text) {
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
          // BUFFER quiz data: render cards on FIRST text delta so the
          // intro appears above the cards (correct visual order)
          pendingQuiz = payload.quiz;
          var chip = document.getElementById("quiz-chip");
          if (chip) chip.disabled = true;
          continue;
        }
        if (payload.grade) {
          // Color the original quiz cards: green = correct, red = wrong pick.
          // The SCORE comes from this data event, never from the model's
          // prose - narration can miscount what the tool result got right.
          // Data over prose, same rule as everything else here.
          applyGradeToCards(payload.grade.verdicts);
          if (payload.grade.score) {
            const s = document.createElement("div");
            s.className = "msg ai score-line";
            s.textContent = `Result: ${payload.grade.score} correct`;
            els.messages.appendChild(s);
            els.messages.scrollTop = els.messages.scrollHeight;
          }
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
          if (pendingQuiz && pendingQuiz !== true && display.trim().length > 10) {
            renderStructuredQuiz(pendingQuiz);
            pendingQuiz = true;  // flag: already rendered, keep filter active
          }
          els.messages.scrollTop = els.messages.scrollHeight;
        }
      }
    }

    if (toolChip) toolChip.remove();
    if (!received) aiBubble.textContent = "(no reply — try again)";
    else finalizeAiBubble(aiBubble, received); // maybe turn options into buttons
  } catch (err) {
    aiBubble.textContent = "Something went wrong — please try again."
  } finally {
    setStreaming(false);
  }
}

function applyGradeToCards(verdicts) {
  // Color the quiz cards: green = correct pick, green = right answer (shown),
  // red = wrong pick. Scoped to the LIVE quiz (.quiz-active): replayed
  // quizzes also carry .q-block elements, and a global index would color
  // the history instead of the quiz being graded. Text-quiz (learn mode)
  // blocks carry no marker - fall back to the global scan for them.
  var blocks = document.querySelectorAll(".quiz-active .q-block");
  if (blocks.length === 0) blocks = document.querySelectorAll(".q-block");
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
  // THE composer state switch: while the tutor answers, every input is
  // visibly disabled - a guarded-but-enabled button correctly does nothing
  // and correctly feels broken. One setter, all controls, no exceptions.
  streaming = on;
  els.send.disabled = on;
  els.input.disabled = on;
  document.getElementById("photo-btn").disabled = on;
  document.getElementById("quiz-chip").disabled = on;
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

function renderBatchCard(div, preText, blocks, readonly) {
  // only REPLACE content for the text-quiz path (preText = the question
  // text); the structured path passes "" and has already set a title/tag
  if (preText.trim()) div.innerHTML = renderRich(preText.trim());
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
      // replayed quizzes render read-only: the grading session is gone,
      // so the questions show as history, not as live buttons
      const b = document.createElement(readonly ? "div" : "button");
      b.className = "opt-btn" + (readonly ? " static" : "");
      b.textContent = `${o.letter})  ${o.text}`;
      if (!readonly) b.addEventListener("click", () => {
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

  if (!readonly) {
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
  }
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
function renderStructuredQuiz(quiz, readonly) {
  if (!quiz.questions || quiz.questions.length === 0) return;  // safety: no empty cards
  const holder = document.createElement("div");
  holder.className = "msg ai";
  const title = quiz.chapter ? `**Quiz - ${quiz.chapter}** (difficulty ${quiz.difficulty}/5)` : "**Quiz**";
  holder.innerHTML = renderRich(title);
  if (readonly) {
    // a replayed quiz must announce itself - dead-looking buttons read as
    // broken to anyone who doesn't know it's history
    const tag = document.createElement("div");
    tag.className = "quiz-replay-tag";
    tag.textContent = "Past quiz — already answered";
    holder.appendChild(tag);
  } else {
    // the LIVE quiz: grade events must color THIS quiz's blocks, not
    // whichever .q-block elements happen to sit earlier in the chat
    const prev = document.querySelector(".quiz-active");
    if (prev) prev.classList.remove("quiz-active");
    holder.classList.add("quiz-active");
  }
  const LETTERS = ["A", "B", "C", "D"];
  renderBatchCard(holder, "", quiz.questions.map((q) => ({
    num: q.num,
    lines: [q.text, ...q.options.map((text, i) => `${LETTERS[i]}) ${text}`)],
  })), readonly);
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
  // no parent ever signed in on this device: there is no PIN to ask for
  if (!JSON.parse(localStorage.getItem("pr_parent") || "{}").id) {
    location.href = "/parent.html";
    return;
  }
  const pin = await askPin("Parent PIN");
  if (!pin) return;
  const parent = JSON.parse(localStorage.getItem("pr_parent") || "{}");
  const res = await fetch("/api/parent/login", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ parent_id: parent.id, pin }),
  });
  if (res.ok) location.href = "/parent.html";
  else if (res.status === 404) {
    // server reset wiped this parent (ephemeral demo storage): clear the
    // ghost session and send them to sign-in, which recreates the profile
    localStorage.removeItem("pr_parent");
    const err = document.getElementById("pin-error");
    err.classList.remove("hidden");
    err.textContent = "Profile was reset — reopening parent sign-in…";
    setTimeout(() => location.href = "/parent.html", 1400);
  }
  else pinError();
};

// quick-quiz chip (always available, next to input)
document.getElementById("quiz-chip").onclick = () => {
  if (!streaming && studentId) send("quiz me on a chapter I should practice");
};

// ---------- boot ----------

loadStudents();


// ---------- notebook photo input (attach-then-send pattern) ----------
// Privacy is the design, not a feature: the image is re-encoded through a
// canvas BEFORE upload (destroys EXIF/GPS metadata and downsizes to 1280px,
// all device-side), and the server keeps it in request memory only. The
// photo button only DOCKS a preview chip at the composer; the student
// types freely, and ONE Send carries image + words. The echo-back card
// after Send is the trust bridge: extraction is a proposal, the student
// is ground truth - a misread digit never reaches the tutor unconfirmed.
let dockedPhoto = null;        // {b64, dataUrl} while a photo is attached

document.getElementById("photo-btn").onclick = () => {
  if (!studentId || streaming) return;
  document.getElementById("photo-input").click();
};

document.getElementById("photo-input").onchange = async (e) => {
  const file = e.target.files && e.target.files[0];
  e.target.value = "";                       // allow re-picking the same file
  if (!file) return;
  try {
    const b64 = await _stripExifAndShrink(file);
    dockedPhoto = { b64: b64, dataUrl: `data:image/jpeg;base64,${b64}` };
    _renderDock();
    els.input.focus();
  } catch (err) {
    addAi("That photo didn't work — try another one 📸");
  }
};

function _renderDock() {
  let dock = document.getElementById("photo-dock");
  if (!dockedPhoto) { if (dock) dock.remove(); return; }
  if (!dock) {
    dock = document.createElement("div");
    dock.id = "photo-dock";
    const x = document.createElement("button");
    x.type = "button";
    x.id = "photo-dock-x";
    x.textContent = "✕";
    x.title = "Remove photo";
    x.onclick = () => { dockedPhoto = null; _renderDock(); els.input.focus(); };
    dock.appendChild(x);
    els.composer.insertBefore(dock, els.input);
  }
  dock.style.backgroundImage = `url(${dockedPhoto.dataUrl})`;
  els.input.placeholder = "Add a message with your photo (optional)…";
}

function _clearDock() {
  dockedPhoto = null;
  const dock = document.getElementById("photo-dock");
  if (dock) dock.remove();
  els.input.placeholder = "Ask about any chapter — e.g. 'help me with quadratic equations'";
}

async function _sendWithPhoto(text) {
  // Send with an attached photo: the student's message appears IMMEDIATELY
  // (their bubble first, processing below it), then the
  // vision read runs with their words as context, then the echo-back card.
  const b64 = dockedPhoto.b64;
  const words = text.trim() || "Help me solve this problem";
  const thumbSrc = dockedPhoto.dataUrl;
  _clearDock();
  els.input.value = "";

  // their message, visible the instant they press Send
  const userBubble = bubble("user", `📷 ${words}`);
  const img = document.createElement("img");
  img.src = thumbSrc; img.alt = "your photo";
  userBubble.insertBefore(img, userBubble.firstChild);
  els.messages.scrollTop = els.messages.scrollHeight;

  const busy = addAi("📷 Reading your photo…");
  setStreaming(true);
  try {
    const res = await fetch("/api/photo", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ student_id: studentId, image_b64: b64, prompt: words }),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "read failed");
    const out = await res.json();
    busy.remove();
    setStreaming(false);
    if (!out.found) {
      addAi("Hmm, I couldn't find a maths problem in that photo. Try one page, one problem, good light 📸");
      return;
    }
    _confirmExtraction(out.text, words, thumbSrc, userBubble);
  } catch (err) {
    setStreaming(false);
    addAi("Photo didn't work — " + (err.message || "please try again"));
  }
}

function _stripExifAndShrink(file) {
  // canvas re-encode: every EXIF/GPS tag dies here, before any byte leaves
  // the device. Max 1280px keeps the vision payload small.
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      URL.revokeObjectURL(url);
      const scale = Math.min(1, 1280 / Math.max(img.width, img.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(img.width * scale);
      canvas.height = Math.round(img.height * scale);
      canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
      const dataUrl = canvas.toDataURL("image/jpeg", 0.85);
      resolve(dataUrl.split(",")[1]);         // strip the data: prefix
    };
    img.onerror = () => reject(new Error("couldn't read that image"));
    img.src = url;
  });
}

// the staged thumbnail of a just-confirmed photo, waiting for the student's
// own Send press (confirm stages the message; it never auto-sends)
let pendingPhotoThumb = null;

// ---------- device-local photo thumbnails ----------
// The photo stays visible in the student's chat FOREVER - but only on the
// device that took it (which already holds the original). Thumbnails live
// in localStorage, keyed by the confirmed message content; the server
// never receives or stores a single image byte. Other devices replay the
// message with a graceful placeholder instead of a broken image.
const THUMB_MAX = 25;   // ~20KB each -> hard ceiling around half a meg

function _thumbStore(studentId) {
  try { return JSON.parse(localStorage.getItem(`pr_thumbs_${studentId}`)) || []; }
  catch (e) { return []; }
}

function _saveThumb(studentId, msgContent, dataUrl) {
  try {
    const store = _thumbStore(studentId);
    store.push({ h: msgContent, d: dataUrl });
    while (store.length > THUMB_MAX) store.shift();     // oldest out, storage capped
    localStorage.setItem(`pr_thumbs_${studentId}`, JSON.stringify(store));
  } catch (e) { /* quota exceeded: thumbnails are cosmetic, never fatal */ }
}

function _getThumb(studentId, msgContent) {
  const hit = _thumbStore(studentId).find(t => t.h === msgContent);
  return hit ? hit.d : null;
}

function _shrinkForStore(dataUrl) {
  // 160px jpeg ~20KB: enough to recognize the page, kind to the quota
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(1, 160 / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * scale);
      c.height = Math.round(img.height * scale);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      resolve(c.toDataURL("image/jpeg", 0.7));
    };
    img.onerror = () => resolve(null);
    img.src = dataUrl;
  });
}

function _confirmExtraction(text, words, thumbSrc, userBubble) {
  // echo-back card: the student confirms or edits the READ before the
  // tutor acts on it. This is the bridge that keeps compute-don't-trust
  // intact for photographed problems: a misread digit dies here.
  const card = document.createElement("div");
  card.className = "msg ai extract-card";
  card.innerHTML =
    `<div>I read this as:</div>` +
    `<blockquote>${renderRich(text)}</blockquote>` +
    `<div class="extract-actions">` +
    `<button type="button" class="opt-btn correct">✓ Correct — teach me</button>` +
    `<button type="button" class="opt-btn">✏️ Edit</button></div>`;
  els.messages.appendChild(card);
  els.messages.scrollTop = els.messages.scrollHeight;
  // chips stay dimmed while the card pends: the next action belongs to
  // the card (confirm/edit), not to starting a parallel question
  document.getElementById("photo-btn").disabled = true;
  document.getElementById("quiz-chip").disabled = true;
  const [ok, edit] = card.querySelectorAll("button");
  // The full message = the student's words (sent) + the confirmed read
  // (reviewed here). Confirm folds the read into the already-visible
  // bubble and starts the agent turn - no duplicate bubble.
  const staged = `📷 ${words} — photo reads: ${text}`;
  const release = () => {
    document.getElementById("photo-btn").disabled = false;
    document.getElementById("quiz-chip").disabled = false;
  };
  ok.onclick = () => {
    card.remove();
    release();
    if (userBubble) {
      // bubble() wrote a single text node; img was inserted before it.
      // Update the text in place so the displayed message matches what
      // the agent receives and what history stores.
      const t = userBubble.lastChild;
      if (t && t.nodeType === 3) t.nodeValue = staged;
    }
    if (thumbSrc) _shrinkForStore(thumbSrc).then(d => d && _saveThumb(studentId, staged, d));
    _streamTurn(staged);
  };
  edit.onclick = () => {
    card.remove();
    release();                                // they are rewording: chips live
    if (userBubble) userBubble.remove();     // they want to reword it
    els.input.value = staged;
    els.input.focus();
  };
}


// ---------- new chat (fresh conversation, scores preserved) ----------
// Clears BOTH stores: the messages table (visible history) and the
// checkpointer thread (the tutor's memory). Progress lives in a separate
// table and survives - a fresh chat is a fresh conversation, not a fresh
// student. confirm() is dead in webviews, hence the modal.
document.getElementById("newchat-btn").onclick = () => {
  if (!studentId || streaming) return;   // no mid-turn wipes: orphaned replies
  document.getElementById("confirm-modal").classList.remove("hidden");
};

document.getElementById("confirm-no").onclick = () =>
  document.getElementById("confirm-modal").classList.add("hidden");

document.getElementById("confirm-yes").onclick = async () => {
  document.getElementById("confirm-modal").classList.add("hidden");
  if (!studentId) return;
  try {
    await fetch(`/api/students/${studentId}/chat`, { method: "DELETE" });
  } catch (e) { /* even if the wire fails, the local reset below stands */ }
  // thumbnails die with the conversation they belonged to - no orphans
  localStorage.removeItem(`pr_thumbs_${studentId}`);
  els.messages.innerHTML = "";
  addAi(`Fresh start! What shall we work on, ${studentName}?`);
};


