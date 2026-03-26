from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify, abort, make_response)
import json, os, hashlib, uuid, re, time, logging
from functools import wraps
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename

# ── ENV ────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ── APP ────────────────────────────────────────────────────────
app = Flask(__name__)

SECRET = os.environ.get("FFS_SECRET", "")
if not SECRET:
    import secrets as _s
    SECRET = _s.token_hex(32)
    logging.warning("FFS_SECRET not set — using a random key (sessions won't survive restarts).")
app.secret_key = SECRET

app.config.update(
    SESSION_COOKIE_HTTPONLY   = True,
    SESSION_COOKIE_SAMESITE   = "Lax",
    SESSION_COOKIE_SECURE     = os.environ.get("HTTPS", "0") == "1",
    PERMANENT_SESSION_LIFETIME= timedelta(hours=8),
    MAX_CONTENT_LENGTH        = 8 * 1024 * 1024,  # 8 MB
)

# ── UPLOADS ────────────────────────────────────────────────────
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXT   = {"png", "jpg", "jpeg", "gif", "webp"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def _allowed(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT

def save_image(field: str) -> str:
    f = request.files.get(field)
    if not (f and f.filename and _allowed(f.filename)):
        return ""
    ext      = secure_filename(f.filename).rsplit(".", 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    f.save(os.path.join(UPLOAD_FOLDER, filename))
    return filename

def delete_image(filename: str):
    if filename:
        path = os.path.join(UPLOAD_FOLDER, os.path.basename(filename))
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass

# ── DATA ───────────────────────────────────────────────────────
DATA_FILE = os.path.join(BASE_DIR, "data.json")

DEFAULT_DATA = {
    "admin": {
        "username":     "admin",
        "password":     hashlib.sha256("admin123".encode()).hexdigest(),
        "pbkdf2":       False,
        "force_change": True
    },
    "site_settings": {
        "company_name": "Shakthi Pack Machineries",
        "phone":        "+91 00000 00000",
        "email":        "info@shakthipack.com",
        "address":      "Chennai, Tamil Nadu, India"
    },
    "machine_categories": [
        {
            "id": 1, "name": "Vertical FFS Machines", "slug": "vertical-ffs",
            "description": "High-speed vertical form fill seal machines for granules, powders, and liquids.",
            "image": "",
            "machines": [
                {"id": 101, "name": "VFFS-200", "image": "",
                 "description": "200 packs/min vertical FFS for granules & snacks",
                 "specs": "Speed: 200 packs/min | Film width: 100-380mm | Power: 3.5kW"},
                {"id": 102, "name": "VFFS-350 Pro", "image": "",
                 "description": "Heavy duty model for powder & spice packaging",
                 "specs": "Speed: 350 packs/min | Film width: 150-450mm | Power: 5kW"},
                {"id": 103, "name": "VFFS-Liquid 100", "image": "",
                 "description": "Liquid and paste packaging with servo control",
                 "specs": "Speed: 100 packs/min | Volume: 50-1000ml | Power: 4kW"}
            ]
        },
        {
            "id": 2, "name": "Horizontal FFS Machines", "slug": "horizontal-ffs",
            "description": "Horizontal form fill seal for biscuits, candy bars, soap, and rigid products.",
            "image": "",
            "machines": [
                {"id": 201, "name": "HFFS-Flow 150", "image": "",
                 "description": "Flow wrap machine for bakery and confectionery products",
                 "specs": "Speed: 150 packs/min | Film width: 200-600mm | Power: 4kW"},
                {"id": 202, "name": "HFFS-Pillow 200", "image": "",
                 "description": "Pillow pack wrapper for rigid products",
                 "specs": "Speed: 200 packs/min | Film width: 250-700mm | Power: 5.5kW"}
            ]
        },
        {
            "id": 3, "name": "Rotary FFS Machines", "slug": "rotary-ffs",
            "description": "Rotary pouch form fill seal for stand-up pouches and gusseted bags.",
            "image": "",
            "machines": [
                {"id": 301, "name": "RFFS-Rotary 8", "image": "",
                 "description": "8-station rotary FFS for premade pouches",
                 "specs": "Speed: 60 pouches/min | Pouch size: 80-250mm | Power: 6kW"},
                {"id": 302, "name": "RFFS-Ziplock 12", "image": "",
                 "description": "12-station for ziplock stand-up pouches",
                 "specs": "Speed: 80 pouches/min | Pouch size: 100-300mm | Power: 7kW"}
            ]
        },
        {
            "id": 4, "name": "Multi-Lane FFS Machines", "slug": "multilane-ffs",
            "description": "Multi-lane sachet machines for ketchup, shampoo, and small portioned products.",
            "image": "",
            "machines": [
                {"id": 401, "name": "ML-Sachet 4L", "image": "",
                 "description": "4-lane sachet machine for liquid/semi-liquid products",
                 "specs": "Speed: 400 sachets/min | Volume: 5-50ml | Power: 3kW"},
                {"id": 402, "name": "ML-Sachet 8L", "image": "",
                 "description": "8-lane high output sachet line",
                 "specs": "Speed: 800 sachets/min | Volume: 5-100ml | Power: 5kW"}
            ]
        }
    ],
    "spare_categories": [
        {
            "id": 1, "name": "Sealing & Heating Parts", "slug": "sealing-heating",
            "description": "All sealing jaws, heating elements, and temperature-related components.",
            "image": "",
            "spares": [
                {"id": 1001, "name": "Horizontal Sealing Jaw (VFFS)", "part_no": "VSJ-H-001", "image": "",
                 "description": "Chrome-plated horizontal sealing jaw for VFFS-200 and VFFS-350",
                 "compatible": "VFFS-200, VFFS-350 Pro"},
                {"id": 1002, "name": "Vertical Sealing Jaw Set", "part_no": "VSJ-V-002", "image": "",
                 "description": "Pair of vertical sealing jaws with Teflon coating",
                 "compatible": "VFFS-200, VFFS-350 Pro, VFFS-Liquid 100"},
                {"id": 1003, "name": "Heating Element 230V/500W", "part_no": "HE-230-500", "image": "",
                 "description": "Cartridge heater for sealing jaw assembly",
                 "compatible": "All VFFS, HFFS models"},
                {"id": 1004, "name": "RTD Temperature Sensor PT100", "part_no": "RTD-PT100", "image": "",
                 "description": "Precision temperature sensor for jaw control",
                 "compatible": "Universal"},
                {"id": 1005, "name": "Flow Wrap Sealing Roller", "part_no": "FWR-001", "image": "",
                 "description": "Knurled sealing roller for HFFS flow wrap machines",
                 "compatible": "HFFS-Flow 150, HFFS-Pillow 200"}
            ]
        },
        {
            "id": 2, "name": "Drive & Motion Components", "slug": "drive-motion",
            "description": "Servo drives, motors, belts, chains, and transmission parts.",
            "image": "",
            "spares": [
                {"id": 2001, "name": "Servo Motor 400W", "part_no": "SM-400W", "image": "",
                 "description": "AC servo motor for film pulling mechanism",
                 "compatible": "VFFS-200, VFFS-350 Pro"},
                {"id": 2002, "name": "Timing Belt HTD 5M-750", "part_no": "TB-5M-750", "image": "",
                 "description": "Reinforced timing belt for main drive",
                 "compatible": "VFFS series"},
                {"id": 2003, "name": "Gear Box 1:20 Ratio", "part_no": "GB-1-20", "image": "",
                 "description": "Helical gear reduction box for cutter drive",
                 "compatible": "HFFS-Flow 150"},
                {"id": 2004, "name": "Linear Guide Rail 600mm", "part_no": "LGR-600", "image": "",
                 "description": "Precision linear guide with carriage block",
                 "compatible": "RFFS-Rotary 8, RFFS-Ziplock 12"},
                {"id": 2005, "name": "Chain Sprocket Set", "part_no": "CSS-001", "image": "",
                 "description": "Drive chain and sprocket for conveyor system",
                 "compatible": "HFFS-Pillow 200"}
            ]
        },
        {
            "id": 3, "name": "Film & Forming Parts", "slug": "film-forming",
            "description": "Forming tubes, film guides, rollers, and bag shaping components.",
            "image": "",
            "spares": [
                {"id": 3001, "name": "Forming Tube 60mm", "part_no": "FT-060", "image": "",
                 "description": "Stainless steel forming tube for small pouches",
                 "compatible": "VFFS-200"},
                {"id": 3002, "name": "Forming Tube 90mm", "part_no": "FT-090", "image": "",
                 "description": "Medium forming tube for standard packaging",
                 "compatible": "VFFS-200, VFFS-350 Pro"},
                {"id": 3003, "name": "Film Tension Roller Set", "part_no": "FTR-SET", "image": "",
                 "description": "Three-roller film tension assembly",
                 "compatible": "VFFS series, HFFS series"},
                {"id": 3004, "name": "Film Dancer Arm", "part_no": "FDA-001", "image": "",
                 "description": "Spring-loaded film dancer for consistent tension",
                 "compatible": "All FFS machines"},
                {"id": 3005, "name": "Gusset Former Plate", "part_no": "GFP-001", "image": "",
                 "description": "Side gusset forming plate for stand-up pouches",
                 "compatible": "RFFS series"}
            ]
        },
        {
            "id": 4, "name": "Pneumatic & Vacuum Parts", "slug": "pneumatic-vacuum",
            "description": "Air cylinders, valves, suction cups, vacuum pumps, and pneumatic fittings.",
            "image": "",
            "spares": [
                {"id": 4001, "name": "Air Cylinder 32x100mm", "part_no": "AC-32-100", "image": "",
                 "description": "Double-acting air cylinder for jaw actuation",
                 "compatible": "VFFS-350 Pro, RFFS series"},
                {"id": 4002, "name": "Solenoid Valve 5/2-way 1/4in", "part_no": "SV-52-014", "image": "",
                 "description": "Pneumatic solenoid valve for cylinder control",
                 "compatible": "Universal"},
                {"id": 4003, "name": "Vacuum Suction Cup 40mm", "part_no": "VSC-040", "image": "",
                 "description": "Silicone suction cup for pouch opening",
                 "compatible": "RFFS-Rotary 8, RFFS-Ziplock 12"},
                {"id": 4004, "name": "Pressure Regulator FRL Unit", "part_no": "FRL-001", "image": "",
                 "description": "Filter-Regulator-Lubricator assembly",
                 "compatible": "All pneumatic FFS machines"},
                {"id": 4005, "name": "Vacuum Pump 40L/min", "part_no": "VP-040", "image": "",
                 "description": "Oil-free vacuum pump for film handling",
                 "compatible": "RFFS series, ML-Sachet series"}
            ]
        },
        {
            "id": 5, "name": "Control & Electrical Parts", "slug": "control-electrical",
            "description": "PLCs, HMI screens, sensors, encoders, and electrical components.",
            "image": "",
            "spares": [
                {"id": 5001, "name": "7in HMI Touch Panel", "part_no": "HMI-7T", "image": "",
                 "description": "Color touch HMI for machine control interface",
                 "compatible": "VFFS-350 Pro, HFFS-Pillow 200"},
                {"id": 5002, "name": "PLC CPU Module", "part_no": "PLC-CPU-01", "image": "",
                 "description": "Main PLC controller with 32 I/O points",
                 "compatible": "VFFS series"},
                {"id": 5003, "name": "Proximity Sensor M12 NPN", "part_no": "PS-M12-NPN", "image": "",
                 "description": "Inductive proximity sensor for position detection",
                 "compatible": "Universal"},
                {"id": 5004, "name": "Rotary Encoder 600 PPR", "part_no": "RE-600", "image": "",
                 "description": "Shaft encoder for film length measurement",
                 "compatible": "All FFS machines"},
                {"id": 5005, "name": "SSR Solid State Relay 40A", "part_no": "SSR-40A", "image": "",
                 "description": "Solid state relay for heater power control",
                 "compatible": "Universal"}
            ]
        },
        {
            "id": 6, "name": "Cutting & Perforating Parts", "slug": "cutting-perforating",
            "description": "Rotary knives, cross-cut blades, perforation tools, and cutting cylinders.",
            "image": "",
            "spares": [
                {"id": 6001, "name": "Cross Cut Blade Set", "part_no": "CCB-SET", "image": "",
                 "description": "Hardened steel cross-cut blade pair",
                 "compatible": "VFFS-200, VFFS-350 Pro"},
                {"id": 6002, "name": "Rotary Knife Cylinder", "part_no": "RKC-001", "image": "",
                 "description": "Rotary knife assembly for continuous cutting",
                 "compatible": "HFFS-Flow 150"},
                {"id": 6003, "name": "Perforation Blade 150mm", "part_no": "PB-150", "image": "",
                 "description": "Circular perforation blade for tear-notch",
                 "compatible": "ML-Sachet series"},
                {"id": 6004, "name": "Anvil Roller (Hardened)", "part_no": "AR-H-001", "image": "",
                 "description": "Hardened anvil roller for rotary cutting",
                 "compatible": "HFFS series"}
            ]
        }
    ],
    "enquiries": []
}

def load_data() -> dict:
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w") as f:
            json.dump(DEFAULT_DATA, f, indent=2)
    with open(DATA_FILE) as f:
        return json.load(f)

def save_data(data: dict):
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, DATA_FILE)

# ── SECURITY HELPERS ───────────────────────────────────────────

def hash_password(pw: str) -> str:
    salt = os.environ.get("FFS_SALT", "shakthipack_change_this_salt_in_production")
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 260_000).hex()

# Rate limiting (in-memory, resets on restart)
_login_attempts: dict = {}
MAX_ATTEMPTS  = 10
LOCKOUT_SECS  = 300

def _check_rate_limit(ip: str) -> bool:
    now      = time.time()
    attempts = [t for t in _login_attempts.get(ip, []) if now - t < LOCKOUT_SECS]
    _login_attempts[ip] = attempts
    return len(attempts) < MAX_ATTEMPTS

def _record_attempt(ip: str):
    _login_attempts.setdefault(ip, []).append(time.time())

def _clear_attempts(ip: str):
    _login_attempts.pop(ip, None)

# CSRF
def _csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = uuid.uuid4().hex
    return session["csrf_token"]

def _validate_csrf():
    token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    if not token or token != session.get("csrf_token"):
        abort(403)

app.jinja_env.globals["csrf_token"] = _csrf_token


# Inject unread enquiry count into every template automatically
@app.context_processor
def inject_unread():
    if session.get("admin"):
        try:
            d = load_data()
            unread = sum(1 for e in d["enquiries"] if not e.get("read"))
        except Exception:
            unread = 0
        return {"unread_count": unread}
    return {"unread_count": 0}
# Input helpers
_SLUG_RE = re.compile(r"[^a-z0-9-]")

def slugify(text: str) -> str:
    return _SLUG_RE.sub("-", re.sub(r"\s+", "-", text.lower().strip()))

def sanitize(val, maxlen: int = 500) -> str:
    return (val or "").strip()[:maxlen]

# ── DECORATORS ────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login", next=request.path))
        return f(*args, **kwargs)
    return decorated

def csrf_protected(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.method == "POST":
            _validate_csrf()
        return f(*args, **kwargs)
    return decorated

# ── SECURITY HEADERS ──────────────────────────────────────────

@app.after_request
def set_security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"]        = "SAMEORIGIN"
    resp.headers["X-XSS-Protection"]       = "1; mode=block"
    resp.headers["Referrer-Policy"]        = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"]     = "geolocation=(), microphone=(), camera=()"
    if os.environ.get("HTTPS") == "1":
        resp.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return resp

# ── ERROR HANDLERS ────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return render_template("errors/404.html"), 404

@app.errorhandler(403)
def forbidden(e):
    return render_template("errors/403.html"), 403

@app.errorhandler(413)
def too_large(e):
    flash("File too large. Maximum upload size is 8 MB.", "error")
    return redirect(request.referrer or url_for("index"))

@app.errorhandler(500)
def server_error(e):
    logging.exception("Internal error")
    return render_template("errors/500.html"), 500

# ── PUBLIC ROUTES ─────────────────────────────────────────────

@app.route("/")
def index():
    data = load_data()
    return render_template("index.html",
                           machine_categories=data["machine_categories"],
                           spare_categories=data["spare_categories"],
                           settings=data.get("site_settings", {}))

@app.route("/machines")
def machines():
    data = load_data()
    return render_template("machines.html", categories=data["machine_categories"])

@app.route("/machines/<slug>")
def machine_category(slug):
    slug = slugify(slug)
    data = load_data()
    cat  = next((c for c in data["machine_categories"] if c["slug"] == slug), None)
    if not cat:
        abort(404)
    return render_template("machine_detail.html", category=cat)

@app.route("/spares")
def spares():
    data  = load_data()
    q     = sanitize(request.args.get("q", ""), 100).lower()
    categories = data["spare_categories"]
    if q:
        filtered = []
        for cat in categories:
            matched = [s for s in cat["spares"]
                       if q in s["name"].lower()
                       or q in s["part_no"].lower()
                       or q in s["description"].lower()
                       or q in s.get("compatible", "").lower()]
            if matched:
                filtered.append({**cat, "spares": matched})
        categories = filtered
    return render_template("spares.html", categories=categories, query=q)

@app.route("/spares/<slug>")
def spare_category(slug):
    slug = slugify(slug)
    data = load_data()
    cat  = next((c for c in data["spare_categories"] if c["slug"] == slug), None)
    if not cat:
        abort(404)
    return render_template("spare_detail.html", category=cat)

@app.route("/search")
def search():
    q    = sanitize(request.args.get("q", ""), 100).lower()
    data = load_data()
    machines_results, spares_results = [], []
    if q:
        for cat in data["machine_categories"]:
            for m in cat["machines"]:
                if (q in m["name"].lower()
                        or q in m["description"].lower()
                        or q in m.get("specs", "").lower()):
                    machines_results.append({**m, "category": cat["name"], "cat_slug": cat["slug"]})
        for cat in data["spare_categories"]:
            for s in cat["spares"]:
                if (q in s["name"].lower()
                        or q in s["part_no"].lower()
                        or q in s["description"].lower()
                        or q in s.get("compatible", "").lower()):
                    spares_results.append({**s, "category": cat["name"], "cat_slug": cat["slug"]})
    return render_template("search.html", query=q,
                           machines=machines_results, spares=spares_results)

@app.route("/enquiry", methods=["GET", "POST"])
@csrf_protected
def enquiry():
    data = load_data()
    if request.method == "POST":
        name    = sanitize(request.form.get("name", ""), 120)
        email   = sanitize(request.form.get("email", ""), 254)
        phone   = sanitize(request.form.get("phone", ""), 30)
        subject = sanitize(request.form.get("subject", ""), 120)
        message = sanitize(request.form.get("message", ""), 2000)

        errors = []
        if not name:
            errors.append("Name is required.")
        if not re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            errors.append("A valid email address is required.")
        if not subject:
            errors.append("Please select a subject.")
        if len(message) < 10:
            errors.append("Message must be at least 10 characters.")
        if errors:
            for err in errors:
                flash(err, "error")
            return render_template("enquiry.html", prefill=request.form,
                                   machine_categories=data["machine_categories"],
                                   spare_categories=data["spare_categories"])

        entry = {
            "id":        str(uuid.uuid4()),
            "name":      name,
            "email":     email,
            "phone":     phone,
            "subject":   subject,
            "message":   message,
            "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            "read":      False
        }
        data["enquiries"].append(entry)
        save_data(data)
        flash("Your enquiry has been submitted! We'll get back to you within 24 hours.", "success")
        return redirect(url_for("enquiry"))

    return render_template("enquiry.html", prefill={},
                           machine_categories=data["machine_categories"],
                           spare_categories=data["spare_categories"])

# ── ADMIN AUTH ────────────────────────────────────────────────

@app.route("/admin/login", methods=["GET", "POST"])
@csrf_protected
def admin_login():
    if session.get("admin"):
        return redirect(url_for("admin_dashboard"))
    ip = request.remote_addr
    if not _check_rate_limit(ip):
        flash("Too many failed attempts. Please wait 5 minutes.", "error")
        return render_template("admin/login.html")
    if request.method == "POST":
        data     = load_data()
        username = sanitize(request.form.get("username", ""), 80)
        password = request.form.get("password", "")
        stored   = data["admin"]["password"]
        pbkdf2_ok = hash_password(password) == stored
        sha256_ok = hashlib.sha256(password.encode()).hexdigest() == stored and not data["admin"].get("pbkdf2")
        if username == data["admin"]["username"] and (pbkdf2_ok or sha256_ok):
            if sha256_ok:
                data["admin"]["password"] = hash_password(password)
                data["admin"]["pbkdf2"]   = True
                save_data(data)
            _clear_attempts(ip)
            session.clear()
            session.permanent        = True
            session["admin"]         = True
            session["logged_in_at"]  = time.time()
            next_url = request.args.get("next", "")
            if next_url and next_url.startswith("/admin"):
                return redirect(next_url)
            return redirect(url_for("admin_dashboard"))
        _record_attempt(ip)
        remaining = MAX_ATTEMPTS - len(_login_attempts.get(ip, []))
        flash(f"Invalid credentials. {remaining} attempt(s) remaining before lockout.", "error")
    return render_template("admin/login.html")

@app.route("/admin/logout")
@login_required
def admin_logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("index"))

# ── ADMIN DASHBOARD ───────────────────────────────────────────

@app.route("/admin")
@app.route("/admin/")
@login_required
def admin_dashboard():
    data           = load_data()
    total_machines = sum(len(c["machines"]) for c in data["machine_categories"])
    total_spares   = sum(len(c["spares"])   for c in data["spare_categories"])
    unread         = sum(1 for e in data["enquiries"] if not e.get("read"))
    recent_enq     = sorted(data["enquiries"], key=lambda x: x["timestamp"], reverse=True)[:5]
    force_change   = data["admin"].get("force_change", False)
    if force_change:
        flash("Please change your default admin password in Settings.", "error")
    return render_template("admin/dashboard.html",
                           total_machines=total_machines,
                           total_spares=total_spares,
                           total_enquiries=len(data["enquiries"]),
                           unread_enquiries=unread,
                           recent_enquiries=recent_enq,
                           settings=data.get("site_settings", {}))

# ── ADMIN MACHINE CATEGORIES ──────────────────────────────────

@app.route("/admin/machines")
@login_required
def admin_machines():
    data = load_data()
    return render_template("admin/machines.html", categories=data["machine_categories"])

@app.route("/admin/machines/add", methods=["GET", "POST"])
@login_required
@csrf_protected
def admin_add_machine_category():
    if request.method == "POST":
        data = load_data()
        name = sanitize(request.form.get("name", ""), 120)
        if not name:
            flash("Category name is required.", "error")
            return render_template("admin/machine_category_form.html", cat=None)
        slug = slugify(name)
        if slug in [c["slug"] for c in data["machine_categories"]]:
            slug = slug + "-" + uuid.uuid4().hex[:4]
        new_cat = {
            "id":          max((c["id"] for c in data["machine_categories"]), default=0) + 1,
            "name":        name,
            "slug":        slug,
            "description": sanitize(request.form.get("description", ""), 1000),
            "image":       save_image("image"),
            "machines":    []
        }
        data["machine_categories"].append(new_cat)
        save_data(data)
        flash("Machine category added.", "success")
        return redirect(url_for("admin_machines"))
    return render_template("admin/machine_category_form.html", cat=None)

@app.route("/admin/machines/edit/<int:cat_id>", methods=["GET", "POST"])
@login_required
@csrf_protected
def admin_edit_machine_category(cat_id):
    data = load_data()
    cat  = next((c for c in data["machine_categories"] if c["id"] == cat_id), None)
    if not cat:
        abort(404)
    if request.method == "POST":
        name = sanitize(request.form.get("name", ""), 120)
        if not name:
            flash("Category name is required.", "error")
            return render_template("admin/machine_category_form.html", cat=cat)
        cat["name"]        = name
        cat["description"] = sanitize(request.form.get("description", ""), 1000)
        new_img = save_image("image")
        if new_img:
            delete_image(cat.get("image"))
            cat["image"] = new_img
        save_data(data)
        flash("Category updated.", "success")
        return redirect(url_for("admin_machines"))
    return render_template("admin/machine_category_form.html", cat=cat)

@app.route("/admin/machines/delete/<int:cat_id>", methods=["POST"])
@login_required
@csrf_protected
def admin_delete_machine_category(cat_id):
    data = load_data()
    cat  = next((c for c in data["machine_categories"] if c["id"] == cat_id), None)
    if cat:
        delete_image(cat.get("image"))
        for m in cat.get("machines", []):
            delete_image(m.get("image"))
        data["machine_categories"] = [c for c in data["machine_categories"] if c["id"] != cat_id]
        save_data(data)
        flash("Category and all its machines deleted.", "success")
    return redirect(url_for("admin_machines"))

# ── ADMIN MACHINES ────────────────────────────────────────────

@app.route("/admin/machines/<int:cat_id>/add-machine", methods=["GET", "POST"])
@login_required
@csrf_protected
def admin_add_machine(cat_id):
    data = load_data()
    cat  = next((c for c in data["machine_categories"] if c["id"] == cat_id), None)
    if not cat:
        abort(404)
    if request.method == "POST":
        name = sanitize(request.form.get("name", ""), 120)
        if not name:
            flash("Machine name is required.", "error")
            return render_template("admin/machine_form.html", cat=cat, machine=None)
        new_machine = {
            "id":          max((m["id"] for c in data["machine_categories"] for m in c["machines"]), default=0) + 1,
            "name":        name,
            "description": sanitize(request.form.get("description", ""), 1000),
            "specs":       sanitize(request.form.get("specs", ""), 500),
            "image":       save_image("image")
        }
        cat["machines"].append(new_machine)
        save_data(data)
        flash("Machine added.", "success")
        return redirect(url_for("admin_machines"))
    return render_template("admin/machine_form.html", cat=cat, machine=None)

@app.route("/admin/machines/<int:cat_id>/edit-machine/<int:machine_id>", methods=["GET", "POST"])
@login_required
@csrf_protected
def admin_edit_machine(cat_id, machine_id):
    data    = load_data()
    cat     = next((c for c in data["machine_categories"] if c["id"] == cat_id), None)
    if not cat:
        abort(404)
    machine = next((m for m in cat["machines"] if m["id"] == machine_id), None)
    if not machine:
        abort(404)
    if request.method == "POST":
        name = sanitize(request.form.get("name", ""), 120)
        if not name:
            flash("Machine name is required.", "error")
            return render_template("admin/machine_form.html", cat=cat, machine=machine)
        machine["name"]        = name
        machine["description"] = sanitize(request.form.get("description", ""), 1000)
        machine["specs"]       = sanitize(request.form.get("specs", ""), 500)
        new_img = save_image("image")
        if new_img:
            delete_image(machine.get("image"))
            machine["image"] = new_img
        save_data(data)
        flash("Machine updated.", "success")
        return redirect(url_for("admin_machines"))
    return render_template("admin/machine_form.html", cat=cat, machine=machine)

@app.route("/admin/machines/<int:cat_id>/delete-machine/<int:machine_id>", methods=["POST"])
@login_required
@csrf_protected
def admin_delete_machine(cat_id, machine_id):
    data = load_data()
    cat  = next((c for c in data["machine_categories"] if c["id"] == cat_id), None)
    if cat:
        machine = next((m for m in cat["machines"] if m["id"] == machine_id), None)
        if machine:
            delete_image(machine.get("image"))
        cat["machines"] = [m for m in cat["machines"] if m["id"] != machine_id]
        save_data(data)
        flash("Machine deleted.", "success")
    return redirect(url_for("admin_machines"))

# ── ADMIN SPARE CATEGORIES ────────────────────────────────────

@app.route("/admin/spares")
@login_required
def admin_spares():
    data = load_data()
    return render_template("admin/spares.html", categories=data["spare_categories"])

@app.route("/admin/spares/add", methods=["GET", "POST"])
@login_required
@csrf_protected
def admin_add_spare_category():
    if request.method == "POST":
        data = load_data()
        name = sanitize(request.form.get("name", ""), 120)
        if not name:
            flash("Category name is required.", "error")
            return render_template("admin/spare_category_form.html", cat=None)
        slug = slugify(name)
        if slug in [c["slug"] for c in data["spare_categories"]]:
            slug = slug + "-" + uuid.uuid4().hex[:4]
        new_cat = {
            "id":          max((c["id"] for c in data["spare_categories"]), default=0) + 1,
            "name":        name,
            "slug":        slug,
            "description": sanitize(request.form.get("description", ""), 1000),
            "image":       save_image("image"),
            "spares":      []
        }
        data["spare_categories"].append(new_cat)
        save_data(data)
        flash("Spare category added.", "success")
        return redirect(url_for("admin_spares"))
    return render_template("admin/spare_category_form.html", cat=None)

@app.route("/admin/spares/edit/<int:cat_id>", methods=["GET", "POST"])
@login_required
@csrf_protected
def admin_edit_spare_category(cat_id):
    data = load_data()
    cat  = next((c for c in data["spare_categories"] if c["id"] == cat_id), None)
    if not cat:
        abort(404)
    if request.method == "POST":
        name = sanitize(request.form.get("name", ""), 120)
        if not name:
            flash("Category name is required.", "error")
            return render_template("admin/spare_category_form.html", cat=cat)
        cat["name"]        = name
        cat["description"] = sanitize(request.form.get("description", ""), 1000)
        new_img = save_image("image")
        if new_img:
            delete_image(cat.get("image"))
            cat["image"] = new_img
        save_data(data)
        flash("Category updated.", "success")
        return redirect(url_for("admin_spares"))
    return render_template("admin/spare_category_form.html", cat=cat)

@app.route("/admin/spares/delete/<int:cat_id>", methods=["POST"])
@login_required
@csrf_protected
def admin_delete_spare_category(cat_id):
    data = load_data()
    cat  = next((c for c in data["spare_categories"] if c["id"] == cat_id), None)
    if cat:
        delete_image(cat.get("image"))
        for s in cat.get("spares", []):
            delete_image(s.get("image"))
        data["spare_categories"] = [c for c in data["spare_categories"] if c["id"] != cat_id]
        save_data(data)
        flash("Category and all its spares deleted.", "success")
    return redirect(url_for("admin_spares"))

# ── ADMIN SPARES ──────────────────────────────────────────────

@app.route("/admin/spares/<int:cat_id>/add-spare", methods=["GET", "POST"])
@login_required
@csrf_protected
def admin_add_spare(cat_id):
    data = load_data()
    cat  = next((c for c in data["spare_categories"] if c["id"] == cat_id), None)
    if not cat:
        abort(404)
    if request.method == "POST":
        name = sanitize(request.form.get("name", ""), 120)
        if not name:
            flash("Part name is required.", "error")
            return render_template("admin/spare_form.html", cat=cat, spare=None)
        new_spare = {
            "id":          max((s["id"] for c in data["spare_categories"] for s in c["spares"]), default=0) + 1,
            "name":        name,
            "part_no":     sanitize(request.form.get("part_no", ""), 60),
            "description": sanitize(request.form.get("description", ""), 1000),
            "compatible":  sanitize(request.form.get("compatible", ""), 300),
            "image":       save_image("image")
        }
        cat["spares"].append(new_spare)
        save_data(data)
        flash("Spare added.", "success")
        return redirect(url_for("admin_spares"))
    return render_template("admin/spare_form.html", cat=cat, spare=None)

@app.route("/admin/spares/<int:cat_id>/edit-spare/<int:spare_id>", methods=["GET", "POST"])
@login_required
@csrf_protected
def admin_edit_spare(cat_id, spare_id):
    data  = load_data()
    cat   = next((c for c in data["spare_categories"] if c["id"] == cat_id), None)
    if not cat:
        abort(404)
    spare = next((s for s in cat["spares"] if s["id"] == spare_id), None)
    if not spare:
        abort(404)
    if request.method == "POST":
        name = sanitize(request.form.get("name", ""), 120)
        if not name:
            flash("Part name is required.", "error")
            return render_template("admin/spare_form.html", cat=cat, spare=spare)
        spare["name"]        = name
        spare["part_no"]     = sanitize(request.form.get("part_no", ""), 60)
        spare["description"] = sanitize(request.form.get("description", ""), 1000)
        spare["compatible"]  = sanitize(request.form.get("compatible", ""), 300)
        new_img = save_image("image")
        if new_img:
            delete_image(spare.get("image"))
            spare["image"] = new_img
        save_data(data)
        flash("Spare updated.", "success")
        return redirect(url_for("admin_spares"))
    return render_template("admin/spare_form.html", cat=cat, spare=spare)

@app.route("/admin/spares/<int:cat_id>/delete-spare/<int:spare_id>", methods=["POST"])
@login_required
@csrf_protected
def admin_delete_spare(cat_id, spare_id):
    data = load_data()
    cat  = next((c for c in data["spare_categories"] if c["id"] == cat_id), None)
    if cat:
        spare = next((s for s in cat["spares"] if s["id"] == spare_id), None)
        if spare:
            delete_image(spare.get("image"))
        cat["spares"] = [s for s in cat["spares"] if s["id"] != spare_id]
        save_data(data)
        flash("Spare deleted.", "success")
    return redirect(url_for("admin_spares"))

# ── ADMIN ENQUIRIES ───────────────────────────────────────────

@app.route("/admin/enquiries")
@login_required
def admin_enquiries():
    data      = load_data()
    enquiries = sorted(data["enquiries"], key=lambda x: x["timestamp"], reverse=True)
    return render_template("admin/enquiries.html", enquiries=enquiries)

@app.route("/admin/enquiries/<enq_id>/read", methods=["POST"])
@login_required
@csrf_protected
def admin_mark_read(enq_id):
    data = load_data()
    for e in data["enquiries"]:
        if e["id"] == enq_id:
            e["read"] = True
            break
    save_data(data)
    return redirect(url_for("admin_enquiries"))

@app.route("/admin/enquiries/delete/<enq_id>", methods=["POST"])
@login_required
@csrf_protected
def admin_delete_enquiry(enq_id):
    data = load_data()
    data["enquiries"] = [e for e in data["enquiries"] if e["id"] != enq_id]
    save_data(data)
    flash("Enquiry deleted.", "success")
    return redirect(url_for("admin_enquiries"))

# ── ADMIN SETTINGS ────────────────────────────────────────────

@app.route("/admin/settings", methods=["GET", "POST"])
@login_required
@csrf_protected
def admin_settings():
    data = load_data()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "change_password":
            current  = request.form.get("current_password", "")
            new_pass = request.form.get("new_password", "")
            confirm  = request.form.get("confirm_password", "")
            stored   = data["admin"]["password"]
            valid    = (hash_password(current) == stored
                        or hashlib.sha256(current.encode()).hexdigest() == stored)
            if not valid:
                flash("Current password is incorrect.", "error")
            elif len(new_pass) < 8:
                flash("New password must be at least 8 characters.", "error")
            elif new_pass != confirm:
                flash("Passwords do not match.", "error")
            else:
                data["admin"]["password"]     = hash_password(new_pass)
                data["admin"]["pbkdf2"]       = True
                data["admin"]["force_change"] = False
                save_data(data)
                flash("Password updated successfully.", "success")

        elif action == "site_settings":
            data["site_settings"] = {
                "company_name": sanitize(request.form.get("company_name", ""), 120),
                "phone":        sanitize(request.form.get("phone", ""), 30),
                "email":        sanitize(request.form.get("email", ""), 254),
                "address":      sanitize(request.form.get("address", ""), 300),
            }
            save_data(data)
            flash("Site settings updated.", "success")

    return render_template("admin/settings.html",
                           settings=data.get("site_settings", {}),
                           force_change=data["admin"].get("force_change", False))

# ── ENTRY POINT ───────────────────────────────────────────────

if __name__ == "__main__":
    port  = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    if debug:
        logging.warning("Running in DEBUG mode — do not use in production.")
    app.run(host="0.0.0.0", port=port, debug=debug)
