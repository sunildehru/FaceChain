import json
import unittest
import os
from src.fingerprint import build_record, hash_record
from src.chain.provider import deploy_registry_contract, get_chain_telemetry, get_w3
from src.chain.anchor import anchor_record
from src.verify import verify_on_chain
from src.chain.ipfs import compute_ipfs_cid, pin_record_to_ipfs
from src.forensics import analyze_facial_liveness_and_deepfake
from src.osint import get_watchlist

class TestFaceChainPipeline(unittest.TestCase):

    def test_canonical_fingerprint_deterministic(self):
        """Verify that hashing is strictly deterministic across equivalent records."""
        sample_match = {
            "post_id": "test_post_001",
            "username": "en.wikipedia.org",
            "platform": "live-web-crawler",
            "image_path": "https://example.com/portrait.jpg",
            "caption": "Executive Portrait",
            "timestamp": "https://en.wikipedia.org/wiki/Test",
            "similarity_score": 0.9421,
            "query_timestamp": "2026-10-22T12:00:00+00:00"
        }
        
        record1 = build_record(sample_match)
        record2 = build_record(sample_match)
        
        hash1 = hash_record(record1)
        hash2 = hash_record(record2)
        
        self.assertEqual(hash1, hash2)
        self.assertEqual(len(hash1), 64)
        self.assertIsInstance(hash1, str)

    def test_blockchain_anchor_and_tamper_detection(self):
        """Verify that anchoring works and any tampering causes the verification to fail."""
        contract_address, abi = deploy_registry_contract()
        self.assertIsNotNone(contract_address)
        self.assertGreater(len(abi), 0)

        sample_match = {
            "post_id": "test_post_anchor",
            "username": "test.org",
            "platform": "live-web-crawler",
            "image_path": "https://test.org/image.png",
            "caption": "Original Clean Caption",
            "timestamp": "https://test.org/profile",
            "similarity_score": 0.985,
            "query_timestamp": "2026-10-22T12:00:00+00:00"
        }

        record = build_record(sample_match)
        record_hash = hash_record(record)

        # 1. Anchor on-chain
        receipt = anchor_record(record_hash, contract_address, abi)
        self.assertTrue(receipt["status"])
        self.assertTrue(receipt["tx_hash"].startswith("0x"))
        self.assertGreaterEqual(receipt["block_number"], 0)

        # 2. Verify on-chain (should be True)
        verified_result = verify_on_chain(record, contract_address, abi)
        self.assertTrue(verified_result["is_verified"])
        self.assertIsNotNone(verified_result["submitter"])
        self.assertGreater(verified_result["anchored_at"], 0)

        # 3. Simulate tamper (should fail)
        tampered_record = json.loads(json.dumps(record))
        tampered_record["caption"] = "FRAUDULENT ADVERSARY CAPTION"

        tampered_result = verify_on_chain(tampered_record, contract_address, abi)
        self.assertFalse(tampered_result["is_verified"])

    def test_chain_telemetry(self):
        """Verify telemetry reporting."""
        w3 = get_w3()
        telemetry = get_chain_telemetry("0x1234567890123456789012345678901234567890")
        self.assertEqual(telemetry["status"], "OPERATIONAL")
        self.assertEqual(telemetry["chain_id"], w3.eth.chain_id)
        self.assertGreater(telemetry["accounts_count"], 0)

    def test_ipfs_multihash_cid_generation(self):
        """Verify authentic Base58 IPFS CIDv0 derivation (Option A)."""
        content = b"FaceChain Cryptographic Content Identifier Payload"
        cid = compute_ipfs_cid(content)
        self.assertTrue(cid.startswith("Qm"))
        self.assertEqual(len(cid), 46)

    def test_forensics_spectral_analysis(self):
        """Verify 2D FFT and Laplacian analysis on reference preset portrait."""
        for ref_image in ["dataset/presets/zuckerberg.jpg", "dataset/presets/satya_nadella.jpg", "dataset/images/alice.jpg"]:
            if os.path.exists(ref_image):
                result = analyze_facial_liveness_and_deepfake(ref_image)
                self.assertIn("liveness_score", result)
                self.assertIn("deepfake_risk", result)
                self.assertGreater(result["liveness_score"], 0.0)
                break

    def test_osint_watchlist_structure(self):
        """Verify OSINT watchlist retrieval (Option D)."""
        watchlist = get_watchlist()
        self.assertIsInstance(watchlist, list)
        self.assertGreater(len(watchlist), 0)
        self.assertIn("name", watchlist[0])
        self.assertIn("monitored_urls", watchlist[0])

    def test_multi_match_canonical_record(self):
        """Verify multi-match biometric telemetry in canonical record."""
        multi_match = {
            "post_id": "test_multi_001",
            "username": "en.wikipedia.org",
            "platform": "live-web-crawler",
            "image_path": "https://example.com/multi_face.jpg",
            "caption": "Executive Multi Match",
            "timestamp": "https://en.wikipedia.org/wiki/Satya_Nadella",
            "similarity_score": 0.925,
            "query_timestamp": "2026-10-22T12:00:00+00:00",
            "matches_count": 4,
            "unique_matches_count": 2,
            "duplicates_count": 2,
            "images_scanned": 48,
            "faces_detected": 18
        }
        record = build_record(multi_match)
        self.assertEqual(record["matches_count"], 4)
        self.assertEqual(record["unique_matches_count"], 2)
        self.assertEqual(record["duplicates_count"], 2)
        self.assertEqual(record["images_scanned"], 48)
        self.assertEqual(record["faces_detected"], 18)
        h = hash_record(record)
        self.assertEqual(len(h), 64)

    def test_filename_deduplication_helper(self):
        """Verify normalization and deduplication helper on Wikipedia thumbnail URLs."""
        from src.search import _extract_base_filename
        url1 = "https://upload.wikimedia.org/wikipedia/commons/thumb/1/18/Mark_Zuckerberg_F8_2019_Keynote.jpg/220px-Mark_Zuckerberg_F8_2019_Keynote.jpg"
        url2 = "https://upload.wikimedia.org/wikipedia/commons/thumb/1/18/Mark_Zuckerberg_F8_2019_Keynote.jpg/440px-Mark_Zuckerberg_F8_2019_Keynote.jpg"
        self.assertEqual(_extract_base_filename(url1), "mark_zuckerberg_f8_2019_keynote.jpg")
        self.assertEqual(_extract_base_filename(url1), _extract_base_filename(url2))

    def test_autonomous_discovery_login_wall_filter(self):
        """Verify that walled-garden social media and login screens are strictly pruned."""
        from src.discovery import is_crawlable_public_url
        self.assertFalse(is_crawlable_public_url("https://www.instagram.com/p/sample/"))
        self.assertFalse(is_crawlable_public_url("https://facebook.com/profile.php?id=123"))
        self.assertFalse(is_crawlable_public_url("https://twitter.com/user/status/123"))
        self.assertFalse(is_crawlable_public_url("https://x.com/user"))
        self.assertFalse(is_crawlable_public_url("https://tiktok.com/@user"))
        self.assertFalse(is_crawlable_public_url("https://example.com/accounts/login"))

        # Valid public bio/profile sites
        self.assertTrue(is_crawlable_public_url("https://en.wikipedia.org/wiki/Satya_Nadella"))
        self.assertTrue(is_crawlable_public_url("https://cs.stanford.edu/people/faculty"))
        self.assertTrue(is_crawlable_public_url("https://github.com/torvalds"))
        self.assertTrue(is_crawlable_public_url("https://techcrunch.com/about-us"))

        # LinkedIn public person profiles allowed for cached thumbnail verification
        self.assertTrue(is_crawlable_public_url("https://www.linkedin.com/in/hari-pavan"))
        self.assertTrue(is_crawlable_public_url("https://in.linkedin.com/in/hemanth-j"))
        self.assertTrue(is_crawlable_public_url("https://www.linkedin.com/pub/john-doe/1/2/3"))

    def test_bing_image_pairs_extraction(self):
        """Verify regex & JSON extraction of purl and turl from Bing image markup."""
        from src.discovery import extract_bing_image_pairs_from_text
        sample_html = '''
        <a class="iusc" href="#" m='{"ns":"1","purl":"https://www.linkedin.com/in/hari-pavan","murl":"https://media.licdn.com/dms/image/sample.jpg","turl":"https://ts1.mm.bing.net/th?id=OIP.abc123xyz","t":"Hari Pavan - Senior HR Consultant | LinkedIn"}'></a>
        '''
        pairs = extract_bing_image_pairs_from_text(sample_html)
        self.assertGreaterEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["purl"], "https://www.linkedin.com/in/hari-pavan")
        self.assertEqual(pairs[0]["turl"], "https://ts1.mm.bing.net/th?id=OIP.abc123xyz")
        self.assertIn("Hari Pavan", pairs[0]["title"])

if __name__ == '__main__':
    unittest.main()
