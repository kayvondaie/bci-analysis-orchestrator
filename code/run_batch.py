"""Orchestrator: fire N data-dict-capsule runs, one per BCI session.

For each session (auto-discovered or explicitly listed), this:
  1. Looks up the raw + processed CO data assets by name
  2. Calls client.computations.run_capsule(...) against the data-dict-capsule
     with those assets attached per-run and SUBJECT/DATE/TARGET_STEM passed
     as parameters
  3. Prints the launched computation id

Fire-and-forget — does NOT wait for the launched computations. Captured
/results/ assets ("data_dict_<subject>_<date>") appear in CO as each
individual run finishes.

Env vars:
  CODEOCEAN_TOKEN         required — CO API token (cop_...)
  DATA_DICT_CAPSULE_ID    target capsule UUID (default: bci-data-dict-capsule-bruker keeper)
  HOURS                   look back this many hours (default 30)
  SESSIONS                optional override list:
                            "850378:2026-05-26,824468:2026-05-27:bci2"
                          (subject:date[:target_stem], comma- or newline-separated)
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
from codeocean.computation import RunParams, DataAssetsRunParam, NamedRunParam

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
    print("       Add it via the env editor's Environment Variables section.",
          file=sys.stderr)
    sys.exit(2)

client = CodeOcean(domain=DOMAIN, token=TOKEN)

print(f"{'='*70}")
print(f"BCI Analysis Orchestrator")
print(f"  Target capsule: {DATA_DICT_CAPSULE_ID}")
print(f"  Look back:      {HOURS} hours")
print(f"  SESSIONS:       {SESSIONS_RAW or '(auto-discover)'}")
print(f"  Default stem:   {DEFAULT_TARGET_STEM}")
print(f"{'='*70}")


# ---------------------------------------------------------------------------
# Session selection
# ---------------------------------------------------------------------------
def parse_sessions_override(s: str) -> list[tuple[str, str, str]]:
    """Parse 'subject:date[:target_stem],...' into [(subject, date, target_stem), ...].

    Splits on commas and/or newlines. Whitespace is stripped per token.
    """
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
    """Find raw assets created in the last `hours` that have a matching processed asset.

    Returns [(subject, date, target_stem), ...] using DEFAULT_TARGET_STEM.
    """
    cutoff = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp())

    # Search broadly; AIND BCI subject IDs all start with 8.
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
        # Parse subject + date from raw name: "single-plane-ophys_<subj>_<YYYY-MM-DD>_..."
        m = re.match(r"single-plane-ophys_(\d+)_(\d{4}-\d{2}-\d{2})_", raw.name)
        if not m:
            continue
        subject, date = m.group(1), m.group(2)

        # Confirm there's a matching processed asset (auto-capture pattern).
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
# Asset lookup per session
# ---------------------------------------------------------------------------
def find_asset_pair(subject: str, date: str):
    """Return (raw_asset, processed_asset) IDs or (None, None) if not found.

    Picks the most recently-created processed asset that prefix-matches the raw.
    """
    # Search by subject only — CO's search doesn't tokenize hyphenated dates,
    # so 'name:single-plane-ophys_850381_2026-05-27' returns 0 even though the
    # bare subject does match. Filter by date in Python below.
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

    # Filter procs to those that prefix-match the chosen raw, pick most recent.
    matching = [p for p in procs if p.name.startswith(raw.name + "_processed_")]
    if not matching:
        return raw, None, "no processed asset matching this raw"
    matching.sort(key=lambda p: p.created, reverse=True)
    return raw, matching[0], None


# ---------------------------------------------------------------------------
# Fire the runs
# ---------------------------------------------------------------------------
launched: list[dict] = []
errors: list[dict] = []

for subject, date, stem in pairs:
    print(f"\n--- {subject} {date} {stem} ---")
    raw, proc, err = find_asset_pair(subject, date)
    if err:
        print(f"  SKIP: {err}")
        errors.append({"subject": subject, "date": date, "stem": stem, "error": err})
        continue
    print(f"  raw:  {raw.name}  ({raw.id})")
    print(f"  proc: {proc.name}  ({proc.id})")

    try:
        params = RunParams(
            capsule_id=DATA_DICT_CAPSULE_ID,
            data_assets=[
                DataAssetsRunParam(id=raw.id, mount=raw.name),
                DataAssetsRunParam(id=proc.id, mount=proc.name),
            ],
            named_parameters=[
                NamedRunParam(param_name="SUBJECT", value=subject),
                NamedRunParam(param_name="DATE", value=date),
                NamedRunParam(param_name="TARGET_STEM", value=stem),
            ],
        )
        comp = client.computations.run_capsule(params)
        print(f"  LAUNCHED computation id={comp.id}")
        launched.append({
            "subject": subject, "date": date, "stem": stem,
            "computation_id": comp.id,
            "raw_id": raw.id, "raw_name": raw.name,
            "proc_id": proc.id, "proc_name": proc.name,
        })
    except Exception as e:
        print(f"  FAIL: {e}")
        errors.append({"subject": subject, "date": date, "stem": stem, "error": str(e)})


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n{'='*70}")
print(f"Orchestrator done.")
print(f"  Launched: {len(launched)}")
print(f"  Errors:   {len(errors)}")
print(f"{'='*70}")

summary_path = RESULTS / "runs.json"
with open(summary_path, "w") as f:
    json.dump({
        "launched": launched,
        "errors": errors,
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }, f, indent=2)
print(f"\nWrote {summary_path}")

if errors:
    sys.exit(1)
