"""Claude extractor pipeline with a fake LLM: conversion, confidence, failures, cache, report.
No network: FakeLLMClient answers from the ground truth."""

from __future__ import annotations

import pytest

from invoice_agent.data_gen.models import InvoiceGroundTruth
from invoice_agent.evals.llm_runs import load_cache, run_split
from invoice_agent.evals.reference import load_reference
from invoice_agent.evals.run_eval import build_report
from invoice_agent.extraction.llm import (
    FakeLLMClient,
    LLMInvoiceRead,
    LLMLine,
    catalog_prompt,
    extract_with_llm,
    to_extracted,
)


def perfect_read(gt: InvoiceGroundTruth, org_number: str) -> LLMInvoiceRead:
    """What a flawless reader would answer for this document."""
    return LLMInvoiceRead(
        document_type=gt.document_type,
        language=gt.language,
        supplier_org_number=org_number,
        number=gt.number,
        invoice_date=gt.invoice_date.isoformat(),
        delivery_date=gt.delivery_date.isoformat(),
        delivery_note_id=gt.delivery_note_id,
        credits_invoice_number=gt.credits_invoice_number,
        lines=[
            LLMLine(
                description=x.description,
                product_id=x.product_id,
                qty=x.qty,
                unit=x.unit,
                unit_price=x.unit_price,
                vat_rate=x.vat_rate,
                line_total=x.line_total,
            )
            for x in gt.lines
        ],
        total_excl_vat=gt.total_excl_vat,
        vat_total=gt.vat_total,
        total_incl_vat=gt.total_incl_vat,
    )


@pytest.fixture(scope="module")
def ref(generated_dir):
    return load_reference(generated_dir)


@pytest.fixture(scope="module")
def fake(ref, generated_dir):
    orgs = {s.supplier_id: s.org_number for s in ref.suppliers}
    answers = {
        FakeLLMClient.key((generated_dir / g.file).read_bytes()): perfect_read(
            g, orgs[g.supplier_id]
        )
        for g in ref.ground_truth
        if g.split == "hard"
    }
    return FakeLLMClient(answers)


@pytest.fixture(scope="module")
def known(ref):
    return {p.product_id for p in ref.products}


def _hard(ref):
    return [g for g in ref.ground_truth if g.split == "hard"]


def test_a_perfect_answer_gets_full_confidence(ref, generated_dir, fake, known):
    gt = _hard(ref)[0]
    pdf = (generated_dir / gt.file).read_bytes()
    inv, result = extract_with_llm(pdf, gt.invoice_id, fake, "catalog", known)
    assert result is not None and inv.confidence == 1.0 and inv.problems == []
    assert inv.number == gt.number and inv.delivery_date == gt.delivery_date
    assert [x.product_id for x in inv.lines] == [x.product_id for x in gt.lines]


def test_answer_whose_totals_do_not_add_up_goes_to_review(ref, known):
    gt = _hard(ref)[0]
    read = perfect_read(gt, "000000-0001").model_copy(update={"total_incl_vat": 1.0})
    assert to_extracted(gt.invoice_id, read, known).confidence < 0.9


def test_invented_product_id_is_dropped_and_reported(ref, known):
    gt = _hard(ref)[0]
    read = perfect_read(gt, "000000-0001")
    read.lines[0].product_id = "P999"
    inv = to_extracted(gt.invoice_id, read, known)
    assert inv.lines[0].product_id is None
    assert any("unknown product id" in p for p in inv.problems)


def test_unparseable_date_is_a_problem_not_a_crash(ref, known):
    gt = _hard(ref)[0]
    read = perfect_read(gt, "000000-0001").model_copy(update={"invoice_date": "28 Jan"})
    inv = to_extracted(gt.invoice_id, read, known)
    assert inv.invoice_date is None and inv.confidence < 1.0


def test_failed_call_becomes_a_zero_confidence_read(known):
    inv, result = extract_with_llm(b"not a known pdf", "INV-X", FakeLLMClient({}), "c", known)
    assert result is None and inv.confidence == 0.0


def test_catalog_prompt_lists_every_product(ref):
    text = catalog_prompt(ref.suppliers, ref.products)
    assert all(p.product_id in text for p in ref.products)


def test_cache_is_written_once_and_reused(ref, generated_dir, fake, tmp_path):
    run_split(ref, generated_dir, "hard", fake, tmp_path, seed=42)
    calls = fake.calls
    assert len(load_cache(tmp_path, 42, fake.model)) == 30
    run_split(ref, generated_dir, "hard", fake, tmp_path, seed=42)
    assert fake.calls == calls  # nothing new was sent


def test_report_includes_claude_section_when_the_cache_is_complete(
    ref, generated_dir, fake, tmp_path
):
    run_split(ref, generated_dir, "hard", fake, tmp_path, seed=42)
    report = build_report(generated_dir, tmp_path, fake.model)
    assert "### Extraction: hard, Claude" in report
    assert "checks on Claude reads" in report
    assert "Claude extraction on the standard split: not run yet" in report
