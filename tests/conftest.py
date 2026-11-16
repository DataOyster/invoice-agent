"""Shared fixtures: generate the dataset once per test session into a temporary folder."""

from __future__ import annotations

import pytest

from invoice_agent.data_gen.generator import GeneratorConfig, generate
from invoice_agent.data_gen.writer import write_dataset


@pytest.fixture(scope="session")
def generated_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("generated")
    write_dataset(generate(GeneratorConfig(seed=42)), out)
    return out
