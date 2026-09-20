#!/usr/bin/env python3
"""用語辞書の置換の単体テスト。

    python tests/test_glossary.py

枠組みは使わない。依存を増やさずに、素の python で走ることを優先する。

**このテストがある理由。** 辞書の項目は2語以上のものが半数近くを占める。
文字起こしの行は「[12:34] 本文」の形なので、語と語の間に改行と時刻の見出しが
挟まって切れる。これを2回続けて取りこぼした（ADK の land/graph、
Transformer の SSI/amps）ため、回帰として残す。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from glossary import build_pattern, substitute  # noqa: E402

fails = 0


def check(name: str, got, want) -> None:
    global fails
    if got == want:
        print(f"  ok    {name}")
    else:
        fails += 1
        print(f"  FAIL  {name}")
        print(f"          got  {got!r}")
        print(f"          want {want!r}")


def apply_one(wrong: str, right: str, text: str, **kw):
    """辞書1項目ぶんの置換を行い、(結果, 行またぎ件数) を返す。"""
    entry = {"wrong": wrong, "right": right}
    entry.update(kw)
    return substitute(build_pattern(entry), right, text)


print("行内の置換（回帰）")
check("1語",
      apply_one("comnet", "ConvNet", "[00:05] you just have a massive comnet here")[0],
      "[00:05] you just have a massive ConvNet here")
check("2語",
      apply_one("land chain", "LangChain", "[25:32] using land chain, CrewAI,")[0],
      "[25:32] using LangChain, CrewAI,")
check("大文字小文字を無視する（既定）",
      apply_one("adke", "ADK", "[04:48] a very simple ADKE agents")[0],
      "[04:48] a very simple ADK agents")
check("語の一部には当てない",
      apply_one("comnet", "ConvNet", "[00:05] the comnetwork was")[0],
      "[00:05] the comnetwork was")

print("\n行またぎ（この修正の対象）")
# 実測1: ADK の講演。land と graph が行末と行頭に割れていた
src1 = ("[20:59] ADK, as I just said, but if you build agent with land\n"
        "[21:04] graph, land chain, you can do that.")
got1, crossed1 = apply_one("land graph", "LangGraph", src1)
check("改行と時刻の見出しをまたいで当たる",
      "LangGraph" in got1, True)
check("時刻の見出しが残る",
      "[21:04]" in got1, True)
check("行数が変わらない",
      len(got1.split("\n")), 2)
check("置換語は次の行の先頭へ送られる",
      got1.split("\n")[1].startswith("[21:04] LangGraph,"), True)
check("行またぎとして数える",
      crossed1, 1)

# 実測2: Transformer の講義。SSI と amps が割れていた
src2 = ("[31:37] make sure you get your sparse sit histograms, your SSI\n"
        "[31:40] amps, your collars to grams, textiles, hand images")
got2, crossed2 = apply_one("SSI amps", "SSIMs", src2)
check("2件目の実測ケースにも当たる",
      got2.split("\n")[1].startswith("[31:40] SSIMs,"), True)
check("行またぎとして数える（2件目）",
      crossed2, 1)

print("\n行をまたがない場合は見出しを動かさない")
got3, crossed3 = apply_one("land chain", "LangChain",
                           "[25:32] using land chain today")
check("同じ行なら語の位置は変わらない",
      got3, "[25:32] using LangChain today")
check("行またぎに数えない",
      crossed3, 0)

print("\n時刻の書式")
for stamp in ("[01:02]", "[101:02]", "[1:02:03]"):
    src = f"[00:00] with land\n{stamp} graph, ok"
    got, _ = apply_one("land graph", "LangGraph", src)
    check(f"{stamp} を語間として認める", "LangGraph" in got, True)

print("\nregex 指定の項目は素通しする")
check("regex はそのまま使う",
      apply_one(r"foo\d+", "FOO", "[00:00] foo12 bar", regex=True)[0],
      "[00:00] FOO bar")

print()
if fails:
    print(f"{fails} 件が失敗した")
    sys.exit(1)
print("すべて通った")
