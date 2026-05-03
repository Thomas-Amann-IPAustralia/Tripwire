# Tripwire Dashboard — Deployment Runbook

Tripwire Dashboard is a Node/Express + React (Vite) application. The Express server
reads `ipfr.sqlite` and related data files from a persistent disk, serves the
compiled React app, and exposes REST API endpoints used by the frontend.

---

## Contents

1. [Prerequisites](#1-prerequisites)
2. [Render Deployment (§12.2)](#2-render-deployment)
3. [GitHub Actions Data Sync (§12.3)](#3-github-actions-data-sync)
4. [Environment Variable Reference (§12.5)](#4-environment-variable-reference)
5. [Vite / API URL Configuration (§12.4)](#5-vite--api-url-configuration)
6. [First-Run Checklist](#6-first-run-checklist)

---

## 1. Prerequisites

- Node 20+ installed locally (for testing builds).
- The Tripwire pipeline runs and commits updated data to the GitHub repository
  (`data/ipfr_corpus/ipfr.sqlite`, `data/influencer_sources/`, etc.).
- A Render account (free tier works; see cold-start note in §2).

---

## 2. Render Deployment

These steps follow §12.2 of the system plan.

### 2.1 Create the Web Service

1. Push the repository to GitHub if it is not already there.
2. Log in to the [Render dashboard](https://dashboard.render.com) and click
   **New → Web Service**.
3. Connect to the GitHub repository (`Thomas-Amann-IPAustralia/Tripwire` or
   your fork).
4. Set the following fields:

   | Field | Value |
   |---|---|
   | **Name** | `tripwire-dashboard` |
   | **Environment** | `Node` |
   | **Build Command** | `cd dashboard && npm install && npm run build` |
   | **Start Command** | `cd dashboard && npm start` |
   | **Branch** | `main` (or whichever branch holds production code) |

5. Under **Advanced → Add Disk**, create a persistent disk:

   | Field | Value |
   |---|---|
   | **Name** | `tripwire-data` |
   | **Mount Path** | `/data` |
   | **Size** | 1 GB |

6. Add the environment variables listed in [§4](#4-environment-variable-reference).
   At minimum:

   ```
   DASHBOARD_USER=<choose a username>
   DASHBOARD_PASS=<choose a strong password>
   DATA_ROOT=/data
   NODE_ENV=production
   ```

   Leave `DASHBOARD_ORIGIN` blank for the initial deploy; you will set it
   after the service URL is assigned (see step 8).

7. Click **Create Web Service** and wait for the first deploy to complete.

8. Copy the assigned URL (e.g., `https://tripwire-dashboard.onrender.com`).
   Go to the service's **Environment** tab and add:

   ```
   DASHBOARD_ORIGIN=https://tripwire-dashboard.onrender.com
   ```

   Save and trigger a manual redeploy.

9. Share the URL and Basic Auth credentials with team members.

### 2.2 Render Free Tier — Cold Start Note

The free tier spins the service down after 15 minutes of inactivity. The
first request after a spin-down takes approximately 30 seconds (cold start).
This is acceptable for a small internal team. To eliminate cold starts, upgrade
to **Render Starter** ($7/month) which keeps the service always on.

---

## 3. GitHub Actions Data Sync

After each Tripwire pipeline run, GitHub Actions syncs the updated SQLite
database and related data files to the Render persistent disk via `rsync` over
SSH. This step keeps the dashboard data fresh without requiring a full
redeploy.

### 3.1 SSH Key Setup

1. Generate a dedicated key pair (do not use a key with passphrase):

   ```bash
   ssh-keygen -t ed25519 -C "tripwire-render-sync" -f ~/.ssh/render_sync_key -N ""
   ```

2. Add the **public key** (`render_sync_key.pub`) to the Render service's SSH
   access (via Render's **SSH** tab on the service, or via `~/.ssh/authorized_keys`
   on a Render shell session).

3. Add the following **GitHub Actions secrets** to the repository
   (`Settings → Secrets and variables → Actions`):

   | Secret | Value |
   |---|---|
   | `RENDER_SSH_KEY` | Full contents of `render_sync_key` (private key) |
   | `RENDER_SSH_HOST` | Render SSH host for the service (shown on Render → SSH tab) |
   | `RENDER_SSH_USER` | Render SSH user (shown on Render → SSH tab) |

### 3.2 GitHub Actions Step

Append the following step to `.github/workflows/tripwire.yml` **after** the
existing pipeline run step. Do not modify the file directly — add it when you
are ready to enable sync.

```yaml
# Append to .github/workflows/tripwire.yml — after the main pipeline step
- name: Sync data to Render persistent disk
  env:
    RENDER_SSH_KEY:  ${{ secrets.RENDER_SSH_KEY }}
    RENDER_SSH_HOST: ${{ secrets.RENDER_SSH_HOST }}
    RENDER_SSH_USER: ${{ secrets.RENDER_SSH_USER }}
  run: |
    mkdir -p ~/.ssh
    echo "$RENDER_SSH_KEY" > ~/.ssh/render_key
    chmod 600 ~/.ssh/render_key
    rsync -avz --delete \
      -e "ssh -i ~/.ssh/render_key -o StrictHostKeyChecking=no" \
      data/ipfr_corpus/ipfr.sqlite \
      data/influencer_sources/ \
      data/logs/ \
      tripwire_config.yaml \
      ${RENDER_SSH_USER}@${RENDER_SSH_HOST}:/data/
```

### 3.3 Alternative Sync Approach (No SSH Key Management)

If configuring SSH access to the Render disk proves difficult, use Render's
[Deploy Hooks](https://render.com/docs/deploy-hooks) combined with uploading
the database as a GitHub release asset:

1. On each pipeline run, upload `ipfr.sqlite` as a GitHub release asset.
2. Configure the Express server to download the latest release asset on
   startup if the local copy is stale (compare `Last-Modified` header against
   the file's mtime).
3. Trigger a Render redeploy via the deploy hook URL.

This trades SSH key management complexity for a slightly longer cold start
(the server downloads the database file on first boot). Implement whichever
approach the team finds easier to maintain.

---

## 4. Environment Variable Reference

All variables are set on the Render service's **Environment** tab (or locally
in a `.env` file that is **not** committed).

| Variable | Required in | Default | Purpose |
|---|---|---|---|
| `DASHBOARD_USER` | Production | — | Basic Auth username presented to all visitors |
| `DASHBOARD_PASS` | Production | — | Basic Auth password |
| `DASHBOARD_ORIGIN` | Production | — | CORS allowed origin — set to the full Render URL (e.g., `https://tripwire-dashboard.onrender.com`) |
| `DATA_ROOT` | Production | `../../` (relative to `dashboard/server/`) | Absolute path to the data directory on the persistent disk; set to `/data` on Render |
| `NODE_ENV` | Production | `development` | When `production`: enables Basic Auth, locks CORS to `DASHBOARD_ORIGIN`, disables stack traces in error responses |
| `PORT` | Both | `3001` | TCP port Express listens on; Render sets this automatically — do not override it manually on Render |

### Local Development

For local runs, create `dashboard/.env` (not committed):

```
DATA_ROOT=../   # one level up from dashboard/; points to repo root data/
NODE_ENV=development
PORT=3001
```

The Vite dev server proxies `/api/*` to `http://localhost:3001` automatically
(configured in `dashboard/vite.config.js`).

---

## 5. Vite / API URL Configuration

In production the React app is served by Express from the same origin, so all
API calls use relative paths (`/api/runs`, not `http://localhost:3001/api/runs`).

`dashboard/vite.config.js` configures the dev proxy:

```js
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:3001'   // dev only — proxies to Express
    }
  },
  base: '/',
});
```

`dashboard/src/hooks/useData.js` uses `API_BASE = ''` (empty string) so all
fetches are relative and work in both dev (via Vite proxy) and production
(same-origin Express).

---

## 6. First-Run Checklist

- [ ] `DASHBOARD_USER` and `DASHBOARD_PASS` set on Render
- [ ] `DATA_ROOT=/data` set on Render
- [ ] `NODE_ENV=production` set on Render
- [ ] Service deployed and accessible at the assigned URL
- [ ] `DASHBOARD_ORIGIN` set to the assigned URL, service redeployed
- [ ] Initial data synced to `/data` (either via SSH rsync or manual upload of `ipfr.sqlite`)
- [ ] Dashboard loads and displays data
- [ ] GitHub Actions SSH secrets configured (`RENDER_SSH_KEY`, `RENDER_SSH_HOST`, `RENDER_SSH_USER`)
- [ ] Data sync step appended to `tripwire.yml` and tested on a `workflow_dispatch` run
