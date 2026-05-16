# Boxi Catalog

Remote product catalog for the Boxi iOS app. Auto-refreshed daily via bol.com API.

## How it works

- The Boxi app fetches `catalog.json` on launch and caches it.
- A GitHub Action runs every night (04:00 UTC) to rebuild the catalog with up-to-date prices and popular products from bol.com.
- If `catalog.json` changes, the action commits and pushes — users see the new catalog on the next app launch.

## Manual refresh

Go to **Actions → Refresh catalog daily → Run workflow** to trigger immediately.

## Local testing

```bash
export BOL_CLIENT_ID=...
export BOL_CLIENT_SECRET=...
python3 scripts/build_catalog.py catalog.json
```

## Required secrets

Set in **Settings → Secrets and variables → Actions**:

- `BOL_CLIENT_ID`     — bol.com Open API client ID
- `BOL_CLIENT_SECRET` — bol.com Open API client secret

Create credentials at: https://login.bol.com → Account → Open API → Toevoegen
