#!/usr/bin/env python3
"""文字起こしの品質ゲート。

自動音声認識は内容を静かに落とす。整った出力が返ってくるため、
数えない限り欠落に気づけない。このスクリプトは失敗しうるチェックを与える。

検査項目:
  1. カバレッジ率      文字化された秒数 / 音声全体の長さ
  2. ギャップの発話判定 ギャップ区間の音量を実測し、沈黙か脱落かを区別する
  3. 窓境界の整合      30秒窓の整数倍のギャップ（= 窓ごと欠落の兆候）
  4. 反復ハルシネーション 同一フレーズの連続反復

終了コード 0 = GREEN、1 = RED、2 = 検証不能（= 合格ではない）。

使い方:
    python verify_transcript.py transcript.json --audio audio.wav
    python verify_transcript.py transcript.json --audio audio.wav --json result.json
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import statistics
import subprocess
import sys
from pathlib import Path

# Windows のコンソール既定は cp932 のため、日本語と記号で落ちる。UTF-8 を明示する。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ---------------------------------------------------------------- ffmpeg

def resolve_ffmpeg() -> str | None:
    """ffmpeg の実行パスを解決する。パスは固定しない（環境ごとに変わるため）。"""
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    return shutil.which("ffmpeg")


def _run_ffmpeg(ff: str, args: list[str]) -> str:
    """ffmpeg を実行し stderr を返す（ffmpeg は情報を stderr に出す）。"""
    proc = subprocess.run(
        [ff, "-nostdin", *args],
        capture_output=True, text=True, errors="replace",
    )
    return proc.stderr


def media_duration(ff: str, path: Path) -> float | None:
    """メディアの長さを秒で返す。"""
    err = _run_ffmpeg(ff, ["-i", str(path)])
    m = re.search(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)", err)
    if not m:
        return None
    h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mi * 60 + s


def mean_volume(ff: str, path: Path, start: float, dur: float) -> float | None:
    """指定区間の平均音量(dB)を返す。"""
    if dur <= 0.05:
        return None
    err = _run_ffmpeg(ff, [
        "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(path),
        "-af", "volumedetect", "-f", "null", "-",
    ])
    m = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", err)
    return float(m.group(1)) if m else None


# ---------------------------------------------------------------- 解析

def load_segments(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):           # {"segments": [...]} 形式にも対応
        data = data.get("segments", [])
    segs = [s for s in data if "start" in s and "end" in s]
    segs.sort(key=lambda s: s["start"])
    return segs


def find_gaps(segs: list[dict], duration: float | None, min_gap: float) -> list[dict]:
    """発話区間の隙間を列挙する。"""
    gaps, prev = [], 0.0
    for s in segs:
        if s["start"] - prev > min_gap:
            gaps.append({"start": round(prev, 2),
                         "end": round(s["start"], 2),
                         "length": round(s["start"] - prev, 2)})
        prev = max(prev, s["end"])
    if duration and duration - prev > min_gap:
        gaps.append({"start": round(prev, 2),
                     "end": round(duration, 2),
                     "length": round(duration - prev, 2)})
    return gaps


def window_aligned(length: float, window: float = 30.0, tol: float = 1.5) -> bool:
    """長さが窓サイズの整数倍に近いか（窓ごと欠落の兆候）。"""
    if length < window - tol:
        return False
    return abs(length - round(length / window) * window) <= tol


def speech_reference(ff: str, audio: Path, segs: list[dict], samples: int = 5) -> float | None:
    """確実に発話がある区間の音量中央値を、比較の基準として求める。"""
    cands = [s for s in segs if s["end"] - s["start"] >= 3.0 and s.get("text", "").strip()]
    if not cands:
        return None
    step = max(1, len(cands) // samples)
    vols = []
    for s in cands[::step][:samples]:
        v = mean_volume(ff, audio, s["start"], min(10.0, s["end"] - s["start"]))
        if v is not None:
            vols.append(v)
    return statistics.median(vols) if vols else None


_WORD = re.compile(r"[A-Za-z0-9']+")


def repeated_phrase(text: str, min_n: int = 3, max_n: int = 8, threshold: int = 4) -> tuple[str, int] | None:
    """同一フレーズの連続反復を検出する（whisper のループ出力の兆候）。"""
    words = _WORD.findall(text.lower())
    for n in range(min_n, max_n + 1):
        if len(words) < n * threshold:
            continue
        i = 0
        while i + n <= len(words):
            phrase = words[i:i + n]
            reps = 1
            j = i + n
            while j + n <= len(words) and words[j:j + n] == phrase:
                reps += 1
                j += n
            if reps >= threshold:
                return " ".join(phrase), reps
            i += 1
    return None


# ---------------------------------------------------------------- 本体

def main() -> int:
    p = argparse.ArgumentParser(description="文字起こしの品質ゲート")
    p.add_argument("transcript", type=Path, help="segments を含む JSON")
    p.add_argument("--audio", type=Path, help="元音声/動画（音量検証に使用）")
    p.add_argument("--duration", type=float, help="長さを秒で明示（--audio がない場合）")
    p.add_argument("--min-coverage", type=float, default=0.95, help="必要カバレッジ率（既定 0.95）")
    p.add_argument("--min-gap", type=float, default=5.0, help="検査対象とするギャップの最小秒数")
    p.add_argument("--speech-margin", type=float, default=6.0,
                   help="発話基準から何dB以内なら発話とみなすか（既定 6.0）")
    p.add_argument("--json", type=Path, default=Path(".jv/audit/gate.json"),
                   help="結果をJSONで書き出す（既定 .jv/audit/gate.json）")
    p.add_argument("--repetition", choices=["fail", "warn"], default="fail",
                   help="反復ハルシネーションの扱い。該当箇所を整理済みなら warn に降格する")
    p.add_argument("--allow-unverified", action="store_true",
                   help="音量検証ができなくても失敗させない（既定では失敗）")
    args = p.parse_args()

    segs = load_segments(args.transcript)
    if not segs:
        print("RED     セグメントが0件です")
        return 1

    ff = resolve_ffmpeg()
    duration = args.duration
    if duration is None and args.audio and ff:
        duration = media_duration(ff, args.audio)

    covered = sum(s["end"] - s["start"] for s in segs)
    words = sum(len(s.get("text", "").split()) for s in segs)
    findings: list[str] = []
    result: dict = {"segments": len(segs), "covered_sec": round(covered, 1), "words": words}

    print("=" * 62)
    print("  文字起こし品質ゲート")
    print("=" * 62)
    print(f"  セグメント数 : {len(segs)}")
    print(f"  文字化された秒数: {covered/60:.1f} 分")
    print(f"  語数         : {words:,}")

    # --- 1. カバレッジ率 -------------------------------------------------
    if duration:
        ratio = covered / duration
        result["duration_sec"] = round(duration, 1)
        result["coverage"] = round(ratio, 4)
        status = "GREEN" if ratio >= args.min_coverage else "RED  "
        print(f"  音声全体     : {duration/60:.1f} 分")
        print(f"\n  [{status}] カバレッジ {ratio*100:.1f}% (下限 {args.min_coverage*100:.0f}%)")
        if ratio < args.min_coverage:
            findings.append(f"カバレッジ不足: {ratio*100:.1f}% — {(duration-covered)/60:.1f}分が未文字化")
        coverage_unverified = False
    else:
        # カバレッジはこのゲートの主目的である。測れなかったことを
        # 「指摘なし」として通すと、いちばん重要な検査を飛ばしたまま
        # GREEN が出る（実測: 素の python で走らせて ffmpeg が解決できず、
        # SKIP のまま終了コード0になった）。検証できないことは合格ではない。
        print("\n  [UNVER] カバレッジ: 音声長が不明のため判定できない")
        print("          --audio を渡し、ffmpeg が解決できる環境で走らせること")
        print("          uv run --no-project --python 3.12 --with imageio-ffmpeg \\")
        print("              python verify_transcript.py … --audio <音声>")
        coverage_unverified = True

    # --- 2/3. ギャップの発話判定 ----------------------------------------
    gaps = find_gaps(segs, duration, args.min_gap)
    result["gaps"] = gaps
    total_gap = sum(g["length"] for g in gaps)
    print(f"\n  [INFO ] {args.min_gap:.0f}秒超のギャップ {len(gaps)}件 / 合計 {total_gap/60:.2f}分")

    aligned = [g for g in gaps if window_aligned(g["length"])]
    if aligned:
        print(f"  [WARN ] うち {len(aligned)}件が30秒窓の整数倍 — 窓ごと欠落の兆候")
        for g in aligned[:5]:
            print(f"          {g['start']/60:6.2f}分〜 長さ {g['length']:.1f}秒")

    unverified = coverage_unverified
    if gaps:
        if not (args.audio and ff):
            print("  [UNVER] ギャップの音量検証: 音声またはffmpegが無いため実行不能")
            unverified = True
        else:
            ref = speech_reference(ff, args.audio, segs)
            if ref is None:
                print("  [UNVER] 発話基準の測定に失敗")
                unverified = True
            else:
                print(f"  [INFO ] 発話区間の基準音量: {ref:.1f} dB")
                speechy = []
                for g in gaps:
                    v = mean_volume(ff, args.audio, g["start"], g["length"])
                    g["mean_volume_db"] = v
                    if v is not None and v >= ref - args.speech_margin:
                        g["verdict"] = "speech-missing"
                        speechy.append(g)
                    else:
                        g["verdict"] = "silence"
                if speechy:
                    lost = sum(g["length"] for g in speechy)
                    print(f"  [RED  ] {len(speechy)}件のギャップが発話レベル — 約{lost/60:.2f}分の脱落")
                    for g in speechy[:6]:
                        print(f"          {g['start']/60:6.2f}分〜 {g['length']:5.1f}秒  "
                              f"{g['mean_volume_db']:.1f} dB (基準 {ref:.1f} dB)")
                    findings.append(
                        f"発話の脱落: {len(speechy)}件・約{lost/60:.2f}分（音量が発話レベル）")
                else:
                    print("  [GREEN] すべてのギャップは沈黙と判定")

    # --- 4. 反復ハルシネーション ----------------------------------------
    loops = []
    for s in segs:
        hit = repeated_phrase(s.get("text", ""))
        if hit:
            loops.append({"start": round(s["start"], 1), "phrase": hit[0], "repeats": hit[1]})
    result["repetition_loops"] = loops
    if loops:
        fatal = args.repetition == "fail"
        print(f"\n  [{'RED  ' if fatal else 'WARN '}] 反復ハルシネーション {len(loops)}件"
              + ("" if fatal else "（確認済みとして降格）"))
        for l in loops[:5]:
            print(f"          {l['start']/60:6.2f}分  \"{l['phrase']}\" ×{l['repeats']}")
        if fatal:
            findings.append(f"反復ハルシネーション: {len(loops)}箇所"
                            "（再実行ではなく該当箇所の整理で対処する）")
    else:
        print("\n  [GREEN] 反復ハルシネーションなし")

    # --- 判定 -------------------------------------------------------------
    print("\n" + "-" * 62)
    if findings:
        code = 1
        print("  判定: RED — 文書作成に進まないこと")
        for f in findings:
            print(f"    · {f}")
        print("\n  対処は references/asr-playbook.md を参照")
    elif unverified and not args.allow_unverified:
        code = 2
        print("  判定: UNVERIFIED — 検証できないことは合格ではない")
        print("    · --audio を指定するか --allow-unverified を明示すること")
    else:
        code = 0
        print("  判定: GREEN")
    print("-" * 62)

    result["verdict"] = {0: "green", 1: "red", 2: "unverified"}[code]
    result["findings"] = findings
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  結果を書き出し: {args.json}")
    return code


if __name__ == "__main__":
    sys.exit(main())
