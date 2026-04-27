#!/usr/bin/env bash
# BR Task Tracker — install / upgrade script for a Linux server VM.
#
# Idempotent: safe to run on a fresh VM and again after `git pull` to
# pick up new code. Re-running does NOT overwrite the existing
# /etc/tasktrack/tasktrack.env (so generated tokens survive).
#
# Usage:
#   sudo ./scripts/install.sh                # standard install
#   sudo ./scripts/install.sh --no-restart   # don't restart the service at the end
#   ./scripts/install.sh --prefix /tmp/tt-test --no-systemd --no-restart
#                                            # dry-run for testing the script
#
# After the first run, bootstrap the admin user:
#   cd __APP_DIR__
#   sudo -u __SERVICE_USER__ .venv/bin/flask --app wsgi create-admin \
#       --email you@example.com --name "Your Name"
#
set -euo pipefail

# ─── Defaults ────────────────────────────────────────────────────────────
PREFIX=""               # If set (e.g. /tmp/tt-test), all paths are scoped under it.
SERVICE_USER="patheal"  # Linux user the service runs as.
APP_DIR="/opt/tasktrack"
DATA_DIR="/var/lib/tasktrack"
CONFIG_DIR="/etc/tasktrack"
LOG_DIR="/var/log/tasktrack"
SYSTEMD_DIR="/etc/systemd/system"
SERVICE_NAME="tasktrack.service"
DO_SYSTEMD=1
DO_RESTART=1

while [[ $# -gt 0 ]]; do
    case "$1" in
        --prefix) PREFIX="$2"; shift 2 ;;
        --service-user) SERVICE_USER="$2"; shift 2 ;;
        --no-systemd) DO_SYSTEMD=0; shift ;;
        --no-restart) DO_RESTART=0; shift ;;
        -h|--help)
            sed -n '2,18p' "$0"
            exit 0
            ;;
        *)
            echo "unknown option: $1" >&2
            exit 1
            ;;
    esac
done

# Apply prefix to all paths (used for dry-runs).
if [[ -n "$PREFIX" ]]; then
    APP_DIR="${PREFIX}${APP_DIR}"
    DATA_DIR="${PREFIX}${DATA_DIR}"
    CONFIG_DIR="${PREFIX}${CONFIG_DIR}"
    LOG_DIR="${PREFIX}${LOG_DIR}"
    SYSTEMD_DIR="${PREFIX}${SYSTEMD_DIR}"
fi

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE_ENV="${REPO_DIR}/deploy/tasktrack.env.template"
TEMPLATE_UNIT="${REPO_DIR}/deploy/tasktrack.service.template"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

# ─── 0. Pre-flight ───────────────────────────────────────────────────────
[[ -f "$TEMPLATE_ENV" ]] || fail "missing template: $TEMPLATE_ENV"
[[ -f "$TEMPLATE_UNIT" ]] || fail "missing template: $TEMPLATE_UNIT"
[[ -f "$REPO_DIR/wsgi.py" ]] || fail "wsgi.py not found — is $REPO_DIR the repo root?"

if [[ -z "$PREFIX" && $EUID -ne 0 ]]; then
    fail "run as root (sudo) for system-wide install — or pass --prefix /tmp/... for a dry-run"
fi

say "BR Task Tracker installer"
echo "    repo        = $REPO_DIR"
echo "    service user= $SERVICE_USER"
echo "    app dir     = $APP_DIR"
echo "    data dir    = $DATA_DIR"
echo "    config dir  = $CONFIG_DIR"
echo "    log dir     = $LOG_DIR"
echo "    systemd     = $([[ $DO_SYSTEMD -eq 1 ]] && echo yes || echo no)"
[[ -n "$PREFIX" ]] && echo "    PREFIX      = $PREFIX (dry-run mode)"

