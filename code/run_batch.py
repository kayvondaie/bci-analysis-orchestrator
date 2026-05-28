"""Orchestrator: prep the data-dict-capsule for a workstation batch.

Purpose: attach all recently-uploaded BCI sessions to the data-dict-capsule
at capsule level. Does NOT run the data-dict-capsule itself.

Daily flow:
  1. Sessions auto-upload via spyder_upload.py
  2. kd pipeline auto-runs (produces processed assets)
  3. THIS orchestrator runs (auto: attaches recent assets to data-dict-capsule)
  4. You launch the data-dict-capsule cloud workstation
  5. Run explore.py — it auto-discovers attached sessions and processes them all
  6. Stop workstation -> "save results as asset" -> CO asset with all session outputs

Behavior:
  - Sweeps off all single-plane-ophys_* attachments currently on
    data-dict-capsule (clean slate)
  - Attaches every (raw, processed) pair created in the last HOURS that
    has a matching processed asset
  - Prints what was attached and the suggested workstation command

Env vars:
  CODEOCEAN_TOKEN         required — CO API token (cop_...)
  DATA_DICT_CAPSULE_ID    target capsule UUID (default: keeper)
  HOURS                   look back this many hours (default 30)
  SESSIONS                optional override list:
                            "850378:2026-05-26,824468:2026-05-27:bci2"
  TARGET_STEM             default epoch when not specified per-session (default 'bci')
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from codeocean import CodeOcean
from codeocean.data_asset import DataAssetSearchParams
from codeocean.capsule import DataAssetAttachParams

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TOKEN = os.environ.get("CODEOCEAN_TOKEN")
DOMAIN = os.environ.get("CODEOCEAN_DOMAIN", "https://codeocean.allenneuraldynamics.org")
DATA_DICT_CAPSULE_ID = os.environ.get(
    "DATA_DICT_CAPSULE_ID",
    "12239610-4b38-4e30-8391-e52b6d89a76c",  # bci-data-dict-capsule-bruker (keeper)
)
HOURS = int(os.environ.get("HOURS", "30"))
SESSIONS_RAW = os.environ.get("SESSIONS", "").strip()
DEFAULT_TARGET_STEM = os.environ.get("TARGET_STEM", "bci")

RESULTS = Path("/results")
RESULTS.mkdir(exist_ok=True)

if not TOKEN:
    print("ERROR: CODEOCEAN_TOKEN env var not set on this capsule.", file=sys.stderr)
    sys.exit(2)

client = CodeOcean(domain=DOMAIN, token=TOKEN)

print(f"{'='*70}")
print(f"BCI Analysis Orchestrator (attach-only mode)")
print(f"  Target capsule: {DATA_DICT_CAPSULE_ID}")
print(f"  Look back:      {HOURS} hours")
print(f"  SESSIONS:       {SESSIONS_RAW or '(auto-discover)'}")
print(f"  Default stem:   {DEFAULT_TARGET_STEM}")
print(f"{'='*70}")


# ---------------------------------------------------------------------------
# Session selection
# ---------------------------------------------------------------------------
def parse_sessions_override(s: str) -> list[tuple[str, str, str]]:
    tokens = re.split(r"[,\n]+", s)
    out: list[tuple[str, str, str]] = []
    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        parts = [p.strip() for p in tok.split(":")]
        if len(parts) == 2:
            out.append((parts[0], parts[1], DEFAULT_TARGET_STEM))
        elif len(parts) == 3:
            out.append((parts[0], parts[1], parts[2]))
        else:
            print(f"WARNING: skipping malformed SESSIONS entry: {tok!r}", file=sys.stderr)
    return out


def auto_discover_pairs(hours: int) -> list[tuple[str, str, str]]:
    cutoff = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp())
    results = client.data_assets.search_data_assets(DataAssetSearchParams(
        query="single-plane-ophys_8", limit=500,
    ))
    candidates = results.results
    raws = [
        a for a in candidates
        if a.name.startswith("single-plane-ophys_")
        and "_processed_" not in a.name
        and a.created >= cutoff
    ]
    procs = [a for a in candidates if "_processed_" in a.name]
    pairs: list[tuple[str, str, str]] = []
    for raw in raws:
        m = re.match(r"single-plane-ophys_(\d+)_(\d{4}-\d{2}-\d{2})_", raw.name)
        if not m:
            continue
        subject, date = m.group(1), m.group(2)
        has_proc = any(p.name.startswith(raw.name + "_processed_") for p in procs)
        if not has_proc:
            print(f"  skipping {raw.name} (no matching processed asset)")
            continue
        pairs.append((subject, date, DEFAULT_TARGET_STEM))
    return pairs


if SESSIONS_RAW:
    pairs = parse_sessions_override(SESSIONS_RAW)
    print(f"\nUsing explicit SESSIONS override: {len(pairs)} session(s)")
else:
    pairs = auto_discover_pairs(HOURS)
    print(f"\nAuto-discovered {len(pairs)} session(s) in last {HOURS} hours")

for subject, date, stem in pairs:
    print(f"  {subject}  {date}  stem={stem}")

if not pairs:
    print("\nNothing to attach.")
    sys.exit(0)


# ---------------------------------------------------------------------------
# Resolve to asset IDs
# ---------------------------------------------------------------------------
def find_asset_pair(subject: str, date: str):
    results = client.data_assets.search_data_assets(DataAssetSearchParams(
        query=f"name:single-plane-ophys_{subject}", limit=200,
    ))
    candidates = results.results
    prefix = f"single-plane-ophys_{subject}_{date}_"
    raws = [a for a in candidates if a.name.startswith(prefix) and "_processed_" not in a.name]
    procs = [a for a in candidates if a.name.startswith(prefix) and "_processed_" in a.name]

    if not raws:
        return None, None, "no raw asset found"
    if len(raws) > 1:
        return None, None, f"multiple raw assets: {[r.name for r in raws]}"
    raw = raws[0]
    matching = [p for p in procs if p.name.startswith(raw.name + "_processed_")]
    if not matching:
        return raw, None, "no processed asset matching this raw"
    matching.sort(key=lambda p: p.created, reverse=True)
    return raw, matching[0], None


resolved: list[dict] = []
errors: list[dict] = []
for subject, date, stem in pairs:
    raw, proc, err = find_asset_pair(subject, date)
    if err:
        print(f"  SKIP {subject} {date} {stem}: {err}")
        errors.append({"subject": subject, "date": date, "stem": stem, "error": err})
        continue
    resolved.append({
        "subject": subject, "date": date, "stem": stem,
        "raw_id": raw.id, "raw_name": raw.name,
        "proc_id": proc.id, "proc_name": proc.name,
    })


# ---------------------------------------------------------------------------
# Sweeping pre-detach
# ---------------------------------------------------------------------------
print(f"\nSweeping detach of all single-plane-ophys_* assets from data-dict-capsule...")
sweep_results = client.data_assets.search_data_assets(DataAssetSearchParams(
    query="single-plane-ophys_8", limit=500,
))
detach_ids = [a.id for a in sweep_results.results]
print(f"  candidates: {len(detach_ids)}")
n_detached = 0
n_skipped = 0
for asset_id in detach_ids:
    try:
        client.capsules.detach_data_assets(
            capsule_id=DATA_DICT_CAPSULE_ID,
            data_assets=[asset_id],
        )
        n_detached += 1
    except Exception as e:
        msg = str(e).lower()
        if "not attached" in msg or "404" in msg or "not found" in msg:
            n_skipped += 1
        elif "running cloud workstation" in msg:
            print(f"  ABORT: data-dict-capsule has a running workstation. Stop it and re-run.")
            sys.exit(5)
        else:
            print(f"  detach warning for {asset_id}: {e}")
print(f"  detached {n_detached}, already-detached {n_skipped}")


# ---------------------------------------------------------------------------
# Attach the N (raw, proc) pairs at capsule level
# ---------------------------------------------------------------------------
all_attach_params = []
for r in resolved:
    all_attach_params.append(DataAssetAttachParams(id=r["raw_id"], mount=r["raw_name"]))
    all_attach_params.append(DataAssetAttachParams(id=r["proc_id"], mount=r["proc_name"]))

print(f"\nAttaching {len(all_attach_params)} assets to data-dict-capsule (capsule level)...")
if all_attach_params:
    try:
        client.capsules.attach_data_assets(
            capsule_id=DATA_DICT_CAPSULE_ID,
            attach_params=all_attach_params,
        )
        print(f"  attached {len(all_attach_params)} assets")
    except Exception as e:
        print(f"  attach error: {e}")
        sys.exit(6)


# ---------------------------------------------------------------------------
# Summary + workstation instructions
# ---------------------------------------------------------------------------
print(f"\n{'='*70}")
print(f"Done. {len(resolved)} session(s) attached to data-dict-capsule.")
print(f"{'='*70}")
print(f"\nNext step — launch the data-dict-capsule cloud workstation:")
print(f"  https://codeocean.allenneuraldynamics.org/capsule/3591777/tree")
print(f"\nIn the workstation, open code/explore.py and Shift+Enter through")
print(f"CELLs 1-3. It will auto-discover the attached sessions and process")
print(f"each one into /results/<subject>_<date>_<stem>/.")
print(f"\nWhen done, stop the workstation and capture /results/ as a CO asset.")

summary = {
    "attached": resolved,
    "errors": errors,
    "ran_at": datetime.now(timezone.utc).isoformat(),
}
with open(RESULTS / "attached.json", "w") as f:
    json.dump(summary, f, indent=2)
print(f"\nDetails in /results/attached.json")
