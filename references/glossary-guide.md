# 用語辞書の運用（SKILL.md 手順4の詳細）

### 何を auto にして、何を review にするか

**一般語と同じ綴りになる項目は `--mode review` で入れる。**
auto で入れると、その語が本来の意味で使われている文書を壊す。

```bash
python $SK/scripts/glossary.py add --wrong "resonant" --right "ResNet" \
       --note "ML文脈のみ。共振の意味なら置換しない" --mode review
```

| 誤認識 | 正しい表記 | 判断 |
|---|---|---|
| `comnet` | `ConvNet` | **auto**。この綴りの一般語は無い |
| `alfalfold` | `AlphaFold` | **auto**。同上 |
| `resonant` | `ResNet` | **review**。共振の意味で使われうる |
| `testers` | `tensors` | **review**。試験者の意味で使われうる |
| `Cloud` | `Claude` | **review**。Google Cloud と cloud computing がある |

**迷ったら review にする。** review は `apply` のときに「要確認」として件数つきで
報告されるので、見落とすことはない。**auto の誤爆は静かに起きる**が、
review の取りこぼしは画面に出る。**害の大きさが違う。**

### 辞書が拾えないもの

**辞書は語の対応しか持てない。** 次の2つは辞書の仕事ではないので、
手で直して `<名前>.exempt.txt` に記録する。

- **一度きりの崩れ方**（`name a GPT` → `nanoGPT` のように、その素材でしか出ないもの）
- **画面を見なければ確定できないもの**（モデルID、講義番号、論文の題名）
