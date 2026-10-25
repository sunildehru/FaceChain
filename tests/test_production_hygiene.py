import unittest
import os
from PIL import Image
from starlette.requests import Request
from starlette.responses import Response
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api import (
    app,
    favicon_ico,
    robots_txt,
    sitemap_xml,
    site_webmanifest,
    healthz,
    get_styles,
    get_app_js,
    add_security_headers_middleware,
    custom_http_exception_handler
)
import asyncio

class TestFaceChainProductionHygiene(unittest.TestCase):
    def test_brand_assets_files_exist_and_valid(self):
        """All production branding assets exist with verified image dimensions."""
        self.assertTrue(os.path.exists("frontend/favicon.svg"))
        self.assertTrue(os.path.exists("frontend/favicon.ico"))
        self.assertTrue(os.path.exists("frontend/apple-touch-icon.png"))
        self.assertTrue(os.path.exists("frontend/og-image.png"))

        # Check apple-touch-icon dimensions (180x180)
        with Image.open("frontend/apple-touch-icon.png") as img:
            self.assertEqual(img.size, (180, 180))

        # Check Open Graph image dimensions (1200x630)
        with Image.open("frontend/og-image.png") as img:
            self.assertEqual(img.size, (1200, 630))

    def test_favicon_ico_route(self):
        """Favicon route serves verified image/x-icon."""
        resp = asyncio.run(favicon_ico())
        self.assertEqual(resp.media_type, "image/x-icon")
        self.assertTrue(os.path.exists(resp.path))

    def test_robots_txt_route(self):
        """Robots.txt route returns crawler policy with temp/ exclusions."""
        resp = asyncio.run(robots_txt())
        self.assertEqual(resp.media_type, "text/plain")
        if hasattr(resp, 'path'):
            with open(resp.path, 'r', encoding='utf-8') as f:
                content = f.read()
        else:
            content = resp.body.decode('utf-8')
        self.assertIn("User-agent: *", content)
        self.assertIn("Disallow: /temp/", content)

    def test_sitemap_xml_route(self):
        """Sitemap.xml route returns valid XML sitemap."""
        resp = asyncio.run(sitemap_xml())
        self.assertEqual(resp.media_type, "application/xml")
        self.assertTrue(os.path.exists(resp.path))

    def test_site_webmanifest_route(self):
        """Webmanifest route returns valid PWA metadata."""
        resp = asyncio.run(site_webmanifest())
        self.assertEqual(resp.media_type, "application/manifest+json")
        self.assertTrue(os.path.exists(resp.path))

    def test_styles_css_route(self):
        """Styles route serves valid text/css stylesheet."""
        resp = asyncio.run(get_styles())
        self.assertEqual(resp.media_type, "text/css")
        self.assertTrue(os.path.exists(resp.path))

    def test_app_js_route(self):
        """Client script route serves application/javascript."""
        resp = asyncio.run(get_app_js())
        self.assertEqual(resp.media_type, "application/javascript")
        self.assertTrue(os.path.exists(resp.path))

    def test_healthz_route(self):
        """Healthz route returns operational JSON telemetry."""
        data = asyncio.run(healthz())
        self.assertEqual(data["status"], "HEALTHY")
        self.assertEqual(data["protocol"], "FaceChain Biometric Registry")
        self.assertIn("timestamp", data)

    def test_custom_404_html_for_browser_requests(self):
        """Browser navigation requesting text/html receives custom Bauhaus 404 page."""
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/missing-coordinate",
            "headers": [(b"accept", b"text/html,application/xhtml+xml")]
        }
        req = Request(scope)
        exc = StarletteHTTPException(status_code=404, detail="Not Found")
        resp = asyncio.run(custom_http_exception_handler(req, exc))
        self.assertEqual(resp.status_code, 404)
        self.assertIsInstance(resp, HTMLResponse)
        self.assertIn("Biometric Coordinate Not Found", resp.body.decode('utf-8'))

    def test_custom_404_json_for_api_requests(self):
        """API client requesting application/json receives structured JSON error."""
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/missing-route",
            "headers": [(b"accept", b"application/json")]
        }
        req = Request(scope)
        exc = StarletteHTTPException(status_code=404, detail="Custom route missing")
        resp = asyncio.run(custom_http_exception_handler(req, exc))
        self.assertEqual(resp.status_code, 404)
        self.assertIsInstance(resp, JSONResponse)
        import json
        data = json.loads(resp.body.decode('utf-8'))
        self.assertEqual(data["status"], 404)
        self.assertEqual(data["detail"], "Custom route missing")

    def test_security_headers_middleware(self):
        """Defensive security headers are injected into every response."""
        scope = {"type": "http", "method": "GET", "path": "/"}
        req = Request(scope)
        async def mock_call_next(_):
            return Response(content="OK")
        resp = asyncio.run(add_security_headers_middleware(req, mock_call_next))
        self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(resp.headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(resp.headers["Referrer-Policy"], "strict-origin-when-cross-origin")
        self.assertIn("camera=(self)", resp.headers["Permissions-Policy"])

    def test_frontend_index_title_and_og_tags_present(self):
        """Index.html contains valid UTF-8 title, Open Graph tags, and Twitter cards."""
        with open("frontend/index.html", "r", encoding="utf-8") as f:
            html = f.read()

        # Check clean title without replacement characters
        self.assertNotIn("\ufffd", html)
        self.assertIn("<title>FaceChain ✦ Cryptographic Biometric Registry", html)

        # Check Open Graph tags
        self.assertIn('<meta property="og:image" content="/static/og-image.png">', html)
        self.assertIn('<meta property="og:title"', html)

        # Check Twitter tags
        self.assertIn('<meta name="twitter:card" content="summary_large_image">', html)

        # Check Manifest & Theme Color
        self.assertIn('<link rel="manifest" href="/site.webmanifest">', html)
        self.assertIn('<meta name="theme-color" content="#f3ecdd">', html)

        # Check Keyboard help modal
        self.assertIn('id="keyboardHelpModal"', html)
        self.assertIn('id="offlineNotice"', html)

if __name__ == "__main__":
    unittest.main()
