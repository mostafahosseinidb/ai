#!/usr/bin/env bash
# Survey a host to plan a ChainMind deployment on it.
#
# Read-only: it starts nothing, installs nothing and changes nothing.
#
# It is also deliberately careful about secrets, because the intended target
# is a machine that already runs a trading bot:
#
#   * process command lines are NOT printed -- an exchange bot is routinely
#     started with its API key as an argument, and `ps aux` would leak it;
#   * environment variables, .env files and key material are never read;
#   * only listening ports and process *names* are shown.
#
# Run it on the target server and paste the output back.
#
#   bash survey-host.sh
#   bash survey-host.sh > chainmind-survey.txt

set -uo pipefail

section() { printf '\n== %s ==\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

printf 'ChainMind host survey — %s\n' "$(date -u '+%Y-%m-%d %H:%M UTC')"

section "identity"
printf 'hostname   %s\n' "$(hostname 2>/dev/null || echo '?')"
printf 'kernel     %s\n' "$(uname -sr 2>/dev/null || echo '?')"
printf 'arch       %s\n' "$(uname -m 2>/dev/null || echo '?')"
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  printf 'os         %s\n' "${PRETTY_NAME:-unknown}"
fi
printf 'uptime     %s\n' "$(uptime -p 2>/dev/null || echo '?')"
printf 'timezone   %s\n' "$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo '?')"

section "virtualisation"
if have systemd-detect-virt; then
  printf 'virt       %s\n' "$(systemd-detect-virt 2>/dev/null || echo none)"
fi
if [ -f /.dockerenv ]; then echo 'container  yes (docker)'; fi
printf 'cgroup     v%s\n' "$([ -f /sys/fs/cgroup/cgroup.controllers ] && echo 2 || echo 1)"

section "cpu"
printf 'cores      %s\n' "$(nproc 2>/dev/null || echo '?')"
if [ -r /proc/cpuinfo ]; then
  printf 'model      %s\n' "$(grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2- | sed 's/^ *//')"
fi
printf 'loadavg    %s\n' "$(cut -d' ' -f1-3 /proc/loadavg 2>/dev/null || echo '?')"

section "memory"
free -h 2>/dev/null || printf 'free(1) unavailable\n'

section "disk"
df -h / /var /home /opt /srv 2>/dev/null | awk 'NR==1 || !seen[$1]++'

section "python"
for candidate in python3 python3.13 python3.12 python3.11 python3.10; do
  if have "$candidate"; then
    printf '%-12s %s\n' "$candidate" "$("$candidate" -V 2>&1)"
  fi
done
if have python3; then
  python3 - <<'PY' 2>/dev/null
import os, resource, sys
ok = hasattr(os, "fork") and hasattr(os, "wait4")
print(f"sandbox      {'supported' if ok else 'NOT supported'} (needs fork + wait4)")
soft, hard = resource.getrlimit(resource.RLIMIT_NPROC)
print(f"rlimit_nproc soft={soft} hard={hard}")
print(f"executable   {sys.executable}")
PY
fi

section "listening ports (is 8787 free?)"
if have ss; then
  ss -tlnp 2>/dev/null | awk 'NR==1 || $4 ~ /:[0-9]+$/' | sed 's/users:.*pid=\([0-9]*\).*/pid=\1/' | head -40
elif have netstat; then
  netstat -tlnp 2>/dev/null | head -40
else
  echo 'neither ss nor netstat is available'
fi
# Whether the dashboard port is free is better answered by trying to bind it
# than by parsing a socket table this host may not even have.
if have python3; then
  python3 - <<'PROBE' 2>/dev/null
import socket
for port in (8787, 8788):
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
            print(f"port {port}    free")
        except OSError as exc:
            print(f"port {port}    IN USE ({exc.strerror})")
PROBE
fi

section "busiest processes (names only — arguments withheld on purpose)"
ps -eo pid,pcpu,pmem,etimes,comm --sort=-pcpu 2>/dev/null | head -15

section "services"
if have systemctl; then
  systemctl list-units --type=service --state=running --no-pager --no-legend 2>/dev/null \
    | awk '{print $1}' | head -30
else
  echo 'systemd not present'
fi
if have docker; then
  printf '\ndocker containers:\n'
  docker ps --format '  {{.Names}}  {{.Image}}  {{.Status}}' 2>/dev/null || echo '  (cannot query docker)'
fi

section "outbound network"
if have curl; then
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 https://pypi.org/simple/ 2>/dev/null)
  printf 'pypi       HTTP %s\n' "${code:-unreachable}"
else
  echo 'curl not installed'
fi

section "what this does NOT collect"
cat <<'NOTE'
  environment variables, .env files, API keys, wallet or exchange
  credentials, process arguments, and the contents of any config file.
  If a deployment plan needs one of those, it will be asked for explicitly.
NOTE

printf '\n-- end of survey --\n'
