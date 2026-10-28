#!/usr/bin/env python3
"""split_validation_batch.py <file> <1|2> — split groups in half (allowlist-safe)."""
import json
import sys

with open(sys.argv[1]) as f:
    batch = json.load(f)
half = len(batch["groups"]) // 2
part = batch["groups"][:half] if sys.argv[2] == "1" else batch["groups"][half:]
print(json.dumps({"summary": batch.get("summary", {}), "groups": part}))
