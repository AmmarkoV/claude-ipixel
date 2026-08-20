#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

$DIR/venv/bin/python $DIR/service.py --address 5B:18:0C:7E:39:FB --interval 420

exit 0
