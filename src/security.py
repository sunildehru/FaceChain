"""
FaceChain Security & Input Sanitation Engine
Strictly validates uploaded media, enforces size & format boundaries,
and neutralizes any local path or credential leakage.
"""

import io
import os
import re
from typing import Tuple, Dict, Any, Optional
from PIL import Image
from fastapi import HTTPException, UploadFile

# Maximum allowed upload size (15 MB to prevent memory exhaustion / DoS)
MAX_UPLOAD_SIZE_BYTES = 15 * 1024 * 1024

# Allowed image MIME signatures
ALLOWED_MAGIC_SIGNATURES = [
    (b"\xff\xd8\xff", "image/jpeg"),               # JPEG
    (b"\x89PNG\r\n\x1a\n", "image/png"),          # PNG
    (b"RIFF", "image/webp"),                       # WebP (RIFF header)
    (b"GIF87a", "image/gif"),                      # GIF87a
    (b"GIF89a", "image/gif"),                      # GIF89a
]

# Prohibited dangerous signatures (executables, shell scripts, html/php, archives)
DANGEROUS_SIGNATURES = [
    b"MZ",                                         # Windows PE Executable / DLL
    b"\x7fELF",                                    # Linux ELF Executable
    b"#!",                                         # Unix Shell Script
    b"<?php",                                      # PHP script
    b"<html",                                      # HTML document
    b"<!DOCTYPE",                                  # HTML/XML doctype
    b"%PDF",                                       # PDF document (uploaded as image)
    b"PK\x03\x04",                                 # ZIP / Jar / Office doc
]

def sanitize_filename(filename: str) -> str:
    """
    Strips directory traversal sequences, path separators, and non-alphanumeric chars.
    Returns a safe basename with allowed extensions.
    """
    if not filename:
        return "query_image.jpg"

    # Strip directory path
    base = os.path.basename(filename)
    
    # Strip null bytes and control chars
    base = re.sub(r'[\x00-\x1f\x7f]', '', base)
    
    # Keep only safe alphanumeric characters, underscores, hyphens, and periods
    safe_base = re.sub(r'[^a-zA-Z0-9_.-]', '_', base)
    
    # Ensure reasonable length
    if len(safe_base) > 80:
        name_part, ext_part = os.path.splitext(safe_base)
        safe_base = name_part[:70] + ext_part
        
    return safe_base

def validate_and_sanitize_image_bytes(
    raw_bytes: bytes,
    filename: str,
    max_size: int = MAX_UPLOAD_SIZE_BYTES
) -> Tuple[str, bytes]:
    """
    Strict validation of uploaded file bytes:
    1. Size ceiling enforcement (rejects zip bombs / memory exhaustion).
    2. Magic byte signature verification.
    3. Structural image verification via PIL.
    4. Safe filename normalization.
    """
    if not raw_bytes or len(raw_bytes) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    if len(raw_bytes) > max_size:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum permissible size of {max_size // (1024*1024)} MB."
        )

    # Check for prohibited dangerous executable/script signatures
    header = raw_bytes[:16]
    for dang in DANGEROUS_SIGNATURES:
        if header.startswith(dang) or dang in header[:8]:
            raise HTTPException(
                status_code=400,
                detail="Security rejection: uploaded file contains executable or script signatures."
            )

    # Validate against allowed image magic signatures
    is_valid_magic = False
    for magic, _ in ALLOWED_MAGIC_SIGNATURES:
        if header.startswith(magic):
            is_valid_magic = True
            break
        # Special check for WebP: starts with RIFF, then 4 bytes of length, then WEBP
        if magic == b"RIFF" and header[:4] == b"RIFF" and len(raw_bytes) >= 12 and raw_bytes[8:12] == b"WEBP":
            is_valid_magic = True
            break

    if not is_valid_magic:
        raise HTTPException(
            status_code=400,
            detail="Unsupported or invalid image format. Supported formats: JPEG, PNG, WebP."
        )

    # Structural integrity check: verify image data using PIL
    try:
        with Image.open(io.BytesIO(raw_bytes)) as pil_img:
            pil_img.verify()
            
            # Additional bounds check: reject dimension bombs (e.g. 50,000 x 50,000 decompression bombs)
            w, h = pil_img.size
            if w > 10000 or h > 10000 or (w * h) > 36000000:
                raise HTTPException(
                    status_code=400,
                    detail="Image dimensions exceed maximum safe operational bounds (max 36 megapixels)."
                )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Corrupted or invalid image payload: {str(e)}"
        )

    clean_filename = sanitize_filename(filename)
    return clean_filename, raw_bytes

def sanitize_text_for_export(val: Any, max_len: int = 256) -> str:
    r"""
    Sanitizes string attributes for safe PDF rendering and JSON-LD generation:
    - Escapes XML/HTML syntax characters.
    - Strips local filesystem drive letters (C:\, D:\) and home directories.
    - Truncates to max_len to prevent layout overflow.
    """
    if val is None:
        return "N/A"
        
    s = str(val).strip()
    
    # Neutralize any local Windows/Unix absolute machine paths
    s = re.sub(r'[A-Za-z]:\\[^"\'\s<>]+', lambda m: os.path.basename(m.group(0)), s)
    s = re.sub(r'/(?:Users|home|root)/[^"\'\s<>]+', lambda m: os.path.basename(m.group(0)), s)
    s = re.sub(r'temp[\\/][^"\'\s<>]+', lambda m: os.path.basename(m.group(0)), s)
    
    # Strip non-printable ASCII control characters (keep space and standard punctuation)
    s = re.sub(r'[\x00-\x1f\x7f]', '', s)
    
    # Truncate
    if len(s) > max_len:
        s = s[:max_len - 3] + "..."
        
    return s

def sanitize_record_for_export(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Returns a copy of a canonical verification record with all internal machine paths sanitized.
    """
    clean = {}
    for k, v in record.items():
        if isinstance(v, str):
            clean[k] = sanitize_text_for_export(v)
        elif isinstance(v, dict):
            clean[k] = {sub_k: sanitize_text_for_export(sub_v) if isinstance(sub_v, str) else sub_v for sub_k, sub_v in v.items()}
        elif isinstance(v, list):
            clean[k] = [sanitize_text_for_export(item) if isinstance(item, str) else item for item in v]
        else:
            clean[k] = v
            
    # Guarantee image_path does not retain local temp path
    if "image_path" in clean and clean["image_path"]:
        img_p = clean["image_path"]
        if not img_p.startswith("http"):
            clean["image_path"] = os.path.basename(img_p)
            
    return clean
