#!/usr/bin/env bash
cd "$(dirname "$0")"
uv run --no-project --python 3.12 --with-requirements requirements.txt -- \
  streamlit run fahrtenplaner/app.py --server.address localhost --server.runOnSave true
