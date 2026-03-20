from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import json, os, hashlib, uuid
from functools import wraps
from werkzeug.utils import secure_filename

# Optional: load .env in development
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

app = Flask(__name__)
# Read secret from environment; fallback only for local/dev (not secure)
app.secret_key = os.environ.get("FFS_SECRET", "ffs_secret_2024")

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def save_uploaded_image(file_field):
    f = request.files.get(file_field)
    if f and f.filename and allowed_file(f.filename):
        ext = f.filename.rsplit(".", 1)[1].lower()
        filename = f"{uuid.uuid4().hex}.{ext}"
        f.save(os.path.join(UPLOAD_FOLDER, filename))
        return filename
    return ""

DATA_FILE = "data.json"

DEFAULT_DATA = {
    "admin": {"username": "admin", "password": hashlib.sha256("admin123".encode()).hexdigest()},
    "machine_categories": [
        {
            "id": 1,
            "name": "Vertical FFS Machines",
            "slug": "vertical-ffs",
            "description": "High-speed vertical form fill seal machines for granules, powders, and liquids.",
            "image": "vertical.jpg",
            "machines": [
                {"id": 101, "name": "VFFS-200", "description": "200 packs/min vertical FFS for granules & snacks", "specs": "Speed: 200 packs/min | Film width: 100–380mm | Power: 3.5kW"},
                {"id": 102, "name": "VFFS-350 Pro", "description": "Heavy duty model for powder & spice packaging", "specs": "Speed: 350 packs/min | Film width: 150–450mm | Power: 5kW"},
                {"id": 103, "name": "VFFS-Liquid 100", "description": "Liquid and paste packaging with servo control", "specs": "Speed: 100 packs/min | Volume: 50–1000ml | Power: 4kW"}
            ]
        },
        {
            "id": 2,
            "name": "Horizontal FFS Machines",
            "slug": "horizontal-ffs",
            "description": "Horizontal form fill seal for biscuits, candy bars, soap, and rigid products.",
            "image": "horizontal.jpg",
            "machines": [
                {"id": 201, "name": "HFFS-Flow 150", "description": "Flow wrap machine for bakery and confectionery products", "specs": "Speed: 150 packs/min | Film width: 200–600mm | Power: 4kW"},
                {"id": 202, "name": "HFFS-Pillow 200", "description": "Pillow pack wrapper for rigid products", "specs": "Speed: 200 packs/min | Film width: 250–700mm | Power: 5.5kW"}
            ]
        },
        {
            "id": 3,
            "name": "Rotary FFS Machines",
            "slug": "rotary-ffs",
            "description": "Rotary pouch form fill seal for stand-up pouches and gusseted bags.",
            "image": "rotary.jpg",
            "machines": [
                {"id": 301, "name": "RFFS-Rotary 8", "description": "8-station rotary FFS for premade pouches", "specs": "Speed: 60 pouches/min | Pouch size: 80–250mm | Power: 6kW"},
                {"id": 302, "name": "RFFS-Ziplock 12", "description": "12-station for ziplock stand-up pouches", "specs": "Speed: 80 pouches/min | Pouch size: 100–300mm | Power: 7kW"}
            ]
        },
        {
            "id": 4,
            "name": "Multi-Lane FFS Machines",
            "slug": "multilane-ffs",
            "description": "Multi-lane sachet machines for ketchup, shampoo, and small portioned products.",
            "image": "multilane.jpg",
            "machines": [
                {"id": 401, "name": "ML-Sachet 4L", "description": "4-lane sachet machine for liquid/semi-liquid products", "specs": "Speed: 400 sachets/min | Volume: 5–50ml | Power: 3kW"},
                {"id": 402, "name": "ML-Sachet 8L", "description": "8-lane high output sachet line", "specs": "Speed: 800 sachets/min | Volume: 5–100ml | Power: 5kW"}
            ]
        }
    ],
    "spare_categories": [
        {
            "id": 1,
            "name": "Sealing & Heating Parts",
            "slug": "sealing-heating",
            "description": "All sealing jaws, heating elements, and temperature-related components.",
            "spares": [
                {"id": 1001, "name": "Horizontal Sealing Jaw (VFFS)", "part_no": "VSJ-H-001", "description": "Chrome-plated horizontal sealing jaw for VFFS-200 and VFFS-350", "compatible": "VFFS-200, VFFS-350 Pro"},
                {"id": 1002, "name": "Vertical Sealing Jaw Set", "part_no": "VSJ-V-002", "description": "Pair of vertical sealing jaws with Teflon coating", "compatible": "VFFS-200, VFFS-350 Pro, VFFS-Liquid 100"},
                {"id": 1003, "name": "Heating Element 230V/500W", "part_no": "HE-230-500", "description": "Cartridge heater for sealing jaw assembly", "compatible": "All VFFS, HFFS models"},
                {"id": 1004, "name": "RTD Temperature Sensor PT100", "part_no": "RTD-PT100", "description": "Precision temperature sensor for jaw control", "compatible": "Universal"},
                {"id": 1005, "name": "Flow Wrap Sealing Roller", "part_no": "FWR-001", "description": "Knurled sealing roller for HFFS flow wrap machines", "compatible": "HFFS-Flow 150, HFFS-Pillow 200"}
            ]
        },
        {
            "id": 2,
            "name": "Drive & Motion Components",
            "slug": "drive-motion",
            "description": "Servo drives, motors, belts, chains, and transmission parts.",
            "spares": [
                {"id": 2001, "name": "Servo Motor 400W", "part_no": "SM-400W", "description": "AC servo motor for film pulling mechanism", "compatible": "VFFS-200, VFFS-350 Pro"},
                {"id": 2002, "name": "Timing Belt HTD 5M-750", "part_no": "TB-5M-750", "description": "Reinforced timing belt for main drive", "compatible": "VFFS series"},
                {"id": 2003, "name": "Gear Box 1:20 Ratio", "part_no": "GB-1-20", "description": "Helical gear reduction box for cutter drive", "compatible": "HFFS-Flow 150"},
                {"id": 2004, "name": "Linear Guide Rail 600mm", "part_no": "LGR-600", "description": "Precision linear guide with carriage block", "compatible": "RFFS-Rotary 8, RFFS-Ziplock 12"},
                {"id": 2005, "name": "Chain Sprocket Set", "part_no": "CSS-001", "description": "Drive chain and sprocket for conveyor system", "compatible": "HFFS-Pillow 200"}
            ]
        },
        {
            "id": 3,
            "name": "Film & Forming Parts",
            "slug": "film-forming",
            "description": "Forming tubes, film guides, rollers, and bag shaping components.",
            "spares": [
                {"id": 3001, "name": "Forming Tube Ø60mm", "part_no": "FT-060", "description": "Stainless steel forming tube for small pouches", "compatible": "VFFS-200"},
                {"id": 3002, "name": "Forming Tube Ø90mm", "part_no": "FT-090", "description": "Medium forming tube for standard packaging", "compatible": "VFFS-200, VFFS-350 Pro"},
                {"id": 3003, "name": "Film Tension Roller Set", "part_no": "FTR-SET", "description": "Three-roller film tension assembly", "compatible": "VFFS series, HFFS series"},
                {"id": 3004, "name": "Film Dancer Arm", "part_no": "FDA-001", "description": "Spring-loaded film dancer for consistent tension", "compatible": "All FFS machines"},
                {"id": 3005, "name": "Gusset Former Plate", "part_no": "GFP-001", "description": "Side gusset forming plate for stand-up pouches", "compatible": "RFFS series"}
            ]
        },
        {
            "id": 4,
            "name": "Pneumatic & Vacuum Parts",
            "slug": "pneumatic-vacuum",
            "description": "Air cylinders, valves, suction cups, vacuum pumps, and pneumatic fittings.",
            "spares": [
                {"id": 4001, "name": "Air Cylinder Ø32 × 100mm", "part_no": "AC-32-100", "description": "Double-acting air cylinder for jaw actuation", "compatible": "VFFS-350 Pro, RFFS series"},
                {"id": 4002, "name": "Solenoid Valve 5/2-way 1/4\"", "part_no": "SV-52-014", "description": "Pneumatic solenoid valve for cylinder control", "compatible": "Universal"},
                {"id": 4003, "name": "Vacuum Suction Cup Ø40mm", "part_no": "VSC-040", "description": "Silicone suction cup for pouch opening", "compatible": "RFFS-Rotary 8, RFFS-Ziplock 12"},
                {"id": 4004, "name": "Pressure Regulator FRL Unit", "part_no": "FRL-001", "description": "Filter-Regulator-Lubricator assembly", "compatible": "All pneumatic FFS machines"},
                {"id": 4005, "name": "Vacuum Pump 40L/min", "part_no": "VP-040", "description": "Oil-free vacuum pump for film handling", "compatible": "RFFS series, ML-Sachet series"}
            ]
        },
        {
            "id": 5,
            "name": "Control & Electrical Parts",
            "slug": "control-electrical",
            "description": "PLCs, HMI screens, sensors, encoders, and electrical components.",
            "spares": [
                {"id": 5001, "name": "7\" HMI Touch Panel", "part_no": "HMI-7T", "description": "Color touch HMI for machine control interface", "compatible": "VFFS-350 Pro, HFFS-Pillow 200"},
                {"id": 5002, "name": "PLC CPU Module", "part_no": "PLC-CPU-01", "description": "Main PLC controller with 32 I/O points", "compatible": "VFFS series"},
                {"id": 5003, "name": "Proximity Sensor M12 NPN", "part_no": "PS-M12-NPN", "description": "Inductive proximity sensor for position detection", "compatible": "Universal"},
                {"id": 5004, "name": "Rotary Encoder 600 PPR", "part_no": "RE-600", "description": "Shaft encoder for film length measurement", "compatible": "All FFS machines"},
                {"id": 5005, "name": "SSR Solid State Relay 40A", "part_no": "SSR-40A", "description": "Solid state relay for heater power control", "compatible": "Universal"}
            ]
        },
        {
            "id": 6,
            "name": "Cutting & Perforating Parts",
            "slug": "cutting-perforating",
            "description": "Rotary knives, cross-cut blades, perforation tools, and cutting cylinders.",
            "spares": [
                {"id": 6001, "name": "Cross Cut Blade Set", "part_no": "CCB-SET", "description": "Hardened steel cross-cut blade pair", "compatible": "VFFS-200, VFFS-350 Pro"},
                {"id": 6002, "name": "Rotary Knife Cylinder", "part_no": "RKC-001", "description": "Rotary knife assembly for continuous cutting", "compatible": "HFFS-Flow 150"},
                {"id": 6003, "name": "Perforation Blade Ø150mm", "part_no": "PB-150", "description": "Circular perforation blade for tear-notch", "compatible": "ML-Sachet series"},
                {"id": 6004, "name": "Anvil Roller (Hardened)", "part_no": "AR-H-001", "description": "Hardened anvil roller for rotary cutting", "compatible": "HFFS series"}
            ]
        }
    ],
    "enquiries": []
}

