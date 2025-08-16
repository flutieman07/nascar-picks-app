
from flask import Flask, render_template, request, redirect, session, url_for, send_file, jsonify
import sqlite3
import csv
import os
from io import StringIO
from pathlib import Path
from werkzeug.security import generate_password_hash, check_password_hash
import secrets
import string

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change_me_please")

DB_PATH = os.environ.get("DB_PATH", "picks.db")
DB_DIR = os.path.dirname(DB_PATH)
if DB_DIR and not os.path.exists(DB_DIR):
    os.makedirs(DB_DIR, exist_ok=True)

DRIVERS_CSV = "nascar_2025_driver_names.csv"
ROUNDS_TOTAL = 6

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
    c.execute("""CREATE TABLE IF NOT EXISTS schedule (
        week INTEGER PRIMARY KEY,
        race_name TEXT NOT NULL,
        race_date TEXT
    )""")
    # NEW: persistent drivers table
    c.execute("""CREATE TABLE IF NOT EXISTS drivers (
        name TEXT PRIMARY KEY
    )""")
    c.execute("INSERT OR IGNORE INTO meta (key,value) VALUES ('current_week',1)")
    conn.commit()
    # Seed drivers from CSV on first run (idempotent)
    try:
        csv_path = Path(DRIVERS_CSV)
        if csv_path.exists():
            with open(csv_path, newline='') as f:
                reader = csv.reader(f); header = next(reader, None)
                for row in reader:
                    if not row: continue
                    name = row[0].strip()
                    if name:
                        c.execute("INSERT OR IGNORE INTO drivers (name) VALUES (?)", (name,))
            conn.commit()
    except Exception:
        pass
    conn.close()

init_db()

# --- Helpers ---
def last_name_key(full_name:str):
    tokens = full_name.replace('.', '').split()
    if not tokens: return ('', full_name.lower())
    suffixes = {'jr','sr','ii','iii','iv','v'}
    last = tokens[-1].lower()
    if last in suffixes and len(tokens) >= 2:
        last = tokens[-2].lower()
    return (last, full_name.lower())

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

def user_names():
    return [u["username"] for u in list_users()]

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

def ensure_seed_users():
    wanted = [("Matt", True), ("Mark", False), ("Bob", False), ("Bill", False)]
    conn=get_conn(); c=conn.cursor()
    for uname, admin in wanted:
        c.execute("SELECT 1 FROM users WHERE username=?", (uname,))
        if not c.fetchone():
            c.execute("INSERT INTO users (username, password_hash, is_admin, must_change_pw) VALUES (?, NULL, ?, 1)",
                      (uname, int(admin)))
    conn.commit(); conn.close()

ensure_seed_users()

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

def get_schedule_entry(week:int):
    conn=get_conn(); c=conn.cursor()
    c.execute("SELECT race_name, race_date FROM schedule WHERE week=?", (week,))
    r=c.fetchone(); conn.close()
    if not r: return None
    return {"race_name": r[0], "race_date": r[1]}

def list_schedule():
    conn=get_conn(); c=conn.cursor()
    c.execute("SELECT week, race_name, COALESCE(race_date,'') FROM schedule ORDER BY week")
    rows=[{"week":r[0], "race_name":r[1], "race_date":r[2]} for r in c.fetchall()]
    conn.close(); return rows

def upsert_schedule(rows):
    conn=get_conn(); c=conn.cursor()
    for wk, name, date in rows:
        c.execute("REPLACE INTO schedule (week, race_name, race_date) VALUES (?,?,?)", (wk, name, date))
    conn.commit(); conn.close()

# --- Drivers helpers ---
def list_all_drivers():
    conn=get_conn(); c=conn.cursor()
    c.execute("SELECT name FROM drivers")
    names=[r[0] for r in c.fetchall()]
    conn.close()
    return sorted(names, key=last_name_key)

def add_driver(name:str):
    name=name.strip()
    if not name: return
    conn=get_conn(); c=conn.cursor()
    c.execute("INSERT OR IGNORE INTO drivers (name) VALUES (?)", (name,))
    conn.commit(); conn.close()

# --- Draft helpers ---
def draft_available_drivers(week):
    conn=get_conn(); c=conn.cursor()
    c.execute("SELECT driver FROM draft_picks WHERE week=?", (week,))
    taken={row[0] for row in c.fetchall()}
    c.execute("SELECT name FROM drivers")
    all_drivers=[r[0] for r in c.fetchall()]
    conn.close()
    available=[d for d in all_drivers if d not in taken]
    return sorted(available, key=last_name_key)

def user_draft_picks(week, username):
    conn=get_conn(); c=conn.cursor()
    c.execute("SELECT round,driver FROM draft_picks WHERE week=? AND username=? ORDER BY round ASC", (week, username))
    rows=c.fetchall(); conn.close(); return rows

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

