#!/usr/bin/env python3
"""忠実性の機械チェック。

「この訳は忠実ですか」とモデルに尋ねるのは、生成側と評価側が同じ推論器である以上、
独立した検証にならない。そこで機械的に照合する。

原文訳ブロックに現れるラテン文字トークン（コマンド名・ファイル名・識別子・
バージョン番号）は、すべて出典に存在していなければならない。
存在しないものは、補足の内容が訳に漏れ出したか、モデルが補完した疑いがある。

限界（重要）:
  日本語の訳文そのものの忠実性は検査できない。検査できるのは固有名詞・識別子の
  出所だけである。ただし捏造がもっとも危険なのはまさにそこ（存在しないコマンド名、
  誤ったバージョン番号、実在しないファイルパス）なので、費用対効果は高い。

終了コード 0 = GREEN、1 = RED。

使い方:
    python verify_fidelity.py 成果物.md --source transcript.txt
    python verify_fidelity.py 成果物.md --source a.txt --source b.txt --check-numbers
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 識別子・コマンド名・ファイル名の形をしたトークン
TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[._/@-][A-Za-z0-9]+)*")
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")

# 訳文の体裁として現れうる、出典に無くても問題にしない語
STOPWORDS = {
    "mermaid", "flowchart", "sequencediagram", "statediagram", "classdiagram",
    "graph", "td", "lr", "tb", "rl", "bt", "fill", "style", "note", "over",
    "participant", "subgraph", "end", "http", "https", "www", "com", "md",
    "the", "and", "for", "not", "you", "your", "with", "this", "that",
}


def norm(s: str) -> str:
    """全角・半角を揃えて小文字化する。"""
    return unicodedata.normalize("NFKC", s).lower()


def squash(s: str) -> str:
    """区切り文字を落として英数字だけにする。

    話し言葉の "code reviewer" と正規表記の "code-reviewer"、
    "settings JSON" と "settings.json" を同一視するために必要。
    これをしないと、正当な表記の正規化がすべて誤検出になる。
    """
    return re.sub(r"[^a-z0-9]+", "", norm(s))


def extract_translation_blocks(text: str, label: str) -> list[tuple[int, str]]:
    """`### 原文訳` 見出しから次の見出しまでを取り出す。"""
    lines = text.splitlines()
    blocks, cur, start = [], None, 0
    for i, ln in enumerate(lines, 1):
        if re.match(rf"^#{{2,4}}\s*{re.escape(label)}\s*$", ln.strip()):
            if cur is not None:
                blocks.append((start, "\n".join(cur)))
            cur, start = [], i
        elif cur is not None and re.match(r"^#{2,4}\s", ln):
            blocks.append((start, "\n".join(cur)))
            cur = None
        elif cur is not None:
            cur.append(ln)
    if cur is not None:
        blocks.append((start, "\n".join(cur)))
    return blocks


def main() -> int:
    p = argparse.ArgumentParser(description="忠実性の機械チェック")
    p.add_argument("doc", type=Path)
    p.add_argument("--source", type=Path, action="append", required=True,
                   help="出典テキスト（複数指定可）")
    p.add_argument("--label", default="原文訳")
    p.add_argument("--min-len", type=int, default=3, help="検査するトークンの最小長")
    p.add_argument("--check-numbers", action="store_true",
                   help="数値も検査する（日本語の万・億表記で誤検出しやすい）")
    p.add_argument("--ignore", action="append", default=[],
                   help="無視するトークン（繰り返し指定可）")
    p.add_argument("--ignore-file", type=Path,
                   help="無視するトークンの一覧（1行1件、# 以降は理由）。"
                        "判断をファイルに残すことで、後から見直せるようにする")
    p.add_argument("--max-report", type=int, default=25)
    args = p.parse_args()

    doc = args.doc.read_text(encoding="utf-8")
    raw_src = "\n".join(s.read_text(encoding="utf-8") for s in args.source)
    # 文字起こしの [MM:SS] は足場であって内容ではない。語の連結を妨げるので落とす。
    raw_src = re.sub(r"\[\d{1,3}:\d{2}(?::\d{2})?\]", " ", raw_src)
    src, src_sq = norm(raw_src), squash(raw_src)

    ignore = STOPWORDS | {norm(x) for x in args.ignore}
    if args.ignore_file and args.ignore_file.exists():
        for ln in args.ignore_file.read_text(encoding="utf-8").splitlines():
            tok = ln.split("#", 1)[0].strip()
            if tok:
                ignore.add(norm(tok))

    blocks = extract_translation_blocks(doc, args.label)

    print("=" * 62)
    print("  忠実性の機械チェック")
    print("=" * 62)
    print(f"  対象   : {args.doc.name}")
    print(f"  出典   : {', '.join(s.name for s in args.source)}  ({len(src):,}文字)")
    print(f"  {args.label}ブロック: {len(blocks)}件")

    if not blocks:
        print("\n  [RED  ] 原文訳ブロックが1件も見つからない")
        return 1

    suspects: dict[str, list[int]] = {}
    checked = 0
    for line_no, body in blocks:
        cands = list(TOKEN.findall(body))
        if args.check_numbers:
            cands += NUMBER.findall(body)
        for tok in cands:
            n = norm(tok)
            if len(n) < args.min_len or n in ignore:
                continue
            checked += 1
            sq = squash(tok)
            if n not in src and (not sq or sq not in src_sq):
                suspects.setdefault(tok, []).append(line_no)

    print(f"  検査トークン: {checked:,}件 / 種類 {len(set(map(norm, suspects)))+0:,}件が未照合")

    if suspects:
        print(f"\n  [RED  ] 出典に見つからないトークン {len(suspects)}種")
        for tok, locs in sorted(suspects.items(), key=lambda x: -len(x[1]))[:args.max_report]:
            where = ", ".join(f"{l}行付近" for l in locs[:3])
            print(f"          {tok!r}  ×{len(locs)}  ({where})")
        if len(suspects) > args.max_report:
            print(f"          ... 他 {len(suspects)-args.max_report}種")
        print("\n  いずれかに該当するはず:")
        print("    · 補足の内容が原文訳ブロックに漏れている")
        print("    · 画面など音声以外を出典とする情報（出典に追加するか、画面ブロックへ移す）")
        print("    · モデルが補完した（要修正）")
        print("    · 出典側の表記揺れ（--ignore で除外）")
    else:
        print("\n  [GREEN] 原文訳ブロックのトークンはすべて出典に存在")

    print("\n" + "-" * 62)
    print("  判定: " + ("RED" if suspects else "GREEN"))
    print("  注: 日本語訳文そのものの忠実性は本検査の対象外")
    print("-" * 62)
    return 1 if suspects else 0


if __name__ == "__main__":
    sys.exit(main())
