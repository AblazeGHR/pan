"""Linear scan of the legacy inline-link syntax used by Pan wire projections.

Repeated unmatched '[' characters made the old unanchored regex retry the
same suffix quadratically. Preserve that syntax (including nested opening
brackets and backslash escapes), without parsing or rewriting provider text.
"""
from dataclasses import dataclass
import re


_HREF_END = re.compile(r"[)\s]")
_ATTACHMENT_HREF = re.compile(
    r"/api/(?:attachments/(?:ref/|editor/|[^)\s]+)|fs/read\?[^)\s]+)\Z")


@dataclass(frozen=True)
class LinkMatch:
    text: str
    first: int
    last: int
    close_label: int

    def group(self, name=0):
        if name == 0:
            return self.text[self.first:self.last]
        if name == "label":
            return self.text[self.first:self.close_label + 1]
        if name == "href":
            return self.text[self.close_label + 2:self.last - 1]
        raise IndexError(name)

    def start(self):
        return self.first

    def end(self):
        return self.last


class MarkdownLinks:
    def __init__(self, *, attachments_only=False):
        self.attachments_only = attachments_only

    def finditer(self, text):
        cursor = 0
        delimiter = None
        size = len(text)
        while cursor < size:
            first = text.find("[", cursor)
            if first < 0:
                return
            end = first + 1
            while end < size:
                char = text[end]
                if char == "\\" and end + 1 < size and text[end + 1] != "\n":
                    end += 2
                elif char in "]\r\n\\":
                    break
                else:
                    end += 1
            if end >= size:
                return
            cursor = end + 1
            if text[end] != "]" or end + 1 >= size or text[end + 1] != "(":
                continue
            href_start = end + 2
            # All candidate starts increase. Reuse the next delimiter instead
            # of rescanning long invalid href suffixes for every candidate.
            if delimiter is None or delimiter.start() < href_start:
                delimiter = _HREF_END.search(text, href_start)
            if delimiter is None:
                return
            last = delimiter.start()
            if last == href_start or text[last] != ")":
                continue
            if self.attachments_only and not _ATTACHMENT_HREF.fullmatch(text[href_start:last]):
                continue
            yield LinkMatch(text, first, last + 1, end)
            cursor = last + 1

    def sub(self, replace, text):
        chunks = []
        previous = 0
        for match in self.finditer(text):
            chunks.extend((text[previous:match.start()], replace(match)))
            previous = match.end()
        chunks.append(text[previous:])
        return "".join(chunks)
