/**
 * ============================================================
 *  EXAM PROCTORING SYSTEM — exam.js
 *  Handles:
 *   1. Countdown Timer (30 min → auto-submit)
 *   2. Webcam capture + send to Flask backend
 *   3. Process annotated frames back from server
 *   4. Tab-switch / blur detection
 *   5. Warning popup management
 *   6. Termination flow
 *   7. Question navigation
 * ============================================================
 */

"use strict";

/* ──────────────────────────────────────────────────────────────
   EXAM QUESTIONS (dummy data — replace with real questions)
────────────────────────────────────────────────────────────── */
const QUESTIONS = [
  {
    text: "Which data structure follows the Last-In-First-Out (LIFO) principle?",
    options: ["Queue", "Stack", "Linked List", "Binary Tree"],
    marks: 2
  },
  {
    text: "What is the time complexity of binary search on a sorted array of n elements?",
    options: ["O(n)", "O(n²)", "O(log n)", "O(n log n)"],
    marks: 2
  },
  {
    text: "Which protocol is used for secure web communication (HTTPS)?",
    options: ["FTP", "SSL/TLS", "SMTP", "UDP"],
    marks: 2
  },
  {
    text: "In object-oriented programming, which concept restricts direct access to an object's data?",
    options: ["Inheritance", "Polymorphism", "Encapsulation", "Abstraction"],
    marks: 2
  },
  {
    text: "What does SQL stand for?",
    options: ["Structured Query Language", "Simple Query Language", "Standard Query Logic", "Sequential Query Language"],
    marks: 2
  },
  {
    text: "Which sorting algorithm has an average time complexity of O(n log n)?",
    options: ["Bubble Sort", "Insertion Sort", "Merge Sort", "Selection Sort"],
    marks: 2
  },
  {
    text: "In networking, which layer of the OSI model handles routing?",
    options: ["Data Link Layer", "Transport Layer", "Network Layer", "Session Layer"],
    marks: 2
  },
  {
    text: "Which keyword is used to prevent inheritance in Java?",
    options: ["static", "abstract", "final", "private"],
    marks: 2
  },
  {
    text: "What is the main advantage of a hash table over a sorted array for lookups?",
    options: ["Lower memory usage", "O(1) average lookup time", "Ordered storage", "Simpler implementation"],
    marks: 2
  },
  {
    text: "Which programming paradigm treats computation as the evaluation of mathematical functions?",
    options: ["Object-Oriented", "Procedural", "Functional", "Imperative"],
    marks: 2
  }
];

/* ──────────────────────────────────────────────────────────────
   STATE
────────────────────────────────────────────────────────────── */
const state = {
  currentQ:   0,
  answers:    new Array(QUESTIONS.length).fill(null), // selected option index
  warnCount:  0,
  terminated: false,
  examOver:   false,

  // Timer (30 minutes = 1800 seconds)
  totalSeconds:   30 * 60,
  remainingSeconds: 30 * 60,
  timerInterval:  null,

  // Webcam
  stream:          null,
  processingFrame: false,   // prevent overlapping requests
  frameInterval:   null,

  // Tab-switch cooldown (prevent spam)
  lastTabWarn:     0
};

/* ──────────────────────────────────────────────────────────────
   INIT  — runs after DOM is loaded
────────────────────────────────────────────────────────────── */
document.addEventListener("DOMContentLoaded", () => {
  buildQuestionNav();
  renderQuestion(0);
  startTimer();
  initWebcam();
  setupTabDetection();
});

/* ──────────────────────────────────────────────────────────────
   1. TIMER
────────────────────────────────────────────────────────────── */
function startTimer() {
  state.timerInterval = setInterval(() => {
    if (state.examOver) return;

    state.remainingSeconds--;

    if (state.remainingSeconds <= 0) {
      clearInterval(state.timerInterval);
      updateTimerDisplay(0);
      autoSubmitExam("Time expired");
      return;
    }

    updateTimerDisplay(state.remainingSeconds);

    // Visual urgency cues
    const box = document.getElementById("timerBox");
    if (state.remainingSeconds <= 300) {        // last 5 min
      box.classList.remove("warn-time");
      box.classList.add("danger-time");
    } else if (state.remainingSeconds <= 600) { // last 10 min
      box.classList.add("warn-time");
    }
  }, 1000);
}

function updateTimerDisplay(seconds) {
  const m = String(Math.floor(seconds / 60)).padStart(2, "0");
  const s = String(seconds % 60).padStart(2, "0");
  document.getElementById("timerDisplay").textContent = `${m}:${s}`;
}

