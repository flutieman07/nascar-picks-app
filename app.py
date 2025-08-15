
from flask import Flask, render_template, request, redirect, session, url_for, send_file
import sqlite3
import csv
import os
from pathlib import Path
from werkzeug.security import generate_password_hash, check_password_hash
import secrets
import string

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change_me_please")

# --- Persistence: allow DB location via env var (e.g., /var/data/picks.db on Render Disk)
DB_PATH = os.environ.get("DB_PATH", "picks.db")
DB_DIR = os.path.dirname(DB_PATH)
if DB_DIR and not os.path.exists(DB_DIR):
    os.makedirs(DB_DIR, exist_ok=True)

DRIVERS_CSV = "nascar_2025_driver_names.csv"
ROUNDS_TOTAL = 6

def load_drivers():
    if not Path(DRIVERS_CSV).exists():
        raise FileNotFoundError(f"Missing {DRIVERS_CSV}. Put it next to app.py.")
    with open(DRIVERS_CSV, newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        return [row[0] for row in reader if row and row[0].strip()]

drivers = load_drivers()

def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    conn = get_conn(); c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS picks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        week INTEGER NOT NULL,
        driver1 TEXT NOT NULL, driver2 TEXT NOT NULL, driver3 TEXT NOT NULL,
        driver4 TEXT NOT NULL, driver5 TEXT NOT NULL, driver6 TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY, value INTEGER
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS drafts (
        week INTEGER PRIMARY KEY,
        order_csv TEXT NOT NULL,
        current_round INTEGER NOT NULL,
        current_index INTEGER NOT NULL,
        rounds_total INTEGER NOT NULL,
        status TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS draft_picks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        week INTEGER NOT NULL, round INTEGER NOT NULL,
        username TEXT NOT NULL, driver TEXT NOT NULL,
        ts DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        password_hash TEXT,
        is_admin INTEGER NOT NULL DEFAULT 0,
        must_change_pw INTEGER NOT NULL DEFAULT 1
    )""")
    c.execute("INSERT OR IGNORE INTO meta (key,value) VALUES ('current_week',1)")
    conn.commit(); conn.close()

init_db()

def get_user(username):
    conn=get_conn(); c=conn.cursor()
    c.execute("SELECT username, password_hash, is_admin, must_change_pw FROM users WHERE username=?", (username,))
    row=c.fetchone(); conn.close()
    if not row: return None
    return {"username":row[0], "password_hash":row[1], "is_admin":bool(row[2]), "must_change_pw":bool(row[3])}

def list_users():
    conn=get_conn(); c=conn.cursor()
    c.execute("SELECT username, is_admin, (password_hash IS NOT NULL AND password_hash!='') as has_pw, must_change_pw FROM users ORDER BY username")
    rows=[{"username":r[0], "is_admin":bool(r[1]), "has_pw":bool(r[2]), "must_change_pw":bool(r[3])} for r in c.fetchall()]
    conn.close(); return rows

def set_user(username, password, is_admin=False, must_change=True):
    ph = generate_password_hash(password) if password else None
    conn=get_conn(); c=conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (username, password_hash, is_admin, must_change_pw) VALUES (?, ?, ?, ?)",
              (username, ph, int(is_admin), int(must_change)))
    conn.commit(); conn.close()

def reset_user_password(username, temp_password):
    ph = generate_password_hash(temp_password)
    conn=get_conn(); c=conn.cursor()
    c.execute("UPDATE users SET password_hash=?, must_change_pw=1 WHERE username=?", (ph, username))
    conn.commit(); conn.close()

def delete_user(username):
    conn=get_conn(); c=conn.cursor()
    c.execute("DELETE FROM users WHERE username=?", (username,))
    conn.commit(); conn.close()

def generate_temp_password(length=10):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def ensure_seed_users():
    wanted = [
        ("Matt", True),
        ("Mark", False),
        ("Bob", False),
        ("Bill", False),
    ]
    conn=get_conn(); c=conn.cursor()
    temp_pw_map = {}
    for uname, admin in wanted:
        c.execute("SELECT 1 FROM users WHERE username=?", (uname,))
        if not c.fetchone():
            temp = generate_temp_password()
            c.execute("INSERT INTO users (username, password_hash, is_admin, must_change_pw) VALUES (?, ?, ?, 1)",
                      (uname, generate_password_hash(temp), int(admin)))
            temp_pw_map[uname] = temp
    conn.commit(); conn.close()
    return temp_pw_map

seeded = ensure_seed_users()

def get_current_week():
    conn=get_conn(); c=conn.cursor()
    c.execute("SELECT value FROM meta WHERE key='current_week'"); r=c.fetchone()
    conn.close(); return int(r[0]) if r else 1

def set_current_week(week:int):
    conn=get_conn(); c=conn.cursor()
    c.execute("UPDATE meta SET value=? WHERE key='current_week'", (int(week),))
    conn.commit(); conn.close()

def advance_week():
    conn=get_conn(); c=conn.cursor()
    c.execute("UPDATE meta SET value=value+1 WHERE key='current_week'")
    conn.commit(); conn.close()

def user_has_submitted(username, week):
    conn=get_conn(); c=conn.cursor()
    c.execute("SELECT 1 FROM picks WHERE username=? AND week=? LIMIT 1",(username,week))
    ok=c.fetchone() is not None; conn.close(); return ok

def save_picks_row(username, week, picks):
    assert len(picks)==ROUNDS_TOTAL
    conn=get_conn(); c=conn.cursor()
    c.execute("""INSERT INTO picks (username,week,driver1,driver2,driver3,driver4,driver5,driver6)
              VALUES (?,?,?,?,?,?,?,?)""", (username,week,*picks))
    conn.commit(); conn.close()

def get_draft(week):
    conn=get_conn(); c=conn.cursor()
    c.execute("SELECT week,order_csv,current_round,current_index,rounds_total,status FROM drafts WHERE week=?",(week,))
    r=c.fetchone(); conn.close()
    if not r: return None
    return {"week":r[0], "order":r[1].split(","), "current_round":r[2], "current_index":r[3], "rounds_total":r[4], "status":r[5]}

def create_draft(week, order_list):
    conn=get_conn(); c=conn.cursor()
    c.execute("DELETE FROM draft_picks WHERE week=?", (week,))
    c.execute("REPLACE INTO drafts (week,order_csv,current_round,current_index,rounds_total,status) VALUES (?,?,?,?,?,?)",
              (week, ",".join(order_list), 1, 0, ROUNDS_TOTAL, "active"))
    conn.commit(); conn.close()
    return get_draft(week)

def current_player(order, current_round, current_index):
    return (order[current_index] if current_round%2==1 else list(reversed(order))[current_index])

def draft_available_drivers(week):
    conn=get_conn(); c=conn.cursor()
    c.execute("SELECT driver FROM draft_picks WHERE week=?", (week,))
    taken={row[0] for row in c.fetchall()}; conn.close()
    return [d for d in drivers if d not in taken]

def user_draft_picks(week, username):
    conn=get_conn(); c=conn.cursor()
    c.execute("SELECT round,driver FROM draft_picks WHERE week=? AND username=? ORDER BY round ASC", (week, username))
    rows=c.fetchall(); conn.close(); return rows

def add_draft_pick(week, round_no, username, driver):
    conn=get_conn(); c=conn.cursor()
    c.execute("INSERT INTO draft_picks (week,round,username,driver) VALUES (?,?,?,?)",(week,round_no,username,driver))
    conn.commit(); conn.close()

def advance_pointer(draft):
    order=draft["order"]; n=len(order)
    current_round=draft["current_round"]; current_index=draft["current_index"]
    if current_index+1<n:
        new_index=current_index+1; new_round=current_round
    else:
        new_index=0; new_round=current_round+1
    status=draft["status"]
    if new_round>draft["rounds_total"]:
        status="complete"
    conn=get_conn(); c=conn.cursor()
    c.execute("UPDATE drafts SET current_round=?, current_index=?, status=? WHERE week=?",
              (new_round,new_index,status,draft["week"]))
    conn.commit(); conn.close()

def consolidate_to_picks(week):
    conn=get_conn(); c=conn.cursor()
    c.execute("SELECT DISTINCT username FROM draft_picks WHERE week=?", (week,))
    users_in_draft=[r[0] for r in c.fetchall()]
    for uname in users_in_draft:
        c.execute("SELECT driver FROM draft_picks WHERE week=? AND username=? ORDER BY round ASC",(week,uname))
        ds=[r[0] for r in c.fetchall()]
        if len(ds)==ROUNDS_TOTAL:
            c.execute("SELECT 1 FROM picks WHERE week=? AND username=? LIMIT 1",(week,uname))
            if not c.fetchone():
                c.execute("""INSERT INTO picks (username,week,driver1,driver2,driver3,driver4,driver5,driver6)
                          VALUES (?,?,?,?,?,?,?,?)""", (uname,week,*ds))
    conn.commit(); conn.close()

@app.route("/", methods=["GET","POST"])
def login():
    if request.method=="POST":
        u=request.form.get("username","").strip(); p=request.form.get("password","")
        user = get_user(u)
        if user and user["password_hash"] and check_password_hash(user["password_hash"], p):
            session["username"]=u
            session["is_admin"]=user["is_admin"]
            if user["must_change_pw"]:
                return redirect(url_for("change_password"))
            return redirect(url_for("draft"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

@app.route("/admin_users", methods=["GET","POST"])
def admin_users():
    if not session.get("is_admin"): return "Unauthorized",403
    message=None
    temp_pw=None
    if request.method=="POST":
        action = request.form.get("action")
        if action=="create":
            uname = request.form.get("username","").strip()
            is_admin = 1 if request.form.get("is_admin")=="on" else 0
            if not uname:
                message="Username is required."
            else:
                temp_pw = generate_temp_password()
                set_user(uname, temp_pw, is_admin=bool(is_admin), must_change=True)
                message=f"Created {uname}. Temporary password shown below; share it securely."
        elif action=="reset":
            uname = request.form.get("username","").strip()
            if not uname:
                message="Username is required for reset."
            else:
                temp_pw = generate_temp_password()
                reset_user_password(uname, temp_pw)
                message=f"Reset password for {uname}. Temporary password shown below; share it securely."
        elif action=="delete":
            uname = request.form.get("username","").strip()
            if not uname:
                message="Username is required for delete."
            else:
                delete_user(uname)
                message=f"Deleted {uname}."
    return render_template("admin_users.html", users=list_users(), message=message, temp_pw=temp_pw)

@app.route("/change_password", methods=["GET","POST"])
def change_password():
    if "username" not in session: return redirect(url_for("login"))
    message=None
    if request.method=="POST":
        current = request.form.get("current","")
        new1 = request.form.get("new1","")
        new2 = request.form.get("new2","")
        user = get_user(session["username"])
        if not user or not user["password_hash"] or not check_password_hash(user["password_hash"], current):
            message="Current password is incorrect."
        elif len(new1) < 8:
            message="New password must be at least 8 characters."
        elif new1 != new2:
            message="New passwords do not match."
        else:
            ph = generate_password_hash(new1)
            conn=get_conn(); c=conn.cursor()
            c.execute("UPDATE users SET password_hash=?, must_change_pw=0 WHERE username=?", (ph, session["username"]))
            conn.commit(); conn.close()
            return redirect(url_for("draft"))
    return render_template("change_password.html", message=message)

@app.route("/admin_order", methods=["GET","POST"])
def admin_order():
    if not session.get("is_admin"): return "Unauthorized",403
    week = get_current_week()
    message = None
    if request.method=="POST":
        raw = request.form.get("order","").strip()
        order = [x.strip() for x in raw.split(",") if x.strip()]
        valid_users = {u["username"] for u in list_users()}
        if not order:
            message = "Please enter a comma-separated list of usernames."
        elif any(o not in valid_users for o in order):
            message = f"Unknown username in list. Valid: {', '.join(sorted(valid_users))}"
        elif len(order) != len(valid_users):
            message = f"Please include each user exactly once: {', '.join(sorted(valid_users))}"
        else:
            create_draft(week, order)
            return redirect(url_for('order'))
    d = get_draft(week)
    current_order = d["order"] if d else None
    return render_template("admin_order.html", week=week, valid=[u["username"] for u in list_users()],
                           message=message, current_order=current_order)

@app.route("/order")
def order():
    week = get_current_week()
    d = get_draft(week)
    return render_template("order.html", week=week, draft=d)

@app.route("/start_draft")
def start_draft():
    if not session.get("is_admin"): return "Unauthorized",403
    week=get_current_week()
    if not get_draft(week):
        default_order = [u["username"] for u in list_users()]
        create_draft(week, default_order)
    return redirect(url_for("draft", week=week))

@app.route("/draft", methods=["GET","POST"])
def draft():
    if "username" not in session: return redirect(url_for("login"))
    username=session["username"]
    week_param=request.args.get("week","").strip()
    week=int(week_param) if week_param.isdigit() else get_current_week()
    d=get_draft(week)
    if not d:
        msg="Draft not started yet. Ask the admin to set order at /admin_order or visit /start_draft"
        return msg
    available=draft_available_drivers(week)
    my_picks=user_draft_picks(week, username)
    on_the_clock=(d["order"][d["current_index"]] if d["current_round"]%2==1 else list(reversed(d["order"]))[d["current_index"]])
    if request.method=="POST":
        if d["status"]=="complete": return redirect(url_for("draft", week=week))
        chosen=request.form.get("driver","").strip()
        d=get_draft(week); on_the_clock=(d["order"][d["current_index"]] if d["current_round"]%2==1 else list(reversed(d["order"]))[d["current_index"]])
        if username!=on_the_clock: return "Not your turn.",403
        if chosen not in available: return "Driver not available.",400
        add_draft_pick(week, d["current_round"], username, chosen)
        advance_pointer(d)
        d2=get_draft(week)
        if d2["status"]=="complete": consolidate_to_picks(week)
        return redirect(url_for("draft", week=week))
    d=get_draft(week)
    on_the_clock=(d["order"][d["current_index"]] if d["current_round"]%2==1 else list(reversed(d["order"]))[d["current_index"]])
    is_my_turn=(username==on_the_clock)
    available=draft_available_drivers(week)
    return render_template("draft.html", draft=d, available=available, my_picks=my_picks,
                           username=username, is_my_turn=is_my_turn, on_the_clock=on_the_clock)

@app.route("/picks")
def view_picks():
    week_param=request.args.get("week","").strip()
    week=int(week_param) if week_param.isdigit() else get_current_week()
    conn=get_conn(); c=conn.cursor()
    c.execute("""SELECT username,driver1,driver2,driver3,driver4,driver5,driver6
                 FROM picks WHERE week=? ORDER BY username""", (week,))
    rows=c.fetchall(); conn.close()
    return render_template("picks.html", picks=rows, week=week)

# --- Admin backup: download the SQLite DB file
@app.route("/admin_backup")
def admin_backup():
    if not session.get("is_admin"): return "Unauthorized", 403
    # Ensure file exists
    if not os.path.exists(DB_PATH):
        return "No database found.", 404
    return send_file(DB_PATH, as_attachment=True, download_name=os.path.basename(DB_PATH))

@app.route("/next_week")
def next_week():
    if not session.get("is_admin"): return "Unauthorized",403
    advance_week(); return f"Advanced to Week {get_current_week()}"

@app.route("/set_week/<int:week>")
def set_week(week):
    if not session.get("is_admin"): return "Unauthorized",403
    set_current_week(week); return f"Set current week to {get_current_week()}"

if __name__=="__main__":
    port=int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0", port=port)
