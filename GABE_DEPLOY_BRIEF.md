# Gabe Deploy Brief

This is the shortest practical handoff for getting `TaskTrack` running on the office Linux VM.

## Goal

Deploy an internal workflow tracker for BRCE staff with:

- authenticated dashboard
- isolated manager workflow pages
- public/internal submission forms
- Telegram capture bot
- future path to AD-backed auth and role-based access

This should start simple and be easy to expand later.

## Current Source Options

GitHub:

- `git@github.com:Rtoony/tasktrack.git`

Safe Share bundle:

- `tasktrack-office-bundle-20260406.tgz`

## What The App Is

Flask + SQLite + Gunicorn app with these workflows:

- Project Work
- CAD Development
- Training
- Capability Tracking
- Suggestion Box

Extra pieces:

- admin panel
- isolated workflow pages
- submission forms
- Telegram bot service

## Target Host Assumption

Linux VM on local office network.

Preferred future auth target:

- Windows / Active Directory credentials
- expected login style: `BRCE\\username`

If AD auth is not ready immediately:

- deploy app first
- keep app-local login for Josh only
- keep submission forms open by direct link
- add AD auth second

## Day One Permission Intent

- `admin`
  Josh only
  full CRUD and admin control

- future `manager_editor`
  view all workflow pages
  create and edit
  no delete

- future `manager_viewer`
  full read-only access to workflow pages and details

- future `worker`
  assigned-task-only view
  comments/status/close/send-back actions

## Required Runtime

- Python 3
- Flask
- Gunicorn
- requests
- git
- systemd user services

For Telegram bot:

- `bw`
- `jq`
- Vaultwarden access to item `BR_TRACK_BOT`
- required field: `TELEGRAM_BOT_TOKEN`

## Files To Install

Project:

- `app.py`
- `templates/`
- `telegram_bot.py`
- `telegram_bot_launcher.sh`
- `README.md`
- `OFFICE_DEPLOY.md`
- `ops/systemd/`

Data:

- `tracker.db` if current live data should be preserved

## Suggested Install Path

```text
/home/<vm-user>/projects/collab-tracker
/home/<vm-user>/.config/systemd/user/collab-tracker.service
/home/<vm-user>/.config/systemd/user/tasktrack-telegram-bot.service
```

## Fast Deploy Steps

```bash
mkdir -p ~/projects
cd ~/projects
git clone git@github.com:Rtoony/tasktrack.git collab-tracker
cd collab-tracker
```

Install dependencies if needed:

```bash
python3 -m pip install flask gunicorn requests
```

Install user services:

```bash
mkdir -p ~/.config/systemd/user
cp ops/systemd/collab-tracker.service ~/.config/systemd/user/
cp ops/systemd/tasktrack-telegram-bot.service ~/.config/systemd/user/
```

Update hardcoded home paths in the service files if the VM username is not `rtoony`.

If migrating live data:

- copy `tracker.db` into the project root

Start services:

```bash
systemctl --user daemon-reload
systemctl --user enable --now collab-tracker.service
systemctl --user enable --now tasktrack-telegram-bot.service
```

Verify:

```bash
curl http://127.0.0.1:5050/healthz
systemctl --user status collab-tracker.service --no-pager
systemctl --user status tasktrack-telegram-bot.service --no-pager
```

## Open Questions For Office Setup

These are the only infrastructure questions that still matter:

1. Can the Linux VM authenticate against BRCE Active Directory?
2. If yes, what is the preferred method: LDAP, SSSD, or other standard AD integration?
3. What internal hostname or DNS name should TaskTrack use?
4. Where should backups live?
5. Does the VM have access to Vaultwarden and `bw` for Telegram bot startup?

## Backup Recommendation

Start with a simple nightly backup:

- `tracker.db`
- project folder
- systemd service files

Keep at least:

- 7 daily backups

Preferred:

- second storage location outside the VM

## Telegram Bot

Bot in use:

- `@BR_Task_Admin_Bot`

Current behavior:

- pairing code shown in admin page
- linked chats only
- guided entry for all workflows
- quick CAD capture
- quick suggestion capture

## Recommended Rollout Order

1. Deploy app on VM.
2. Verify Josh admin access.
3. Use submission forms for broad intake.
4. Add manager roles later.
5. Add AD auth once infra path is confirmed.
6. Add assignments / My Work after that.

## Bottom Line

The cleanest first office launch is:

- app runs on Linux VM
- Josh is sole admin
- submission links are available broadly
- AD auth and role refinement come next, not first
