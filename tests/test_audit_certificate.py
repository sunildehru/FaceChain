import unittest
import io
import asyncio
from PIL import Image
from fastapi import HTTPException

from src.security import (
    validate_and_sanitize_image_bytes,
    sanitize_text_for_export,
    sanitize_record_for_export,
    MAX_UPLOAD_SIZE_BYTES
)
from src.certificate import (
    generate_audit_certificate_pdf,
    generate_audit_certificate_jsonld
)
from src.api import export_certificate_pdf, export_certificate_jsonld

class TestFaceChainAuditCertificateAndSecurity(unittest.TestCase):
    def setUp(self):
        # Create standard test JPEG
        img = Image.new('RGB', (120, 120), color='teal')
        buf = io.BytesIO()
        img.save(buf, format='JPEG')
        self.sample_jpeg_bytes = buf.getvalue()

        self.mock_record = {
            "post_id": "web_987654321",
            "caption": "Mark Zuckerberg",
            "username": "en.wikipedia.org",
            "post_url": "https://en.wikipedia.org/wiki/Mark_Zuckerberg",
            "image_sha256": "4b227777d4dd1fc61c6f884f48641d02b4d121d3fd328cb08b5531fcacdabf8a",
            "similarity_score": 0.9982,
            "distance": 0.058,
            "matches_count": 27,
            "unique_matches_count": 20,
            "duplicates_count": 7,
            "images_scanned": 16,
            "faces_detected": 27,
            "ipfs_cid": "QmYwAPJzv5CZsnA625s3Xf2nemtYgPpHdWEz79ojWnPbdG"
        }

        self.mock_receipt = {
            "contract_address": "0xF2E246BB76DF876Cef8b38ae84130F4F55De395b",
            "tx_hash": "0x3f5c71b68bb568465b05a764722888cf3e1d6706de813ee8ab522a4ef69d7b42",
            "block_number": 1,
            "submitter": "0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf",
            "gas_used": 44210
        }

        self.mock_forensics = {
            "liveness_score": 0.9942,
            "deepfake_risk": "LOW (AUTHENTIC PHOTOMETRIC TEXTURE)",
            "classification": "AUTHENTIC_CAMERA_CAPTURE",
            "laplacian_edge_variance": 248.5,
            "high_frequency_ratio": 0.4128
        }

    def test_upload_security_valid_image(self):
        """Valid JPEG within size limit is accepted and sanitized."""
        clean_name, data = validate_and_sanitize_image_bytes(
            self.sample_jpeg_bytes,
            "../../malicious_traversal/avatar.jpg"
        )
        self.assertEqual(clean_name, "avatar.jpg")
        self.assertEqual(len(data), len(self.sample_jpeg_bytes))

    def test_upload_security_rejects_executable(self):
        """Files containing executable PE headers (MZ) are strictly blocked."""
        fake_pe = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff"
        with self.assertRaises(HTTPException) as ctx:
            validate_and_sanitize_image_bytes(fake_pe, "virus.jpg")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("executable", ctx.exception.detail)

    def test_upload_security_rejects_shell_script(self):
        """Files containing Unix shell scripts (#!) are strictly blocked."""
        fake_script = b"#!/bin/bash\nrm -rf /\n"
        with self.assertRaises(HTTPException) as ctx:
            validate_and_sanitize_image_bytes(fake_script, "script.png")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_upload_security_rejects_oversized_payload(self):
        """Uploads exceeding the maximum permissible boundary are rejected."""
        fake_huge = b"\xff\xd8\xff" + b"\x00" * (MAX_UPLOAD_SIZE_BYTES + 1024)
        with self.assertRaises(HTTPException) as ctx:
            validate_and_sanitize_image_bytes(fake_huge, "huge.jpg")
        self.assertEqual(ctx.exception.status_code, 413)

    def test_security_sanitization_removes_personal_paths(self):
        """Sanitizer scrubs local machine drive letters, usernames, and temp paths."""
        dirty_path = r"C:\Users\SecretDeveloper\AppData\Local\Temp\capture.jpg"
        clean = sanitize_text_for_export(dirty_path)
        self.assertNotIn("Users", clean)
        self.assertNotIn("SecretDeveloper", clean)
        self.assertNotIn("C:", clean)
        self.assertEqual(clean, "capture.jpg")

    def test_generate_pdf_certificate_zero_leaks_and_valid_format(self):
        """PDF certificate is generated in-memory, fits on 1 page, and contains zero host leaks."""
        pdf_bytes = generate_audit_certificate_pdf(
            self.mock_record,
            self.mock_receipt,
            self.mock_forensics
        )
        self.assertGreater(len(pdf_bytes), 1500)
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))

        # Verify zero host leaks in raw binary payload
        prohibited = [b"sunil", b"dehru", b"c:\\users", b"d:\\projects", b"/users/"]
        lower_bytes = pdf_bytes.lower()
        for forbidden in prohibited:
            self.assertNotIn(forbidden, lower_bytes, f"Leaked {forbidden} in PDF certificate!")

    def test_generate_jsonld_verifiable_credential(self):
        """JSON-LD certificate adheres to W3C Verifiable Credential structure."""
        doc = generate_audit_certificate_jsonld(
            self.mock_record,
            self.mock_receipt,
            self.mock_forensics
        )
        self.assertIn("@context", doc)
        self.assertIn("https://www.w3.org/2018/credentials/v1", doc["@context"])
        self.assertIn("credentialSubject", doc)
        self.assertEqual(doc["credentialSubject"]["biometricDiagnostics"]["matchesCount"], 27)
        self.assertEqual(doc["proof"]["blockNumber"], 1)

    def test_api_certificate_pdf_endpoint(self):
        """export_certificate_pdf endpoint returns a streaming PDF response with correct headers."""
        async def _test():
            resp = await export_certificate_pdf({
                "record": self.mock_record,
                "receipt": self.mock_receipt,
                "forensics": self.mock_forensics
            })
            self.assertEqual(resp.media_type, "application/pdf")
            self.assertIn("attachment; filename=", resp.headers.get("Content-Disposition", ""))
            chunks = [c async for c in resp.body_iterator]
            pdf_content = b"".join(chunks)
            self.assertTrue(pdf_content.startswith(b"%PDF-"))
        asyncio.run(_test())

    def test_api_certificate_jsonld_endpoint(self):
        """export_certificate_jsonld returns valid JSON-LD credential response."""
        resp = asyncio.run(export_certificate_jsonld({
            "record": self.mock_record,
            "receipt": self.mock_receipt,
            "forensics": self.mock_forensics
        }))
        self.assertEqual(resp.status_code, 200)
        import json
        data = json.loads(resp.body.decode('utf-8'))
        self.assertIn("@context", data)
        self.assertIn("credentialSubject", data)

if __name__ == "__main__":
    unittest.main()
