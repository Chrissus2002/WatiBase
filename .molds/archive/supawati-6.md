<!-- molds
id: supawati-6
stage: closed
resume_stage: 
parent: 
deps: 
priority: P2
created_at: 2026-04-22T23:14:49Z
updated_at: 2026-04-23T00:04:24Z
closed_at: 2026-04-23T00:04:24Z
defer_until: 
code_branch: 
-->




# Run backfill messages in background process

## TL;DR

- The `backfill` command currently blocks the terminal for the entire run (fetching messages for every contact, upserting to staging, promoting to canonical)
- Make it spawn a background process so the user gets their terminal back immediately, with a PID they can use to kill it if needed

## Problem

`backfill --tenant-id <id>` can take a long time — it fetches messages for every contact with a 2-second sleep between each. The CLI blocks until completion, tying up the user's terminal.

## Solution

Use `subprocess.Popen` to re-run the CLI script as a child process with a hidden `--_background` flag. The parent prints the PID and returns immediately; the child runs the backfill synchronously with output redirected to a log file.

### CLI changes in `scripts/wati_supabase_cli.py`

1. Replace `import multiprocessing` with `import subprocess`
2. Add hidden `--_background` flag to backfill parser (`help=argparse.SUPPRESS`)
3. Add `_check_existing_backfill(tenant_id)` — queries `ops.import_jobs` for `status='running'` + `job_type='message_backfill'`, checks if PID is alive via `os.kill(pid, 0)`. Returns the job dict if alive, otherwise marks stale jobs as failed and returns None
4. Rewrite `handle_backfill_messages`:
   - If `--_background` flag is set → run synchronously (current blocking behavior, this is the child)
   - Otherwise (parent):
     - Call `_check_existing_backfill()` — refuse if one is already running
     - Create `logs/` dir on demand
     - Spawn child via `subprocess.Popen` with:
       - Command: `sys.executable scripts/wati_supabase_cli.py backfill --tenant-id X --_background [--limit Y]`
       - `stdout`/`stderr` → `logs/backfill_<tenant_id>_<timestamp>.log`
       - `start_new_session=True` (Ctrl-C won't kill child)
       - `env` with `PYTHONPATH` set to `shared/`
     - Print PID, log path, monitoring and kill instructions

### PID tracking in `shared/batch_message_import.py`

After creating the import job row, store `os.getpid()` into `subprocess_pid` on the job row. This makes `status --tenant-id` show the PID.

### Pre-existing bug fix in `shared/batch_message_import.py`

Line 70 has `phon2e` instead of `phone` — fix this typo.

### User experience

```
$ wati-supabase backfill --tenant-id 42
Backfill started in background (PID 12345).
Log: logs/backfill_42_20260422_143000.log
Monitor: wati-supabase status --tenant-id 42
Stop:    kill 12345
$

$ wati-supabase backfill --tenant-id 42
Backfill already running for tenant 42 (PID 12345).
Use 'kill 12345' to stop it, or wait for it to finish.
```

## Files to Modify
| File | Action | Done | Purpose |
|------|--------|---------|
| `scripts/wati_supabase_cli.py` | Modify | yes | Replace `multiprocessing` with `subprocess`, add `--_background` flag, add `_check_existing_backfill()`, rewrite `handle_backfill_messages` with parent/child logic |
| `shared/batch_message_import.py` | Modify | yes | Add `import os`, store `os.getpid()` in `subprocess_pid` after job creation |

## Verification

- `backfill --tenant-id <id>` returns immediately with PID and log path
- Log file is created in `logs/` and receives backfill output
- `status --tenant-id <id>` shows the running job with PID
- Running `backfill` again for the same tenant refuses with a message showing the existing PID
- `kill <pid>` stops the background process
- `backfill --help` does NOT show `--_background` (hidden)

## Implementation Notes

- `subprocess.Popen` is preferred over `multiprocessing.Process` because the module-level client initialization in `wati_supabase_cli.py` would re-execute in a spawned process. `Popen` re-runs the script cleanly as a separate interpreter
- `start_new_session=True` gives the child its own process group so Ctrl-C doesn't kill it
- `PYTHONPATH` must be set explicitly in the `Popen` `env` since the child isn't launched via the `wati-supabase` shell wrapper
- Use `time.strftime` for the log filename timestamp (`time` is already imported)
- `*.log` is already in `.gitignore` — no gitignore changes needed
- No automatic log cleanup — user can `rm logs/*.log` manually
