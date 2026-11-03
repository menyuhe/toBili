#!/bin/bash
set -euo pipefail
umask 027
PROJECT=/www/wwwroot/tobill
test "$(realpath "$PROJECT")" = /www/wwwroot/tobill
cd "$PROJECT"
test -f cloud/app.py
test -f src/cloud_protocol.py
if test -e .env || test -e /etc/systemd/system/ToBili.service; then
  echo 'Existing ToBili configuration found; refusing to replace credentials or service.'
  exit 1
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends python3-venv postgresql
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check --index-url https://pypi.org/simple -r cloud/requirements.txt
if ! id ToBili >/dev/null 2>&1; then
  useradd --system --home-dir "$PROJECT" --no-create-home --shell /usr/sbin/nologin ToBili
fi
chgrp -R ToBili .venv cloud src
chmod -R g+rX .venv cloud src
install -d -o ToBili -g ToBili -m 0750 data
systemctl start postgresql
python3 - <<'PY'
from pathlib import Path
import secrets
import subprocess
password = secrets.token_hex(32)
invite = secrets.token_hex(24)
def psql(sql):
    return subprocess.run(['runuser','-u','postgres','--','psql','-v','ON_ERROR_STOP=1','-At'],
                          input=sql, text=True, check=True, capture_output=True).stdout.strip()
if psql("SELECT 1 FROM pg_roles WHERE rolname='ToBili';") or psql("SELECT 1 FROM pg_database WHERE datname='ToBili';"):
    raise SystemExit('Existing ToBili database/role found; refusing to alter it.')
psql(f'CREATE ROLE "ToBili" LOGIN PASSWORD \'{password}\';')
subprocess.run(['runuser','-u','postgres','--','createdb','--owner=ToBili','ToBili'],check=True)
path=Path('/www/wwwroot/tobill/.env')
path.write_text('RELAY_PUBLIC_ORIGIN=https://tobili.4wdo.cn\n'
                f'RELAY_DATABASE_URL=postgresql+psycopg://ToBili:{password}@127.0.0.1:5432/ToBili\n'
                f'RELAY_REGISTRATION_INVITE={invite}\n', encoding='utf-8')
path.chmod(0o600)
print('ToBili database and private environment created; secrets were not printed.')
PY
install -m 0644 cloud/baota/ToBili.service /etc/systemd/system/ToBili.service
systemctl daemon-reload
systemctl enable --now ToBili
for attempt in $(seq 1 20); do
  if curl --fail --silent http://127.0.0.1:18788/api/v1/health; then
    echo
    echo 'ToBili relay ready on 127.0.0.1:18788; Nginx HTTPS proxy is the next step.'
    exit 0
  fi
  sleep 1
done
systemctl status ToBili --no-pager
exit 1