def load_data():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w") as f:
            json.dump(DEFAULT_DATA, f, indent=2)
    with open(DATA_FILE) as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated

# ── PUBLIC ROUTES ──────────────────────────────────────────────

@app.route("/")
def index():
    data = load_data()
    return render_template("index.html", 
                           machine_categories=data["machine_categories"],
                           spare_categories=data["spare_categories"])

@app.route("/machines")
def machines():
    data = load_data()
    return render_template("machines.html", categories=data["machine_categories"])

@app.route("/machines/<slug>")
def machine_category(slug):
    data = load_data()
    cat = next((c for c in data["machine_categories"] if c["slug"] == slug), None)
    if not cat:
        return redirect(url_for("machines"))
    return render_template("machine_detail.html", category=cat)

@app.route("/spares")
def spares():
    data = load_data()
    return render_template("spares.html", categories=data["spare_categories"])

@app.route("/spares/<slug>")
def spare_category(slug):
    data = load_data()
    cat = next((c for c in data["spare_categories"] if c["slug"] == slug), None)
    if not cat:
        return redirect(url_for("spares"))
    return render_template("spare_detail.html", category=cat)

@app.route("/enquiry", methods=["GET", "POST"])
def enquiry():
    if request.method == "POST":
        data = load_data()
        import time
        entry = {
            "id": int(time.time()),
            "name": request.form.get("name"),
            "email": request.form.get("email"),
            "phone": request.form.get("phone"),
            "subject": request.form.get("subject"),
            "message": request.form.get("message"),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        data["enquiries"].append(entry)
        save_data(data)
        flash("Your enquiry has been submitted! We'll get back to you soon.", "success")
        return redirect(url_for("enquiry"))
    return render_template("enquiry.html")

# ── ADMIN ROUTES ───────────────────────────────────────────────

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        data = load_data()
        username = request.form.get("username")
        password = hashlib.sha256(request.form.get("password", "").encode()).hexdigest()
        if username == data["admin"]["username"] and password == data["admin"]["password"]:
            session["admin"] = True
            return redirect(url_for("admin_dashboard"))
        flash("Invalid credentials.", "error")
    return render_template("admin/login.html")

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("index"))

