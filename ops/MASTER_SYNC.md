# TaskTrack Master-List Auto-Sync

Daily systemd-driven sync of the firm's Master Project List (XLSX) +
Project Locator (KMZ) into TaskTrack's `projects` + `project_sites`
tables. Built as a **prototype** running off a USB drive on the
operator's private AI PC; designed so the eventual deployment to a
firm-owned VM with NAS access is a one-env-var change.

## Pieces

| File | Role |
|---|---|
| `scripts/sync_master_if_changed.py` | Cron entrypoint. sha256 change detection + state file management. |
| `scripts/import_projects_from_master.py` | The actual XLSX/KMZ → DB importer. Returns a structured report via `--report-json`. |
| `scripts/notify_master_sync.py` | Reads the report on stdin, formats a Telegram digest, posts to the operator's chat. |
| `ops/systemd/tasktrack-master-sync.service` | Oneshot user unit; injects vault secrets via `nexus-svc-inject`. |
| `ops/systemd/tasktrack-master-sync.timer` | Daily at 03:30 local, ±5 min jitter. |
| `app/routes/registry.py` `/api/v1/projects/sync-status` | Surface for the admin badge. |
| `~/.local/state/tasktrack/master-sync.json` | Persistent state — last-run hashes + compact report. Lives outside the repo. |

## Data contract

| Source | Owner | Authority |
|---|---|---|
| `Master List - Numeric MMDDYY.xlsx` | Office admin | **Wins** on every field except `notes`, `lat/lng` (mirrored from KMZ primary site), and `project_sites` (KMZ-driven). |
| `Project Locator.kmz` | CAD lead | Sole source for pin geometry + per-site pin color. |
| TaskTrack admin UI | Operator | Owns the `notes` column. Anything else they edit will be overwritten on the next sync. |

**Vanished-row rule:** if a project number that was previously
imported disappears from the next master XLSX, the sync sets
`vanished_from_master_at = now()` and flips `display_status` to
`dormant`. The row, its FK references from tickets, and its sites are
preserved. If the project reappears in a later master, the flag
clears automatically.

## Prototype mode (current)

The wrapper defaults to `/media/rtoony/13FB-6205` for sources. To
prototype a sync run:

```
# Drop a state file in a sandbox path so the live state isn't touched
TASKTRACK_MASTER_SOURCE_DIR=/media/rtoony/13FB-6205 \
  /home/rtoony/miniconda3/bin/python3 \
  /home/rtoony/projects/collab-tracker/scripts/sync_master_if_changed.py \
  --state-file /tmp/master-sync-test.json \
  --db sqlite:////tmp/tracker.db.test \
  --dry-run
```

The systemd units are **deliberately not enabled yet**. They live in
`ops/systemd/` for installation post-NAS-cutover (see runbook below).

## Go-live runbook (when NAS is ready)

This is what needs to happen the day the firm-owned NAS share is
available. Estimate: ~30 minutes of plumbing once the share exists.

### 1. NAS share

On Synology DSM:
- Create share `engineering-data` (or whatever the firm wants to call it).
- Create subfolder `master-list/`.
- Grant **read-write** to the office admin's domain account.
- Create a dedicated SMB user `tasktrack-sync` with **read-only** access
  to `engineering-data` (or just to `master-list/` if Synology allows
  that nested grant).
- Confirm the office admin can save the XLSX + KMZ there from their
  Windows machine.

### 2. Vault item

Add a new Vaultwarden item `Nexus - Engineering Data Share` in the
**Cloud Services** group with custom fields:

```
SYNOLOGY_ENG_DATA_USER   tasktrack-sync
SYNOLOGY_ENG_DATA_PASS   <password>
```

Mirror the structure of `Nexus - Backup` so `nexus-svc-inject`'s
existing path works for it.

Add `TASKTRACK_MASTER_SOURCE_DIR` to the existing `Nexus - TaskTrack`
item as a custom field with value
`/mnt/synology-eng-data/master-list`.

### 3. fstab + credentials file

This is the **only** on-disk secret allowed by the zero-disk policy
(same exception as `synology-machine-backups.creds`; see CLAUDE.md).

```
sudo mkdir -p /mnt/synology-eng-data
sudo chown rtoony:rtoony /mnt/synology-eng-data
```

