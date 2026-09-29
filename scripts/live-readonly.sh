#!/usr/bin/env bash
# Read-only checks against the live account mailctl is configured for,
# reported as TAP. Run with -h for usage.
#
# Safety: every mailctl call goes through run_mailctl, whose guard refuses a
# subcommand that can change anything unless --dry-run is in its argv, and
# refuses --yes outright. Each test makes a fixed, small number of calls,
# with no retries, a pause between tests, a timeout on every call, and stdin
# from /dev/null so a password prompt can never block. Nothing here prints
# the environment or the config; diagnostics are mailctl's own output lines,
# trimmed.
#
# `set -e` is left off on purpose: a failing check must end its own test,
# not the run.

set -uo pipefail

##############################################################################
# Settings

readonly CALL_TIMEOUT=90
readonly DIAG_LINES=12
readonly DIAG_WIDTH=160

# The offline tests set this to 0; against the live account leave it alone.
PAUSE=${LIVECHECK_PAUSE:-2}

# In run order. `unchanged` stays last: it compares against the baseline
# taken before the first test.
readonly TESTS=(
  test
  probe
  check-baseline
  list
  show
  rules
  folders
  folder-counts
  backup
  search
  search-unread
  search-sort
  senders
  search-like
  build-filter
  json
  view-keeps-unread
  mark
  apply
  apply-like
  add
  add-create-folder
  add-like
  remove-rule
  disable-rule
  subscribe
  create-folder
  rename-folder
  optimize-rules
  unchanged
)

# Subcommands that change something, locally or on the server.
readonly MUTATING=(
  add
  apply
  backup
  create-folder
  disable-rule
  enable-rule
  mark
  migrate-config
  move-rule
  optimize-rules
  remove-rule
  rename-folder
  restore
  save-baseline
  subscribe
  unsubscribe
)

# What `probe --json` must hold: it parses, it is version 1, and each half
# has its identity, a non-empty capability list, and the account's shape.
# Prints the first problem found and exits non-zero.
read -r -d '' PROBE_CHECK << 'PYTHON'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        doc = json.load(handle)

except ValueError as exc:
    sys.exit(f"not JSON: {exc}")


def need(ok, what):
    if not ok:
        sys.exit(what)


need(isinstance(doc, dict), "not a JSON object")
need(doc.get("version") == 1, "version is not 1")
need(isinstance(doc.get("taken"), str), "no taken time")
need(doc.get("endpoints"), "no endpoints")

for half, keys in (
    ("rules", ("active_rule_set", "extensions")),
    ("mail", ("delimiter", "namespaces")),
):
    section = doc.get(half)
    need(isinstance(section, dict), f"no {half} section")
    need(isinstance(section.get("identity"), dict), f"no {half} identity")
    need(section.get("capabilities"), f"no {half} capabilities")

    for key in keys:
        need(key in section, f"no {half} {key}")
PYTHON
readonly PROBE_CHECK

# What `folders --counts --json` must hold: it parses, it is version 1, it
# lists folders, and each has integer message and unread counts, unread no
# more than the total, and a size that is an integer or null. Prints the
# first problem found and exits non-zero.
read -r -d '' COUNTS_CHECK << 'PYTHON'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        doc = json.load(handle)

except ValueError as exc:
    sys.exit(f"not JSON: {exc}")


def need(ok, what):
    if not ok:
        sys.exit(what)


def whole(value):
    return type(value) is int and value >= 0


need(isinstance(doc, dict), "not a JSON object")
need(doc.get("version") == 1, "version is not 1")
need(isinstance(doc.get("folders"), list) and doc["folders"], "no folders")

for folder in doc["folders"]:
    name = folder.get("name")

    for key in ("messages", "unseen"):
        need(whole(folder.get(key)), f"{name!r} has no integer {key}")

    need(folder["unseen"] <= folder["messages"], f"{name!r} unread > total")
    need(
        folder.get("size") is None or whole(folder["size"]),
        f"{name!r} has a size that is not an integer",
    )
PYTHON
readonly COUNTS_CHECK

# What `search --sort size --reverse --json` must hold: at most three
# messages, sizes that never grow, and the order named. Prints the first
# problem found and exits non-zero.
read -r -d '' SORT_CHECK << 'PYTHON'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        doc = json.load(handle)

except ValueError as exc:
    sys.exit(f"not JSON: {exc}")

sizes = [message.get("size") for message in doc.get("messages", [])]

