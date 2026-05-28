# bci-analysis-orchestrator

A CO capsule that fires N parallel Reproducible Runs of
`bci-data-dict-capsule-bruker`, one per BCI session, to produce per-session
`data_dict.pkl` + figures without any manual asset attachment.

## How it fits together

```
spyder_upload.py    →  aind-data-transfer-service  →  CO raw asset
                                                         │
                                                         ▼
                                   aind-single-plane-ophys-pipeline-kd
                                                         │
                                                         ▼
                                                  CO processed asset

                  (You) click "Reproducible Run" once on THIS capsule
                                       │
                                       ▼
              run_batch.py finds today's (raw, processed) pairs and
              fires N run_capsule calls against the data-dict-capsule
                                       │
                                       ▼
              N captured "data_dict_<subject>_<date>" assets in CO
```

Fire-and-forget: this capsule launches the runs and exits. Each launched
run produces its own `/results/` directory captured as a derived asset.

## Required env vars on this capsule

Set these in the CO env editor's Environment Variables section:

- `CODEOCEAN_TOKEN` — your CO API token (cop_...). Generate at
  CO Web UI → avatar → Access Tokens.

## Optional env vars

- `DATA_DICT_CAPSULE_ID` — target capsule UUID. Default points at the
  current keeper, `12239610-4b38-4e30-8391-e52b6d89a76c`
  (bci-data-dict-capsule-bruker, slug 3591777).
- `HOURS` — auto-discovery look-back window. Default 30.
- `SESSIONS` — explicit override, format
  `"850378:2026-05-26,824468:2026-05-27:bci2"`. Comma- or newline-
  separated. If `:target_stem` is omitted, falls back to `TARGET_STEM`.
- `TARGET_STEM` — default epoch when not specified per-session.
  Default `bci`.

## Usage

Click **Reproducible Run** in CO web UI. That's it.

To override the auto-discovery for a one-off batch, set `SESSIONS` via
the env editor temporarily and re-run.

## Output

`/results/runs.json` — list of launched computations + any errors. Each
launched run also creates its own captured `data_dict_*` asset.
