"""
ShakthiPack Machineries — Flask Web Application
Production-ready build.

Upgrade changelog:
  1. Pagination on admin enquiries list
  2. Last-login timestamp for admin
  3. Admin activity log
  4. Better search indexing (pre-built inverted index)
  5. Replace data.json with PostgreSQL (falls back to JSON when DATABASE_URL absent)
  6. Preview images before upload (JS FileReader in forms)
  7. Multi-admin support with roles (superadmin / editor)
"""

from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify, abort, make_response, g)
import json, os, hashlib, uuid, re, time, logging, io, csv, hmac as _hmac
from collections import defaultdict
from functools import wraps
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename

# ── ENV ────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ── LOGGING ───────────────────────────────────────────────────
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s req=%(request_id)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("shakthipack")

class RequestIdFilter(logging.Filter):
    def filter(self, record):
        try:
            record.request_id = g.get("request_id", "-")
        except RuntimeError:
            record.request_id = "-"
        return True

logger.addFilter(RequestIdFilter())

# ── APP ────────────────────────────────────────────────────────
app = Flask(__name__)
app.config["PROPAGATE_EXCEPTIONS"] = True

SECRET = os.environ.get("FFS_SECRET", "")
if not SECRET:
    import secrets as _s
    SECRET = _s.token_hex(32)
    logger.warning("FFS_SECRET not set — using random key (sessions won't survive restarts).",
                   extra={"request_id": "-"})
app.secret_key = SECRET

app.config.update(
    SESSION_COOKIE_HTTPONLY   = True,
    SESSION_COOKIE_SAMESITE   = "Lax",
    SESSION_COOKIE_SECURE     = os.environ.get("HTTPS", "0") == "1",
    PERMANENT_SESSION_LIFETIME= timedelta(hours=8),
    MAX_CONTENT_LENGTH        = 8 * 1024 * 1024,
)

# ── REQUEST LIFECYCLE ─────────────────────────────────────────

@app.before_request
def _before():
    raw_rid = request.headers.get("X-Request-ID", "")
    rid = re.sub(r"[^a-zA-Z0-9_-]", "", raw_rid)[:32] or uuid.uuid4().hex[:12]
    g.request_id = rid

@app.after_request
def _after(resp):
    rid = _rid()
    resp.headers["X-Request-ID"] = rid
    resp.headers["Server"] = "ShakthiPack"
    return resp

def _rid() -> str:
    try:
        return g.get("request_id", "-")
    except RuntimeError:
        return "-"

# ── UPLOADS ────────────────────────────────────────────────────
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXT   = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_IMG_SIZE  = 4 * 1024 * 1024
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def _allowed(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT

def _verify_image(stream) -> bool:
    try:
        from PIL import Image
        stream.seek(0)
        img = Image.open(stream)
        img.verify()
        stream.seek(0)
        return True
    except Exception:
        return False

def save_image(field: str) -> str:
    f = request.files.get(field)
    if not (f and f.filename and _allowed(f.filename)):
        return ""
    f.stream.seek(0, 2)
    size = f.stream.tell()
    f.stream.seek(0)
    if size > MAX_IMG_SIZE:
        flash("Image too large (max 4 MB per image).", "error")
        return ""
    if not _verify_image(f.stream):
        flash("Invalid image file.", "error")
        return ""
    try:
        from PIL import Image as PILImage
        f.stream.seek(0)
        img = PILImage.open(f.stream)
        img = img.convert("RGBA" if img.mode in ("RGBA","P") else "RGB")
        max_dim = 1200
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim), PILImage.LANCZOS)
        filename = f"{uuid.uuid4().hex}.webp"
        out_path = os.path.join(UPLOAD_FOLDER, filename)
        img.save(out_path, "WEBP", quality=82, method=4)
        return filename
    except Exception as e:
        logger.warning("Image optimization failed, saving original: %s", e,
                       extra={"request_id": _rid()})
        f.stream.seek(0)
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
        except OSError as e:
            logger.warning("Could not delete image %s: %s", filename, e, extra={"request_id": _rid()})

# ── DATABASE LAYER ─────────────────────────────────────────────
# Supports PostgreSQL when DATABASE_URL is set; falls back to data.json.

DATABASE_URL = os.environ.get("DATABASE_URL", "")
_pg_conn = None

def _get_pg():
    global _pg_conn
    try:
        import psycopg2
        import psycopg2.extras
        if _pg_conn is None or _pg_conn.closed:
            _pg_conn = psycopg2.connect(DATABASE_URL)
            _pg_conn.autocommit = True
            _init_pg_schema(_pg_conn)
        return _pg_conn
    except Exception as e:
        logger.error("PostgreSQL connection failed: %s", e, extra={"request_id": _rid()})
        return None

def _init_pg_schema(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS shakthipack_data (
                key TEXT PRIMARY KEY,
                value JSONB NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT now()
            );
            CREATE TABLE IF NOT EXISTS activity_log (
                id SERIAL PRIMARY KEY,
                admin_username TEXT NOT NULL,
                action TEXT NOT NULL,
                detail TEXT,
                ip TEXT,
                ts TIMESTAMPTZ DEFAULT now()
            );
        """)

def _pg_load(key: str):
    conn = _get_pg()
    if not conn:
        return None
    try:
        import psycopg2.extras
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT value FROM shakthipack_data WHERE key=%s", (key,))
            row = cur.fetchone()
            return dict(row["value"]) if row else None
    except Exception as e:
        logger.error("PG load error: %s", e, extra={"request_id": _rid()})
        return None

def _pg_save(key: str, value: dict):
    conn = _get_pg()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO shakthipack_data(key, value, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()
            """, (key, json.dumps(value)))
        return True
    except Exception as e:
        logger.error("PG save error: %s", e, extra={"request_id": _rid()})
        return False

def _pg_log_activity(username: str, action: str, detail: str = "", ip: str = ""):
    conn = _get_pg()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO activity_log(admin_username,action,detail,ip) VALUES(%s,%s,%s,%s)",
                (username, action, detail, ip)
            )
    except Exception as e:
        logger.warning("PG activity log error: %s", e, extra={"request_id": _rid()})

# ── JSON-based data helpers ────────────────────────────────────

DATA_FILE   = os.path.join(BASE_DIR, "data.json")
_cache      = {"data": None, "mtime": 0.0}

