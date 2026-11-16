import unittest

from notewrench.calibredupes.normalize import isbn10, isbn13, known_text, normalize_text


class NormalizeTests(unittest.TestCase):
    def test_isbn_checksums_and_bidirectional_equivalence(self):
        self.assertEqual(isbn13("0-306-40615-2"), "9780306406157")
        self.assertEqual(isbn10("9780306406157"), "0306406152")
        self.assertEqual(isbn13("0 8044 2957 x"), "9780804429573")
        self.assertEqual(isbn10("9780804429573"), "080442957X")
        self.assertEqual(isbn13("978–0–306–40615–7"), "9780306406157")

    def test_invalid_isbn_is_never_repaired_or_extracted(self):
        for value in ("9780306406158", "0306406153", "1234567890", "X306406152", "",
                      "ISBN 9780306406157", "9780306406157,9780131103627", "978.0306406157",
                      "９７８０３０６４０６１５７", "9780306406157/", "0306406152X", "9770306406158"):
            with self.subTest(value=value):
                self.assertIsNone(isbn13(value))
                self.assertIsNone(isbn10(value))

    def test_979_has_no_isbn10_equivalent(self):
        self.assertEqual(isbn13("9791090636071"), "9791090636071")
        self.assertIsNone(isbn10("9791090636071"))

    def test_unicode_normalization_preserves_accents(self):
        self.assertEqual(normalize_text("  ＣＡＦÉ—Maps! "), "café maps")
        self.assertEqual(normalize_text("Zoë"), normalize_text("Zoë"))
        self.assertEqual(normalize_text("Straße"), normalize_text("STRASSE"))
        self.assertNotEqual(normalize_text("Zoë"), normalize_text("Zoe"))
        self.assertEqual(known_text("Unknown"), "")
        self.assertEqual(normalize_text("中文 标题"), "中文 标题")


if __name__ == "__main__":
    unittest.main()
