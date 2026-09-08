import datetime
import io
import unittest

from pylabrobot.lib import toml as toml_parser


class TOMLParserTests(unittest.TestCase):
  def test_common_values_and_comments(self):
    result = toml_parser.loads(
      """
      title = "TOML # example" # an actual comment
      enabled = true
      count = 1_000
      ratio = 1.5
      hex = 0xCA_FE
      names = ["Ada", "Grace",]
      point = { x = 2, y = 3 }
      birthday = 1815-12-10
      """
    )

    self.assertEqual(result["title"], "TOML # example")
    self.assertIs(result["enabled"], True)
    self.assertEqual(result["count"], 1000)
    self.assertEqual(result["ratio"], 1.5)
    self.assertEqual(result["hex"], 0xCAFE)
    self.assertEqual(result["names"], ["Ada", "Grace"])
    self.assertEqual(result["point"], {"x": 2, "y": 3})
    self.assertEqual(result["birthday"], datetime.date(1815, 12, 10))

  def test_tables_dotted_and_quoted_keys(self):
    result = toml_parser.loads(
      """
      [database]
      server = "localhost"
      connection.port = 5432

      [database.credentials]
      "user.name" = 'admin'
      """
    )

    self.assertEqual(
      result,
      {
        "database": {
          "server": "localhost",
          "connection": {"port": 5432},
          "credentials": {"user.name": "admin"},
        }
      },
    )

  def test_multiline_array_and_binary_load(self):
    source = b"values = [\n  1, # first\n  2,\n]\n"
    self.assertEqual(toml_parser.load(io.BytesIO(source)), {"values": [1, 2]})

  def test_escapes_and_datetime(self):
    result = toml_parser.loads('message = "hello\\nworld\\u0021"\nwhen = 1979-05-27T07:32:00Z')
    self.assertEqual(result["message"], "hello\nworld!")
    self.assertEqual(
      result["when"],
      datetime.datetime(1979, 5, 27, 7, 32, tzinfo=datetime.timezone.utc),
    )

  def test_invalid_or_unsupported_input_has_line_number(self):
    cases = (
      "answer = maybe",
      "answer = 1\nanswer = 2",
      "[answer]\nvalue = 1\n[answer]",
      "[[products]]\nname = 'hammer'",
    )
    for source in cases:
      with self.subTest(source=source):
        with self.assertRaisesRegex(toml_parser.TOMLDecodeError, r"line \d+:"):
          toml_parser.loads(source)


if __name__ == "__main__":
  unittest.main()
