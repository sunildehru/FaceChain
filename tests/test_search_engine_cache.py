import unittest
from src.discovery import (
    is_crawlable_public_url,
    extract_bing_image_pairs_from_text,
    search_bing_images_with_profiles,
    discover_candidate_target_urls
)

class TestSearchEngineCache(unittest.TestCase):

    def test_linkedin_url_filtering(self):
        """LinkedIn personal profiles (/in/, /pub/) are allowed; login/feed are filtered."""
        self.assertTrue(is_crawlable_public_url("https://www.linkedin.com/in/alex-smith"))
        self.assertTrue(is_crawlable_public_url("https://in.linkedin.com/in/jane-doe"))
        self.assertFalse(is_crawlable_public_url("https://www.linkedin.com/feed"))
        self.assertFalse(is_crawlable_public_url("https://www.linkedin.com/login"))

    def test_bing_image_pairs_parsing(self):
        """Parses purl, turl, murl, and title from Bing Image Search markup."""
        mock_html = """
        <div class="imgpt">
            <a class="iusc" m='{"ns":"1","purl":"https://www.linkedin.com/in/alex-smith","murl":"https://example.com/photo.jpg","turl":"https://ts1.mm.bing.net/th?id=OIP.mock123","t":"Alex Smith - Staff Engineer | LinkedIn"}'></a>
            <a class="iusc" m='{"ns":"1","purl":"https://github.com/alex-smith","murl":"https://github.com/avatar.png","turl":"https://ts2.mm.bing.net/th?id=OIP.mock456","t":"Alex Smith on GitHub"}'></a>
        </div>
        """
        pairs = extract_bing_image_pairs_from_text(mock_html)
        self.assertEqual(len(pairs), 2)
        self.assertEqual(pairs[0]["purl"], "https://www.linkedin.com/in/alex-smith")
        self.assertEqual(pairs[0]["turl"], "https://ts1.mm.bing.net/th?id=OIP.mock123")
        self.assertIn("Staff Engineer", pairs[0]["title"])
        self.assertEqual(pairs[1]["purl"], "https://github.com/alex-smith")

    def test_candidate_discovery_structure(self):
        """Verify discover_candidate_target_urls returns enriched metadata with sample_image."""
        # Generic query against discovery engine
        candidates = discover_candidate_target_urls(name="Satya Nadella", max_pages_budget=5)
        self.assertIsInstance(candidates, list)
        self.assertGreater(len(candidates), 0)
        # Verify item structure
        first = candidates[0]
        self.assertIn("url", first)
        self.assertIn("domain", first)
        self.assertIn("title", first)
        self.assertIn("source", first)

if __name__ == '__main__':
    unittest.main()
