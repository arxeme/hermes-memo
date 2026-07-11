#!/usr/bin/env bash
set -euo pipefail

# Publish the local hermes-memo plugin into a remote Hermes installation
# (the Incus VM on the Voyager test server), mirroring hermes-telex's deploy.
#
# Connection defaults are read from deploy/env.local (gitignored):
#   SMC_PROFILE=... SERVER_HOST=... VM_NAME=... REMOTE_USER=...
#   REMOTE_HERMES_HOME=/home/<user>/.hermes
#   REMOTE_HERMES_INSTALL_DIR=<HERMES_HOME>/hermes-agent
#
# Memory providers are activated by config, not `hermes plugins enable`:
# the plugin tree lands in HERMES_HOME/plugins/memo and the VM's
# ~/.hermes/config.yaml must carry `memory.provider: memo` (maintained on
# the VM, this script only verifies and warns). Plugin-owned runtime config
# (base_url / api_key) is seeded into HERMES_HOME/memo.json from
# MEMO_BASE_URL / MEMO_API_KEY when both are set in the environment or in
# deploy/env.local — the URL must be reachable FROM INSIDE THE VM (the
# Incus host gateway, e.g. http://<incus-host-gateway>:18000), not a tunnel.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_SRC="$(cd "$SCRIPT_DIR/../src/memo" && pwd)"
ENV_LOCAL="$SCRIPT_DIR/env.local"

die() { echo "ERROR: $1" >&2; exit 1; }
info() { echo "== $1"; }

[[ -f "$ENV_LOCAL" ]] && source "$ENV_LOCAL"
SMC_PROFILE="${SMC_PROFILE:-sea}"
SERVER_HOST="${SERVER_HOST:?SERVER_HOST required (deploy/env.local)}"
VM_NAME="${VM_NAME:?VM_NAME required (deploy/env.local)}"
REMOTE_USER="${REMOTE_USER:-hermes}"
REMOTE_HERMES_HOME="${REMOTE_HERMES_HOME:-/home/${REMOTE_USER}/.hermes}"
REMOTE_HERMES_INSTALL_DIR="${REMOTE_HERMES_INSTALL_DIR:-${REMOTE_HERMES_HOME}/hermes-agent}"
REMOTE_PLUGIN_DIR="${REMOTE_PLUGIN_DIR:-${REMOTE_HERMES_HOME}/plugins/memo}"
MEMO_BASE_URL="${MEMO_BASE_URL:-}"
MEMO_API_KEY="${MEMO_API_KEY:-}"

shell_quote() { printf "%q" "$1"; }
smc_toc() { smc -c "$SMC_PROFILE" toc "$SERVER_HOST" -- "$1"; }
server() { smc_toc "$1"; }
vm_user() { smc_toc "sudo incus exec ${VM_NAME} -- su - ${REMOTE_USER} -c $(shell_quote "$1")"; }
# smc masks remote exit codes, so critical steps echo a sentinel we assert on.
vm_user_checked() {
    local out
    out="$(vm_user "$1
echo __REMOTE_EXIT__:\$?")"
    printf '%s\n' "$out"
    printf '%s\n' "$out" | grep -q "^__REMOTE_EXIT__:0$" || die "remote install failed"
}

TIMESTAMP="$(date +%Y%m%d%H%M%S)"
ARCHIVE_BASENAME="hermes-memo_${TIMESTAMP}.tar.gz"
ARCHIVE="/tmp/${ARCHIVE_BASENAME}"
REMOTE_ARCHIVE="/tmp/${ARCHIVE_BASENAME}"

info "Package local Memo plugin"
tar -czf "$ARCHIVE" \
    --exclude="__pycache__" \
    --exclude="*.pyc" \
    -C "$PLUGIN_SRC" .

info "Upload plugin archive to ${SERVER_HOST}/${VM_NAME}"
smc -c "$SMC_PROFILE" scp "$ARCHIVE" "${SERVER_HOST}:/tmp/${ARCHIVE_BASENAME}"
server "sudo incus file push /tmp/${ARCHIVE_BASENAME} ${VM_NAME}${REMOTE_ARCHIVE}"
smc_toc "rm -f /tmp/${ARCHIVE_BASENAME}"
rm -f "$ARCHIVE"

