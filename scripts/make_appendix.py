#!/usr/bin/env python3
"""精度付録の自動生成。

品質ゲートと用語辞書の結果を統合して、成果物に添付する付録を作る。
「どこを直したか」「どこが検証できていないか」を読み手が判断できる形で必ず示す。

手で書くと、都合の悪い項目が落ちる。機械が書けば落ちない。

使い方:
    python make_appendix.py --gate gate.json --glossary gloss.json -o appendix.md
    python make_appendix.py --gate gate.json --method "faster-whisper small / beam=5"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def mmss(sec: float) -> str:
    return f"{int(sec)//60:02d}:{int(sec)%60:02d}"


def main() -> int:
    p = argparse.ArgumentParser(description="精度付録の生成")
    p.add_argument("--gate", type=Path, help="verify_transcript.py --json の出力")
    p.add_argument("--glossary", type=Path, help="glossary.py apply --report の出力")
    p.add_argument("--exempt-file", type=Path, help="忠実性チェックの免除一覧")
    p.add_argument("--method", default="", help="文字起こしの方法（モデル名・設定）")
    p.add_argument("--title", default="付録：文字起こしの精度について")
    p.add_argument("-o", "--output", type=Path)
    args = p.parse_args()

    L: list[str] = [f"## {args.title}", "",
                    "本ドキュメントは自動音声認識に基づくため、以下の点にご留意ください。", ""]

    # --- 取得方法とカバレッジ -------------------------------------------
    if args.gate and args.gate.exists():
        g = json.loads(args.gate.read_text(encoding="utf-8"))
        L.append("**取得の概要**")
        L.append("")
        L.append("| 項目 | 値 |")
        L.append("|---|---|")
        if args.method:
            L.append(f"| 方法 | {args.method} |")
        if "duration_sec" in g:
            L.append(f"| 音声の長さ | {g['duration_sec']/60:.1f} 分 |")
        L.append(f"| 文字化された長さ | {g['covered_sec']/60:.1f} 分 |")
        if "coverage" in g:
            L.append(f"| カバレッジ | **{g['coverage']*100:.1f}%** |")
        L.append(f"| セグメント数 | {g['segments']:,} |")
        L.append(f"| 語数 | {g['words']:,} |")
        L.append(f"| 品質ゲートの判定 | `{g.get('verdict', '-')}` |")
        L.append("")

        gaps = [x for x in g.get("gaps", []) if x.get("verdict") == "speech-missing"]
        if gaps:
            lost = sum(x["length"] for x in gaps)
            L += [f"**未文字化の区間（発話レベルと判定：計 {lost/60:.2f} 分）**", "",
                  "| 開始 | 長さ | 平均音量 |", "|---|---|---|"]
            for x in gaps:
                L.append(f"| {mmss(x['start'])} | {x['length']:.1f}秒 | {x.get('mean_volume_db', '-')} dB |")
            L += ["", "> これらの区間の内容は本ドキュメントに含まれていません。", ""]
        elif g.get("gaps"):
            L += [f"発話の欠落は検出されていません"
                  f"（{len(g['gaps'])}件のギャップはいずれも沈黙と判定）。", ""]

        loops = g.get("repetition_loops", [])
        if loops:
            L += ["**同一フレーズの反復が検出された箇所**", "",
                  "| 時刻 | フレーズ | 回数 |", "|---|---|---|"]
            for x in loops:
                L.append(f"| {mmss(x['start'])} | `{x['phrase']}` | {x['repeats']} |")
            L += ["", "> 認識側のループか話者の実際の反復かを判断し、"
                  "重複を除いて訳出しています。", ""]

    # --- 用語辞書 --------------------------------------------------------
    if args.glossary and args.glossary.exists():
        rep = json.loads(args.glossary.read_text(encoding="utf-8"))
        if rep.get("applied"):
            L += ["**修正した誤認識（用語辞書により自動置換）**", "",
                  "| 認識結果 | 正しい表記 | 箇所数 |", "|---|---|---|"]
            for r in sorted(rep["applied"], key=lambda x: -x["count"]):
                L.append(f"| `{r['wrong']}` | `{r['right']}` | {r['count']} |")
            L.append("")
        if rep.get("flagged"):
            L += ["**文脈に応じて個別に判断した箇所**", "",
                  "| 認識結果 | 想定される表記 | 箇所数 | 備考 |", "|---|---|---|---|"]
            for r in rep["flagged"]:
                L.append(f"| `{r['wrong']}` | `{r['right']}` | {r['count']} | {r.get('note','')} |")
            L.append("")

    # --- 忠実性チェックの免除 --------------------------------------------
    if args.exempt_file and args.exempt_file.exists():
        rows = []
        for ln in args.exempt_file.read_text(encoding="utf-8").splitlines():
            if not ln.strip() or ln.lstrip().startswith("#"):
                continue
            tok, _, why = ln.partition("#")
            rows.append((tok.strip(), why.strip()))
        if rows:
            L += ["**出典の表記から正規化した語（忠実性チェックの免除項目）**", "",
                  "| 本文の表記 | 理由 |", "|---|---|"]
            for tok, why in rows:
                L.append(f"| `{tok}` | {why} |")
            L.append("")

    L += ["**その他**", "",
          "- 自動認識のため、上表に挙げた以外にも細かな誤りが残っている可能性があります。",
          "- 判断に迷った箇所は本文中に「〔聞き取り不明瞭〕」と明示しています。", ""]

    md = "\n".join(L)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(md + "\n", encoding="utf-8")
        print(f"生成: {args.output}  ({len(L)}行)")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
