import unittest

from maneki.markdown import CODE_CLOSE, CODE_OPEN, to_markup, to_plain


def code(text):
    return f"{CODE_OPEN}{text}{CODE_CLOSE}"


class MarkupTest(unittest.TestCase):
    def test_emphasis_and_code(self):
        self.assertEqual(to_markup("**All** tests *pass* in `make check`"), f"<b>All</b> tests <i>pass</i> in {code('make check')}")
        self.assertEqual(to_markup("__bold__ and _soft_ and ~~gone~~"), "<b>bold</b> and <i>soft</i> and <s>gone</s>")

    def test_code_spans_keep_their_markers(self):
        self.assertEqual(to_markup("run `a **b** c`"), f"run {code('a **b** c')}")

    def test_identifiers_are_not_italic(self):
        self.assertEqual(to_markup("edited snake_case_file.py and 2*3*4"), "edited snake_case_file.py and 2*3*4")

    def test_blocks(self):
        text = "# Summary\n\n- fixed the race\n  - nested\n1. first\n> careful\n---\n```\nmake **check**\n```"
        expected = f"<b>Summary</b>\n\n• fixed the race\n  • nested\n1. first\n▎ <i>careful</i>\n──────\n{code('make **check**')}"
        self.assertEqual(to_markup(text), expected)

    def test_links_show_their_text(self):
        self.assertEqual(to_markup("see [the docs](https://example.com/a_b)"), "see <u>the docs</u>")

    def test_agent_text_cannot_inject_markup(self):
        self.assertEqual(to_markup('<span foreground="red">x</span> & **y**'), '&lt;span foreground="red"&gt;x&lt;/span&gt; &amp; <b>y</b>')

    def test_plain_drops_the_markers(self):
        self.assertEqual(to_plain("## **Done**: `make check` passes & <ok>"), "Done: make check passes & <ok>")

    def test_unpaired_markers_stay(self):
        self.assertEqual(to_markup("a ** b and * c"), "a ** b and * c")


if __name__ == "__main__":
    unittest.main()