@app.route("/admin")
@login_required
def admin_dashboard():
    data = load_data()
    total_machines = sum(len(c["machines"]) for c in data["machine_categories"])
    total_spares = sum(len(c["spares"]) for c in data["spare_categories"])
    return render_template("admin/dashboard.html", data=data,
                           total_machines=total_machines,
                           total_spares=total_spares,
                           total_enquiries=len(data["enquiries"]))

# MACHINE CATEGORY CRUD
@app.route("/admin/machines")
@login_required
def admin_machines():
    data = load_data()
    return render_template("admin/machines.html", categories=data["machine_categories"])

@app.route("/admin/machines/add", methods=["GET", "POST"])
@login_required
def admin_add_machine_category():
    if request.method == "POST":
        data = load_data()
        new_cat = {
            "id": max((c["id"] for c in data["machine_categories"]), default=0) + 1,
            "name": request.form["name"],
            "slug": request.form["name"].lower().replace(" ", "-"),
            "description": request.form["description"],
            "image": save_uploaded_image("image"),
            "machines": []
        }
        data["machine_categories"].append(new_cat)
        save_data(data)
        flash("Machine category added.", "success")
        return redirect(url_for("admin_machines"))
    return render_template("admin/machine_category_form.html", cat=None)

@app.route("/admin/machines/edit/<int:cat_id>", methods=["GET", "POST"])
@login_required
def admin_edit_machine_category(cat_id):
    data = load_data()
    cat = next((c for c in data["machine_categories"] if c["id"] == cat_id), None)
    if not cat:
        return redirect(url_for("admin_machines"))
    if request.method == "POST":
        cat["name"] = request.form["name"]
        cat["description"] = request.form["description"]
        new_img = save_uploaded_image("image")
        if new_img:
            cat["image"] = new_img
        save_data(data)
        flash("Category updated.", "success")
        return redirect(url_for("admin_machines"))
    return render_template("admin/machine_category_form.html", cat=cat)

