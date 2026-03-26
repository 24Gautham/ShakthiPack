# Shakthi Pack Machineries — Production Deployment Guide

## Quick Start (local)

```bash
pip install -r requirements.txt
cp .env.example .env          # edit FFS_SECRET and FFS_SALT
python app.py
```

## Production Deployment

### 1. Environment Variables

Copy `.env.example` to `.env` and set:

| Variable      | Description                                      |
|---------------|--------------------------------------------------|
| `FFS_SECRET`  | Long random string for session cookie signing    |
| `FFS_SALT`    | Random salt for PBKDF2 password hashing          |
| `HTTPS`       | Set to `1` when behind TLS (enables HSTS, Secure cookie) |
| `FLASK_DEBUG` | Must be `0` or unset in production               |

Generate secure values:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 2. Run with Gunicorn

```bash
gunicorn app:app --workers 2 --bind 0.0.0.0:5000 --timeout 120
```

### 3. Nginx Reverse Proxy (recommended)

```nginx
server {
    listen 443 ssl;
    server_name yourdomain.com;

    client_max_body_size 10M;   # match MAX_CONTENT_LENGTH

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /static/ {
        alias /path/to/ShakthiPackMachineries/static/;
        expires 30d;
    }
}
```

Set `HTTPS=1` in `.env` when proxied behind TLS.

### 4. First Login

- URL: `/admin/login`
- Default credentials: `admin` / `admin123`
- **Change the password immediately** — the app will show a warning until you do.

### 5. File Uploads

Uploaded images are saved to `static/uploads/`. Back this directory up regularly.

## Security Features Implemented

- **CSRF protection** on all POST forms
- **PBKDF2 password hashing** (260,000 iterations) — auto-upgrades old SHA-256 hashes on first login
- **Login rate limiting** — 10 attempts per 5-minute window per IP
- **Secure session cookies** — HttpOnly, SameSite=Lax, Secure flag when HTTPS=1
- **8-session lifetime** — sessions expire after 8 hours
- **Security headers** — X-Content-Type-Options, X-Frame-Options, X-XSS-Protection, Referrer-Policy, Permissions-Policy, HSTS (when HTTPS=1)
- **File upload validation** — extension whitelist + UUID filenames (no path traversal)
- **Atomic data writes** — JSON written to `.tmp` then renamed to prevent corruption
- **404/403/500 error pages** — no stack traces leaked to users
- **Input sanitization** — all form inputs stripped and length-capped before storage
- **POST-only deletes** — all destructive actions require POST + CSRF token
- **Safe redirects** — `next=` parameter validated to only allow `/admin` paths

## Recommended Next Steps for Full Production

1. **Database** — Replace `data.json` with SQLite or PostgreSQL for concurrent writes
2. **Image CDN** — Serve uploads via a CDN or object storage (S3/R2) for performance
3. **Email notifications** — Add SMTP config to email new enquiries to the business
4. **Backups** — Schedule automatic backups of `data.json` and `static/uploads/`
5. **Monitoring** — Add application logging to a file or service like Sentry
