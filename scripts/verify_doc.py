#!/usr/bin/env python3
"""文書契約ゲート。

成果物が「原文訳／補足／図解」の三層構成を守っているかを機械的に検査する。
文書は整って見えるほど欠落に気づきにくい。だから目視ではなく数える。

検査項目:
  1. 出典の明示        冒頭に出典・取得日時がある
  2. 原文訳ブロック    各セクションに存在し、引用記法になっている
  3. 補足ブロック      各セクションに存在する
  4. 図の帰属          原文にない図である旨の断りが冒頭にある
  5. コードフェンス    開閉が対応している
  6. Mermaid構文       図種別の宣言がある
  7. 精度付録          --require-appendix 指定時

終了コード 0 = GREEN、1 = RED。

使い方:
    python verify_doc.py 成果物.md
    python verify_doc.py 成果物.md --require-appendix --exempt '^(目次|付録|呼びかけ)'
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 既定で本文セクションとみなさない見出し
DEFAULT_EXEMPT = r"^\s*(目次|付録|出典|原典|参考|索引|謝辞|ライセンス)"

# 出典表記の揺れ
SOURCE_LABELS = ("出典", "原典", "ソース", "Source")

# セクション内に置くと、そのセクションを契約検査から外す明示マーカー。
# 「補足が無い」ことを黙って見逃すのではなく、意図的な判断として記録させる。
#
# マーカー名はスキル名から意図的に切り離してある。文書に埋め込まれる文字列が
# スキル名を追跡すると、改名のたびに既存文書が壊れるため。
EXEMPT_MARKERS = (
    "<!-- contract:exempt -->",
    "<!-- s2md:exempt -->",   # 旧称。既存文書のために受け付ける
)

MERMAID_KINDS = (
    "flowchart", "graph", "sequenceDiagram", "stateDiagram", "classDiagram",
    "erDiagram", "journey", "gantt", "pie", "mindmap", "timeline", "quadrantChart",
    "gitGraph", "C4Context", "sankey", "xychart",
)


def split_sections(lines: list[str]) -> list[dict]:
    """`## ` 見出しで本文を分割する（コードフェンス内の # は無視）。"""
    sections, cur, fence = [], None, False
    for i, ln in enumerate(lines, 1):
        if ln.lstrip().startswith("```"):
            fence = not fence
        if not fence and ln.startswith("## ") and not ln.startswith("### "):
            if cur:
                sections.append(cur)
            cur = {"title": ln[3:].strip(), "line": i, "body": []}
        elif cur:
            cur["body"].append(ln)
    if cur:
        sections.append(cur)
    return sections


# 罫線文字。これで自分で作図してはならない（訳者の図は Mermaid で描く）。
BOX_CHARS = set("┌┐└┘├┤┬┴┼─│━┃╔╗╚╝║═▏▕")


def check_ascii_art(lines: list[str], sections: list[dict], min_rows: int = 3) -> list[str]:
    """罫線文字による自作の図を検出する。

    原文が持つASCII図をそのまま再現するのは許される（原文訳・画面ブロック内）。
    それ以外の場所にある罫線作図は、訳者が描いたものとみなして指摘する。
    画像をASCIIアートで再現すると、ほぼ確実に原文より分かりにくくなる。
    """
    # 原文訳／画面ブロックの行範囲を集める
    allowed: list[tuple[int, int]] = []
    for s in sections:
        start = s["line"]
        cur_head, cur_from = None, None
        for off, ln in enumerate(s["body"], start + 1):
            m = re.match(r"^#{3,4}\s*(.+?)\s*$", ln)
            if m:
                if cur_head in ("原文訳", "画面", "原文ブロック") and cur_from:
                    allowed.append((cur_from, off - 1))
                cur_head, cur_from = m.group(1), off
        if cur_head in ("原文訳", "画面", "原文ブロック") and cur_from:
            allowed.append((cur_from, start + len(s["body"])))

    def in_allowed(n: int) -> bool:
        return any(a <= n <= b for a, b in allowed)

    problems, fence_open, opened_at, buf = [], False, 0, []
    for i, ln in enumerate(lines, 1):
        if ln.lstrip().startswith("```"):
            if not fence_open:
                fence_open, opened_at, buf = True, i, []
            else:
                rows = sum(1 for x in buf if BOX_CHARS & set(x))
                if rows >= min_rows and not in_allowed(opened_at):
                    problems.append(
                        f"{opened_at}行: 罫線文字による作図（{rows}行）"
                        " — 訳者の図は Mermaid で描くこと")
                fence_open = False
            continue
        if fence_open:
            buf.append(ln)
    return problems


