"""Orchestrator: process N BCI sessions in ONE Reproducible Run of the
data-dict-capsule.

Why one run, not N: each Reproducible Run mounts its data assets from
cold cache (the slow "Attaching Data Assets" step). N runs = N mount
penalties. One run with N pairs attached at capsule level pays the mount
cost once for all assets, then the run script loops through sessions
internally.

For each session (auto-discovered or explicitly listed), this:
  1. Sweeps existing capsule-level attachments off the data-dict-capsule
  2. Attaches all (raw, processed) pairs at capsule level (instant — just
     a metadata update)
  3. Launches ONE Reproducible Run of the data-dict-capsule with
     SESSIONS=<comma-separated list> as a positional parameter
  4. The data-dict-capsule's code/run loops over SESSIONS, calling
     run_session.py for each. Outputs land in /results/<sess>/.

Captured asset contains all per-session outputs (data_dict.pkl + figures
+ run_log.txt under <subject>_<date>_<stem>/ subdirs).

Env vars:
  CODEOCEAN_TOKEN         required — CO API token (cop_...)
  DATA_DICT_CAPSULE_ID    target capsule UUID
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
from codeocean.computation import RunParams

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TOKEN = os.environ.get("CODEOCEAN_TOKEN")
DOMAIN = os.environ.get("CODEOCEAN_DOMAIN", "https://codeocean.allenneuraldynamics.org")
DATA_DICT_CAPSULE_ID = os.environ.get(
    "DATA_DICT_CAPSULE_ID",
    "12239610-4b38-4e30-8391-e52b6d89a76c",  # bci-data-dict-capsule-bruker (keeper, slug 3591777)
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
print(f"BCI Analysis Orchestrator (single-run batch mode)")
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
            subject, date = parts
            stem = DEFAULT_TARGET_STEM
        elif len(parts) == 3:
            subject, date, stem = parts
        else:
            print(f"WARNING: skipping malformed SESSIONS entry: {tok!r}", file=sys.stderr)
            continue
        out.append((subject, date, stem))
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
    print("\nNothing to do.")
    sys.exit(0)


# ---------------------------------------------------------------------------
# Asset lookup per session (raw + processed IDs)
# ---------------------------------------------------------------------------
def find_asset_pair(subject: str, date: str):
    """Return (raw_asset, processed_asset) or (raw, None, err) or (None, None, err)."""
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


# Resolve all pairs to asset IDs
resolved: list[dict] = []
errors: list[dict] = []
for subject, date, stem in pairs:
    raw, proc, err = find_asset_pair(subject, date)
    if err:
        print(f"\n--- {subject} {date} {stem} ---  SKIP: {err}")
        errors.append({"subject": subject, "date": date, "stem": stem, "error": err})
        continue
    resolved.append({
        "subject": subject, "date": date, "stem": stem,
        "raw_id": raw.id, "raw_name": raw.name,
        "proc_id": proc.id, "proc_name": proc.name,
    })
    print(f"  resolved {subject} {date} {stem}: raw={raw.name[:50]}..., proc={proc.name[:50]}...")

if not resolved:
    print("\nNo sessions resolved to asset IDs.")
    summary_path = RESULTS / "runs.json"
    with open(summary_path, "w") as f:
        json.dump({"launched": [], "errors": errors, "ran_at": datetime.now(timezone.utc).isoformat()}, f, indent=2)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Sweeping pre-detach (clean slate on data-dict-capsule)
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
# Attach all (raw, proc) pairs at CAPSULE LEVEL (instant metadata update,
# and the one upcoming Reproducible Run mounts them all together so we pay
# the fuse-mount cost once instead of N times).
# ---------------------------------------------------------------------------
print(f"\nAttaching {2 * len(resolved)} assets to data-dict-capsule (capsule level)...")
all_attach_params = []
for r in resolved:
    all_attach_params.append(DataAssetAttachParams(id=r["raw_id"], mount=r["raw_name"]))
    all_attach_params.append(DataAssetAttachParams(id=r["proc_id"], mount=r["proc_name"]))
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
# Launch ONE Reproducible Run with SESSIONS env var (passed as positional arg)
# ---------------------------------------------------------------------------
sessions_str = ",".join(f"{r['subject']}:{r['date']}:{r['stem']}" for r in resolved)
print(f"\nLaunching ONE Reproducible Run on data-dict-capsule...")
print(f"  SESSIONS param = {sessions_str}")

try:
    # NOTE: data-dict-capsule's /code/run treats $1 as the SESSIONS list
    # (comma-separated). It loops internally and calls run_session.py per
    # entry, dropping outputs into /results/<subject>_<date>_<stem>/.
    params = RunParams(
        capsule_id=DATA_DICT_CAPSULE_ID,
        parameters=[sessions_str],
    )
    comp = client.computations.run_capsule(params)
    print(f"  LAUNCHED computation id={comp.id}")
    print(f"\nAll {len(resolved)} sessions will be processed in this one container.")
    print(f"Watch progress in the data-dict-capsule's run history.")
except Exception as e:
    print(f"  LAUNCH FAILED: {e}")
    errors.append({"error": f"launch failed: {e}", "sessions": sessions_str})


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
summary = {
    "launched": [{"computation_id": comp.id if 'comp' in dir() else None,
                  "sessions": sessions_str,
                  "resolved": resolved}],
    "errors": errors,
    "ran_at": datetime.now(timezone.utc).isoformat(),
}
summary_path = RESULTS / "runs.json"
with open(summary_path, "w") as f:
    json.dump(summary, f, indent=2)
print(f"\n{'='*70}")
print(f"Orchestrator done. Wrote {summary_path}")
print(f"{'='*70}")
