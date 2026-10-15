import json
import os
import shutil
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import numpy as np

from .face_encode import encode_face
from .search import find_best_match_web
from .fingerprint import build_record, hash_record
from .chain.anchor import anchor_record
from .chain.ipfs import pin_record_to_ipfs

WATCHLIST_PATH = "dataset/watchlist.json"
FACES_DIR = "dataset/watchlist_faces"
PRESETS_DIR = "dataset/presets"

def _ensure_default_preset_images():
    """Ensures directories exist for the default watchlist targets and presets."""
    os.makedirs(PRESETS_DIR, exist_ok=True)
    os.makedirs(FACES_DIR, exist_ok=True)

def _load_watchlist() -> List[Dict[str, Any]]:
    """Loads watchlist from local persistent JSON store."""
    _ensure_default_preset_images()
    
    default_data = [
        {
            "id": "target_zuck",
            "name": "Mark Zuckerberg",
            "face_image": "dataset/presets/zuckerberg.jpg",
            "monitored_urls": [
                "https://en.wikipedia.org/wiki/Mark_Zuckerberg"
            ],
            "status": "ACTIVE_MONITORING",
            "registered_at": "2026-10-22T10:00:00Z",
            "last_scanned_at": None,
            "sightings": []
        },
        {
            "id": "target_satya",
            "name": "Satya Nadella",
            "face_image": "dataset/presets/satya_nadella.jpg",
            "monitored_urls": [
                "https://en.wikipedia.org/wiki/Satya_Nadella"
            ],
            "status": "ACTIVE_MONITORING",
            "registered_at": "2026-10-22T11:00:00Z",
            "last_scanned_at": None,
            "sightings": []
        },
        {
            "id": "target_sundar",
            "name": "Sundar Pichai",
            "face_image": "dataset/presets/sundar_pichai.jpg",
            "monitored_urls": [
                "https://en.wikipedia.org/wiki/Sundar_Pichai"
            ],
            "status": "ACTIVE_MONITORING",
            "registered_at": "2026-10-22T12:00:00Z",
            "last_scanned_at": None,
            "sightings": []
        }
    ]

    if not os.path.exists(WATCHLIST_PATH):
        os.makedirs(os.path.dirname(WATCHLIST_PATH), exist_ok=True)
        with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
            json.dump(default_data, f, indent=2)
        return default_data
        
    try:
        with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Ensure Alice/Bob are scrubbed and Mark Zuckerberg points to genuine image
            cleaned = []
            modified = False
            for t in data:
                if t.get("id") in ("target_alice", "target_bob"):
                    modified = True
                    continue
                if t.get("id") == "target_zuck":
                    if t.get("face_image") != "dataset/presets/zuckerberg.jpg" or not os.path.exists(t.get("face_image", "")):
                        t["face_image"] = "dataset/presets/zuckerberg.jpg"
                        modified = True
                cleaned.append(t)
            
            # Ensure Satya and Sundar are present in watchlist
            ids = [t["id"] for t in cleaned]
            if "target_satya" not in ids:
                cleaned.append(default_data[1])
                modified = True
            if "target_sundar" not in ids:
                cleaned.append(default_data[2])
                modified = True

            if modified:
                try:
                    with open(WATCHLIST_PATH, "w", encoding="utf-8") as wf:
                        json.dump(cleaned, wf, indent=2)
                except Exception:
                    pass

            return cleaned
    except Exception:
        return default_data

def _save_watchlist(data: List[Dict[str, Any]]) -> None:
    """Saves watchlist to local persistent JSON store."""
    os.makedirs(os.path.dirname(WATCHLIST_PATH), exist_ok=True)
    with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def get_watchlist() -> List[Dict[str, Any]]:
    """Returns all currently registered OSINT watchlist targets."""
    return _load_watchlist()

def register_watchlist_target(name: str, face_image_path: str, monitored_urls: List[str]) -> Dict[str, Any]:
    """Registers a new identity target to be monitored across the web."""
    watchlist = _load_watchlist()
    target_id = f"target_{int(datetime.now().timestamp())}"
    
    new_target = {
        "id": target_id,
        "name": name,
        "face_image": face_image_path,
        "monitored_urls": [u.strip() for u in monitored_urls if u.strip()],
        "status": "ACTIVE_MONITORING",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "last_scanned_at": None,
        "sightings": []
    }
    
    watchlist.append(new_target)
    _save_watchlist(watchlist)
    return new_target

def delete_watchlist_target(target_id: str) -> bool:
    """Removes a target from the surveillance watchlist."""
    watchlist = _load_watchlist()
    initial_len = len(watchlist)
    watchlist = [t for t in watchlist if t["id"] != target_id]
    if len(watchlist) < initial_len:
        _save_watchlist(watchlist)
        return True
    return False

def scan_target(target_id: str, contract_address: str, abi: list, max_pages: int = 10) -> Dict[str, Any]:
    """
    Scans all monitored URLs for a given watchlist target using parallel crawler.
    If matched, generates an on-chain anchor proof and logs an OSINT sighting.
    """
    watchlist = _load_watchlist()
    target = next((t for t in watchlist if t["id"] == target_id), None)
    if not target:
        raise ValueError(f"Target '{target_id}' not found in watchlist.")
        
    face_path = target["face_image"]
    if not os.path.exists(face_path):
        raise FileNotFoundError(f"Reference face image not found at {face_path}")
        
    query_vec = encode_face(face_path)
    scan_results = []
    
    for url in target.get("monitored_urls", []):
        match = find_best_match_web(query_vec, url, max_pages=max_pages)
        if match:
            record = build_record(match)
            record_hash = hash_record(record)
            
            # Pin to IPFS
            ipfs_info = pin_record_to_ipfs(record)
            record["ipfs_uri"] = ipfs_info["ipfs_uri"]
            
            # Anchor to smart contract
            receipt = anchor_record(record_hash, contract_address, abi)
            
            sighting = {
                "sighting_id": f"sight_{int(datetime.now().timestamp())}",
                "target_name": target["name"],
                "url": url,
                "matched_image": match["image_path"],
                "similarity_score": match["similarity_score"],
                "matches_count": match.get("matches_count", 1),
                "images_scanned": match.get("images_scanned", 0),
                "faces_detected": match.get("faces_detected", 0),
                "tx_hash": receipt["tx_hash"],
                "block_number": receipt["block_number"],
                "ipfs_uri": ipfs_info["ipfs_uri"],
                "ipfs_cid": ipfs_info["record_cid"],
                "detected_at": datetime.now(timezone.utc).isoformat()
            }
            target["sightings"].append(sighting)
            scan_results.append(sighting)
            
    target["last_scanned_at"] = datetime.now(timezone.utc).isoformat()
    _save_watchlist(watchlist)
    
    return {
        "target_id": target_id,
        "target_name": target["name"],
        "sightings_found": len(scan_results),
        "results": scan_results
    }
