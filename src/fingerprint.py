import hashlib
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional

def build_record(match: Dict[str, Any], query_timestamp: Optional[str] = None) -> Dict[str, Any]:
    """
    Constructs a JSON-friendly canonical record from a match dictionary.
    Handles hashing the image URL or local file for verifiable proof.
    Reuses query_timestamp if already provided for deterministic repeatability.
    Includes multi-match biometric telemetry so on-chain proofs record the exact match count.
    """
    image_path = match.get("image_path", "")
    image_hash = hashlib.sha256(image_path.encode('utf-8')).hexdigest() if image_path else "0" * 64
    
    if image_path and not image_path.startswith("http"):
        try:
            with open(image_path, "rb") as f:
                image_hash = hashlib.sha256(f.read()).hexdigest()
        except Exception:
            pass

    ts = query_timestamp or match.get("query_timestamp") or datetime.now(timezone.utc).isoformat()

    record = {
        "post_id": match["post_id"],
        "image_sha256": image_hash,
        "caption": match["caption"],
        "username": match["username"],
        "post_url": match.get("timestamp", ""), # Storing source URL here in web scraping mode
        "query_timestamp": ts,
        "similarity_score": match.get("similarity_score", 0.0)
    }

    # Include multi-match biometric accounting in canonical on-chain record
    if "matches_count" in match:
        record["matches_count"] = int(match["matches_count"])
    if "unique_matches_count" in match:
        record["unique_matches_count"] = int(match["unique_matches_count"])
    if "duplicates_count" in match:
        record["duplicates_count"] = int(match["duplicates_count"])
    if "images_scanned" in match:
        record["images_scanned"] = int(match["images_scanned"])
    if "faces_detected" in match:
        record["faces_detected"] = int(match["faces_detected"])
    if "matched_samples" in match:
        record["matched_samples"] = match["matched_samples"]

    return record

def hash_record(record: Dict[str, Any]) -> str:
    """
    Computes a deterministic SHA-256 hash of the record by sorting its JSON keys.
    """
    canonical_json = json.dumps(record, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical_json.encode('utf-8')).hexdigest()
