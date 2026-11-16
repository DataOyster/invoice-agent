"""Command line: python -m invoice_agent.data_gen --seed 42 --out data/generated"""

from __future__ import annotations

import argparse
from pathlib import Path

from invoice_agent.data_gen.generator import GeneratorConfig, generate
from invoice_agent.data_gen.writer import write_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the synthetic invoice dataset.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("data/generated"))
    args = parser.parse_args()
    dataset = generate(GeneratorConfig(seed=args.seed))
    write_dataset(dataset, args.out)
    print(f"wrote {len(dataset.invoices)} documents to {args.out}")


if __name__ == "__main__":
    main()
