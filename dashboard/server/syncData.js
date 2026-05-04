// Downloads the latest data assets from GitHub Releases if the local copy is
// missing or older than the latest release. Runs once at server startup.

import fs from 'fs';
import path from 'path';
import https from 'https';
import { execSync } from 'child_process';
import { DB_PATH, CONFIG_PATH, REGISTRY_PATH, FEEDBACK_PATH, SNAPSHOTS_PATH } from './db.js';

const GITHUB_REPO = process.env.GITHUB_REPO;
const GITHUB_TOKEN = process.env.GITHUB_TOKEN;

const ASSETS = [
  { localPath: DB_PATH,       assetName: 'ipfr.sqlite'          },
  { localPath: CONFIG_PATH,   assetName: 'tripwire_config.yaml' },
  { localPath: REGISTRY_PATH, assetName: 'source_registry.csv'  },
  { localPath: FEEDBACK_PATH, assetName: 'feedback.jsonl'       },
];

function httpsGet(url, headers = {}) {
  return new Promise((resolve, reject) => {
    const opts = {
      headers: {
        'User-Agent': 'tripwire-dashboard',
        'Accept': 'application/vnd.github+json',
        ...headers,
      },
    };
    https.get(url, opts, res => {
      if (res.statusCode === 301 || res.statusCode === 302) {
        return resolve(httpsGet(res.headers.location, headers));
      }
      let body = '';
      res.on('data', chunk => body += chunk);
      res.on('end', () => resolve({ statusCode: res.statusCode, body, headers: res.headers }));
    }).on('error', reject);
  });
}

function downloadToFile(url, destPath, headers = {}) {
  return new Promise((resolve, reject) => {
    const opts = {
      headers: {
        'User-Agent': 'tripwire-dashboard',
        'Accept': 'application/octet-stream',
        ...headers,
      },
    };
    const follow = targetUrl => {
      https.get(targetUrl, opts, res => {
        if (res.statusCode === 301 || res.statusCode === 302) {
          return follow(res.headers.location);
        }
        if (res.statusCode !== 200) {
          return reject(new Error(`HTTP ${res.statusCode} downloading ${targetUrl}`));
        }
        fs.mkdirSync(path.dirname(destPath), { recursive: true });
        const tmp = destPath + '.tmp';
        const out = fs.createWriteStream(tmp);
        res.pipe(out);
        out.on('finish', () => { fs.renameSync(tmp, destPath); resolve(); });
        out.on('error', reject);
      }).on('error', reject);
    };
    follow(url);
  });
}

async function getLatestRelease() {
  const authHeader = GITHUB_TOKEN ? { Authorization: `Bearer ${GITHUB_TOKEN}` } : {};
  const url = `https://api.github.com/repos/${GITHUB_REPO}/releases/latest`;
  const { statusCode, body } = await httpsGet(url, authHeader);
  if (statusCode !== 200) {
    console.warn(`[sync] Could not fetch latest release (HTTP ${statusCode})`);
    return null;
  }
  return JSON.parse(body);
}

function releaseIsNewer(release, localPath) {
  try {
    const localMtime = fs.statSync(localPath).mtimeMs;
    return new Date(release.published_at).getTime() > localMtime;
  } catch {
    return true; // file absent — always download
  }
}

export async function syncDataFromRelease() {
  if (!GITHUB_REPO) {
    console.log('[sync] GITHUB_REPO not set — skipping data sync');
    return;
  }

  console.log('[sync] Checking for updated data in latest GitHub release...');
  let release;
  try {
    release = await getLatestRelease();
  } catch (err) {
    console.warn(`[sync] Failed to reach GitHub API: ${err.message}`);
    return;
  }
  if (!release) return;

  console.log(`[sync] Latest release: ${release.tag_name} (${release.published_at})`);

  fs.mkdirSync(SNAPSHOTS_PATH, { recursive: true });

  const authHeader = GITHUB_TOKEN ? { Authorization: `Bearer ${GITHUB_TOKEN}` } : {};

  for (const { localPath, assetName } of ASSETS) {
    const asset = release.assets?.find(a => a.name === assetName);
    if (!asset) {
      console.log(`[sync] No asset '${assetName}' in release — skipping`);
      continue;
    }
    if (!releaseIsNewer(release, localPath)) {
      console.log(`[sync] ${assetName} is up to date — skipping`);
      continue;
    }
    console.log(`[sync] Downloading ${assetName} → ${localPath}`);
    try {
      await downloadToFile(asset.url, localPath, {
        ...authHeader,
        Accept: 'application/octet-stream',
      });
      console.log(`[sync] ${assetName} downloaded OK`);
    } catch (err) {
      console.error(`[sync] Failed to download ${assetName}: ${err.message}`);
    }
  }

  // Snapshots tarball — download and extract into SNAPSHOTS_PATH.
  // A sentinel file records the last-synced release tag to avoid
  // re-extracting on every startup.
  const SNAPSHOTS_ASSET = 'snapshots.tar.gz';
  const snapshotAsset = release.assets?.find(a => a.name === SNAPSHOTS_ASSET);
  if (!snapshotAsset) {
    console.log(`[sync] No asset '${SNAPSHOTS_ASSET}' in release — skipping snapshot sync`);
  } else {
    const sentinelPath = path.join(SNAPSHOTS_PATH, '.last_sync_tag');
    let currentTag = null;
    try { currentTag = fs.readFileSync(sentinelPath, 'utf8').trim(); } catch { /* absent */ }

    if (currentTag === release.tag_name) {
      console.log(`[sync] ${SNAPSHOTS_ASSET} is up to date (${release.tag_name}) — skipping`);
    } else {
      const tmpTar = path.join(SNAPSHOTS_PATH, '..', 'snapshots_sync.tar.gz.tmp');
      console.log(`[sync] Downloading ${SNAPSHOTS_ASSET}…`);
      try {
        await downloadToFile(snapshotAsset.url, tmpTar, {
          ...authHeader,
          Accept: 'application/octet-stream',
        });
        // Extract: the tarball contains a `snapshots/` directory; extract its
        // contents directly into SNAPSHOTS_PATH using --strip-components=1.
        fs.mkdirSync(SNAPSHOTS_PATH, { recursive: true });
        execSync(
          `tar xzf "${tmpTar}" --strip-components=1 -C "${SNAPSHOTS_PATH}"`,
          { stdio: 'pipe' },
        );
        fs.unlinkSync(tmpTar);
        fs.writeFileSync(sentinelPath, release.tag_name, 'utf8');
        console.log(`[sync] ${SNAPSHOTS_ASSET} extracted OK (${release.tag_name})`);
      } catch (err) {
        console.error(`[sync] Failed to sync snapshots: ${err.message}`);
        try { fs.unlinkSync(tmpTar); } catch { /* ignore */ }
      }
    }
  }
}
