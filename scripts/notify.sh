#!/bin/bash
# Push a short message to Peter's phone via ntfy.
#
#   notify.sh "title" "body" [priority] [tags]
#
# priority: min|low|default|high|urgent      tags: comma-separated emoji names
#
# The topic lives in /root/.ntfy_topic (chmod 600, NOT in git). ntfy.sh topics
# are PUBLIC to anyone who knows the name, which is why the name is random and
# why nothing sensitive goes in a message: no API keys, no telemetry, no
# tailnet addresses. Status only.
#
# Never let a notification failure break the caller - this is telemetry about
# the work, not the work.
set -u

TOPIC_FILE=/root/.ntfy_topic
[ -r "$TOPIC_FILE" ] || { echo "notify: no topic file, skipping" >&2; exit 0; }
TOPIC=$(cat "$TOPIC_FILE")

TITLE="${1:-LLMUAV}"
BODY="${2:-}"
PRIORITY="${3:-default}"
TAGS="${4:-robot}"

curl -fsS --max-time 15 \
  -H "Title: $TITLE" \
  -H "Priority: $PRIORITY" \
  -H "Tags: $TAGS" \
  -d "$BODY" \
  "https://ntfy.sh/$TOPIC" >/dev/null 2>&1 \
  && echo "notify: sent [$TITLE]" \
  || echo "notify: FAILED (ignored)" >&2

exit 0
