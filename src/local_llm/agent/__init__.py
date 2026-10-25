"""Phase 4: Pi(pi-coding-agent)をハーネス付きで動かす runner。

- contract.py  Task Contract の読み込み・hash・Schema 検証
- scope.py     契約の scope グロブ(TypeScript 側 agent/harness/src/glob.ts と同じ規則)
- runner.py    pi --mode rpc を起動し、prompt を送って agent_settled まで待つ
- verify.py    criterion ごとの決定論的な検証(エージェントの自己申告は使わない)
- report.py    verification-result / completion-decision JSON と report.md の生成
"""
