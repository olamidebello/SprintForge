#!/usr/bin/env bash
# Run on the target Debian/Ubuntu server: sudo bash deploy.sh certificates@example.com
set -Eeuo pipefail
if [[ ${EUID} -ne 0 ]]; then
  echo 'Run this script as root or with sudo.' >&2
  exit 1
fi
if [[ $# -ne 1 || ! $1 =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]; then
  echo 'Usage: sudo bash deploy.sh CERTIFICATE_EMAIL' >&2
  exit 2
fi
if [[ ! -f /etc/debian_version ]]; then
  echo 'This installer supports Debian and Ubuntu servers.' >&2
  exit 1
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git ansible-core util-linux ca-certificates
exec 9>/run/lock/sprintforge-deploy.lock
flock -n 9 || { echo 'Another SprintForge deployment is running.' >&2; exit 1; }
checkout=$(mktemp -d /tmp/sprintforge-deploy.XXXXXXXX)
trap 'rm -rf -- "$checkout"' EXIT
git clone --depth 1 --branch main https://github.com/olamidebello/SprintForge.git "$checkout/source"
python3 - "$1" "$checkout/vars.json" <<'PY'
import json,sys
with open(sys.argv[2], 'w') as f:
    json.dump({'sprintforge_email':sys.argv[1]}, f)
PY
ansible-playbook -i localhost, -c local "$checkout/source/deploy/deploy.yml" --extra-vars "@$checkout/vars.json"
printf '\nSprintForge deployment checks passed: https://sprintforge.netwizzard.tech\n'
printf 'Create or promote your super administrator:\n'
printf 'sudo -u sprintforge env SPRINTFORGE_DB=/var/lib/sprintforge/sprintforge.db python3 /opt/sprintforge/manage.py create-superadmin YOUR_EMAIL\n'