if doc.get("sort") != {"key": "size", "reverse": True}:
    sys.exit(f"sort is {doc.get('sort')!r}, not size reversed")

if len(sizes) > 3:
    sys.exit(f"--limit 3 listed {len(sizes)} messages")

if any(later > earlier for earlier, later in zip(sizes, sizes[1:])):
    sys.exit(f"sizes are not largest first: {sizes}")
PYTHON
readonly SORT_CHECK

# What `senders --json` must hold: it parses, it is version 1, no more than
# five rows, each with whole counts and unread no more than its total, the
# rows busiest first, and the report's own totals at least what its rows
# add up to. Prints the first problem found and exits non-zero.
read -r -d '' SENDERS_CHECK << 'PYTHON'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        doc = json.load(handle)

except ValueError as exc:
    sys.exit(f"not JSON: {exc}")


def need(ok, what):
    if not ok:
        sys.exit(what)


def whole(value):
    return type(value) is int and value >= 0


need(isinstance(doc, dict), "not a JSON object")
need(doc.get("version") == 1, "version is not 1")
need(isinstance(doc.get("senders"), list), "no senders list")

rows = doc["senders"]

need(len(rows) <= 5, f"--top 5 listed {len(rows)} rows")

for row in rows:
    key = row.get("key")

    for name in ("total", "unread"):
        need(whole(row.get(name)), f"{key!r} has no integer {name}")

    need(row["unread"] <= row["total"], f"{key!r} unread > total")

order = [(-row["total"], -row["unread"]) for row in rows]

need(order == sorted(order), "the rows are not busiest first")

for name in ("messages", "unread"):
    need(whole(doc.get(name)), f"the report has no integer {name}")

need(doc["unread"] <= doc["messages"], "the report's unread > its total")
need(
    doc["messages"] >= sum(row["total"] for row in rows),
    "the rows count more messages than the report",
)
PYTHON
readonly SENDERS_CHECK

# What `optimize-rules --dry-run --json` must hold: it parses, it is version
# 1, it is that command's plan, every proposal list is a list, `changes`
# says whether any proposal was made, and a diff is there exactly when it
# does. Prints the first problem found and exits non-zero.
read -r -d '' OPTIMIZE_CHECK << 'PYTHON'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        doc = json.load(handle)

except ValueError as exc:
    sys.exit(f"not JSON: {exc}")


def need(ok, what):
    if not ok:
        sys.exit(what)


need(isinstance(doc, dict), "not a JSON object")
need(doc.get("version") == 1, "version is not 1")

plan = doc.get("plan")

need(isinstance(plan, dict), "no plan")
need(plan.get("command") == "optimize-rules", "not an optimize-rules plan")

for name in ("removals", "reorders", "merges", "uncertain", "considered"):
    need(isinstance(plan.get(name), list), f"no {name} list")

proposed = bool(plan["removals"] or plan["reorders"] or plan["merges"])

need(plan.get("changes") is proposed, "changes disagrees with the proposals")

if proposed:
    diff = plan.get("diff")
    need(isinstance(diff, dict) and diff.get("text"), "changes but no diff")

else:
    need(plan.get("diff") is None, "a diff with nothing proposed")
PYTHON
readonly OPTIMIZE_CHECK

# Lines mailctl prints only when it has actually changed something.
CHANGED_RE='^(Backed up|Created IMAP|Uploaded|Moved [0-9]|Flagged [0-9]'
CHANGED_RE+='|Deleted [0-9]|Marked [0-9]|Subscribed|Unsubscribed|Restored'
CHANGED_RE+='|Renamed IMAP|Removed .* from the subscription list)'
readonly CHANGED_RE

##############################################################################
# Utilities

