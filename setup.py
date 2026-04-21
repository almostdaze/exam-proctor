"""
setup.py — One-time setup script for Exam Proctoring System.
Run this before starting the app for the first time.
"""

import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "database", "exam.db")

os.makedirs(os.path.join(BASE_DIR, "database"), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "static", "screenshots"), exist_ok=True)

conn = sqlite3.connect(DB_PATH)
cur  = conn.cursor()

cur.execute("""
    CREATE TABLE IF NOT EXISTS students (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT    NOT NULL,
        roll       TEXT    NOT NULL UNIQUE,
        created_at TEXT    DEFAULT (datetime('now'))
    )
""")

cur.execute("""
    CREATE TABLE IF NOT EXISTS exam_logs (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        roll               TEXT    NOT NULL,
        login_time         TEXT,
        end_time           TEXT,
        warning_count      INTEGER DEFAULT 0,
        termination_reason TEXT
    )
""")

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
print("✓ Database initialized at:", DB_PATH)
print("✓ Screenshots directory ready.")
print("\nSetup complete! Run: python app.py")