# 評価データセット

Phase 5 以降の評価に使う、版番号付きの JSONL。**学習には使わない**(評価汚染の防止、契約 EVAL_SPLIT)。

| ファイル | 由来 | 件数 | 形式 |
| --- | --- | --- | --- |
| `qa-v1.jsonl` | `training/data/split.py` が `training/data/qa.jsonl` を seed 42 で 9:1 に分けた eval 側のコピー | `wc -l` で確認 | 1 行 1 JSON、`{"question", "answer", "source"}` |

- `source` は `相対パス#見出し` で、どのチャンクから合成した質問かを示す。
- 合成の条件(プロンプト、モデル id、日時)は `training/data/qa.meta.json`。
- 版を上げるときはファイルを上書きせず `qa-v2.jsonl` を作り、この表に行を足す(過去の結果と比較できるようにする)。
- Phase 6 の採点器はこのファイルを読む。Phase 5 の暫定採点は `training/eval_quick.py`。
