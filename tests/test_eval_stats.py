"""Statistics and query parsing used by the paper's evaluation harnesses.

Each expected value is worked by hand in the comment beside it, so a failure
means the formula changed, not that a fixture drifted.
"""

from __future__ import annotations

import math

import pytest

from eval.annotation_kit import kappa
from eval.baselines import mcnemar_exact, parse_query, wilson
from eval.human_eval import krippendorff_interval
from eval.live_eval import cohen_kappa


def test_wilson_matches_the_paper_interval_for_7_of_40() -> None:
    low, high = wilson(7, 40)
    assert low == pytest.approx(0.0875, abs=5e-4)
    assert high == pytest.approx(0.3196, abs=5e-4)


def test_wilson_zero_successes_has_a_zero_lower_bound() -> None:
    low, high = wilson(0, 25)
    assert low == 0.0
    assert high == pytest.approx(0.1332, abs=5e-4)


def test_mcnemar_exact_two_sided() -> None:
    # 12 gained, 4 lost: 2 * sum(C(16, 0..4)) / 2^16 = 2 * 2517 / 65536
    assert mcnemar_exact(4, 12) == pytest.approx(2 * 2517 / 65536)
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(3, 3) == 1.0


def test_cohen_kappa_binary() -> None:
    # observed 0.75, expected 0.5*0.25 + 0.5*0.75 = 0.5 -> 0.5
    assert kappa([1, 1, 0, 0], [1, 0, 0, 0]) == pytest.approx(0.5)
    assert cohen_kappa(["A", "A", "B", "B"], ["A", "B", "B", "B"]) == pytest.approx(0.5)


def test_quadratic_kappa_is_one_on_perfect_agreement_and_penalises_distance() -> None:
    assert kappa([0, 1, 2, 2], [0, 1, 2, 2], "quadratic") == pytest.approx(1.0)
    near = kappa([0, 1, 2, 2, 0], [0, 1, 1, 2, 0], "quadratic")
    far = kappa([0, 1, 2, 2, 0], [0, 1, 0, 2, 0], "quadratic")
    assert near > far


def test_krippendorff_interval() -> None:
    assert krippendorff_interval([[1, 1], [3, 3], [5, 5]]) == pytest.approx(1.0)
    # values 1,2,2,1: D_o = 4/4 = 1; D_e = 8/12; alpha = 1 - 1.5 = -0.5
    assert krippendorff_interval([[1, 2], [2, 1]]) == pytest.approx(-0.5)
    assert math.isnan(krippendorff_interval([[4]]))


def test_parse_query_drops_the_clauses_lucene_sends_to_missing_fields() -> None:
    expanded = (
        "What is the point of any of this? | Felt emotions: sadness, grief, fear"
        " | Intent: seek guidance"
    )
    terms = parse_query(expanded)
    assert "sadness" not in terms  # became a query on field 'emotions'
    assert "seek" not in terms  # became a query on field 'intent'
    assert "this" not in terms  # 'this?' is a wildcard query
    assert {"grief", "fear", "guidance", "point"} <= set(terms)
