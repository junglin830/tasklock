# tasklock

**Collision-free task IDs for a shared `tasks.json` when several AI coding agents work at the same time.**
多個 AI agent 同時共用一份 `tasks.json` 時，保證任務編號不撞號、不互相覆蓋。

## Install

```bash
pip install git+https://github.com/junglin830/tasklock
tasklock --help
```

Or just copy the single `.py` file — it has no dependencies.

## Why / 為什麼

If you run Claude Code, Codex, Cursor or your own scripts side by side on one repo, they often share a task list.
Each agent reads the file, takes "max ID + 1" and writes the whole file back. Without a lock, agents that start a few
seconds apart get **the same ID**, or one overwrites the other's change. We hit this in production
(several T422–T426 pairs pointing at different projects).

同時跑好幾個 agent 時，每個都「讀檔 → 自己算最大號 +1 → 整份寫回」，沒有鎖就會撞號或互蓋。這是我們實際踩過的問題。

Proof — 40 processes adding a task at once (`tests/test_concurrency.py`):

| | Result |
|---|---|
| Without the lock | `T1, T1, T2, T6, T6, … T18, T18, T18, T18` — many duplicates |
| With tasklock | 40 adds → 40 unique IDs (tested on Linux and Windows) |

## How it works / 原理

- **Windows:** a kernel named Mutex. If an agent crashes, the OS releases it — no stale lock files.
- **Linux / macOS:** `fcntl.flock`, also released automatically when the process dies.
- Writes go to a temp file, then `os.replace` swaps it in atomically, so nobody reads a half-written file.
- One file, Python standard library only, Python 3.8+.

## Usage / 用法

```bash
python tasklock.py add --title "Fix login" --proj web --note "details" --accept "test passes"
# -> T13   (get the ID first, then write it into your docs)

python tasklock.py update --id T13 --status done      # adds done_at automatically
python tasklock.py list --status active
python tasklock.py dedupe --dry-run                   # repair duplicate IDs already in the file
```

Options: `--file PATH` (or env `TASKLOCK_FILE`, default `./tasks.json`), env `TASKLOCK_PREFIX` (default `T`),
env `TASKLOCK_TIMEOUT` seconds (default 70).

As a library:

```python
from tasklock import add_task, update_task
tid = add_task("tasks.json", title="Write tests", owner="codex")
update_task("tasks.json", tid, status="done")
```

### Tip for agent instructions (CLAUDE.md / AGENTS.md)

> Never edit `tasks.json` by hand to add tasks. Run `python tasklock.py add ...`, take the printed ID, then use it.

## File format

```json
{ "tasks": [ { "id": "T13", "title": "Fix login", "status": "active", "prio": "P3", "created_at": "2026-10-22" } ] }
```

New tasks are inserted at the top (newest first). `dedupe` relies on that order: for a duplicated ID, the copy
lowest in the list was written first and keeps the ID; later copies get fresh IDs.

## Test

```bash
python tests/test_concurrency.py
```

## License

MIT
