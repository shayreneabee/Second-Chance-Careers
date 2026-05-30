# Find the Beat Public Launch

This app is ready to run behind Gunicorn with SQLite for now.

## Required Environment Variables

- `SECRET_KEY`: long random string. Never use the development fallback in public.
- `SESSION_COOKIE_SECURE=1`: enable once the site is on HTTPS.
- `TRUST_PROXY=1`: keep enabled on Render, Railway, Fly, Heroku-style hosts, or any reverse proxy.
- `MAX_UPLOAD_MB=100`: adjust if your host allows larger or smaller uploads.
- `INSTANCE_DIR`, `UPLOAD_DIR`, `DATABASE_PATH`: point these at persistent storage.

## Important SQLite Note

SQLite is fine for a first public beta, but only if the database and uploads live on persistent disk.
Do not deploy this to a platform with an ephemeral filesystem unless you attach a persistent volume.

Good first choices:

- Render with a persistent disk
- Fly.io with a volume
- Railway with persistent storage
- A small VPS

Avoid for SQLite/uploads unless configured carefully:

- Hosts that erase local files on restart or deploy
- Serverless-only platforms

## Start Command

The `Procfile` uses:

```bash
gunicorn app:app --workers 2 --threads 4 --timeout 120
```

## Local Production Smoke Test

```bash
SECRET_KEY=local-prod-test SESSION_COOKIE_SECURE=0 venv/bin/gunicorn app:app --workers 1 --threads 2 --bind 127.0.0.1:5002
```

Then open:

```text
http://127.0.0.1:5002/healthz
```

## Before Sharing Publicly

- Set a real `SECRET_KEY`.
- Turn on HTTPS.
- Verify persistent disk paths.
- Create a test account.
- Upload one image and one video.
- Send one inbox message between two profiles.
- Confirm the app still works after a restart.
