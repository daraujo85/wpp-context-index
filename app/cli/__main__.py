"""`wpp-context` CLI: thin argparse wrapper. Parses args and delegates to
services/* (T15) — ingest calls services.ingestion.run(), search calls
services.search.search(); the CLI never duplicates their logic, same rule
as the API layer (app/api/search.py, app/api/ingest.py).

inspect/source stay stubs: no services.inspect/source exist yet (M3 scope,
out of this round).
"""
from __future__ import annotations

import argparse
import time
from datetime import date, datetime

from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table

from app.config import Settings
from app.services import ingestion
from app.services import search as search_service

_GREEN, _YELLOW, _RED, _BLUE, _DIM = "#8ec07c", "#d79921", "#fb4934", "#83a598", "#5a5a5a"
_SPARK = "▁▂▃▄▅▆▇█"
_KIND = {True: ("👥", "#d3869b", "grupo"), False: ("👤", _BLUE, "dm")}  # is_group -> (icone, cor, rotulo)


class _ProgressState:
    """Tracks what the `ingest` callback alone doesn't carry: per-source
    message counts (via delta between its "start" and "done" tick) and a
    throughput history — both derived client-side from the same cumulative
    `summary`, no service-layer change needed."""

    def __init__(self, sources: list[dict], total: int):
        self.sources = sources
        self.total = total
        self.started_at = time.monotonic()
        self.last_tick_at = self.started_at
        self.last_lidas = 0
        self.source_start_lidas: dict[str, int] = {}
        self.source_msgs: dict[str, int] = {}  # keyed by alias, not index: run()
        # reorders sources ("dead chicken") vs. the original Settings().sources()
        # order this state was built from — index would map to the wrong contact.
        self.spark: list[float] = [0.0] * 40
        self.events: list[tuple[str, str, str]] = []  # (hora, ação colorida, contato)
        self.current_alias: str | None = None

    _UNIT_TAG = {
        "prefilter_discard": (_DIM, "pré-filtro"),
        "guardrail_start": (_YELLOW, "classificando (LLM)..."),
        "guardrail_discard": (_YELLOW, "descartado"),
        "embed_start": (_YELLOW, "gerando embedding..."),
        "indexed": (_GREEN, "indexado"),
        "unit_error": (_RED, "erro"),
    }

    def _log(self, color: str, label: str, alias: str, is_group: bool) -> None:
        hora = datetime.now().strftime("%H:%M:%S")
        icon, kcolor, _ = _KIND[is_group]
        self.events.append((hora, f"[{color}]{label}[/]", f"[{kcolor}]{icon}[/] {alias}"))
        self.events = self.events[-10:]

    def tick(self, phase: str, index: int, source: dict, summary) -> float:
        is_group = source["is_group"]

        if phase not in ("start", "done"):
            # per-unit stream row: action + contato + tipo only, never body
            # text (PRD "never log personal content") — messages_read (and
            # thus throughput) only moves at source-level start/done, so
            # these don't touch the sparkline.
            color, label = self._UNIT_TAG.get(phase, (_DIM, phase))
            self._log(color, label, source["alias"], is_group)
            return 0.0

        now = time.monotonic()
        dt = max(now - self.last_tick_at, 1e-6)
        delta = summary.messages_read - self.last_lidas
        rate = delta / dt
        self.spark = (self.spark + [delta])[-40:]
        self.last_lidas = summary.messages_read
        self.last_tick_at = now

        alias = source["alias"]
        if phase == "start":
            self.current_alias = alias
            self.source_start_lidas[alias] = summary.messages_read
            self._log(_YELLOW, "lendo", alias, is_group)
        else:
            n = summary.messages_read - self.source_start_lidas.get(alias, summary.messages_read)
            self.source_msgs[alias] = n
            self._log(_GREEN, f"lida ({n} msg)", alias, is_group)
        return rate


