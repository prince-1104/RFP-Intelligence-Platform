from typing import Literal

from bs4 import BeautifulSoup, Tag

from rfp.ingestion.metadata import normalize_text
from rfp.ingestion.options import ParserOptions
from rfp.schemas.parsed import ParsedBlock, ParsedPage, ParsedTable, SourceLocator

LABEL_DEPTH = 3
"""How many ancestors up a block may sit below the element its label belongs to."""


def _is_label(tag: Tag) -> bool:
    return tag.name in {"label", "dt"} or any(
        str(name).endswith("label") for name in tag.get("class") or []
    )


def field_label(element: Tag) -> str | None:
    """The explicit label of the field holding ``element``: a label element that is the
    immediate previous sibling of the element or of one of its nearest ancestors, as in
    ``<span class="field-label">Closing Date</span><div><p>value</p></div>`` or
    ``<dt>Due</dt><dd>value</dd>``. Text that merely sits nearby is never used."""
    if element.find_parent(_is_label) is not None:
        return None
    node: Tag | None = element
    for _ in range(LABEL_DEPTH):
        if node is None or node.name in {"body", "html"}:
            return None
        previous = node.find_previous_sibling()
        if isinstance(previous, Tag) and _is_label(previous):
            label = normalize_text(previous.get_text(" ")).rstrip(":").strip()
            return label or None
        if previous is not None:
            return None
        parent = node.parent
        node = parent if isinstance(parent, Tag) else None
    return None


def parse_html(
    data: bytes, relative_path: str, options: ParserOptions
) -> tuple[list[ParsedPage], str | None, list[str]]:
    soup = BeautifulSoup(data, "lxml")
    if sum(1 for _ in soup.descendants) > options.html_max_nodes:
        return [], None, ["html_node_limit"]
    title = normalize_text(soup.title.get_text(" ")) if soup.title else None
    for element in soup.find_all(["script", "style", "template", "noscript"]):
        element.decompose()
    for element in list(soup.find_all(True)):
        if element.parent is not None and (
            element.has_attr("hidden") or element.get("aria-hidden") == "true"
        ):
            element.decompose()
    root = soup.body or soup
    blocks: list[ParsedBlock] = []
    tables: list[ParsedTable] = []
    heading = None
    for index, element in enumerate(root.find_all(True), 1):
        name = element.name
        if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            heading = normalize_text(element.get_text(" "))
        anchor = element.get("id")
        if not anchor:
            parent = element.find_parent(id=True)
            anchor = parent.get("id") if isinstance(parent, Tag) else None
        locator = SourceLocator(
            relative_path=relative_path,
            element_id=str(anchor) if anchor else None,
            heading=heading,
            element_index=index,
        )
        if name == "table":
            table_index = len(tables) + 1
            locator.table_index = table_index
            rows: list[list[str | None]] = []
            headers = None
            warnings = []
            for row in element.find_all("tr"):
                if row.find_parent("table") is not element:
                    continue
                cells = row.find_all(["th", "td"], recursive=False)
                values = [normalize_text(cell.get_text(" ")) for cell in cells]
                if not values:
                    continue
                if headers is None and all(cell.name == "th" for cell in cells):
                    headers = values
                if any(
                    cell.get("rowspan", "1") != "1" or cell.get("colspan", "1") != "1"
                    for cell in cells
                ):
                    warnings.append("merged_cells_not_expanded")
                row_values: list[str | None] = list(values)
                rows.append(row_values)
            if headers is None:
                warnings.append("ambiguous_table_headers")
            tables.append(
                ParsedTable(
                    table_id=f"html-table-{table_index}",
                    table_index=table_index,
                    headers=headers,
                    rows=rows,
                    source_locator=locator,
                    extraction_method="beautifulsoup",
                    warnings=sorted(set(warnings)),
                )
            )
        if name not in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "a"}:
            continue
        if element.find_parent("table"):
            continue
        # Keep leaf list/paragraph content once; links are separate provenance records.
        text = normalize_text(element.get_text(" "))
        if not text:
            continue
        label = None if name.startswith("h") else field_label(element)
        if label and not text.casefold().startswith(label.casefold()):
            text = f"{label}: {text}"
        kind: Literal["heading", "paragraph", "list_item", "link"] = (
            "heading"
            if name.startswith("h")
            else ("list_item" if name == "li" else "link" if name == "a" else "paragraph")
        )
        href = element.get("href") if name == "a" else None
        blocks.append(
            ParsedBlock(
                block_id=f"html-element-{index}",
                block_type=kind,
                text=text,
                section_heading=heading,
                source_locator=locator,
                link_url=str(href) if href is not None else None,
            )
        )
    # Preserve visible text from unstructured div/header/nav content too, including table cells.
    text = normalize_text(root.get_text("\n", strip=True))
    warnings = [] if text else ["no_readable_content"]
    return [ParsedPage(text=text, blocks=blocks, tables=tables)], title or None, warnings
