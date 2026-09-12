import asyncio

from backend.app.research import _fact_check, _result_url


def test_search_redirect_is_normalized():
    assert _result_url("/l/?uddg=https%3A%2F%2Fexample.org%2Freport") == "https://example.org/report"


def test_fact_checker_withholds_obvious_other_city_source():
    facts, gaps = _fact_check(
        "Chennai",
        [{
            "claim": "Health evidence",
            "category": "source_evidence",
            "evidence_quote": "Pune: hypertension and diabetes screening expanded across public health facilities.",
            "source_title": "Pune: Screening report",
            "source_url": "https://example.org/pune",
        }],
    )
    assert facts == []
    assert len(gaps) == 1


def test_fact_checker_accepts_qualified_health_evidence():
    facts, gaps = _fact_check(
        "Chennai",
        [{
            "claim": "A health report discusses hypertension care.",
            "category": "source_evidence",
            "evidence_quote": "This public health report discusses hypertension and diabetes prevention programs in primary care.",
            "source_title": "National health report",
            "source_url": "https://example.org/health",
        }],
    )
    assert facts[0]["verification_status"] == "PARTIALLY_SUPPORTED"
    assert gaps == []