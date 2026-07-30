# SPDX-License-Identifier: MIT
"""A small HTML query helper for the render tests.

The render assertions need structure, not string matching: "the search input sits
inside the page actions" is a question about ancestry, and a substring check on the
markup cannot answer it. This builds a tree from the rendered markup with the
standard library only, so the module's test suite keeps its single dependency
(pytest) and stays runnable anywhere.
"""
from __future__ import annotations

from html.parser import HTMLParser

# Tags that never carry children, so they are never pushed onto the open-element stack.
_VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


class Node:
    """One element. `children` holds child Nodes and raw text strings."""

    def __init__(self, tag: str, attrs: dict, parent: "Node | None" = None) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list = []
        self.parent = parent

    @property
    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    @property
    def id(self) -> str:
        return self.attrs.get("id") or ""

    def text(self) -> str:
        parts: list[str] = []

        def walk(node: "Node") -> None:
            for child in node.children:
                if isinstance(child, str):
                    parts.append(child)
                else:
                    walk(child)

        walk(self)
        return " ".join(" ".join(parts).split())

    def elements(self):
        """Every descendant element, document order, self excluded."""
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.elements()

    def find_all(self, tag: str | None = None, cls: str | None = None,
                 id: str | None = None, **attrs) -> list["Node"]:
        """Elements matching every given filter.

        Attribute filters take a value to match, or `...` to mean "carries this
        attribute at all", which is how the tests ask for the calendar's day cells:
        they are identified by `data-day`, not by a class name.
        """
        found = []
        for node in self.elements():
            if tag is not None and node.tag != tag:
                continue
            if cls is not None and cls not in node.classes:
                continue
            if id is not None and node.id != id:
                continue
            if any(not _attr_matches(node, k.replace("_", "-"), v) for k, v in attrs.items()):
                continue
            found.append(node)
        return found

    def find(self, tag: str | None = None, cls: str | None = None,
             id: str | None = None, **attrs) -> "Node | None":
        hits = self.find_all(tag, cls, id, **attrs)
        return hits[0] if hits else None

    def ancestors(self) -> list["Node"]:
        out, node = [], self.parent
        while node is not None:
            out.append(node)
            node = node.parent
        return out

    def has_ancestor(self, cls: str | None = None, id: str | None = None) -> bool:
        return any((cls is None or cls in a.classes) and (id is None or a.id == id)
                   for a in self.ancestors())

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.tag} class={sorted(self.classes)} id={self.id!r}>"


def _attr_matches(node: Node, name: str, want) -> bool:
    if want is ...:
        return name in node.attrs
    return node.attrs.get(name) == want


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document", {})
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs) -> None:
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self._stack[-1])
        self._stack[-1].children.append(node)
        if tag not in _VOID:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs) -> None:
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self._stack[-1])
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        for depth in range(len(self._stack) - 1, 0, -1):
            if self._stack[depth].tag == tag:
                del self._stack[depth:]
                return

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._stack[-1].children.append(data)


def parse(markup: str) -> Node:
    """Parse markup into a queryable tree rooted at a synthetic document node."""
    builder = _Builder()
    builder.feed(markup)
    builder.close()
    return builder.root


def classes_in(markup: str) -> set[str]:
    """Every class token that appears anywhere in the markup."""
    tokens: set[str] = set()
    for node in parse(markup).elements():
        tokens |= node.classes
    return tokens