#-----------------------------------------------------------------------------
usage() {
  cat << 'EOF'
Usage: live-readonly.sh [--list] [-h|--help] [TEST...]

Run read-only checks against the live account mailctl is configured for and
report them as TAP. With no TEST names, run them all; with names, run only
those (in the standard order). The `unchanged` test compares the active
script and the folder list against a baseline taken before the first test.

Only read-only subcommands and --dry-run are ever sent; --yes never is.

Options:
  --list      print the test names and exit
  -h, --help  print this help and exit

Requires python3, to parse the --json documents.

Environment:
  MAILCTL_BIN  the mailctl to run (default: mailctl on PATH, else the
               repo's .venv/bin/mailctl). mailctl's own configuration
               (config file, MAILCTL_* settings) applies as usual.

Exit status: 0 when nothing failed, 1 when a test failed, 2 on a usage
error or when mailctl cannot be found.
EOF
}

#-----------------------------------------------------------------------------
in_list() {
  local needle=$1 item
  shift

  for item in "$@"; do
    [[ $item == "$needle" ]] && return 0
  done

  return 1
}

#-----------------------------------------------------------------------------
# Succeeds when the argv may be sent: nothing mutating without --dry-run,
# and never --yes. Any mutating name anywhere in the argv counts, so a
# positional that happens to share one only makes the guard stricter.
guard_allows() {
  local arg mutating=0 dry=0

  for arg in "$@"; do
    [[ $arg == --yes ]] && return 1
    [[ $arg == --dry-run ]] && dry=1
    in_list "$arg" "${MUTATING[@]}" && mutating=1
  done

  ((mutating && !dry)) && return 1

  return 0
}

#-----------------------------------------------------------------------------
# Run mailctl once. Sets RC; stdout lands in $OUT and stderr in $ERR, with
# a trailing CR dropped from each line so the parsers see one line ending:
# `show` prints the server's script bytes, and those end in CRLF. $RAW keeps
# stdout byte for byte.
run_mailctl() {
  if ! guard_allows "$@"; then
    printf 'live-readonly.sh refused to run: mailctl %s\n' "$*" > "$ERR"
    : > "$OUT"
    : > "$RAW"
    RC=125

    return "$RC"
  fi

  timeout "$CALL_TIMEOUT" "$MAILCTL_BIN" "$@" < /dev/null > "$RAW" 2> "$ERR"
  RC=$?

  sed 's/\r$//' "$RAW" > "$OUT"
  sed -i 's/\r$//' "$ERR"

  return "$RC"
}

#-----------------------------------------------------------------------------
diag() {
  DIAG+=("$@")
}

#-----------------------------------------------------------------------------
# Queue the last call's output as diagnostics, trimmed.
diag_output() {
  local line

  while IFS= read -r line; do
    DIAG+=("  ${line:0:DIAG_WIDTH}")
  done < <(cat "$OUT" "$ERR" | head -n "$DIAG_LINES")
}

#-----------------------------------------------------------------------------
fail() {
  diag "$1"
  diag_output

  return 1
}

#-----------------------------------------------------------------------------
# Skip the test, for the reason given, when the value is empty.
need() {
  [[ -n $1 ]] && return 0

  SKIP_REASON=$2

  return 2
}

#-----------------------------------------------------------------------------
expect_ok() {
  ((RC == 0)) || fail "mailctl $1 exited $RC"
}

#-----------------------------------------------------------------------------
expect_line() {
  grep -qE -- "$1" "$OUT" || fail "expected a line matching: $1"
}

#-----------------------------------------------------------------------------
# The last call's stdout is one --json document, of the version this script
# knows, carrying the key named.
expect_document() {
  python3 -c '
import json, sys
document = json.load(sys.stdin)
sys.exit(0 if document.get("version") == 1 and sys.argv[1] in document else 1)
' "$1" < "$RAW" 2> /dev/null \
    || fail "stdout is not one version 1 JSON document with '$1'"
}

#-----------------------------------------------------------------------------
expect_nothing_changed() {
  if grep -qE "$CHANGED_RE" "$OUT"; then
    fail "mailctl reported a change under --dry-run"

    return 1
  fi
}

#-----------------------------------------------------------------------------
# Folder names from the last `folders` output, one per line.
folder_names() {
  sed -n -e 's/^  \(.*[^ ]\)  *(not subscribed)$/\1/p; t' \
    -e 's/^  \([^ ].*\)$/\1/p' "$OUT"
}

#-----------------------------------------------------------------------------
# An existing, subscribed folder other than INBOX where there is one.
pick_folder() {
  local name

  name=$(grep -v '(not subscribed)$' "$OUT" | sed -n 's/^  \([^ ].*\)$/\1/p' \
    | grep -vix 'INBOX' | head -n 1)

  printf '%s\n' "${name:-INBOX}"
}

#-----------------------------------------------------------------------------
# Rows of the last `search` output as "UID MARK", newest first.
message_marks() {
  awk '
    /^ +UID +Received/ { col = index($0, "Mark"); next }
    col && /^ +[0-9]+  / { mark = substr($0, col, 4); gsub(/ /, "", mark)
                           print $1, mark }
  ' "$OUT"
}

#-----------------------------------------------------------------------------
# Bare address from the From: line of the last `view` output.
view_from_address() {
  local from

  from=$(sed -n 's/^  From: *//p' "$OUT" | head -n 1)

  if [[ $from =~ \<([^\>]+)\> ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"

  else
    printf '%s\n' "$from"
  fi
}

#-----------------------------------------------------------------------------
fingerprint() {
  run_mailctl show || return 1
  sha256sum < "$RAW" | cut -c1-64

  run_mailctl folders || return 1
  sha256sum < "$RAW" | cut -c1-64
}

##############################################################################
# Tests
#
# Each returns 0 to pass, 1 to fail, 2 to skip (reason in SKIP_REASON).

#-----------------------------------------------------------------------------
t_test() {
  run_mailctl test
  expect_ok test || return 1

  expect_line '^ManageSieve: connected' || return 1
  expect_line '^IMAP: connected' || return 1

  # Only the state word is pinned; how and where it was found varies with
  # the rung of the password ladder that supplied it.
  if grep -qE '^Password:  unset( |$)' "$OUT"; then
    fail "the password is unset"

    return 1
  fi

  expect_line '^Password:  set( |$)'
}

#-----------------------------------------------------------------------------
t_probe() {
  local problem

  run_mailctl probe --json
  expect_ok 'probe --json' || return 1

  problem=$(python3 -c "$PROBE_CHECK" "$RAW" 2>&1) \
    || fail "probe --json: ${problem:-not a probe document}"
}

#-----------------------------------------------------------------------------
# Only where a baseline has been saved; this never saves one. Serious drift
# fails, since that is what the check exists to catch; informational drift
# passes with its lines as diagnostics.
t_check_baseline() {
  run_mailctl show-baseline --json

  if ((RC == 1)) && grep -q 'no baseline has been saved' "$ERR"; then
    SKIP_REASON="no baseline saved ('mailctl save-baseline' records one)"

    return 2
  fi

  expect_ok 'show-baseline --json' || return 1

  run_mailctl check-baseline

  case $RC in
    0)
      expect_line '^No drift: '
      ;;

    3)
      diag 'informational drift since the baseline:'
      diag_output
      ;;

    4)
      fail 'serious drift since the baseline'
      ;;

    *)
      fail "mailctl check-baseline exited $RC"
      ;;
  esac
}

