import unittest
from pydantic import BaseModel, Field
from ripple_edge.tools import inline


class Inner(BaseModel):
    x: float


class Report(BaseModel):
    title: str = Field(min_length=1)
    content: str
    at: Inner


class InlineTest(unittest.TestCase):
    def test_keeps_a_field_named_title_and_drops_title_metadata(self):
        schema = inline(Report.model_json_schema())
        self.assertEqual(set(schema['properties']), {'title', 'content', 'at'})
        self.assertIn('title', schema['required'])
        self.assertNotIn('title', schema)
        self.assertNotIn('title', schema['properties']['title'])
        self.assertEqual(schema['properties']['at']['properties'], {'x': {'type': 'number'}})


if __name__ == '__main__':
    unittest.main()
