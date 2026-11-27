#!/bin/sh
set -u

auth_dir=$(mktemp -d -t digues-xvfb.XXXXXX)
export XAUTHORITY="$auth_dir/Xauthority"
touch "$XAUTHORITY"
xvfb_pid=
node_pid=

cleanup() {
  if [ -n "$node_pid" ]; then
    kill -TERM "$node_pid" 2>/dev/null || true
    wait "$node_pid" 2>/dev/null || true
  fi
  if [ -n "$xvfb_pid" ]; then
    kill -TERM "$xvfb_pid" 2>/dev/null || true
    wait "$xvfb_pid" 2>/dev/null || true
  fi
  rm -r "$auth_dir"
}

on_signal() {
  cleanup
  exit 0
}
trap on_signal TERM INT

display_number=90
display_ready=
while [ "$display_number" -le 119 ]; do
  if [ -e "/tmp/.X11-unix/X$display_number" ]; then
    display_number=$((display_number + 1))
    continue
  fi
  if ! xauth -f "$XAUTHORITY" add ":$display_number" . "$(mcookie)" >/dev/null; then
    echo 'ERROR Xvfb authentication setup failed' >&2
    cleanup
    exit 1
  fi
  Xvfb ":$display_number" -screen 0 2560x2560x24 -nolisten tcp \
    -auth "$XAUTHORITY" >>"$auth_dir/xvfb.log" 2>&1 &
  xvfb_pid=$!
  attempt=0
  while [ "$attempt" -lt 50 ]; do
    if ! kill -0 "$xvfb_pid" 2>/dev/null; then break; fi
    if [ -S "/tmp/.X11-unix/X$display_number" ]; then
      display_ready=yes
      break
    fi
    attempt=$((attempt + 1))
    sleep 0.1
  done
  if [ -n "$display_ready" ]; then break; fi
  kill -TERM "$xvfb_pid" 2>/dev/null || true
  wait "$xvfb_pid" 2>/dev/null || true
  xvfb_pid=
  display_number=$((display_number + 1))
done
if [ -z "$display_ready" ]; then
  echo 'ERROR Xvfb could not start on displays :90–:119' >&2
  if [ -f "$auth_dir/xvfb.log" ]; then sed -n '1,40p' "$auth_dir/xvfb.log" >&2; fi
  cleanup
  exit 1
fi

export DISPLAY=:$display_number
echo "Xvfb ready on $DISPLAY"
node server.mjs &
node_pid=$!
wait "$node_pid"
status=$?
cleanup
exit "$status"
