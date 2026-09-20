#!/usr/bin/env python3
"""文字起こし（差分再実行つき）。

音声抽出と文字起こしの結果をメディアの指紋でキャッシュする。
62分の音声で45分かかる処理を、書式を直すたびに繰り返すのは現実的でない。
取得と生成を分離しておく。

推奨設定は references/asr-playbook.md の根拠に基づく既定値として埋め込んである。
既定の whisper 設定は発話を丸ごと落とすため、ここでは使わない。

実行例:
    uv run --no-project --python 3.12 --with faster-whisper --with imageio-ffmpeg \\
        python transcribe.py video.mp4 --bench
    uv run --no-project --python 3.12 --with faster-whisper --with imageio-ffmpeg \\
        python transcribe.py video.mp4 --model small --beam 5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

TERMS = Path(__file__).resolve().parent.parent / "glossary" / "terms.json"


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


def fingerprint(path: Path, chunk: int = 1 << 20) -> str:
    """先頭・末尾1MBとサイズから指紋を作る。巨大ファイル全体のハッシュは取らない。"""
    h = hashlib.sha256()
    size = path.stat().st_size
    h.update(str(size).encode())
    with path.open("rb") as f:
        h.update(f.read(chunk))
        if size > chunk * 2:
            f.seek(-chunk, 2)
            h.update(f.read(chunk))
    return h.hexdigest()[:16]


def extract_audio(ff: str, src: Path, dst: Path) -> None:
    subprocess.run([ff, "-nostdin", "-loglevel", "error", "-i", str(src),
                    "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                    "-y", str(dst)], check=True)


def slice_audio(ff: str, src: Path, dst: Path, start: float, dur: float) -> None:
    subprocess.run([ff, "-nostdin", "-loglevel", "error", "-ss", str(start),
                    "-t", str(dur), "-i", str(src), "-y", str(dst)], check=True)


def media_duration(ff: str, path: Path) -> float | None:
    proc = subprocess.run([ff, "-nostdin", "-i", str(path)],
                          capture_output=True, text=True, errors="replace")
    m = re.search(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)", proc.stderr)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None


def hotwords() -> str:
    if not TERMS.exists():
        return ""
    d = json.loads(TERMS.read_text(encoding="utf-8"))
    words = list(d.get("hotwords", []))
    for e in d.get("replacements", []):
        if not e.get("regex") and e["right"] not in words:
            words.append(e["right"])
    return ", ".join(words)


def transcribe(model, audio: Path, language: str, beam: int, hw: str):
    return model.transcribe(
        str(audio),
        language=language,
        beam_size=beam,
        hotwords=hw or None,
        vad_filter=False,                 # 既定 True は発話を丸ごと落とす
        condition_on_previous_text=False, # 既定 True は文脈崩壊で窓をスキップする
        no_speech_threshold=0.9,          # 既定 0.6 は無音判定が厳しすぎる
        compression_ratio_threshold=2.8,
    )


def main() -> int:
    p = argparse.ArgumentParser(description="文字起こし（差分再実行つき）")
    p.add_argument("media", type=Path)
    p.add_argument("--model", default="small")
    p.add_argument("--beam", type=int, default=5)
    p.add_argument("--language", default="en")
    p.add_argument("--threads", type=int, default=8)
    # hotwords は既定で渡さない。語の精度は上がるが反復ループを誘発し、
    # その間の発話を丸ごと落とす（asr-playbook の失敗モード3、実測で約440語）。
    # 手順4の事後置換で回復するほうが安全なので、危ないほうを既定にしない。
    p.add_argument("--no-hotwords", action="store_true",
                   help="既定の挙動。互換のために残してある（指定しなくても渡さない）")
    p.add_argument("--hotwords", action="store_true",
                   help="hotwords を渡す。**通常は使わない。** hotwords の有無で"
                        "出力がどう変わるかを比べたいときだけ（asr-playbook 参照）")
    p.add_argument("--out-dir", type=Path, default=Path(".jv/work"))
    p.add_argument("--cache-dir", type=Path, default=Path(".jv/cache"))
    p.add_argument("--bench", action="store_true",
                   help="3分の断片で速度を実測し、所要時間の見積りだけ出す")
    p.add_argument("--bench-at", type=float, default=600.0)
    p.add_argument("--force", action="store_true", help="キャッシュを無視して再実行")
    args = p.parse_args()

    from faster_whisper import WhisperModel  # 遅延import（--bench以外でも同じ）

    if not args.media.exists():
        print(f"エラー: ソースが見つかりません: {args.media}", file=sys.stderr)
        return 1

    ff = resolve_ffmpeg()
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    fp = fingerprint(args.media)
    audio = args.cache_dir / f"{fp}.wav"
    if audio.exists() and not args.force:
        print(f"[cache] 音声: {audio.name}")
    else:
        print("[run  ] 音声を抽出中…")
        extract_audio(ff, args.media, audio)
    duration = media_duration(ff, audio) or 0.0
    print(f"        長さ {duration/60:.1f} 分")

    hw = hotwords() if args.hotwords else ""
    print(f"[info ] hotwords {len(hw.split(','))} 語 — **比較検証のときだけ使うこと**"
          if hw else "[info ] hotwords なし（既定）")

    # --- ベンチ ----------------------------------------------------------
    if args.bench:
        bench = args.cache_dir / f"{fp}.bench.wav"
        slice_audio(ff, audio, bench, args.bench_at, 180)
        print(f"[bench] モデル {args.model} を読み込み中…")
        t0 = time.time()
        m = WhisperModel(args.model, device="cpu", compute_type="int8",
                         cpu_threads=args.threads)
        t1 = time.time()
        segs, _ = transcribe(m, bench, args.language, args.beam, hw)
        txt = " ".join(s.text.strip() for s in segs)
        t2 = time.time()
        rate = 180 / (t2 - t1)
        print(f"\n  読み込み  : {t1-t0:.0f} 秒")
        print(f"  180秒の処理: {t2-t1:.0f} 秒  →  {rate:.2f} 倍速")
        print(f"  全体の見積り: 約 {duration/rate/60:.0f} 分")
        print(f"\n  抜粋: {txt[:200]}…")
        return 0

    # --- 本実行 ----------------------------------------------------------
    # hotwords の有無は結果を変える。キーに入れないと2パスが同じ箱に落ちて
    # 突き合わせができなくなる。
    tag = "" if hw else ".nohw"
    cache = args.cache_dir / f"{fp}.{args.model}.b{args.beam}{tag}.json"
    if cache.exists() and not args.force:
        print(f"[cache] 文字起こし: {cache.name}（--force で再実行）")
        out = json.loads(cache.read_text(encoding="utf-8"))
    else:
        print(f"[run  ] モデル {args.model} を読み込み中…")
        m = WhisperModel(args.model, device="cpu", compute_type="int8",
                         cpu_threads=args.threads)
        t0 = time.time()
        segs, _ = transcribe(m, audio, args.language, args.beam, hw)
        out = []
        for s in segs:
            out.append({"start": s.start, "end": s.end, "text": s.text.strip()})
            if len(out) % 100 == 0:
                el = time.time() - t0
                eta = (duration - s.start) / (s.start / el) / 60 if s.start > 0 else 0
                print(f"        {s.start/60:5.1f}分 経過{el/60:4.1f}分 残り約{eta:.0f}分",
                      flush=True)
        cache.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[done ] {time.time()-t0:.0f} 秒")

    (args.out_dir / "transcript.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.out_dir / "transcript.txt").write_text(
        "\n".join(f"[{int(s['start'])//60:02d}:{int(s['start'])%60:02d}] {s['text']}"
                  for s in out) + "\n", encoding="utf-8")

    print(f"\n出力: {args.out_dir/'transcript.json'} / transcript.txt")
    print(f"音声: {audio}")
    print("\n次の手順（ゲートを必ず通すこと）:")
    print(f"  python verify_transcript.py {args.out_dir/'transcript.json'} --audio {audio} --json gate.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
