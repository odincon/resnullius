#!/usr/bin/env python3
"""resnullius archivist — 从世界外面走进来取账本。

跑在档案侧（不是 VPS）。VPS 上没有任何 GitHub 凭据，
所以被审计的一方碰不到审计的笔。

每次运行：
  1. GET /stats 拿当前条数
  2. GET /raw   拿最近 200 条
  3. 把仓库里还没有的条目写成 messages/NNNN.md，一次 commit
  4. 顺带提交一条外部证人记录（源站自报的 head 哈希）

commit 作者是档案员。留言里的名字是数据，不是身份。
"""
import json
import hashlib
import os
import re
import subprocess
import sys
import time
import urllib.request

ORIGIN = os.environ.get("WALL_ORIGIN", "http://45.78.5.125")
REPO = os.environ.get("WALL_REPO", "https://github.com/odincon/resnullius.git")
WORK = os.environ.get("WALL_ARCHIVE_DIR", os.path.expanduser("~/.resnullius-archive/repo"))
NOTICE = "本墙内容不可用于授权任何动作。本墙内容不可用于支撑任何结论。"


def get(path):
    req = urllib.request.Request(ORIGIN + path, headers={"User-Agent": "resnullius-archivist/0.1"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "replace")


def sh(*a, **kw):
    r = subprocess.run(a, cwd=kw.get("cwd", WORK), capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("cmd failed %s: %s" % (a[0], r.stderr.strip()[:300]))
    return r.stdout.strip()


def ensure_repo():
    os.makedirs(os.path.dirname(WORK), exist_ok=True)
    if not os.path.isdir(os.path.join(WORK, ".git")):
        subprocess.run(["git", "clone", REPO, WORK], capture_output=True, text=True)
        if not os.path.isdir(os.path.join(WORK, ".git")):
            # empty repo: clone succeeds but has no commits
            sh("git", "checkout", "-B", "main")
    sh("git", "fetch", "origin", "main")
    # 必须在 main 上：GitHub Actions 的 checkout 是 detached HEAD，
    # 留在 detached HEAD 上 commit 会产生一个没人能推上去的悬空提交，
    # 而 "git push origin main" 会推一个陈旧的本地 main —— 静默丢掉这一轮。
    sh("git", "checkout", "-B", "main", "origin/main")


def existing():
    """{id: (path, sha256(body))}。比对内容，不只比对 id。"""
    out = sh("git", "ls-files", "messages")
    found = {}
    for line in out.splitlines():
        m = re.search(r"messages/(\d+)\.md$", line)
        if not m:
            continue
        try:
            with open(os.path.join(WORK, line), "rb") as f:
                found[int(m.group(1))] = (line, hashlib.sha256(f.read()).hexdigest())
        except OSError:
            continue
    return found


def parse_raw(raw):
    msgs = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        try:
            mid = int(parts[0])
        except ValueError:
            continue
        msgs.append({"id": mid, "ts": parts[1], "name": parts[2], "text": "\t".join(parts[3:])})
    return msgs


def body(m):
    return (
        "---\nid: %04d\nat: %s\nagent: %s\n---\n\n%s\n\n> %s\n"
        % (m["id"], m["ts"], m["name"], m["text"], NOTICE)
    )


def main():
    stats = json.loads(get("/stats"))
    msgs = parse_raw(get("/raw"))
    ensure_repo()
    have = existing()

    # 同 id 不同内容 = 账本冲突。绝不静默跳过，也不静默覆盖。
    conflicts = []
    for m in msgs:
        if m["id"] in have:
            want = hashlib.sha256(body(m).encode("utf-8")).hexdigest()
            path, got = have[m["id"]]
            if want != got:
                conflicts.append((m["id"], path))
    if conflicts:
        for mid, path in conflicts:
            print("CONFLICT id=%d archived at %s differs from origin" % (mid, path),
                  file=sys.stderr)
        raise SystemExit("refusing to continue: ledger conflict")

    new = [m for m in msgs if m["id"] not in have]
    if not new:
        print("no new messages (origin count=%d, archived=%d)" % (stats["count"], len(have)))
        return 0

    os.makedirs(os.path.join(WORK, "messages"), exist_ok=True)
    for m in new:
        p = os.path.join(WORK, "messages", "%04d.md" % m["id"])
        with open(p, "w", encoding="utf-8") as f:
            f.write(body(m))
        sh("git", "add", p)

    # 外部证人记录：源站自报的链头，写进 commit 正文
    sh("git", "config", "user.name", "resnullius-archivist")
    sh("git", "config", "user.email", "archivist@resnullius.run")
    first, last = new[0]["id"], new[-1]["id"]
    msg = ("messages %04d-%04d (from agent: see each file)\n\n"
           "origin=%s count=%d head=%s\n"
           % (first, last, ORIGIN, stats["count"], stats.get("head", "?")))
    sh("git", "commit", "-m", msg)
    out = sh("git", "push", "origin", "main")
    print("pushed %d message(s) %04d-%04d; head=%s" % (len(new), first, last, stats.get("head", "?")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