#-----------------------------------------------------------------------------
t_list() {
  local active

  run_mailctl list
  expect_ok list || return 1

  active=$(grep -c '(active)$' "$OUT")
  ((active == 1)) \
    || fail "expected one (active) line, got $active" \
    || return 1

  if grep -q '^[[:space:]]*$' "$OUT"; then
    fail "list printed a blank line"

    return 1
  fi
}

#-----------------------------------------------------------------------------
t_show() {
  local markers summary

  run_mailctl show
  expect_ok show || return 1

  expect_line '^# ---- .+ ----$' || return 1

  summary=$(
    tail -n 1 "$OUT" \
      | sed -n 's/^# ---- \([0-9][0-9]*\) rule(s): .*/\1/p'
  )
  [[ -n $summary ]] || fail "no '# ---- N rule(s)' summary line" || return 1

  markers=$(grep -c '^# rule:\[' "$OUT")

  ((summary == markers)) \
    || fail "summary says $summary rule(s), $markers marker(s) found"
}

#-----------------------------------------------------------------------------
t_rules() {
  local markers count

  run_mailctl show
  expect_ok show || return 1
  markers=$(grep -c '^# rule:\[' "$OUT")

  run_mailctl rules
  expect_ok rules || return 1

  count=$(sed -n 's/^\([0-9][0-9]*\) rule(s), in evaluation order:$/\1/p' \
    "$OUT")

  if [[ -z $count ]]; then
    grep -q '^The active script has no rules\.$' "$OUT" && count=0
  fi

  [[ -n $count ]] || fail "no rule count in rules output" || return 1

  ((count == markers)) \
    || fail "rules counts $count, show has $markers '# rule:[' marker(s)"
}

#-----------------------------------------------------------------------------
t_folders() {
  local count listed

  run_mailctl folders
  expect_ok folders || return 1

  count=$(sed -n 's/^\([0-9][0-9]*\) folder(s),.*/\1/p' "$OUT")
  [[ -n $count ]] || fail "no 'N folder(s)' line" || return 1

  listed=$(folder_names | wc -l)

  ((count == listed)) \
    || fail "header says $count folder(s), $listed listed" || return 1

  folder_names | grep -qx 'INBOX' || fail "INBOX is not listed"
}

