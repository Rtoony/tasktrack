# TaskTrack Email Intake

IMAP poller that forwards unread messages to `/api/triage`, creating CAD tasks
flagged `needs_review`. Runs as a systemd user timer every 5 minutes.

## Wiring (one-time setup)

1. **Provision the mailbox.** Point selected email forwarding rules at a
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
- Whitelisted MIME attachments (PDF / DWG / DXF / PNG / JPG / XLSX / DOCX)
  ride along with the triage POST: once the task row is created, each part
  is uploaded to `/api/v1/attachments/<table>/<task_id>` with the same
  triage token. Failures are logged but never block the row — the task
  still lands so the operator can chase the missing file. Override the
  per-part cap (default 50 MB, matching the server) with
  `INTAKE_MAX_ATTACHMENT_BYTES`.
- Body-only text extraction; rich attachment understanding (Gemini vision
  OCR on scanned PDFs, image captioning) is still a later iteration.

## STATUS 2026-06-09 — paused (bridge signed out)

`tasktrack-email-intake.timer` was **disabled** 2026-06-09 after crash-looping
since the 06-08 reboot. Root cause: Proton Bridge restarted at boot and could
not load its keychain ("could not create keychain: no keychain" in
`~/snap/protonmail-bridge/13/.local/share/protonmail/bridge-v3/logs/`), so the
RtoonyClwBot account is effectively signed out → IMAP LOGIN returns
"no such user", then "too many login attempts" from the 5-min retry hammering.

To revive (Josh, interactive):
1. `snap run protonmail-bridge --cli` → `login` → re-auth RtoonyClwBot@proton.me.
2. Note the NEW bridge IMAP password (`info` in the CLI) — it likely changed.
   Update vault item "Nexus - Intake Mailbox" (INTAKE_IMAP_PASS) and the
   PROTON_BRIDGE_PASSWORD mirror in "Nexus - Maximus" (Maximus email-ops uses
   the same bridge and is likely broken by this too).
3. Test one login manually, then: `systemctl --user enable --now tasktrack-email-intake.timer`
4. Still outstanding from the original checklist: Proton filter (+intake →
   Folders/Intake) and Gmail/work-mail forwarding — without those, the poller
   finds nothing even when healthy.
