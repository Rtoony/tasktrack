# TaskTrack Email Intake

IMAP poller that forwards unread messages to `/api/triage`, creating CAD tasks
flagged `needs_review`. Runs as a systemd user timer every 5 minutes.

## Wiring (one-time setup)

1. **Provision the mailbox.** Point personal + work email forwarding rules at a
   dedicated Nexus mailbox (e.g. `intake@roonytoony.dev` served by Proton Bridge
   on the AI PC, or a plain Gmail with an app password).

2. **Create the vault item.** In Vaultwarden, add
   `Nexus - Intake Mailbox` with these fields:

   ```
   INTAKE_IMAP_HOST      imap.example.com
   INTAKE_IMAP_PORT      993
   INTAKE_IMAP_USER      intake@roonytoony.dev
   INTAKE_IMAP_PASS      <app-password>
   INTAKE_IMAP_FOLDER    INBOX
   INTAKE_IMAP_SSL       1
   ```

   `TASKTRACK_URL` + `TASKTRACK_TOKEN` already live in `Nexus - TaskTrack`.

3. **Enable the timer.**

   ```
   systemctl --user enable --now tasktrack-email-intake.timer
   ```

4. **Verify.** Send a test email to the intake mailbox, wait up to 5 min, and
   check the AI Intake / CAD Development tabs for a new row with the "AI ·
   needs review" badge.

## Manual test run

```
nexus-svc-inject tasktrack-email-intake "TaskTrack" "Intake Mailbox"
set -a; source /dev/shm/nexus-env-tasktrack-email-intake; set +a
/home/rtoony/miniconda3/bin/python3 /home/rtoony/projects/collab-tracker/ops/email_intake.py
```

## Notes

- Messages are marked `\Seen` only after the triage POST succeeds, so transient
  network / LiteLLM outages don't drop mail — the next tick retries.
- Up to 10 messages per tick (`INTAKE_MAX_MESSAGES` override). Large backlogs
  drain over several ticks.
- The poller does **not** currently handle attachments — PDFs / images are
  ignored. Add Gemini vision handling in a later iteration if needed.