/* Auto-submit when timer hits 0 */
function autoSubmitExam(reason) {
  if (state.examOver) return;
  state.examOver = true;

  // POST to submit endpoint via a hidden form
  const form = document.createElement("form");
  form.method = "POST";
  form.action = "/submit_exam";
  document.body.appendChild(form);
  form.submit();
}

/* ──────────────────────────────────────────────────────────────
   2. WEBCAM SETUP
────────────────────────────────────────────────────────────── */
async function initWebcam() {
  const video    = document.getElementById("rawVideo");
  const feedImg  = document.getElementById("processedFeed");
  const statusEl = document.getElementById("webcamStatus");

  try {
    state.stream = await navigator.mediaDevices.getUserMedia({
      video: { width: 320, height: 240, facingMode: "user" },
      audio: false
    });
    video.srcObject = state.stream;
    await video.play();

    // Show live video directly in the webcam box
    video.style.cssText = "display:block;width:100%;height:100%;object-fit:cover;position:absolute;inset:0;";

    // The processed <img> will overlay on top once server sends annotated frames
    feedImg.style.cssText = "position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:0;transition:opacity 0.3s;";

    statusEl.textContent = "";    // clear "Initializing..."

    // Begin sending frames every 1.5 seconds
    state.frameInterval = setInterval(captureAndSendFrame, 1500);

  } catch (err) {
    statusEl.textContent = "Camera unavailable. Ensure permission is granted.";
    console.warn("Webcam error:", err);
  }
}

/* ──────────────────────────────────────────────────────────────
   3. FRAME CAPTURE + SEND TO BACKEND
────────────────────────────────────────────────────────────── */
function captureAndSendFrame() {
  if (state.terminated || state.examOver || state.processingFrame) return;

  const video  = document.getElementById("rawVideo");
  const canvas = document.getElementById("captureCanvas");

  if (!video || video.readyState < 2) return;

  canvas.width  = 320;
  canvas.height = 240;

  const ctx = canvas.getContext("2d");
  ctx.drawImage(video, 0, 0, 320, 240);

  // Compress to JPEG (quality 0.7 to reduce bandwidth)
  const frameData = canvas.toDataURL("image/jpeg", 0.70);

  state.processingFrame = true;

  fetch("/process_frame", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ frame: frameData })
  })
  .then(r => r.json())
  .then(data => {
    state.processingFrame = false;

    if (data.error) return;

    // ── Update annotated webcam feed ───────────
    if (data.frame) {
      const feedImg = document.getElementById("processedFeed");
      feedImg.src = data.frame;
      feedImg.style.opacity = "1";   // fade in over live video once first frame arrives
    }

    // ── Sync warning count ─────────────────────
    if (data.warn_count !== undefined) {
      updateWarnBadge(data.warn_count);
    }

    // ── Show new warnings ──────────────────────
    if (data.warnings && data.warnings.length > 0) {
      const newest = data.warnings[data.warnings.length - 1];
      if (newest.count > state.warnCount) {
        state.warnCount = newest.count;
        addWarningToLog(newest);
        showWarnModal(newest.reason, newest.count);
      }
    }

    // ── Handle termination ─────────────────────
    if (data.terminated && !state.terminated) {
      state.terminated = true;
      closeWarnModal();
      showTermModal(data.term_reason || "Exam terminated due to violations.");
    }
  })
  .catch(() => { state.processingFrame = false; });
}

/* ──────────────────────────────────────────────────────────────
   4. TAB SWITCH / VISIBILITY DETECTION
────────────────────────────────────────────────────────────── */
function setupTabDetection() {
  // visibilitychange: user switches tab or minimizes window
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) reportTabSwitch("Tab switch detected");
  });

  // blur: browser window loses focus
  window.addEventListener("blur", () => {
    reportTabSwitch("Window focus lost");
  });
}

function reportTabSwitch(reason) {
  if (state.terminated || state.examOver) return;

  // Cooldown: don't fire more than once per 4 seconds
  const now = Date.now();
  if (now - state.lastTabWarn < 4000) return;
  state.lastTabWarn = now;

  fetch("/tab_switch_warning", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ reason })
  })
  .then(r => r.json())
  .then(data => {
    state.warnCount = data.warn_count;
    updateWarnBadge(data.warn_count);
    addWarningToLog({ reason: "Tab switching detected", count: data.warn_count, time: now_time() });
    showWarnModal("Tab switching detected", data.warn_count);

    if (data.terminated) {
      state.terminated = true;
      showTermModal("Repeated tab switching");
    }
  });
}

