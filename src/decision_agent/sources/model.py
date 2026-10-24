from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

BlockKind = Literal["heading", "bullet", "para"]


class Block(BaseModel):
    """One text unit of a document: a heading, a list item, or a paragraph."""

    text: str
    kind: BlockKind = "para"
    level: int = 0
    checked: bool | None = None
    location: str = ""


class Table(BaseModel):
    """A table with a header row. Cell values are plain strings."""

    headers: list[str]
    rows: list[list[str]]
    first_row_location: int = 2


class Section(BaseModel):
    """A sheet, slide, page group, or the whole body of a text document."""

    name: str = ""
    blocks: list[Block] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)


class ParsedDocument(BaseModel):
    """Result of reading one file. Everything downstream works on this model only."""

    path: str
    fmt: str
    sections: list[Section] = Field(default_factory=list)
    meta: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    def all_blocks(self) -> list[Block]:
        return [b for s in self.sections for b in s.blocks]

    def is_empty(self) -> bool:
        return not any(s.blocks or s.tables for s in self.sections)