def _bar(value: int, maximum: int, width: int, color: str) -> str:
    filled = round(width * value / maximum) if maximum else 0
    return f"[{color}]{'█' * filled}[/][{_DIM}]{'·' * (width - filled)}[/]"


def _sparkline(samples: list[float]) -> str:
    peak = max(samples) or 1
    return "".join(_SPARK[min(len(_SPARK) - 1, int(v / peak * (len(_SPARK) - 1)))] for v in samples)


def _render_progress(index: int, total: int, phase: str, summary, state: _ProgressState) -> Panel:
    """Live status panel for `ingest`, styled after the approved mockups
    (layout only — see decisions below): progress bar w/ eta+rate, a
    5-column stat strip w/ %, a source queue on the left, and on the right
    a "stream" table (hora/ação/contato) + throughput sparkline + media mix.
    Full-width (`expand=True`).

    Deliberately NOT reproduced: literal message-body snippets in the
    stream (PRD "never log personal content" — rows carry action+contato
    only) and a "motivos de descarte" breakdown (should_discard() has no
    reason taxonomy today — would need a domain change, not a CLI one)."""
    done = index if phase == "done" else index - 1
    elapsed = time.monotonic() - state.started_at
    avg_per_source = elapsed / done if done else 0
    eta = max(0, round((total - done) * avg_per_source))
    rate = sum(state.spark[-5:]) / min(5, len(state.spark)) if any(state.spark) else 0.0

    header = Progress(
        TextColumn("[bold]ingest[/]"),
        BarColumn(bar_width=None),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("{task.completed}/{task.total}"),
        TextColumn(f"[{_DIM}]· eta {eta}s · {rate:.1f} msg/s[/]"),
        expand=True,
    )
    header.add_task("ingest", total=total, completed=done)

    lidas = summary.messages_read or 1  # avoid div/0; stays 0% when truly 0
    def _pct(n: int) -> str:
        return f"[{_DIM}]{round(100 * n / lidas)}%[/]"

    stats = Table.grid(expand=True, padding=(0, 3))
    for _ in range(5):
        stats.add_column(justify="left")
    stats.add_row("lidas", "pré-filtro", "guardrail-descartadas", "indexadas", "erros")
    stats.add_row(
        f"[bold white]{summary.messages_read}[/]",
        f"[bold {_BLUE}]{summary.prefilter_discarded}[/] {_pct(summary.prefilter_discarded)}",
        f"[bold {_YELLOW}]{summary.guardrail_discarded}[/] {_pct(summary.guardrail_discarded)}",
        f"[bold {_GREEN}]{summary.contexts_indexed}[/] {_pct(summary.contexts_indexed)}",
        f"[bold {_RED}]{summary.errors}[/]" if summary.errors else f"[{_DIM}]0[/]",
    )

    n_groups = sum(1 for s in state.sources if s["is_group"])
    n_dms = len(state.sources) - n_groups
    done_groups = sum(1 for s in state.sources if s["alias"] in state.source_msgs and s["is_group"])
    done_dms = sum(1 for s in state.sources if s["alias"] in state.source_msgs and not s["is_group"])
    kind_tally = (
        f"[{_KIND[False][1]}]{_KIND[False][0]} dm {done_dms}/{n_dms}[/]"
        f"   [{_KIND[True][1]}]{_KIND[True][0]} grupo {done_groups}/{n_groups}[/]"
    )

    queue = Table(show_header=False, expand=True, box=None, padding=(0, 1))
    queue.add_column()
    for src in state.sources:
        alias = src["alias"]
        icon, kcolor, _ = _KIND[src["is_group"]]
        label = f"[{kcolor}]{icon}[/] [bold]{alias}[/]"
        # done/current keyed by alias, not list position: run() processes
        # sources in its own "dead chicken" order, not state.sources' order.
        if alias in state.source_msgs:
            n = state.source_msgs[alias]
            queue.add_row(f"[{_DIM}]✓[/] {label}  [{_DIM}]{n} msg total[/]")
        elif alias == state.current_alias:
            queue.add_row(f"[bold {_YELLOW}]▶[/] {label}  [{_YELLOW}]lendo...[/]")
        else:
            queue.add_row(f"[{_DIM}]·[/] {label}")

    activity = Table(show_header=False, expand=True, box=None, padding=(0, 1))
    activity.add_column(width=8)
    activity.add_column(width=10)
    activity.add_column()
    for hora, acao, contato in reversed(state.events):
        activity.add_row(f"[{_DIM}]{hora}[/]", acao, contato)
    if not state.events:
        activity.add_row("", "", f"[{_DIM}]...[/]")

    stream_header = f"[{_DIM}]stream[/]"
    if state.current_alias:
        stream_header += f" [{_DIM}]· lendo[/] [bold]{state.current_alias}[/]"

    media_max = max(summary.images_processed, summary.audio_processed, summary.videos_processed, 1)
    media = Table.grid(expand=True, padding=(0, 1))
    media.add_column(width=8)
    media.add_column()
    media.add_column(justify="right", width=4)
    media.add_row("imagem", _bar(summary.images_processed, media_max, 16, _BLUE), str(summary.images_processed))
    media.add_row("áudio", _bar(summary.audio_processed, media_max, 16, "#d3869b"), str(summary.audio_processed))
    media.add_row("vídeo", _bar(summary.videos_processed, media_max, 16, _BLUE), str(summary.videos_processed))

    right = Group(
        stream_header, activity,
        f"[{_DIM}]throughput[/]  [{_GREEN}]{_sparkline(state.spark)}[/]",
        f"[{_DIM}]mídia processada[/]", media,
    )
    columns = Table.grid(expand=True, padding=(0, 2))
    columns.add_column(ratio=2)
    columns.add_column(ratio=3)
    columns.add_row(Group(f"[{_DIM}]fila[/]  {kind_tally}", queue), right)

    return Panel(
        Group(header, stats, columns),
        title=f"[bold]wpp-context ingest[/]  [{index}/{total}]",
        subtitle=datetime.now().strftime("%H:%M:%S"),
        border_style="blue",
        expand=True,
    )


