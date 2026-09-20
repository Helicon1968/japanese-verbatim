#!/usr/bin/env python3
"""後始末（finish）の単体テスト。

    python tests/test_workspace.py

**このテストがある理由。** shutil.rmtree は1つでも掴まれていると
PermissionError で落ちる。Dropbox 配下で2回続けて起きた。中身は消えているのに
スタックトレースだけが出て、片付いたのか失敗したのかが分からなくなっていた。
"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from workspace import clear_dir  # noqa: E402

fails = 0


def check(name: str, got, want) -> None:
    global fails
    if got == want:
        print(f"  ok    {name}")
    else:
        fails += 1
        print(f"  FAIL  {name}")
        print(f"          got  {got!r}")
        print(f"          want {want!r}")


def build(root: Path) -> None:
    """work/ の典型的な中身を作る。"""
    (root / "frames").mkdir(parents=True)
    (root / "sheets").mkdir()
    (root / "transcript.txt").write_text("x", encoding="utf-8")
    (root / "frames" / "f_000003.jpg").write_bytes(b"\xff\xd8")
    (root / "frames" / "f_000024.jpg").write_bytes(b"\xff\xd8")
    (root / "sheets" / "sheet1.jpg").write_bytes(b"\xff\xd8")


with tempfile.TemporaryDirectory() as td:
    print("掴まれていない場合")
    work = Path(td) / "work"
    build(work)
    failed = clear_dir(work)
    check("消せなかったものは無い", failed, [])
    check("中身が空になる", list(work.iterdir()), [])
    check("work 自体は残る", work.is_dir(), True)

    print("\n中身が無い場合")
    check("空でも落ちない", clear_dir(work), [])

with tempfile.TemporaryDirectory() as td:
    print("\nファイルを掴まれている場合")
    work = Path(td) / "work"
    build(work)
    locked = work / "frames" / "f_000003.jpg"
    fh = open(locked, "rb")          # Windows では開いている間は消せない
    try:
        failed = clear_dir(work)
        if sys.platform == "win32":
            check("掴まれたファイルを報告する", locked in failed, True)
            check("その親ディレクトリも報告する", work / "frames" in failed, True)
            check("掴まれていないものは消えている",
                  (work / "transcript.txt").exists(), False)
            check("sheets は消えている", (work / "sheets").exists(), False)
        else:
            # POSIX は開いていても unlink できる。消えることだけ確かめる
            check("POSIX では消せる", failed, [])
    finally:
        fh.close()
        shutil.rmtree(work, ignore_errors=True)

print()
if fails:
    print(f"{fails} 件が失敗した")
    sys.exit(1)
print("すべて通った")
