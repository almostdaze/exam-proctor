"""
================================================================================
  ONLINE EXAM PROCTORING SYSTEM
  Backend: Flask + OpenCV + SQLite
  Author: Academic Project
  Description: Real-time proctoring with face detection, tab switching,
               warning system, and evidence capture.
================================================================================
"""

import os
import time
import base64
import sqlite3
import threading
from datetime import datetime
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, jsonify, Response
)

import cv2
import numpy as np

# ─────────────────────────────────────────────
#  App Configuration
# ─────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = "exam_proctor_secret_2024"  # Used for session management

BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
DB_PATH        = os.path.join(BASE_DIR, "database", "exam.db")
SCREENSHOT_DIR = os.path.join(BASE_DIR, "static", "screenshots")

# Automatically find OpenCV's bundled Haar cascade data folder.
# cv2.data.haarcascades works on Windows, Mac, and Linux regardless
# of where Python is installed.
HAAR_DIR = cv2.data.haarcascades          # e.g. C:\Python\Lib\site-packages\cv2\data\

os.makedirs(SCREENSHOT_DIR, exist_ok=True)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# ─────────────────────────────────────────────
#  Haar Cascade Classifiers
# ─────────────────────────────────────────────
face_cascade  = cv2.CascadeClassifier(HAAR_DIR + "haarcascade_frontalface_default.xml")
eye_cascade   = cv2.CascadeClassifier(HAAR_DIR + "haarcascade_eye.xml")
smile_cascade = cv2.CascadeClassifier(HAAR_DIR + "haarcascade_smile.xml")

# ─────────────────────────────────────────────
#  In-memory proctoring state per student
#  { roll_number: ProctoringState }
# ─────────────────────────────────────────────
proctor_states = {}
state_lock = threading.Lock()


class ProctoringState:
    """Holds real-time proctoring data for one student session."""

    def __init__(self, roll):
        self.roll               = roll
        self.warning_count      = 0
        self.terminated         = False
        self.termination_reason = None

        # Face tracking
        self.no_face_seconds    = 0.0   # consecutive seconds with no face
        self.away_seconds       = 0.0   # consecutive seconds face not centered
        self.last_check_time    = time.time()

        # Movement heuristic (detect rapid position changes → suspicious)
        self.prev_face_center   = None
        self.move_events        = 0     # large jumps within a rolling window
        self.move_window_start  = time.time()

        # Talking heuristic: track mouth-region pixel variance over time
        self.smile_frames       = 0     # frames mouth detected open
        self.talk_frames_window = 0
        self.talk_window_start  = time.time()

        # Latest frame (for broadcasting)
        self.latest_frame_jpg   = None
        self.latest_warnings    = []    # recent warning messages

    def add_warning(self, reason):
        """Increment warning counter and record reason."""
        self.warning_count += 1
        self.latest_warnings.append({
            "count": self.warning_count,
            "reason": reason,
            "time": datetime.now().strftime("%H:%M:%S")
        })
        # Keep only last 10 warnings in memory
        if len(self.latest_warnings) > 10:
            self.latest_warnings.pop(0)
        return self.warning_count


# ─────────────────────────────────────────────
#  Database Helpers
# ─────────────────────────────────────────────