#-----------------------------------------------------------------------------
# One call. A server without LIST-STATUS is refused by mailctl rather than
# counted a folder at a time, and that is a skip here, not a failure.
t_folder_counts() {
  local problem

  run_mailctl folders --counts --json

  if ((RC != 0)) && grep -q 'does not advertise LIST-STATUS' "$ERR"; then
    SKIP_REASON="the server does not advertise LIST-STATUS"

    return 2
  fi

  expect_ok 'folders --counts --json' || return 1

  problem=$(python3 -c "$COUNTS_CHECK" "$RAW" 2>&1) \
    || fail "folders --counts --json: ${problem:-not a counts document}"
}

#-----------------------------------------------------------------------------
t_backup() {
  local target dir written

  touch "$WORK/marker"

  run_mailctl backup --dry-run
  expect_ok 'backup --dry-run' || return 1
  expect_line '^\[dry-run\] would write .* to ' || return 1

  target=$(sed -n 's/^\[dry-run\] would write .* to \(.*\)$/\1/p' "$OUT")
  dir=$(dirname -- "$target")

  [[ ! -e $target ]] || fail "the backup file exists: $target" || return 1

  [[ -d $dir ]] || return 0

  written=$(find "$dir" -mindepth 1 -maxdepth 1 -newer "$WORK/marker" | wc -l)

  ((written == 0)) || fail "$written new file(s) in the backup directory"
}

#-----------------------------------------------------------------------------
t_search() {
  local rows

  run_mailctl search --limit 5
  expect_ok 'search --limit 5' || return 1

  rows=$(message_marks | wc -l)

  ((rows <= 5)) || fail "--limit 5 listed $rows rows"
}

#-----------------------------------------------------------------------------
# One call: the state and date filters reach the server and come back
# narrowed. The dates are not checked row by row -- SINCE compares the
# server's date, and a message near midnight can show either side of it.
t_search_unread() {
  local since rows

  since=$(date -d '30 days ago' +%F 2> /dev/null || date -v-30d +%F)

  run_mailctl search --unread --since "$since" --limit 5
  expect_ok "search --unread --since $since --limit 5" || return 1

  rows=$(message_marks | wc -l)

  ((rows <= 5)) || fail "--limit 5 listed $rows rows" || return 1

  message_marks | awk '$2 !~ /N/ { bad = 1 } END { exit bad }' \
    || fail "--unread listed a message that is not unread"
}

#-----------------------------------------------------------------------------
# One call: the largest three, largest first -- the limit is taken after
# sorting, by the server where it advertises SORT.
t_search_sort() {
  local problem

  run_mailctl search --sort size --reverse --limit 3 --json
  expect_ok 'search --sort size --reverse --limit 3 --json' || return 1

  problem=$(python3 -c "$SORT_CHECK" "$RAW" 2>&1) \
    || fail "search --sort: ${problem:-not a listing document}"
}

#-----------------------------------------------------------------------------
# One call over the last 30 days, capped by the default --max-messages: a
# mailbox busier than that is refused, which is a skip here, not a failure.
t_senders() {
  local since problem

  since=$(date -d '30 days ago' +%F 2> /dev/null || date -v-30d +%F)

  run_mailctl senders --since "$since" --top 5 --json

  if ((RC != 0)) && grep -q -- '--max-messages is' "$ERR"; then
    SKIP_REASON="more mail since $since than --max-messages allows"

    return 2
  fi

  expect_ok "senders --since $since --top 5 --json" || return 1

  problem=$(python3 -c "$SENDERS_CHECK" "$RAW" 2>&1) \
    || fail "senders --json: ${problem:-not a senders document}"
}

#-----------------------------------------------------------------------------
t_search_like() {
  local uid

  run_mailctl search --limit 1
  expect_ok 'search --limit 1' || return 1
  uid=$(message_marks | awk '{ print $1; exit }')
  need "$uid" "the folder has no messages" || return 2

  run_mailctl search --like "$uid" --limit 5
  expect_ok "search --like $uid" || return 1
  expect_line '^Criteria: ' || return 1

  # Criteria derived from a message match it, and it is the newest, so it
  # is listed unless five newer matches arrived in between.
  message_marks | awk -v uid="$uid" '$1 == uid { f = 1 } END { exit !f }' \
    || fail "uid $uid is not listed by criteria derived from it"
}

