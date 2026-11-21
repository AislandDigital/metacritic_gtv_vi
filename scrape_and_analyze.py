#!/usr/bin/env python3
"""
Compara notas de usuários no Metacritic:

  quem deu nota BAIXA no GTA VI também deu nota ALTA no
  Zelda: Ocarina of Time remake (2026)?

Comandos separados:
  scrape-gta      → data/gta_vi_reviews.json
  scrape-zelda    → data/zelda_oot_2026_reviews.json
  scrape-users    → data/user_profiles/<author>.json  (perfis dos low-scorers do GTA)
  analyze         → cruza os três e gera data/analysis_report.json
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import httpx
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table

console = Console()

BASE = "https://backend.metacritic.com/reviews/metacritic/user"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

GTA_SLUG = "grand-theft-auto-vi"
ZELDA_SLUG = "the-legend-of-zelda-ocarina-of-time-2026"

DATA_DIR = Path(__file__).resolve().parent / "data"
GTA_PATH = DATA_DIR / "gta_vi_reviews.json"
ZELDA_PATH = DATA_DIR / "zelda_oot_2026_reviews.json"
PROFILES_DIR = DATA_DIR / "user_profiles"
REPORT_PATH = DATA_DIR / "analysis_report.json"
MATCHES_PATH = DATA_DIR / "hypothesis_matches.json"


@dataclass
class Review:
    id: str
    author: str
    score: int | None
    date: str | None
    quote: str | None
    platform: str | None
    game_slug: str
    game_title: str


def _slug_from_url(url: str | None) -> str:
    if not url:
        return ""
    parts = [p for p in url.strip("/").split("/") if p]
    return parts[-1] if parts else ""


def parse_item(item: dict, fallback_slug: str = "") -> Review:
    product = item.get("reviewedProduct") or {}
    url = product.get("url") or ""
    return Review(
        id=str(item.get("id") or ""),
        author=str(item.get("author") or ""),
        score=item.get("score"),
        date=item.get("date"),
        quote=item.get("quote"),
        platform=item.get("platform") or (product.get("platform") or {}).get("name"),
        game_slug=_slug_from_url(url) or fallback_slug,
        game_title=str(product.get("title") or fallback_slug),
    )


def save_json(path: Path, rows: Iterable[Review]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(r) for r in rows], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_json(path: Path) -> list[Review]:
    return [Review(**row) for row in json.loads(path.read_text(encoding="utf-8"))]


def merge_reviews(*batches: list[Review]) -> list[Review]:
    seen: set[str] = set()
    out: list[Review] = []
    for batch in batches:
        for r in batch:
            key = r.id or f"{r.author}:{r.game_slug}:{r.score}:{r.date}:{r.quote}"
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
    return out


def is_oot_2026(r: Review) -> bool:
    return "ocarina-of-time-2026" in (r.game_slug or "").lower()


class MetacriticClient:
    def __init__(self, delay: float = 0.3, timeout: float = 30.0):
        self.delay = delay
        self.client = httpx.Client(headers=HEADERS, timeout=timeout, follow_redirects=True)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> MetacriticClient:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def _get(self, url: str, params: dict | None = None) -> dict:
        for attempt in range(5):
            try:
                r = self.client.get(url, params=params)
                if r.status_code == 429:
                    wait = 2**attempt
                    console.log(f"[yellow]rate limit, {wait}s[/yellow]")
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                time.sleep(self.delay)
                return r.json()
            except httpx.HTTPError as e:
                if attempt == 4:
                    raise
                wait = 1.5**attempt
                console.log(f"[yellow]erro {e}; retry {wait:.1f}s[/yellow]")
                time.sleep(wait)
        return {}

    def fetch_game_reviews(
        self,
        slug: str,
        *,
        sentiment: str = "all",
        limit: int = 100,
        max_results: int | None = None,
        label: str | None = None,
    ) -> list[Review]:
        url = f"{BASE}/games/{slug}/web"
        first = self._get(
            url,
            {
                "offset": 0,
                "limit": min(limit, 100),
                "filterBySentiment": sentiment,
                "sort": "date",
            },
        )
        total = int((first.get("data") or {}).get("totalResults") or 0)
        if max_results is not None:
            total = min(total, max_results)

        items = list((first.get("data") or {}).get("items") or [])
        reviews = [parse_item(i, slug) for i in items]

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(label or slug, total=max(total, 1))
            progress.update(task, completed=len(reviews))
            offset = len(reviews)
            while offset < total:
                page = self._get(
                    url,
                    {
                        "offset": offset,
                        "limit": min(limit, 100),
                        "filterBySentiment": sentiment,
                        "sort": "date",
                    },
                )
                batch = (page.get("data") or {}).get("items") or []
                if not batch:
                    break
                reviews.extend(parse_item(i, slug) for i in batch)
                offset += len(batch)
                progress.update(task, completed=min(len(reviews), total), total=total)

        return merge_reviews(reviews)

    def fetch_user_reviews(self, username: str, limit: int = 100) -> list[Review]:
        url = f"{BASE}/users/{username}/web"
        reviews: list[Review] = []
        offset = 0
        while True:
            page = self._get(
                url,
                {"filterByType": "games", "offset": offset, "limit": min(limit, 100)},
            )
            batch = (page.get("data") or {}).get("items") or []
            if not batch:
                break
            reviews.extend(parse_item(i) for i in batch)
            total = int((page.get("data") or {}).get("totalResults") or 0)
            offset += len(batch)
            if offset >= total:
                break
        return reviews


# ── commands ──────────────────────────────────────────────────────────────


def fetch_game_stats(client: MetacriticClient, slug: str) -> dict:
    url = f"{BASE}/games/{slug}/stats/web"
    return (client._get(url).get("data") or {}).get("item") or {}


def cmd_scrape_gta(args: argparse.Namespace) -> None:
    """
    Baixa o máximo possível de user reviews do GTA VI.

    Metacritic tem ~55k *notas*, mas a API pública só lista reviews *com texto*
    e limita ~10k por filtro de sentimento. Por isso baixamos positive + neutral
    + negative (+ all) e fazemos merge.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with MetacriticClient(delay=args.delay) as client:
        console.print("[bold]Baixando stats agregados do GTA VI…[/bold]")
        stats = fetch_game_stats(client, GTA_SLUG)
        stats_path = DATA_DIR / "gta_vi_stats.json"
        stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(
            f"Notas totais no site: {stats.get('reviewCount')} | "
            f"média {stats.get('score')} | "
            f"+{stats.get('positiveCount')} ~{stats.get('neutralCount')} "
            f"-{stats.get('negativeCount')}"
        )
        console.print(
            "[dim]API só expõe reviews com texto (~10k/filtro). "
            "Notas sem texto não são listáveis.[/dim]"
        )

        console.print("[bold]Baixando reviews com texto (todos os sentimentos)…[/bold]")
        batches: list[Review] = []
        for sentiment, label in [
            ("positive", "GTA VI positive"),
            ("neutral", "GTA VI mixed"),
            ("negative", "GTA VI negative"),
            ("all", "GTA VI all"),
        ]:
            rows = client.fetch_game_reviews(
                GTA_SLUG,
                sentiment=sentiment,
                max_results=args.max,
                label=label,
            )
            console.print(f"  {sentiment}: {len(rows)}")
            batches.append(rows)

        rows = merge_reviews(*batches)
        save_json(GTA_PATH, rows)
        console.print(f"[green]OK[/green] {GTA_PATH} ({len(rows)} reviews únicas com texto)")


