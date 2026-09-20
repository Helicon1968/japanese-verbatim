#!/usr/bin/env python3
"""作業領域の作成・点検・後始末。

中間ファイルは必ず `.jv/work/` の下に置く。出力先に散らかさない。
成果物と作業ゴミが同じ階層にあると、どれが納品物か分からなくなる。

    <出力先>/
      <名前>.md               成果物
      <名前>.exempt.txt       成果物（忠実性チェックの判断記録）
      .jv/
        work/    中間ファイル（finish で削除する）
        audit/   出典の生データと検証結果（既定で残す）
        cache/   再実行用キャッシュ（--purge-cache で削除）

start は --source を必須にしてある。**何を変換するのかを決めずに始めさせない**ため。
finish は既定で確認のみ（dry run）。実際に消すには --apply を付ける。
削除は `.jv/` の内側に限定され、それ以外には一切触れない。

成果物名は日本語で付ける（SKILL.md 0-1）。文字種の検査も変換もしていないので
そのまま通る。空白だけは入れないこと（コマンドで毎回引用符が要る）。

1つの素材から深さの違う版を複数出すこともある（詳細版と要点版など）。
その場合は成果物名を複数登録する。登録していない .md は非成果物として報告される。

使い方:
    python workspace.py start --source video.mp4 --name ハーネス・エンジニアリング_完全ガイド
    python workspace.py start --source a.mp4 --name 詳細版 --also 要点版
    python workspace.py name --add 要点版      # 後から足す
    python workspace.py name                   # 登録済みの一覧
    python workspace.py status
    python workspace.py finish            # 消す対象の確認のみ
    python workspace.py finish --apply    # 実行
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import shutil
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

JV = ".jv"
SUBDIRS = ("work", "audit", "cache")

# 成果物として出力先に残してよいもの（マニフェストの name から厳密に決める）。
# 拡張子だけで判定すると、中間生成物の appendix.md や plan.md まで
# 成果物と誤判定してしまう。
def deliverables(names: str | list[str]) -> tuple[str, ...]:
    """成果物名の並びから、出力先に置いてよいファイル名を作る。

    1つの素材から深さの違う版を複数出すことがある（詳細版と要点版など）。
    その場合は名前を複数登録する。登録していない .md は非成果物として報告される。
    """
    if isinstance(names, str):
        names = [names]
    out: list[str] = []
    for n in names:
        # <名前>.assets/ は出典画像の置き場で、成果物の一部。work/ ではないので消さない。
        out += [f"{n}.md", f"{n}.exempt.txt", f"{n}.assets"]
    return tuple(out)


def manifest_names(m: dict) -> list[str]:
    """マニフェストから成果物名の並びを取り出す。

    `names` を持たない古いマニフェストとも互換にする。
    """
    if m.get("names"):
        return list(m["names"])
    return [m["name"]] if m.get("name") else []


def jv_root(out_dir: Path) -> Path:
    return out_dir / JV


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n/1:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}GB"


def dir_size(p: Path) -> tuple[int, int]:
    """(ファイル数, 合計バイト数)"""
    files = [f for f in p.rglob("*") if f.is_file()]
    return len(files), sum(f.stat().st_size for f in files)


def cmd_start(args) -> int:
    out = args.out_dir.resolve()
    root = jv_root(out)
    for d in SUBDIRS:
        (root / d).mkdir(parents=True, exist_ok=True)

    src = args.source
    src_kind = "url" if src.startswith(("http://", "https://")) else "file"
    if src_kind == "file":
        sp = Path(src)
        if not sp.exists():
            print(f"エラー: ソースが見つかりません: {sp}", file=sys.stderr)
            return 1
        src = str(sp.resolve())

    names = [args.name] + list(args.also or [])
    seen, uniq = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            uniq.append(n)

    manifest = {
        "name": uniq[0],          # 旧形式との互換のために残す
        "names": uniq,
        "source": src,
        "source_kind": src_kind,
        "out_dir": str(out),
        "started_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    (root / "audit" / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"作業領域を作成しました: {root}")
    print(f"  ソース  : {src}")
    print(f"  成果物名: {' / '.join(uniq)}")
    print("\n以降、中間ファイルはすべて次の場所に置くこと（その場で書くスクリプトも含む）:")
    print(f"  {root/'work'}")
    print("\n出力先に置いてよいのは成果物だけ:")
    for n in uniq:
        print(f"  {out/(n + '.md')}")
        print(f"  {out/(n + '.exempt.txt')}")
    if len(uniq) == 1:
        print("\n版を増やすときは後から登録できる:")
        print("  python workspace.py name --add <もう一つの名前>")
    return 0


def cmd_name(args) -> int:
    """成果物名を後から足す／一覧する。

    版を増やすかどうかは分量を見てから決めることが多い。start の時点で
    決め切らなくてよいようにしてある。登録しないまま2つ目の .md を置くと、
    status がそれを非成果物として報告する。
    """
    out = args.out_dir.resolve()
    man = jv_root(out) / "audit" / "manifest.json"
    if not man.exists():
        print(f"作業領域がありません: {man}", file=sys.stderr)
        return 1
    m = json.loads(man.read_text(encoding="utf-8"))
    names = manifest_names(m)

    if args.add:
        if args.add in names:
            print(f"登録済み: {args.add}")
        else:
            names.append(args.add)
            m["names"] = names
            m["name"] = names[0]
            man.write_text(json.dumps(m, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            print(f"追加しました: {args.add}")

    print(f"\n成果物名 {len(names)}件")
    for n in names:
        print(f"  {n}.md / {n}.exempt.txt / {n}.assets")
    return 0


def _manifest(out: Path) -> dict:
    man = jv_root(out) / "audit" / "manifest.json"
    return json.loads(man.read_text(encoding="utf-8")) if man.exists() else {}


def _keep_names(out: Path) -> tuple[str, ...]:
    names = manifest_names(_manifest(out))
    return deliverables(names) if names else ()


def _strays(out: Path) -> list[Path]:
    """出力先に残った、成果物でもソースでもないファイル。"""
    m = _manifest(out)
    names = manifest_names(m)
    keep = deliverables(names) if names else ()
    src_name = Path(m["source"]).name if m.get("source_kind") == "file" else None
    out_files = []
    for f in sorted(out.iterdir()):
        if f.name == JV or f.name in keep or f.name == src_name:
            continue          # 作業領域・成果物・元ソースは対象外
        if f.is_file() and f.name.startswith("."):
            continue
        out_files.append(f)
    return out_files


def cmd_status(args) -> int:
    out = args.out_dir.resolve()
    root = jv_root(out)
    print(f"出力先: {out}")
    if not root.exists():
        print("  .jv/ がありません（workspace.py start を実行していない）")
    else:
        for d in SUBDIRS:
            p = root / d
            if p.exists():
                n, sz = dir_size(p)
                print(f"  .jv/{d:<6} {n:4d}ファイル  {human(sz)}")

    keep_names = _keep_names(out)
    kept = [f for f in sorted(out.iterdir()) if f.name in keep_names]
    print(f"\n成果物 {len(kept)}件")
    for f in kept:
        print(f"  {f.name}")

    strays = _strays(out)
    if strays:
        print(f"\n出力先に残っている非成果物 {len(strays)}件"
              "（.jv/ の外なので finish では削除しません）")
        for f in strays[:30]:
            tag = "DIR " if f.is_dir() else "    "
            size = human(dir_size(f)[1]) if f.is_dir() else human(f.stat().st_size)
            print(f"  {tag}{f.name}  {size}")
        if len(strays) > 30:
            print(f"  ... 他 {len(strays)-30}件")
        print("\n  → 次回からは .jv/work/ に置くこと。今回分は内容を確認して手で処理する")
    else:
        print("\n出力先に非成果物はありません")
    return 0


def clear_dir(path: Path) -> list[Path]:
    """path の中身を消す。path 自体は残す。消せなかったものを返す。

    shutil.rmtree は1つでも掴まれていると PermissionError で落ちる。
    Dropbox・OneDrive・検索インデクサ・ウイルス対策が、直前まで書いていた
    ディレクトリを掴んでいることは珍しくない（実測: Dropbox 配下で2回）。

    そのとき中身は消えているのにスタックトレースだけが出て、片付いたのか
    失敗したのかが分からなくなる。**空のディレクトリが残るのは実害がない**ので、
    ファイルを1つずつ消し、消せなかったものだけを報告する。
    """
    failed: list[Path] = []
    for p in sorted(path.rglob("*"), key=lambda q: len(q.parts), reverse=True):
        try:
            if p.is_dir() and not p.is_symlink():
                p.rmdir()
            else:
                p.unlink()
        except OSError:
            failed.append(p)
    return failed


def cmd_finish(args) -> int:
    out = args.out_dir.resolve()
    root = jv_root(out)
    if not root.exists():
        print(f"作業領域がありません: {root}")
        return 0

    targets = [root / "work"]
    if args.purge_cache:
        targets.append(root / "cache")
    if args.purge_audit:
        targets.append(root / "audit")

    total_n = total_sz = 0
    print("削除対象（.jv/ の内側のみ）")
    for t in targets:
        if not t.exists():
            continue
        n, sz = dir_size(t)
        total_n, total_sz = total_n + n, total_sz + sz
        print(f"  {t.relative_to(out)}  {n}ファイル  {human(sz)}")
    if total_n == 0:
        print("  （なし）")

    keep = [d for d in SUBDIRS if not any((root / d) == t for t in targets)]
    if keep:
        print("\n残すもの: " + ", ".join(f".jv/{d}" for d in keep))
        print("  audit は出典の生データと検証結果。後から再検証するために残す")
        print("  cache は再実行を即座に終わらせるために残す（--purge-cache で削除）")

    strays = _strays(out)
    if strays:
        print(f"\n注意: 出力先に非成果物が {len(strays)}件あります"
              "（.jv/ の外なので削除しません）")
        for f in strays[:20]:
            print(f"  {f.name}")

    if not args.apply:
        print("\n確認のみです。実際に削除するには --apply を付けてください。")
        return 0

    failed: list[Path] = []
    for t in targets:
        if not t.exists():
            continue
        # 安全確認: .jv/ の内側であることを必ず検証してから削除する
        if jv_root(out).resolve() not in t.resolve().parents:
            print(f"中止: {t} は .jv/ の内側ではありません", file=sys.stderr)
            return 1
        failed += clear_dir(t)
        t.mkdir(parents=True, exist_ok=True)
        print(f"削除: {t.relative_to(out)}")

    print(f"\n完了: {total_n}ファイル / {human(total_sz)} を削除しました")

    if failed:
        stuck_files = [p for p in failed if p.is_file()]
        print(f"\n注意: {len(failed)}件を消せませんでした（他のプロセスが使用中）")
        for p in failed[:10]:
            print(f"  {'DIR ' if p.is_dir() else '    '}{p.relative_to(out)}")
        if len(failed) > 10:
            print(f"  ... 他 {len(failed)-10}件")
        if stuck_files:
            print("\n  **ファイルが残っています。** 中身を確認して手で処理すること。")
            return 1
        print("\n  空のディレクトリだけなので実害はありません。")
        print("  Dropbox や検索インデクサが掴んでいることが多い。")
        print("  気になるなら時間をおいて finish --apply を再実行できます。")
    return 0


def cmd_adopt(args) -> int:
    """出力先に散らばった非成果物を .jv/ へ回収する。

    削除ではなく移動なので取り消せる。回収後に finish --apply で消す。
    規約が無かった頃の実行結果や、規約から外れた実行の後始末に使う。

    --audit で指定したものは .jv/audit/ へ送る。こちらは finish で消えない。
    出典の生データ（raw.txt）のように、後から再検証するために残すものに使う。
    """
    out = args.out_dir.resolve()
    root = jv_root(out)
    if not root.exists():
        print(f"作業領域がありません: {root}（先に start を実行してください）", file=sys.stderr)
        return 1
    dest = root / "work" / "adopted"
    audit_dest = root / "audit"

    strays = _strays(out)
    files = [f for f in strays if f.is_file()]
    dirs = [f for f in strays if f.is_dir()]
    include = set(args.include or [])
    exclude = set(args.exclude or [])
    audit = set(args.audit or [])

    candidates = [f for f in files if f.name not in exclude]
    candidates += [d for d in dirs if d.name in include and d.name not in exclude]
    to_audit = [f for f in candidates if f.name in audit]
    picked = [f for f in candidates if f.name not in audit]
    skipped_dirs = [d for d in dirs if d.name not in include]

    if to_audit:
        print(f"監査用に保存 {len(to_audit)}件  ->  {audit_dest.relative_to(out)}"
              "（finish では削除されません）")
        for f in to_audit:
            print(f"  {f.name}")
        print()

    if not picked:
        print("回収対象はありません")
    else:
        total = sum(dir_size(f)[1] if f.is_dir() else f.stat().st_size for f in picked)
        print(f"回収対象 {len(picked)}件 / {human(total)}  ->  {dest.relative_to(out)}")
        for f in picked:
            print(f"  {'DIR ' if f.is_dir() else '    '}{f.name}")

    if skipped_dirs:
        print(f"\n見送るディレクトリ {len(skipped_dirs)}件"
              "（別の出力先の可能性があるため、--include で明示しない限り触れません）")
        for d in skipped_dirs:
            print(f"  {d.name}")

    if not args.apply:
        print("\n確認のみです。実際に移動するには --apply を付けてください。")
        return 0

    def _move(items: list[Path], into: Path) -> None:
        if not items:
            return
        into.mkdir(parents=True, exist_ok=True)
        for f in items:
            target = into / f.name
            if target.exists():
                target = into / f"{f.stem}_dup{f.suffix}"
            shutil.move(str(f), str(target))

    _move(to_audit, audit_dest)
    _move(picked, dest)
    if to_audit:
        print(f"\n{len(to_audit)}件を {audit_dest.relative_to(out)} へ保存しました")
    if picked:
        print(f"{len(picked)}件を {dest.relative_to(out)} へ移動しました")
        print("内容を確認したうえで finish --apply を実行すると削除されます")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="作業領域の作成・点検・後始末")
    p.add_argument("--out-dir", type=Path, default=Path("."))
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start", help="作業領域を作る（ソースの指定が必須）")
    s.add_argument("--source", required=True,
                   help="変換するファイルまたはURL。何を変換するか決めずに始めないこと")
    s.add_argument("--name", required=True,
                   help="成果物のファイル名（拡張子なし）。日本語で付ける。空白は入れない")
    s.add_argument("--also", action="append", metavar="名前",
                   help="同じ素材から出すもう一つの版の名前（繰り返し可）。"
                        "後から `name --add` でも足せる")
    s.set_defaults(func=cmd_start)

    sub.add_parser("status", help="作業領域と残存ファイルを点検").set_defaults(func=cmd_status)

    n = sub.add_parser("name", help="成果物名を後から足す／一覧する")
    n.add_argument("--add", metavar="名前", help="成果物名を追加する")
    n.set_defaults(func=cmd_name)

    a = sub.add_parser("adopt", help="出力先の非成果物を .jv/work/ へ回収（既定は確認のみ）")
    a.add_argument("--apply", action="store_true", help="実際に移動する")
    a.add_argument("--include", action="append", help="回収するディレクトリ名（繰り返し可）")
    a.add_argument("--exclude", action="append", help="回収しない名前（繰り返し可）")
    a.add_argument("--audit", action="append",
                   help="削除せず .jv/audit/ に保存する名前（繰り返し可）。"
                        "出典の生データなど、後から再検証するために残すもの")
    a.set_defaults(func=cmd_adopt)

    f = sub.add_parser("finish", help="中間ファイルを削除（既定は確認のみ）")
    f.add_argument("--apply", action="store_true", help="実際に削除する")
    f.add_argument("--purge-cache", action="store_true", help="キャッシュも削除する")
    f.add_argument("--purge-audit", action="store_true",
                   help="監査用データも削除する（再検証できなくなる）")
    f.set_defaults(func=cmd_finish)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
