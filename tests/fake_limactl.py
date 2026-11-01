#!/usr/bin/env python3
"""Integration-test fake: never invokes Lima or creates a VM."""
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The vcluster-lima-nodeprovider contributors
import json
import os
from pathlib import Path
import sys

if "FAKE_LIMA_STATE" not in os.environ:
    raise SystemExit("FAKE_LIMA_STATE must point to a temporary test directory")
root = Path(os.environ["FAKE_LIMA_STATE"])
root.mkdir(parents=True, exist_ok=True)
args = [arg for arg in sys.argv[1:] if arg != "--tty=false"]
with (root.parent / "calls.jsonl").open("a") as stream:
    json.dump(args, stream)
    stream.write("\n")
if os.environ.get("FAKE_LIMA_FAIL") == args[0]:
    print("synthetic-secret-must-not-leak", file=sys.stderr)
    raise SystemExit(1)
if args[0] == "list":
    if "--format=json" in args:
        if len(args) > 2:
            # Lima 2.1 returns an error for a specifically named missing VM.
            if not (root / args[-1]).exists():
                raise SystemExit(1)
            files = [root / args[-1]]
        else:
            files = sorted(root.iterdir())
        records = [{"name": file.name, "status": "Running"} for file in files]
        if os.environ.get("FAKE_LIMA_ARRAY"):
            print(json.dumps(records))
        else:
            for record in records:
                print(json.dumps(record))
    else:
        for file in root.iterdir():
            print(file.name)
elif args[0] == "start":
    name = next((arg.split("=", 1)[1] for arg in args if arg.startswith("--name=")), args[1])
    (root / name).touch()
elif args[0] == "delete":
    (root / args[-1]).unlink(missing_ok=True)
elif args[0] == "shell":
    if "-c" in args:
        # Exercise the real HTTP/JSON transport without executing guest commands.
        payload = json.load(sys.stdin)
        if os.environ.get("FAKE_LIMA_FAIL") == "bootstrap":
            raise SystemExit(1)
        if not isinstance(payload.get("user_data"), str):
            raise SystemExit(1)
else:
    raise SystemExit("Unsupported fake command")