def cmd_scrape_zelda(args: argparse.Namespace) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with MetacriticClient(delay=args.delay) as client:
        console.print("[bold]2/3 Baixando reviews do Ocarina of Time remake (2026)…[/bold]")
        sentiment = "positive" if args.positive_only else "all"
        rows = client.fetch_game_reviews(
            ZELDA_SLUG,
            sentiment=sentiment,
            max_results=args.max,
            label="OoT 2026",
        )
        # se baixou all, também pega positive para cobrir o teto de 10k
        if sentiment == "all":
            pos = client.fetch_game_reviews(
                ZELDA_SLUG,
                sentiment="positive",
                max_results=args.max,
                label="OoT 2026 positive",
            )
            rows = merge_reviews(rows, pos)
        save_json(ZELDA_PATH, rows)
        console.print(f"[green]OK[/green] {ZELDA_PATH} ({len(rows)} reviews)")


def cmd_scrape_users(args: argparse.Namespace) -> None:
    if not GTA_PATH.exists():
        console.print("[red]Falta data/gta_vi_reviews.json — rode scrape-gta primeiro[/red]")
        raise SystemExit(1)

    gta = load_json(GTA_PATH)
    low_authors = sorted(
        {
            r.author
            for r in gta
            if r.author and r.score is not None and r.score <= args.low_max
        }
    )

    # pula quem já tem review na página do OoT (já sabemos a nota)
    already_on_zelda: set[str] = set()
    if ZELDA_PATH.exists() and not args.include_known:
        already_on_zelda = {r.author for r in load_json(ZELDA_PATH) if r.author}
        before = len(low_authors)
        low_authors = [a for a in low_authors if a not in already_on_zelda]
        console.print(
            f"Pulando {before - len(low_authors)} low-scorers que já têm review no OoT"
        )

    if args.max_users is not None:
        low_authors = low_authors[: args.max_users]

    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    done = {p.stem for p in PROFILES_DIR.glob("*.json")}
    todo = [a for a in low_authors if a not in done]

    console.print(
        f"[bold]3/3 Baixando perfis de low-scorers do GTA (≤{args.low_max})…[/bold]"
    )
    already = len(done.intersection(set(low_authors)))
    console.print(
        f"Alvos: {len(low_authors)} | já baixados: {already} | faltam: {len(todo)}"
    )

    errors = 0
    with MetacriticClient(delay=args.delay) as client:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("perfis", total=len(todo))
            for author in todo:
                try:
                    rows = client.fetch_user_reviews(author)
                    save_json(PROFILES_DIR / f"{author}.json", rows)
                except Exception as e:
                    errors += 1
                    console.log(f"[red]{author}: {e}[/red]")
                progress.advance(task)

    console.print(
        f"[green]OK[/green] {PROFILES_DIR}/ "
        f"({len(list(PROFILES_DIR.glob('*.json')))} perfis, {errors} erros)"
    )


