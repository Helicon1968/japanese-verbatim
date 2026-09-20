#!/usr/bin/env python3
"""画面変化のフレーム抽出。

スクリーンキャストでは、音声で曖昧だった技術用語はほぼ必ず画面に映っている。
勘でタイムスタンプを選ぶのではなく、画面が変わった瞬間を検出して拾う。

既定は高速モード（キーフレームのみ復号）。画面共有の動画では画面遷移で
キーフレームが入るため、これで十分なことが多い。取りこぼす場合は --full。

使い方は2段階になる。**検出結果をそのまま貼らないこと。**

  1. 候補を出して中身を読む（`.jv/work/frames/` に出る。finish で消える）
  2. 採用する画面だけを時刻指定で抜き直し、`--assets` で成果物側に入れる

シーン検出が返すのは「画面が切り替わった瞬間」であって、話者がその画面を
説明している時刻ではない。前後にずれるので、1の結果をそのまま本文に貼ると
図と説明が食い違う。2で自分が選んだ秒数を指定する。

使い方:
    python grab_frames.py video.mp4                       # 候補を出す
    python grab_frames.py video.mp4 --scene 0.25 --min-interval 15
    python grab_frames.py video.mp4 --at 120,300,1800     # 時刻指定で抜き直す
    python grab_frames.py video.mp4 --at 1896,2076 --assets <成果物名> \
        --crop-bottom 12                                  # 成果物に入れる
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def resolve_ffmpeg() -> str:
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    ff = shutil.which("ffmpeg")
    if not ff:
        sys.exit("ffmpeg が見つかりません（--with imageio-ffmpeg を付けて実行してください）")
    return ff


def detect_scenes(ff: str, video: Path, thr: float, fast: bool, upto: float | None) -> list[float]:
    """シーン変化の時刻を秒で返す。"""
    cmd = [ff, "-nostdin", "-loglevel", "info"]
    if fast:
        cmd += ["-skip_frame", "nokey"]
    if upto:
        cmd += ["-t", str(upto)]
    cmd += ["-i", str(video), "-an",
            "-vf", f"select='gt(scene,{thr})',showinfo", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    return [float(m) for m in re.findall(r"pts_time:(\d+(?:\.\d+)?)", proc.stderr)]


def thin_out(times: list[float], min_interval: float) -> list[float]:
    kept: list[float] = []
    for t in sorted(times):
        if not kept or t - kept[-1] >= min_interval:
            kept.append(t)
    return kept


def mmss(t: float) -> str:
    return f"{int(t)//60:02d}:{int(t)%60:02d}"


def build_vf(width: int, crop_bottom: float, crop_right: float) -> str:
    """切り取ってから縮小する。順序を逆にすると切る量がずれる。

    焼き込み字幕は下端、話者のワイプは右端に出ることが多い。
    どちらも「画面の情報」ではないので、貼る前に落とす。
    """
    parts = []
    if crop_bottom or crop_right:
        w = f"trunc(iw*{1 - crop_right / 100:.4f})"
        h = f"trunc(ih*{1 - crop_bottom / 100:.4f})"
        parts.append(f"crop={w}:{h}:0:0")
    parts.append(f"scale={width}:-1")
    return ",".join(parts)


def main() -> int:
    p = argparse.ArgumentParser(description="画面変化のフレーム抽出")
    p.add_argument("video", type=Path)
    p.add_argument("--out", type=Path, default=Path(".jv/work/frames"))
    p.add_argument("--scene", type=float, default=0.08,
                   help="シーン変化の閾値。画面共有は変化が緩やかなため既定を低くしてある"
                        "（実測: 62分の講演で 0.3→4件、0.08→116件）")
    p.add_argument("--min-interval", type=float, default=20.0, help="最小間隔（秒）")
    p.add_argument("--limit", type=int, default=60, help="抽出する最大枚数")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--crop-bottom", type=float, default=0.0, metavar="PCT",
                   help="下端を切る割合（%%）。焼き込み字幕を落とす。"
                        "実測（62分の講演）: 12 で字幕帯が消える")
    p.add_argument("--crop-right", type=float, default=0.0, metavar="PCT",
                   help="右端を切る割合（%%）。話者のワイプを落とす。"
                        "実測（同じ講演）: 22 で概ね消えるが縁が残る / "
                        "27 で完全に消えるが右端の情報も切れた。"
                        "切りすぎは欠落なので、22 から始めて結果を見ること")
    p.add_argument("--assets", metavar="成果物名",
                   help="成果物側 <成果物名>.assets/ に出す。"
                        "raw.txt 用の [[IMAGE]] 行と本文用の参照も出力する")
    p.add_argument("--full", action="store_true", help="全フレームを復号（低速・高精度）")
    p.add_argument("--upto", type=float, help="検出を先頭からこの秒数までに限る（試験用）")
    p.add_argument("--at", help="検出せず、指定秒のみ抽出（カンマ区切り）")
    p.add_argument("--every", type=float, help="検出せず、一定間隔で抽出（秒）")
    args = p.parse_args()

    ff = resolve_ffmpeg()

    if args.assets:
        # 成果物に入れるのは「自分で選んだ画面」だけ。検出結果の丸写しを許さない。
        if not args.at:
            sys.exit("--assets は --at と併せて使うこと。\n"
                     "先に候補を出して中身を読み、採用する時刻を決めてから抜き直す。\n"
                     "シーン検出の時刻は画面が切り替わった瞬間であって、\n"
                     "話者がその画面を説明している時刻ではない。")
        args.out = Path(f"{args.assets}.assets")

    args.out.mkdir(parents=True, exist_ok=True)

    if args.at:
        times = [float(x) for x in args.at.split(",") if x.strip()]
        how = "指定時刻"
    elif args.every:
        proc = subprocess.run([ff, "-nostdin", "-i", str(args.video)],
                              capture_output=True, text=True, errors="replace")
        m = re.search(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)", proc.stderr)
        dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0
        times = [t for t in range(5, int(dur), int(args.every))]
        how = f"{args.every:.0f}秒間隔"
    else:
        mode = "全フレーム" if args.full else "キーフレームのみ"
        print(f"[run  ] シーン検出中（{mode} / 閾値 {args.scene}）…")
        raw = detect_scenes(ff, args.video, args.scene, not args.full, args.upto)
        print(f"        候補 {len(raw)}件")
        times = thin_out(raw, args.min_interval)
        how = f"シーン検出 thr={args.scene}"

    if len(times) > args.limit:
        step = len(times) / args.limit
        times = [times[int(i * step)] for i in range(args.limit)]
    print(f"[info ] 抽出 {len(times)}枚（{how} / 最小間隔 {args.min_interval:.0f}秒）")

    vf = build_vf(args.width, args.crop_bottom, args.crop_right)
    if args.crop_bottom or args.crop_right:
        print(f"[info ] 切り取り 下{args.crop_bottom:.0f}% / 右{args.crop_right:.0f}%")

    index = []
    for t in times:
        name = (f"screen-{mmss(t).replace(':', '')}.jpg" if args.assets
                else f"f_{int(t):06d}.jpg")
        subprocess.run([ff, "-nostdin", "-loglevel", "error", "-ss", f"{t:.3f}",
                        "-i", str(args.video), "-frames:v", "1",
                        "-vf", vf, "-y", str(args.out / name)],
                       check=False)
        if (args.out / name).exists():
            index.append({"time": round(t, 2), "mmss": mmss(t), "file": name})

    (args.out / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")

    if not args.assets:
        md = ["# 抽出フレーム", "",
              f"出典: `{args.video.name}` / 方法: {how}", "",
              "| 時刻 | ファイル |", "|---|---|"]
        md += [f"| {x['mmss']} | `{x['file']}` |" for x in index]
        (args.out / "index.md").write_text("\n".join(md) + "\n", encoding="utf-8")
        print(f"[done ] {len(index)}枚 -> {args.out}")
        print(f"        目次: {args.out/'index.md'}")
        print("\n**候補である。このまま本文に貼らないこと。**")
        print("1枚ずつ読んで内容を確かめ、採用する画面を決める。")
        print("音声で曖昧だった技術用語は、該当時刻のフレームで照合する。")
        print(f"\n採用が決まったら時刻を指定して抜き直す:")
        print(f"  python grab_frames.py {args.video.name} "
              f"--at <秒,秒,…> --assets <成果物名> --crop-bottom 12")
        return 0

    print(f"[done ] {len(index)}枚 -> {args.out}")
    print("\n--- raw.txt に追記する行（説明は自分で書くこと） ---")
    for x in index:
        print(f"[[IMAGE {Path(x['file']).stem}]] {x['mmss']} の画面 — 〔内容を1行で〕")
    print("\n--- 本文に貼る参照 ---")
    for x in index:
        print(f"![{x['mmss']} の画面 — 〔内容〕]({args.out.name}/{x['file']})")
    print("\n説明を〔〕のまま残さないこと。ゲート2は点数しか数えないので、"
          "空の説明でも通ってしまう。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
