<!-- molds
id: supawati-9
stage: qa
resume_stage: 
parent: 
deps: 
priority: P2
created_at: 2026-04-23T18:38:34Z
updated_at: 2026-04-23T18:52:04Z
closed_at: 
defer_until: 
code_branch: 
-->



# Implement the logs CLI command

## TL;DR

- Replace the `logs` placeholder with a command that reads and displays log files from the `logs/` directory
- No subcommands — just `wati-supabase logs` lists files, optional `--file` to display a specific log

## Problem

The `backfill` command writes output to `logs/backfill_<tenant_id>_<timestamp>.log` but the `logs` CLI command just prints "not implemented". The user has to manually navigate to the `logs/` directory.

## Solution

Replace the `logs` placeholder (line 90) with a `handle_logs` handler:

1. **Default (no args)**: list all `.log` files in `logs/` sorted by modification time (newest first), showing filename and size
2. **`--file <name>`**: print the full contents of the specified log file
3. **`--tail`**: used with `--file`, only show the last 50 lines
4. If `logs/` doesn't exist or is empty, print `"No log files found."`

```
$ wati-supabase logs
Log files (newest first):
  backfill_42_20260423_143000.log  (12.3 KB)
  backfill_42_20260422_100000.log  (8.1 KB)

$ wati-supabase logs --file backfill_42_20260423_143000.log
<full file contents>

$ wati-supabase logs --file backfill_42_20260423_143000.log --tail
<last 50 lines>
```

## Files to Modify
| File | Action | Done | Purpose |
|------|--------|---------|
| `scripts/wati_supabase_cli.py` | Modify | yes | Replace `logs` placeholder with `handle_logs` handler, add `--file` and `--tail` args |

## Verification

- `wati-supabase logs` lists log files when they exist
- `wati-supabase logs` prints "No log files found." when `logs/` is empty or missing
- `wati-supabase logs --file <name>` displays the file contents
- `wati-supabase logs --file <name> --tail` shows last 50 lines
- `wati-supabase logs --help` shows the available options

## Implementation Notes

- Log dir is `Path(__file__).resolve().parent.parent / "logs"` (same as in `handle_backfill_messages`)
- Use `sorted(log_dir.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)` for newest-first listing
- Format file sizes as human-readable (KB/MB)
