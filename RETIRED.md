# RETIRED — this is no longer the live TaskTrack instance

**Retired:** 2026-07-09
**Canonical instance now:** office VM (BRPLVM), `/opt/tasktrack`, tailnet
address `https://brplvm.taild2a25b.ts.net:8444` (VM tailnet join pending as
of this writing — see the migration doc below for current status).

This directory, its `tracker.db`, and the local `collab-tracker.service` /
`radicale.service` / `nexus-pulse-api.service` / `nexus-operator-artifacts.timer`
units were stopped and disabled, **not deleted**. Everything here is exactly
as it was on 2026-07-04 (last real write), kept as a rollback path through
approximately **2026-07-22**.

To roll back:
```
systemctl --user enable --now collab-tracker.service radicale.service \
  nexus-pulse-api.service nexus-operator-artifacts.timer
```

Full retirement manifest (row counts, integrity check, what was and wasn't
touched, why): `~/reports/2026-07-08-tasktrack-vm-migration/RETIREMENT-MANIFEST-2026-07-09.txt`

Full migration history (connectivity plan, coupling inventory, decisions
made along the way): `~/reports/2026-07-08-tasktrack-vm-migration/STATUS.md`

The co-dev pod (`~/tasktrack-codev/`), the LAB staging instance
(`collab-tracker-lab.service`, `~/projects/collab-tracker-lab/`), and this
repo's git history are all still active and unaffected — this retirement is
about the *live prod app + its data*, not the dev/build tooling around it.
