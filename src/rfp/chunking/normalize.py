import re
import unicodedata

from rfp.schemas.chunks import ChunkConfig


def normalize(text: str, config: ChunkConfig) -> str:
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    # Only a discretionary soft hyphen is removable. ASCII hyphens may be part/model IDs.
    text = re.sub(r"(?<=[A-Za-z])\u00ad[ \t]*\n[ \t]*(?=[a-z])", "", text)
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in text.split("\n")]
    if config.join_wrapped_lines:
        joined: list[str] = []
        for line in lines:
            if (
                joined
                and line
                and re.match(r"^[a-z]", line)
                and re.search(r"[A-Za-z]$", joined[-1])
                and not re.match(r"^(?:[-*•]|\d+[.)])\s", joined[-1])
            ):
                joined[-1] += " " + line
            else:
                joined.append(line)
        lines = joined
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
