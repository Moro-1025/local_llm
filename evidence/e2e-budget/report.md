# Agent run report: e2e-budget

| 項目 | 値 |
| --- | --- |
| task | RAG の設計方針を 3 点にまとめて |
| contract | `contract:local-llm:agent:summarize-docs` v1 (`sha256:6519657f7e58...`) |
| 判定 | **blocked** |
| stop_reason | budget_exceeded |
| steps / tool_calls / blocks | 3 / 5 / 0 |
| pi | 13.3s, turns=3, settled=True, exit=0, aborted_turns=1 |
| 日時 | 2026-10-25T03:36:21+00:00 |

## Completion Criteria

| ID | 必須 | 結果 | 備考 |
| --- | --- | --- | --- |
| FINISH_CALLED | yes | **fail** | finish が呼ばれていない(停止理由: budget_exceeded) |
| SUMMARY_NONEMPTY | yes | **fail** | result.summary が空、または result.json が無い |
| SOURCES_IN_SCOPE | yes | **fail** | result.sources が空 |
| ONLY_ALLOWED_TOOLS | yes | **pass** | 実行されたツールはすべて許可内(許可外の呼び出し 0 件は block) |
| NO_SCOPE_VIOLATION | yes | **pass** | scope 外アクセスなし(block 0 件) |
| WITHIN_BUDGET | yes | **fail** | stop_reason=budget_exceeded(steps=3, tool_calls=5, blocks=0) |
| RUN_SETTLED | yes | **pass** | agent_settled 受信、13.3s、exit=0 |
| SUMMARY_QUALITY | no | **not_run** | この criterion_id に対応する決定論的な検査が runner に無い |

## Tool calls (events.jsonl)

| seq | type | tool | 備考 |
| --- | --- | --- | --- |
| 3 | tool_call | rag_search | {"query": "RAG 設計方針", "top_k": 3} |
| 4 | tool_call | list_files | {"glob": "docs/**/*.md"} |
| 5 | tool_result | list_files | docs/README.md docs/external-spec.md docs/harness/README.md docs/harness/reports |
| 6 | tool_result | rag_search | [1] ai_harness/docs/profiles/production-high-risk.md › Profile: 本番・高リスクAIエージェント  |
| 8 | tool_call | rag_search | {"query": "RAG architecture design approach principles", "top_k": 3} |
| 9 | tool_call | read_file | {"path": "docs/spec-phase3-6.md"} |
| 10 | tool_call | read_file | {"path": "docs/internal-spec.md"} |
| 11 | tool_result | read_file | # Phase 3〜6 仕様  README のロードマップの後半、Phase 3(RAG)、Phase 4(ツール呼び出しとエージェント)、 Phase |
| 12 | tool_result | read_file | # 内部仕様書(Phase 1〜3)  実装の構造、モジュールの責務、データの流れを定義する。利用者から見た振る舞いは [external-spec.md](e |
| 14 | tool_result | rag_search | error: tool output budget exhausted (6048/6000 chars). Call finish now with what you al |
| 16 | stop |  | stop_reason=budget_exceeded |

## Reasons

- task: RAG の設計方針を 3 点にまとめて
- stop_reason: budget_exceeded
- block: tool_not_allowed 0 件, scope_boundary_exceeded 0 件
- 判定は runner の検証結果のみ(エージェントの finish 本文は採用の根拠にしない)
