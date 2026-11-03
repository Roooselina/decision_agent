"""Demo reset and warm-up, run by tools\\demo_reset.ps1 (which stops and starts the program).

    python tools/demo_reset.py prepare   (program stopped) back up, restore the demo folder
    python tools/demo_reset.py warm      (program running) first check, compare with the key

prepare
  1. Copies the data folder (settings, state databases, activity) and the demo folder to
     %LOCALAPPDATA%\\DecisionAgent_demo\\backups\\<timestamp>. Nothing is deleted.
  2. Restores the demo folder from the pristine copy in %LOCALAPPDATA%\\DecisionAgent_demo\\pristine.
     The pristine copy is made once: the current folder, if its content equals a fresh copy from
     tools/make_demo_capstone.py; otherwise the fresh copy. Only files that differ are written back,
     so unchanged files keep their stored results. Files that are not in the pristine copy are
     moved into the backup, not deleted.
warm
  Makes sure the folder is registered, starts its check, confirms the held-back first check only
  when its estimate is within --max-requests, waits until it is done, and prints the requests
  used, the stale lines against the answer key, and the line to type in the live edit.
Findings and verdicts are not cleared: a matching cache is what makes the demo fast.
"""

from __future__ import annotations

import argparse
import filecmp
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

from decision_agent.desktop.paths import app_home
from decision_agent.desktop.statedump import read_settings

REPO = Path(__file__).resolve().parent.parent
DOWNLOADS = Path.home() / "Downloads"
DEFAULT_VARIANT = "demo_bookswap"
WORK = Path.home() / "AppData" / "Local" / "DecisionAgent_demo"

# root: the registered folder. generator: make_demo_capstone.py variant (None: the pristine copy
# is the folder as it is on the first run). key: answer key JSON with line-level expectations, or
# None (then the result is compared with the baseline saved by "warm --save-baseline").
VARIANTS = {
    "demo_bookswap": {
        "root": DOWNLOADS / "demo_bookswap",
        "generator": None,
        "key": None,
        "live_edit": {
            "file": "demo_bookswap/requirements/requirements_backend.md",
            "old": "- 서버는 AWS EC2에 배포한다",
            "new": "- 서버는 AWS Lightsail에 배포한다",
            "expect": "몇 초 뒤 이 줄의 '반영 안 됨' 지적(서버 배포 Lightsail 결정)이 사라지고 결과가 1개 줄어듭니다.",
        },
    },
    "demo_capstone": {
        "root": DOWNLOADS / "demo_capstone" / "demo_capstone",
        "generator": "capstone",
        "key": DOWNLOADS / "demo_capstone" / "demo_capstone_answer_key.json",
        "live_edit": {
            "file": "requirements/requirements_frontend.md",
            "old": "FE-05 매물 등록 화면에서 사진은 최대 3장까지 첨부할 수 있다",
            "new": "FE-05 매물 등록 화면에서 사진은 최대 5장까지 첨부할 수 있다",
            "expect": "몇 초 뒤 이 줄의 '반영 안 됨' 지적이 사라지고 결과가 1개 줄어듭니다.",
        },
    },
}


def folder_of(variant: str) -> Path:
    return VARIANTS[variant]["root"]


def answer_key(variant: str) -> list[dict] | None:
    path = VARIANTS[variant]["key"]
    if path is None:
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    seen = set()
    rows = []
    for row in data["artifact_lines"]:
        if row.get("expected") == "INCONSISTENT" and row["item"] not in seen:
            seen.add(row["item"])
            rows.append(row)
    return rows


# prepare


def same_content(a: Path, b: Path) -> bool:
    """Byte-equal, or for Office files equal apart from the docProps timestamps."""
    if filecmp.cmp(a, b, shallow=False):
        return True
    if a.suffix.lower() in {".docx", ".xlsx", ".pptx"}:
        try:
            with zipfile.ZipFile(a) as za, zipfile.ZipFile(b) as zb:
                names = [n for n in za.namelist() if not n.startswith("docProps/")]
                if names != [n for n in zb.namelist() if not n.startswith("docProps/")]:
                    return False
                return all(za.read(n) == zb.read(n) for n in names)
        except (zipfile.BadZipFile, KeyError, OSError):
            return False
    if a.suffix.lower() == ".pdf":
        # Generated PDFs carry their creation time; compare sizes only.
        return a.stat().st_size == b.stat().st_size
    return False


def files_under(root: Path) -> dict[str, Path]:
    return {str(p.relative_to(root)): p for p in root.rglob("*") if p.is_file()}


