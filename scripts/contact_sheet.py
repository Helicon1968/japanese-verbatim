#!/usr/bin/env python3
"""候補フレームを一覧表（コンタクトシート）に並べる。

    uv run --no-project --python 3.12 --with pillow \
        python contact_sheet.py .jv/work/frames

**なぜ要るか。** grab_frames.py のシーン検出は、60分を超える動画だと
100枚近い候補を返す。これを1枚ずつ原寸で読むのは高くつく。
縮小した一覧で「画面に文字があるか」「画作りが変わっていないか」を先に見て、
採る候補だけを原寸で確かめるほうが速い。

**一覧でも全枚数に目を通すことは変わらない。** 縮小しても、スライドか
話者のアップかは判別できる。判別できなかったものは原寸で開く。

手順5-4（素材が1本かどうか）の確認にも使う。画作りの変化は縮小しても分かる。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def label_of(path: Path) -> str:
    """f_001461.jpg -> 24:21。秒が読めなければファイル名をそのまま返す。"""
    stem = path.stem
    if "_" not in stem:
        return stem
    tail = stem.rsplit("_", 1)[1]
    if not tail.isdigit():
        return stem
    sec = int(tail)
    return f"{sec // 60:02d}:{sec % 60:02d}"


def main() -> int:
    p = argparse.ArgumentParser(description="候補フレームの一覧表を作る")
    p.add_argument("frames", type=Path, help="フレームの入ったディレクトリ")
    p.add_argument("--out", type=Path, help="出力先（既定: <frames>/../sheets）")
    p.add_argument("--cols", type=int, default=5)
    p.add_argument("--rows", type=int, default=4)
    p.add_argument("--width", type=int, default=384, help="サムネイル1枚の幅")
    p.add_argument("--glob", default="f_*.jpg")
    args = p.parse_args()

    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("Pillow が要る: uv run --with pillow ...", file=sys.stderr)
        return 2

    frames = sorted(args.frames.glob(args.glob))
    if not frames:
        print(f"フレームが見つからない: {args.frames}/{args.glob}", file=sys.stderr)
        return 1

    out = args.out or args.frames.parent / "sheets"
    out.mkdir(parents=True, exist_ok=True)

    per = args.cols * args.rows
    sheets = (len(frames) + per - 1) // per
    print(f"{len(frames)}枚 -> {sheets}シート（{args.cols}x{args.rows}）")

    for s in range(sheets):
        chunk = frames[s * per:(s + 1) * per]
        tiles, th = [], 0
        for f in chunk:
            im = Image.open(f)
            th = round(im.height * args.width / im.width)
            tiles.append((f, im.resize((args.width, th))))

        sheet = Image.new("RGB", (args.cols * args.width, args.rows * th), "black")
        d = ImageDraw.Draw(sheet)
        for i, (f, im) in enumerate(tiles):
            x, y = (i % args.cols) * args.width, (i // args.cols) * th
            sheet.paste(im, (x, y))
            # 時刻を焼き込む。あとで --at に渡す値がそのまま読めるようにする
            d.rectangle([x, y, x + 62, y + 18], fill="black")
            d.text((x + 4, y + 4), label_of(f), fill="yellow")

        path = out / f"sheet{s + 1}.jpg"
        sheet.save(path, quality=88)
        print(f"  {path}  {sheet.size[0]}x{sheet.size[1]}")

    print("\n一覧で選別したあと、採る候補は原寸で開いて確かめること。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