/* ──────────────────────────────────────────────────────────────
   5. WARNING UI
────────────────────────────────────────────────────────────── */
function updateWarnBadge(count) {
  const badge = document.getElementById("warnBadge");
  const span  = document.getElementById("warnCount");
  span.textContent = count;
  badge.classList.toggle("danger", count >= 3);
}

function addWarningToLog(w) {
  const list  = document.getElementById("wlogList");
  const empty = list.querySelector(".wlog-empty");
  if (empty) empty.remove();

  const entry = document.createElement("div");
  entry.className = "wlog-entry";
  entry.innerHTML = `
    <div class="wlog-num">Warning #${w.count}</div>
    <div class="wlog-reason">${escapeHtml(w.reason)}</div>
    <div class="wlog-time">${w.time || now_time()}</div>
  `;
  list.prepend(entry);  // newest on top
}

function showWarnModal(reason, count) {
  if (state.terminated) return;
  document.getElementById("warnModalTitle").textContent = `Warning #${count} Issued`;
  document.getElementById("warnModalMsg").textContent   = reason;
  document.getElementById("warnModalCount").textContent = count;
  document.getElementById("warnModal").style.display    = "flex";
}

function closeWarnModal() {
  document.getElementById("warnModal").style.display = "none";
}

function showTermModal(reason) {
  clearInterval(state.timerInterval);
  clearInterval(state.frameInterval);
  document.getElementById("termReason").textContent = "Reason: " + reason;
  document.getElementById("termModal").style.display = "flex";
}

function goToResult() {
  window.location.href = "/result/" + STUDENT_ROLL;
}

function confirmSubmit() {
  document.getElementById("submitModal").style.display = "flex";
}
function closeSubmitModal() {
  document.getElementById("submitModal").style.display = "none";
}

/* ──────────────────────────────────────────────────────────────
   6. QUESTION NAVIGATION
────────────────────────────────────────────────────────────── */
function buildQuestionNav() {
  const grid = document.getElementById("qNavGrid");
  grid.innerHTML = "";
  QUESTIONS.forEach((_, i) => {
    const btn = document.createElement("button");
    btn.className    = "qnav-btn" + (i === 0 ? " current" : "");
    btn.textContent  = i + 1;
    btn.dataset.idx  = i;
    btn.onclick      = () => renderQuestion(i);
    btn.id           = `qnav-${i}`;
    grid.appendChild(btn);
  });
}

function renderQuestion(idx) {
  state.currentQ = idx;
  const q = QUESTIONS[idx];

  document.getElementById("qNumber").textContent =
    `Question ${idx + 1} of ${QUESTIONS.length}`;

  document.getElementById("questionBody").innerHTML =
    `<p>${escapeHtml(q.text)}</p>`;

  // Options
  const optList = document.getElementById("optionsList");
  optList.innerHTML = "";
  q.options.forEach((opt, oi) => {
    const item = document.createElement("label");
    item.className = "option-item" + (state.answers[idx] === oi ? " selected" : "");
    item.innerHTML = `
      <input type="radio" name="option" value="${oi}"
             ${state.answers[idx] === oi ? "checked" : ""}/>
      <span class="option-label">${escapeHtml(opt)}</span>
    `;
    item.addEventListener("change", () => selectAnswer(idx, oi));
    optList.appendChild(item);
  });

  // Update nav grid
  document.querySelectorAll(".qnav-btn").forEach((btn, i) => {
    btn.classList.remove("current", "answered");
    if (i === idx)               btn.classList.add("current");
    else if (state.answers[i] !== null) btn.classList.add("answered");
  });

  // Prev/Next buttons
  document.getElementById("btnPrev").disabled = (idx === 0);
  document.getElementById("btnNext").disabled = (idx === QUESTIONS.length - 1);
}

function selectAnswer(qIdx, optIdx) {
  state.answers[qIdx] = optIdx;

  // Update selection style
  document.querySelectorAll(".option-item").forEach((el, i) => {
    el.classList.toggle("selected", i === optIdx);
  });

  // Mark in sidebar
  const navBtn = document.getElementById(`qnav-${qIdx}`);
  if (navBtn && qIdx !== state.currentQ) {
    navBtn.classList.add("answered");
  } else if (navBtn) {
    navBtn.classList.remove("current");
    navBtn.classList.add("answered");
    navBtn.classList.add("current");
  }
}

function changeQuestion(delta) {
  const next = state.currentQ + delta;
  if (next >= 0 && next < QUESTIONS.length) renderQuestion(next);
}

/* ──────────────────────────────────────────────────────────────
   UTILITIES
────────────────────────────────────────────────────────────── */
function escapeHtml(str) {
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}

function now_time() {
  const d = new Date();
  return d.toTimeString().slice(0, 8);
}