DEFAULT_DATA = {
    "admin": {
        "username":     "admin",
        "password":     hashlib.sha256("admin123".encode()).hexdigest(),
        "pbkdf2":       False,
        "force_change": True,
        "last_login":   None,
        "role":         "superadmin",
    },
    "admins": [],
    "site_settings": {
        "company_name": "Shakthi Pack Machineries",
        "phone":        "+91 00000 00000",
        "email":        "info@shakthipack.com",
        "address":      "Chennai, Tamil Nadu, India"
    },
    "activity_log": [],
    "machine_categories": [
        {"id":1,"name":"Vertical FFS Machines","slug":"vertical-ffs","description":"High-speed vertical form fill seal machines for granules, powders, and liquids.","image":"","machines":[
            {"id":101,"name":"VFFS-200","image":"","description":"200 packs/min vertical FFS for granules & snacks","specs":"Speed: 200 packs/min | Film width: 100-380mm | Power: 3.5kW"},
            {"id":102,"name":"VFFS-350 Pro","image":"","description":"Heavy duty model for powder & spice packaging","specs":"Speed: 350 packs/min | Film width: 150-450mm | Power: 5kW"},
            {"id":103,"name":"VFFS-Liquid 100","image":"","description":"Liquid and paste packaging with servo control","specs":"Speed: 100 packs/min | Volume: 50-1000ml | Power: 4kW"}]},
        {"id":2,"name":"Horizontal FFS Machines","slug":"horizontal-ffs","description":"Horizontal form fill seal for biscuits, candy bars, soap, and rigid products.","image":"","machines":[
            {"id":201,"name":"HFFS-Flow 150","image":"","description":"Flow wrap machine for bakery and confectionery products","specs":"Speed: 150 packs/min | Film width: 200-600mm | Power: 4kW"},
            {"id":202,"name":"HFFS-Pillow 200","image":"","description":"Pillow pack wrapper for rigid products","specs":"Speed: 200 packs/min | Film width: 250-700mm | Power: 5.5kW"}]},
        {"id":3,"name":"Rotary FFS Machines","slug":"rotary-ffs","description":"Rotary pouch form fill seal for stand-up pouches and gusseted bags.","image":"","machines":[
            {"id":301,"name":"RFFS-Rotary 8","image":"","description":"8-station rotary FFS for premade pouches","specs":"Speed: 60 pouches/min | Pouch size: 80-250mm | Power: 6kW"},
            {"id":302,"name":"RFFS-Ziplock 12","image":"","description":"12-station for ziplock stand-up pouches","specs":"Speed: 80 pouches/min | Pouch size: 100-300mm | Power: 7kW"}]},
        {"id":4,"name":"Multi-Lane FFS Machines","slug":"multilane-ffs","description":"Multi-lane sachet machines for ketchup, shampoo, and small portioned products.","image":"","machines":[
            {"id":401,"name":"ML-Sachet 4L","image":"","description":"4-lane sachet machine for liquid/semi-liquid products","specs":"Speed: 400 sachets/min | Volume: 5-50ml | Power: 3kW"},
            {"id":402,"name":"ML-Sachet 8L","image":"","description":"8-lane high output sachet line","specs":"Speed: 800 sachets/min | Volume: 5-100ml | Power: 5kW"}]}
    ],
    "spare_categories": [
        {"id":1,"name":"Sealing & Heating Parts","slug":"sealing-heating","description":"All sealing jaws, heating elements, and temperature-related components.","image":"","spares":[
            {"id":1001,"name":"Horizontal Sealing Jaw (VFFS)","part_no":"VSJ-H-001","image":"","description":"Chrome-plated horizontal sealing jaw for VFFS-200 and VFFS-350","compatible":"VFFS-200, VFFS-350 Pro"},
            {"id":1002,"name":"Vertical Sealing Jaw Set","part_no":"VSJ-V-002","image":"","description":"Pair of vertical sealing jaws with Teflon coating","compatible":"VFFS-200, VFFS-350 Pro, VFFS-Liquid 100"},
            {"id":1003,"name":"Heating Element 230V/500W","part_no":"HE-230-500","image":"","description":"Cartridge heater for sealing jaw assembly","compatible":"All VFFS, HFFS models"},
            {"id":1004,"name":"RTD Temperature Sensor PT100","part_no":"RTD-PT100","image":"","description":"Precision temperature sensor for jaw control","compatible":"Universal"},
            {"id":1005,"name":"Flow Wrap Sealing Roller","part_no":"FWR-001","image":"","description":"Knurled sealing roller for HFFS flow wrap machines","compatible":"HFFS-Flow 150, HFFS-Pillow 200"}]},
        {"id":2,"name":"Drive & Motion Components","slug":"drive-motion","description":"Servo drives, motors, belts, chains, and transmission parts.","image":"","spares":[
            {"id":2001,"name":"Servo Motor 400W","part_no":"SM-400W","image":"","description":"AC servo motor for film pulling mechanism","compatible":"VFFS-200, VFFS-350 Pro"},
            {"id":2002,"name":"Timing Belt HTD 5M-750","part_no":"TB-5M-750","image":"","description":"Reinforced timing belt for main drive","compatible":"VFFS series"},
            {"id":2003,"name":"Gear Box 1:20 Ratio","part_no":"GB-1-20","image":"","description":"Helical gear reduction box for cutter drive","compatible":"HFFS-Flow 150"},
            {"id":2004,"name":"Linear Guide Rail 600mm","part_no":"LGR-600","image":"","description":"Precision linear guide with carriage block","compatible":"RFFS-Rotary 8, RFFS-Ziplock 12"},
            {"id":2005,"name":"Chain Sprocket Set","part_no":"CSS-001","image":"","description":"Drive chain and sprocket for conveyor system","compatible":"HFFS-Pillow 200"}]},
        {"id":3,"name":"Film & Forming Parts","slug":"film-forming","description":"Forming tubes, film guides, rollers, and bag shaping components.","image":"","spares":[
            {"id":3001,"name":"Forming Tube 60mm","part_no":"FT-060","image":"","description":"Stainless steel forming tube for small pouches","compatible":"VFFS-200"},
            {"id":3002,"name":"Forming Tube 90mm","part_no":"FT-090","image":"","description":"Medium forming tube for standard packaging","compatible":"VFFS-200, VFFS-350 Pro"},
            {"id":3003,"name":"Film Tension Roller Set","part_no":"FTR-SET","image":"","description":"Three-roller film tension assembly","compatible":"VFFS series, HFFS series"},
            {"id":3004,"name":"Film Dancer Arm","part_no":"FDA-001","image":"","description":"Spring-loaded film dancer for consistent tension","compatible":"All FFS machines"},
            {"id":3005,"name":"Gusset Former Plate","part_no":"GFP-001","image":"","description":"Side gusset forming plate for stand-up pouches","compatible":"RFFS series"}]},
        {"id":4,"name":"Pneumatic & Vacuum Parts","slug":"pneumatic-vacuum","description":"Air cylinders, valves, suction cups, vacuum pumps, and pneumatic fittings.","image":"","spares":[
            {"id":4001,"name":"Air Cylinder 32x100mm","part_no":"AC-32-100","image":"","description":"Double-acting air cylinder for jaw actuation","compatible":"VFFS-350 Pro, RFFS series"},
            {"id":4002,"name":"Solenoid Valve 5/2-way 1/4in","part_no":"SV-52-014","image":"","description":"Pneumatic solenoid valve for cylinder control","compatible":"Universal"},
            {"id":4003,"name":"Vacuum Suction Cup 40mm","part_no":"VSC-040","image":"","description":"Silicone suction cup for pouch opening","compatible":"RFFS-Rotary 8, RFFS-Ziplock 12"},
            {"id":4004,"name":"Pressure Regulator FRL Unit","part_no":"FRL-001","image":"","description":"Filter-Regulator-Lubricator assembly","compatible":"All pneumatic FFS machines"},
            {"id":4005,"name":"Vacuum Pump 40L/min","part_no":"VP-040","image":"","description":"Oil-free vacuum pump for film handling","compatible":"RFFS series, ML-Sachet series"}]},
        {"id":5,"name":"Control & Electrical Parts","slug":"control-electrical","description":"PLCs, HMI screens, sensors, encoders, and electrical components.","image":"","spares":[
            {"id":5001,"name":"7in HMI Touch Panel","part_no":"HMI-7T","image":"","description":"Color touch HMI for machine control interface","compatible":"VFFS-350 Pro, HFFS-Pillow 200"},
            {"id":5002,"name":"PLC CPU Module","part_no":"PLC-CPU-01","image":"","description":"Main PLC controller with 32 I/O points","compatible":"VFFS series"},
            {"id":5003,"name":"Proximity Sensor M12 NPN","part_no":"PS-M12-NPN","image":"","description":"Inductive proximity sensor for position detection","compatible":"Universal"},
            {"id":5004,"name":"Rotary Encoder 600 PPR","part_no":"RE-600","image":"","description":"Shaft encoder for film length measurement","compatible":"All FFS machines"},
            {"id":5005,"name":"SSR Solid State Relay 40A","part_no":"SSR-40A","image":"","description":"Solid state relay for heater power control","compatible":"Universal"}]},
        {"id":6,"name":"Cutting & Perforating Parts","slug":"cutting-perforating","description":"Rotary knives, cross-cut blades, perforation tools, and cutting cylinders.","image":"","spares":[
            {"id":6001,"name":"Cross Cut Blade Set","part_no":"CCB-SET","image":"","description":"Hardened steel cross-cut blade pair","compatible":"VFFS-200, VFFS-350 Pro"},
            {"id":6002,"name":"Rotary Knife Cylinder","part_no":"RKC-001","image":"","description":"Rotary knife assembly for continuous cutting","compatible":"HFFS-Flow 150"},
            {"id":6003,"name":"Perforation Blade 150mm","part_no":"PB-150","image":"","description":"Circular perforation blade for tear-notch","compatible":"ML-Sachet series"},
            {"id":6004,"name":"Anvil Roller (Hardened)","part_no":"AR-H-001","image":"","description":"Hardened anvil roller for rotary cutting","compatible":"HFFS series"}]}
    ],
    "enquiries": []
}