#-----------------------------------------------------------------------------
t_build_filter() {
  local uid

  run_mailctl search --limit 1
  expect_ok 'search --limit 1' || return 1
  uid=$(message_marks | awk '{ print $1; exit }')
  need "$uid" "the folder has no messages" || return 2

  run_mailctl search --like "$uid" --build-filter --json
  expect_ok "search --like $uid --build-filter --json" || return 1

  # Only the document goes to stdout; the message shown goes to stderr.
  [[ $(head -n 1 "$OUT") == '{' ]] \
    || fail "stdout holds more than the filter document" || return 1

  expect_line '^  "version": 1,$' || return 1
  expect_line '^    "terms": \[$'
}

#-----------------------------------------------------------------------------
t_json() {
  run_mailctl search --json --limit 3
  expect_ok 'search --json --limit 3' || return 1
  expect_document messages || return 1

  run_mailctl folders --json
  expect_ok 'folders --json' || return 1
  expect_document folders || return 1

  run_mailctl rules --json
  expect_ok 'rules --json' || return 1
  expect_document rules
}

#-----------------------------------------------------------------------------
t_view_keeps_unread() {
  local uid

  run_mailctl search --limit 20
  expect_ok 'search --limit 20' || return 1

  uid=$(message_marks | awk '$2 ~ /N/ { print $1; exit }')
  need "$uid" "no unread message among the newest 20" || return 2

  run_mailctl view "$uid"
  expect_ok "view $uid" || return 1

  run_mailctl search --limit 20
  expect_ok 'search --limit 20' || return 1

  message_marks \
    | awk -v uid="$uid" '$1 == uid && $2 ~ /N/ { f = 1 } END { exit !f }' \
    || fail "uid $uid is no longer marked unread (N) after view"
}

#-----------------------------------------------------------------------------
t_mark() {
  local uid before after

  run_mailctl search --limit 1
  expect_ok 'search --limit 1' || return 1
  uid=$(message_marks | awk '{ print $1; exit }')
  need "$uid" "the folder has no messages" || return 2
  before=$(message_marks | awk '{ print $2; exit }')

  run_mailctl mark --dry-run "$uid" --flag
  expect_ok "mark --dry-run $uid --flag" || return 1
  expect_nothing_changed || return 1
  expect_line '^\[dry-run\] would mark|^Nothing to change' || return 1

  # Wider than the first listing, so mail arriving meanwhile cannot push
  # the message out of it.
  run_mailctl search --limit 20
  expect_ok 'search --limit 20' || return 1
  after=$(message_marks | awk -v uid="$uid" '$1 == uid { print $2; exit }')

  [[ $after == "$before" ]] \
    || fail "uid $uid marks went from '$before' to '$after' under --dry-run"
}

#-----------------------------------------------------------------------------
t_apply() {
  local folder uid from

  run_mailctl folders
  expect_ok folders || return 1
  folder=$(pick_folder)

  run_mailctl search --limit 1
  expect_ok 'search --limit 1' || return 1
  uid=$(message_marks | awk '{ print $1; exit }')
  need "$uid" "the folder has no messages" || return 2

  run_mailctl view "$uid"
  expect_ok "view $uid" || return 1
  from=$(view_from_address)
  need "$from" "the newest message has no From address" || return 2

  run_mailctl apply --dry-run --from "$from" --fileinto "$folder"
  expect_ok 'apply --dry-run' || return 1
  expect_nothing_changed || return 1

  expect_line '^\[dry-run\]|^No existing messages match\.$'
}

#-----------------------------------------------------------------------------
t_apply_like() {
  local folder uid

  run_mailctl folders
  expect_ok folders || return 1
  folder=$(pick_folder)

  run_mailctl search --limit 1
  expect_ok 'search --limit 1' || return 1
  uid=$(message_marks | awk '{ print $1; exit }')
  need "$uid" "the folder has no messages" || return 2

  run_mailctl apply --dry-run --like "$uid" --fileinto "$folder"
  expect_ok "apply --dry-run --like $uid" || return 1
  expect_nothing_changed || return 1

  expect_line "^Message uid $uid in " || return 1
  expect_line '^\[dry-run\]|^No existing messages match\.$|^Skipping'
}

