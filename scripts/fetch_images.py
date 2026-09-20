#!/usr/bin/env python3
"""出典の画像を取得して成果物に組み込む。

前提: 取得の段階で raw.txt に次の形で画像を記録しておくこと。

    [[IMAGE <メディアID>]] 説明文（装飾的なものは "decorative" を含める）

情報を持つ画像は、ASCIIアートで描き起こしてはならない。元画像を保存して
参照すれば、原文と同じ情報がそのまま読者に届く。

保存先は `<出力先>/<名前>.assets/` で、これは成果物の一部として残す
（`.jv/work/` ではない。finish で消えては困る）。

既定は確認のみ。実際に取得するには --apply を付ける。
ダウンロードはユーザーの承認が要る操作なので、勝手に実行しないこと。

使い方:
    python fetch_images.py --raw .jv/audit/raw.txt --name harness-engineering
    python fetch_images.py --raw .jv/audit/raw.txt --name harness-engineering --apply
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

MARKER = re.compile(r"\[\[IMAGE\s+([A-Za-z0-9_-]+)\]\]\s*(.*)")

# X/Twitter のメディアURL。他のソースは --url-template で差し替える。
DEFAULT_TEMPLATE = "https://pbs.twimg.com/media/{id}?format=jpg&name=large"


def slugify(text: str, limit: int = 40) -> str:
    head = text.split("/")[0]
    head = re.sub(r"\(.*?\)", " ", head)
    head = head.split(":")[-1] if ":" in head else head
    head = unicodedata.normalize("NFKC", head).lower()
    head = re.sub(r"[^a-z0-9]+", "-", head).strip("-")
    return (head[:limit].rstrip("-") or "image")


def parse(raw: Path) -> list[dict]:
    items = []
    for n, ln in enumerate(raw.read_text(encoding="utf-8").splitlines(), 1):
        m = MARKER.search(ln)
        if not m:
            continue
        mid, desc = m.group(1), m.group(2).strip()
        items.append({
            "id": mid,
            "line": n,
            "description": desc,
            "decorative": "decorative" in desc.lower(),
        })
    return items


def main() -> int:
    p = argparse.ArgumentParser(description="出典画像の取得と組み込み")
    p.add_argument("--raw", type=Path, required=True, help="[[IMAGE ...]] を含む出典テキスト")
    p.add_argument("--name", required=True, help="成果物名（拡張子なし）")
    p.add_argument("--out-dir", type=Path, default=Path("."))
    p.add_argument("--url-template", default=DEFAULT_TEMPLATE)
    p.add_argument("--include-decorative", action="store_true",
                   help="装飾画像も取得する（既定は説明文だけにして取得しない）")
    p.add_argument("--apply", action="store_true", help="実際にダウンロードする")
    args = p.parse_args()

    if not args.raw.exists():
        print(f"エラー: {args.raw} がありません", file=sys.stderr)
        return 1

    items = parse(args.raw)
    if not items:
        print("画像の記録が見つかりません。")
        print("取得時に [[IMAGE <id>]] 説明 の形で raw.txt に残すこと。")
        return 0

    assets = args.out_dir / f"{args.name}.assets"
    targets = [i for i in items if args.include_decorative or not i["decorative"]]
    skipped = [i for i in items if i not in targets]

    for n, it in enumerate(targets, 1):
        it["file"] = f"{n:02d}-{slugify(it['description'])}.jpg"
        it["url"] = args.url_template.format(id=it["id"])

    print(f"画像の記録 {len(items)}件（取得対象 {len(targets)}件 / 装飾のため見送り {len(skipped)}件）")
    print(f"保存先: {assets}\n")
    for it in targets:
        print(f"  {it['file']}")
        print(f"    出典行 {it['line']} / {it['description'][:70]}")
    if skipped:
        print("\n見送り（装飾的なため、本文では一行の説明に留める）")
        for it in skipped:
            print(f"  {it['id']}  {it['description'][:70]}")

    print("\n--- 本文に貼る参照 ---")
    for it in targets:
        alt = it["description"].split("/")[0].strip().rstrip(":") or it["id"]
        print(f"![{alt}]({assets.name}/{it['file']})")

    if not args.apply:
        print("\n確認のみです。実際に取得するには --apply を付けてください。")
        print("ダウンロードはユーザーの承認が要る操作です。無断で実行しないこと。")
        return 0

    assets.mkdir(parents=True, exist_ok=True)
    ok = 0
    for it in targets:
        dest = assets / it["file"]
        try:
            req = urllib.request.Request(it["url"], headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            dest.write_bytes(data)
            it["bytes"] = len(data)
            ok += 1
            print(f"取得: {it['file']}  {len(data)/1024:.0f}KB")
        except Exception as e:
            it["error"] = str(e)
            print(f"失敗: {it['file']}  {e}", file=sys.stderr)

    (assets / "index.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{ok}/{len(targets)}件を取得しました -> {assets}")
    print("本文の該当箇所に、上の参照を貼ること。")
    return 0 if ok == len(targets) else 1


if __name__ == "__main__":
    sys.exit(main())
