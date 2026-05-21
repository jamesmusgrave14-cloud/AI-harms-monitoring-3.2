# AI Harms Monitoring Dashboard (3.2 improved)

Static Vite/React dashboard for monitoring public reporting and open-source signals relevant to AI-enabled harms.

## What this version improves

- Working navigation buttons for all main views.
- Stricter open-source/open-weights evidence filtering.
- Clear separation between model/tool watchlist and open-source evidence.
- Client-side CSV export.
- Copyable automated briefing.
- Better evidence labelling: open-source signal, source type, priority, risk area and reasons.
- GitHub Actions uses `npm install`, so it does not require an existing `package-lock.json`.

## Local use

```bash
npm install
pip install -r requirements.txt
npm run build
npm run dev
```

## GitHub Pages

This repo is configured for a GitHub Pages URL ending `/3.2/`. If the repo name changes, update `base` in `vite.config.js`.