#-----------------------------------------------------------------------------
t_add() {
  local folder

  run_mailctl folders
  expect_ok folders || return 1
  folder=$(pick_folder)

  run_mailctl add --dry-run --subject 'Café' --fileinto "$folder"
  expect_ok 'add --dry-run' || return 1
  expect_nothing_changed || return 1

  expect_line '^\[dry-run\] the script was NOT uploaded\.$' || return 1
  expect_line "^Rule '[^']*café"
}

#-----------------------------------------------------------------------------
t_add_create_folder() {
  local probe="MailctlReadonlyProbe-$$"

  run_mailctl add --dry-run --from probe@example.invalid \
    --fileinto "$probe" --create-folder
  expect_ok 'add --dry-run --create-folder' || return 1
  expect_nothing_changed || return 1

  expect_line '^\[dry-run\] would create IMAP folder' || return 1

  run_mailctl folders
  expect_ok folders || return 1

  if folder_names | grep -qF -- "$probe"; then
    fail "the probe folder exists after a dry run"

    return 1
  fi
}

#-----------------------------------------------------------------------------
t_add_like() {
  local folder uid

  run_mailctl folders
  expect_ok folders || return 1
  folder=$(pick_folder)

  run_mailctl search --limit 1
  expect_ok 'search --limit 1' || return 1
  uid=$(message_marks | awk '{ print $1; exit }')
  need "$uid" "the folder has no messages" || return 2

  run_mailctl add --dry-run --like "$uid" --fileinto "$folder"
  expect_ok "add --dry-run --like $uid" || return 1
  expect_nothing_changed || return 1

  expect_line "^Message uid $uid in " || return 1
  expect_line '^\[dry-run\] the script was NOT uploaded\.$'
}

#-----------------------------------------------------------------------------
t_remove_rule() {
  local name

  run_mailctl show
  expect_ok show || return 1

  # The marker is the rule's name exactly as the script holds it. A marker
  # that is there but does not parse is a failure, not a reason to skip.
  name=$(sed -n 's/^# rule:\[\(.*\)\]$/\1/p' "$OUT" | head -n 1)

  if [[ -z $name ]] && grep -q '^# rule:\[' "$OUT"; then
    fail "show has '# rule:[' markers, but none parsed as a name"

    return 1
  fi

  need "$name" "the active script has no rules" || return 2

  run_mailctl remove-rule --dry-run "$name"
  expect_ok 'remove-rule --dry-run' || return 1
  expect_nothing_changed || return 1

  expect_line '^--- sieve diff ---$' || return 1
  expect_line '^\[dry-run\] the script was NOT uploaded\.$'
}

#-----------------------------------------------------------------------------
# Both switches on one rule: whichever state it is in, one of the two plans a
# change and the other says there is nothing to do.
t_disable_rule() {
  local name switch
  local planned='^\[dry-run\] the script was NOT uploaded\.$'

  run_mailctl show
  expect_ok show || return 1

  name=$(sed -n 's/^# rule:\[\(.*\)\]$/\1/p' "$OUT" | head -n 1)

  if [[ -z $name ]] && grep -q '^# rule:\[' "$OUT"; then
    fail "show has '# rule:[' markers, but none parsed as a name"

    return 1
  fi

  need "$name" "the active script has no rules" || return 2

  for switch in disable enable; do
    run_mailctl "$switch-rule" --dry-run "$name"
    expect_ok "$switch-rule --dry-run" || return 1
    expect_nothing_changed || return 1

    expect_line "$planned|already ${switch}d in .*; nothing to change\.$" \
      || return 1
  done
}

#-----------------------------------------------------------------------------
t_subscribe() {
  run_mailctl subscribe --dry-run INBOX
  expect_ok 'subscribe --dry-run INBOX' || return 1
  expect_nothing_changed || return 1

  expect_line 'already subscribed; nothing to change\.$|^\[dry-run\]'
}

#-----------------------------------------------------------------------------
t_create_folder() {
  local probe

  probe="MailctlReadonlyProbe-$(date -u +%Y%m%dT%H%M%S)-$$"

  run_mailctl create-folder --dry-run "$probe"
  expect_ok 'create-folder --dry-run' || return 1
  expect_nothing_changed || return 1

  expect_line '^\[dry-run\] would create IMAP folder' || return 1

  run_mailctl folders
  expect_ok folders || return 1

  if folder_names | grep -qF -- "$probe"; then
    fail "the probe folder exists after a dry run"

    return 1
  fi
}