def load_data() -> dict:
    if DATABASE_URL:
        data = _pg_load("main")
        if data is None:
            _pg_save("main", DEFAULT_DATA)
            return DEFAULT_DATA.copy()
        # Migrate new keys
        for key, val in [("admins",[]),("activity_log",[])]:
            if key not in data:
                data[key] = val
        adm = data.setdefault("admin", {})
        if "last_login" not in adm: adm["last_login"] = None
        if "role" not in adm: adm["role"] = "superadmin"
        return data

    try:
        mtime = os.path.getmtime(DATA_FILE)
    except OSError:
        mtime = 0.0
    if _cache["data"] is not None and mtime == _cache["mtime"]:
        return _cache["data"]
    if not os.path.exists(DATA_FILE):
        _write_defaults()
    try:
        with open(DATA_FILE) as f:
            data = json.load(f)
        for key, val in [("admins",[]),("activity_log",[])]:
            if key not in data:
                data[key] = val
        adm = data.setdefault("admin", {})
        if "last_login" not in adm: adm["last_login"] = None
        if "role" not in adm: adm["role"] = "superadmin"
        _cache["data"] = data
        _cache["mtime"] = mtime
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load data.json: %s — using defaults", e, extra={"request_id": _rid()})
        return DEFAULT_DATA.copy()

def _write_defaults():
    with open(DATA_FILE, "w") as f:
        json.dump(DEFAULT_DATA, f, indent=2)

def save_data(data: dict):
    if DATABASE_URL:
        if not _pg_save("main", data):
            raise RuntimeError("Failed to save data to PostgreSQL")
        return
    tmp = DATA_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, DATA_FILE)
        _cache["data"]  = data
        _cache["mtime"] = os.path.getmtime(DATA_FILE)
    except OSError as e:
        logger.error("Failed to save data.json: %s", e, extra={"request_id": _rid()})
        raise

# ── ACTIVITY LOG ───────────────────────────────────────────────

def log_activity(action: str, detail: str = ""):
    username = session.get("admin_username", "admin")
    ip = _client_ip()
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    if DATABASE_URL:
        _pg_log_activity(username, action, detail, ip)
        return
    try:
        data = load_data()
        entry = {"ts": ts, "username": username, "action": action, "detail": detail, "ip": ip}
        data.setdefault("activity_log", []).insert(0, entry)
        data["activity_log"] = data["activity_log"][:500]
        save_data(data)
    except Exception as e:
        logger.warning("Activity log write failed: %s", e, extra={"request_id": _rid()})

# ── SEARCH INDEX ───────────────────────────────────────────────

_search_index = {"machines": [], "spares": [], "_built_at": 0.0}

def _build_search_index(data: dict) -> dict:
    machines = []
    for cat in data.get("machine_categories", []):
        for m in cat.get("machines", []):
            tokens = {}
            for field, weight in [(m["name"],4),(m.get("description",""),2),(m.get("specs",""),1),(cat["name"],1)]:
                for tok in re.findall(r"[a-z0-9]+", field.lower()):
                    if len(tok) >= 2:
                        tokens[tok] = max(tokens.get(tok, 0), weight)
            machines.append({**m, "category": cat["name"], "cat_slug": cat["slug"], "_tokens": tokens})
    spares = []
    for cat in data.get("spare_categories", []):
        for s in cat.get("spares", []):
            tokens = {}
            for field, weight in [(s["name"],4),(s["part_no"],5),(s.get("description",""),2),(s.get("compatible",""),2),(cat["name"],1)]:
                for tok in re.findall(r"[a-z0-9]+", field.lower()):
                    if len(tok) >= 2:
                        tokens[tok] = max(tokens.get(tok, 0), weight)
            spares.append({**s, "category": cat["name"], "cat_slug": cat["slug"], "_tokens": tokens})
    return {"machines": machines, "spares": spares, "_built_at": time.time()}

def _get_index(data: dict) -> dict:
    global _search_index
    try:
        mtime = os.path.getmtime(DATA_FILE)
    except OSError:
        mtime = 0.0
    stale = (not _search_index["machines"] and not _search_index["spares"])
    if not DATABASE_URL:
        stale = stale or (_search_index.get("_built_at", 0) < mtime)
    else:
        stale = stale or (time.time() - _search_index.get("_built_at", 0) > 60)
    if stale:
        _search_index = _build_search_index(data)
    return _search_index

def _score_item(item: dict, query_tokens: list) -> int:
    tok_map = item["_tokens"]
    score = 0
    for qt in query_tokens:
        for tok, w in tok_map.items():
            if qt == tok:          score += w * 10
            elif tok.startswith(qt): score += w * 5
            elif qt in tok:        score += w * 2
    return score

def _do_search(q: str, data: dict):
    idx = _get_index(data)
    query_tokens = [t for t in re.findall(r"[a-z0-9]+", q.lower()) if len(t) >= 2]
    if not query_tokens:
        return [], []
    machines_results, spares_results = [], []
    for m in idx["machines"]:
        sc = _score_item(m, query_tokens)
        if sc > 0:
            machines_results.append({**{k:v for k,v in m.items() if k!="_tokens"}, "_score": sc})
    for s in idx["spares"]:
        sc = _score_item(s, query_tokens)
        if sc > 0:
            spares_results.append({**{k:v for k,v in s.items() if k!="_tokens"}, "_score": sc})
    machines_results.sort(key=lambda x: -x["_score"])
    spares_results.sort(key=lambda x: -x["_score"])
    return machines_results, spares_results

# ── SECURITY HELPERS ───────────────────────────────────────────

def hash_password(pw: str) -> str:
    salt = os.environ.get("FFS_SALT", "shakthipack_change_this_salt_in_production")
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 260_000).hex()

_TRUSTED_PROXIES = set(filter(None, os.environ.get("TRUSTED_PROXIES", "").split(",")))

def _client_ip() -> str:
    peer = request.remote_addr or ""
    if peer in _TRUSTED_PROXIES or os.environ.get("TRUST_PROXY", "0") == "1":
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return peer

_login_attempts: dict = {}
MAX_LOGIN_ATTEMPTS = 10
LOCKOUT_SECS       = 300
_enquiry_attempts: dict = {}
MAX_ENQUIRY        = 5
ENQUIRY_WINDOW     = 600

def _check_rate_limit(store, ip, max_hits, window):
    now  = time.time()
    hits = [t for t in store.get(ip, []) if now - t < window]
    store[ip] = hits
    return len(hits) < max_hits

def _record_hit(store, ip):
    store.setdefault(ip, []).append(time.time())

def _clear_hits(store, ip):
    store.pop(ip, None)

def _csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = uuid.uuid4().hex
    return session["csrf_token"]

def _validate_csrf():
    token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    stored = session.get("csrf_token", "")
    if not token or not stored or not _hmac.compare_digest(str(token), str(stored)):
        abort(403)

app.jinja_env.globals["csrf_token"]   = _csrf_token
app.jinja_env.globals["current_year"] = lambda: datetime.utcnow().year

