# SIH26047 — "new" (redesigned copy)

A **redesigned copy** of the SIH26047 patient case-taking app, kept in its own
folder so you can run it side by side with the original:

- original: `sih26047-software-newdb` → http://localhost:8000
- this copy: `new` → **http://localhost:8001**

Same features and same Supabase project as the base copy — only the design
changed (Sunset Glass: warm cream, espresso ink, amber actions, dark gradient
hero with a touch-reactive ember background). Nothing here touches the base
project folder.

## Run it

Double-click `start-app.bat`, or from this folder:

```bash
node server.js
```

Then open http://localhost:8001 → log in → fill a case → Review & confirm →
prescribe → check the portal.

The static server is `server.js` (zero dependencies, port pinned to 8001).
Internet is needed for the CDN scripts (Supabase client, Tesseract OCR, fonts).

## Database

Uses the same Supabase project as `sih26047-software-newdb`; the schema lives
in `sql/` (run `schema.sql`, then `seed_dev.sql` in the SQL editor of a new
project if you want a fresh database).