def get_db():
    """Return a new SQLite connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create all tables if they don't exist."""
    conn = get_db()
    cur  = conn.cursor()

    # Students table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    NOT NULL,
            roll       TEXT    NOT NULL UNIQUE,
            created_at TEXT    DEFAULT (datetime('now'))
        )
    """)

    # Exam sessions / logs
    cur.execute("""
        CREATE TABLE IF NOT EXISTS exam_logs (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            roll               TEXT    NOT NULL,
            login_time         TEXT,
            end_time           TEXT,
            warning_count      INTEGER DEFAULT 0,
            termination_reason TEXT,
            FOREIGN KEY (roll) REFERENCES students(roll)
        )
    """)

    # Screenshot evidence
    cur.execute("""
        CREATE TABLE IF NOT EXISTS screenshots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            roll        TEXT NOT NULL,
            filepath    TEXT NOT NULL,
            reason      TEXT,
            captured_at TEXT DEFAULT (datetime('now'))
        )
    """)

    conn.commit()
    conn.close()


# ─────────────────────────────────────────────
#  OpenCV Proctoring Logic
# ─────────────────────────────────────────────

def process_frame(frame, state: ProctoringState):
    """
    Analyze a single webcam frame:
      1. Detect faces (count → no-face / multi-face warning)
      2. Check face position (centered?) → looking-away warning
      3. Detect rapid movement → suspicious-movement warning
      4. Heuristic talking detection via smile cascade
    Returns annotated frame bytes (JPEG).
    """
    now   = time.time()
    dt    = now - state.last_check_time   # seconds since last frame
    state.last_check_time = now

    h, w  = frame.shape[:2]
    gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray  = cv2.equalizeHist(gray)

    # ── Face Detection ──────────────────────────────────────────────────────
    faces = face_cascade.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(80, 80)
    )

    warning_this_frame = None

    if len(faces) == 0:
        # No face visible
        state.no_face_seconds += dt
        cv2.putText(frame, "NO FACE DETECTED", (10, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 220), 2)
        if state.no_face_seconds >= 3.0:          # 3 consecutive seconds
            warning_this_frame = "No face detected"
            state.no_face_seconds = 0.0
    else:
        state.no_face_seconds = 0.0   # reset counter

    if len(faces) > 1:
        # Multiple people in frame
        warning_this_frame = "Multiple faces detected"
        cv2.putText(frame, "MULTIPLE FACES!", (10, 80),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 220), 2)

    # ── Per-face Analysis ───────────────────────────────────────────────────
    for (x, y, fw, fh) in faces:
        cv2.rectangle(frame, (x, y), (x+fw, y+fh), (30, 160, 80), 2)

        face_cx = x + fw // 2
        face_cy = y + fh // 2

        # Center zone: middle 50% of frame width & height
        cx_ok = (w * 0.25) < face_cx < (w * 0.75)
        cy_ok = (h * 0.20) < face_cy < (h * 0.80)

        if not (cx_ok and cy_ok):
            state.away_seconds += dt
            cv2.putText(frame, "LOOK AT SCREEN", (10, 120),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 140, 255), 2)
            if state.away_seconds >= 5.0:
                warning_this_frame = "Looking away from screen"
                state.away_seconds = 0.0
        else:
            state.away_seconds = 0.0

        # ── Rapid-movement heuristic ────────────────────────────────────────
        center = (face_cx, face_cy)
        if state.prev_face_center is not None:
            dist = ((center[0] - state.prev_face_center[0]) ** 2 +
                    (center[1] - state.prev_face_center[1]) ** 2) ** 0.5
            if dist > (w * 0.12):            # > 12% of frame width
                state.move_events += 1

        state.prev_face_center = center

        # Check move window (10-second rolling)
        if now - state.move_window_start > 10:
            if state.move_events >= 5:       # ≥5 large moves in 10 s
                warning_this_frame = "Suspicious movement detected"
            state.move_events      = 0
            state.move_window_start = now

        # ── Talking heuristic via smile / mouth-open detection ───────────────
        roi_gray = gray[y:y+fh, x:x+fw]
        # Check lower half of face for mouth activity
        lower_half = roi_gray[fh//2:, :]
        smiles = smile_cascade.detectMultiScale(
            lower_half,
            scaleFactor=1.7,
            minNeighbors=22,
            minSize=(25, 15)
        )
        if len(smiles) > 0:
            state.smile_frames += 1

        # 15-second talking window
        state.talk_frames_window += 1
        if now - state.talk_window_start > 15:
            ratio = state.smile_frames / max(state.talk_frames_window, 1)
            if ratio > 0.35:                  # mouth open >35% of frames
                warning_this_frame = "Possible talking detected"
            state.smile_frames       = 0
            state.talk_frames_window = 0
            state.talk_window_start  = now

    # ── Overlay: Warning Counter ─────────────────────────────────────────────
    cv2.rectangle(frame, (0, 0), (w, 30), (20, 20, 40), -1)
    label = f"Warnings: {state.warning_count}/4  |  Roll: {state.roll}"
    cv2.putText(frame, label, (8, 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)

    # ── Issue Warning if Triggered ───────────────────────────────────────────
    if warning_this_frame and not state.terminated:
        count = state.add_warning(warning_this_frame)
        save_screenshot(frame.copy(), state.roll, warning_this_frame)
        update_db_warnings(state.roll, count, warning_this_frame)

        if count >= 4:
            state.terminated         = True
            state.termination_reason = warning_this_frame
            terminate_exam_db(state.roll, warning_this_frame)

    # Encode to JPEG
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
    state.latest_frame_jpg = buf.tobytes()
    return state.latest_frame_jpg


def save_screenshot(frame, roll, reason):
    """Save annotated frame as evidence screenshot."""
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{roll}_{ts}.jpg"
    filepath = os.path.join(SCREENSHOT_DIR, filename)
    cv2.imwrite(filepath, frame)

    conn = get_db()
    conn.execute(
        "INSERT INTO screenshots (roll, filepath, reason) VALUES (?,?,?)",
        (roll, f"static/screenshots/{filename}", reason)
    )
    conn.commit()
    conn.close()


def update_db_warnings(roll, count, reason):
    """Update warning count in the live exam log."""
    conn = get_db()
    conn.execute(
        "UPDATE exam_logs SET warning_count=? WHERE roll=? AND end_time IS NULL",
        (count, roll)
    )
    conn.commit()
    conn.close()


def terminate_exam_db(roll, reason):
    """Mark exam as terminated in the database."""
    conn = get_db()
    conn.execute(
        """UPDATE exam_logs
           SET end_time=datetime('now'), termination_reason=?
           WHERE roll=? AND end_time IS NULL""",
        (reason, roll)
    )
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────
#  Flask Routes
# ─────────────────────────────────────────────

@app.route("/", methods=["GET", "POST"])
def login():
    """Login page: student name + roll number."""
    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        roll = request.form.get("roll", "").strip()

        if not name or not roll:
            error = "Both fields are required."
        else:
            conn = get_db()
            # Upsert student record
            conn.execute(
                "INSERT OR IGNORE INTO students (name, roll) VALUES (?,?)",
                (name, roll)
            )
            # Create exam log entry
            conn.execute(
                "INSERT INTO exam_logs (roll, login_time) VALUES (?,datetime('now'))",
                (roll,)
            )
            conn.commit()
            conn.close()

            # Store in session
            session["name"] = name
            session["roll"] = roll

            # Initialize proctoring state
            with state_lock:
                proctor_states[roll] = ProctoringState(roll)

            return redirect(url_for("exam"))

    return render_template("login.html", error=error)


@app.route("/exam")
def exam():
    """Exam dashboard — protected, requires login."""
    if "roll" not in session:
        return redirect(url_for("login"))
    return render_template(
        "exam.html",
        name=session["name"],
        roll=session["roll"]
    )


@app.route("/process_frame", methods=["POST"])
def receive_frame():
    """
    Endpoint: browser sends webcam frame as base64 JPEG.
    Server processes it through OpenCV and returns:
      - annotated frame (base64)
      - latest warnings
      - terminated flag
    """
    if "roll" not in session:
        return jsonify({"error": "Not authenticated"}), 401

    roll = session["roll"]
    data = request.json.get("frame", "")

    if not data:
        return jsonify({"error": "No frame data"}), 400

    # Decode base64 → numpy array
    try:
        img_bytes = base64.b64decode(data.split(",")[-1])
        nparr     = np.frombuffer(img_bytes, np.uint8)
        frame     = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    except Exception as e:
        return jsonify({"error": f"Frame decode error: {e}"}), 400

    with state_lock:
        state = proctor_states.get(roll)
        if state is None:
            state = ProctoringState(roll)
            proctor_states[roll] = state

    # Process frame
    processed_jpg = process_frame(frame, state)

    # Encode processed frame back to base64
    processed_b64 = "data:image/jpeg;base64," + base64.b64encode(processed_jpg).decode()

    return jsonify({
        "frame":       processed_b64,
        "warnings":    state.latest_warnings,
        "warn_count":  state.warning_count,
        "terminated":  state.terminated,
        "term_reason": state.termination_reason
    })


@app.route("/tab_switch_warning", methods=["POST"])
def tab_switch_warning():
    """Called by JavaScript when a tab-switch / blur event is detected."""
    if "roll" not in session:
        return jsonify({"error": "Not authenticated"}), 401

    roll = session["roll"]
    with state_lock:
        state = proctor_states.get(roll)
        if state and not state.terminated:
            count = state.add_warning("Tab switching detected")
            update_db_warnings(roll, count, "Tab switching detected")
            if count >= 4:
                state.terminated         = True
                state.termination_reason = "Tab switching detected"
                terminate_exam_db(roll, "Tab switching detected")
            return jsonify({
                "warn_count": count,
                "terminated": state.terminated
            })

    return jsonify({"warn_count": 0, "terminated": False})


@app.route("/submit_exam", methods=["POST"])
def submit_exam():
    """Student manually submits the exam."""
    if "roll" not in session:
        return redirect(url_for("login"))

    roll = session["roll"]
    conn = get_db()
    conn.execute(
        """UPDATE exam_logs
           SET end_time=datetime('now'), termination_reason='Submitted by student'
           WHERE roll=? AND end_time IS NULL""",
        (roll,)
    )
    conn.commit()
    conn.close()
    session.clear()
    return redirect(url_for("result", roll=roll))


@app.route("/result/<roll>")
def result(roll):
    """Post-exam result / summary page."""
    conn = get_db()
    student = conn.execute(
        "SELECT * FROM students WHERE roll=?", (roll,)
    ).fetchone()
    log = conn.execute(
        "SELECT * FROM exam_logs WHERE roll=? ORDER BY id DESC LIMIT 1", (roll,)
    ).fetchone()
    shots = conn.execute(
        "SELECT * FROM screenshots WHERE roll=? ORDER BY captured_at DESC", (roll,)
    ).fetchall()
    conn.close()
    return render_template("result.html", student=student, log=log, shots=shots)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ─────────────────────────────────────────────
#  Entry Point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    print("=" * 60)
    print("  EXAM PROCTORING SYSTEM  — Starting on http://127.0.0.1:5000")
    print("=" * 60)
    app.run(debug=True, threaded=True, port=5000)
