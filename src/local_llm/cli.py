"""CLI チャット(エントリポイント `llm-chat`)。

llama-server に対してストリーミングで会話する。thinking(推論過程)は薄い色で表示し、
応答後にトークン数と生成速度を 1 行で出す。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from rich.console import Console
from rich.text import Text

from local_llm.client import LlamaClient, LlamaClientError
from local_llm.config import load_settings
from local_llm.history import ChatLogger
from local_llm.types import Message, Usage

HELP_TEXT = """\
コマンド:
  /model            モデル一覧を表示
  /model <id>       モデルを切り替える
  /clear            会話履歴を消す
  /system <text>    system prompt を差し替える
  /think on|off     thinking の有無を切り替える
  /help             このヘルプ
  /quit             終了(Ctrl+D / Ctrl+Z でも終了)
"""


class ChatSession:
    """CLI の会話状態(履歴・モデル・設定)を持つ。"""

    def __init__(
        self,
        client: LlamaClient,
        console: Console,
        logger: ChatLogger,
        *,
        model: str,
        system_prompt: str | None = None,
        enable_thinking: bool = True,
    ) -> None:
        self.client = client
        self.console = console
        self.logger = logger
        self.model = model
        self.system_prompt = system_prompt
        self.enable_thinking = enable_thinking
        self.history: list[Message] = []

    # ------------------------------------------------------------ メッセージ組み立て

    def build_messages(self) -> list[Message]:
        """送信用のメッセージ列(system prompt + 履歴)を作る。"""
        messages: list[Message] = []
        if self.system_prompt:
            messages.append(Message("system", self.system_prompt))
        messages.extend(self.history)
        return messages

    # ------------------------------------------------------------ スラッシュコマンド

    async def handle_command(self, line: str) -> bool:
        """スラッシュコマンドを処理する。終了するなら False を返す。"""
        cmd, _, arg = line.strip().partition(" ")
        arg = arg.strip()

        if cmd in ("/quit", "/exit"):
            return False
        if cmd == "/help":
            self.console.print(HELP_TEXT, highlight=False)
        elif cmd == "/clear":
            self.history.clear()
            self.console.print("[yellow]履歴を消しました[/]")
        elif cmd == "/system":
            self.system_prompt = arg or None
            msg = f"system prompt を設定しました: {arg}" if arg else "system prompt を外しました"
            self.console.print(f"[yellow]{msg}[/]", highlight=False)
        elif cmd == "/think":
            if arg.lower() in ("on", "off"):
                self.enable_thinking = arg.lower() == "on"
                self.console.print(f"[yellow]thinking: {'on' if self.enable_thinking else 'off'}[/]")
            else:
                self.console.print("[red]使い方: /think on|off[/]")
        elif cmd == "/model":
            await self._command_model(arg)
        else:
            self.console.print(f"[red]不明なコマンドです: {cmd}[/]  (/help で一覧)")
        return True

    async def _command_model(self, arg: str) -> None:
        if arg:
            self.model = arg
            self.console.print(f"[yellow]モデルを切り替えました: {arg}[/]", highlight=False)
            return
        try:
            models = await self.client.list_models()
        except LlamaClientError as exc:
            self.console.print(f"[red]{exc}[/]")
            return
        self.console.print("[bold]利用できるモデル:[/]")
        for m in models:
            mark = "*" if m == self.model else " "
            self.console.print(f"  {mark} {m}", highlight=False)

    # ------------------------------------------------------------ 1 往復

    async def ask(self, user_text: str) -> None:
        """ユーザー発言を送り、応答をストリーミング表示して履歴に積む。"""
        self.history.append(Message("user", user_text))
        self.logger.append("user", user_text, self.model)

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        usage: Usage | None = None
        printed_reasoning_header = False
        printed_content_header = False
        started = time.perf_counter()
        first_token_at: float | None = None

        try:
            async for chunk in self.client.chat(
                self.build_messages(), self.model, enable_thinking=self.enable_thinking
            ):
                if chunk.reasoning:
                    if first_token_at is None:
                        first_token_at = time.perf_counter()
                    if not printed_reasoning_header:
                        self.console.print(Text("[thinking]", style="dim italic"))
                        printed_reasoning_header = True
                    self.console.print(Text(chunk.reasoning, style="dim"), end="")
                    reasoning_parts.append(chunk.reasoning)
                if chunk.content:
                    if first_token_at is None:
                        first_token_at = time.perf_counter()
                    if not printed_content_header:
                        if printed_reasoning_header:
                            self.console.print()  # thinking と本文の区切り
                        self.console.print(Text("assistant>", style="bold green"), end=" ")
                        printed_content_header = True
                    self.console.print(Text(chunk.content), end="")
                    content_parts.append(chunk.content)
                if chunk.usage:
                    usage = chunk.usage
        except LlamaClientError as exc:
            self.console.print()
            self.console.print(f"[red]エラー: {exc}[/]", highlight=False)
            # 失敗した発言は履歴から外す(次回送信で二重にならないように)
            self.history.pop()
            return
        except (KeyboardInterrupt, asyncio.CancelledError):
            self.console.print("\n[yellow](中断しました)[/]")

        elapsed = time.perf_counter() - started
        content = "".join(content_parts)
        reasoning = "".join(reasoning_parts)
        self.console.print()
        if content or reasoning:
            self.history.append(Message("assistant", content))
            self.logger.append(
                "assistant", content, self.model, reasoning=reasoning or None
            )
        self._print_stats(usage, elapsed, first_token_at - started if first_token_at else None)

    def _print_stats(self, usage: Usage | None, elapsed: float, ttft: float | None) -> None:
        """トークン数と生成速度を 1 行で表示する。"""
        if usage is None:
            self.console.print(Text(f"({elapsed:.1f}s, usage なし)", style="dim"))
            return
        # 生成速度は「初トークンまでの待ち時間」を除いた時間で割る(プロンプト処理を含めない)
        gen_time = elapsed - (ttft or 0.0)
        tps = usage.completion_tokens / gen_time if gen_time > 0 else 0.0
        self.console.print(
            Text(
                f"(prompt {usage.prompt_tokens} tok / completion {usage.completion_tokens} tok, "
                f"{tps:.1f} tok/s, {elapsed:.1f}s)",
                style="dim",
            )
        )

    # ------------------------------------------------------------ メインループ

    async def run(self) -> None:
        self.console.print(
            f"[bold]llm-chat[/]  model=[cyan]{self.model}[/]  thinking={'on' if self.enable_thinking else 'off'}"
            "  (/help でコマンド一覧)",
            highlight=False,
        )
        if not await self.client.health():
            self.console.print(
                f"[red]警告: llama-server({self.client.base_url})に接続できません。"
                "`.\\scripts\\start-server.ps1` で起動してください。[/]",
                highlight=False,
            )

        while True:
            try:
                line = self.console.input("[bold cyan]you>[/] ")
            except (EOFError, KeyboardInterrupt):
                self.console.print()
                break
            line = line.strip()
            if not line:
                continue
            if line.startswith("/"):
                if not await self.handle_command(line):
                    break
                continue
            await self.ask(line)

        self.console.print(f"[dim]ログ: {self.logger.path}[/]", highlight=False)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    settings = load_settings()
    parser = argparse.ArgumentParser(prog="llm-chat", description="llama-server と CLI でチャットする")
    parser.add_argument("--model", "-m", default=settings.default_model, help="使うモデル id")
    parser.add_argument("--system", "-s", default=None, help="system prompt")
    parser.add_argument("--no-think", action="store_true", help="thinking を無効にする")
    parser.add_argument("--base-url", default=settings.base_url, help="llama-server の URL")
    parser.add_argument("--timeout", type=float, default=settings.timeout, help="HTTP タイムアウト(秒)")
    parser.add_argument("--log-dir", default="logs", help="JSONL ログの保存先")
    return parser.parse_args(argv)


async def _amain(args: argparse.Namespace) -> None:
    console = Console()
    async with LlamaClient(args.base_url, timeout=args.timeout) as client:
        session = ChatSession(
            client,
            console,
            ChatLogger(args.log_dir),
            model=args.model,
            system_prompt=args.system,
            enable_thinking=not args.no_think,
        )
        await session.run()


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        asyncio.run(_amain(args))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
