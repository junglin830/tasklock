"""Spawn many processes that add tasks at the same time; every ID must be unique."""
import json, os, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "..", "tasklock.py")


def test_parallel_add(n=40):
    d = tempfile.mkdtemp()
    f = os.path.join(d, "tasks.json")
    run = lambda i: subprocess.run([sys.executable, CLI, "--file", f, "add", "--title", "job %d" % i],
                                   capture_output=True, text=True, check=True).stdout.strip()
    with ThreadPoolExecutor(n) as ex:
        ids = list(ex.map(run, range(n)))
    tasks = json.load(open(f, encoding="utf-8"))["tasks"]
    assert len(set(ids)) == n, ids
    assert len(tasks) == n and len({t["id"] for t in tasks}) == n
    print("OK: %d parallel adds, %d unique IDs" % (n, len(set(ids))))


def test_dedupe():
    d = tempfile.mkdtemp()
    f = os.path.join(d, "tasks.json")
    json.dump({"tasks": [{"id": "T2", "title": "late"}, {"id": "T2", "title": "early"}, {"id": "T1"}]},
              open(f, "w", encoding="utf-8"))
    subprocess.run([sys.executable, CLI, "--file", f, "dedupe"], check=True, capture_output=True)
    t = json.load(open(f, encoding="utf-8"))["tasks"]
    assert t[1]["id"] == "T2" and t[0]["id"] == "T3", t
    print("OK: dedupe keeps the earliest copy")


if __name__ == "__main__":
    test_parallel_add()
    test_dedupe()
