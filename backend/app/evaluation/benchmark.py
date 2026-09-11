"""Benchmark repositories and questions.

Ground truth is the set of files that must appear in the retrieved context for
an answer to be possible. It is deliberately conservative: a question's
``relevant_files`` lists only files a careful engineer would agree are needed,
so a high score cannot be earned by retrieving everything.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


class Category(StrEnum):
    REPOSITORY = "repository"
    COMPONENT = "component"
    RELATIONSHIP = "relationship"
    FLOW = "flow"
    NAVIGATION = "navigation"


@dataclass
class BenchmarkQuestion:
    id: str
    question: str
    category: Category
    relevant_files: list[str]
    relevant_entities: list[str] = field(default_factory=list)
    #: Substrings an acceptable answer should mention. Used as a coarse
    #: correctness signal, not as a substitute for human judgement.
    expected_mentions: list[str] = field(default_factory=list)


@dataclass
class Benchmark:
    name: str
    #: Either a local path (deterministic, used in CI) or a public git URL.
    local_path: Path | None
    url: str | None
    questions: list[BenchmarkQuestion]


SAMPLE_REPO_QUESTIONS = [
    BenchmarkQuestion(
        id="mini-repo-1",
        question="What does this repository do?",
        category=Category.REPOSITORY,
        relevant_files=["README.md"],
        expected_mentions=["login", "user"],
    ),
    BenchmarkQuestion(
        id="mini-repo-2",
        question="How is this application structured?",
        category=Category.REPOSITORY,
        relevant_files=["README.md", "app/main.py"],
        expected_mentions=["app"],
    ),
    BenchmarkQuestion(
        id="mini-comp-1",
        question="Where is authentication implemented?",
        category=Category.COMPONENT,
        relevant_files=["app/auth/service.py"],
        relevant_entities=["AuthService", "login"],
        expected_mentions=["AuthService"],
    ),
    BenchmarkQuestion(
        id="mini-comp-2",
        question="What does the AuthService class do?",
        category=Category.COMPONENT,
        relevant_files=["app/auth/service.py"],
        relevant_entities=["AuthService"],
        expected_mentions=["session", "credential"],
    ),
    BenchmarkQuestion(
        id="mini-comp-3",
        question="How are sessions created?",
        category=Category.COMPONENT,
        relevant_files=["app/core/session.py"],
        relevant_entities=["SessionManager", "create"],
        expected_mentions=["token"],
    ),
    BenchmarkQuestion(
        id="mini-rel-1",
        question="What calls `find_by_email`?",
        category=Category.RELATIONSHIP,
        relevant_files=["app/auth/service.py", "app/users/repository.py"],
        relevant_entities=["login", "find_by_email"],
        expected_mentions=["login"],
    ),
    BenchmarkQuestion(
        id="mini-rel-2",
        question="Which classes inherit from BaseRepository?",
        category=Category.RELATIONSHIP,
        relevant_files=["app/users/repository.py", "app/core/base.py"],
        relevant_entities=["UserRepository", "BaseRepository"],
        expected_mentions=["UserRepository"],
    ),
    BenchmarkQuestion(
        id="mini-rel-3",
        question="What imports app/core/session.py?",
        category=Category.RELATIONSHIP,
        relevant_files=["app/auth/service.py", "app/main.py"],
        expected_mentions=["service.py"],
    ),
    BenchmarkQuestion(
        id="mini-rel-4",
        question="What does `login` call?",
        category=Category.RELATIONSHIP,
        relevant_files=["app/auth/service.py", "app/users/repository.py", "app/core/session.py"],
        relevant_entities=["find_by_email", "create", "hash_password"],
        expected_mentions=["find_by_email"],
    ),
    BenchmarkQuestion(
        id="mini-flow-1",
        question="What happens when a user submits a login request?",
        category=Category.FLOW,
        relevant_files=[
            "app/auth/routes.py",
            "app/auth/service.py",
            "app/users/repository.py",
            "app/core/session.py",
        ],
        relevant_entities=["login_route", "login", "find_by_email", "create"],
        expected_mentions=["login_route", "AuthService"],
    ),
    BenchmarkQuestion(
        id="mini-flow-2",
        question="How does a request get routed to a handler?",
        category=Category.FLOW,
        relevant_files=["app/main.py", "app/auth/routes.py"],
        relevant_entities=["handle", "login_route"],
        expected_mentions=["handle"],
    ),
    BenchmarkQuestion(
        id="mini-nav-1",
        question="Where would I change the code to add a password reset endpoint?",
        category=Category.NAVIGATION,
        relevant_files=["app/auth/routes.py", "app/auth/service.py", "app/main.py"],
        expected_mentions=["routes.py"],
    ),
    BenchmarkQuestion(
        id="mini-nav-2",
        question="Where is the entry point of this application?",
        category=Category.NAVIGATION,
        relevant_files=["app/main.py"],
        relevant_entities=["main", "handle"],
        expected_mentions=["main.py"],
    ),
]

# Ground truth below was checked by hand against psf/requests at the time of
# writing. It targets stable, long-lived modules rather than incidental files.
REQUESTS_QUESTIONS = [
    BenchmarkQuestion(
        id="req-repo-1",
        question="What does this repository do?",
        category=Category.REPOSITORY,
        relevant_files=["README.md"],
        expected_mentions=["HTTP"],
    ),
    BenchmarkQuestion(
        id="req-comp-1",
        question="Where is cookie handling implemented?",
        category=Category.COMPONENT,
        relevant_files=["src/requests/cookies.py"],
        relevant_entities=["RequestsCookieJar", "cookiejar_from_dict"],
        expected_mentions=["cookies.py"],
    ),
    BenchmarkQuestion(
        id="req-comp-2",
        question="How is authentication handled?",
        category=Category.COMPONENT,
        relevant_files=["src/requests/auth.py"],
        relevant_entities=["HTTPBasicAuth", "HTTPDigestAuth"],
        expected_mentions=["auth"],
    ),
    BenchmarkQuestion(
        id="req-comp-3",
        question="What does the Session class do?",
        category=Category.COMPONENT,
        relevant_files=["src/requests/sessions.py"],
        relevant_entities=["Session"],
        expected_mentions=["Session"],
    ),
    BenchmarkQuestion(
        id="req-rel-1",
        question="Which classes inherit from `BaseAdapter`?",
        category=Category.RELATIONSHIP,
        relevant_files=["src/requests/adapters.py"],
        relevant_entities=["HTTPAdapter", "BaseAdapter"],
        expected_mentions=["HTTPAdapter"],
    ),
    BenchmarkQuestion(
        id="req-rel-2",
        question="What calls `resolve_redirects`?",
        category=Category.RELATIONSHIP,
        relevant_files=["src/requests/sessions.py"],
        relevant_entities=["resolve_redirects", "send"],
        expected_mentions=["send"],
    ),
    BenchmarkQuestion(
        id="req-rel-3",
        question="What imports src/requests/models.py?",
        category=Category.RELATIONSHIP,
        relevant_files=["src/requests/sessions.py", "src/requests/adapters.py"],
        expected_mentions=["sessions.py"],
    ),
    BenchmarkQuestion(
        id="req-flow-1",
        question="What happens when you call requests.get?",
        category=Category.FLOW,
        relevant_files=["src/requests/api.py", "src/requests/sessions.py"],
        relevant_entities=["get", "request", "send"],
        expected_mentions=["api.py"],
    ),
    BenchmarkQuestion(
        id="req-flow-2",
        question="How does a request get prepared before it is sent?",
        category=Category.FLOW,
        relevant_files=["src/requests/models.py", "src/requests/sessions.py"],
        relevant_entities=["PreparedRequest", "prepare", "prepare_request"],
        expected_mentions=["PreparedRequest"],
    ),
    BenchmarkQuestion(
        id="req-nav-1",
        question="Where would I change the code to add a new retry policy?",
        category=Category.NAVIGATION,
        relevant_files=["src/requests/adapters.py"],
        relevant_entities=["HTTPAdapter"],
        expected_mentions=["adapters.py"],
    ),
]


SAMPLE_REPO_BENCHMARK = Benchmark(
    name="miniapp",
    local_path=FIXTURES_DIR / "sample_repo",
    url=None,
    questions=SAMPLE_REPO_QUESTIONS,
)

REQUESTS_BENCHMARK = Benchmark(
    name="requests",
    local_path=None,
    url="https://github.com/psf/requests",
    questions=REQUESTS_QUESTIONS,
)

BENCHMARKS = {
    benchmark.name: benchmark for benchmark in (SAMPLE_REPO_BENCHMARK, REQUESTS_BENCHMARK)
}
