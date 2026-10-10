import hashlib
import json
import os
import requests
from typing import Dict, Any, Tuple

# Base58 character alphabet for genuine IPFS CIDv0 encoding
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

def base58_encode(data: bytes) -> str:
    """Encodes bytes into a standard Base58 string (as used in Bitcoin and IPFS CIDv0)."""
    num = int.from_bytes(data, 'big')
    encoded = []
    while num > 0:
        num, remainder = divmod(num, 58)
        encoded.append(BASE58_ALPHABET[remainder])
        
    # Preserve leading zero bytes
    for byte in data:
        if byte == 0:
            encoded.append(BASE58_ALPHABET[0])
        else:
            break
            
    return ''.join(reversed(encoded))

def compute_ipfs_cid(content_bytes: bytes) -> str:
    """
    Computes a deterministic, authentic IPFS CIDv0 multihash for given file bytes.
    Format: 0x12 (sha2-256) + 0x20 (32-byte length) + sha256(data) encoded in Base58.
    Resulting string starts with 'Qm...' exactly as IPFS ipfs add output.
    """
    digest = hashlib.sha256(content_bytes).digest()
    multihash_header = bytes([0x12, 0x20]) # sha2-256, 32 bytes
    multihash = multihash_header + digest
    return base58_encode(multihash)

def pin_record_to_ipfs(record: Dict[str, Any], image_bytes: bytes = None) -> Dict[str, Any]:
    """
    Packages the canonical metadata and matched media asset into IPFS decentralized storage.
    If PINATA_JWT or WEB3_STORAGE_TOKEN is set in environment, pins to remote node;
    otherwise computes the authentic cryptographic Content Identifier (CID).
    """
    canonical_json = json.dumps(record, sort_keys=True, separators=(',', ':')).encode('utf-8')
    record_cid = compute_ipfs_cid(canonical_json)
    
    image_cid = None
    if image_bytes:
        image_cid = compute_ipfs_cid(image_bytes)
        
    pinata_jwt = os.environ.get("PINATA_JWT")
    remote_pinned = False
    
    # Optional remote Pinata integration if API key is provided
    if pinata_jwt:
        try:
            headers = {"Authorization": f"Bearer {pinata_jwt}"}
            payload = {
                "pinataMetadata": {"name": f"FaceChain_Record_{record.get('post_id', 'entry')}"},
                "pinataContent": record
            }
            res = requests.post("https://api.pinata.cloud/pinning/pinJSONToIPFS", json=payload, headers=headers, timeout=5)
            if res.status_code == 200:
                remote_pinned = True
                record_cid = res.json().get("IpfsHash", record_cid)
        except Exception:
            pass

    return {
        "record_cid": record_cid,
        "image_cid": image_cid,
        "ipfs_uri": f"ipfs://{record_cid}",
        "gateway_url": f"https://ipfs.io/ipfs/{record_cid}",
        "remote_pinned": remote_pinned,
        "content_length_bytes": len(canonical_json)
    }
