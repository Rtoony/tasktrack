# TaskTrack

Internal task and workflow tracker for:

- `Project Work`
- `CAD Development`
- `Training`
- `Capability Tracking`
- `Suggestion Box`

It also includes:

- auth-gated dashboard
- admin user/email management
- isolated admin workflow pages
- public submission forms
- Telegram bot capture via `@BR_Task_Admin_Bot`

## Main Files

- `app.py`
  Flask app, SQLite schema, routes, admin APIs, submission forms
- `templates/index.html`
  Main dashboard UI and standalone workflow views
- `templates/admin.html`
  Admin UI, workflow shortcuts, Telegram pairing controls
- `templates/weekly_submit.html`
  Multi-row weekly `Project Work` submission form
- `templates/simple_submit.html`
  Shared simple form template for other submission pages
- `templates/submit_hub.html`
  Public submission-form hub
- `telegram_bot.py`
  Telegram bot worker for mobile task capture
- `telegram_bot_launcher.sh`
  Pulls bot token from Vaultwarden item `BR_TRACK_BOT` and starts the bot
- `tracker.db`
  SQLite database

## Runtime

Current app service:

- user systemd unit: `~/.config/systemd/user/collab-tracker.service`
- bind: `0.0.0.0:5050`

Current Telegram bot service:

- user systemd unit: `~/.config/systemd/user/tasktrack-telegram-bot.service`

## Current Routes

Dashboard and admin:

- `/`
- `/admin`
- `/admin/workflow/project`
- `/admin/workflow/work`
- `/admin/workflow/training`
- `/admin/workflow/personnel`
- `/admin/workflow/suggestions`

Submission forms:

- `/submit`
- `/submit/project-work`
- `/submit/cad-development`
- `/submit/training`
- `/submit/capability`
- `/submit/suggestion-box`

## Telegram Bot

Bot username:

- `@BR_Task_Admin_Bot`

How it works:

- admin page shows a pairing code
- send `/link CODE` to the bot
- linked chats can create tasks
- supported modes:
  - guided `New Task`
  - `Quick CAD`
  - `Quick Suggestion`

## Secrets

Do not create `.env` files.

Current bot startup behavior:

- `telegram_bot_launcher.sh` reads the token from Vaultwarden item `BR_TRACK_BOT`
- expected custom field name inside that vault item:
  - `TELEGRAM_BOT_TOKEN`

The web app currently relies on the existing local environment and system setup.

## Migration Notes

For a same-day move to another Linux VM, the minimum copy set is:

- this whole folder: `projects/collab-tracker`
- user service files:
  - `~/.config/systemd/user/collab-tracker.service`
  - `~/.config/systemd/user/tasktrack-telegram-bot.service`

On the target machine:

1. Copy the project folder.
2. Ensure Python, Flask, Gunicorn, and `requests` are available.
3. Ensure `bw`, `jq`, and Vaultwarden session access are available if Telegram bot is needed.
4. Reload systemd:
   - `systemctl --user daemon-reload`
5. Enable/start services:
   - `systemctl --user enable --now collab-tracker.service`
   - `systemctl --user enable --now tasktrack-telegram-bot.service`
6. Verify:
   - `curl http://127.0.0.1:5050/healthz`
   - `systemctl --user status collab-tracker.service`
   - `systemctl --user status tasktrack-telegram-bot.service`

## Recommendation For Office Codex

Yes, put the project where the office Codex can read it directly.

Best practical options:

1. Copy the project folder to the office VM.
2. Keep this `README.md` with it.
3. Optionally initialize a git repo or push to GitHub later.

GitHub is helpful, but not required for today.

If you need this live quickly, the priority order is:

1. copy project
2. copy service files
3. verify dependencies
4. start services
5. test login, forms, and Telegram bot

## Recommendation About Git

You are not overthinking it, but GitHub is optional for the immediate move.

Short term:

- local copy or `rsync` is enough

Medium term:

- put this in git
- keep a remote backup
- use that as the source of truth for future Codex sessions and VM rebuilds

## Recommended Next Structural Step

If this grows further, add:

- `assigned_to_user_id`
- `assigned_to_name`
- restricted `My Work` views for subusers

That is the clean next step toward a simple internal service-desk model.