info "Install plugin, verify provider import, and restart gateway"
INSTALL_CMD=$(cat <<EOF
set -eu
export HERMES_HOME="${REMOTE_HERMES_HOME}"
export REMOTE_PLUGIN_DIR="${REMOTE_PLUGIN_DIR}"
export HERMES_INSTALL_DIR="${REMOTE_HERMES_INSTALL_DIR}"
export PATH="\$HOME/.local/bin:\$HOME/.cargo/bin:\$PATH"

test -d "${REMOTE_HERMES_INSTALL_DIR}"
test -x "${REMOTE_HERMES_INSTALL_DIR}/venv/bin/python"

service=hermes-gateway.service
systemctl --user stop "\$service" 2>/dev/null || true
systemctl --user reset-failed "\$service" 2>/dev/null || true

mkdir -p "${REMOTE_HERMES_HOME}/plugins"
rm -rf "${REMOTE_PLUGIN_DIR}"
mkdir -p "${REMOTE_PLUGIN_DIR}"
tar -xzf "${REMOTE_ARCHIVE}" -C "${REMOTE_PLUGIN_DIR}" 2>/dev/null || tar -xzf "${REMOTE_ARCHIVE}" -C "${REMOTE_PLUGIN_DIR}"
rm -f "${REMOTE_ARCHIVE}" 2>/dev/null || true  # pushed as root; VM cleanup is cosmetic
chmod -R u+rwX,go+rX "${REMOTE_PLUGIN_DIR}"
test -f "${REMOTE_PLUGIN_DIR}/plugin.yaml"
test -f "${REMOTE_PLUGIN_DIR}/__init__.py"

# httpx is the plugin's only dependency.
if "${REMOTE_HERMES_INSTALL_DIR}/venv/bin/python" -c 'import httpx' 2>/dev/null; then
    echo "httpx already present"
elif "${REMOTE_HERMES_INSTALL_DIR}/venv/bin/python" -m pip --version >/dev/null 2>&1; then
    "${REMOTE_HERMES_INSTALL_DIR}/venv/bin/python" -m pip install httpx
elif command -v uv >/dev/null 2>&1; then
    uv pip install --python "${REMOTE_HERMES_INSTALL_DIR}/venv/bin/python" httpx
else
    echo "ERROR: httpx missing and no pip/uv available" >&2; exit 1
fi

# Verify the provider imports against the real hermes MemoryProvider ABC.
"${REMOTE_HERMES_INSTALL_DIR}/venv/bin/python" - <<'PY'
import importlib.util, os, pathlib, sys
sys.path.insert(0, os.environ["HERMES_INSTALL_DIR"])
plugin_dir = pathlib.Path(os.environ["REMOTE_PLUGIN_DIR"])
spec = importlib.util.spec_from_file_location(
    "memo", plugin_dir / "__init__.py", submodule_search_locations=[str(plugin_dir)])
module = importlib.util.module_from_spec(spec)
module.__path__ = [str(plugin_dir)]
sys.modules["memo"] = module
spec.loader.exec_module(module)
assert hasattr(module, "register"), "plugin does not expose register(ctx)"
provider = module.MemoMemoryProvider()
assert provider.name == "memo"
print("provider import OK (register + MemoMemoryProvider)")
PY

if grep -q "provider: *memo" "${REMOTE_HERMES_HOME}/config.yaml" 2>/dev/null; then
    echo "config.yaml: memory.provider=memo OK"
else
    echo "WARNING: ${REMOTE_HERMES_HOME}/config.yaml lacks 'memory: {provider: memo}' — set it on the VM"
fi

systemctl --user start "\$service"
sleep 2
systemctl --user --no-pager status "\$service" | head -5 || true
EOF
)
vm_user_checked "$INSTALL_CMD"

if [[ -n "$MEMO_BASE_URL" && -n "$MEMO_API_KEY" ]]; then
    info "Seed HERMES_HOME/memo.json (base_url=${MEMO_BASE_URL})"
    vm_user "umask 077 && printf '{ \"base_url\": \"%s\", \"api_key\": \"%s\" }\n' '$MEMO_BASE_URL' '$MEMO_API_KEY' > ${REMOTE_HERMES_HOME}/memo.json && echo memo.json written"
else
    info "MEMO_BASE_URL/MEMO_API_KEY not set — leaving HERMES_HOME/memo.json as-is"
fi

info "Done"
