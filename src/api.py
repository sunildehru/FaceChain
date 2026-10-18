import os
import shutil
import json
import io
import asyncio
import threading
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .face_encode import encode_face
from .search import find_best_match_web, stream_search_pipeline
from .fingerprint import build_record, hash_record
from .chain.provider import get_w3, deploy_registry_contract, get_chain_telemetry
from .chain.anchor import anchor_record
from .chain.ipfs import pin_record_to_ipfs
from .forensics import analyze_facial_liveness_and_deepfake
from .osint import get_watchlist, register_watchlist_target, scan_target, delete_watchlist_target, _ensure_default_preset_images
from .verify import verify_on_chain
from .discovery import discover_candidate_target_urls, is_crawlable_public_url
from .security import validate_and_sanitize_image_bytes, sanitize_record_for_export
from .certificate import generate_audit_certificate_pdf, generate_audit_certificate_jsonld

CONTRACT_ADDRESS = None
CONTRACT_ABI = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles startup and shutdown events for the FastAPI application."""
    global CONTRACT_ADDRESS, CONTRACT_ABI
    try:
        # Pre-stage preset portrait images
        _ensure_default_preset_images()
        CONTRACT_ADDRESS, CONTRACT_ABI = deploy_registry_contract()
    except Exception as e:
        print(f"[API] Error deploying contract: {e}")
    yield

app = FastAPI(
    title="FaceChain Forensic Biometric & OSINT Ledger API",
    description="Parallel web crawling face identification, FFT deepfake detection, IPFS pinning, and EVM ledger anchoring",
    version="2.5.0",
    lifespan=lifespan
)

@app.middleware("http")
async def add_security_headers_middleware(request: Request, call_next):
    """Injects defensive HTTP security and privacy headers into every response."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(self), microphone=()"
    return response

