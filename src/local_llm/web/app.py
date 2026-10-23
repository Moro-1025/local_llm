"""FastAPI アプリ(エントリポイント `llm-web`)。

`create_app()` で設定・DB・LlamaClient を差し替えられるようにしてある(テスト用)。
"""

from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from local_llm.client import LlamaClient
from local_llm.config import Settings, load_settings
from local_llm.web.db import Database
from local_llm.web.routes_chat import router

STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    settings: Settings | None = None,
    *,
    db: Database | None = None,
    client: LlamaClient | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    db = db or Database(settings.db_path)
    own_client = client is None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.db = db
        app.state.client = client or LlamaClient(settings.base_url, timeout=settings.timeout)
        db.init_schema()
        try:
            yield
        finally:
            if own_client:
                await app.state.client.aclose()

    app = FastAPI(title="local_llm chat", lifespan=lifespan)
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


# uvicorn から "local_llm.web.app:app" で参照する
app = create_app()


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    parser = argparse.ArgumentParser(prog="llm-web", description="Web チャット UI を起動する")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="コード変更で自動再起動(開発用)")
    args = parser.parse_args(argv)

    uvicorn.run("local_llm.web.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
