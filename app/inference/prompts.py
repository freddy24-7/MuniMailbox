from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
_USER_MARKER = "---USER---"


@dataclass(frozen=True)
class Prompt:
    system: str
    user: str


def render_prompt(name: str, variables: Mapping[str, str]) -> Prompt:
    """Load `prompts/<name>.md` and fill its `$variables`.

    The file is split into system and user parts *before* substitution, so email text
    containing the marker cannot move itself into the system part.
    """
    template = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    system, user = template.split(_USER_MARKER, maxsplit=1)
    return Prompt(
        system=Template(system).substitute(variables).strip(),
        user=Template(user).substitute(variables).strip(),
    )