# ─── 1. System dependencies ──────────────────────────────────────────────
if [[ -z "$PREFIX" ]]; then
    say "installing system packages (python3, venv, git, sqlite, curl)"
    if command -v apt-get >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq
        apt-get install -y --no-install-recommends \
            python3 python3-venv python3-pip \
            git sqlite3 curl ca-certificates
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y python3 python3-pip git sqlite curl ca-certificates
    else
        warn "no apt-get or dnf — install python3, python3-venv, git, sqlite3, curl manually"
    fi
fi

# ─── 2. Service user ─────────────────────────────────────────────────────
if [[ -z "$PREFIX" ]]; then
    if id -u "$SERVICE_USER" >/dev/null 2>&1; then
        say "service user '$SERVICE_USER' already exists"
    else
        say "creating service user '$SERVICE_USER'"
        useradd --system --create-home --shell /bin/bash "$SERVICE_USER"
    fi
fi

# ─── 3. Directories ──────────────────────────────────────────────────────
say "ensuring directories"
mkdir -p "$APP_DIR" "$DATA_DIR" "$CONFIG_DIR" "$LOG_DIR"
if [[ -z "$PREFIX" ]]; then
    chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR" "$LOG_DIR"
    chmod 0750 "$DATA_DIR" "$LOG_DIR"
    chmod 0755 "$CONFIG_DIR"
fi

# ─── 4. Sync code ────────────────────────────────────────────────────────
say "syncing app code -> $APP_DIR"
# rsync without --delete so the .venv survives between installs.
rsync -a --exclude='.git' \
          --exclude='__pycache__' \
          --exclude='.pytest_cache' \
          --exclude='.venv' \
          --exclude='tracker.db' \
          --exclude='tracker.db-*' \
          --exclude='*.bak' \
          "$REPO_DIR/" "$APP_DIR/"
if [[ -z "$PREFIX" ]]; then
    chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"
fi

# ─── 5. Python virtualenv ────────────────────────────────────────────────
say "building / updating Python virtualenv"
if [[ ! -d "$APP_DIR/.venv" ]]; then
    python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip wheel
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
if [[ -z "$PREFIX" ]]; then
    chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR/.venv"
fi

# ─── 6. /etc/tasktrack/tasktrack.env (only if missing) ───────────────────
ENV_FILE="$CONFIG_DIR/tasktrack.env"
if [[ ! -f "$ENV_FILE" ]]; then
    say "generating $ENV_FILE from template (FIRST INSTALL)"
    install -m 0640 "$TEMPLATE_ENV" "$ENV_FILE"
    # Generate fresh scoped tokens.
    TOKEN_TRIAGE="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
    TOKEN_PERSONAL="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
    TOKEN_BOT="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
    sed -i \
        -e "s|__SET_BY_INSTALL__|placeholder|g" \
        "$ENV_FILE"
    # Now replace placeholders one at a time so each token is unique.
    sed -i "0,/^TASKTRACK_TOKEN_TRIAGE=.*/{s||TASKTRACK_TOKEN_TRIAGE=${TOKEN_TRIAGE}|}" "$ENV_FILE"
    sed -i "0,/^TASKTRACK_TOKEN_PERSONAL=.*/{s||TASKTRACK_TOKEN_PERSONAL=${TOKEN_PERSONAL}|}" "$ENV_FILE"
    sed -i "0,/^TASKTRACK_TOKEN_BOT=.*/{s||TASKTRACK_TOKEN_BOT=${TOKEN_BOT}|}" "$ENV_FILE"
    if [[ -z "$PREFIX" ]]; then
        chown root:"$SERVICE_USER" "$ENV_FILE"
        chmod 0640 "$ENV_FILE"
    fi
else
    say "$ENV_FILE already exists — leaving it untouched"
fi

# Stamp git SHA so /healthz can report it.
if command -v git >/dev/null 2>&1 && [[ -d "$REPO_DIR/.git" ]]; then
    GIT_SHA="$(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    if grep -q "^TASKTRACK_GIT_SHA=" "$ENV_FILE"; then
        sed -i "s|^TASKTRACK_GIT_SHA=.*|TASKTRACK_GIT_SHA=${GIT_SHA}|" "$ENV_FILE"
    elif grep -q "^# TASKTRACK_GIT_SHA=" "$ENV_FILE"; then
        sed -i "s|^# TASKTRACK_GIT_SHA=.*|TASKTRACK_GIT_SHA=${GIT_SHA}|" "$ENV_FILE"
    else
        printf "\nTASKTRACK_GIT_SHA=%s\n" "$GIT_SHA" >> "$ENV_FILE"
    fi
