"""Documentation parser.

Markdown files are split at heading boundaries so that each chunk is a
self-contained section rather than an arbitrary window of text.
"""

import re
from pathlib import Path

from backend.app.models.entities import CodeEntity, EntityType, ParsedRepository

LANGUAGE = "markdown"
HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def parse_markdown_file(file_path: Path, repo_id: str, rel_path: str) -> ParsedRepository:
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ParsedRepository()

    lines = source.splitlines()
    if not lines:
        return ParsedRepository()

    # (title, start_line, level) for every heading, plus a preamble section when
    # the file starts with prose.
    headings: list[tuple[str, int, int]] = []
    in_fence = False
    for index, line in enumerate(lines, start=1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = HEADING.match(line)
        if match:
            headings.append((match.group(2), index, len(match.group(1))))

    sections: list[tuple[str, int, int]] = []
    if not headings:
        sections.append((Path(rel_path).name, 1, len(lines)))
    else:
        if headings[0][1] > 1:
            sections.append((f"{Path(rel_path).name} (intro)", 1, headings[0][1] - 1))
        for position, (title, start, _level) in enumerate(headings):
            end = headings[position + 1][1] - 1 if position + 1 < len(headings) else len(lines)
            sections.append((title, start, end))

    result = ParsedRepository()
    for title, start, end in sections:
        content = "\n".join(lines[start - 1 : end]).strip()
        if len(content) < 40:
            continue
        result.entities.append(
            CodeEntity(
                repository_id=repo_id,
                file_path=rel_path,
                language=LANGUAGE,
                entity_type=EntityType.DOC_SECTION,
                entity_name=title,
                start_line=start,
                end_line=end,
                source_code=content,
            )
        )
    return result
