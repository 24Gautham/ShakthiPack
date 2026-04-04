# Shakthi Pack Machineries — Production Deployment Guide

A Flask web application for managing and showcasing Form Fill Seal (FFS) packaging machines and spare parts, with a full admin panel.

---

## Quick Start (local)

```bash
git clone <repo>
cd ShakthiPack
pip install -r requirements.txt
cp .env.example .env        # Edit FFS_SECRET and FFS_SALT
python app.py
# Visit http://localhost:5000
```

Default admin: `admin` / `admin123` — **change this immediately after first login**.

---

## Production Deployment

### 1. Environment Variables

Copy `.env.example` to `.env` and configure:

| Variable          | Required | Description                                                  |
|-------------------|----------|--------------------------------------------------------------|
| `FFS_SECRET`      | ✅        | Long random string for session signing                       |
| `FFS_SALT`        | ✅        | Random salt for PBKDF2 password hashing                     |
| `HTTPS`           | —        | Set `1` when behind TLS (enables HSTS + Secure cookie)       |
| `FLASK_DEBUG`     | —        | Must be `0` or unset in production                           |
| `PORT`            | —        | Port to listen on (default: `5000`)                          |
| `LOG_LEVEL`       | —        | `DEBUG` / `INFO` / `WARNING` / `ERROR` (default: `INFO`)    |
| `WEB_CONCURRENCY` | —        | Gunicorn worker count override                               |

Generate secure values:
```bash
python -c "import secrets; print(secrets.token_hex(32))"   # for FFS_SECRET
python -c "import secrets; print(secrets.token_hex(16))"   # for FFS_SALT
```

### 2. Run with Gunicorn

```bash
# Using the included gunicorn.conf.py (recommended)
gunicorn -c gunicorn.conf.py app:app

# Or manually
gunicorn app:app --workers 4 --bind 0.0.0.0:5000 --timeout 60 --access-logfile -
```

### 3. Docker

```bash
# Build
docker build -f dockerfile -t shakthipack .

# Run
docker run -d \
  -p 5000:5000 \
  -e FFS_SECRET=your_secret_here \
  -e FFS_SALT=your_salt_here \
  -v $(pwd)/data.json:/app/data.json \
  -v $(pwd)/static/uploads:/app/static/uploads \
  --name shakthipack \
  shakthipack

# Health check
curl http://localhost:5000/health
```

### 4. Nginx Reverse Proxy

```nginx
server {
    listen 443 ssl http2;
    server_name yourdomain.com;

    ssl_certificate     /etc/ssl/certs/your_cert.pem;
    ssl_certificate_key /etc/ssl/private/your_key.pem;

    client_max_body_size 10M;   # match MAX_CONTENT_LENGTH

    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }

    # Serve static files directly — bypasses Python for ~95% of asset requests
    location /static/ {
        alias  /path/to/ShakthiPack/static/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
}

server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$host$request_uri;
}
```

Set `HTTPS=1` in `.env` after enabling TLS.

---

## Admin Panel

| URL                       | Description                           |
|---------------------------|---------------------------------------|
| `/admin/login`            | Admin login                           |
| `/admin/`                 | Dashboard (stats + recent enquiries)  |
| `/admin/machines`         | Manage machine categories & models    |
| `/admin/spares`           | Manage spare part categories & parts  |
| `/admin/enquiries`        | View, reply to, and manage enquiries  |
| `/admin/enquiries/export.csv` | Export all enquiries to CSV      |
| `/admin/settings`         | Change password, update site info     |

### Enquiry Management

- **View** full message with one click → auto-marks as read
- **Reply** directly via your email client
- **Mark All Read** in one click
- **Bulk Delete** all read enquiries at once
- **Export CSV** — download all enquiries for Excel/CRM import

---

## Application Structure

```
app.py                  Main Flask application
data.json               All data (machines, spares, enquiries, settings)
gunicorn.conf.py        Production gunicorn configuration
dockerfile              Docker image definition
Procfile                Heroku/Railway process definition
requirements.txt        Python dependencies
static/
  uploads/              User-uploaded images (back this up!)
templates/
  base.html             Public site base layout
  index.html            Homepage
  machines.html         Machine catalog listing
  machine_detail.html   Machine category detail
  spares.html           Spare parts catalog
  spare_detail.html     Spare category detail
  search.html           Search results
  enquiry.html          Public enquiry form
  contact.html          Contact / about page
  admin/
    base.html           Admin panel base layout
    dashboard.html      Stats + recent enquiries
    machines.html       Machine admin list
    machine_form.html   Add/edit machine
    machine_category_form.html
    spares.html         Spares admin list
    spare_form.html     Add/edit spare part
    spare_category_form.html
    enquiries.html      Enquiries list with bulk actions
    enquiry_detail.html Full enquiry view + reply
    settings.html       Password + site settings
    login.html          Admin login
  errors/
    403.html, 404.html, 500.html
```

---

## Security Features

| Feature                    | Implementation                                          |
|----------------------------|---------------------------------------------------------|
| CSRF protection            | Token on every POST form, validated server-side         |
| Password hashing           | PBKDF2-SHA256 (260,000 iterations); auto-upgrades SHA-256 |
| Login rate limiting        | 10 attempts / 5 min per IP (in-memory)                  |
| Secure session cookies     | HttpOnly, SameSite=Lax, Secure flag when HTTPS=1        |
| Session expiry             | 8 hours                                                 |
| Security headers           | X-Content-Type-Options, X-Frame-Options, X-XSS-Protection |
| Content-Security-Policy    | Restricts scripts, styles, fonts, images to self        |
| HSTS + preload             | Enabled when `HTTPS=1`                                  |
| File upload validation     | Extension whitelist; UUID filenames (no path traversal) |
| Atomic data writes         | Write to `.tmp`, then `os.replace()` — no corrupt JSON  |
| Input sanitization         | All form inputs stripped and length-capped              |
| Safe next-redirects        | `next=` param only accepted for `/admin` paths          |
| Non-root Docker user       | Runs as `appuser` inside container                      |

---

## Health & Observability

- **`GET /health`** — JSON liveness probe: `{"status": "ok", "timestamp": "..."}` (HTTP 200) or `{"status": "error"}` (HTTP 500)
- **`GET /robots.txt`** — Blocks `/admin/` and uploads from crawlers
- **`GET /sitemap.xml`** — Auto-generated sitemap for all public pages
- **Structured logs** — Timestamped, leveled output to stdout (gunicorn merges access + app logs)

---

## Recommended Next Steps

1. **Database** — Replace `data.json` with SQLite (via SQLAlchemy) for concurrent writes and proper transactions
2. **Email notifications** — Add SMTP (via `smtplib` or `Flask-Mail`) to email new enquiries
3. **Image CDN / object storage** — Serve uploads via S3/R2/Cloudflare for scale
4. **Automated backups** — Cron job to back up `data.json` + `static/uploads/` daily
5. **Sentry / error tracking** — Add `sentry-sdk[flask]` for production error monitoring
6. **Admin 2FA** — Add TOTP (e.g. `pyotp`) for two-factor admin login
7. **Pagination** — Add pagination to enquiries and spare parts lists as data grows
