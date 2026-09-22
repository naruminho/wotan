"""Command line interface: ``wotan`` (or ``python -m wotan``).

  wotan [workspace]        serve the IDE on localhost (opens the browser)
  wotan --app              native window via pywebview (WebView2 on Windows)
  wotan doctor|probe       tune the LLM gateway configuration
  wotan mock               run the mock gateway + identity API (for tests/demos)
  wotan run "prompt"       one-shot agent run in a workspace (used by scheduler)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import webbrowser
from pathlib import Path

from . import __version__
from .paths import ensure_layout, utf8_console


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .config import load_config
    from .server import create_app

    workspace = Path(args.workspace or os.environ.get("WOTAN_WORKSPACE") or Path.cwd()).resolve()
    ensure_layout()
    cfg = load_config(workspace=workspace)
    host = args.host or cfg.server.host
    port = args.port or cfg.server.port
    app = create_app(workspace=workspace, config=cfg)
    url = f"http://{host}:{port}/"
    if args.app:
        return _run_app_window(app, host, port, url, workspace)
    if cfg.server.open_browser and not args.no_browser:
        webbrowser.open(url)
    print(f"Wotan serving {workspace} on {url} (Ctrl+C to stop)")
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


def _run_app_window(app: object, host: str, port: int, url: str, workspace: Path) -> int:
    """Optional native window (pywebview / WebView2). The UI does not depend on it."""
    try:
        import threading

        import uvicorn
        import webview  # type: ignore
    except ImportError:
        print("pywebview is not installed - falling back to the browser. (pip install 'wotan[app]')")
        return _cmd_serve(argparse.Namespace(workspace=str(workspace), host=host, port=port, app=False, no_browser=False))

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    window = webview.create_window("Wotan", url, width=1440, height=900)
    webview.start()
    server.should_exit = True
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    from .doctor import doctor_all

    print(doctor_all(args.config, args.workspace))
    return 0


def _cmd_mock(args: argparse.Namespace) -> int:
    import uvicorn

    from .mock_gateway import app

    print(f"Mock gateway + identity API on http://127.0.0.1:{args.port}/mock/...")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    """One-shot agent run (scheduler / scripting). Prints the final answer."""
    from .config import load_config
    from .db import get_db
    from .agent.session import AgentSession

    workspace = Path(args.workspace or Path.cwd()).resolve()
    ensure_layout()
    cfg = load_config(workspace=workspace)

    async def main() -> int:
        events: list[dict] = []

        async def emit(event: dict) -> None:
            events.append(event)
            if event.get("type") == "token":
                print(event.get("text", ""), end="", flush=True)
            elif event.get("type") == "tool_end":
                print(f"\n[tool] {event.get('tool')}", file=sys.stderr)

        session = AgentSession(cfg, workspace, get_db(), emit)
        result = await session.run_turn(args.prompt, model_ref=args.model or cfg.default_model)
        print()
        return 0 if result.get("status") in ("done", "finished") else 1

    return asyncio.run(main())


def main(argv: list[str] | None = None) -> int:
    utf8_console()
    parser = argparse.ArgumentParser(prog="wotan", description="Wotan - local web IDE with a built-in coding agent")
    parser.add_argument("command", nargs="?", default="serve", choices=["serve", "doctor", "probe", "mock", "run"],
                        help="serve (default) | doctor/probe | mock | run")
    parser.add_argument("rest", nargs="*", help="for 'run': the prompt")
    parser.add_argument("workspace", nargs="?", default="", help="workspace folder (default: cwd)")
    parser.add_argument("--host", default="", help="bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=0, help="port (default from config, 8765)")
    parser.add_argument("--app", action="store_true", help="open a native window (pywebview)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    parser.add_argument("--config", default="", help="config YAML path")
    parser.add_argument("--model", default="", help="model ref for 'run' (provider/model)")
    args = parser.parse_args(argv)

    # `wotan run "prompt"` packs the prompt into `rest`
    if args.command == "run":
        prompt = " ".join(args.rest) if args.rest else ""
        if not prompt:
            print("usage: wotan run \"prompt\" [workspace]")
            return 2
        ws = args.workspace or ""
        args.workspace = ws
        args.prompt = prompt  # type: ignore[attr-defined]
        return _cmd_run(args)
    if args.command in ("doctor", "probe"):
        return _cmd_doctor(args)
    if args.command == "mock":
        if not args.port:
            args.port = 8787
        return _cmd_mock(args)
    return _cmd_serve(args)


if __name__ == "__main__":
    raise SystemExit(main())