def parse_specs(specs_str: str) -> list:
    result = []
    for item in (specs_str or "").split("|"):
        item = item.strip()
        if not item: continue
        idx = item.find(":")
        if idx != -1:
            result.append({"key": item[:idx].strip(), "val": item[idx+1:].strip()})
        else:
            result.append({"key": item, "val": ""})
    return result

app.jinja_env.globals["parse_specs"] = parse_specs

def _get_unread(data: dict) -> int:
    return sum(1 for e in data.get("enquiries", []) if not e.get("read"))

@app.context_processor
def inject_globals():
    unread = 0
    if session.get("admin"):
        unread = session.get("_unread_cache", 0)
    data = load_data()
    site = dict(data.get("site_settings", {}))
    site["_machine_cats"] = [{"id":c["id"],"name":c["name"],"slug":c["slug"]} for c in data.get("machine_categories",[])]
    site["_spare_cats"]   = [{"id":c["id"],"name":c["name"],"slug":c["slug"]} for c in data.get("spare_categories",[])]
    current_admin = {
        "username": session.get("admin_username", "admin"),
        "role":     session.get("admin_role", "superadmin"),
    }
    return {"unread_count": unread, "settings": site, "current_admin": current_admin}

def _refresh_unread_cache(data: dict):
    if session.get("admin"):
        session["_unread_cache"] = _get_unread(data)

_SLUG_RE = re.compile(r"[^a-z0-9-]+")

def slugify(text: str) -> str:
    return _SLUG_RE.sub("-", re.sub(r"\s+", "-", text.lower().strip())).strip("-")

def sanitize(val, maxlen: int = 500) -> str:
    return (val or "").strip()[:maxlen]

# ── MULTI-ADMIN HELPERS ───────────────────────────────────────

def _find_admin(data: dict, username: str):
    primary = data.get("admin", {})
    if primary.get("username") == username:
        return primary
    for a in data.get("admins", []):
        if a.get("username") == username:
            return a
    return None

def _all_admins(data: dict) -> list:
    result = [{**data.get("admin",{}), "_is_primary": True}]
    for a in data.get("admins", []):
        result.append({**a, "_is_primary": False})
    return result

def _verify_admin_password(admin: dict, password: str) -> bool:
    stored    = admin.get("password", "")
    pbkdf2_ok = _hmac.compare_digest(hash_password(password), stored)
    sha256_ok = (_hmac.compare_digest(hashlib.sha256(password.encode()).hexdigest(), stored)
                 and not admin.get("pbkdf2"))
    return pbkdf2_ok or sha256_ok

# ── DECORATORS ────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login", next=request.path))
        return f(*args, **kwargs)
    return decorated

def superadmin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login", next=request.path))
        if session.get("admin_role") != "superadmin":
            flash("This action requires superadmin privileges.", "error")
            return redirect(url_for("admin_dashboard"))
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
    resp.headers["X-Content-Type-Options"]  = "nosniff"
    resp.headers["X-Frame-Options"]         = "SAMEORIGIN"
    resp.headers["X-XSS-Protection"]        = "1; mode=block"
    resp.headers["Referrer-Policy"]         = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"]      = "geolocation=(), microphone=(), camera=()"
    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none';"
    )
    resp.headers["Content-Security-Policy"] = csp
    if os.environ.get("HTTPS") == "1":
        resp.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    return resp

# ── ERROR HANDLERS ────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e): return render_template("errors/404.html"), 404

@app.errorhandler(403)
def forbidden(e): return render_template("errors/403.html"), 403

@app.errorhandler(413)
def too_large(e):
    flash("File too large. Maximum upload size is 8 MB.", "error")
    return redirect(request.referrer or url_for("index"))

@app.errorhandler(500)
def server_error(e):
    logger.exception("Internal error", extra={"request_id": _rid()})
    return render_template("errors/500.html"), 500

# ── HEALTH / ROBOTS / SITEMAP ─────────────────────────────────

@app.route("/health")
def health():
    try:
        load_data()
        return jsonify({"status":"ok","timestamp":datetime.utcnow().isoformat(),"db":"postgresql" if DATABASE_URL else "json"}), 200
    except Exception as e:
        return jsonify({"status":"error"}), 500

@app.route("/robots.txt")
def robots():
    lines = ["User-agent: *","Allow: /","Disallow: /admin/","Disallow: /static/uploads/","",f"Sitemap: {request.url_root}sitemap.xml"]
    resp = make_response("\n".join(lines))
    resp.content_type = "text/plain"
    return resp