# --- Routes ---
@app.route("/", methods=["GET","POST"])
def login():
    if request.method=="POST":
        u=request.form.get("username","").strip(); p=request.form.get("password","")
        user = get_user(u)
        if user and user["password_hash"] and check_password_hash(user["password_hash"], p):
            session["username"]=u
            session["is_admin"]=user["is_admin"]
            session['just_logged_in']=True
            if user["must_change_pw"]:
                return redirect(url_for("change_password"))
            return redirect(url_for("post_login"))
    return render_template("login.html")

@app.route("/post_login")
def post_login():
    if "username" not in session:
        return redirect(url_for("login"))
    just = session.pop('just_logged_in', False)
    week = get_current_week()
    d = get_draft(week)
    if just and d and d['status'] == 'complete':
        return redirect(url_for('all_picks', week=week))
    return redirect(url_for('draft', week=week))

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

@app.route("/admin_users", methods=["GET","POST"])
def admin_users():
    if not session.get("is_admin"): return "Unauthorized",403
    message=None; temp_pw=None
    if request.method=="POST":
        action = request.form.get("action")
        if action=="create":
            uname = request.form.get("username","").strip()
            is_admin = 1 if request.form.get("is_admin")=="on" else 0
            if not uname: message="Username is required."
            else:
                temp_pw = secrets.token_urlsafe(8)
                set_user(uname, temp_pw, is_admin=bool(is_admin), must_change=True)
                message=f"Created {uname}. Temporary password shown below; share it securely."
        elif action=="reset":
            uname = request.form.get("username","").strip()
            if not uname: message="Username is required for reset."
            else:
                temp_pw = secrets.token_urlsafe(8)
                reset_user_password(uname, temp_pw)
                message=f"Reset password for {uname}. Temporary password shown below; share it securely."
        elif action=="delete":
            uname = request.form.get("username","").strip()
            if not uname: message="Username is required for delete."
            else:
                delete_user(uname); message=f"Deleted {uname}."
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

@app.route("/admin_schedule", methods=["GET","POST"])
def admin_schedule():
    if not session.get("is_admin"): return "Unauthorized",403
    message=None
    if request.method=="POST":
        csv_text = request.form.get("csv_text","").strip()
        if not csv_text:
            message="Please paste CSV with columns: week,race_name,race_date"
        else:
            try:
                reader = csv.DictReader(StringIO(csv_text))
                rows=[]
                for row in reader:
                    wk = int(row.get("week","").strip())
                    name = row.get("race_name","").strip()
                    date = row.get("race_date","").strip() if row.get("race_date") else None
                    if not wk or not name:
                        raise ValueError("Missing week or race_name")
                    rows.append((wk, name, date))
                upsert_schedule(rows)
                message=f"Imported {len(rows)} schedule entries."
            except Exception as e:
                message=f"Failed to import: {e}"
    sched = list_schedule()
    return render_template("admin_schedule.html", schedule=sched, message=message)

@app.route("/order")
def order():
    week = get_current_week()
    d = get_draft(week)
    sched = get_schedule_entry(week)
    return render_template("order.html", week=week, draft=d, sched=sched)

@app.route("/admin_order", methods=["GET","POST"])
def admin_order():
    if not session.get("is_admin"): return "Unauthorized",403
    schedule_rows = list_schedule()
    current_week = get_current_week()
    message=None
    if request.method=="POST":
        week_str = request.form.get("week") or ""
        try:
            week = int(week_str)
        except:
            week = current_week
        raw = request.form.get("order","").strip()
        order = [x.strip() for x in raw.split(",") if x.strip()]
        valid_users = {u['username'] for u in list_users()}
        if not order:
            message = "Please enter a comma-separated list of usernames."
        elif any(o not in valid_users for o in order):
            message = f"Unknown username in list. Valid: {', '.join(sorted(valid_users))}"
        elif len(order) != len(valid_users):
            message = f"Please include each user exactly once: {', '.join(sorted(valid_users))}"
        else:
            create_draft(week, order)
            set_current_week(week)
            return redirect(url_for('draft', week=week))
    d = get_draft(current_week)
    current_order = d["order"] if d else None
    sched = get_schedule_entry(current_week)
    return render_template("admin_order.html",
                           week=current_week, valid=[u['username'] for u in list_users()],
                           message=message, current_order=current_order,
                           sched=sched, schedule_list=schedule_rows)