def ensure_pristine(variant: str) -> Path:
    pristine = WORK / "pristine" / variant
    if pristine.exists():
        return pristine
    if VARIANTS[variant]["generator"] is None:
        shutil.copytree(folder_of(variant), pristine)
        print(f"원본 사본을 만들었습니다: {pristine} (지금 폴더 기준)")
        return pristine
    fresh_root = Path(tempfile.mkdtemp(prefix="demo_fresh_"))
    # The generator needs the document libraries of the development environment.
    dev = REPO / ".venv" / "Scripts" / "python.exe"
    made = subprocess.run(
        [str(dev if dev.exists() else sys.executable), str(REPO / "tools" / "make_demo_capstone.py"),
         "--out", str(fresh_root), "--variant", VARIANTS[variant]["generator"]],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if made.returncode != 0:
        raise SystemExit("make_demo_capstone.py failed: " + made.stderr[-800:])
    fresh = fresh_root / variant / variant
    current = folder_of(variant)
    a, b = files_under(fresh), files_under(current)
    matches = set(a) == set(b) and all(same_content(a[k], b[k]) for k in a)
    source = current if matches else fresh
    shutil.copytree(source, pristine)
    shutil.rmtree(fresh_root, ignore_errors=True)
    print(f"원본 사본을 만들었습니다: {pristine} ({'지금 폴더' if matches else '새로 생성한 파일'} 기준)")
    return pristine


def prepare(variant: str) -> int:
    folder = folder_of(variant)
    pristine = ensure_pristine(variant)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = WORK / "backups" / stamp
    # 1. Back up the data folder (the program is stopped, so the databases are at rest).
    home = app_home()
    shutil.copytree(home, backup / "data", ignore=shutil.ignore_patterns("webview"))
    shutil.copytree(folder, backup / "demo_folder")
    print(f"백업: {backup}")

    # 2. Restore the demo folder.
    want, have = files_under(pristine), files_under(folder)
    restored, moved = [], []
    for rel, src in want.items():
        dst = folder / rel
        if rel not in have or not same_content(src, have[rel]):
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            restored.append(rel)
    for rel, path in have.items():
        if rel not in want:
            target = backup / "extra_files" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))
            moved.append(rel)
    if restored or moved:
        for rel in restored:
            print(f"  원래대로 되돌림: {rel}")
        for rel in moved:
            print(f"  원본에 없는 파일을 백업으로 옮김: {rel}")
    else:
        print("시연 폴더는 이미 원본 그대로입니다.")
    return 0


# warm


class Api:
    def __init__(self) -> None:
        self.port = read_settings().ui_port

    def call(self, path: str, body: dict | None = None, timeout: float = 15.0):
        token = (app_home() / "ui_token").read_text(encoding="utf-8").strip()
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data,
            method="GET" if body is None else "POST",
            headers={"x-da-token": token, "content-type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def wait_up(self, seconds: float = 180.0) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/ping", timeout=2)
                return True
            except (urllib.error.URLError, OSError):
                time.sleep(1)
        return False

    def folder(self, root: str) -> dict | None:
        for entry in self.call("/api/status").get("folders", []):
            if str(Path(entry["root"])).lower() == root.lower():
                return entry
        return None


