"""Command line: python -m arxiv_radar <command>."""
from __future__ import annotations

import argparse
import logging
import sys

from .config import load_config


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="arxiv_radar", description=__doc__)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="fetch, classify and write today's papers")
    r.add_argument("--date", help="override the day label (YYYY-MM-DD)")
    r.add_argument("--offline", metavar="DIR", help="read a recorded day (rss/, api/, seeds/ XML) instead of arXiv")
    r.add_argument("--dry-run", action="store_true", help="write output to --out instead of the data dir")
    r.add_argument("--out", default="out", help="output dir for --dry-run")
    r.add_argument("--skip-citations", action="store_true")

    sub.add_parser("refresh", help="backfill newly followed authors and refresh citation counts")
    sub.add_parser("citations", help="refresh citation counts only")
    sub.add_parser("retrain", help="train the classifier from current feedback without fetching")
    rs = sub.add_parser("restat", help="compute analytics for a day processed before they existed")
    rs.add_argument("date")

    b = sub.add_parser("build-site", help="assemble the static dashboard into _site/")
    b.add_argument("--out", default="_site")

    e = sub.add_parser("export-bib", help="print BibTeX for a Library folder")
    e.add_argument("folder")

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)

    from . import pipeline
    from .store import Store

    if args.cmd == "run":
        pipeline.run(cfg, day=args.date, offline=args.offline,
                     out_dir=args.out if args.dry_run else None, skip_citations=args.skip_citations)
    elif args.cmd == "refresh":
        pipeline.refresh(cfg)
    elif args.cmd == "citations":
        store = Store(cfg.data_dir)
        pipeline.refresh_citations(cfg, store, store)
    elif args.cmd == "restat":
        st = pipeline.restat(cfg, args.date)
        print(f"{args.date}: {st['general']['count']} papers, {st['personal']['count']} passed")
    elif args.cmd == "retrain":
        pipeline.retrain(cfg)
    elif args.cmd == "build-site":
        print(pipeline.build_site(cfg, out=args.out))
    elif args.cmd == "export-bib":
        from .bibtex import folder_bibtex

        sys.stdout.write(folder_bibtex(Store(cfg.data_dir).read("library.json"), args.folder))
    return 0


if __name__ == "__main__":
    sys.exit(main())
