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
  select: document.getElementById("student-select"),
  newBtn: document.getElementById("new-student-btn"),
};

let studentId = null;
let studentName = "";
let streaming = false;

// ---------- onboarding ----------

async function loadStudents() {
  const res = await fetch("/api/students");
  const students = await res.json();
  els.select.innerHTML =
    `<option value="">— who's learning? —</option>` +
    students
      .map((s) => `<option value="${s.id}">${s.name} (Class ${s.grade})</option>`)
      .join("");
  els.select.onchange = () => {
    const opt = els.select.selectedOptions[0];
    if (els.select.value) enterChat(Number(els.select.value), opt.textContent.split(" (")[0]);
  };
}

els.newBtn.onclick = () => {
  els.onboarding.classList.remove("hidden");
  els.obName.focus();
};

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
  els.select.value = s.id;
  enterChat(s.id, s.name);
};

function enterChat(id, name) {
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
        if (payload.done) continue;
        if (payload.quiz) {
          // STRUCTURED quiz delivery: cards rendered from data, not prose.
          // The answer key is not in this payload - it stays server-side.
          renderStructuredQuiz(payload.quiz);
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
          aiBubble.innerHTML = renderRich(received);
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

// ---------- boot ----------

loadStudents();