@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Serves custom Bauhaus 404 page for browser requests or clean JSON for API callers."""
    if exc.status_code == 404:
        accept = request.headers.get("accept", "")
        if "text/html" in accept and os.path.exists("frontend/404.html"):
            with open("frontend/404.html", "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read(), status_code=404)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "status": exc.status_code}
    )

@app.get("/favicon.ico", include_in_schema=False)
async def favicon_ico():
    """Serves multi-resolution legacy browser favicon."""
    if os.path.exists("frontend/favicon.ico"):
        return FileResponse("frontend/favicon.ico", media_type="image/x-icon")
    raise HTTPException(status_code=404, detail="Favicon not found")

@app.get("/robots.txt", include_in_schema=False)
async def robots_txt():
    """Serves crawler policy."""
    if os.path.exists("frontend/robots.txt"):
        return FileResponse("frontend/robots.txt", media_type="text/plain")
    return Response(content="User-agent: *\nAllow: /\n", media_type="text/plain")

@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml():
    """Serves search engine sitemap."""
    if os.path.exists("frontend/sitemap.xml"):
        return FileResponse("frontend/sitemap.xml", media_type="application/xml")
    raise HTTPException(status_code=404, detail="Sitemap not found")

@app.get("/site.webmanifest", include_in_schema=False)
async def site_webmanifest():
    """Serves Progressive Web App manifest."""
    if os.path.exists("frontend/site.webmanifest"):
        return FileResponse("frontend/site.webmanifest", media_type="application/manifest+json")
    raise HTTPException(status_code=404, detail="Manifest not found")

@app.get("/healthz")
async def healthz():
    """Kubernetes / container health probe endpoint."""
    return {
        "status": "HEALTHY",
        "protocol": "FaceChain Biometric Registry",
        "version": "2.5.0",
        "evm_connected": bool(CONTRACT_ADDRESS),
        "contract_address": CONTRACT_ADDRESS,
        "timestamp": datetime.now().isoformat()
    }

os.makedirs("temp", exist_ok=True)
os.makedirs("temp/scraped", exist_ok=True)
os.makedirs("dataset/watchlist_faces", exist_ok=True)
os.makedirs("dataset/presets", exist_ok=True)

for src_name, dst_name in [
    ("satya_nadella.jpg", "sample_lens.jpg"),
    ("zuckerberg.jpg", "sample_zuck.jpg"),
    ("sundar_pichai.jpg", "sample_sundar.jpg")
]:
    src_f = f"dataset/presets/{src_name}"
    dst_f = f"frontend/{dst_name}"
    if os.path.exists(src_f) and not os.path.exists(dst_f):
        try:
            shutil.copyfile(src_f, dst_f)
        except Exception:
            pass

# Ensure the landing page portrait is available in frontend
_default_fallback_portrait = "dataset/presets/satya_nadella.jpg"
_landing_portrait_dst = "frontend/sample_realistic_portrait.jpg"
if not os.path.exists(_landing_portrait_dst) and os.path.exists(_default_fallback_portrait):
    try:
        shutil.copyfile(_default_fallback_portrait, _landing_portrait_dst)
    except Exception as _e:
        print(f"[API] Fallback copy notice: {_e}")

if os.path.exists("frontend"):
    app.mount("/static", StaticFiles(directory="frontend"), name="static")

@app.get("/", response_class=HTMLResponse)
async def get_index():
    with open("frontend/index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/api/status")
async def get_status():
    """Returns the live status of the EVM testnet and smart contract deployment."""
    telemetry = get_chain_telemetry(CONTRACT_ADDRESS)
    telemetry["contract_deployed"] = bool(CONTRACT_ADDRESS)
    return telemetry

@app.get("/api/landing/portrait")
async def get_landing_portrait():
    """Serves the authentic candid portrait for the interactive biometric lens on the landing page."""
    headers = {
        "Cache-Control": "public, max-age=86400",
    }
    p = "frontend/sample_realistic_portrait.jpg"
    if os.path.exists(p):
        return FileResponse(p, media_type="image/jpeg", headers=headers)
    raise HTTPException(status_code=404, detail="Landing portrait not found")

@app.get("/api/presets")
async def get_presets():
    """Provides curated demonstrator presets with offline query reference images."""
    return [
        {
            "id": "wikipedia-zuck",
            "name": "Mark Zuckerberg",
            "affiliation": "Meta Platforms · Founder & CEO",
            "target_urls": ["https://en.wikipedia.org/wiki/Mark_Zuckerberg"],
            "image_url": "/api/presets/wikipedia-zuck/image",
            "description": "Scans Wikipedia biography for Mark Zuckerberg and authenticates biometric vectors across pages."
        },
        {
            "id": "satya-nadella",
            "name": "Satya Nadella",
            "affiliation": "Microsoft Corporation · Chairman & CEO",
            "target_urls": ["https://en.wikipedia.org/wiki/Satya_Nadella"],
            "image_url": "/api/presets/satya-nadella/image",
            "description": "Scans Wikipedia article for Satya Nadella and resolves executive identity across sub-pages."
        },
        {
            "id": "sundar-pichai",
            "name": "Sundar Pichai",
            "affiliation": "Alphabet & Google · CEO",
            "target_urls": ["https://en.wikipedia.org/wiki/Sundar_Pichai"],
            "image_url": "/api/presets/sundar-pichai/image",
            "description": "Scans Wikipedia profile for Sundar Pichai across sub-pages and anchors sightings on EVM ledger."
        }
    ]

@app.get("/api/presets/{preset_id}/image")
async def get_preset_image(preset_id: str):
    """Serves the offline reference photo for an instant one-click demonstration with anti-caching headers."""
    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0"
    }
    pid = preset_id.lower().replace("-", "_")
    if "zuck" in pid:
        p = "dataset/presets/zuckerberg.jpg"
        if os.path.exists(p):
            return FileResponse(p, media_type="image/jpeg", headers=headers)
    elif "satya" in pid:
        p = "dataset/presets/satya_nadella.jpg"
        if os.path.exists(p):
            return FileResponse(p, media_type="image/jpeg", headers=headers)
    elif "sundar" in pid or "pichai" in pid:
        p = "dataset/presets/sundar_pichai.jpg"
        if os.path.exists(p):
            return FileResponse(p, media_type="image/jpeg", headers=headers)

    # Fallback to any available preset portrait
    for p in ["dataset/presets/zuckerberg.jpg", "dataset/presets/satya_nadella.jpg", "dataset/presets/sundar_pichai.jpg"]:
        if os.path.exists(p):
            return FileResponse(p, media_type="image/jpeg", headers=headers)
    raise HTTPException(status_code=404, detail="Preset image not found")

@app.post("/api/discover")
async def discover_urls_endpoint(
    name: str = Form(None),
    file: UploadFile = File(None),
    max_pages: int = Form(10)
):
    """
    Autonomous Discovery Seam:
    Executes search engine dorking (pruning walled-garden login walls) and reverse visual lookup
    to identify candidate public websites where a subject's identity appears.
    """
    temp_path = None
    if file and file.filename:
        temp_path = f"temp/discover_{file.filename}"
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    try:
        clean_name = (name or "").strip()
        discovered = discover_candidate_target_urls(
            name=clean_name if clean_name else None,
            image_path=temp_path,
            max_pages_budget=max_pages
        )
        msg = None
        if len(discovered) == 0 and not clean_name:
            msg = "Subject name required to spider open web profiles (preset biometric match not found). Please type subject name."
        return JSONResponse(content={
            "status": "success",
            "name": clean_name,
            "count": len(discovered),
            "discovered": discovered,
            "urls": [d["url"] for d in discovered],
            "message": msg
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Discovery Error: {str(e)}")
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass

@app.get("/api/discover/test")
async def test_discover(name: str = "Satya Nadella", max_pages: int = 5):
    """Diagnostic GET endpoint to test autonomous discovery on any name."""
    clean = name.strip()
    discovered = discover_candidate_target_urls(name=clean, max_pages_budget=max_pages)
    return {
        "status": "success",
        "name": clean,
        "count": len(discovered),
        "discovered": discovered
    }

@app.post("/api/pipeline/stream")
async def run_pipeline_stream(
    file: UploadFile = File(...),
    target_url: str = Form(None),
    target_urls: str = Form(None),
    subject_name: str = Form(None),
    auto_discover: bool = Form(False),
    max_pages: int = Form(10),
    candidate_metadata: str = Form(None)
):
    """
    Streaming Producer-Consumer verification pipeline via Server-Sent Events (SSE).
    Supports Autonomous Web Discovery (Search Engine Dorks + Reverse Visual Search + Cached Thumbnails),
    streams live crawler progression, real-time match detections (with deduplication),
    and on-chain EVM ledger anchoring proofs directly to the UI.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No face image uploaded.")

    raw_urls = target_urls or target_url or ""
    has_target_urls = bool(raw_urls and raw_urls.strip())
    has_subject_name = bool(subject_name and subject_name.strip())

    if not has_target_urls and not auto_discover and not has_subject_name:
        raise HTTPException(status_code=400, detail="At least one valid Target URL or Subject Name (for Auto-Discovery) is required.")

    if not CONTRACT_ADDRESS:
        raise HTTPException(status_code=500, detail="Smart contract registry is not currently deployed.")

    # Secure upload validation & sanitation
    raw_bytes = await file.read()
    clean_filename, valid_bytes = validate_and_sanitize_image_bytes(raw_bytes, file.filename)
    safe_prefix = int(datetime.now().timestamp() * 1000)
    temp_path = f"temp/{safe_prefix}_{clean_filename}"
    with open(temp_path, "wb") as buffer:
        buffer.write(valid_bytes)

    async def event_generator():
        try:
            # Phase 1: Real Forensic Deepfake & Liveness Spectral Analysis
            phase1_msg = {
                "type": "phase",
                "stage": 1,
                "percent": 12,
                "name": "Phase 01: FFT Spectral Deepfake Analysis",
                "detail": "Computing 2D Fast Fourier Transform power spectrum and Laplacian edge variance..."
            }
            yield f"data: {json.dumps(phase1_msg)}\n\n"
            await asyncio.sleep(0.05)

            forensics = await asyncio.to_thread(analyze_facial_liveness_and_deepfake, temp_path)
            liveness_pct = round(forensics.get("liveness_score", 0.0) * 100, 1)
            lap_val = forensics.get("laplacian_edge_variance", 0)
            forensics_msg = {
                "type": "forensics_done",
                "stage": 1,
                "percent": 22,
                "forensics": forensics,
                "detail": f"Authenticity: {liveness_pct}%, Laplacian: {lap_val}"
            }
            yield f"data: {json.dumps(forensics_msg)}\n\n"
            await asyncio.sleep(0.05)

            # Phase 2: Facial vector encoding
            phase2_msg = {
                "type": "phase",
                "stage": 2,
                "percent": 30,
                "name": "Phase 02: Biometric Ingestion & Facial Vector Extraction",
                "detail": "Extracting 128-D ResNet metric biometric vector..."
            }
            yield f"data: {json.dumps(phase2_msg)}\n\n"
            await asyncio.sleep(0.05)

            try:
                query_vec = await asyncio.to_thread(encode_face, temp_path)
            except Exception as fe_exc:
                err_msg = {
                    "type": "error",
                    "detail": str(fe_exc)
                }
                yield f"data: {json.dumps(err_msg)}\n\n"
                return

            if query_vec is None:
                err_msg = {
                    "type": "error",
                    "detail": "No human face detected in the uploaded image. Please provide a clear portrait photo."
                }
                yield f"data: {json.dumps(err_msg)}\n\n"
                return

            # Autonomous Discovery Stage: Search Engine Dorks, Thumbnail Caches & Reverse Visual Lookup
            active_urls = raw_urls
            candidate_items_for_pipeline = []

            # Parse pre-existing candidate_metadata from frontend if sent
            if candidate_metadata:
                try:
                    parsed_meta = json.loads(candidate_metadata)
                    if isinstance(parsed_meta, dict):
                        candidate_items_for_pipeline.extend(list(parsed_meta.values()))
                    elif isinstance(parsed_meta, list):
                        candidate_items_for_pipeline.extend(parsed_meta)
                except Exception:
                    pass

            if auto_discover or not has_target_urls:
                disc_phase_msg = {
                    "type": "phase",
                    "stage": 2,
                    "percent": 38,
                    "name": "Autonomous Discovery: Search Engine Dorks & Thumbnail Cache Lookup",
                    "detail": f"Scanning search engine indexes and cached portraits for '{subject_name or 'subject'}'..."
                }
                yield f"data: {json.dumps(disc_phase_msg)}\n\n"
                await asyncio.sleep(0.05)

                discovered_items = await asyncio.to_thread(
                    discover_candidate_target_urls,
                    name=subject_name,
                    image_path=temp_path,
                    max_pages_budget=max_pages
                )

                if discovered_items:
                    disc_urls = [d["url"] for d in discovered_items]
                    candidate_items_for_pipeline.extend(discovered_items)
                    disc_event = {
                        "type": "discovered_urls",
                        "count": len(disc_urls),
                        "discovered": discovered_items,
                        "urls": disc_urls,
                        "detail": f"Auto-discovered {len(disc_urls)} public endpoint(s) with search-engine thumbnail caching."
                    }
                    yield f"data: {json.dumps(disc_event)}\n\n"
                    await asyncio.sleep(0.05)
                    active_urls = json.dumps(disc_urls)
                else:
                    if not has_target_urls:
                        msg_detail = f"Autonomous discovery found zero public crawlable web pages for '{subject_name}'. Please enter a Target URL manually." if subject_name else "Autonomous discovery requires a Subject Name (e.g. 'Hemanth J') or a Target URL to spider."
                        err_msg = {
                            "type": "error",
                            "detail": msg_detail
                        }
                        yield f"data: {json.dumps(err_msg)}\n\n"
                        return

            # Phase 3: Producer-Consumer Stream Search
            phase3_msg = {
                "type": "phase",
                "stage": 3,
                "percent": 45,
                "name": "Phase 03: Parallel Domain Spider & Live Face Matching",
                "detail": "Spidering pages and evaluating biometric vectors in real-time as images download..."
            }
            yield f"data: {json.dumps(phase3_msg)}\n\n"
            await asyncio.sleep(0.05)

            event_q = asyncio.Queue()
            loop = asyncio.get_running_loop()

            def run_producer_consumer():
                try:
                    for ev in stream_search_pipeline(
                        query_embedding=query_vec,
                        target_urls=active_urls,
                        max_pages=max_pages,
                        candidate_items=candidate_items_for_pipeline
                    ):
                        loop.call_soon_threadsafe(event_q.put_nowait, ev)
                except Exception as exc:
                    loop.call_soon_threadsafe(event_q.put_nowait, {"type": "error", "message": str(exc)})
                finally:
                    loop.call_soon_threadsafe(event_q.put_nowait, None)

            worker_thread = threading.Thread(target=run_producer_consumer, daemon=True)
            worker_thread.start()

            final_match_result = None

            while True:
                ev = await event_q.get()
                if ev is None:
                    break

                ev_type = ev.get("type")
                if ev_type == "complete":
                    final_match_result = ev.get("result")
                    yield f"data: {json.dumps(ev)}\n\n"
                elif ev_type in ("match_found", "progress", "started", "error"):
                    yield f"data: {json.dumps(ev)}\n\n"
                    if ev_type == "error":
                        return

            if not final_match_result:
                no_match_msg = {
                    "type": "no_match",
                    "message": "Zero matching facial vectors found within threshold across target domain(s).",
                    "forensics": forensics
                }
                yield f"data: {json.dumps(no_match_msg)}\n\n"
                return

            record = build_record(final_match_result)
            record["forensics"] = forensics

            # Phase 4: IPFS Content Identifier derivation
            phase4_msg = {
                "type": "phase",
                "stage": 4,
                "percent": 78,
                "name": "Phase 04: Cryptographic IPFS Pinning",
                "detail": "Deriving Base58 CIDv0 content identifier multihash..."
            }
            yield f"data: {json.dumps(phase4_msg)}\n\n"
            await asyncio.sleep(0.05)

            ipfs_info = await asyncio.to_thread(pin_record_to_ipfs, record)
            record["ipfs_uri"] = ipfs_info["ipfs_uri"]
            record["ipfs_cid"] = ipfs_info["record_cid"]
            record_hash = hash_record(record)

            ipfs_msg = {
                "type": "ipfs_done",
                "stage": 4,
                "percent": 85,
                "ipfs": ipfs_info,
                "record_hash": record_hash
            }
            yield f"data: {json.dumps(ipfs_msg)}\n\n"
            await asyncio.sleep(0.05)

            # Phase 5: Anchor into Solidity contract
            phase5_msg = {
                "type": "phase",
                "stage": 5,
                "percent": 90,
                "name": "Phase 05: Anchoring to EVM Ledger",
                "detail": "Submitting fingerprint transaction to Solidity smart contract..."
            }
            yield f"data: {json.dumps(phase5_msg)}\n\n"
            await asyncio.sleep(0.05)

            receipt = await asyncio.to_thread(anchor_record, record_hash, CONTRACT_ADDRESS, CONTRACT_ABI)

            anchor_msg = {
                "type": "anchor_done",
                "stage": 5,
                "percent": 95,
                "receipt": receipt,
                "tx_hash": receipt.get("tx_hash", ""),
                "block_number": receipt.get("block_number", 0),
                "gas_used": receipt.get("gas_used", 0)
            }
            yield f"data: {json.dumps(anchor_msg)}\n\n"
            await asyncio.sleep(0.05)

            # Phase 6: Immediate on-chain cryptographic re-verification
            phase6_msg = {
                "type": "phase",
                "stage": 6,
                "percent": 98,
                "name": "Phase 06: Zero-Trust Verification",
                "detail": "Direct on-chain proof assertion against Solidity state..."
            }
            yield f"data: {json.dumps(phase6_msg)}\n\n"
            await asyncio.sleep(0.05)

            verification_result = await asyncio.to_thread(verify_on_chain, record, CONTRACT_ADDRESS, CONTRACT_ABI)

            m_cnt = final_match_result.get("matches_count", 1)
            u_cnt = final_match_result.get("unique_matches_count", 1)
            d_cnt = final_match_result.get("duplicates_count", 0)
            i_cnt = final_match_result.get("images_scanned", 0)
            f_cnt = final_match_result.get("faces_detected", 0)

            # Final DONE payload
            done_payload = {
                "type": "done",
                "status": "success",
                "match": final_match_result,
                "record": record,
                "hash": record_hash,
                "receipt": receipt,
                "tx_hash": receipt.get("tx_hash", ""),
                "block_number": receipt.get("block_number", 0),
                "gas_used": receipt.get("gas_used", 0),
                "forensics": forensics,
                "ipfs": ipfs_info,
                "matches_count": m_cnt,
                "unique_matches_count": u_cnt,
                "duplicates_count": d_cnt,
                "images_scanned": i_cnt,
                "faces_detected": f_cnt,
                "all_matches": final_match_result.get("all_matches", []),
                "is_verified": verification_result.get("is_verified", False),
                "verification": verification_result,
                "contract_address": CONTRACT_ADDRESS,
                "message": f"Identified {m_cnt} verified facial match(es) ({u_cnt} unique, {d_cnt} duplicates); anchored cryptographically to EVM ledger."
            }
            yield f"data: {json.dumps(done_payload)}\n\n"

        except Exception as e:
            err_msg = {"type": "error", "detail": str(e)}
            yield f"data: {json.dumps(err_msg)}\n\n"
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

