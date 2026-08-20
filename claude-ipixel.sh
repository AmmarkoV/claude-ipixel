#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

$DIR/venv/bin/python $DIR/service.py "$@"

exit 0
