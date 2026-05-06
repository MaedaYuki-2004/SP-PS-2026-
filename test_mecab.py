# test_mecab.py を以下に書き換え
import MeCab
import unidic

dicdir = unidic.DICDIR
tagger = MeCab.Tagger('-d "' + dicdir + '"')

words = [
    "おんど", "かいけい", "がっこう", "ぎんこう", "こうえん",
    "こうつう", "こうばい", "しごと", "しつど", "じどうしゃ",
    "しゅうしょく", "しゅみ", "しょうめいしょ", "しょくざい", "すけじゅーる",
    "すーぱー", "せいきゅうしょ", "ぜいきん", "ちゅうしょく", "ちょうしょく",
    "ちょうみりょう", "ちょきん", "でんしゃ", "でんわ", "どうろ",
    "びょういん", "びよういん", "ほどう", "ゆうしょく", "やちん"
]

print(f"{'単語':<12} {'品詞':<20} {'アクセント'}")
print("-" * 50)
for word in words:
    result = tagger.parse(word)
    lines = [l for l in result.splitlines() if l and l != "EOS"]
    if len(lines) == 1:
        parts = lines[0].split("\t")
        features = parts[1].split(",") if len(parts) > 1 else []
        accent = features[24] if len(features) > 24 else "不明"
        print(f"  [OK] {word:<12} {accent}")
    else:
        print(f"  [分割] {word:<12} → {len(lines)}分割")