def _zelda_score_from_sources(
    author: str,
    zelda_by_author: dict[str, Review],
) -> tuple[int | None, str | None, str | None]:
    """Retorna (score, title, source) — source = 'zelda_page' | 'profile' | None."""
    if author in zelda_by_author:
        r = zelda_by_author[author]
        return r.score, r.game_title, "zelda_page"

    profile = PROFILES_DIR / f"{author}.json"
    if profile.exists():
        best: Review | None = None
        for r in load_json(profile):
            if not is_oot_2026(r) or r.score is None:
                continue
            if best is None or r.score > (best.score or -1):
                best = r
        if best is not None:
            return best.score, best.game_title, "profile"
    return None, None, None


def cmd_analyze(args: argparse.Namespace) -> None:
    if not GTA_PATH.exists():
        console.print("[red]Falta scrape-gta[/red]")
        raise SystemExit(1)

    gta = load_json(GTA_PATH)
    zelda = load_json(ZELDA_PATH) if ZELDA_PATH.exists() else []

    gta_by_author: dict[str, Review] = {}
    for r in gta:
        if not r.author:
            continue
        prev = gta_by_author.get(r.author)
        if prev is None or (r.score is not None and (prev.score is None or r.score < prev.score)):
            gta_by_author[r.author] = r

    zelda_by_author: dict[str, Review] = {}
    for r in zelda:
        if not r.author or r.score is None:
            continue
        prev = zelda_by_author.get(r.author)
        if prev is None or r.score > (prev.score or -1):
            zelda_by_author[r.author] = r

    low_authors = {
        a
        for a, r in gta_by_author.items()
        if r.score is not None and r.score <= args.low_max
    }

    profiles_loaded = len(list(PROFILES_DIR.glob("*.json"))) if PROFILES_DIR.exists() else 0

    matches = []
    both = []  # reviewed both (any Zelda score)
    zelda_known_for_low = 0
    source_counts = Counter()

    for author in sorted(low_authors):
        z_score, z_title, source = _zelda_score_from_sources(author, zelda_by_author)
        if z_score is None:
            continue
        zelda_known_for_low += 1
        source_counts[source or "?"] += 1
        g = gta_by_author[author]
        row = {
            "author": author,
            "gta_score": g.score,
            "zelda_score": z_score,
            "zelda_title": z_title,
            "source": source,
            "gta_quote": (g.quote or "")[:240],
        }
        both.append(row)
        if z_score >= args.zelda_min:
            matches.append(row)

    # padrões entre quem tem nota nos dois
    patterns = Counter()
    for row in both:
        g, z = row["gta_score"], row["zelda_score"]
        if z >= args.zelda_min and g <= args.low_max:
            patterns["zelda_alta_gta_baixa"] += 1
        elif z >= 8 and g <= args.low_max:
            patterns["zelda_boa_gta_baixa"] += 1
        elif z <= args.low_max:
            patterns["ambos_baixos"] += 1
        else:
            patterns["outro"] += 1

    score_dist = Counter(r.score for r in gta if r.score is not None)

    report = {
        "thresholds": {"low_max": args.low_max, "zelda_min": args.zelda_min},
        "gta_reviews": len(gta),
        "gta_unique_authors": len(gta_by_author),
        "gta_score_distribution": dict(sorted(score_dist.items())),
        "low_gta_authors": len(low_authors),
        "zelda_reviews": len(zelda),
        "zelda_unique_authors": len(zelda_by_author),
        "user_profiles_downloaded": profiles_loaded,
        "low_scorers_with_known_zelda_score": zelda_known_for_low,
        "zelda_score_sources": dict(source_counts),
        "hypothesis_matches": len(matches),
        "hypothesis_rate_among_low": (
            len(matches) / len(low_authors) if low_authors else 0.0
        ),
        "hypothesis_rate_among_low_with_zelda_score": (
            len(matches) / zelda_known_for_low if zelda_known_for_low else 0.0
        ),
        "patterns_among_both": dict(patterns),
        "examples": matches[:50],
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    MATCHES_PATH.write_text(json.dumps(matches, ensure_ascii=False, indent=2), encoding="utf-8")

    # print
    console.print()
    console.rule("[bold]GTA VI baixa × Ocarina remake alta")
    console.print(f"Reviews GTA: {report['gta_reviews']} | autores: {report['gta_unique_authors']}")
    console.print(f"Reviews OoT 2026: {report['zelda_reviews']} | autores: {report['zelda_unique_authors']}")
    console.print(f"Perfis de usuário baixados: {profiles_loaded}")

    dist = Table(title="Notas GTA VI (reviews com texto)")
    dist.add_column("Nota")
    dist.add_column("Qtd", justify="right")
    for s, n in report["gta_score_distribution"].items():
        dist.add_row(str(s), str(n))
    console.print(dist)

    console.print()
    console.print(f"Autores com GTA ≤ {args.low_max}: [bold]{len(low_authors)}[/bold]")
    console.print(
        f"Desses, com nota conhecida no OoT remake: [bold]{zelda_known_for_low}[/bold]"
    )
    console.print(
        f"Matches (GTA ≤ {args.low_max} E OoT ≥ {args.zelda_min}): "
        f"[bold cyan]{len(matches)}[/bold cyan]"
    )
    console.print(
        f"% entre todos os low-scorers: "
        f"{report['hypothesis_rate_among_low']*100:.2f}%"
    )
    console.print(
        f"% entre low-scorers que TAMBÉM avaliaram OoT: "
        f"{report['hypothesis_rate_among_low_with_zelda_score']*100:.2f}%"
    )
    console.print(f"Padrões (quem tem nota nos dois): {dict(patterns)}")

    if matches:
        t = Table(title="Exemplos: GTA baixa + OoT alta")
        t.add_column("Autor")
        t.add_column("GTA", justify="right")
        t.add_column("OoT", justify="right")
        t.add_column("Fonte")
        for row in matches[:25]:
            t.add_row(
                row["author"],
                str(row["gta_score"]),
                str(row["zelda_score"]),
                row["source"] or "",
            )
        console.print(t)

    rate = report["hypothesis_rate_among_low_with_zelda_score"]
    console.print()
    if zelda_known_for_low == 0:
        verdict = (
            "Ainda não há overlap suficiente. "
            "Rode scrape-zelda e scrape-users, depois analyze de novo."
        )
    elif rate < 0.15:
        verdict = (
            "Hipótese fraca: entre quem avaliou os dois, a maioria dos "
            "low-scorers do GTA NÃO deu 10 no Ocarina remake."
        )
    elif rate < 0.40:
        verdict = (
            "Hipótese parcial: existe overlap relevante, mas não explica "
            "sozinha a maior parte das notas baixas."
        )
    else:
        verdict = (
            "Hipótese forte: boa parte de quem deu nota baixa no GTA e "
            "também jogou/avaliou OoT deu nota alta no remake."
        )
    console.print(f"[bold green]Veredito:[/bold green] {verdict}")
    console.print(f"\nRelatório: {REPORT_PATH}")
    console.print(f"Matches:   {MATCHES_PATH}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Compara notas GTA VI × Ocarina of Time remake (2026)"
    )
    p.add_argument("--delay", type=float, default=0.3)
    sub = p.add_subparsers(dest="cmd", required=True)

    gta = sub.add_parser("scrape-gta", help="Baixa reviews do GTA VI")
    gta.add_argument("--max", type=int, default=None)
    gta.set_defaults(func=cmd_scrape_gta)

    zelda = sub.add_parser("scrape-zelda", help="Baixa reviews do OoT remake 2026")
    zelda.add_argument("--max", type=int, default=None)
    zelda.add_argument("--positive-only", action="store_true")
    zelda.set_defaults(func=cmd_scrape_zelda)

    users = sub.add_parser(
        "scrape-users",
        help="Baixa perfis dos usuários que deram nota baixa no GTA",
    )
    users.add_argument("--low-max", type=int, default=1, help="Nota GTA considerada baixa")
    users.add_argument("--max-users", type=int, default=None, help="Limite de perfis")
    users.add_argument(
        "--include-known",
        action="store_true",
        help="Também baixa perfis de quem já aparece nas reviews do OoT",
    )
    users.set_defaults(func=cmd_scrape_users)

    an = sub.add_parser("analyze", help="Cruza GTA × Zelda × perfis")
    an.add_argument("--low-max", type=int, default=1)
    an.add_argument("--zelda-min", type=int, default=10)
    an.set_defaults(func=cmd_analyze)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
