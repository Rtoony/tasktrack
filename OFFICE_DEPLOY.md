# Office Deploy

Use this when moving TaskTrack to the office Linux VM.

## Fastest Options

Option 1: clone from GitHub

```bash
git clone git@github.com:Rtoony/tasktrack.git
cd tasktrack
```

Option 2: unpack the Safe Share bundle

```bash
tar -xzf tasktrack-office-bundle-20260406.tgz
cd projects/collab-tracker
```

## Required Files

Project:

- `app.py`
- `templates/`
- `telegram_bot.py`
- `telegram_bot_launcher.sh`
- `README.md`
- `OFFICE_DEPLOY.md`
- `ops/systemd/`

Local data:

- `tracker.db`

User services:

- `collab-tracker.service`
- `tasktrack-telegram-bot.service`

## Dependencies

The target VM needs:

- Python 3
- Flask
- Gunicorn
- requests
- git
- `bw`
- `jq`
- systemd user services

If using the Telegram bot, the VM also needs Vaultwarden access for:

- `BR_TRACK_BOT`
  Required field: `TELEGRAM_BOT_TOKEN`

## Recommended Layout

```text
/home/<you>/projects/collab-tracker
/home/<you>/.config/systemd/user/collab-tracker.service
/home/<you>/.config/systemd/user/tasktrack-telegram-bot.service
```

## Deploy Steps

1. Put the repo in place.

2. Restore or copy `tracker.db` if you want the current live data.

3. Copy the systemd units:

```bash
mkdir -p ~/.config/systemd/user
cp ops/systemd/collab-tracker.service ~/.config/systemd/user/
cp ops/systemd/tasktrack-telegram-bot.service ~/.config/systemd/user/
```

4. Update any hardcoded home paths in the service files if the username/path differs.

5. Reload and start services:

```bash
systemctl --user daemon-reload
systemctl --user enable --now collab-tracker.service
systemctl --user enable --now tasktrack-telegram-bot.service
```

6. Verify:

```bash
curl http://127.0.0.1:5050/healthz
systemctl --user status collab-tracker.service --no-pager
systemctl --user status tasktrack-telegram-bot.service --no-pager
```

## Optional Transfer Commands

If copying directly from another Linux machine:

```bash
rsync -avz /home/rtoony/projects/collab-tracker/ user@office-vm:/home/user/projects/collab-tracker/
rsync -avz /home/rtoony/.config/systemd/user/collab-tracker.service user@office-vm:/home/user/.config/systemd/user/
rsync -avz /home/rtoony/.config/systemd/user/tasktrack-telegram-bot.service user@office-vm:/home/user/.config/systemd/user/
```

If you only want the database copied later:

```bash
rsync -avz /home/rtoony/projects/collab-tracker/tracker.db user@office-vm:/home/user/projects/collab-tracker/
```

## Notes

- `tracker.db` is ignored in git on purpose.
- The Telegram bot launcher reads its token directly from Vaultwarden at startup.
- Safe Share was repaired and can be used as a transport surface for this deployment bundle.
- The next architectural step after deployment is assignment fields plus restricted `My Work` views.
