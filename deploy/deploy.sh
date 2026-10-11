#!/usr/bin/env bash
# Runs ON the server, started by the deploy job in .github/workflows/ci.yml:
#     deploy/deploy.sh <git commit>
#
# Puts that commit in place, updates dependencies and the database, restarts the app and
# checks that it answers. If it does not, the previous commit is put back.
set -euo pipefail

target="${1:?usage: deploy.sh <git commit>}"
app_dir=/opt/munimailbox
health_url=http://127.0.0.1:8000/readyz

cd "$app_dir"
previous="$(git rev-parse HEAD)"

install() {
  git fetch --quiet origin
  git checkout --quiet --detach "$1"
  uv sync --locked --no-dev
}

healthy() {
  for _ in $(seq 1 60); do
    if curl --silent --fail --max-time 2 "$health_url" > /dev/null; then return 0; fi
    sleep 2
  done
  return 1
}

echo "Deploying $target (currently $previous)"
install "$target"
# Migrations only ever add; the previous version of the code keeps working with the new
# schema, which is what makes putting the old commit back safe.
set -a; . /etc/munimailbox/env; set +a
uv run --no-dev alembic upgrade head
sudo systemctl restart munimailbox

if healthy; then
  echo "Deployed $target"
  exit 0
fi

echo "The app does not answer on $health_url. Putting $previous back." >&2
install "$previous"
sudo systemctl restart munimailbox
healthy || echo "The previous version does not answer either. Check: journalctl -u munimailbox" >&2
exit 1
