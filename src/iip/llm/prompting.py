"""Versioned prompts.

Each prompt is a file named ``<name>_v<N>.md`` with a ``# system`` and a
``# user`` section. Placeholders use ``$name`` (``string.Template``). A prompt that
has been used in production is never edited in place: changes go in a new
version file, and every stored analysis records which version produced it, so
results can always be traced back to the exact prompt.
"""

from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from string import Template


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    system: str
    user: Template

    @property
    def id(self) -> str:
        return f"{self.name}_{self.version}"

    def render(self, **values: str) -> str:
        # substitute (not safe_substitute): a missing value is a bug, fail loudly.
        return self.user.substitute(**values)


@cache
def load_prompt(name: str, version: str) -> Prompt:
    raw = files("iip.llm").joinpath(f"prompts/{name}_{version}.md").read_text(encoding="utf-8")
    sections: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line in raw.splitlines():
        if line.startswith("# ") and line[2:].strip() in {"system", "user"}:
            if current is not None:
                sections[current] = "\n".join(lines).strip()
            current, lines = line[2:].strip(), []
        else:
            lines.append(line)
    if current is not None:
        sections[current] = "\n".join(lines).strip()
    missing = {"system", "user"} - sections.keys()
    if missing:
        raise ValueError(f"prompt {name}_{version} is missing sections: {sorted(missing)}")
    return Prompt(
        name=name, version=version, system=sections["system"], user=Template(sections["user"])
    )
