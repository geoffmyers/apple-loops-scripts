#!/bin/bash
#
# Reconvert EVERY Splice sample, not just the new ones.
#
# Use this once after updating the toolkit, or if loops are not showing up
# under the right instrument or genre in Logic's Loop Browser. It rewrites
# every .caf this tool has made, which takes a few minutes for a large
# library. Your Splice folder is never touched.
#
# For everyday use, run "Update Apple Loops from Splice" instead.

SELF="${BASH_SOURCE[0]}"
while [ -L "$SELF" ]; do
    LINK="$(readlink "$SELF")"
    case "$LINK" in
        /*) SELF="$LINK" ;;
        *)  SELF="$(dirname "$SELF")/$LINK" ;;
    esac
done
HERE="$(cd "$(dirname "$SELF")" && pwd)"

exec "$HERE/Update Apple Loops from Splice.command" --rebuild