def warm(
    variant: str, max_requests: int, timeout_minutes: float, forget_mail: bool = False,
    save_baseline: bool = False,
) -> int:
    root = str(folder_of(variant))
    api = Api()
    if not api.wait_up():
        print("프로그램이 3분 안에 응답하지 않았습니다. 바탕화면 아이콘으로 실행한 뒤 다시 하세요.")
        return 1
    if api.folder(root) is None:
        api.call("/api/folders/add", {"path": root})
        print(f"시연 폴더를 등록했습니다: {root}")
    if forget_mail:
        # Re-read this folder's mail sources from Gmail: a mail moved to the Gmail trash
        # (the live mail of a rehearsal) does not come back; the rest is read again.
        for source in read_settings().mail_sources:
            if str(Path(source.target)).lower() == root.lower():
                removed = api.call(f"/api/mail-sources/{source.id}/forget", {}).get("removed")
                api.call("/api/mail/check", {"source_id": source.id})
                print(f"메일 출처 '{source.name}': 저장된 메일 {removed}통을 지우고 다시 읽습니다.")
        time.sleep(20)
    started = time.monotonic()
    api.call("/api/rescan", {"root": root})
    confirmed = False
    calls_seen = 0
    deadline = started + timeout_minutes * 60
    time.sleep(3)
    while time.monotonic() < deadline:
        entry = api.folder(root) or {}
        state = (entry.get("state") or {}).get("state")
        progress = entry.get("progress") or {}
        calls_seen = max(calls_seen, int(progress.get("llm_calls") or 0))
        pending = entry.get("pending_confirm")
        if pending and not confirmed:
            estimate = int(pending.get("estimated_calls") or 0)
            print(f"첫 점검 예상 요청: 약 {estimate}회 (한도 {max_requests}회)")
            if estimate > max_requests:
                print("예상 요청이 한도를 넘어 멈춥니다. --max-requests를 올리거나 앱에서 직접 확인하세요.")
                return 1
            api.call("/api/confirm", {"root": root})
            confirmed = True
        elif state == "verified" and not (progress.get("phase") in {"reading", "extracting", "checking"}):
            break
        elif entry.get("failure") and state not in {"checking"}:
            print("점검 실패:", (entry.get("failure") or {}).get("message", "")[:200])
            return 1
        time.sleep(3)
    else:
        print(f"{timeout_minutes:g}분 안에 끝나지 않았습니다. 앱 화면에서 진행 상태를 확인하세요.")
        return 1
    entry = api.folder(root) or {}
    completion = entry.get("completion") or {}
    seconds = time.monotonic() - started
    used = completion.get("llm_calls", calls_seen) if confirmed else calls_seen
    print(f"웜업 완료: {seconds:.0f}초, 이번 점검 요청 {used}회, 결과 {entry.get('open_findings')}")

    findings = [f for f in api.call("/api/findings") if str(Path(f["root"])).lower() == root.lower()]
    stale = [f for f in findings if f["label"] == "INCONSISTENT" and f.get("status", "open") == "open"]
    key = answer_key(variant)
    ok = True
    if key is not None:
        found = []
        for row in key:
            hit = any(
                Path(f["source_doc"]).as_posix().endswith(row["file"]) and row["text"] in f["text"]
                for f in stale
            )
            found.append(hit)
            print(f"  [{'O' if hit else 'X'}] {row['item']:>2}. {row['file']}: {row['text'][:50]}")
        extra = [
            f for f in stale
            if not any(Path(f["source_doc"]).as_posix().endswith(r["file"]) and r["text"] in f["text"] for r in key)
        ]
        for f in extra:
            print(f"  [+] 정답표에 없는 지적: {f['source_doc']} {f.get('location', '')}: {f['text'][:60]}")
        print(f"정답표 {sum(found)}/{len(key)}개 찾음, 정답표 밖 지적 {len(extra)}개, 반영 안 됨 총 {len(stale)}개")
        ok = sum(found) == len(key) and not extra
    lines = sorted({(f["source_doc"], str(f.get("location", "")), f["text"]) for f in stale})
    baseline = WORK / f"baseline_{variant}.json"
    if save_baseline:
        baseline.write_text(json.dumps(lines, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"기준 결과를 저장했습니다: 반영 안 됨 {len(lines)}줄 ({baseline})")
    elif baseline.exists():
        want = {tuple(x) for x in json.loads(baseline.read_text(encoding="utf-8"))}
        have = set(lines)
        for doc, loc, text in sorted(want - have):
            print(f"  [-] 기준에 있는데 없음: {doc} {loc}: {text[:60]}")
        for doc, loc, text in sorted(have - want):
            print(f"  [+] 기준에 없는 지적: {doc} {loc}: {text[:60]}")
        same = want == have
        print(f"기준 결과와 {'같음' if same else '다름'}: 반영 안 됨 {len(have)}줄 (기준 {len(want)}줄)")
        ok = ok and same
    else:
        print(f"반영 안 됨 {len(lines)}줄 (기준 결과 없음: --save-baseline 으로 저장)")

    edit = VARIANTS[variant]["live_edit"]
    print()
    print("라이브 편집에서 입력할 것:")
    print(f"  파일: {Path(root) / edit['file']}")
    print(f"  바꿀 줄: {edit['old']}")
    print(f"  새 문장: {edit['new']}   (저장)")
    print(f"  예상: {edit['expect']}")
    return 0 if ok else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="시연 폴더 초기화와 웜업")
    parser.add_argument("action", choices=["prepare", "warm"])
    parser.add_argument("--variant", default=DEFAULT_VARIANT, choices=sorted(VARIANTS))
    parser.add_argument("--max-requests", type=int, default=450)
    parser.add_argument("--timeout-minutes", type=float, default=30)
    parser.add_argument("--forget-mail", action="store_true", help="메일 출처를 지우고 다시 읽음")
    parser.add_argument("--save-baseline", action="store_true", help="이번 결과를 기준 결과로 저장")
    args = parser.parse_args()
    if args.action == "prepare":
        return prepare(args.variant)
    return warm(args.variant, args.max_requests, args.timeout_minutes, args.forget_mail, args.save_baseline)


if __name__ == "__main__":
    sys.exit(main())
