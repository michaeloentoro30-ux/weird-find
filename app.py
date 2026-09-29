import os, sqlite3, secrets
from datetime import datetime
from functools import wraps
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE = Path(__file__).resolve().parent
DB = BASE / "durex_finds.db"
UPLOADS = BASE / "static" / "uploads"
UPLOADS.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-change-this-secret-key")
ADMIN_PIN = os.getenv("ADMIN_PIN", "212523")
MAX_UPLOAD_MB = 8
ALLOWED = {"png", "jpg", "jpeg", "webp", "gif"}

RARITIES = {
    "common": {"label":"Common", "points":2},
    "uncommon": {"label":"Uncommon", "points":4},
    "rare": {"label":"Rare", "points":6},
    "legendary": {"label":"Legendary", "points":10},
    "mythical": {"label":"Mythical", "points":0},
}
SHOP = {
    "icecream": {"name":"Ice Cream Coupon", "cost":40, "emoji":"🍦"},
    "yupi": {"name":"Yupi Coupon", "cost":45, "emoji":"🍬"},
}

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con

def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        points INTEGER NOT NULL DEFAULT 0,
        theme TEXT NOT NULL DEFAULT 'classic',
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS finds(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        image_path TEXT NOT NULL,
        caption TEXT NOT NULL,
        description TEXT DEFAULT '',
        rarity TEXT NOT NULL DEFAULT 'common',
        status TEXT NOT NULL DEFAULT 'pending',
        page_number INTEGER NOT NULL DEFAULT 1,
        attachment TEXT NOT NULL DEFAULT 'tape',
        approved_by INTEGER,
        uploaded_at TEXT NOT NULL,
        approved_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(approved_by) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS point_transactions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        reason TEXT NOT NULL,
        find_id INTEGER,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(find_id) REFERENCES finds(id)
    );
    CREATE TABLE IF NOT EXISTS inventory(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        item_key TEXT NOT NULL,
        item_name TEXT NOT NULL,
        purchase_cost INTEGER NOT NULL,
        purchased_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'not_given',
        given_at TEXT
    );
    CREATE TABLE IF NOT EXISTS admin_logs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_user_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        target_id INTEGER,
        created_at TEXT NOT NULL
    );
    """)
    con.commit()
    con.close()

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def current_user():
    uid = session.get("user_id")
    if not uid: return None
    con=db()
    u=con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    con.close()
    return u

def login_required(f):
    @wraps(f)
    def w(*a, **kw):
        if not current_user():
            flash("Please log in first.")
            return redirect(url_for("login"))
        return f(*a, **kw)
    return w

def admin_required(f):
    @wraps(f)
    def w(*a, **kw):
        if not session.get("admin_ok"):
            flash("Admin access required.")
            return redirect(url_for("admin_pin"))
        return f(*a, **kw)
    return w

def recompute_pages(con, rarity):
    rows=con.execute("SELECT id FROM finds WHERE rarity=? AND status='approved' ORDER BY approved_at,id", (rarity,)).fetchall()
    for i,r in enumerate(rows):
        con.execute("UPDATE finds SET page_number=? WHERE id=?", (i//12+1, r["id"]))

@app.context_processor
def inject():
    u=current_user()
    return {"user":u, "rarities":RARITIES, "shop":SHOP}

@app.route("/")
def index():
    con=db()
    featured=con.execute("""
      SELECT f.*,u.username FROM finds f JOIN users u ON u.id=f.user_id
      WHERE f.status='approved' ORDER BY f.approved_at DESC LIMIT 8
    """).fetchall()
    con.close()
    return render_template("index.html", featured=featured)

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method=="POST":
        username=request.form.get("username","").strip()
        password=request.form.get("password","")
        confirm=request.form.get("confirm","")
        if len(username)<3 or len(username)>24 or not username.replace("_","").isalnum():
            flash("Username must be 3–24 characters using letters, numbers or _.")
        elif len(password)<6:
            flash("Password must be at least 6 characters.")
        elif password!=confirm:
            flash("Passwords do not match.")
        else:
            con=db()
            try:
                con.execute("INSERT INTO users(username,password_hash,created_at) VALUES(?,?,?)",
                            (username,generate_password_hash(password),now()))
                con.commit()
                flash("Account created. You can log in now.")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("That username is already taken.")
            finally: con.close()
    return render_template("auth.html", mode="register")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        username=request.form.get("username","").strip()
        password=request.form.get("password","")
        con=db(); u=con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone(); con.close()
        if u and check_password_hash(u["password_hash"],password):
            session.clear(); session["user_id"]=u["id"]
            return redirect(url_for("book", rarity="common"))
        flash("Invalid username or password.")
    return render_template("auth.html", mode="login")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/book/<rarity>")
@login_required
def book(rarity):
    rarity=rarity.lower()
    if rarity not in RARITIES: abort(404)
    page=max(1, int(request.args.get("page",1)))
    con=db()
    finds=con.execute("""SELECT f.*,u.username FROM finds f JOIN users u ON u.id=f.user_id
                         WHERE f.rarity=? AND f.status='approved' AND f.page_number=?
                         ORDER BY f.approved_at,f.id""",(rarity,page)).fetchall()
    maxpage=con.execute("SELECT COALESCE(MAX(page_number),1) m FROM finds WHERE rarity=? AND status='approved'",(rarity,)).fetchone()["m"]
    con.close()
    return render_template("book.html", rarity=rarity, finds=finds, page=page, maxpage=maxpage)

@app.route("/find/<int:find_id>")
def find_detail(find_id):
    con=db()
    f=con.execute("SELECT f.*,u.username FROM finds f JOIN users u ON u.id=f.user_id WHERE f.id=?",(find_id,)).fetchone()
    con.close()
    if not f: abort(404)
    return render_template("find.html", find=f)

@app.route("/upload", methods=["GET","POST"])
@login_required
def upload():
    if request.method=="POST":
        file=request.files.get("image")
        caption=request.form.get("caption","").strip()
        desc=request.form.get("description","").strip()
        if not file or not file.filename:
            flash("Choose an image.")
        elif not caption:
            flash("Add a caption.")
        else:
            ext=file.filename.rsplit(".",1)[-1].lower() if "." in file.filename else ""
            if ext not in ALLOWED:
                flash("Allowed: PNG, JPG, JPEG, WEBP, GIF.")
            else:
                safe=secure_filename(file.filename)
                name=f"{secrets.token_hex(8)}_{safe}"
                file.save(UPLOADS/name)
                con=db()
                con.execute("""INSERT INTO finds(user_id,image_path,caption,description,uploaded_at)
                               VALUES(?,?,?,?,?)""",(session["user_id"],f"uploads/{name}",caption,desc,now()))
                con.commit(); con.close()
                flash("Find submitted for host review!")
                return redirect(url_for("book",rarity="common"))
    return render_template("upload.html")

@app.route("/profile")
@login_required
def profile():
    con=db()
    finds=con.execute("SELECT * FROM finds WHERE user_id=? ORDER BY uploaded_at DESC",(session["user_id"],)).fetchall()
    tx=con.execute("SELECT * FROM point_transactions WHERE user_id=? ORDER BY created_at DESC LIMIT 20",(session["user_id"],)).fetchall()
    con.close()
    return render_template("profile.html", finds=finds, tx=tx)

@app.route("/theme", methods=["POST"])
@login_required
def theme():
    t=request.form.get("theme","classic")
    if t not in {"classic","dark","lab","mystery"}: t="classic"
    con=db(); con.execute("UPDATE users SET theme=? WHERE id=?",(t,session["user_id"])); con.commit(); con.close()
    flash("Theme updated.")
    return redirect(request.referrer or url_for("index"))

@app.route("/shop")
@login_required
def shop_page():
    return render_template("shop.html")

@app.route("/shop/buy/<item>", methods=["POST"])
@login_required
def buy(item):
    if item not in SHOP: abort(404)
    con=db()
    u=con.execute("SELECT points FROM users WHERE id=?",(session["user_id"],)).fetchone()
    cost=SHOP[item]["cost"]
    if u["points"] < cost:
        con.close(); flash("Not enough points."); return redirect(url_for("shop_page"))
    con.execute("UPDATE users SET points=points-? WHERE id=?",(cost,session["user_id"]))
    con.execute("""INSERT INTO inventory(user_id,item_key,item_name,purchase_cost,purchased_at)
                   VALUES(?,?,?,?,?)""",(session["user_id"],item,SHOP[item]["name"],cost,now()))
    con.execute("""INSERT INTO point_transactions(user_id,amount,reason,created_at)
                   VALUES(?,?,?,?)""",(session["user_id"],-cost,f"Redeemed {SHOP[item]['name']}",now()))
    con.commit(); con.close()
    flash(f"{SHOP[item]['name']} added to your inventory.")
    return redirect(url_for("inventory"))

@app.route("/inventory")
@login_required
def inventory():
    con=db()
    items=con.execute("SELECT * FROM inventory WHERE user_id=? ORDER BY purchased_at DESC",(session["user_id"],)).fetchall()
    con.close()
    return render_template("inventory.html",items=items)

@app.route("/inventory/<int:item_id>/given", methods=["POST"])
@login_required
def given(item_id):
    con=db()
    item=con.execute("SELECT * FROM inventory WHERE id=? AND user_id=?",(item_id,session["user_id"])).fetchone()
    if not item:
        con.close(); abort(404)
    if item["status"]!="given":
        con.execute("UPDATE inventory SET status='given',given_at=? WHERE id=?",(now(),item_id))
        con.commit()
    con.close()
    return redirect(url_for("inventory"))

@app.route("/admin-pin", methods=["GET","POST"])
@login_required
def admin_pin():
    if request.method=="POST":
        pin=request.form.get("pin","")
        if secrets.compare_digest(pin, ADMIN_PIN):
            session["admin_ok"]=True
            return redirect(url_for("admin"))
        flash("Incorrect admin PIN.")
    return render_template("admin_pin.html")

@app.route("/admin")
@login_required
@admin_required
def admin():
    con=db()
    pending=con.execute("""SELECT f.*,u.username FROM finds f JOIN users u ON u.id=f.user_id
                           WHERE f.status='pending' ORDER BY f.uploaded_at""").fetchall()
    users=con.execute("SELECT id,username,points,created_at FROM users ORDER BY points DESC").fetchall()
    rewards=con.execute("SELECT * FROM inventory ORDER BY purchased_at DESC LIMIT 50").fetchall()
    con.close()
    return render_template("admin.html",pending=pending,users=users,rewards=rewards)

@app.route("/admin/review/<int:find_id>", methods=["POST"])
@login_required
@admin_required
def review(find_id):
    rarity=request.form.get("rarity","common")
    action=request.form.get("action","approve")
    if rarity not in RARITIES: abort(400)
    con=db()
    f=con.execute("SELECT * FROM finds WHERE id=?",(find_id,)).fetchone()
    if not f: con.close(); abort(404)
    old_points=RARITIES.get(f["rarity"],{"points":0})["points"] if f["status"]=="approved" else 0
    new_points=RARITIES[rarity]["points"] if action=="approve" else 0
    if action=="approve":
        con.execute("""UPDATE finds SET status='approved',rarity=?,approved_by=?,approved_at=? WHERE id=?""",
                    (rarity,session["user_id"],now(),find_id))
    else:
        con.execute("UPDATE finds SET status='rejected',approved_by=?,approved_at=? WHERE id=?",
                    (session["user_id"],now(),find_id))
    delta=new_points-old_points
    if delta:
        con.execute("UPDATE users SET points=points+? WHERE id=?",(delta,f["user_id"]))
        con.execute("""INSERT INTO point_transactions(user_id,amount,reason,find_id,created_at)
                       VALUES(?,?,?,?,?)""",(f["user_id"],delta,f"{RARITIES[rarity]['label']} find approved",find_id,now()))
    recompute_pages(con, rarity)
    if old_points and f["rarity"]!=rarity: recompute_pages(con,f["rarity"])
    con.execute("INSERT INTO admin_logs(admin_user_id,action,target_id,created_at) VALUES(?,?,?,?)",
                (session["user_id"],f"{action}:{rarity}",find_id,now()))
    con.commit(); con.close()
    return redirect(url_for("admin"))

@app.route("/admin/move/<int:find_id>", methods=["POST"])
@login_required
@admin_required
def move(find_id):
    rarity=request.form.get("rarity","common")
    if rarity not in RARITIES: abort(400)
    con=db()
    f=con.execute("SELECT * FROM finds WHERE id=?",(find_id,)).fetchone()
    if not f: con.close(); abort(404)
    old=RARITIES[f["rarity"]]["points"]
    new=RARITIES[rarity]["points"]
    delta=new-old
    con.execute("UPDATE finds SET rarity=? WHERE id=?",(rarity,find_id))
    if delta:
        con.execute("UPDATE users SET points=points+? WHERE id=?",(delta,f["user_id"]))
        con.execute("INSERT INTO point_transactions(user_id,amount,reason,find_id,created_at) VALUES(?,?,?,?,?)",
                    (f["user_id"],delta,f"Rarity changed to {rarity}",find_id,now()))
    recompute_pages(con, f["rarity"]); recompute_pages(con, rarity)
    con.commit(); con.close()
    return redirect(url_for("admin"))

@app.route("/admin/reward/<int:item_id>/given", methods=["POST"])
@login_required
@admin_required
def admin_reward_given(item_id):
    con=db()
    con.execute("UPDATE inventory SET status='given',given_at=? WHERE id=?",(now(),item_id))
    con.commit(); con.close()
    return redirect(url_for("admin"))

@app.route("/uploads/<path:name>")
def uploads(name):
    return send_from_directory(UPLOADS,name)

if __name__=="__main__":
    init_db()
    app.run(debug=True)
