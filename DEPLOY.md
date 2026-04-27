# BR Task Tracker — Deployment Runbook

This is the install/upgrade guide for **the company VM** (a Linux server,
typically running on Hyper-V on a Windows Server host, accessible to
employees over the LAN).

The Nexus / personal install is documented in `SERVICE_DESK_RESTRUCTURE.md`.

---

## What gets installed where

| Path | Purpose |
|---|---|
| `/opt/tasktrack/` | Application code + `.venv/` virtualenv |
| `/var/lib/tasktrack/tracker.db` | SQLite database (single file) |
| `/etc/tasktrack/tasktrack.env` | Environment / configuration |
| `/etc/systemd/system/tasktrack.service` | systemd unit |
| `/var/log/tasktrack/` | Reserved for log files (currently logs go to journalctl) |
| Linux user `patheal` | Owns the data directory + runs the service |

The install is **idempotent** — re-running `scripts/install.sh` on top
of an existing install picks up new code without touching the DB or the
generated tokens.

---

## First-time install (fresh VM)

### Prerequisites

- A Linux VM with one of:
  - Ubuntu Server 22.04 / 24.04 LTS (preferred — tested)
  - Debian 12+
  - Rocky Linux / AlmaLinux 9 (apt or dnf — script handles both)
- Sudo access for the operator running the install
- Network reachable from the Windows Server host
- `git` (the script will install it if missing on apt-based systems)

The VM does NOT need:
- A reverse proxy (gunicorn binds directly to `0.0.0.0:5050` in the
  default company env)
- HTTPS in the prototype phase (set `SESSION_COOKIE_SECURE=false` in
  the env file — already the default in the template)
- A specific Python version pre-installed (the script installs
  `python3` + `python3-venv` from the distro repo)

### Steps

```bash
# 1. Fetch the code (run as your sudo user, NOT as root).
cd ~
git clone https://github.com/Rtoony/BR_Tasktrack.git
cd BR_Tasktrack

# 2. Run the installer.
sudo ./scripts/install.sh

# 3. Bootstrap the first admin user.
cd /opt/tasktrack
sudo -u patheal env DB_PATH=/var/lib/tasktrack/tracker.db \
    ./.venv/bin/python -m flask --app wsgi create-admin \
        --email patheal@brengineering.com \
        --name "Josh Patheal"
# (You'll be prompted for an initial password.)

# 4. Verify.
curl http://127.0.0.1:5050/healthz                # should print "ok"
systemctl status tasktrack.service                # should be active

# 5. From a workstation on the LAN, open:
#    http://<vm-ip>:5050/login
```

That's the whole install. The login form should accept the email and
password you just set.

### What `scripts/install.sh` does, step by step

1. Installs distro packages: `python3 python3-venv python3-pip git sqlite3 curl`.
2. Creates the `patheal` system user if missing.
3. Creates `/opt/tasktrack`, `/var/lib/tasktrack`, `/etc/tasktrack`, `/var/log/tasktrack`.
4. `rsync`s the repo source into `/opt/tasktrack` (excluding `.git`, `.venv`, the dev `tracker.db`, etc.).
5. Builds `/opt/tasktrack/.venv` and installs `requirements.txt`.
6. **First install only**: copies `deploy/tasktrack.env.template` to
   `/etc/tasktrack/tasktrack.env`, generates fresh
   `TASKTRACK_TOKEN_TRIAGE` / `_PERSONAL` / `_BOT` values, sets the file
   to `root:patheal 0640`. Re-installs leave this file untouched so
   tokens survive upgrades.
7. Creates an empty `/var/lib/tasktrack/tracker.db` (SQLite file owned
   by `patheal`) and runs `alembic upgrade head` to build the schema.
8. Generates `/etc/systemd/system/tasktrack.service` from the template
   (substituting the user + paths), `daemon-reload`, `enable`, and
   `restart`.

If anything goes wrong, re-run with `--no-restart` to inspect logs
before flipping the service:

```bash
sudo ./scripts/install.sh --no-restart
sudo systemctl start tasktrack.service
sudo journalctl -u tasktrack.service -n 50
```

---

## Day-to-day operations

### Restart after a config change

```bash
sudo systemctl restart tasktrack.service
sudo systemctl status tasktrack.service
```

### View live logs

```bash
sudo journalctl -u tasktrack.service -f
```

Logs are JSON-structured in the company profile — pipe to `jq` for
human-readable output:

```bash
sudo journalctl -u tasktrack.service -f -o cat | jq -r '"\(.ts) \(.level) \(.msg)"'
```

### Upgrade the deployed code

```bash
cd ~/BR_Tasktrack
git pull
sudo ./scripts/install.sh
# install.sh handles: rsync new code, pip install, alembic upgrade,
# daemon-reload, systemctl restart.
```

### Add another admin / approved email

```bash
cd /opt/tasktrack
sudo -u patheal env DB_PATH=/var/lib/tasktrack/tracker.db \
    ./.venv/bin/python -m flask --app wsgi create-admin \
        --email newperson@brengineering.com \
        --name "New Person"
```

