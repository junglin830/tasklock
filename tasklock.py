#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tasklock — collision-free task IDs for a shared tasks.json used by several AI agents at once.

Problem
    Several coding agents (Claude Code, Codex, Cursor, scripts...) share one tasks.json.
    Each one reads the file, computes "max ID + 1" and writes the whole file back.
    With no lock, two agents running seconds apart get the same ID, or one silently
    overwrites the other's change.

Fix
    Wrap read -> compute next ID -> write in a single critical section:
      * Windows : a kernel named Mutex (released by the OS if the process crashes).
      * POSIX   : fcntl.flock on a lock file (also released automatically on crash).
    Writes go to a temp file and are swapped in with os.replace (atomic on one volume),
    so readers never see a half-written file.

Usage
    python tasklock.py add --proj web --title "Fix login" --note "..." --accept "..."
    python tasklock.py update --id T12 --status done
    python tasklock.py list --status active
    python tasklock.py dedupe --dry-run      # repair existing duplicate IDs

    File path: --file PATH, or env TASKLOCK_FILE, default ./tasks.json
    ID prefix: env TASKLOCK_PREFIX (default "T")

Pure standard library, Python 3.8+. MIT License.
"""
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime

LOCK_TIMEOUT = float(os.environ.get("TASKLOCK_TIMEOUT", "70"))
PREFIX = os.environ.get("TASKLOCK_PREFIX", "T")


class LockError(RuntimeError):
    pass


class TaskLock:
    """Cross-process lock bound to one tasks file. Use as a context manager."""

    def __init__(self, path, timeout=LOCK_TIMEOUT):
        self.path = os.path.abspath(path)
        self.timeout = timeout
        self._h = None
        self._fd = None

    def __enter__(self):
        if os.name == "nt":
            import ctypes
            k = ctypes.windll.kernel32
            k.CreateMutexW.restype = ctypes.c_void_p
            k.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            k.ReleaseMutex.argtypes = [ctypes.c_void_p]
            k.CloseHandle.argtypes = [ctypes.c_void_p]
            name = "Local\\tasklock_" + hashlib.sha1(
                os.path.normcase(self.path).encode("utf-8")).hexdigest()[:16]
            h = k.CreateMutexW(None, False, name)
            if not h:
                raise LockError("cannot create mutex")
            r = k.WaitForSingleObject(h, int(self.timeout * 1000))
            if r not in (0, 0x80):  # WAIT_OBJECT_0 / WAIT_ABANDONED
                k.CloseHandle(h)
                raise LockError("timed out after {}s waiting for lock on {}".format(self.timeout, self.path))
            self._h = h
        else:
            import fcntl
            self._fd = os.open(self.path + ".lock", os.O_CREAT | os.O_RDWR, 0o644)
            start = time.time()
            while True:
                try:
                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.time() - start > self.timeout:
                        os.close(self._fd)
                        raise LockError("timed out after {}s waiting for lock on {}".format(self.timeout, self.path))
                    time.sleep(0.05)
        return self

    def __exit__(self, *exc):
        if self._h:
            import ctypes
            k = ctypes.windll.kernel32
            k.ReleaseMutex(ctypes.c_void_p(self._h))
            k.CloseHandle(ctypes.c_void_p(self._h))
            self._h = None
        if self._fd is not None:
            import fcntl
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None
        return False


def load(path):
    if not os.path.exists(path):
        return {"tasks": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save(path, data):
    tmp = "{}.tmp-{}".format(path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _num(tid):
    tid = str(tid)
    return int(tid[len(PREFIX):]) if tid.startswith(PREFIX) and tid[len(PREFIX):].isdigit() else 0


def next_id(data):
    return "{}{}".format(PREFIX, max([_num(t.get("id")) for t in data.get("tasks", [])] or [0]) + 1)


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def add_task(path, **fields):
    """Atomically append a task and return its new unique ID."""
    with TaskLock(path):
        data = load(path)
        tid = next_id(data)
        task = {"id": tid, "created_at": _today()}
        task.update({k: v for k, v in fields.items() if v is not None})
        if task.get("status") == "done":
            task.setdefault("done_at", _today())
        data.setdefault("tasks", []).insert(0, task)  # newest first
        save(path, data)
        return tid


def update_task(path, tid, **fields):
    with TaskLock(path):
        data = load(path)
        for t in data.get("tasks", []):
            if t.get("id") == tid:
                for k, v in fields.items():
                    if v is not None:
                        t[k] = v
                if fields.get("status") == "done":
                    t.setdefault("done_at", _today())
                save(path, data)
                return t
        raise KeyError(tid)


def dedupe(path, dry_run=False):
    """Give fresh IDs to later duplicates. Newest-first order means the copy with the
    largest index was written first and keeps the original ID."""
    with TaskLock(path):
        data = load(path)
        tasks = data.get("tasks", [])
        groups = {}
        for i, t in enumerate(tasks):
            groups.setdefault(t.get("id"), []).append(i)
        n = max([_num(t.get("id")) for t in tasks] or [0]) + 1
        mapping = []
        for tid in sorted((k for k, v in groups.items() if len(v) > 1), key=_num):
            idxs = sorted(groups[tid], reverse=True)
            for i in idxs[1:]:
                new = "{}{}".format(PREFIX, n)
                n += 1
                mapping.append({"old_id": tid, "new_id": new, "index": i, "title": tasks[i].get("title", "")})
                tasks[i]["id"] = new
        if mapping and not dry_run:
            save(path, data)
        return mapping


def main(argv=None):
    p = argparse.ArgumentParser(prog="tasklock", description="Collision-free tasks.json for multiple AI agents")
    p.add_argument("--file", default=os.environ.get("TASKLOCK_FILE", "tasks.json"))
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="add a task with a guaranteed-unique ID")
    a.add_argument("--title", required=True)
    for f in ("proj", "note", "accept", "owner", "type"):
        a.add_argument("--" + f)
    a.add_argument("--status", default="active")
    a.add_argument("--prio", default="P3")

    u = sub.add_parser("update", help="update fields of one task")
    u.add_argument("--id", required=True)
    for f in ("title", "proj", "note", "accept", "owner", "type", "status", "prio"):
        u.add_argument("--" + f)

    l = sub.add_parser("list", help="list tasks")
    l.add_argument("--status")
    l.add_argument("--json", action="store_true")

    d = sub.add_parser("dedupe", help="repair duplicate IDs already in the file")
    d.add_argument("--dry-run", action="store_true")

    args = p.parse_args(argv)
    path = os.path.abspath(args.file)
    try:
        if args.cmd == "add":
            print(add_task(path, title=args.title, proj=args.proj, note=args.note, accept=args.accept,
                           owner=args.owner, type=args.type, status=args.status, prio=args.prio))
        elif args.cmd == "update":
            update_task(path, args.id, title=args.title, proj=args.proj, note=args.note, accept=args.accept,
                        owner=args.owner, type=args.type, status=args.status, prio=args.prio)
            print(args.id)
        elif args.cmd == "list":
            ts = [t for t in load(path).get("tasks", []) if not args.status or t.get("status") == args.status]
            if args.json:
                print(json.dumps(ts, ensure_ascii=False, indent=2))
            else:
                for t in ts:
                    print("{:<6} {:<8} {:<4} {}".format(t.get("id", ""), t.get("status", ""), t.get("prio", ""), t.get("title", "")))
        elif args.cmd == "dedupe":
            m = dedupe(path, args.dry_run)
            print(json.dumps({"renumbered": len(m), "dry_run": args.dry_run, "mapping": m}, ensure_ascii=False, indent=2))
    except LockError as e:
        print("ERROR: {}".format(e), file=sys.stderr)
        return 2
    except KeyError as e:
        print("ERROR: task not found: {}".format(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