@app.route("/admin/machines/delete/<int:cat_id>")
@login_required
def admin_delete_machine_category(cat_id):
    data = load_data()
    data["machine_categories"] = [c for c in data["machine_categories"] if c["id"] != cat_id]
    save_data(data)
    flash("Category deleted.", "success")
    return redirect(url_for("admin_machines"))

@app.route("/admin/machines/<int:cat_id>/add-machine", methods=["GET", "POST"])
@login_required
def admin_add_machine(cat_id):
    data = load_data()
    cat = next((c for c in data["machine_categories"] if c["id"] == cat_id), None)
    if request.method == "POST":
        new_machine = {
            "id": max((m["id"] for c in data["machine_categories"] for m in c["machines"]), default=0) + 1,
            "name": request.form["name"],
            "description": request.form["description"],
            "specs": request.form["specs"],
            "image": save_uploaded_image("image")
        }
        cat["machines"].append(new_machine)
        save_data(data)
        flash("Machine added.", "success")
        return redirect(url_for("admin_machines"))
    return render_template("admin/machine_form.html", cat=cat, machine=None)

@app.route("/admin/machines/<int:cat_id>/edit-machine/<int:machine_id>", methods=["GET", "POST"])
@login_required
def admin_edit_machine(cat_id, machine_id):
    data = load_data()
    cat = next((c for c in data["machine_categories"] if c["id"] == cat_id), None)
    machine = next((m for m in cat["machines"] if m["id"] == machine_id), None)
    if request.method == "POST":
        machine["name"] = request.form["name"]
        machine["description"] = request.form["description"]
        machine["specs"] = request.form["specs"]
        new_img = save_uploaded_image("image")
        if new_img:
            machine["image"] = new_img
        save_data(data)
        flash("Machine updated.", "success")
        return redirect(url_for("admin_machines"))
    return render_template("admin/machine_form.html", cat=cat, machine=machine)

@app.route("/admin/machines/<int:cat_id>/delete-machine/<int:machine_id>")
@login_required
def admin_delete_machine(cat_id, machine_id):
    data = load_data()
    cat = next((c for c in data["machine_categories"] if c["id"] == cat_id), None)
    cat["machines"] = [m for m in cat["machines"] if m["id"] != machine_id]
    save_data(data)
    flash("Machine deleted.", "success")
    return redirect(url_for("admin_machines"))

# SPARE CATEGORY CRUD
@app.route("/admin/spares")
@login_required
def admin_spares():
    data = load_data()
    return render_template("admin/spares.html", categories=data["spare_categories"])

