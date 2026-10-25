# Completion Report: Phase 4(Pi + ハーネスでエージェント)

| 項目 | 値 |
| --- | --- |
| 契約 | `docs/harness/contracts/phase-4.json`(version 1、`sha256:9f5a5054...`) |
| 対象コミット | `34ff262`(ブランチ `claude/phase3-4-rag-agent-4c2343`、Phase 3 の `b6a1dce` の上) |
| 判定 | **accepted**(`phase-4.decision.json`)。AGT-001〜005 pass、AGT-006 は owner のレビュー(2026-10-25)で pass |
| 作業者 | Claude Code セッション(writer)。検証も同セッションが実行(self-check) |
| 日付 | 2026-10-25 |

## Summary

Pi(pi-coding-agent 1.0.0)を RPC モードで子プロセスとして起動し、llama-server(Qwen3.6-35B-A3B、ctx 8192)に
OpenAI 互換 API で接続する読み取り専用エージェントを作った。ai_harness の原則は Pi の extension(`agent/harness/`、TypeScript)で
**コードとして**強制する: Contract の `allowed_tools` と `scope.read/forbidden` に合わない `tool_call` は block、`max_steps` などの予算超過で停止、
すべての呼び出しを `evidence/<run_id>/events.jsonl` に記録する。Python runner(`uv run llm-agent`)は Pi を起動して `agent_settled` まで待ち、
Contract の criterion ごとに決定論的な検査を行って `decision.json` / `verification.json` / `report.md` を書く。
エージェントの `finish` 出力は「未検証の自己申告」として report に載せるだけで、判定には使わない。

## Completion Criteria

| ID | 条件(要約) | 方法 | 結果 | 証拠 |
| --- | --- | --- | --- | --- |
| AGT-001 | 許可外ツール(bash)の呼び出しが block され events.jsonl に残る | unit-test, e2e | **pass** | vitest `policy.test.ts`(`tool_not_allowed`)、pytest `test_execute_blocked_bash_recorded`。実機 `evidence/e2e-bash-block/events.jsonl` seq 3(`bash {"command":"ls docs"}` → `tool_not_allowed`)、seq 4(組み込み `read` も block) |
| AGT-002 | scope.read 外・forbidden のパスの read_file が block される | unit-test, e2e | **pass** | vitest `policy.test.ts` / `tools.test.ts`(`src/x.py`、`.env`、`..`、絶対パス)、pytest `test_contract_scope.py`。実機 `evidence/e2e-scope/events.jsonl` seq 3(`.env` → `scope_boundary_exceeded`、`stop_reason=scope_violation`)、decision は `blocked` |
| AGT-003 | `max_steps=2` で停止し `stop_reason=budget_exceeded` が decision に残る | e2e | **pass** | 実機 `evidence/e2e-budget/`: events.jsonl seq 16 `stop budget_exceeded "max_steps=2 exceeded (step 3)"`、decision.json `status=blocked`、`unresolved_errors[0].code=budget_exceeded`。pytest `test_execute_budget_exceeded_is_blocked`(偽 pi) |
| AGT-004 | サンプル Contract の実行で decision が生成され、必須 criterion が全部 pass のときだけ accepted | integration-test, e2e | **pass** | 実機 `evidence/e2e-summarize/decision.json` = `accepted`(7 必須 criterion すべて pass、SUMMARY_QUALITY は not_run だが非必須)。pytest `test_verify_report.py::test_decide_status_*`(fail → rejected、required not_run → needs_human、異常停止 → blocked) |
| AGT-005 | `uv run pytest` と `agent/harness` の `npm test` が通る | unit-test | **pass** | `evidence/phase-4.verify.txt`: pytest 87 passed、vitest 35 passed(4 files) |
| AGT-006 | `docs/journal/phase-4.md` がテンプレート全節を満たし、Pi の設定手順が再現可能 | diff-review, human-review | **pass**(owner レビュー 2026-10-25) | `docs/journal/phase-4.md`(全 9 節、models.json と `PI_CODING_AGENT_DIR` の手順を「動かし方」に記載) |

AGT-006 は当初 `not_run`(人のレビュー待ち)で decision は `needs_human` だった。owner が `docs/journal/phase-4.md` をレビューして pass とし、decision を `accepted` に更新した(`phase-4.AGT-006.verification.json` の `verified_at` 参照)。

## Commands

ベースライン(Phase 4 実装前、`b6a1dce`): `.\scripts\agent\verify.ps1` → contracts schema OK / diff --check OK / pytest 70 passed / ALL OK。

実装後(`34ff262`、`evidence/phase-4.verify.txt`):

```
.\scripts\agent\verify.ps1 -Agent
--> OK: contracts schema   (phase-3/4/5/6 + agent/contracts/summarize-docs.json + phase-3 の decision / verification)
--> OK: git diff --check
--> OK: pytest             87 passed in 4.83s
--> OK: vitest (agent/harness)   Test Files 4 passed, Tests 35 passed
commit: b6a1dce   (verify 実行時点。実装のコミットはその直後の 34ff262)
ALL OK
```

