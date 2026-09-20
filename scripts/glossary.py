#!/usr/bin/env python3
"""用語辞書 — 誤認識を恒久的な制約に変える。

辞書は文字起こし「後」に働く。既知の誤りを置換し、直した箇所を記録する。
そして毎回 add で育てる。一度潰した誤りに二度と出会わないための仕組みであり、
これを飛ばすと辞書はただの初期設定になる。

**hotwords として事前に渡してはならない。** かつてはそれが主用途だったが、
実測で反復ループを誘発することが分かった。ループは窓を食いつぶし、その間の発話が
出力から丸ごと消える。同じ講演で hotwords あり5箇所・約440語の欠落に対し、
なしでは0件だった（references/asr-playbook.md の失敗モード3）。
しかも辞書が育つほど語数が増え、悪化する。

`hotwords` サブコマンドは比較検証のために残してある。通常の手順では使わない。

辞書に入れてよいのは語・短い固有名詞まで。長いフレーズを蓄積しないこと。

使い方:
    python glossary.py apply transcript.txt -o fixed.txt --report gloss.json
    python glossary.py add --wrong CloudMD --right CLAUDE.md --note 頻出
    python glossary.py report gloss.json
    python glossary.py list
    python glossary.py hotwords    # 比較検証用。通常は使わない
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

# Windows のコンソール既定は cp932 のため、日本語と記号で落ちる。UTF-8 を明示する。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DEFAULT_TERMS = Path(__file__).resolve().parent.parent / "glossary" / "terms.json"


def today() -> str:
    return _dt.date.today().isoformat()


def load(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "updated": today(), "hotwords": [], "replacements": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, data: dict) -> None:
    data["updated"] = today()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def build_pattern(entry: dict) -> re.Pattern:
    """語境界を意識したパターンを作る。ドットを含む語（CLAUDE.md）にも対応する。"""
    if entry.get("regex"):
        src = entry["wrong"]
    else:
        # 文字起こしは行折り返しされるため、語間の空白は改行にもなりうる
        body = r"\s+".join(re.escape(w) for w in entry["wrong"].split())
        src = r"(?<![A-Za-z0-9_])" + body + r"(?![A-Za-z0-9_])"
    flags = re.IGNORECASE if entry.get("ci", True) else 0
    return re.compile(src, flags)


# ---------------------------------------------------------------- 各コマンド

def cmd_hotwords(args) -> int:
    """whisper の hotwords に渡す文字列を出力する。

    **通常の手順では使わない。** 反復ループを誘発するため、文字起こしは
    `transcribe.py --no-hotwords` で回す。残してあるのは、hotwords の有無で
    出力がどう変わるかを比べたいときのため。
    """
    data = load(args.terms)
    words = list(data.get("hotwords", []))
    # 置換辞書の「正しい側」も語彙バイアスとして有用
    for e in data.get("replacements", []):
        if not e.get("regex") and e["right"] not in words:
            words.append(e["right"])
    print(", ".join(words))
    return 0


def cmd_apply(args) -> int:
    """既知の誤認識を置換し、レビュー対象を報告する。"""
    data = load(args.terms)
    text = args.input.read_text(encoding="utf-8")
    applied, flagged = [], []

    for e in data.get("replacements", []):
        pat = build_pattern(e)
        hits = pat.findall(text)
        if not hits:
            continue
        rec = {"wrong": e["wrong"], "right": e["right"],
               "count": len(hits), "note": e.get("note", "")}
        if e.get("mode", "auto") == "review":
            flagged.append(rec)          # 文脈依存のため自動置換しない
        else:
            text = pat.sub(e["right"], text)
            applied.append(rec)

    out = args.output or args.input
    out.write_text(text, encoding="utf-8")

    total = sum(r["count"] for r in applied)
    print(f"置換: {len(applied)}種 / {total}箇所 -> {out}")
    for r in sorted(applied, key=lambda x: -x["count"]):
        print(f"  {r['count']:4d}  {r['wrong']!r} -> {r['right']!r}")
    if flagged:
        print(f"\n要確認（自動置換しない）: {len(flagged)}種")
        for r in flagged:
            print(f"  {r['count']:4d}  {r['wrong']!r} ~ {r['right']!r}  {r['note']}")

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(
            {"source": str(args.input), "applied": applied, "flagged": flagged},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nレポート: {args.report}")
    return 0


def cmd_add(args) -> int:
    """新しく見つけた誤認識を辞書に追加する（学習エッジ）。"""
    data = load(args.terms)
    reps = data.setdefault("replacements", [])
    for e in reps:
        if e["wrong"].lower() == args.wrong.lower():
            print(f"既存: {e['wrong']!r} -> {e['right']!r}  （変更なし）")
            return 0
    if len(args.wrong.split()) > 4:
        print("拒否: 4語を超える句は辞書に入れないこと（語・短い固有名詞のみ）", file=sys.stderr)
        return 1
    reps.append({"wrong": args.wrong, "right": args.right,
                 "mode": args.mode, "ci": not args.case_sensitive,
                 "note": args.note or "", "added": today()})
    save(args.terms, data)
    print(f"追加: {args.wrong!r} -> {args.right!r}  (mode={args.mode})  計{len(reps)}件")
    return 0


def cmd_report(args) -> int:
    """apply のレポートから、成果物に添付する精度付録のMarkdownを生成する。"""
    rep = json.loads(args.report_json.read_text(encoding="utf-8"))
    lines = ["## 付録：文字起こしの精度について", "",
             "本ドキュメントは自動音声認識に基づくため、以下の点にご留意ください。", ""]

    if rep.get("applied"):
        lines += ["**修正した誤認識（用語辞書により自動置換）**", "",
                  "| 認識結果 | 正しい表記 | 箇所数 |", "|---|---|---|"]
        for r in sorted(rep["applied"], key=lambda x: -x["count"]):
            lines.append(f"| `{r['wrong']}` | `{r['right']}` | {r['count']} |")
        lines.append("")

    if rep.get("flagged"):
        lines += ["**文脈に応じて個別に判断した箇所**", "",
                  "| 認識結果 | 想定される表記 | 箇所数 | 備考 |", "|---|---|---|---|"]
        for r in rep["flagged"]:
            lines.append(f"| `{r['wrong']}` | `{r['right']}` | {r['count']} | {r['note']} |")
        lines.append("")

    if not rep.get("applied") and not rep.get("flagged"):
        lines += ["既知の誤認識パターンは検出されませんでした。", ""]

    md = "\n".join(lines)
    if args.output:
        args.output.write_text(md + "\n", encoding="utf-8")
        print(f"生成: {args.output}")
    else:
        print(md)
    return 0


def cmd_list(args) -> int:
    data = load(args.terms)
    reps = data.get("replacements", [])
    print(f"辞書: {args.terms}")
    print(f"更新: {data.get('updated', '-')} / hotwords {len(data.get('hotwords', []))}語 / 置換 {len(reps)}件\n")
    for e in reps:
        mark = "auto  " if e.get("mode", "auto") == "auto" else "review"
        print(f"  [{mark}] {e['wrong']!r} -> {e['right']!r}"
              + (f"   # {e['note']}" if e.get("note") else ""))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="用語辞書")
    p.add_argument("--terms", type=Path, default=DEFAULT_TERMS, help="辞書ファイル")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("hotwords",
                   help="語彙を出力（比較検証用。通常は使わない — ループを誘発する）"
                   ).set_defaults(func=cmd_hotwords)

    a = sub.add_parser("apply", help="既知の誤認識を置換")
    a.add_argument("input", type=Path)
    a.add_argument("-o", "--output", type=Path)
    a.add_argument("--report", type=Path)
    a.set_defaults(func=cmd_apply)

    d = sub.add_parser("add", help="辞書に追加（学習エッジ）")
    d.add_argument("--wrong", required=True)
    d.add_argument("--right", required=True)
    d.add_argument("--note", default="")
    d.add_argument("--mode", choices=["auto", "review"], default="auto")
    d.add_argument("--case-sensitive", action="store_true")
    d.set_defaults(func=cmd_add)

    r = sub.add_parser("report", help="精度付録のMarkdownを生成")
    r.add_argument("report_json", type=Path)
    r.add_argument("-o", "--output", type=Path)
    r.set_defaults(func=cmd_report)

    sub.add_parser("list", help="辞書の内容を表示").set_defaults(func=cmd_list)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