fi

# ─── 7. Database — empty file + alembic upgrade ──────────────────────────
DB_FILE="$DATA_DIR/tracker.db"
if [[ ! -f "$DB_FILE" ]]; then
    say "creating empty SQLite at $DB_FILE"
    touch "$DB_FILE"
    if [[ -z "$PREFIX" ]]; then
        chown "$SERVICE_USER:$SERVICE_USER" "$DB_FILE"
        chmod 0640 "$DB_FILE"
    fi
fi

# DB_PATH is read by app/db.py from app.config["DB_PATH"] which the
# Flask app pulls from the env. We need to override the default
# DB_PATH (project-rooted tracker.db) with our /var/lib path. The
# clean way is via env when invoking flask CLI.
say "running alembic upgrade head"
if [[ -z "$PREFIX" ]]; then
    sudo -u "$SERVICE_USER" \
        env TASKTRACK_DATABASE_URL="sqlite:///$DB_FILE" \
        "$APP_DIR/.venv/bin/python" -m alembic -c "$APP_DIR/alembic.ini" upgrade head
else
    TASKTRACK_DATABASE_URL="sqlite:///$DB_FILE" \
        "$APP_DIR/.venv/bin/python" -m alembic -c "$APP_DIR/alembic.ini" upgrade head
fi

# ─── 8. systemd unit ─────────────────────────────────────────────────────
if [[ "$DO_SYSTEMD" -eq 1 ]]; then
    say "generating $SYSTEMD_DIR/$SERVICE_NAME"
    mkdir -p "$SYSTEMD_DIR"
    sed \
        -e "s|__SERVICE_USER__|$SERVICE_USER|g" \
        -e "s|__APP_DIR__|$APP_DIR|g" \
        -e "s|__DATA_DIR__|$DATA_DIR|g" \
        "$TEMPLATE_UNIT" > "$SYSTEMD_DIR/$SERVICE_NAME"
    if [[ -z "$PREFIX" ]]; then
        # Add DB_PATH override via systemd Environment= so the app
        # finds the file under /var/lib instead of the project dir.
        if ! grep -q "^Environment=DB_PATH=" "$SYSTEMD_DIR/$SERVICE_NAME"; then
            sed -i "/^EnvironmentFile=/a Environment=DB_PATH=$DB_FILE" \
                "$SYSTEMD_DIR/$SERVICE_NAME"
        fi
        systemctl daemon-reload
        systemctl enable "$SERVICE_NAME"
        if [[ "$DO_RESTART" -eq 1 ]]; then
            say "restarting $SERVICE_NAME"
            systemctl restart "$SERVICE_NAME"
            sleep 2
            systemctl --no-pager status "$SERVICE_NAME" | head -12
        else
            say "skipping restart (--no-restart)"
        fi
    fi
else
    say "skipping systemd (--no-systemd)"
fi

# ─── 9. Summary ──────────────────────────────────────────────────────────
cat <<EOF

==> Install complete.

Bootstrap the first admin user (only on a fresh deploy):

    cd $APP_DIR
    sudo -u $SERVICE_USER \\
        env DB_PATH=$DB_FILE \\
        ./.venv/bin/python -m flask --app wsgi create-admin \\
            --email YOUR_EMAIL --name "YOUR NAME"

Verify the service:

    curl http://127.0.0.1:5050/healthz
    systemctl status $SERVICE_NAME

The web app should answer on http://<this-vm-ip>:5050. Open from any
browser on the LAN. Login with the admin email + password you just set.

Edit $CONFIG_DIR/tasktrack.env to change config; restart with
\`systemctl restart $SERVICE_NAME\` to apply.
EOF