@app.route("/admin_reset_picks", methods=["GET","POST"])
def admin_reset_picks():
    if not session.get("is_admin"): return "Unauthorized", 403
    message=None; week=get_current_week()
    if request.method=="POST":
        target_week = request.form.get("week","").strip()
        confirm = request.form.get("confirm","") == "on"
        if not target_week.isdigit():
            message = "Please enter a valid week number."
        elif not confirm:
            message = "You must check the confirmation box to proceed."
        else:
            target_week = int(target_week)
            conn=get_conn(); c=conn.cursor()
            c.execute("DELETE FROM draft_picks WHERE week=?", (target_week,))
            c.execute("DELETE FROM drafts WHERE week=?", (target_week,))
            c.execute("DELETE FROM picks WHERE week=?", (target_week,))
            conn.commit(); conn.close()
            return render_template("admin_reset_picks.html", week=week, message=f"All picks and draft state for Week {target_week} have been reset.", done=True)
    return render_template("admin_reset_picks.html", week=week, message=message, done=False)

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
        return "Draft not started yet. Ask the admin to set order at /admin_order or visit /start_draft"

    available=draft_available_drivers(week)
    my_picks=user_draft_picks(week, username)
    on_the_clock=(d["order"][d["current_index"]] if d["current_round"]%2==1 else list(reversed(d["order"]))[d["current_index"]])

    if request.method=="POST":
        if d["status"]=="complete": return redirect(url_for("draft", week=week))
        chosen=request.form.get("driver","").strip()
        custom=request.form.get("custom_driver","").strip()
        d=get_draft(week)
        on_the_clock=(d["order"][d["current_index"]] if d["current_round"]%2==1 else list(reversed(d["order"]))[d["current_index"]])
        if username!=on_the_clock: return "Not your turn.",403

        # If custom driver provided, add to drivers table (persist for future)
        if custom:
            add_driver(custom)
            chosen = custom

        # Recompute availability after possibly adding custom
        available_now = draft_available_drivers(week)
        if chosen not in available_now:
            return "Driver not available.",400

        add_draft_pick(week, d["current_round"], username, chosen)
        advance_pointer(d)
        d2=get_draft(week)
        if d2["status"]=="complete": consolidate_to_picks(week)
        return redirect(url_for("draft", week=week))

    sched = get_schedule_entry(week)
    schedule_list = list_schedule()
    return render_template("draft.html", draft=d, available=available, my_picks=my_picks,
                           username=username, is_my_turn=(username==on_the_clock), on_the_clock=on_the_clock,
                           sched=sched, schedule_list=schedule_list, current_week=week, rounds_total=ROUNDS_TOTAL)

@app.route("/draft_state")
def draft_state():
    week_param = request.args.get("week","").strip()
    week = int(week_param) if week_param.isdigit() else get_current_week()
    d = get_draft(week)
    if not d:
        return jsonify({"error":"no_draft"}), 404

    conn=get_conn(); c=conn.cursor()
    c.execute("SELECT round, username, driver, ts FROM draft_picks WHERE week=? ORDER BY round ASC, ts ASC, id ASC", (week,))
    picks_rows=c.fetchall()
    conn.close()

    picks=[{"round":r, "username":u, "driver":dr, "ts":ts} for (r,u,dr,ts) in picks_rows]

    grid = {}
    order = d["order"]
    all_users = set(user_names()) | set(order)
    for u in order + sorted(all_users - set(order)):
        grid[u] = {i: "" for i in range(1, d["rounds_total"]+1)}
    for p in picks:
        grid[p["username"]][p["round"]] = p["driver"]

    on_the_clock = (order[d["current_index"]] if d["current_round"]%2==1 else list(reversed(order))[d["current_index"]])
    available = draft_available_drivers(week)

    return jsonify({
        "week": week,
        "status": d["status"],
        "current_round": d["current_round"],
        "current_index": d["current_index"],
        "on_the_clock": on_the_clock,
        "order": order,
        "rounds_total": d["rounds_total"],
        "picks": picks,
        "grid": grid,
        "available": available
    })

@app.route("/all_picks")
def all_picks():
    week_param = request.args.get("week","").strip()
    week = int(week_param) if week_param.isdigit() else get_current_week()
    conn=get_conn(); c=conn.cursor()
    c.execute("SELECT DISTINCT week FROM picks ORDER BY week")
    weeks = [r[0] for r in c.fetchall()]
    if week not in weeks:
        if not weeks: weeks=[week]
    c.execute("""SELECT username, driver1, driver2, driver3, driver4, driver5, driver6
                 FROM picks WHERE week=? ORDER BY username""", (week,))
    rows=c.fetchall(); conn.close()
    sched = get_schedule_entry(week)
    return render_template("all_picks.html", week=week, weeks=weeks, picks=rows, sched=sched)

@app.route("/picks")
def view_picks():
    week_param=request.args.get("week","").strip()
    week=int(week_param) if week_param.isdigit() else get_current_week()
    conn=get_conn(); c=conn.cursor()
    c.execute("""SELECT username,driver1,driver2,driver3,driver4,driver5,driver6
                 FROM picks WHERE week=? ORDER BY username""", (week,))
    rows=c.fetchall(); conn.close()
    sched = get_schedule_entry(week)
    return render_template("picks.html", picks=rows, week=week, sched=sched)

@app.route("/schedule")
def schedule_page():
    sched = list_schedule()
    return render_template("schedule.html", schedule=sched)

@app.route("/admin_backup")
def admin_backup():
    if not session.get("is_admin"): return "Unauthorized", 403
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
