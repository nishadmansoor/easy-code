"""Question analysis.

Decides whether a question is best answered from the semantic index, the
structural graph, or both, and pulls out the identifiers and file paths worth
looking up in the graph.
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum


class QuestionType(StrEnum):
    SEMANTIC = "semantic"
    STRUCTURAL = "structural"
    MIXED = "mixed"


class StructuralIntent(StrEnum):
    CALLERS = "callers"
    CALLEES = "callees"
    IMPORTERS = "importers"
    DEPENDENCIES = "dependencies"
    INHERITANCE = "inheritance"
    DEFINITION = "definition"
    FILE_CONTENTS = "file_contents"
    ARCHITECTURE = "architecture"
    ENTRY_POINT = "entry_point"


# Each pattern is anchored on wording that genuinely implies graph traversal.
STRUCTURAL_PATTERNS: list[tuple[StructuralIntent, str]] = [
    (StructuralIntent.CALLERS, r"\b(what|who|which)\b.{0,30}\bcalls?\b"),
    (StructuralIntent.CALLERS, r"\bcalled\s+(by|from)\b"),
    (StructuralIntent.CALLERS, r"\b(callers?|call\s+sites?|usages?|used\s+by)\b"),
    (StructuralIntent.CALLERS, r"\bwhere\s+is\s+.+\s+(called|invoked|used)\b"),
    (StructuralIntent.CALLEES, r"\bwhat\s+does\s+.+\s+call\b"),
    (StructuralIntent.CALLEES, r"\b(callees?|calls\s+out\s+to)\b"),
    (StructuralIntent.IMPORTERS, r"\b(what|which)\b.{0,30}\bimports?\b"),
    (StructuralIntent.IMPORTERS, r"\bimported\s+by\b"),
    (StructuralIntent.DEPENDENCIES, r"\bdepends?\s+on\b"),
    (StructuralIntent.DEPENDENCIES, r"\bdependenc(y|ies)\b"),
    (
        StructuralIntent.INHERITANCE,
        r"\b(inherit|inherits|inheritance|subclass|superclass|extends|base\s+class)\b",
    ),
    (StructuralIntent.DEFINITION, r"\bwhere\s+is\s+.+\s+(defined|declared|implemented)\b"),
    (StructuralIntent.DEFINITION, r"\bdefinition\s+of\b"),
    (StructuralIntent.FILE_CONTENTS, r"\bwhat('s| is)?\s+(in|inside)\s+\S+\.\w+\b"),
    (StructuralIntent.FILE_CONTENTS, r"\bcontents?\s+of\b"),
    (StructuralIntent.ARCHITECTURE, r"\b(architecture|structure|organiz|layout|modules?\s+fit)\b"),
    (StructuralIntent.ENTRY_POINT, r"\bentry\s?points?\b"),
    (StructuralIntent.ENTRY_POINT, r"\b(where|how)\s+does\s+.{0,20}\bstart\b"),
]

SEMANTIC_HINTS = [
    r"\bwhat\s+does\s+this\s+(repo|repository|project|codebase|application|app)\b",
    r"\bexplain\b",
    r"\bhow\s+does\b",
    r"\bwhy\b",
    r"\bpurpose\b",
    r"\boverview\b",
    r"\bsummar",
    r"\bdescribe\b",
    r"\bwhat\s+happens\s+when\b",
    r"\bwhere\s+would\s+i\b",
    r"\bhow\s+(would|do)\s+i\b",
    r"\bhandle[sd]?\b",
    r"\bresponsible\s+for\b",
]

FLOW_HINTS = [
    r"\bwhat\s+happens\s+when\b",
    r"\bflow\b",
    r"\bmove[s]?\s+through\b",
    r"\bend\s+to\s+end\b",
    r"\blife\s?cycle\b",
    r"\bpipeline\b",
    r"\binteract",
    r"\bwhere\s+would\s+i\s+(add|change|modify|implement)\b",
]

BACKTICKED = re.compile(r"`([^`]+)`")
CALL_SYNTAX = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)")
DOTTED = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b")
CAMEL_CASE = re.compile(r"\b([A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+)\b")
SNAKE_CASE = re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b")
FILE_PATH = re.compile(
    r"\b((?:[\w.-]+/)*[\w.-]+\.(?:py|js|jsx|ts|tsx|java|go|rs|rb|md|yaml|yml|json|toml))\b"
)

#: Words that look like identifiers but are ordinary English in a question.
IDENTIFIER_STOPWORDS = {
    "what_is",
    "how_to",
    "e_g",
    "i_e",
}


@dataclass
class QuestionAnalysis:
    question: str
    question_type: QuestionType
    intents: list[StructuralIntent] = field(default_factory=list)
    identifiers: list[str] = field(default_factory=list)
    file_paths: list[str] = field(default_factory=list)
    is_flow_question: bool = False

    @property
    def wants_graph(self) -> bool:
        return self.question_type in (QuestionType.STRUCTURAL, QuestionType.MIXED)

    @property
    def wants_vector(self) -> bool:
        return self.question_type in (QuestionType.SEMANTIC, QuestionType.MIXED)


def extract_identifiers(question: str) -> tuple[list[str], list[str]]:
    """Return (identifiers, file_paths) mentioned in the question."""
    file_paths: list[str] = []
    identifiers: list[str] = []

    def add_identifier(value: str) -> None:
        cleaned = value.strip().strip("()").strip()
        if (
            cleaned
            and cleaned.isidentifier()
            and cleaned.lower() not in IDENTIFIER_STOPWORDS
            and len(cleaned) > 1
            and cleaned not in identifiers
        ):
            identifiers.append(cleaned)

    for match in FILE_PATH.finditer(question):
        path = match.group(1)
        if path not in file_paths:
            file_paths.append(path)

    remainder = FILE_PATH.sub(" ", question)

    for match in BACKTICKED.finditer(remainder):
        token = match.group(1).strip()
        if "." in token and not token.endswith(")"):
            for part in token.split("."):
                add_identifier(part)
        add_identifier(token.removesuffix("()"))

    for match in CALL_SYNTAX.finditer(remainder):
        add_identifier(match.group(1))

    for match in DOTTED.finditer(remainder):
        add_identifier(match.group(1))
        add_identifier(match.group(2))

    for pattern in (CAMEL_CASE, SNAKE_CASE):
        for match in pattern.finditer(remainder):
            add_identifier(match.group(1))

    return identifiers, file_paths


def analyze_question(question: str) -> QuestionAnalysis:
    """Classify a question and extract the entities it refers to."""
    lowered = question.lower()

    intents: list[StructuralIntent] = []
    for intent, pattern in STRUCTURAL_PATTERNS:
        if re.search(pattern, lowered) and intent not in intents:
            intents.append(intent)

    identifiers, file_paths = extract_identifiers(question)
    is_flow = any(re.search(pattern, lowered) for pattern in FLOW_HINTS)
    has_semantic_hint = any(re.search(pattern, lowered) for pattern in SEMANTIC_HINTS)

    # A structural intent with a concrete target is a graph question. The same
    # intent phrased vaguely, or combined with "explain"/"how does", needs prose
    # as well and is therefore mixed.
    has_target = bool(identifiers or file_paths)

    if intents and has_target and not has_semantic_hint and not is_flow:
        question_type = QuestionType.STRUCTURAL
    elif intents or is_flow or (has_target and has_semantic_hint):
        question_type = QuestionType.MIXED
    else:
        question_type = QuestionType.SEMANTIC

    # Architecture and entry-point questions always need prose context too.
    if question_type == QuestionType.STRUCTURAL and (
        StructuralIntent.ARCHITECTURE in intents or StructuralIntent.ENTRY_POINT in intents
    ):
        question_type = QuestionType.MIXED

    return QuestionAnalysis(
        question=question,
        question_type=question_type,
        intents=intents,
        identifiers=identifiers,
        file_paths=file_paths,
        is_flow_question=is_flow,
    )