@app.route("/admin/spares/add", methods=["GET", "POST"])
@login_required
def admin_add_spare_category():
    if request.method == "POST":
        data = load_data()
        new_cat = {
            "id": max((c["id"] for c in data["spare_categories"]), default=0) + 1,
            "name": request.form["name"],
            "slug": request.form["name"].lower().replace(" ", "-"),
            "description": request.form["description"],
            "image": save_uploaded_image("image"),
            "spares": []
        }
        data["spare_categories"].append(new_cat)
        save_data(data)
        flash("Spare category added.", "success")
        return redirect(url_for("admin_spares"))
    return render_template("admin/spare_category_form.html", cat=None)

@app.route("/admin/spares/edit/<int:cat_id>", methods=["GET", "POST"])
@login_required
def admin_edit_spare_category(cat_id):
    data = load_data()
    cat = next((c for c in data["spare_categories"] if c["id"] == cat_id), None)
    if request.method == "POST":
        cat["name"] = request.form["name"]
        cat["description"] = request.form["description"]
        new_img = save_uploaded_image("image")
        if new_img:
            cat["image"] = new_img
        save_data(data)
        flash("Category updated.", "success")
        return redirect(url_for("admin_spares"))
    return render_template("admin/spare_category_form.html", cat=cat)

@app.route("/admin/spares/delete/<int:cat_id>")
@login_required
def admin_delete_spare_category(cat_id):
    data = load_data()
    data["spare_categories"] = [c for c in data["spare_categories"] if c["id"] != cat_id]
    save_data(data)
    flash("Category deleted.", "success")
    return redirect(url_for("admin_spares"))

@app.route("/admin/spares/<int:cat_id>/add-spare", methods=["GET", "POST"])
@login_required
def admin_add_spare(cat_id):
    data = load_data()
    cat = next((c for c in data["spare_categories"] if c["id"] == cat_id), None)
    if request.method == "POST":
        new_spare = {
            "id": max((s["id"] for c in data["spare_categories"] for s in c["spares"]), default=0) + 1,
            "name": request.form["name"],
            "part_no": request.form["part_no"],
            "description": request.form["description"],
            "compatible": request.form["compatible"],
            "image": save_uploaded_image("image")
        }
        cat["spares"].append(new_spare)
        save_data(data)
        flash("Spare added.", "success")
        return redirect(url_for("admin_spares"))
    return render_template("admin/spare_form.html", cat=cat, spare=None)

@app.route("/admin/spares/<int:cat_id>/edit-spare/<int:spare_id>", methods=["GET", "POST"])
@login_required
def admin_edit_spare(cat_id, spare_id):
    data = load_data()
    cat = next((c for c in data["spare_categories"] if c["id"] == cat_id), None)
    spare = next((s for s in cat["spares"] if s["id"] == spare_id), None)
    if request.method == "POST":
        spare["name"] = request.form["name"]
        spare["part_no"] = request.form["part_no"]
        spare["description"] = request.form["description"]
        spare["compatible"] = request.form["compatible"]
        new_img = save_uploaded_image("image")
        if new_img:
            spare["image"] = new_img
        save_data(data)
        flash("Spare updated.", "success")
        return redirect(url_for("admin_spares"))
    return render_template("admin/spare_form.html", cat=cat, spare=spare)

@app.route("/admin/spares/<int:cat_id>/delete-spare/<int:spare_id>")
@login_required
def admin_delete_spare(cat_id, spare_id):
    data = load_data()
    cat = next((c for c in data["spare_categories"] if c["id"] == cat_id), None)
    cat["spares"] = [s for s in cat["spares"] if s["id"] != spare_id]
    save_data(data)
    flash("Spare deleted.", "success")
    return redirect(url_for("admin_spares"))

@app.route("/admin/enquiries")
@login_required
def admin_enquiries():
    data = load_data()
    return render_template("admin/enquiries.html", enquiries=data["enquiries"])

@app.route("/admin/enquiries/delete/<int:enq_id>")
@login_required
def admin_delete_enquiry(enq_id):
    data = load_data()
    data["enquiries"] = [e for e in data["enquiries"] if e["id"] != enq_id]
    save_data(data)
    flash("Enquiry deleted.", "success")
    return redirect(url_for("admin_enquiries"))

@app.route("/admin/settings", methods=["GET", "POST"])
@login_required
def admin_settings():
    data = load_data()
    if request.method == "POST":
        new_pass = request.form.get("new_password")
        if new_pass:
            data["admin"]["password"] = hashlib.sha256(new_pass.encode()).hexdigest()
            save_data(data)
            flash("Password updated.", "success")
    return render_template("admin/settings.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug_env = os.environ.get("FLASK_DEBUG", "").lower()
    debug = debug_env in ("1", "true", "yes")
    app.run(host="0.0.0.0", port=port, debug=debug)
