"""Research depth: the user's choice between speed and thoroughness.

Almost all of a run's time goes on reading sources, extracting claims from them
and checking those claims, and all three scale with how many sources are read.
A follow-up research round roughly doubles the work again. So depth is not just
"fewer sources": the quick profile also forbids a second round, because reading
fewer sources makes it more likely that coverage falls short and triggers one.

Estimates are measured on Groq's free tier with extraction spread across three
models. They move with provider load and with how much has been published about
a city, so the UI presents them as approximate.
"""

from dataclasses import asdict, dataclass
from typing import Literal

Depth = Literal["quick", "balanced", "thorough"]


@dataclass(frozen=True)
class ResearchProfile:
    key: Depth
    label: str
    summary: str
    estimate: str
    queries_per_round: int
    results_per_query: int
    max_fetches_per_round: int
    max_claims_per_source: int
    document_chars: int
    max_rounds: int

    def as_dict(self) -> dict:
        return asdict(self)


PROFILES: dict[str, ResearchProfile] = {
    "quick": ResearchProfile(
        key="quick",
        label="Quick",
        summary="5 sources, a single round. Best for a first look; expect more open gaps.",
        estimate="about 1-2 minutes",
        queries_per_round=3,
        results_per_query=4,
        max_fetches_per_round=5,
        max_claims_per_source=3,
        document_chars=4000,
        max_rounds=1,
    ),
    "balanced": ResearchProfile(
        key="balanced",
        label="Balanced",
        summary="8 sources, and a smaller second round if coverage falls short.",
        estimate="about 2-4 minutes",
        queries_per_round=5,
        results_per_query=5,
        max_fetches_per_round=8,
        max_claims_per_source=4,
        document_chars=6000,
        max_rounds=2,
    ),
    "thorough": ResearchProfile(
        key="thorough",
        label="Thorough",
        summary="12 sources, more claims per source, and a second round if needed. Best before a meeting.",
        estimate="about 4-8 minutes",
        queries_per_round=6,
        results_per_query=6,
        max_fetches_per_round=12,
        max_claims_per_source=5,
        document_chars=7000,
        max_rounds=2,
    ),
}

DEFAULT_DEPTH: Depth = "balanced"


def get_profile(depth: str | None) -> ResearchProfile:
    return PROFILES.get((depth or DEFAULT_DEPTH).lower(), PROFILES[DEFAULT_DEPTH])