#-----------------------------------------------------------------------------
# A real folder, renamed to a name nothing has, planned and never done.
t_rename_folder() {
  local folder probe

  run_mailctl folders
  expect_ok folders || return 1
  folder=$(pick_folder)

  [[ ${folder^^} == INBOX ]] && folder=''
  need "$folder" "no subscribed folder but INBOX to plan a rename of" \
    || return 2

  probe="MailctlReadonlyProbe-$(date -u +%Y%m%dT%H%M%S)-$$"

  run_mailctl rename-folder --dry-run "$folder" "$probe"
  expect_ok 'rename-folder --dry-run' || return 1
  expect_nothing_changed || return 1

  expect_line '^\[dry-run\] nothing was renamed'
}

#-----------------------------------------------------------------------------
# One call: the whole rule set's proposals, planned and never applied.
t_optimize_rules() {
  local problem

  run_mailctl optimize-rules --dry-run --json
  expect_ok 'optimize-rules --dry-run --json' || return 1
  expect_nothing_changed || return 1

  problem=$(python3 -c "$OPTIMIZE_CHECK" "$RAW" 2>&1) \
    || fail "optimize-rules --json: ${problem:-not a plan document}"
}

#-----------------------------------------------------------------------------
t_unchanged() {
  local now

  [[ -n $BASELINE ]] || fail "no baseline was taken" || return 1

  now=$(fingerprint) || fail "could not re-read the script and folders" \
    || return 1

  [[ $now == "$BASELINE" ]] \
    || fail "the active script or the folder list changed during the run"
}

##############################################################################
# Main

#-----------------------------------------------------------------------------
resolve_bin() {
  if [[ -n ${MAILCTL_BIN:-} ]]; then
    return 0
  fi

  if command -v mailctl > /dev/null; then
    MAILCTL_BIN=mailctl

    return 0
  fi

  local repo

  repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
  MAILCTL_BIN=$repo/.venv/bin/mailctl
}

#-----------------------------------------------------------------------------
run_one() {
  local number=$1 name=$2 status line

  DIAG=()
  SKIP_REASON=''

  "t_${name//-/_}"
  status=$?

  if ((status == 2)); then
    printf 'ok %d - %s # SKIP %s\n' "$number" "$name" "$SKIP_REASON"

  elif ((status == 0)); then
    printf 'ok %d - %s\n' "$number" "$name"

  else
    printf 'not ok %d - %s\n' "$number" "$name"
    FAILED=$((FAILED + 1))
  fi

  for line in "${DIAG[@]}"; do
    printf '# %s\n' "$line"
  done
}

#-----------------------------------------------------------------------------
main() {
  local arg name number=0
  local -a selected=() wanted=()

  if [[ ${1:-} == --selftest-guard ]]; then
    # Hidden: say whether the guard would let the rest of the argv through.
    shift
    if guard_allows "$@"; then
      echo allowed
      exit 0
    fi

    echo refused
    exit 3
  fi

  for arg in "$@"; do
    case $arg in
      -h | --help)
        usage
        exit 0
        ;;

      --list)
        printf '%s\n' "${TESTS[@]}"
        exit 0
        ;;

      -*)
        printf 'live-readonly.sh: unknown option: %s\n' "$arg" >&2
        exit 2
        ;;

      *)
        in_list "$arg" "${TESTS[@]}" || {
          printf 'live-readonly.sh: unknown test: %s (see --list)\n' \
            "$arg" >&2
          exit 2
        }
        wanted+=("$arg")
        ;;
    esac
  done

  for name in "${TESTS[@]}"; do
    if ((${#wanted[@]} == 0)) || in_list "$name" "${wanted[@]}"; then
      selected+=("$name")
    fi
  done

  resolve_bin

  if ! command -v "$MAILCTL_BIN" > /dev/null; then
    echo "Bail out! mailctl not found (set MAILCTL_BIN)"
    exit 2
  fi

  WORK=$(mktemp -d) || exit 2
  trap 'rm -rf -- "$WORK"' EXIT
  OUT=$WORK/stdout
  RAW=$WORK/stdout.raw
  ERR=$WORK/stderr
  RC=0
  FAILED=0
  BASELINE=''

  echo "1..${#selected[@]}"

  if in_list unchanged "${selected[@]}"; then
    BASELINE=$(fingerprint) || BASELINE=''
    sleep "$PAUSE"
  fi

  for name in "${selected[@]}"; do
    ((number > 0)) && sleep "$PAUSE"
    number=$((number + 1))
    run_one "$number" "$name"
  done

  ((FAILED == 0)) || exit 1
}

main "$@"
