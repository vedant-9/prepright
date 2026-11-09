/* PrepRight chat client — vanilla JS, no dependencies.
 *
 * STATE MODEL (thin slice): the browser owns the conversation history
 * (`messages` array below) and sends the FULL history with every request.
 * The server is stateless. P6 flips this: state moves server-side into
 * LangGraph checkpoints and we send only the new message.
 */

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
let messages = []; // [{role: 'user'|'assistant', content: '...'}]
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
  messages = [];
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

// ---------- streaming ----------

async function send() {
  const text = els.input.value.trim();
  if (!text || streaming || !studentId) return;

  messages.push({ role: "user", content: text });
  addUser(text);
  els.input.value = "";
  setStreaming(true);

  const aiBubble = bubble("ai");
  let received = "";

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ student_id: studentId, messages }),
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

        if (payload.error) throw new Error(payload.error);
        if (payload.done) continue;
        if (payload.delta) {
          received += payload.delta;
          aiBubble.innerHTML = renderRich(received);
          els.messages.scrollTop = els.messages.scrollHeight;
        }
      }
    }

    if (received) messages.push({ role: "assistant", content: received });
    else aiBubble.textContent = "(no reply — try again)";
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

els.composer.onsubmit = (e) => {
  e.preventDefault();
  send();
};

// ---------- boot ----------

loadStudents();