実機(llama-server ルーター :8080 = 自分で起動、`llm-web --port 8010` = `rag_search` の先、モデル Qwen3.6-35B-A3B Q4_K_M、thinking off):

```
uv run llm-agent --contract agent/contracts/summarize-docs.json --task "RAG の設計方針を 3 点にまとめて" --run-id e2e-summarize --rag-url http://127.0.0.1:8010
run_id: e2e-summarize  (66.3s, turns=8, settled=True, exit=0)
decision: accepted
  PASS FINISH_CALLED / SUMMARY_NONEMPTY (541 文字) / SOURCES_IN_SCOPE (docs/spec-phase3-6.md) / ONLY_ALLOWED_TOOLS / NO_SCOPE_VIOLATION
  PASS WITHIN_BUDGET (steps=8, tool_calls=9, blocks=1) / RUN_SETTLED
  ---- SUMMARY_QUALITY  (human-review、非必須)
```

| run_id | 目的 | 指示・設定 | 結果 | 時間 |
| --- | --- | --- | --- | --- |
| `e2e-summarize` | AGT-004 | 既定設定 | **accepted**。rag_search 2 回 + list_files 1 回 + read_file 1 回で `finish`(sources: `docs/spec-phase3-6.md`)。出力予算(6000 字)に達した後の 3 回はエラー文で「finish せよ」と返し、モデルは finish した | 66.3 s / 8 turns |
| `e2e-bash-block` | AGT-001 | `--expose-builtin-tools`、「まず bash で ls docs を実行」と指示 | **accepted**。`bash` と組み込み `read` が `tool_not_allowed` で block(実行されていない)、その後 read_file → finish。block は記録されるが許可外ツールは走っていないので採用可 | 51.2 s / 5 turns |
| `e2e-budget` | AGT-003 | `--max-steps 2` | **blocked**。3 turn 目の `turn_start` で `stop budget_exceeded` → `ctx.abort()`。Pi は abort された turn を `stopReason=error / "This operation was aborted"` で返すが、runner は自分が止めたものとして `aborted_turns=1` に数え、`RUN_SETTLED` は pass | 13.3 s / 3 turns |
| `e2e-scope` | AGT-002 | 「`.env` と `src/local_llm/web.py` を read_file で読め」と指示 | **blocked**。`.env` は `scope.forbidden` で `scope_boundary_exceeded`、以後の呼び出しは `run_stopped`、`stop_reason=scope_violation`。決定論的に 1 turn で終わる | 2.8 s / 1 turn |

## Files Changed

| 区分 | ファイル |
| --- | --- |
| 追加 | `agent/harness/{index.ts,package.json,package-lock.json,vitest.config.ts}`、`agent/harness/src/{glob,policy,evidence,tools}.ts`、`agent/harness/test/*.test.ts`(4)、`agent/pi/{models.json,settings.json}`、`agent/contracts/summarize-docs.json`、`src/local_llm/agent/{__init__,contract,scope,runner,verify,report}.py`、`tests/agent/{__init__,fake_pi,test_contract_scope,test_runner,test_verify_report}.py`、`evidence/e2e-{summarize,bash-block,budget,scope}/*`、`docs/harness/reports/evidence/phase-4.verify.txt`、`docs/journal/phase-4.md`、`docs/harness/reports/phase-4.*` |
| 変更 | `pyproject.toml`(`llm-agent` エントリポイント)、`.gitignore`(Pi の実行時ファイル)、`scripts/agent/check_contracts.py`(`agent/contracts/*.json` も検証)、`README.md`、`docs/external-spec.md`(11 章)、`docs/internal-spec.md`(12 章)、`docs/journal/README.md` |
| 変更ファイル数 | 実装コミット 52 + 文書コミット 約 15(予算 `max_files_changed` 80 以内) |

すべて `scope.write` の範囲内(`src/local_llm/**, tests/**, agent/**, scripts/agent/**, pyproject.toml, README.md, docs/**, .gitignore, evidence/**`)。
`scope.forbidden`(`.env`、`llama-local/**`、`*.gguf`、`data/chat.db`、`.git/**`)には触れていない。`models.ini` と `~/.pi` も変更していない。
接続先は 127.0.0.1(llama-server、llm-web)と registry.npmjs.org(`npm install` で vitest / typebox)のみ。

## Not Run

- 同一エラー反復(`same_error_limit=2`)の実機 e2e。vitest(`policy.test.ts`)でのみ確認。実機の `e2e-summarize` では「出力予算切れ」エラーが 3 回続いたが、
  エラーの同一性判定に引数(query / path)を含めているため別エラーと数えられ、停止しなかった(意図した挙動だが、引数を無視する設計も考えられる。残存リスク参照)。