def check_fences(lines: list[str]) -> list[str]:
    """コードフェンスの開閉とMermaidの図種別を検査する。"""
    problems, fence_open, opened_at, lang = [], False, 0, ""
    for i, ln in enumerate(lines, 1):
        s = ln.lstrip()
        if not s.startswith("```"):
            continue
        if not fence_open:
            fence_open, opened_at, lang = True, i, s[3:].strip()
        else:
            if lang == "mermaid":
                body = [x.strip() for x in lines[opened_at:i - 1] if x.strip()]
                if not body:
                    problems.append(f"{opened_at}行: mermaid ブロックが空")
                elif not body[0].split()[0].rstrip(";").startswith(MERMAID_KINDS):
                    problems.append(
                        f"{opened_at}行: mermaid の図種別が不明 (先頭 {body[0][:30]!r})")
            fence_open = False
    if fence_open:
        problems.append(f"{opened_at}行: コードフェンスが閉じていない")
    return problems


def main() -> int:
    p = argparse.ArgumentParser(description="文書契約ゲート")
    p.add_argument("doc", type=Path)
    p.add_argument("--exempt", default=DEFAULT_EXEMPT,
                   help="本文セクションとみなさない見出しの正規表現")
    p.add_argument("--require-appendix", action="store_true",
                   help="精度に関する付録を必須にする（音声ソースの場合）")
    p.add_argument("--raw", type=Path,
                   help="出典テキスト（[[IMAGE ...]] の取りこぼしを検査する）")
    p.add_argument("--toc-threshold", type=int, default=200,
                   help="この行数以上なら目次を必須にする（既定 200）")
    p.add_argument("--translation-label", default="原文訳")
    p.add_argument("--note-label", default="補足")
    args = p.parse_args()

    text = args.doc.read_text(encoding="utf-8")
    lines = text.splitlines()
    exempt = re.compile(args.exempt)
    findings: list[str] = []

    print("=" * 62)
    print("  文書契約ゲート")
    print("=" * 62)
    print(f"  対象: {args.doc.name}  ({len(lines)}行)")

    # --- 1. 出典 ---------------------------------------------------------
    head = "\n".join(lines[:40])
    has_src = any(l in head for l in SOURCE_LABELS) and re.search(r"https?://|[A-Za-z0-9_.-]+\.(mp4|mov|pdf|wav|m4a)", head)
    print(f"\n  [{'GREEN' if has_src else 'RED  '}] 出典の明示")
    if not has_src:
        findings.append("冒頭40行に出典とURLが見つからない")

    # --- 4. 図の帰属 -----------------------------------------------------
    has_mermaid = "```mermaid" in text
    attributed = bool(re.search(r"訳者|原文に(は)?(図は)?(含まれ|存在し|ない)", head))
    if has_mermaid:
        print(f"  [{'GREEN' if attributed else 'RED  '}] 図の帰属の明示")
        if not attributed:
            findings.append("図があるのに、訳者による補足である旨の断りが冒頭にない")
    else:
        print("  [SKIP ] 図の帰属（図なし）")

    # --- 7. 精度付録 -----------------------------------------------------
    if args.require_appendix:
        ok = re.search(r"##.*付録.*(精度|文字起こし)", text) is not None
        print(f"  [{'GREEN' if ok else 'RED  '}] 精度に関する付録")
        if not ok:
            findings.append("精度に関する付録が見つからない（音声ソースでは必須）")

    # --- 5/6. フェンスとMermaid ------------------------------------------
    fence_problems = check_fences(lines)
    print(f"  [{'GREEN' if not fence_problems else 'RED  '}] コードフェンス / Mermaid 構文")
    for msg in fence_problems[:8]:
        print(f"          {msg}")
    if fence_problems:
        findings.append(f"フェンス/Mermaid の問題 {len(fence_problems)}件")

    # --- 目次 -------------------------------------------------------------
    if len(lines) >= args.toc_threshold:
        has_toc = re.search(r"^##\s*目次", text, re.M) is not None
        print(f"  [{'GREEN' if has_toc else 'RED  '}] 目次"
              f"（{args.toc_threshold}行以上の文書では必須）")
        if not has_toc:
            findings.append(f"目次がない（{len(lines)}行の文書）")

    # --- 出典画像の取りこぼし ---------------------------------------------
    if args.raw and args.raw.exists():
        raw = args.raw.read_text(encoding="utf-8")
        marks = re.findall(r"\[\[IMAGE\s+([A-Za-z0-9_-]+)\]\]\s*(.*)", raw)
        informative = [m for m in marks if "decorative" not in m[1].lower()]
        refs = len(re.findall(r"!\[[^\]]*\]\(", text))
        if informative:
            ok = refs >= len(informative)
            print(f"  [{'GREEN' if ok else 'RED  '}] 出典画像の組み込み"
                  f"（情報を持つ画像 {len(informative)}点 / 本文の参照 {refs}件）")
            if not ok:
                findings.append(
                    f"出典の画像 {len(informative)}点に対し本文の参照が {refs}件 — "
                    "fetch_images.py で取得して参照すること（ASCIIで描き起こさない）")

    # --- 2/3. セクションごとの三層構成 -----------------------------------
    sections = split_sections(lines)

    # --- 自作ASCII図 ------------------------------------------------------
    art = check_ascii_art(lines, sections)
    print(f"  [{'GREEN' if not art else 'RED  '}] 罫線文字による自作の図")
    for msg in art[:8]:
        print(f"          {msg}")
    if len(art) > 8:
        print(f"          ... 他 {len(art)-8}件")
    if art:
        findings.append(f"訳者が罫線文字で作図している {len(art)}件"
                        "（Mermaid で描くか、画像は説明に留める）")

    marked = [s for s in sections
              if any(m in chr(10).join(s["body"]) for m in EXEMPT_MARKERS)]
    body_secs = [s for s in sections
                 if not exempt.match(s["title"]) and s not in marked]
    print(f"\n  [INFO ] セクション {len(sections)}件"
          f"（本文 {len(body_secs)}件 / 見出し免除 {len(sections)-len(body_secs)-len(marked)}件"
          f" / 明示免除 {len(marked)}件）")

    missing_tr, missing_note, not_quoted = [], [], []
    for s in body_secs:
        body = "\n".join(s["body"])
        if args.translation_label not in body:
            missing_tr.append(s)
            continue
        # 原文訳の直後が引用記法になっているか
        seg = body.split(args.translation_label, 1)[1]
        seg_head = [x for x in seg.splitlines()[:8] if x.strip()]
        if not any(x.lstrip().startswith((">", "```", "|")) for x in seg_head):
            not_quoted.append(s)
        if args.note_label not in body:
            missing_note.append(s)

    def report(label: str, items: list[dict], msg: str) -> None:
        status = "GREEN" if not items else "RED  "
        print(f"  [{status}] {label}")
        for s in items[:6]:
            print(f"          {s['line']:5d}行  {s['title'][:44]}")
        if items:
            findings.append(f"{msg}: {len(items)}セクション")

    report(f"「{args.translation_label}」ブロック", missing_tr, "原文訳ブロックの欠落")
    report(f"「{args.note_label}」ブロック", missing_note, "補足ブロックの欠落")
    report("原文訳の引用記法", not_quoted, "原文訳が引用記法になっていない")

    # --- 判定 -------------------------------------------------------------
    print("\n" + "-" * 62)
    if findings:
        print("  判定: RED")
        for f in findings:
            print(f"    · {f}")
        print("-" * 62)
        return 1
    print("  判定: GREEN")
    print("-" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
