import itertools
import random
import re
import time

import pytest

from packages.core.markdown_links import MarkdownLinks


@pytest.mark.parametrize("attachments", [False, True])
def test_legacy_link_semantics_including_nested_openings_and_escapes(attachments):
    label = r"\[(?:\\.|[^\]\\\r\n])*\]"
    href = (r"/api/(?:attachments/(?:ref/|editor/|[^)\s]+)|fs/read\?[^)\s]+)"
            if attachments else r"[^)\s]+")
    legacy = re.compile(label + r"\((?P<href>" + href + r")\)")
    scanner = MarkdownLinks(attachments_only=attachments)
    samples = ["".join(x) for n in range(5) for x in itertools.product("[]()\\a\n", repeat=n)]
    rng = random.Random(15)
    samples += ["".join(rng.choice(["[", "]", "(", ")", "\\", "\r", "\n", "a", " ",
                                    "/api/attachments/a", "/api/fs/read?x"])
                        for _ in range(30)) for _ in range(3000)]
    for text in samples:
        old = [(m.start(), m.end(), m.group(), m.group("href")) for m in legacy.finditer(text)]
        new = [(m.start(), m.end(), m.group(), m.group("href")) for m in scanner.finditer(text)]
        assert new == old, repr(text)


@pytest.mark.parametrize("text", ["[" * 100_000, "[x](" * 25_000 + "unfinished "],
                         ids=["unclosed-labels", "unclosed-destinations"])
def test_unfinished_labels_and_destinations_do_not_rescan_suffixes(text):
    start = time.perf_counter()
    for attachments in [False, True]:
        assert list(MarkdownLinks(attachments_only=attachments).finditer(text)) == []
    # The old regex takes many seconds/minutes on these inputs. A generous
    # bound avoids turning this into a machine-speed microbenchmark.
    assert time.perf_counter() - start < 2


def test_substitution_preserves_unmatched_prose_and_match_locations():
    assert MarkdownLinks().sub(lambda m: m.group("label") + "(" + m.group("href").upper() + ")",
                               "before [a](abc) between [b](def) after") == (
                                   "before [a](ABC) between [b](DEF) after")
