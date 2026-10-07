#!/usr/bin/env bash
# Development server for ITC Events — http://127.0.0.1:9797
cd "$(dirname "$0")"
exec .venv/bin/python -c "from itc import create_app; create_app().run(host='127.0.0.1', port=9797, debug=True)"