@app.post("/api/pipeline")
async def run_pipeline(
    file: UploadFile = File(...),
    target_url: str = Form(None),
    target_urls: str = Form(None),
    subject_name: str = Form(None),
    auto_discover: bool = Form(False),
    max_pages: int = Form(10),
    candidate_metadata: str = Form(None)
):
    """
    Main forensic verification pipeline:
    Supports Autonomous Web Discovery + Search Engine Thumbnail Caches + parallel crawling across target URLs.
    1. FFT 2D spectral & Laplacian edge analysis for Deepfake / Liveness verification.
    2. Facial vector extraction (128-D ResNet metric).
    3. Autonomous discovery (search engine dorks + reverse visual search + cached portraits) if requested.
    4. Staggered parallel domain crawling & cached thumbnail ingestion.
    5. Computes biometric distance across ALL extracted faces and identifies all matches.
    6. Assembles canonical metadata with match accounting & authentic IPFS CIDv0 multihash.
    7. Anchors SHA-256 fingerprint into Solidity contract on EVM ledger.
    8. Verifies state directly against on-chain storage.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No face image uploaded.")
        
    # Unify target_urls
    raw_urls = target_urls or target_url or ""
    has_target_urls = bool(raw_urls and raw_urls.strip())
    has_subject_name = bool(subject_name and subject_name.strip())

    if not has_target_urls and not auto_discover and not has_subject_name:
        raise HTTPException(status_code=400, detail="At least one valid Target URL or Subject Name (for Auto-Discovery) is required.")
        
    if not CONTRACT_ADDRESS:
        raise HTTPException(status_code=500, detail="Smart contract registry is not currently deployed.")
        
    # Secure upload validation & sanitation
    raw_bytes = await file.read()
    clean_filename, valid_bytes = validate_and_sanitize_image_bytes(raw_bytes, file.filename)
    safe_prefix = int(datetime.now().timestamp() * 1000)
    temp_path = f"temp/{safe_prefix}_{clean_filename}"
    with open(temp_path, "wb") as buffer:
        buffer.write(valid_bytes)
        
    try:
        # Phase 1: Real Forensic Deepfake & Liveness Spectral Analysis
        forensics = analyze_facial_liveness_and_deepfake(temp_path)
        
        # Phase 2: Facial vector encoding
        query_vec = encode_face(temp_path)

        # Autonomous Discovery if requested or if no URLs provided
        active_urls = raw_urls
        candidate_items = []

        if candidate_metadata:
            try:
                parsed_meta = json.loads(candidate_metadata)
                if isinstance(parsed_meta, dict):
                    candidate_items.extend(list(parsed_meta.values()))
                elif isinstance(parsed_meta, list):
                    candidate_items.extend(parsed_meta)
            except Exception:
                pass

        if auto_discover or not has_target_urls:
            discovered_items = discover_candidate_target_urls(
                name=subject_name,
                image_path=temp_path,
                max_pages_budget=max_pages
            )
            if discovered_items:
                candidate_items.extend(discovered_items)
                active_urls = json.dumps([d["url"] for d in discovered_items])
            elif not has_target_urls:
                return JSONResponse(content={
                    "status": "no_match",
                    "message": f"Zero public crawlable web pages discovered for '{subject_name}'. Please enter a Target URL manually.",
                    "forensics": forensics
                })
        
        # Phase 3: Parallel crawl target domain(s) and search for all matches
        match = find_best_match_web(query_vec, active_urls, max_pages=max_pages, candidate_items=candidate_items)
        
        if not match:
            return JSONResponse(content={
                "status": "no_match",
                "message": f"Zero matching facial vectors found within threshold across target domain(s). Scanned pages.",
                "forensics": forensics
            })
            
        record = build_record(match)
        record["forensics"] = forensics
        
        # Phase 4: IPFS Decentralized Content Identifier computation
        ipfs_info = pin_record_to_ipfs(record)
        record["ipfs_uri"] = ipfs_info["ipfs_uri"]
        record["ipfs_cid"] = ipfs_info["record_cid"]
        
        record_hash = hash_record(record)
        
        # Phase 5: Anchor into Solidity contract
        receipt = anchor_record(record_hash, CONTRACT_ADDRESS, CONTRACT_ABI)
        
        # Phase 6: Immediate on-chain cryptographic re-verification
        verification_result = verify_on_chain(record, CONTRACT_ADDRESS, CONTRACT_ABI)
        
        return JSONResponse(content={
            "status": "success",
            "match": match,
            "record": record,
            "hash": record_hash,
            "receipt": receipt,
            "tx_hash": receipt["tx_hash"],
            "block_number": receipt["block_number"],
            "gas_used": receipt["gas_used"],
            "forensics": forensics,
            "ipfs": ipfs_info,
            "matches_count": match.get("matches_count", 1),
            "images_scanned": match.get("images_scanned", 0),
            "faces_detected": match.get("faces_detected", 0),
            "all_matches": match.get("all_matches", []),
            "is_verified": verification_result.get("is_verified", False),
            "verification": verification_result,
            "contract_address": CONTRACT_ADDRESS,
            "message": f"Identified {match.get('matches_count', 1)} verified facial match(es) across {match.get('images_scanned', 0)} downloaded images; anchored cryptographically to EVM ledger."
        })
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pipeline Error: {str(e)}")
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass

@app.post("/api/certificate/pdf")
async def export_certificate_pdf(payload: dict):
    """
    Generates and exports an official cryptographic PDF audit certificate.
    Operates strictly in-memory (io.BytesIO) with zero host machine metadata leakage.
    """
    record = payload.get("record")
    receipt = payload.get("receipt")
    forensics = payload.get("forensics")

    if not record or not receipt:
        raise HTTPException(status_code=400, detail="Missing canonical record or on-chain receipt in payload.")

    try:
        pdf_bytes = generate_audit_certificate_pdf(record, receipt, forensics)
        hash_digest = record.get("image_sha256") or receipt.get("record_hash") or "record"
        safe_hash = hash_digest[:10]
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="facechain-audit-{safe_hash}.pdf"',
                "Cache-Control": "no-store, no-cache, must-revalidate"
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate certificate PDF: {str(e)}")

@app.post("/api/certificate/jsonld")
async def export_certificate_jsonld(payload: dict):
    """
    Generates and exports a W3C-compliant Verifiable Credential in JSON-LD format.
    """
    record = payload.get("record")
    receipt = payload.get("receipt")
    forensics = payload.get("forensics")

    if not record or not receipt:
        raise HTTPException(status_code=400, detail="Missing canonical record or on-chain receipt in payload.")

    try:
        doc = generate_audit_certificate_jsonld(record, receipt, forensics)
        return JSONResponse(content=doc)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate JSON-LD credential: {str(e)}")

@app.post("/api/verify")
async def verify_record(record: dict):
    """
    Cryptographic verification endpoint.
    Recalculates canonical SHA-256 hash of JSON payload and queries Solidity contract storage.
    """
    if not CONTRACT_ADDRESS:
        raise HTTPException(status_code=500, detail="Smart contract registry is not currently deployed.")
        
    verification = verify_on_chain(record, CONTRACT_ADDRESS, CONTRACT_ABI)
    record_hash = hash_record(record)
    
    return {
        "is_verified": verification.get("is_verified", False),
        "hash": record_hash,
        "verification": verification,
        "contract_address": CONTRACT_ADDRESS
    }

# ========================================================
# OSINT RADAR ENDPOINTS (ACTIVE & PRODUCTION-GRADE)
# ========================================================
@app.get("/api/osint/watchlist")
async def get_osint_watchlist():
    """Returns the registered surveillance watchlist targets and active sightings."""
    return {"watchlist": get_watchlist()}

@app.get("/api/osint/target/{target_id}/image")
async def get_osint_target_image(target_id: str):
    """Serves the reference portrait image for an OSINT target with anti-caching headers."""
    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0"
    }

    # Explicit guaranteed portrait for Mark Zuckerberg
    if target_id == "target_zuck":
        p = "dataset/presets/zuckerberg.jpg"
        if os.path.exists(p):
            return FileResponse(p, media_type="image/jpeg", headers=headers)

    watchlist = get_watchlist()
    target = next((t for t in watchlist if t["id"] == target_id), None)
    if target and target.get("face_image") and os.path.exists(target["face_image"]) and "alice.jpg" not in target["face_image"]:
        return FileResponse(target["face_image"], media_type="image/jpeg", headers=headers)

    # Specific fallbacks for default targets
    if target_id == "target_satya":
        p = "dataset/presets/satya_nadella.jpg"
        if os.path.exists(p):
            return FileResponse(p, media_type="image/jpeg", headers=headers)
    elif target_id == "target_sundar":
        p = "dataset/presets/sundar_pichai.jpg"
        if os.path.exists(p):
            return FileResponse(p, media_type="image/jpeg", headers=headers)

    raise HTTPException(status_code=404, detail="Target portrait not found")

@app.post("/api/osint/register")
async def register_osint_target(
    name: str = Form(...),
    monitored_urls: str = Form(...),
    file: UploadFile = File(...)
):
    """Registers a new identity into the autonomous surveillance radar."""
    if not name.strip():
        raise HTTPException(status_code=400, detail="Target name is required.")
        
    urls = [u.strip() for u in monitored_urls.replace(",", "\n").splitlines() if u.strip()]
    if not urls:
        raise HTTPException(status_code=400, detail="At least one monitored URL is required.")
        
    target_id = f"target_{int(datetime.now().timestamp())}"
    save_path = f"dataset/watchlist_faces/{target_id}_{file.filename}"
    with open(save_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    target = register_watchlist_target(name.strip(), save_path, urls)
    return {"status": "success", "target": target}

@app.delete("/api/osint/target/{target_id}")
async def delete_osint_target(target_id: str):
    """Removes a target from the surveillance watchlist."""
    deleted = delete_watchlist_target(target_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Target not found in watchlist.")
    return {"status": "success", "deleted_id": target_id}

@app.post("/api/osint/scan/{target_id}")
async def trigger_osint_scan(target_id: str, max_pages: int = Form(10)):
    """Triggers a parallel surveillance crawl across all monitored domains for a target."""
    if not CONTRACT_ADDRESS:
        raise HTTPException(status_code=500, detail="Smart contract registry not deployed.")
    try:
        results = scan_target(target_id, CONTRACT_ADDRESS, CONTRACT_ABI, max_pages=max_pages)
        return {"status": "success", "data": results}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
