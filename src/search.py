import numpy as np
import os
import json
import time
import base64
import requests
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any, List, Union, Generator
from urllib.parse import urlparse
from .face_encode import encode_face
from .scraper import crawl_domain

DEFAULT_MATCH_THRESHOLD = 0.60
DEFAULT_CRAWL_PAGES = 10
DUPLICATE_VECTOR_THRESHOLD = 0.10 # Near-identical face vectors count as duplicate photo instances

def _extract_base_filename(url_or_path: str) -> str:
    """Extracts cleaned base image name to assist URL-based deduplication."""
    clean = url_or_path.split("?")[0].rstrip("/").split("/")[-1].lower()
    # Normalize common Wikipedia thumb prefixes like "220px-" or "300px-"
    if "px-" in clean:
        clean = clean.split("px-", 1)[-1]
    return clean

def stream_search_pipeline(
    query_embedding: np.ndarray,
    target_urls: Union[str, List[Union[str, Dict[str, Any]]]],
    threshold: float = DEFAULT_MATCH_THRESHOLD,
    max_pages: int = DEFAULT_CRAWL_PAGES,
    candidate_items: Optional[List[Dict[str, Any]]] = None
) -> Generator[Dict[str, Any], None, Optional[Dict[str, Any]]]:
    """
    Producer-Consumer Streaming Search Engine with Search-Engine Cached Thumbnail Ingestion:
    - Producer: Ingests public search-engine cached portraits (e.g. Bing CDN thumbnails for LinkedIn/directory profiles)
      without triggering login walls or HTTP 999 blocks; concurrently spiders open web domains.
    - Consumer: Concurrently pulls images from queue, executes 128-D facial vector inference, and detects duplicates.
    - Yields real-time events for live progress and UI streaming.
    """
    url_meta_map: Dict[str, Dict[str, Any]] = {}

    def _register_meta(u_val: str, meta_obj: Dict[str, Any]):
        if not u_val: return
        clean_u = u_val.strip()
        url_meta_map[clean_u] = meta_obj
        url_meta_map[clean_u.rstrip("/")] = meta_obj
        url_meta_map[clean_u.rstrip("/") + "/"] = meta_obj

    # Register candidate metadata if provided
    if candidate_items:
        for c in candidate_items:
            if isinstance(c, dict) and c.get("url"):
                _register_meta(c["url"], c)

    # Parse target_urls (can be JSON string, list of strings, or list of dicts)
    urls: List[str] = []
    if isinstance(target_urls, str):
        trimmed = target_urls.strip()
        if trimmed.startswith("[") and trimmed.endswith("]"):
            try:
                parsed_list = json.loads(trimmed)
                for item in parsed_list:
                    if isinstance(item, dict):
                        u = item.get("url")
                        if u:
                            urls.append(u)
                            _register_meta(u, item)
                    elif isinstance(item, str) and item.strip():
                        urls.append(item.strip())
            except Exception:
                urls = [u.strip() for u in trimmed.splitlines() if u.strip()]
        else:
            urls = [u.strip() for u in trimmed.replace(",", "\n").splitlines() if u.strip()]
    elif isinstance(target_urls, list):
        for item in target_urls:
            if isinstance(item, dict):
                u = item.get("url")
                if u:
                    urls.append(u)
                    _register_meta(u, item)
            elif isinstance(item, str) and item.strip():
                urls.append(item.strip())

    urls = [u for u in urls if u and u.startswith("http")]
    if not urls:
        yield {"type": "error", "message": "No valid target URLs provided."}
        return None

    per_url_pages = max(2, max_pages // max(1, len(urls)))
    image_queue: queue.Queue = queue.Queue(maxsize=150)
    
    total_pages_scanned = 0
    total_images_downloaded = 0
    total_faces_extracted = 0
    matched_candidates: List[Dict[str, Any]] = []
    unique_matches_map: Dict[int, Dict[str, Any]] = {}
    main_page_titles = []
    
    yield {
        "type": "started",
        "target_urls": urls,
        "max_pages": max_pages,
        "message": f"Producer-Consumer pipeline initialized across {len(urls)} domain(s)."
    }

    # Thread-safe synchronization primitives for producer pipeline
    stats_lock = threading.Lock()

    # Background Producer Thread: Concurrently processes search-engine cached portraits
    # and spiders open web domains in parallel without blocking each other.
    def producer_worker():
        nonlocal total_pages_scanned, total_images_downloaded
        os.makedirs("temp", exist_ok=True)
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
            'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8'
        })

        def ingest_cached_thumbnail(idx: int, target_url: str):
            nonlocal total_images_downloaded
            meta = url_meta_map.get(target_url) or url_meta_map.get(target_url.rstrip("/")) or url_meta_map.get(target_url.rstrip("/") + "/") or {}
            sample_image = meta.get("sample_image") or meta.get("fallback_image")
            netloc = urlparse(target_url).netloc.lower()

            # Dynamic on-the-fly thumbnail resolution for direct manual LinkedIn inputs
            if not sample_image and "linkedin.com" in netloc:
                try:
                    from .discovery import search_google_profiles_and_thumbnails, search_bing_images_with_profiles
                    slug = target_url.strip("/").split("/")[-1].replace("-", " ").replace("_", " ")
                    found = search_google_profiles_and_thumbnails(f"{slug} linkedin", max_results=3)
                    if not found or not found[0].get("sample_image"):
                        found = search_bing_images_with_profiles(f"{slug} linkedin", max_results=3)
                    if found and found[0].get("sample_image"):
                        sample_image = found[0]["sample_image"]
                        meta["sample_image"] = sample_image
                        if not meta.get("title") and found[0].get("title"):
                            meta["title"] = found[0]["title"]
                except Exception as ex:
                    print(f"[Searcher Producer] Dynamic thumbnail lookup error for {target_url}: {ex}")

            if not sample_image:
                return

            # 1. Base64 Data URI thumbnail handling (common in Google Search carousels)
            if sample_image.startswith("data:image/") and ";base64," in sample_image:
                try:
                    b64_content = sample_image.split(";base64,")[1]
                    raw_bytes = base64.b64decode(b64_content)
                    if len(raw_bytes) > 400:
                        ext = ".jpg"
                        if "png" in sample_image[:30]: ext = ".png"
                        elif "webp" in sample_image[:30]: ext = ".webp"

                        thumb_file = os.path.join("temp", f"cached_portrait_{int(time.time()*1000)}_{idx}{ext}")
                        with open(thumb_file, "wb") as f:
                            f.write(raw_bytes)

                        with stats_lock:
                            total_images_downloaded += 1
                        print(f"[Searcher Producer] Decoded base64 cached portrait for {target_url[:50]}")
                        image_queue.put((thumb_file, sample_image, target_url))
                except Exception as e:
                    print(f"[Searcher Producer] Base64 decode error for {target_url}: {e}")

            # 2. HTTP CDN thumbnail handling (Google encrypted-tbn0.gstatic.com, Bing ts*.mm.bing.net, etc.)
            elif sample_image.startswith("http"):
                try:
                    resp = session.get(sample_image, timeout=6)
                    if (resp.status_code != 200 or len(resp.content) <= 400) and meta.get("fallback_image") and meta["fallback_image"] != sample_image:
                        resp = session.get(meta["fallback_image"], timeout=6)
                        if resp.status_code == 200 and len(resp.content) > 400:
                            sample_image = meta["fallback_image"]

                    if resp.status_code == 200 and len(resp.content) > 400:
                        content_type = resp.headers.get("content-type", "").lower()
                        ext = ".jpg"
                        if "png" in content_type: ext = ".png"
                        elif "webp" in content_type: ext = ".webp"

                        thumb_file = os.path.join("temp", f"cached_portrait_{int(time.time()*1000)}_{idx}{ext}")
                        with open(thumb_file, "wb") as f:
                            f.write(resp.content)

                        with stats_lock:
                            total_images_downloaded += 1
                        print(f"[Searcher Producer] Ingested search cached portrait for {target_url[:50]}: {sample_image[:60]}")
                        image_queue.put((thumb_file, sample_image, target_url))
                except Exception as e:
                    print(f"[Searcher Producer] Failed to download cached portrait from {sample_image}: {e}")

        def crawl_target_domain(target_url: str):
            nonlocal total_pages_scanned
            netloc = urlparse(target_url).netloc.lower()
            meta = url_meta_map.get(target_url, {})

            # For LinkedIn profiles or walled platforms where cached portrait is ingested:
            # Do NOT scrape internal HTML on linkedin.com to prevent HTTP 999
            if "linkedin.com" in netloc:
                with stats_lock:
                    total_pages_scanned += 1
                    if meta.get("title"):
                        main_page_titles.append(meta["title"])
                return

            try:
                def on_img(item):
                    nonlocal total_images_downloaded
                    with stats_lock:
                        total_images_downloaded += 1
                    image_queue.put(item)

                def on_page(p_url, p_cnt, i_cnt):
                    pass

                imgs, p_title, p_count = crawl_domain(
                    start_url=target_url,
                    max_pages=per_url_pages,
                    on_image_downloaded=on_img,
                    on_page_completed=on_page
                )
                with stats_lock:
                    total_pages_scanned += p_count
                    if p_title:
                        main_page_titles.append(p_title)
            except Exception as e:
                print(f"[Searcher Producer] Error on {target_url}: {e}")

        def run_cache_worker():
            with ThreadPoolExecutor(max_workers=min(6, max(1, len(urls)))) as executor:
                futures = [executor.submit(ingest_cached_thumbnail, idx, u) for idx, u in enumerate(urls)]
                for f in futures:
                    try:
                        f.result()
                    except Exception as ex:
                        print(f"[Searcher Producer] Cached thumbnail error: {ex}")

        def run_domain_crawler_worker():
            with ThreadPoolExecutor(max_workers=min(4, max(1, len(urls)))) as executor:
                futures = [executor.submit(crawl_target_domain, u) for u in urls]
                for f in futures:
                    try:
                        f.result()
                    except Exception as ex:
                        print(f"[Searcher Producer] Domain crawl error: {ex}")

        # Parallelize Cached Portraits and Domain Spidering concurrently!
        cache_thread = threading.Thread(target=run_cache_worker, daemon=True)
        crawl_thread = threading.Thread(target=run_domain_crawler_worker, daemon=True)

        cache_thread.start()
        crawl_thread.start()

        cache_thread.join()
        crawl_thread.join()

        # Sentinel indicating producer finished
        image_queue.put(None)

    producer_thread = threading.Thread(target=producer_worker, daemon=True)
    producer_thread.start()

    # Consumer: In parallel, pulls images from queue and runs facial recognition
    img_processed_count = 0
    
    while True:
        try:
            item = image_queue.get(timeout=0.25)
        except queue.Empty:
            if not producer_thread.is_alive():
                break
            continue

        if item is None:
            # Sentinel received, producer finished
            break

        filepath, live_image_url, page_found_on = item
        img_processed_count += 1

        emb = None
        try:
            emb = encode_face(filepath)
        except Exception:
            pass

        # Cleanup disk file immediately
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception:
                pass

        if emb is None:
            yield {
                "type": "progress",
                "images_scanned": img_processed_count,
                "faces_detected": total_faces_extracted,
                "matches_count": len(matched_candidates),
                "pages_crawled": total_pages_scanned or 1
            }
            continue

        total_faces_extracted += 1
        distance = float(np.linalg.norm(query_embedding - emb))
        is_match = distance <= threshold

        if is_match:
            base_name = _extract_base_filename(live_image_url)
            is_duplicate = False
            duplicate_parent_idx = None

            # Deduplication Engine: Detect identical face vectors or duplicate images
            for prev_idx, prev_match in unique_matches_map.items():
                prev_emb = prev_match["_embedding"]
                vec_diff = float(np.linalg.norm(emb - prev_emb))
                prev_base_name = _extract_base_filename(prev_match["image_url"])
                
                if vec_diff <= DUPLICATE_VECTOR_THRESHOLD or (base_name and base_name == prev_base_name):
                    is_duplicate = True
                    duplicate_parent_idx = prev_idx
                    prev_match["duplicate_count"] += 1
                    if page_found_on not in prev_match["seen_on_pages"]:
                        prev_match["seen_on_pages"].append(page_found_on)
                    break

            match_id = len(matched_candidates) + 1
            match_item = {
                "match_index": match_id,
                "image_url": live_image_url,
                "page_url": page_found_on,
                "distance": round(distance, 4),
                "similarity_score": round(1.0 - (distance / 1.0), 4),
                "confidence_percent": round(max(0.0, (1.0 - (distance / threshold))) * 100, 1),
                "is_duplicate": is_duplicate,
                "duplicate_of": duplicate_parent_idx,
                "duplicate_count": 1,
                "seen_on_pages": [page_found_on],
                "_embedding": emb
            }

            if not is_duplicate:
                unique_matches_map[match_id] = match_item

            matched_candidates.append(match_item)

            print(f"[Searcher Consumer] Verified Match #{match_id}! Sim: {match_item['similarity_score']*100:.1f}% (Dupe: {is_duplicate}) on {page_found_on[:50]}")

            # Stream live match event
            yield {
                "type": "match_found",
                "match": {k: v for k, v in match_item.items() if k != "_embedding"},
                "matches_count": len(matched_candidates),
                "unique_matches_count": len(unique_matches_map),
                "images_scanned": img_processed_count,
                "faces_detected": total_faces_extracted,
                "pages_crawled": total_pages_scanned or 1
            }
        else:
            yield {
                "type": "progress",
                "images_scanned": img_processed_count,
                "faces_detected": total_faces_extracted,
                "matches_count": len(matched_candidates),
                "pages_crawled": total_pages_scanned or 1
            }

    # Ensure background worker joined
    producer_thread.join(timeout=2.0)

    # Sort matches by closest distance
    matched_candidates.sort(key=lambda m: m["distance"])
    clean_matches = [{k: v for k, v in m.items() if k != "_embedding"} for m in matched_candidates]

    overall_title = main_page_titles[0] if main_page_titles else urls[0]

    result = None
    if matched_candidates:
        best = matched_candidates[0]
        duplicates_count = len(matched_candidates) - len(unique_matches_map)
        result = {
            "post_id": f"web_{abs(hash(best['image_url']))}",
            "username": urlparse(urls[0]).netloc,
            "platform": "live-web-crawler",
            "image_path": best["image_url"],
            "caption": overall_title,
            "timestamp": best["page_url"],
            "similarity_score": best["similarity_score"],
            "distance": best["distance"],
            "pages_crawled": total_pages_scanned or len(urls),
            "images_scanned": img_processed_count,
            "faces_detected": total_faces_extracted,
            "matches_count": len(matched_candidates),
            "unique_matches_count": len(unique_matches_map),
            "duplicates_count": duplicates_count,
            "all_matches": clean_matches,
            "matched_samples": [m["image_url"] for m in clean_matches[:5]]
        }

    yield {
        "type": "complete",
        "result": result,
        "pages_crawled": total_pages_scanned or len(urls),
        "images_scanned": img_processed_count,
        "faces_detected": total_faces_extracted,
        "matches_count": len(matched_candidates),
        "unique_matches_count": len(unique_matches_map),
        "duplicates_count": len(matched_candidates) - len(unique_matches_map)
    }

    return result

def find_best_match_web(
    query_vec: np.ndarray,
    target_urls: Union[str, List[Union[str, Dict[str, Any]]]],
    threshold: float = DEFAULT_MATCH_THRESHOLD,
    max_pages: int = DEFAULT_CRAWL_PAGES,
    candidate_items: Optional[List[Dict[str, Any]]] = None
) -> Optional[Dict[str, Any]]:
    """
    Synchronous wrapper over the Producer-Consumer search pipeline.
    Runs the streaming generator to completion and returns the final aggregate match result.
    """
    generator = stream_search_pipeline(query_vec, target_urls, threshold, max_pages, candidate_items=candidate_items)
    final_result = None
    for event in generator:
        if event.get("type") == "complete":
            final_result = event.get("result")
    return final_result
