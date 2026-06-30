# Second Chance Careers Production Storage

Second Chance Careers stores users, profiles, saved jobs, job activity, admin data, and uploads in application storage. For production, that storage must be persistent.

## Required Render Disk

Attach a Render persistent disk to the `second-chance-careers` web service:

- Mount path: `/var/data`
- Database path: `/var/data/second_chance.db`
- Upload path: `/var/data/uploads`

Required environment variables:

```env
DATABASE_PATH=/var/data/second_chance.db
UPLOAD_DIR=/var/data/uploads
```

The `render.yaml` file declares this disk, but if the live Render service was created manually, confirm the disk is attached in the Render dashboard.

## How To Verify

Open the founder dashboard:

```text
https://secondchancecareers.org/admin
```

The storage panel should say:

```text
Persistent storage is active
```

If it says storage needs attention, the app is using an emergency fallback path. That keeps the site online, but it is not sustainable for real users because fallback storage may be lost on redeploys or restarts.

## Long-Term Recommendation

The durable next step is PostgreSQL for user accounts, profiles, jobs, saved jobs, applications, admin analytics, and activity logs. Keep uploads on persistent disk or move them to object storage later.