Populate the creds file from the vault:
```
nexus-inject --group "Cloud Services" | \
  awk -F= '/^SYNOLOGY_ENG_DATA_USER/{u=$2} /^SYNOLOGY_ENG_DATA_PASS/{p=$2} \
           END{printf "username=%s\npassword=%s\n",u,p}' \
  > ~/.secrets/synology-engineering-data.creds
chmod 600 ~/.secrets/synology-engineering-data.creds
```

Add to `/etc/fstab` (one line — careful with the trailing zeros):
```
//192.168.87.37/engineering-data /mnt/synology-eng-data cifs ro,noauto,x-systemd.automount,credentials=/home/rtoony/.secrets/synology-engineering-data.creds,uid=rtoony,gid=rtoony,vers=2.0 0 0
```

Verify:
```
ls /mnt/synology-eng-data/master-list/
# should list the latest XLSX + KMZ
```

### 4. Install the systemd units

```
cp ops/systemd/tasktrack-master-sync.service ~/.config/systemd/user/
cp ops/systemd/tasktrack-master-sync.timer  ~/.config/systemd/user/
systemctl --user daemon-reload
```

### 5. Pre-flight: dry-run against the NAS

```
TASKTRACK_MASTER_SOURCE_DIR=/mnt/synology-eng-data/master-list \
  /home/rtoony/miniconda3/bin/python3 \
  /home/rtoony/projects/collab-tracker/scripts/sync_master_if_changed.py \
  --dry-run
```

Expect `WOULD IMPORT (hash differs)` on the first dry-run.

### 6. First real sync

```
systemctl --user start tasktrack-master-sync.service
journalctl --user -u tasktrack-master-sync.service -n 50 --no-pager
```

You should see a Telegram digest land on `@RoonyToonyBot` summarizing
the import outcome.

### 7. Enable the timer

```
systemctl --user enable --now tasktrack-master-sync.timer
systemctl --user list-timers tasktrack-master-sync.timer
```

Confirm next run = tomorrow 03:30 local.

### 8. Manifest update

```
~/scripts/nexus-manifest-update.sh log \
  "TaskTrack master-list auto-sync enabled (daily 03:30, NAS-driven)" \
  --source=claude-code
```

## Manual operations

### One-shot sync now
```
systemctl --user start tasktrack-master-sync.service
```

### Force re-import even if files haven't changed
```
/home/rtoony/miniconda3/bin/python3 \
  /home/rtoony/projects/collab-tracker/scripts/sync_master_if_changed.py \
  --force
```

### Peek at last-run state
```
cat ~/.local/state/tasktrack/master-sync.json
```

Or, from the browser, sign in to TaskTrack and look at the badge next
to "Project list" on `/admin/projects` — same data, prettier.

### Disable automation temporarily

E.g. if the office admin is mid-restructure and you don't want the
sync to fire while the spreadsheet is in flux:
```
systemctl --user stop tasktrack-master-sync.timer
systemctl --user disable tasktrack-master-sync.timer
```

Re-enable with `enable --now` when ready.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `source dir not present (NAS unmounted?)` | Auto-mount didn't fire or NAS is down | `ls /mnt/synology-eng-data` to trigger automount; check Synology |
| `found N XLSX files matching 'Master List - Numeric*.xlsx'` (N > 3) | Office admin left several backup copies | Have them clean up `master-list/` — keep only the current file plus at most two backups |
| Telegram digest missing | `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CLAUDE_CHAT_ID` not in `Nexus - Messaging` | Re-check vault group; sync still succeeded, digest is best-effort |
| Whole tree of projects flipped to dormant | Office admin saved a stripped-down XLSX by mistake | Restore the previous XLSX → next sync clears `vanished_from_master_at` automatically |
| Sync runs but no rows changed | Master XLSX was re-saved without content changes (Excel touches mtime) | sha256 also matched? Then it correctly no-op'd. If sha differs the importer reports `unchanged_count` instead — fine. |
| Admin badge says "never_run" | State file path got stomped or AI PC was reimaged | Run one sync manually; the badge updates from the freshly-written state |

## Out of scope (deferred)

- Bidirectional sync (TaskTrack pushing back to the XLSX)
- Field-level conflict surfacing on the activity log
- Subscription / webhook-based real-time sync
- CAD-side write-back to the Project Locator KMZ
- Move to the firm-owned VM — straightforward once the NAS share is
  reachable from there; only `TASKTRACK_MASTER_SOURCE_DIR` and the
  fstab entry need duplicating.
