"""Research depth: the user's choice between speed and thoroughness.

Almost all of a run's time goes on reading sources, extracting claims from them
and checking those claims, and all three scale with how many sources are read.
A follow-up research round roughly doubles the work again. So depth is not just
"fewer sources": the quick profile also forbids a second round, because reading
fewer sources makes it more likely that coverage falls short and triggers one.

``max_sources`` is a cap on pages read across the whole run, so the number the
user chose is the number they get. A profile with a second round reads
``first_round_sources`` first and keeps the rest in reserve for the follow-up.

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
    max_sources: int
    first_round_sources: int
    max_claims_per_source: int
    document_chars: int
    max_rounds: int

    def source_budget(self, round_number: int, already_read: int) -> int:
        """Pages this round may read, never letting the run exceed ``max_sources``."""
        planned = self.first_round_sources if round_number <= 1 else self.max_sources
        return max(0, min(planned, self.max_sources) - already_read)

    def as_dict(self) -> dict:
        return asdict(self)


PROFILES: dict[str, ResearchProfile] = {
    "quick": ResearchProfile(
        key="quick",
        label="Quick",
        summary="Reads 5 sources in a single round. Best for a first look; expect more open gaps.",
        estimate="about 1-2 minutes",
        queries_per_round=3,
        results_per_query=4,
        max_sources=5,
        first_round_sources=5,
        max_claims_per_source=3,
        document_chars=4000,
        max_rounds=1,
    ),
    "balanced": ResearchProfile(
        key="balanced",
        label="Balanced",
        summary="Reads up to 8 sources: 6 first, 2 more in a second round if coverage falls short.",
        estimate="about 2-4 minutes",
        queries_per_round=5,
        results_per_query=5,
        max_sources=8,
        first_round_sources=6,
        max_claims_per_source=4,
        document_chars=6000,
        max_rounds=2,
    ),
    "thorough": ResearchProfile(
        key="thorough",
        label="Thorough",
        summary="Reads up to 12 sources: 9 first, 3 more in a second round if needed, with more claims per source. Best before a meeting.",
        estimate="about 4-8 minutes",
        queries_per_round=6,
        results_per_query=6,
        max_sources=12,
        first_round_sources=9,
        max_claims_per_source=5,
        document_chars=7000,
        max_rounds=2,
    ),
}

DEFAULT_DEPTH: Depth = "balanced"


def get_profile(depth: str | None) -> ResearchProfile:
    return PROFILES.get((depth or DEFAULT_DEPTH).lower(), PROFILES[DEFAULT_DEPTH])
