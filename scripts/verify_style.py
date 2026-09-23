#!/usr/bin/env python3
"""文体の一貫性ゲート。

規約は次の2点（SKILL.md「文体」）。

  原文訳       敬体（です・ます）
  📝補足・付録  常体（である）

ただし原文訳には例外がある。原文が短文を並べて畳みかける箇所
（失敗の列挙、対句、節を締める箴言）では、敬体が原文の調子を壊す。
そこは常体のままでよい。**ただしその段落の中では揃える。**

だからこのチェッカーは「常体を見つけたら叱る」ようには作っていない。
それをやると規約自身と矛盾し、原文に忠実であろうとした訳を機械が壊す。

判定するのは「揺れ」であって「文体」ではない:

  RED   補足ブロックに敬体が混ざっている（例外なし。訳者の声は常体）
  RED   原文訳で常体が1文だけ孤立している（前後が敬体 = 推敲の漏れ）
  INFO  原文訳で常体が2文以上続いている（例外の候補。意図的なら補足に理由を書く）

終了コード 0 = GREEN、1 = RED。

使い方:
    python verify_style.py 成果物.md
    python verify_style.py 成果物.md --show-runs   # 例外候補も一覧する
    python verify_style.py 成果物.md --japanese-source   # ソースが日本語（原文訳が無い）
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

# 見出しで文体の期待が変わる。verify_doc.py のブロック名に合わせる。
POLITE_BLOCKS = ("原文訳",)
PLAIN_BLOCKS = ("📝 補足", "補足")
# 画面・原文ブロックは原語のまま残す場所なので文体を見ない
SKIP_BLOCKS = ("画面", "原文ブロック", "図版")

# 見出しで常体を期待するセクション（訳者の記述）
PLAIN_SECTIONS = re.compile(r"^\s*(付録|出典|本書の読み方|目次)")

# 敬体の文末。常体より先に判定する。
# 「作られました」は「た。」で終わるが敬体である — 順序を誤ると誤検出する。
KEITAI = re.compile(
    r"(です|でした|でしょう|でして|"
    r"ます|ました|ましょう|ましたら|まして|"
    r"ません|ませんでした)"
    r"(か|ね|よ)?$"                 # 終助詞は落として判定する
)

# 命令形。敬体でも常体でもない。
# 「削除するために作りなさい」のような節を締める箴言は、1文でも規約の例外にあたる。
# これを常体の孤立として叱ると、原文の調子を保った訳を機械が壊すことになる。
MEIREI = re.compile(r"(なさい|ください|せよ|たまえ|こと)$")

# 常体の文末。敬体でないことを確認したうえで当てる。
#
# い形容詞の直前に来る文字は限定できない。仮名なら「正しい」の“し”、
# 「高い」の“か”、漢字なら「無い」の“無”。以前ここを恣意的な文字クラスで
# 書いていたため、「正しい」「無い」が常体と判定されず「不明」に落ちていた。
# 不明は collect() で捨てられるので常体の連続が途切れ、
# 残った1文が「孤立した常体」として誤報告されていた。
#
# 代償として「〜の扱い」「〜の違い」のような体言止めも常体と見なすが、
# 原文訳・補足の地の文で体言止めが文末に来ることは稀なので、この向きの
# 誤りのほうが害が小さい。
JOTAI = re.compile(
    r"("
    r"である|であった|だ|だった|"
    r"ない|なかった|"
    r"[うくすつぬふむゆるぐずづぶぷ]|"      # 動詞の終止形
    r"[ぁ-ん一-龥]い|"                      # い形容詞（正しい・無い・高い…）
    r"た|"                                   # 過去
    r"れる|られる|せる|させる"
    r")$"
)

SENT_END = "。．!！?？"


def strip_markup(s: str) -> str:
    """強調・リンク・コード記法を落として文末判定を安定させる。"""
    s = re.sub(r"`[^`]*`", "", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = s.replace("**", "").replace("*", "")
    return s.strip()


def sentences(text: str) -> list[str]:
    """句点で文に割る。末尾に句点がない断片は対象にしない。"""
    out = []
    for raw in re.split(r"(?<=[。．!！?？])", text):
        s = raw.strip()
        if s and s[-1] in SENT_END:
            out.append(s)
    return out


# 丸ごと引用された文。文体は引用元のものであって訳者のものではない。
# 「ファイルを要約する、そして次に天気を確認する」のように、原文が
# 言及している句をそのまま示す場合、常体で終わるのが正しい。
QUOTED = re.compile(r"^[「『\"][^「『]*[」』\"]$")


def register(sent: str) -> str:
    """敬体 / 常体 / 命令 / 引用 / 不明 を返す。"""
    body = sent.rstrip(SENT_END).strip()
    if QUOTED.match(body):
        return "引用"
    body = re.sub(r"[」』）\)】〕]+$", "", body).strip()
    if not body:
        return "不明"
    if MEIREI.search(body):
        return "命令"
    if KEITAI.search(body):
        return "敬体"
    if JOTAI.search(body):
        return "常体"
    return "不明"


# 箇条書きの記号。記号のあとに空白が続くものだけを箇条書きとみなす。
# 以前は行頭の `*` だけで弾いていたため、`**強調**` で始まる地の文まで
# 箇条書きとして捨てていた。要点版では判定対象が7文しか残らず、
# ゲートは GREEN なのに実際にはほとんど検査していなかった（偽陰性）。
BULLET = re.compile(r"^([-*+]|\d+\.)\s")


def is_checkable(line: str) -> bool:
    """文体を見るべき行か。

    箇条書き・表・訳者の注記・見出しは体言止めや断片になりやすく、
    文体の判定対象にすると雑音しか出ない。
    """
    s = line.strip()
    if not s:
        return False
    if BULLET.match(s):
        return False
    if s.startswith(("|", "#", ">", "〔", "```")):
        return False
    return True


def parse_blocks(lines: list[str]) -> list[dict]:
    """`## ` セクションと `### ` ブロックに分けて、本文行を集める。

    コードフェンスの内側は一切見ない（原語のまま残す場所なので）。
    """
    blocks: list[dict] = []
    section = "（冒頭）"
    block = None
    fence = False
    for i, ln in enumerate(lines, 1):
        if ln.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        if ln.startswith("## ") and not ln.startswith("### "):
            section = ln[3:].strip()
            block = None
            continue
        m = re.match(r"^#{3,4}\s*(.+?)\s*$", ln)
        if m:
            name = m.group(1).strip()
            block = {"section": section, "name": name, "lines": []}
            blocks.append(block)
            continue
        if block is not None:
            block["lines"].append((i, ln))
        else:
            # 見出し直下でない地の文（付録など）はセクション名で扱う
            blocks.append({"section": section, "name": "（本文）",
                           "lines": [(i, ln)]})
    return blocks


def expected(block: dict, polite_blocks=POLITE_BLOCKS,
             plain_blocks=PLAIN_BLOCKS, japanese_source=False) -> str | None:
    """このブロックに期待される文体。None なら検査しない。

    japanese_source のとき原文訳ブロックは存在しない。引用記法の外は
    すべて訳者の記述なので常体を期待する。引用記法の中は元の著者の文で
    あり、is_checkable() が読み飛ばす（著者の文体を叱らない）。
    """
    if any(k in block["name"] for k in SKIP_BLOCKS):
        return None
    if japanese_source:
        return "常体"
    if any(k in block["name"] for k in polite_blocks):
        return "敬体"
    if any(k in block["name"] for k in plain_blocks):
        return "常体"
    if PLAIN_SECTIONS.match(block["section"]):
        return "常体"
    return None


def collect(block: dict, exp: str) -> list[tuple[int, str, str]]:
    """(行番号, 文, 文体) の列。原文訳は引用記法の中身を見る。"""
    out = []
    polite = exp == "敬体"
    for n, ln in block["lines"]:
        s = ln.strip()
        if polite:
            if not s.startswith(">"):
                continue
            s = s[1:].strip()
            if not s or BULLET.match(s) or s.startswith(("|", "〔")):
                continue
        elif not is_checkable(ln):
            continue
        for sent in sentences(strip_markup(s)):
            r = register(sent)
            if r not in ("不明", "引用"):
                out.append((n, sent, r))
    return out


def runs(items: list[tuple[int, str, str]], target: str) -> list[list[tuple]]:
    """target の文体が連続している区間を返す。"""
    out, cur = [], []
    for it in items:
        if it[2] == target:
            cur.append(it)
        else:
            if cur:
                out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="文体の一貫性ゲート")
    p.add_argument("doc", type=Path)
    p.add_argument("--show-runs", action="store_true",
                   help="例外候補（常体の連続）も一覧する")
    p.add_argument("--max-report", type=int, default=12)
    # verify_doc.py と同じ名前・同じ意味。ブロック名を変えた文書に使う。
    p.add_argument("--translation-label", default=None,
                   help="敬体を期待するブロック名（既定: 原文訳）")
    p.add_argument("--note-label", default=None,
                   help="常体を期待するブロック名（既定: 補足）")
    p.add_argument("--japanese-source", action="store_true",
                   help="ソースが日本語で原文訳が無い文書（SKILL.md 0-2）。"
                        "引用記法の外をすべて訳者の記述として常体で検査する")
    args = p.parse_args()
    if args.japanese_source and args.translation_label:
        # 日本語ソースの引用は元の著者の文。敬体の揺れとして叱ると誤検出になる
        p.error("--japanese-source と --translation-label は併用できない"
                "（日本語ソースの引用ブロックは著者の文体のままなので検査しない）")
    polite_blocks = ((args.translation_label,) if args.translation_label
                     else POLITE_BLOCKS)
    plain_blocks = (args.note_label,) if args.note_label else PLAIN_BLOCKS

    lines = args.doc.read_text(encoding="utf-8").splitlines()
    blocks = parse_blocks(lines)

    print("=" * 62)
    print("  文体の一貫性ゲート")
    print("=" * 62)
    print(f"  対象: {args.doc.name}")

    findings: list[str] = []
    isolated: list[tuple[int, str]] = []   # 原文訳の孤立した常体
    exceptions: list[list[tuple]] = []     # 原文訳の常体の連続（例外候補）
    polite_in_plain: list[tuple[int, str]] = []

    n_polite = n_plain = 0
    for b in blocks:
        exp = expected(b, polite_blocks, plain_blocks, args.japanese_source)
        if exp is None:
            continue
        items = collect(b, exp)
        if not items:
            continue
        if exp == "敬体":
            n_polite += len(items)
            for r in runs(items, "常体"):
                (isolated.append((r[0][0], r[0][1])) if len(r) == 1
                 else exceptions.append(r))
        else:
            n_plain += len(items)
            polite_in_plain += [(n, s) for n, s, r in items if r == "敬体"]

    print(f"  判定した文: 原文訳 {n_polite} / 補足・付録 {n_plain}")

    # --- 補足に敬体（例外なし） -------------------------------------------
    ok = not polite_in_plain
    print(f"\n  [{'GREEN' if ok else 'RED  '}] 補足・付録の文体（常体）")
    for n, s in polite_in_plain[:args.max_report]:
        print(f"          {n:5d}行  {s[:58]}")
    if len(polite_in_plain) > args.max_report:
        print(f"          ... 他 {len(polite_in_plain)-args.max_report}件")
    if polite_in_plain:
        findings.append(f"補足・付録に敬体が {len(polite_in_plain)}文"
                        "（訳者の記述は常体に揃える）")

    # --- 原文訳の孤立した常体 ---------------------------------------------
    ok = not isolated
    print(f"  [{'GREEN' if ok else 'RED  '}] 原文訳の文体（孤立した切り替わり）")
    for n, s in isolated[:args.max_report]:
        print(f"          {n:5d}行  {s[:58]}")
    if len(isolated) > args.max_report:
        print(f"          ... 他 {len(isolated)-args.max_report}件")
    if isolated:
        findings.append(f"原文訳で常体が1文だけ孤立 {len(isolated)}件"
                        "（前後が敬体 — 推敲の漏れ）")
        # 実測でいちばん多い原因を示す。3回の実行で連続して出た。
        print("          ヒント: 原文の1文を訳で2文に割った箇所を疑う。"
              "語順を変えて1文に戻すと直ることが多い")

    # --- 例外候補（叱らない） ----------------------------------------------
    if exceptions:
        total = sum(len(r) for r in exceptions)
        print(f"\n  [INFO ] 原文訳の常体の連続 {len(exceptions)}区間 / 計{total}文")
        print("          原文が畳みかける箇所なら規約の例外にあたる。")
        print("          意図的でないなら敬体に直すこと。")
        if args.show_runs:
            for r in exceptions[:args.max_report]:
                print(f"\n          {r[0][0]}行から {len(r)}文:")
                for n, s, _ in r[:4]:
                    print(f"            {s[:56]}")
                if len(r) > 4:
                    print(f"            ... 他 {len(r)-4}文")
        else:
            for r in exceptions[:args.max_report]:
                print(f"          {r[0][0]:5d}行から {len(r)}文  {r[0][1][:44]}")
        print("\n          （--show-runs で中身を表示）" if not args.show_runs else "")

    print("\n" + "-" * 62)
    if findings:
        print("  判定: RED")
        for f in findings:
            print(f"    · {f}")
        print("-" * 62)
        return 1
    print("  判定: GREEN")
    print("  注: 常体の連続は例外として許容している（INFO に出る）")
    print("-" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