Or for non-admins: log in as an existing admin, go to `/admin`, and
add the email under "Approved Emails". The user can then self-register
at `/register`.

### Reset a forgotten password

```bash
# Same create-admin command — it's idempotent and will reset the
# password for an existing user.
sudo -u patheal env DB_PATH=/var/lib/tasktrack/tracker.db \
    ./.venv/bin/python -m flask --app wsgi create-admin \
        --email theiremail@brengineering.com \
        --name "Their Name"
```

### Change configuration

Edit `/etc/tasktrack/tasktrack.env`, then restart:

```bash
sudo nano /etc/tasktrack/tasktrack.env
sudo systemctl restart tasktrack.service
```

Common edits:

- `BIND_HOST=127.0.0.1` if you put nginx/caddy in front later.
- `SESSION_COOKIE_SECURE=true` once you're on HTTPS.
- `INTAKE_FORM_AUTH=none` to allow anonymous intake-form submissions
  (default is `required` in the company profile — login needed).
- `BRAND_NAME=...` if the firm wants a different display name.

### Backup

The whole production state is one file:

```bash
sudo cp /var/lib/tasktrack/tracker.db /backup/tracker.db.$(date +%Y%m%d-%H%M%S)
```

For a hot copy that's safe while the service is running, use SQLite's
online backup API instead:

```bash
sudo -u patheal sqlite3 /var/lib/tasktrack/tracker.db \
    ".backup /backup/tracker.db.$(date +%Y%m%d-%H%M%S)"
```

Set up a nightly cron or systemd timer for automatic backups; the file
is small (single-digit MBs) so retention is cheap.

---

## Troubleshooting

### `curl http://127.0.0.1:5050/healthz` connection refused

```bash
sudo systemctl status tasktrack.service
sudo journalctl -u tasktrack.service -n 100 --no-pager
```

If the unit is `failed`, the journal will show why (most common: the
port is already in use; the env file has a typo; the .venv is missing).

### Login page loads but login keeps redirecting

Almost always **`SESSION_COOKIE_SECURE=true` + plain HTTP**. The cookie
won't set, so every login looks like a redirect loop. Edit
`/etc/tasktrack/tasktrack.env`:

```
SESSION_COOKIE_SECURE=false
```

…then `sudo systemctl restart tasktrack.service`.

### `flask create-admin` says "no such table: users"

The DB hasn't been migrated yet. Run:

```bash
cd /opt/tasktrack
sudo -u patheal env DB_PATH=/var/lib/tasktrack/tracker.db \
    ./.venv/bin/python -m flask --app wsgi db-upgrade
```

### Employees can't reach the service from their workstations

Walk the path:

1. **VM firewall** — `sudo ufw status` (Ubuntu) or `sudo firewall-cmd --list-all` (Rocky). If active, allow port 5050.
2. **Hyper-V switch** — confirm the VM is on an external/bridged switch (not internal-only).
3. **Windows Server firewall on the host** — allow inbound on 5050 to the VM IP.
4. **Hostname / IP** — employees may need to use the VM's IP if there's no internal DNS.

### Where's the data?

`/var/lib/tasktrack/tracker.db`. To inspect:

```bash
sudo sqlite3 /var/lib/tasktrack/tracker.db
sqlite> .tables
sqlite> SELECT id, email, role FROM users;
```

---

## Uninstall (clean removal)

```bash
sudo systemctl disable --now tasktrack.service
sudo rm /etc/systemd/system/tasktrack.service
sudo systemctl daemon-reload
sudo rm -rf /opt/tasktrack /var/log/tasktrack
sudo rm -rf /etc/tasktrack       # WARNING: removes generated tokens
sudo rm -rf /var/lib/tasktrack   # WARNING: removes the DB — back it up first
# Optional: remove the user (only if nothing else uses it).
sudo userdel -r patheal
```

---

## Known gaps for the prototype

These are intentional limitations for the initial company-VM rollout
(per the locked decisions in `SERVICE_DESK_RESTRUCTURE.md`):

- **Plain HTTP over the LAN.** Security is at the network layer
  (Windows Server access control). When you're ready for HTTPS, put
  nginx or caddy in front, change `BIND_HOST=127.0.0.1`, and flip
  `SESSION_COOKIE_SECURE=true`.
- **AI Intake disabled.** Phase 2/8 work, not in the initial release.
- **Calendar widget disabled.** Will return as an Outlook integration
  in Phase 8.
- **Telegram bot not deployed.** The `/api/v1/telegram/*` endpoints
  exist but no client calls them. The `tasktrack-telegram-bot.service`
  unit is NOT installed by `scripts/install.sh`.
- **Maximus API disabled.** The `/api/v1/maximus/*` endpoints don't
  register at all in the company profile (404 instead of 503).
- **Single-DB SQLite.** Postgres migration is Phase 2; SQLite is fine
  for a single firm at this scale.
- **No automated backups.** Set up a nightly cron + offsite copy.
- **No HTTPS termination.** See above.

The rest of the planned work is in `SERVICE_DESK_RESTRUCTURE.md`.