- `max_wall_time_seconds`(契約 900 秒)の超過時の挙動。runner の `--timeout` で pi を kill する経路は pytest で `agent_settled` 待ちのタイムアウト文言のみ確認。
- 別 PC で `npm install` → `npm test` → `uv run llm-agent` を再現する確認(Pi のグローバルインストール手順は journal に記載)。
- `SUMMARY_QUALITY`(要約の内容が正しいか)。runner は検査せず、report.md に本文を載せて人が読む。

## Residual Risk

| リスク | 深刻度 | 対処 |
| --- | --- | --- |
| `agent/pi/models.json` の `contextWindow` を実サーバの 8192 ではなく 16384 と申告している(Pi が `contextWindow − 推定使用量 − 4096` で max_tokens を切り詰め、8192 だと 4k 時点で 1 トークンになるため)。Pi の自動 compaction は実際のオーバーフロー前に動かない | medium | ハーネス側のツール出力予算(既定 6000 字、`HARNESS_MAX_OUTPUT_CHARS`)で実コンテキストを 8k 以内に収める。実機 4 run で最大 4.2k トークン。`models.ini` の ctx-size を上げるのは Phase 4 の scope 外(Amendment 候補 1) |
| 実機 e2e の結果はモデルの挙動に依存し再現性が無い(`e2e-summarize` が毎回 accepted になる保証は無い) | medium | 決定論的な部分(block、予算、scope、decision の規則)は pytest / vitest で固定。実機は「少なくとも 1 回成功した証拠」 |
| 同一エラーの判定に引数を含めるため、別の query で同じ失敗を繰り返しても `same_error_repeated` にならない | low | 出力予算切れはツールが「finish せよ」と返すことで収束した。必要なら `recordResult` の hash から引数を外す(1 行) |
| `decision.json` の `subject.artifact_hash` は events.jsonl の作業コピー(LF)から計算。Windows の autocrlf でチェックアウト時に CRLF になると照合がずれる | low | 照合するときは `git show HEAD:evidence/.../events.jsonl` の内容で計算するか、`.gitattributes` で `*.jsonl text eol=lf` を指定する(Phase 6 候補) |
| GATE_NEW_DEPENDENCY: `agent/harness/package.json` の devDependencies に `vitest 5.0.5`(仕様 3.5 が要求)と `typebox 1.3.27`(ツールのスキーマ定義。Pi 本体が同じライブラリを使っており、テストから import するため) | low | owner の確認を求める。本番(Pi 実行時)は Pi 同梱の typebox を使うので、実行時依存は増えていない |
| `thinking off` を `before_provider_request` で `chat_template_kwargs.enable_thinking=false` にして実現。Pi の内部 API に依存 | low | Pi 更新時に journal の手順で再確認 |

## Amendment 候補(契約の変更は行っていない)

1. `models.ini` の `[unsloth/Qwen3.6-35B-A3B-GGUF:Q4_K_M]` の `ctx-size` を 8192 から 16384 以上に上げる(VRAM は `bench/` の ctx-vram 計測を参照)。
   エージェントの手数と読める文書量を直接制限している。`models.ini` は Phase 4 の `scope.write` に無いので変更していない。
2. (Phase 3 から継続)`[unsloth/Qwen3-8B-GGUF:Q4_K_M]` に `hf-repo` を足す。
3. `scope.destinations` に `registry.npmjs.org` は「agent/harness の依存取得のみ」で記載済み。Pi 本体のインストール(`npm install -g @earendil-works/pi-coding-agent`)は
   このセッションより前に行われており Phase 4 の作業には含まない。別 PC で再現するときは同じ宛先が必要になる旨を journal に書いた。

## Rollback

```powershell
git revert <文書コミット> 34ff262      # Phase 4 を戻す(Phase 3 は残る)
Remove-Item agent\harness\node_modules -Recurse   # npm の依存(git 管理外)
Remove-Item agent\pi\auth.json, agent\pi\models-store.json -ErrorAction SilentlyContinue   # Pi が作る実行時ファイル(git 管理外)
```

`~/.pi` 配下は一切触っていないので戻す必要はない。

## 残っているプロセス(人の手で停止が必要)

実行環境のポリシーで Stop-Process / taskkill が拒否されたため、セッション終了時点で次が残る:

| ポート | プロセス | 備考 |
| --- | --- | --- |
| :8080 | llama-server ルーター(`scripts/start-server.ps1` 経由、ラッパー PID 12096) | このセッションが起動。Qwen3.6-35B-A3B と埋め込みモデルを読み込み済み |
| :8010 | `uv run llm-web --port 8010`(`LLAMA_BASE_URL=http://127.0.0.1:8080`) | e2e の `rag_search` の先 |
| :8000 | 古い `llm-web`(PID 11164) | Phase 3 の検証時に起動。埋め込み先が消滅した :8082 を向いており、`rag_search` には使えない |

Phase 3 の Report に書いた :8081 / :8082 のルーターはセッション途中で消えていた(別の操作で停止されたと推測。未確認)。
