# Research Copy Status

Copy date: 2026-10-18

This is an isolated copy for offline research. The baseline project remains at:
`C:\Users\Priyanka\Desktop\ai-trading-bot - Copy\ai-tarding-assistant`

The copy preserves durable source, tests, documentation, datasets, historical reports, and existing research artifacts. `.env` was omitted because it contains credentials; `.env.example` contains blank credential fields. Do not commit or share secrets.

## Environment reconciliation (2026-10-18)

The Stage A audit found a `.venv` directory in this copy, contrary to the original copy note below. Its dependency availability was checked without running project code: the declared runtime and test packages are present. The environment is machine-specific and is excluded from Git; the prior statement that it was omitted describes the intended copy process, not the current observed filesystem. No `.env` exists in this research copy. The original project's `.env` was not accessed.

No persistent SQLite database was copied. The SQLite ledger defaults to this copy's `logs/paper_state.sqlite3`; that directory was not copied and any future runtime state will be created under this research copy. Python sources contain no hard-coded path to the original checkout. Some research outputs are relative to the working directory, so run future research only with this directory as the working directory and inspect each script's output path first.

The present `.venv` is machine-local and excluded from Git. If it is removed or unusable, recreate it from this directory with:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

No dependencies were installed and no runner, market API, or historical analysis was started for this copy during Stage A/B. Git was initialized in this research copy only; `.env`, `.venv`, logs, large raw equity candle CSVs, duplicate raw snapshot data, and `complete.json` are ignored. Existing research files and reports remain on disk. Keep the project paper-only; real broker order placement, modification, and cancellation must remain disabled. Passing tests or matching historical reports does not establish profitability.
