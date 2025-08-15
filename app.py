
from flask import Flask, render_template, request, redirect, session, url_for
import sqlite3
import csv
import os
from pathlib import Path

app = Flask(__name__)
app.secret_key = "super_secret_key"  # ← change this for real use

DB_PATH = "picks.db"
DRIVERS_CSV = "nascar_2025_driver_names.csv"

# ---------- Drivers ----------
def load_drivers():
    if not Path(DRIVERS_CSV).exists():
        raise FileNotFoundError(
            f"Missing {DRIVERS_CSV}. Put it in the same folder as app.py."
        )
    with open(DRIVERS_CSV, newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)  # skip header
        return [row[0] for row in reader if row and row[0].strip()]

drivers = load_drivers()

# ---------- Users (simple demo auth) ----------
users = {
    "player1": "pass1",
    "player2": "pass2",
    "player3": "pass3",
    "player4": "pass4",
}

# ---------- Database ----------
def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS picks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            week INTEGER NOT NULL,
            driver1 TEXT NOT NULL,
            driver2 TEXT NOT NULL,
            driver3 TEXT NOT NULL,
            driver4 TEXT NOT NULL,
            driver5 TEXT NOT NULL,
            driver6 TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value INTEGER
        )
    """)
    # Initialize current week = 1 if not set
    c.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('current_week', 1)")
    conn.commit()
    conn.close()

init_db()

def get_current_week():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT value FROM meta WHERE key='current_week'")
    row = c.fetchone()
    conn.close()
    return int(row[0]) if row else 1

def set_current_week(week: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE meta SET value=? WHERE key='current_week'", (int(week),))
    conn.commit()
    conn.close()

def advance_week():
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE meta SET value = value + 1 WHERE key='current_week'")
    conn.commit()
    conn.close()

def user_has_submitted(username: str, week: int) -> bool:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT 1 FROM picks WHERE username=? AND week=? LIMIT 1", (username, week))
    found = c.fetchone() is not None
    conn.close()
    return found

def save_picks(username: str, week: int, picks: list[str]):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO picks (username, week, driver1, driver2, driver3, driver4, driver5, driver6)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (username, week, *picks))
    conn.commit()
    conn.close()

# ---------- Routes ----------
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if username in users and users[username] == password:
            session["username"] = username
            return redirect(url_for("pick"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/pick", methods=["GET", "POST"])
def pick():
    if "username" not in session:
        return redirect(url_for("login"))

    username = session["username"]
    week = get_current_week()

    # Prevent multiple submissions per week
    if user_has_submitted(username, week):
        return f"You have already submitted your picks for Week {week}. <a href='{url_for('logout')}'>Log out</a>"

    if request.method == "POST":
        picks = request.form.getlist("drivers")
        # Ensure exactly 6 unique drivers
        picks = list(dict.fromkeys(picks))  # de-dup while preserving order
        if len(picks) != 6:
            return "Please select exactly 6 unique drivers.", 400
        save_picks(username, week, picks)
        return redirect(url_for("thanks"))

    return render_template("pick.html", drivers=drivers, week=week)

@app.route("/thanks")
def thanks():
    week = get_current_week()
    return f"Your picks for Week {week} have been saved. Good luck! <a href='{url_for('logout')}'>Log out</a>"

# --- Optional admin helpers ---
@app.route("/next_week")
def next_week():
    # Simple guard: only allow if logged in as player1 (treat as admin)
    if session.get("username") != "player1":
        return "Unauthorized", 403
    advance_week()
    return f"Advanced to Week {get_current_week()}"

@app.route("/set_week/<int:week>")
def set_week(week):
    if session.get("username") != "player1":
        return "Unauthorized", 403
    set_current_week(week)
    return f"Set current week to {get_current_week()}"

# Ready for local & Render deployment


# ---------- Picks viewing helpers ----------
def fetch_picks_for_week(week: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT username, driver1, driver2, driver3, driver4, driver5, driver6
        FROM picks
        WHERE week = ?
        ORDER BY username
    """, (week,))
    rows = c.fetchall()
    conn.close()
    return rows

@app.route("/picks")
def view_picks():
    # Default to current week; allow override via ?week=#
    week_param = request.args.get("week", "").strip()
    if week_param.isdigit():
        week = int(week_param)
    else:
        week = get_current_week()
    rows = fetch_picks_for_week(week)
    return render_template("picks.html", picks=rows, week=week)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