def _iso_date(value: str) -> str:
    date.fromisoformat(value)  # raises ValueError on bad format
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wpp-context")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="ingest messages for a date range")
    ingest.add_argument("--start", type=_iso_date, required=True)
    ingest.add_argument("--end", type=_iso_date, required=True)

    search = sub.add_parser("search", help="search indexed messages")
    search.add_argument("query")

    inspect = sub.add_parser("inspect", help="inspect a message by id")
    inspect.add_argument("id")

    source = sub.add_parser("source", help="show source/provenance for an id")
    source.add_argument("id")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "ingest":
        sources = Settings().sources()
        state = _ProgressState(sources, len(sources))
        with Live(refresh_per_second=4) as live:
            def on_progress(phase, i, t, src, s):
                state.tick(phase, i, src, s)
                live.update(_render_progress(i, t, phase, s, state))

            summary = ingestion.run(
                f"{args.start}T00:00:00", f"{args.end}T00:00:00",
                on_progress=on_progress,
            )
        print(
            f"ingest: range {args.start}..{args.end} run_id={summary.run_id} "
            f"messages_read={summary.messages_read} "
            f"contexts_indexed={summary.contexts_indexed} errors={summary.errors}"
        )
        print("total por contato (só esta execução):")
        for src in sources:
            kind = "grupo" if src["is_group"] else "dm"
            n = state.source_msgs.get(src["alias"], 0)
            print(f"  {src['alias']} ({kind}): {n} msg")
    elif args.command == "search":
        results = search_service.search(args.query)
        print(f"search: query={args.query!r} results={len(results)}")
        for r in results:
            print(f"  - {r.context_id} score={r.score:.3f} title={r.title!r}")
    elif args.command == "inspect":
        print(f"inspect: id={args.id!r} (services.inspect not implemented yet)")
    elif args.command == "source":
        print(f"source: id={args.id!r} (services.source not implemented yet)")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