@app.route("/sitemap.xml")
def sitemap():
    data  = load_data()
    base  = request.url_root.rstrip("/")
    urls  = [{"loc":base+"/","priority":"1.0"},{"loc":base+"/machines","priority":"0.9"},{"loc":base+"/spares","priority":"0.9"},{"loc":base+"/enquiry","priority":"0.8"},{"loc":base+"/contact","priority":"0.7"}]
    from xml.sax.saxutils import escape as _xmlesc
    for cat in data["machine_categories"]:
        urls.append({"loc":f"{base}/machines/{_xmlesc(cat['slug'])}","priority":"0.7"})
    for cat in data["spare_categories"]:
        urls.append({"loc":f"{base}/spares/{_xmlesc(cat['slug'])}","priority":"0.6"})
    today = datetime.utcnow().strftime("%Y-%m-%d")
    xml = ['<?xml version="1.0" encoding="UTF-8"?>','<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        xml.append(f"  <url><loc>{_xmlesc(u['loc'])}</loc><lastmod>{today}</lastmod><priority>{u['priority']}</priority></url>")
    xml.append("</urlset>")
    resp = make_response("\n".join(xml))
    resp.content_type = "application/xml"
    return resp

# ── PUBLIC ROUTES ─────────────────────────────────────────────

@app.route("/")
def index():
    data = load_data()
    return render_template("index.html", machine_categories=data["machine_categories"], spare_categories=data["spare_categories"])

@app.route("/machines")
def machines():
    data = load_data()
    cat_filter = sanitize(request.args.get("cat",""), 80)
    valid_slugs = {c["slug"] for c in data["machine_categories"]}
    if cat_filter not in valid_slugs: cat_filter = ""
    return render_template("machines.html", categories=data["machine_categories"], cat_filter=cat_filter)

@app.route("/machines/<slug>")
def machine_category(slug):
    slug = slugify(slug)
    data = load_data()
    cat  = next((c for c in data["machine_categories"] if c["slug"]==slug), None)
    if not cat: abort(404)
    return render_template("machine_detail.html", category=cat)

@app.route("/spares")
def spares():
    data = load_data()
    q          = sanitize(request.args.get("q",""),100).lower()
    cat_filter = sanitize(request.args.get("cat",""),80)
    sort       = request.args.get("sort","")
    if sort not in {"az","za","pn",""}: sort=""
    try: page = max(1,int(request.args.get("page",1) or 1))
    except: page = 1
    PER = 12
    all_cats = data["spare_categories"]
    valid = {c["slug"] for c in all_cats}
    if cat_filter and cat_filter not in valid: cat_filter=""
    all_spares=[]
    for cat in all_cats:
        if cat_filter and cat["slug"]!=cat_filter: continue
        for s in cat["spares"]:
            if not q or any(q in s.get(f,"").lower() for f in ["name","part_no","description","compatible"]):
                all_spares.append({**s,"cat_name":cat["name"],"cat_slug":cat["slug"]})
    if sort=="az": all_spares.sort(key=lambda x:x["name"].lower())
    elif sort=="za": all_spares.sort(key=lambda x:x["name"].lower(),reverse=True)
    elif sort=="pn": all_spares.sort(key=lambda x:x["part_no"].lower())
    total=len(all_spares)
    tp=max(1,(total+PER-1)//PER); page=min(page,tp)
    page_spares=all_spares[(page-1)*PER:page*PER]
    grouped={}
    for s in page_spares:
        cs=s["cat_slug"]
        if cs not in grouped:
            co=next((c for c in all_cats if c["slug"]==cs),{})
            grouped[cs]={**co,"slug":cs,"spares":[]}
        grouped[cs]["spares"].append(s)
    return render_template("spares.html",categories=list(grouped.values()),all_cats=all_cats,query=q,cat_filter=cat_filter,sort=sort,page=page,total_pages=tp,total=total)

@app.route("/spares/<slug>")
def spare_category(slug):
    slug=slugify(slug); data=load_data()
    cat=next((c for c in data["spare_categories"] if c["slug"]==slug),None)
    if not cat: abort(404)
    return render_template("spare_detail.html",category=cat)

@app.route("/api/search-suggest")
def search_suggest():
    q=sanitize(request.args.get("q",""),60).lower().strip()
    if len(q)<2: return jsonify([])
    data=load_data(); idx=_get_index(data)
    suggestions=[]; seen=set()
    for m in idx["machines"]:
        if q in m["name"].lower():
            key=("machine",m["name"])
            if key not in seen: seen.add(key); suggestions.append({"type":"machine","label":m["name"],"sub":m["category"],"icon":"⚙️"})
    for s in idx["spares"]:
        if q in s["name"].lower() or q in s["part_no"].lower():
            key=("spare",s["part_no"])
            if key not in seen: seen.add(key); suggestions.append({"type":"spare","label":s["name"],"sub":s["part_no"],"icon":"🔩"})
    suggestions=sorted(suggestions,key=lambda x:(0 if x["label"].lower().startswith(q) else 1))[:8]
    resp=jsonify(suggestions); resp.headers["Cache-Control"]="no-store"
    return resp

RESULTS_PER_PAGE=10

@app.route("/search")
def search():
    q=sanitize(request.args.get("q",""),100).lower()
    filter_type=request.args.get("type","all")
    if filter_type not in {"all","machine","spare"}: filter_type="all"
    try: page=max(1,int(request.args.get("page",1) or 1))
    except: page=1
    data=load_data()
    mr,sr=[],[]
    if q: mr,sr=_do_search(q,data)
    if filter_type=="machine": sr=[]
    if filter_type=="spare":   mr=[]
    all_results=[("machine",r) for r in mr]+[("spare",r) for r in sr]
    total=len(all_results); tp=max(1,(total+RESULTS_PER_PAGE-1)//RESULTS_PER_PAGE)
    page=min(page,tp)
    pr=all_results[(page-1)*RESULTS_PER_PAGE:page*RESULTS_PER_PAGE]
    return render_template("search.html",query=q,filter_type=filter_type,
        machines=[r for t,r in pr if t=="machine"],spares=[r for t,r in pr if t=="spare"],
        total=total,total_machines=len(mr),total_spares=len(sr),page=page,total_pages=tp,per_page=RESULTS_PER_PAGE)

@app.route("/enquiry",methods=["GET","POST"])
@csrf_protected
def enquiry():
    data=load_data()
    if request.method=="POST":
        ip=_client_ip()
        if not _check_rate_limit(_enquiry_attempts,ip,MAX_ENQUIRY,ENQUIRY_WINDOW):
            flash("Too many enquiries submitted. Please wait 10 minutes.","error")
            return render_template("enquiry.html",prefill=request.form,machine_categories=data["machine_categories"],spare_categories=data["spare_categories"]),429
        name=sanitize(request.form.get("name",""),120)
        email=sanitize(request.form.get("email",""),254)
        phone=sanitize(request.form.get("phone",""),30)
        subject=sanitize(request.form.get("subject",""),120)
        item=sanitize(request.form.get("item",""),200)
        message=sanitize(request.form.get("message",""),2000)
        errors=[]
        if not name: errors.append("Name is required.")
        if not re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+",email): errors.append("A valid email address is required.")
        if not subject: errors.append("Please select a subject.")
        if len(message)<10: errors.append("Message must be at least 10 characters.")
        if errors:
            for err in errors: flash(err,"error")
            return render_template("enquiry.html",prefill=request.form,machine_categories=data["machine_categories"],spare_categories=data["spare_categories"]),422
        _record_hit(_enquiry_attempts,ip)
        entry={"id":str(uuid.uuid4()),"name":name,"email":email,"phone":phone,"subject":subject,"item":item,"message":message,"timestamp":datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"ip":ip,"read":False}
        data["enquiries"].append(entry); save_data(data)
        logger.info("New enquiry from %s <%s>",name,email,extra={"request_id":_rid()})
        flash("Your enquiry has been submitted! We'll get back to you within 24 hours.","success")
        return redirect(url_for("enquiry"))
    prefill={"subject":sanitize(request.args.get("subject",""),120),"item":sanitize(request.args.get("item",""),200),"message":sanitize(request.args.get("message",""),2000)}
    return render_template("enquiry.html",prefill=prefill,machine_categories=data["machine_categories"],spare_categories=data["spare_categories"])

@app.route("/contact")
def contact(): return render_template("contact.html")

# ── ADMIN AUTH ────────────────────────────────────────────────

@app.route("/admin/login",methods=["GET","POST"])
@csrf_protected
def admin_login():
    if session.get("admin"): return redirect(url_for("admin_dashboard"))
    ip=_client_ip()
    if not _check_rate_limit(_login_attempts,ip,MAX_LOGIN_ATTEMPTS,LOCKOUT_SECS):
        flash("Too many failed attempts. Please wait 5 minutes.","error")
        return render_template("admin/login.html")
    if request.method=="POST":
        data=load_data()
        username=sanitize(request.form.get("username",""),80)
        password=request.form.get("password","")
        admin_record=_find_admin(data,username)
        if admin_record and _verify_admin_password(admin_record,password):
            if not admin_record.get("pbkdf2"):
                admin_record["password"]=hash_password(password)
                admin_record["pbkdf2"]=True
            # Feature 2: store last login
            admin_record["last_login"]=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            save_data(data)
            _clear_hits(_login_attempts,ip)
            session.clear(); session.permanent=True
            session["admin"]=True
            session["admin_username"]=username
            session["admin_role"]=admin_record.get("role","superadmin")
            session["logged_in_at"]=time.time()
            session["_unread_cache"]=_get_unread(data)
            log_activity("login",f"Logged in from {ip}")
            logger.info("Admin login: %s from %s",username,ip,extra={"request_id":_rid()})
            next_url=request.args.get("next","")
            if next_url and next_url.startswith("/admin"): return redirect(next_url)
            return redirect(url_for("admin_dashboard"))
        _record_hit(_login_attempts,ip)
        logger.warning("Failed admin login from %s (user=%s)",ip,username,extra={"request_id":_rid()})
        remaining=MAX_LOGIN_ATTEMPTS-len([t for t in _login_attempts.get(ip,[]) if time.time()-t<LOCKOUT_SECS])
        flash(f"Invalid credentials. {remaining} attempt(s) remaining.","error")
    return render_template("admin/login.html")

@app.route("/admin/logout",methods=["POST"])
@login_required
@csrf_protected
def admin_logout():
    log_activity("logout","Logged out")
    session.clear()
    flash("You have been logged out.","success")
    return redirect(url_for("index"))

# ── ADMIN DASHBOARD ───────────────────────────────────────────

@app.route("/admin")
@app.route("/admin/")
@login_required
def admin_dashboard():
    data=load_data()
    total_machines=sum(len(c["machines"]) for c in data["machine_categories"])
    total_spares=sum(len(c["spares"]) for c in data["spare_categories"])
    unread=_get_unread(data); session["_unread_cache"]=unread
    recent_enq=sorted(data["enquiries"],key=lambda x:x["timestamp"],reverse=True)[:5]
    primary=data.get("admin",{})
    if primary.get("force_change") and session.get("admin_username")==primary.get("username"):
        flash("Please change your default admin password in Settings.","error")
    recent_activity=data.get("activity_log",[])[:10]
    return render_template("admin/dashboard.html",
        total_machines=total_machines,total_spares=total_spares,
        total_enquiries=len(data["enquiries"]),unread_enquiries=unread,
        recent_enquiries=recent_enq,recent_activity=recent_activity,
        db_backend="postgresql" if DATABASE_URL else "json")

# ── ADMIN: MACHINES ───────────────────────────────────────────

@app.route("/admin/machines")
@login_required
def admin_machines():
    return render_template("admin/machines.html",categories=load_data()["machine_categories"])

@app.route("/admin/machines/add",methods=["GET","POST"])
@login_required
@csrf_protected
def admin_add_machine_category():
    if request.method=="POST":
        data=load_data(); name=sanitize(request.form.get("name",""),120)
        if not name: flash("Category name is required.","error"); return render_template("admin/machine_category_form.html",cat=None)
        slug=slugify(name)
        if slug in [c["slug"] for c in data["machine_categories"]]: slug+="-"+uuid.uuid4().hex[:4]
        data["machine_categories"].append({"id":max((c["id"] for c in data["machine_categories"]),default=0)+1,"name":name,"slug":slug,"description":sanitize(request.form.get("description",""),1000),"image":save_image("image"),"machines":[]})
        save_data(data); log_activity("add_machine_category",f"Added: {name}"); flash("Machine category added.","success")
        return redirect(url_for("admin_machines"))
    return render_template("admin/machine_category_form.html",cat=None)

@app.route("/admin/machines/edit/<int:cat_id>",methods=["GET","POST"])
@login_required
@csrf_protected
def admin_edit_machine_category(cat_id):
    data=load_data(); cat=next((c for c in data["machine_categories"] if c["id"]==cat_id),None)
    if not cat: abort(404)
    if request.method=="POST":
        name=sanitize(request.form.get("name",""),120)
        if not name: flash("Category name is required.","error"); return render_template("admin/machine_category_form.html",cat=cat)
        cat["name"]=name; cat["description"]=sanitize(request.form.get("description",""),1000)
        new_img=save_image("image")
        if new_img: delete_image(cat.get("image")); cat["image"]=new_img
        save_data(data); log_activity("edit_machine_category",f"Edited id={cat_id}: {name}"); flash("Category updated.","success")
        return redirect(url_for("admin_machines"))
    return render_template("admin/machine_category_form.html",cat=cat)

@app.route("/admin/machines/delete/<int:cat_id>",methods=["POST"])
@login_required
@csrf_protected
def admin_delete_machine_category(cat_id):
    data=load_data(); cat=next((c for c in data["machine_categories"] if c["id"]==cat_id),None)
    if cat:
        delete_image(cat.get("image"))
        for m in cat.get("machines",[]): delete_image(m.get("image"))
        data["machine_categories"]=[c for c in data["machine_categories"] if c["id"]!=cat_id]
        save_data(data); log_activity("delete_machine_category",f"Deleted id={cat_id}"); flash("Category and all its machines deleted.","success")
    return redirect(url_for("admin_machines"))

@app.route("/admin/machines/<int:cat_id>/add-machine",methods=["GET","POST"])
@login_required
@csrf_protected
def admin_add_machine(cat_id):
    data=load_data(); cat=next((c for c in data["machine_categories"] if c["id"]==cat_id),None)
    if not cat: abort(404)
    if request.method=="POST":
        name=sanitize(request.form.get("name",""),120)
        if not name: flash("Machine name is required.","error"); return render_template("admin/machine_form.html",cat=cat,machine=None)
        cat["machines"].append({"id":max((m["id"] for c in data["machine_categories"] for m in c["machines"]),default=0)+1,"name":name,"description":sanitize(request.form.get("description",""),1000),"specs":sanitize(request.form.get("specs",""),500),"image":save_image("image")})
        save_data(data); log_activity("add_machine",f"Added: {name}"); flash("Machine added.","success")
        return redirect(url_for("admin_machines"))
    return render_template("admin/machine_form.html",cat=cat,machine=None)

@app.route("/admin/machines/<int:cat_id>/edit-machine/<int:machine_id>",methods=["GET","POST"])
@login_required
@csrf_protected
def admin_edit_machine(cat_id,machine_id):
    data=load_data(); cat=next((c for c in data["machine_categories"] if c["id"]==cat_id),None)
    if not cat: abort(404)
    machine=next((m for m in cat["machines"] if m["id"]==machine_id),None)
    if not machine: abort(404)
    if request.method=="POST":
        name=sanitize(request.form.get("name",""),120)
        if not name: flash("Machine name is required.","error"); return render_template("admin/machine_form.html",cat=cat,machine=machine)
        machine["name"]=name; machine["description"]=sanitize(request.form.get("description",""),1000); machine["specs"]=sanitize(request.form.get("specs",""),500)
        new_img=save_image("image")
        if new_img: delete_image(machine.get("image")); machine["image"]=new_img
        save_data(data); log_activity("edit_machine",f"Edited id={machine_id}: {name}"); flash("Machine updated.","success")
        return redirect(url_for("admin_machines"))
    return render_template("admin/machine_form.html",cat=cat,machine=machine)

@app.route("/admin/machines/<int:cat_id>/delete-machine/<int:machine_id>",methods=["POST"])
@login_required
@csrf_protected
def admin_delete_machine(cat_id,machine_id):
    data=load_data(); cat=next((c for c in data["machine_categories"] if c["id"]==cat_id),None)
    if cat:
        machine=next((m for m in cat["machines"] if m["id"]==machine_id),None)
        if machine: delete_image(machine.get("image")); log_activity("delete_machine",f"Deleted id={machine_id}")
        cat["machines"]=[m for m in cat["machines"] if m["id"]!=machine_id]
        save_data(data); flash("Machine deleted.","success")
    return redirect(url_for("admin_machines"))

# ── ADMIN: SPARES ─────────────────────────────────────────────

@app.route("/admin/spares")
@login_required
def admin_spares():
    return render_template("admin/spares.html",categories=load_data()["spare_categories"])

@app.route("/admin/spares/add",methods=["GET","POST"])
@login_required
@csrf_protected
def admin_add_spare_category():
    if request.method=="POST":
        data=load_data(); name=sanitize(request.form.get("name",""),120)
        if not name: flash("Category name is required.","error"); return render_template("admin/spare_category_form.html",cat=None)
        slug=slugify(name)
        if slug in [c["slug"] for c in data["spare_categories"]]: slug+="-"+uuid.uuid4().hex[:4]
        data["spare_categories"].append({"id":max((c["id"] for c in data["spare_categories"]),default=0)+1,"name":name,"slug":slug,"description":sanitize(request.form.get("description",""),1000),"image":save_image("image"),"spares":[]})
        save_data(data); log_activity("add_spare_category",f"Added: {name}"); flash("Spare category added.","success")
        return redirect(url_for("admin_spares"))
    return render_template("admin/spare_category_form.html",cat=None)

@app.route("/admin/spares/edit/<int:cat_id>",methods=["GET","POST"])
@login_required
@csrf_protected
def admin_edit_spare_category(cat_id):
    data=load_data(); cat=next((c for c in data["spare_categories"] if c["id"]==cat_id),None)
    if not cat: abort(404)
    if request.method=="POST":
        name=sanitize(request.form.get("name",""),120)
        if not name: flash("Category name is required.","error"); return render_template("admin/spare_category_form.html",cat=cat)
        cat["name"]=name; cat["description"]=sanitize(request.form.get("description",""),1000)
        new_img=save_image("image")
        if new_img: delete_image(cat.get("image")); cat["image"]=new_img
        save_data(data); log_activity("edit_spare_category",f"Edited id={cat_id}: {name}"); flash("Category updated.","success")
        return redirect(url_for("admin_spares"))
    return render_template("admin/spare_category_form.html",cat=cat)

@app.route("/admin/spares/delete/<int:cat_id>",methods=["POST"])
@login_required
@csrf_protected
def admin_delete_spare_category(cat_id):
    data=load_data(); cat=next((c for c in data["spare_categories"] if c["id"]==cat_id),None)
    if cat:
        delete_image(cat.get("image"))
        for s in cat.get("spares",[]): delete_image(s.get("image"))
        data["spare_categories"]=[c for c in data["spare_categories"] if c["id"]!=cat_id]
        save_data(data); log_activity("delete_spare_category",f"Deleted id={cat_id}"); flash("Category and all spares deleted.","success")
    return redirect(url_for("admin_spares"))

@app.route("/admin/spares/<int:cat_id>/add-spare",methods=["GET","POST"])
@login_required
@csrf_protected
def admin_add_spare(cat_id):
    data=load_data(); cat=next((c for c in data["spare_categories"] if c["id"]==cat_id),None)
    if not cat: abort(404)
    if request.method=="POST":
        name=sanitize(request.form.get("name",""),120)
        if not name: flash("Part name is required.","error"); return render_template("admin/spare_form.html",cat=cat,spare=None)
        cat["spares"].append({"id":max((s["id"] for c in data["spare_categories"] for s in c["spares"]),default=0)+1,"name":name,"part_no":sanitize(request.form.get("part_no",""),60),"description":sanitize(request.form.get("description",""),1000),"compatible":sanitize(request.form.get("compatible",""),300),"image":save_image("image")})
        save_data(data); log_activity("add_spare",f"Added: {name}"); flash("Spare added.","success")
        return redirect(url_for("admin_spares"))
    return render_template("admin/spare_form.html",cat=cat,spare=None)

@app.route("/admin/spares/<int:cat_id>/edit-spare/<int:spare_id>",methods=["GET","POST"])
@login_required
@csrf_protected
def admin_edit_spare(cat_id,spare_id):
    data=load_data(); cat=next((c for c in data["spare_categories"] if c["id"]==cat_id),None)
    if not cat: abort(404)
    spare=next((s for s in cat["spares"] if s["id"]==spare_id),None)
    if not spare: abort(404)
    if request.method=="POST":
        name=sanitize(request.form.get("name",""),120)
        if not name: flash("Part name is required.","error"); return render_template("admin/spare_form.html",cat=cat,spare=spare)
        spare["name"]=name; spare["part_no"]=sanitize(request.form.get("part_no",""),60); spare["description"]=sanitize(request.form.get("description",""),1000); spare["compatible"]=sanitize(request.form.get("compatible",""),300)
        new_img=save_image("image")
        if new_img: delete_image(spare.get("image")); spare["image"]=new_img
        save_data(data); log_activity("edit_spare",f"Edited id={spare_id}: {name}"); flash("Spare updated.","success")
        return redirect(url_for("admin_spares"))
    return render_template("admin/spare_form.html",cat=cat,spare=spare)

@app.route("/admin/spares/<int:cat_id>/delete-spare/<int:spare_id>",methods=["POST"])
@login_required
@csrf_protected
def admin_delete_spare(cat_id,spare_id):
    data=load_data(); cat=next((c for c in data["spare_categories"] if c["id"]==cat_id),None)
    if cat:
        spare=next((s for s in cat["spares"] if s["id"]==spare_id),None)
        if spare: delete_image(spare.get("image")); log_activity("delete_spare",f"Deleted id={spare_id}")
        cat["spares"]=[s for s in cat["spares"] if s["id"]!=spare_id]
        save_data(data); flash("Spare deleted.","success")
    return redirect(url_for("admin_spares"))

# ── ADMIN: ENQUIRIES (Feature 1: Pagination) ──────────────────

ENQ_PER_PAGE = 20

@app.route("/admin/enquiries")
@login_required
def admin_enquiries():
    data=load_data(); all_enq=data["enquiries"]
    filter_year=sanitize(request.args.get("year",""),4)
    filter_month=sanitize(request.args.get("month",""),2)
    filter_status=request.args.get("status","all")
    filter_subject=sanitize(request.args.get("subject",""),120)
    sort_by=request.args.get("sort","newest")
    try: page=max(1,int(request.args.get("page",1) or 1))
    except: page=1
    if filter_status not in ("all","read","unread"): filter_status="all"
    if sort_by not in ("newest","oldest","name"): sort_by="newest"
    if filter_year and not filter_year.isdigit(): filter_year=""
    if filter_month and not filter_month.isdigit(): filter_month=""
    def _ts(e): return e.get("timestamp","")
    def _year(e): return _ts(e)[:4]
    def _month(e): return _ts(e)[5:7]
    year_counts=defaultdict(int); month_counts=defaultdict(int); subject_counts=defaultdict(int)
    for e in all_enq:
        y=_year(e); m=_month(e)
        if y: year_counts[y]+=1
        if filter_year==y and m: month_counts[m]+=1
        if e.get("subject"): subject_counts[e["subject"]]+=1
    filtered=all_enq
    if filter_year:    filtered=[e for e in filtered if _year(e)==filter_year]
    if filter_month:   filtered=[e for e in filtered if _month(e)==filter_month]
    if filter_status=="read":   filtered=[e for e in filtered if e.get("read")]
    if filter_status=="unread": filtered=[e for e in filtered if not e.get("read")]
    if filter_subject: filtered=[e for e in filtered if e.get("subject","")==filter_subject]
    if sort_by=="oldest": filtered=sorted(filtered,key=_ts)
    elif sort_by=="name": filtered=sorted(filtered,key=lambda e:e.get("name","").lower())
    else: filtered=sorted(filtered,key=_ts,reverse=True)
    total_filtered=len(filtered)
    total_pages=max(1,(total_filtered+ENQ_PER_PAGE-1)//ENQ_PER_PAGE)
    page=min(page,total_pages)
    enquiries=filtered[(page-1)*ENQ_PER_PAGE:page*ENQ_PER_PAGE]
    all_years=sorted(year_counts.keys(),reverse=True)
    MONTH_NAMES=["","Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    return render_template("admin/enquiries.html",enquiries=enquiries,all_enq_count=len(all_enq),total_filtered=total_filtered,year_counts=dict(year_counts),month_counts=dict(month_counts),subject_counts=dict(subject_counts),all_years=all_years,month_names=MONTH_NAMES,filter_year=filter_year,filter_month=filter_month,filter_status=filter_status,filter_subject=filter_subject,sort_by=sort_by,page=page,total_pages=total_pages,enq_per_page=ENQ_PER_PAGE)

@app.route("/admin/enquiries/<enq_id>")
@login_required
def admin_view_enquiry(enq_id):
    data=load_data(); enq=next((e for e in data["enquiries"] if e["id"]==enq_id),None)
    if not enq: abort(404)
    if not enq.get("read"):
        enq["read"]=True; save_data(data); _refresh_unread_cache(data)
    return render_template("admin/enquiry_detail.html",enq=enq)

@app.route("/admin/enquiries/<enq_id>/read",methods=["POST"])
@login_required
@csrf_protected
def admin_mark_read(enq_id):
    data=load_data()
    for e in data["enquiries"]:
        if e["id"]==enq_id: e["read"]=True; break
    save_data(data); _refresh_unread_cache(data)
    return redirect(url_for("admin_enquiries"))

@app.route("/admin/enquiries/delete/<enq_id>",methods=["POST"])
@login_required
@csrf_protected
def admin_delete_enquiry(enq_id):
    data=load_data(); data["enquiries"]=[e for e in data["enquiries"] if e["id"]!=enq_id]
    save_data(data); _refresh_unread_cache(data); log_activity("delete_enquiry",f"Deleted {enq_id}"); flash("Enquiry deleted.","success")
    return redirect(url_for("admin_enquiries"))

@app.route("/admin/enquiries/mark-all-read",methods=["POST"])
@login_required
@csrf_protected
def admin_mark_all_read():
    data=load_data(); changed=sum(1 for e in data["enquiries"] if not e.get("read"))
    for e in data["enquiries"]: e["read"]=True
    save_data(data); _refresh_unread_cache(data); log_activity("mark_all_read",f"Marked {changed} as read")
    flash(f"Marked {changed} enquir{'y' if changed==1 else 'ies'} as read.","success")
    return redirect(url_for("admin_enquiries"))

@app.route("/admin/enquiries/bulk-delete",methods=["POST"])
@login_required
@csrf_protected
def admin_bulk_delete_enquiries():
    data=load_data(); before=len(data["enquiries"])
    data["enquiries"]=[e for e in data["enquiries"] if not e.get("read")]
    deleted=before-len(data["enquiries"]); save_data(data); _refresh_unread_cache(data)
    log_activity("bulk_delete_enquiries",f"Bulk deleted {deleted}")
    flash(f"Deleted {deleted} read enquir{'y' if deleted==1 else 'ies'}.","success")
    return redirect(url_for("admin_enquiries"))

@app.route("/admin/enquiries/export.csv")
@login_required
def admin_export_enquiries():
    data=load_data(); enquiries=sorted(data["enquiries"],key=lambda x:x["timestamp"],reverse=True)
    buf=io.StringIO(); w=csv.writer(buf)
    w.writerow(["ID","Name","Email","Phone","Subject","Item","Message","Timestamp","IP","Read"])
    for e in enquiries:
        w.writerow([e.get("id",""),e.get("name",""),e.get("email",""),e.get("phone",""),e.get("subject",""),e.get("item",""),e.get("message","").replace("\n"," "),e.get("timestamp",""),e.get("ip",""),"Yes" if e.get("read") else "No"])
    filename=f"enquiries_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.csv"
    resp=make_response(buf.getvalue())
    resp.headers["Content-Type"]="text/csv; charset=utf-8"
    resp.headers["Content-Disposition"]=f'attachment; filename="{filename}"; filename*=UTF-8\'\'{filename}'
    return resp

# ── ADMIN: ACTIVITY LOG (Feature 3) ───────────────────────────

@app.route("/admin/activity-log")
@login_required
@superadmin_required
def admin_activity_log():
    data=load_data(); logs=data.get("activity_log",[])
    try: page=max(1,int(request.args.get("page",1) or 1))
    except: page=1
    PER=50; total=len(logs); tp=max(1,(total+PER-1)//PER); page=min(page,tp)
    return render_template("admin/activity_log.html",logs=logs[(page-1)*PER:page*PER],page=page,total_pages=tp,total=total)

# ── ADMIN: MANAGE ADMINS (Feature 7) ─────────────────────────

@app.route("/admin/admins")
@login_required
@superadmin_required
def admin_manage_admins():
    data=load_data(); admins=_all_admins(data)
    return render_template("admin/admins.html",admins=admins)

@app.route("/admin/admins/add",methods=["GET","POST"])
@login_required
@superadmin_required
@csrf_protected
def admin_add_admin():
    if request.method=="POST":
        data=load_data()
        username=sanitize(request.form.get("username",""),80)
        password=request.form.get("password","")
        role=request.form.get("role","editor")
        if role not in ("superadmin","editor"): role="editor"
        if not username: flash("Username is required.","error"); return render_template("admin/admin_form.html",admin=None)
        if len(password)<12: flash("Password must be at least 12 characters.","error"); return render_template("admin/admin_form.html",admin=None)
        if _find_admin(data,username): flash("Username already exists.","error"); return render_template("admin/admin_form.html",admin=None)
        data.setdefault("admins",[]).append({"username":username,"password":hash_password(password),"pbkdf2":True,"role":role,"force_change":False,"last_login":None,"created_at":datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")})
        save_data(data); log_activity("add_admin",f"Added: {username} role={role}"); flash(f"Admin '{username}' created.","success")
        return redirect(url_for("admin_manage_admins"))
    return render_template("admin/admin_form.html",admin=None)

@app.route("/admin/admins/edit/<path:username>",methods=["GET","POST"])
@login_required
@superadmin_required
@csrf_protected
def admin_edit_admin(username):
    data=load_data(); admin=_find_admin(data,username)
    if not admin: abort(404)
    is_primary=(username==data["admin"].get("username"))
    if request.method=="POST":
        role=request.form.get("role","editor")
        if role not in ("superadmin","editor"): role="editor"
        new_pass=request.form.get("new_password","")
        if new_pass:
            if len(new_pass)<12: flash("Password must be at least 12 characters.","error"); return render_template("admin/admin_form.html",admin=admin,edit=True)
            admin["password"]=hash_password(new_pass); admin["pbkdf2"]=True
        admin["role"]=role; save_data(data); log_activity("edit_admin",f"Edited: {username} role={role}"); flash(f"Admin '{username}' updated.","success")
        return redirect(url_for("admin_manage_admins"))
    return render_template("admin/admin_form.html",admin=admin,edit=True,is_primary=is_primary)

@app.route("/admin/admins/delete/<path:username>",methods=["POST"])
@login_required
@superadmin_required
@csrf_protected
def admin_delete_admin(username):
    data=load_data()
    if username==data["admin"].get("username"): flash("Cannot delete the primary admin account.","error"); return redirect(url_for("admin_manage_admins"))
    if username==session.get("admin_username"): flash("Cannot delete your own account.","error"); return redirect(url_for("admin_manage_admins"))
    data["admins"]=[a for a in data.get("admins",[]) if a["username"]!=username]
    save_data(data); log_activity("delete_admin",f"Deleted: {username}"); flash(f"Admin '{username}' deleted.","success")
    return redirect(url_for("admin_manage_admins"))

# ── ADMIN: SETTINGS ───────────────────────────────────────────

@app.route("/admin/settings",methods=["GET","POST"])
@login_required
@csrf_protected
def admin_settings():
    data=load_data()
    current_username=session.get("admin_username","admin")
    admin_record=_find_admin(data,current_username) or data.get("admin",{})
    if request.method=="POST":
        action=request.form.get("action","")
        if action not in {"change_password","site_settings"}:
            flash("Invalid action.","error")
        elif action=="change_password":
            current=request.form.get("current_password",""); new_pass=request.form.get("new_password",""); confirm=request.form.get("confirm_password","")
            if not _verify_admin_password(admin_record,current): flash("Current password is incorrect.","error")
            elif len(new_pass)<12: flash("New password must be at least 12 characters.","error")
            elif new_pass!=confirm: flash("Passwords do not match.","error")
            else:
                admin_record["password"]=hash_password(new_pass); admin_record["pbkdf2"]=True; admin_record["force_change"]=False
                save_data(data); log_activity("change_password","Password changed"); flash("Password updated successfully.","success")
        elif action=="site_settings":
            if session.get("admin_role")!="superadmin":
                flash("Only superadmin can change site settings.","error")
            else:
                data["site_settings"]={"company_name":sanitize(request.form.get("company_name",""),120),"phone":sanitize(request.form.get("phone",""),30),"email":sanitize(request.form.get("email",""),254),"address":sanitize(request.form.get("address",""),300)}
                save_data(data); log_activity("update_site_settings","Site settings updated"); flash("Site settings updated.","success")
    return render_template("admin/settings.html",settings=data.get("site_settings",{}),force_change=admin_record.get("force_change",False),admin=admin_record,db_backend="postgresql" if DATABASE_URL else "json")

# ── ENTRY POINT ───────────────────────────────────────────────

if __name__=="__main__":
    port=int(os.environ.get("PORT",5000))
    debug=os.environ.get("FLASK_DEBUG","").lower() in ("1","true","yes")
    if debug: logger.warning("Running in DEBUG mode.",extra={"request_id":"-"})
    app.run(host="0.0.0.0",port=port,debug=debug)
