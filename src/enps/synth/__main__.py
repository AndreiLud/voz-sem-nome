"""Command line: python -m enps.synth [--seed N] [--out DIR] [--phrasebank FILE]."""
from __future__ import annotations

import argparse
from pathlib import Path

from .bank import DEFAULT_BANK_PATH, load_bank
from .generator import DEFAULT_OUT, generate, write_dataset
from .story import DEFAULT_SEED
from .summary import summarize


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m enps.synth",
                                     description="Write the synthetic survey files.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output folder")
    parser.add_argument("--phrasebank", type=Path, default=DEFAULT_BANK_PATH)
    parser.add_argument("--quiet", action="store_true", help="do not print the summary")
    args = parser.parse_args(argv)

    dataset = generate(args.seed, load_bank(args.phrasebank))
    paths = write_dataset(dataset, args.out)
    if not args.quiet:
        print(summarize(dataset))
        print("\nWrote " + ", ".join(str(p) for p in paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
