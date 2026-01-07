#!/bin/bash

TOPLEVEL="$(git rev-parse --show-toplevel)"
LOGS_DIR="$TOPLEVEL/logs"
if [ -d $LOGS_DIR ]; then
    echo "Clearing logs in $LOGS_DIR ..."
    rm -rf $LOGS_DIR
    echo "Logs cleared."
else
    echo "Logs directory $LOGS_DIR does not exist. Nothing to clear."
fi