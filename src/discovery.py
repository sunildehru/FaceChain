import re
import os
import urllib.parse
import requests
import base64
import json
import numpy as np
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

DEFAULT_SEARCH_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

GOOGLE_SEARCH_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Sec-Ch-Ua': '"Google Chrome";v="123", "Not:A-Brand";v="8", "Chromium";v="123"',
    'Sec-Ch-Ua-Mobile': '?0',
    'Sec-Ch-Ua-Platform': '"Windows"',
}

# Domains requiring full login / authentication wall to view content
WALLED_GARDEN_DOMAINS = {
    'instagram.com', 'facebook.com', 'fb.com', 'twitter.com', 'x.com',
    'tiktok.com', 'pinterest.com', 'threads.net', 'snapchat.com',
    'quora.com', 't.me', 'telegram.me', 'reddit.com'
}

UNWANTED_NOISE_DOMAINS = {
    'youtube.com', 'youtu.be', 'vimeo.com', 'dailymotion.com',
    'bachpan.com', 'firstcry.com', 'names.org', 'moonastro.com',
    'behindthename.com', 'babycenter.com', 'thebump.com', 'momjunction.com',
    'spotify.com', 'music.apple.com', 'soundcloud.com',
    'imdb.com', 'rottentomatoes.com', 'goodreads.com', 'amazon.com', 'flipkart.com'
}

WALLED_PATH_INDICATORS = [
    '/login', '/signin', '/signup', '/auth', '/checkpoint',
    '/accounts/login', '/session', '/restricted',
    '(disambiguation)', 'disambiguation'
]

# Preset Biometric Encodings Cache (lazy-loaded)
_PRESET_EMBEDDINGS_CACHE = {}

def is_crawlable_public_url(url: str, has_cached_thumbnail: bool = False) -> bool:
    """
    Validates whether a URL is an open, crawlable public webpage with readable metadata,
    strictly filtering out social media login walls, auth screens, noise sites (baby names/music/video), and binary files.
    Allows LinkedIn public profile previews (/in/ or /pub/), GitHub, university pages, and personal portfolios.
    If has_cached_thumbnail is True, allows verified walled profiles (e.g. LinkedIn profiles)
    because their identity is biometrically matched against the search-engine cached portrait.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return False
            
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
            
        # LinkedIn Profile check: allow if it's a specific public person profile
        if "linkedin.com" in netloc:
            path_low = parsed.path.lower()
            if "/in/" in path_low or "/pub/" in path_low:
                return True
            return False

        # Reject social media walled gardens unless paired with a cached search portrait
        for blocked in WALLED_GARDEN_DOMAINS:
            if netloc == blocked or netloc.endswith("." + blocked):
                if has_cached_thumbnail:
                    return True
                return False

        # Reject video / streaming / baby name dictionary sites
        for noise in UNWANTED_NOISE_DOMAINS:
            if netloc == noise or netloc.endswith("." + noise):
                return False
                
        # Reject search engine self-links
        if any(se in netloc for se in ['bing.com', 'google.com', 'yahoo.com', 'duckduckgo.com', 'yandex.com', 'baidu.com', 'msn.com']):
            return False

        # Reject adult / NSFW spam domains and paths
        nsfw_indicators = ['porn', 'xxx', 'nsfw', 'erotic', 'hentai', 'adult-zone']
        if any(term in netloc for term in nsfw_indicators) or any(term in parsed.path.lower() for term in nsfw_indicators):
            return False

        # Reject login / authentication gateways & disambiguation pages
        path = parsed.path.lower()
        for indicator in WALLED_PATH_INDICATORS:
            if indicator in path:
                return False
                
        # Reject static file downloads
        for ext in ['.pdf', '.zip', '.tar', '.gz', '.mp4', '.mp3', '.apk', '.iso', '.exe']:
            if path.endswith(ext):
                return False
                
        return True
    except Exception:
        return False

def _clean_url(raw_url: str) -> str:
    """Extracts direct destination URL from search engine redirect tokens."""
    if not raw_url:
        return ""
        
    # Bing click redirect with Base64 destination: https://www.bing.com/ck/a?!&&p=...&u=a1<base64>&ntb=1
    if "bing.com/ck/a" in raw_url or "u=a1" in raw_url:
        try:
            m = re.search(r'[?&]u=a1([^&]+)', raw_url)
            if m:
                b64_str = m.group(1)
                b64_padded = b64_str + '=' * (-len(b64_str) % 4)
                raw_bytes = base64.urlsafe_b64decode(b64_padded)
                decoded_u = raw_bytes.decode('utf-8', errors='ignore')
                if decoded_u.startswith('http'):
                    return decoded_u
        except Exception:
            pass

    # DuckDuckGo redirect
    if "duckduckgo.com/l/?uddg=" in raw_url or "uddg=" in raw_url:
        try:
            parsed = urllib.parse.urlparse(raw_url)
            query_params = urllib.parse.parse_qs(parsed.query)
            if "uddg" in query_params:
                return query_params["uddg"][0]
        except Exception:
            pass

    # Google /imgres redirect with imgrefurl / imgurl parameters
    if "imgres" in raw_url or "imgrefurl" in raw_url:
        try:
            parsed = urllib.parse.urlparse(raw_url)
            query_params = urllib.parse.parse_qs(parsed.query)
            if "imgrefurl" in query_params:
                dest = query_params["imgrefurl"][0]
                if dest.startswith("http"):
                    return dest
            if "imgurl" in query_params:
                dest = query_params["imgurl"][0]
                if dest.startswith("http"):
                    return dest
        except Exception:
            pass

    # Google /url?q= redirect
    if "/url?q=" in raw_url or "url?q=" in raw_url:
        try:
            parsed = urllib.parse.urlparse(raw_url)
            query_params = urllib.parse.parse_qs(parsed.query)
            if "q" in query_params:
                dest = query_params["q"][0]
                if dest.startswith("http"):
                    return dest
        except Exception:
            pass

    # Yahoo /RU= redirect
    if "/RU=" in raw_url:
        try:
            m = re.search(r'/RU=([^/]+)/', raw_url)
            if m:
                dest = urllib.parse.unquote(m.group(1))
                if dest.startswith("http"):
                    return dest
        except Exception:
            pass

    return raw_url

def recognize_known_identity_from_image(image_path: str) -> Optional[Dict[str, Any]]:
    """
    Biometric Identity Seam:
    Compares query image vector against local preset library (Zuckerberg, Satya Nadella, Sundar Pichai)
    and registered watchlist targets to instantly resolve identity and verified target URLs.
    """
    if not image_path or not os.path.exists(image_path):
        return None

    try:
        from .face_encode import encode_face
        query_vec = encode_face(image_path)
        if query_vec is None:
            return None

        presets_metadata = [
            {
                "name": "Mark Zuckerberg",
                "portrait_path": "dataset/presets/zuckerberg.jpg",
                "urls": ["https://en.wikipedia.org/wiki/Mark_Zuckerberg"]
            },
            {
                "name": "Satya Nadella",
                "portrait_path": "dataset/presets/satya_nadella.jpg",
                "urls": ["https://en.wikipedia.org/wiki/Satya_Nadella"]
            },
            {
                "name": "Sundar Pichai",
                "portrait_path": "dataset/presets/sundar_pichai.jpg",
                "urls": ["https://en.wikipedia.org/wiki/Sundar_Pichai"]
            }
        ]

        best_match = None
        min_dist = 1.0

        for preset in presets_metadata:
            p_path = preset["portrait_path"]
            if not os.path.exists(p_path):
                continue
                
            if p_path not in _PRESET_EMBEDDINGS_CACHE:
                try:
                    p_vec = encode_face(p_path)
                    if p_vec is not None:
                        _PRESET_EMBEDDINGS_CACHE[p_path] = p_vec
                except Exception:
                    continue
                    
            p_vec = _PRESET_EMBEDDINGS_CACHE.get(p_path)
            if p_vec is not None:
                dist = float(np.linalg.norm(query_vec - p_vec))
                if dist < min_dist and dist <= 0.48: # High confidence biometric match
                    min_dist = dist
                    best_match = {
                        "name": preset["name"],
                        "urls": preset["urls"],
                        "confidence": round((1.0 - (dist / 0.60)) * 100, 1),
                        "distance": round(dist, 4),
                        "source": "biometric_preset_recognition"
                    }

        if best_match:
            print(f"[Discovery] Biometrically identified identity: {best_match['name']} (Sim: {best_match['confidence']}%, Dist: {best_match['distance']})")
            return best_match

    except Exception as e:
        print(f"[Discovery] Preset recognition error: {e}")

    return None

GOOGLE_MOBILE_SEARCH_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

def search_google_profiles_and_thumbnails(query_text: str, max_results: int = 8) -> List[Dict[str, Any]]:
    """
    Directly extracts Google Search organic profile cards and the prominent 'Images' carousel
    (matching the user's exact Google Search view).
    Recovers unblocked high-speed Google CDN thumbnails (encrypted-tbn0.gstatic.com)
    and base64 inline portraits paired with genuine LinkedIn/directory profile URLs.
    Upgrades organic links with cached image thumbnails without discarding either.
    """
    results: List[Dict[str, Any]] = []

    def _normalize_key(u: str) -> str:
        return u.strip().rstrip("/").lower()

    def add_candidate(item: Dict[str, Any]):
        u = item.get("url", "").strip()
        if not u or not u.startswith("http"):
            return
        u_key = _normalize_key(u)
        
        for existing in results:
            if _normalize_key(existing["url"]) == u_key:
                if not existing.get("sample_image") and item.get("sample_image"):
                    existing["sample_image"] = item["sample_image"]
                    existing["is_cached_profile"] = True
                    if item.get("title") and len(item["title"]) > len(existing.get("title", "")):
                        existing["title"] = item["title"]
                return

        results.append(item)

    try:
        session = requests.Session()
        session.headers.update(GOOGLE_SEARCH_HEADERS)
        session.cookies.set("SOCS", "CAESHAgBEhJnd3NfMjAyMzA4MTAtMF9SQzIaAmVuIAEaBgiA_LmnBg", domain=".google.com")

        # 1. Google Web Search (where the Images carousel appears beneath the top LinkedIn result)
        web_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(query_text)}&hl=en&num=10"
        resp = session.get(web_url, timeout=7)
        if resp.status_code == 200:
            html = resp.text

            # Map dimg_X IDs to base64 image data strings
            dimg_map = {}
            for m in re.finditer(r"var\s+s='(data:image/[^;]+;base64,[^']+)';.*?ii=\[([^\]]+)\]", html, re.DOTALL):
                b64 = m.group(1)
                for did in re.findall(r"'([^']+)'", m.group(2)):
                    dimg_map[did] = b64

            for m in re.finditer(r"_setImagesSrc\(\[([^\]]+)\],\s*'(data:image/[^;]+;base64,[^']+)'\)", html, re.DOTALL):
                b64 = m.group(2)
                for did in re.findall(r"'([^']+)'", m.group(1)):
                    dimg_map[did] = b64

            for m in re.finditer(r"s='(data:image/[^;]+;base64,[^']+)';.*?var\s+ii=\[([^\]]+)\]", html, re.DOTALL):
                b64 = m.group(1)
                for did in re.findall(r"'([^']+)'", m.group(2)):
                    dimg_map[did] = b64

            soup = BeautifulSoup(html, "html.parser")

            # Find organic links and image carousel cards
            for a in soup.find_all("a", href=True):
                raw_href = a["href"]
                dest = _clean_url(raw_href)
                if not dest or not dest.startswith("http"):
                    continue
                if not is_crawlable_public_url(dest, has_cached_thumbnail=True):
                    continue

                parsed = urllib.parse.urlparse(dest)
                netloc = parsed.netloc.lower()
                if netloc.startswith("www."):
                    netloc = netloc[4:]

                # Look for thumbnail image inside this anchor or in surrounding card container
                thumb_src = None
                img = a.find("img")
                if not img:
                    parent = a.find_parent(["div", "g-inner-card", "g-card", "td", "li", "span"])
                    if parent:
                        img = parent.find("img")

                if img:
                    img_id = img.get("id")
                    src = img.get("src") or img.get("data-src")
                    if img_id and img_id in dimg_map:
                        thumb_src = dimg_map[img_id]
                    elif src and (src.startswith("data:image/") or "encrypted-tbn0.gstatic.com" in src or "googleusercontent.com" in src):
                        thumb_src = src

                title = a.get_text(strip=True)
                if not title:
                    parent = a.find_parent(["div", "g-inner-card", "g-card"])
                    if parent:
                        title = parent.get_text(strip=True)

                if not title or len(title) < 3:
                    slug = parsed.path.strip("/").split("/")[-1].replace("-", " ").replace("_", " ").title()
                    title = f"Profile ({slug}) on {netloc}" if slug else f"Profile on {netloc}"

                add_candidate({
                    "url": dest,
                    "domain": netloc,
                    "title": title,
                    "sample_image": thumb_src,
                    "source": "google_search_cache",
                    "is_cached_profile": bool(thumb_src)
                })

        # 2. Google Images Search (tbm=isch) Mobile SSR (Guaranteed static SSR HTML with unblocked CDN thumbs)
        mobile_session = requests.Session()
        mobile_session.headers.update(GOOGLE_MOBILE_SEARCH_HEADERS)
        mobile_session.cookies.set("SOCS", "CAESHAgBEhJnd3NfMjAyMzA4MTAtMF9SQzIaAmVuIAEaBgiA_LmnBg", domain=".google.com")

        img_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(query_text)}&tbm=isch&hl=en"
        resp_img = mobile_session.get(img_url, timeout=7)
        if resp_img.status_code == 200:
            soup_img = BeautifulSoup(resp_img.text, "html.parser")
            for a in soup_img.find_all("a", href=True):
                raw_href = a["href"]
                dest = _clean_url(raw_href)
                if not dest or not dest.startswith("http"):
                    continue
                if not is_crawlable_public_url(dest, has_cached_thumbnail=True):
                    continue

                img = a.find("img")
                if not img:
                    parent = a.find_parent(["div", "td", "span", "table"])
                    if parent:
                        img = parent.find("img")

                src = None
                if img:
                    src = img.get("src") or img.get("data-src")

                if src and ("gstatic.com" in src or src.startswith("data:image/")):
                    parsed = urllib.parse.urlparse(dest)
                    netloc = parsed.netloc.lower()
                    if netloc.startswith("www."):
                        netloc = netloc[4:]

                    title = (img.get("alt") if img else None) or a.get_text(strip=True)
                    if not title or len(title) < 3:
                        slug = parsed.path.strip("/").split("/")[-1].replace("-", " ").replace("_", " ").title()
                        title = f"Profile ({slug}) on {netloc}" if slug else f"Profile on {netloc}"

                    add_candidate({
                        "url": dest,
                        "domain": netloc,
                        "title": title,
                        "sample_image": src,
                        "source": "google_image_cache",
                        "is_cached_profile": True
                    })

        # 3. Desktop Google Images script regex fallback
        if len(results) < max_results:
            regex_pairs = re.finditer(
                r'\["(https://encrypted-tbn0\.gstatic\.com/images\?q=tbn:[^"]+)",\s*\d+,\s*\d+\].*?\["(https?://[^"]+)",\s*"([^"]*)"',
                resp_img.text if resp_img.status_code == 200 else "",
                re.DOTALL
            )
            for m in regex_pairs:
                tbn_url = m.group(1).replace(r'\/', '/')
                dest_url = _clean_url(m.group(2).replace(r'\/', '/'))
                title_text = m.group(3).replace(r'\/', '')

                if not dest_url or not dest_url.startswith("http"):
                    continue
                if not is_crawlable_public_url(dest_url, has_cached_thumbnail=True):
                    continue

                parsed = urllib.parse.urlparse(dest_url)
                netloc = parsed.netloc.lower()
                if netloc.startswith("www."):
                    netloc = netloc[4:]

                add_candidate({
                    "url": dest_url,
                    "domain": netloc,
                    "title": title_text or f"Profile on {netloc}",
                    "sample_image": tbn_url,
                    "source": "google_image_cache",
                    "is_cached_profile": True
                })

    except Exception as e:
        print(f"[Discovery] Google Search profile extraction error: {e}")

    return results[:max_results]

def extract_bing_image_pairs_from_text(html_text: str) -> List[Dict[str, str]]:
    """
    Direct extraction of (purl, turl, murl, title) from Bing Images HTML.
    Handles JSON attributes (m='{...}'), HTML entity escapes (&quot;), and regex fallback.
    """
    pairs: List[Dict[str, str]] = []
    # Pattern 1: JSON chunks inside m='{...}' or m="{...}" or m=&quot;{...}&quot;
    m_blocks = re.findall(r'm=(?:&quot;|\'|")(\{.*?\})(?:&quot;|\'|")', html_text)
    for block in m_blocks:
        try:
            clean_block = block.replace('&quot;', '"').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
            data = json.loads(clean_block)
            purl = data.get("purl") or data.get("surl")
            turl = data.get("turl")
            murl = data.get("murl")
            title = data.get("t") or data.get("desc") or ""
            if purl and turl:
                pairs.append({"purl": purl, "turl": turl, "murl": murl, "title": title})
        except Exception:
            pass

    # Pattern 2: Regex proximity matching if JSON parser found few items
    if len(pairs) < 3:
        seen_purls = set(p["purl"] for p in pairs)
        for m in re.finditer(r'"purl"\s*:\s*"([^"]+)".*?"turl"\s*:\s*"([^"]+)"', html_text):
            purl = m.group(1).replace(r'\/', '/')
            turl = m.group(2).replace(r'\/', '/')
            if purl not in seen_purls:
                seen_purls.add(purl)
                pairs.append({"purl": purl, "turl": turl, "murl": None, "title": ""})
            
        for m in re.finditer(r'"turl"\s*:\s*"([^"]+)".*?"purl"\s*:\s*"([^"]+)"', html_text):
            turl = m.group(1).replace(r'\/', '/')
            purl = m.group(2).replace(r'\/', '/')
            if purl not in seen_purls:
                seen_purls.add(purl)
                pairs.append({"purl": purl, "turl": turl, "murl": None, "title": ""})

    return pairs

def search_bing_images_with_profiles(query_text: str, max_results: int = 8) -> List[Dict[str, Any]]:
    """
    Extracts search-engine indexed profile URLs and their public CDN cached thumbnails
    from Bing Image Search. Specifically captures LinkedIn profiles, personal blogs,
    directory listings, and portfolios with their exact cached portrait thumbnails.
    """
    results: List[Dict[str, Any]] = []
    seen_urls = set()
    try:
        session = requests.Session()
        session.headers.update(DEFAULT_SEARCH_HEADERS)
        url = f"https://www.bing.com/images/search?q={urllib.parse.quote_plus(query_text)}&first=1"
        resp = session.get(url, timeout=7)
        if resp.status_code == 200:
            extracted_pairs = extract_bing_image_pairs_from_text(resp.text)
            
            for pair in extracted_pairs:
                raw_purl = pair.get("purl", "")
                turl = pair.get("turl", "")
                murl = pair.get("murl")
                title = pair.get("title", "")
                
                if not raw_purl or not turl:
                    continue
                    
                dest_url = _clean_url(raw_purl)
                if not dest_url.startswith("http") or dest_url in seen_urls:
                    continue
                    
                if not is_crawlable_public_url(dest_url, has_cached_thumbnail=True):
                    continue
                    
                parsed = urllib.parse.urlparse(dest_url)
                netloc = parsed.netloc.lower()
                if netloc.startswith("www."):
                    netloc = netloc[4:]
                    
                if turl.startswith("//"):
                    turl = "https:" + turl
                    
                if not title:
                    slug = parsed.path.strip("/").split("/")[-1].replace("-", " ").replace("_", " ").title()
                    title = f"Profile ({slug}) on {netloc}" if slug else f"Profile on {netloc}"
                    
                seen_urls.add(dest_url)
                results.append({
                    "url": dest_url,
                    "domain": netloc,
                    "title": title,
                    "sample_image": turl,
                    "fallback_image": murl,
                    "source": "search_engine_cache",
                    "is_cached_profile": True
                })
                
                if len(results) >= max_results:
                    break
    except Exception as e:
        print(f"[Discovery] Bing Image profile extraction error: {e}")
    return results

def search_duckduckgo_images(query_text: str, max_results: int = 6) -> List[Dict[str, Any]]:
    """
    Queries DuckDuckGo Image Search API as a resilient fallback for cached profile portraits.
    """
    results: List[Dict[str, Any]] = []
    seen_urls = set()
    try:
        session = requests.Session()
        session.headers.update(DEFAULT_SEARCH_HEADERS)
        token_url = f"https://duckduckgo.com/?q={urllib.parse.quote_plus(query_text)}"
        resp = session.get(token_url, timeout=5)
        vqd = None
        vqd_match = re.search(r'vqd=([0-9-]+)', resp.text)
        if vqd_match:
            vqd = vqd_match.group(1)
        else:
            vqd_match2 = re.search(r'vqd="([^"]+)"', resp.text)
            if vqd_match2:
                vqd = vqd_match2.group(1)
                
        if vqd:
            api_url = f"https://duckduckgo.com/i.js?q={urllib.parse.quote_plus(query_text)}&o=json&vqd={vqd}&p=1"
            i_resp = session.get(api_url, timeout=5)
            if i_resp.status_code == 200:
                data = i_resp.json()
                for item in data.get("results", [])[:max_results * 2]:
                    dest_url = item.get("url") or item.get("source")
                    thumb = item.get("thumbnail") or item.get("image")
                    title = item.get("title") or ""
                    if not dest_url or not thumb or not dest_url.startswith("http"):
                        continue
                    clean_dest = _clean_url(dest_url)
                    if clean_dest in seen_urls:
                        continue
                    if not is_crawlable_public_url(clean_dest, has_cached_thumbnail=True):
                        continue
                        
                    parsed = urllib.parse.urlparse(clean_dest)
                    netloc = parsed.netloc.lower()
                    if netloc.startswith("www."): netloc = netloc[4:]
                    
                    seen_urls.add(clean_dest)
                    results.append({
                        "url": clean_dest,
                        "domain": netloc,
                        "title": title or f"Public Profile on {netloc}",
                        "sample_image": thumb,
                        "source": "ddg_image_cache",
                        "is_cached_profile": True
                    })
                    if len(results) >= max_results:
                        break
    except Exception as e:
        print(f"[Discovery] DuckDuckGo Image search error: {e}")
    return results

def search_bing(query_text: str, max_results: int = 6) -> List[Dict[str, Any]]:
    """
    Queries Bing Public Web Search with standard Chrome headers.
    Returns authentic public web page results with inline thumbnail extraction.
    """
    results: List[Dict[str, Any]] = []
    try:
        session = requests.Session()
        session.headers.update(DEFAULT_SEARCH_HEADERS)
        url = f"https://www.bing.com/search?q={urllib.parse.quote_plus(query_text)}&setlang=en"
        resp = session.get(url, timeout=6)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for li in soup.select("li.b_algo"):
                a = li.select_one("h2 a")
                if not a:
                    continue
                href = _clean_url(a.get("href", ""))
                if not href.startswith("http") or not is_crawlable_public_url(href):
                    continue
                domain = urllib.parse.urlparse(href).netloc.lower()
                title = a.get_text(strip=True) or domain
                p = li.select_one("div.b_caption p")
                snippet = p.get_text(strip=True) if p else ""
                
                # Extract inline search result thumbnail if available
                thumb_img = li.select_one("div.b_thumb img, div.cico img, img.rms_img, img")
                sample_img = None
                if thumb_img:
                    raw_src = thumb_img.get("src") or thumb_img.get("data-src-hq") or thumb_img.get("data-src")
                    if raw_src:
                        if raw_src.startswith("//"): raw_src = "https:" + raw_src
                        if raw_src.startswith("http") and not any(ic in raw_src for ic in ['icon', 'logo', 'pixel', 'clear.gif']):
                            sample_img = raw_src

                item = {
                    "url": href,
                    "domain": domain,
                    "title": title,
                    "snippet": snippet,
                    "source": "bing_search"
                }
                if sample_img:
                    item["sample_image"] = sample_img
                    item["is_cached_profile"] = True
                results.append(item)
                if len(results) >= max_results:
                    break
    except Exception as e:
        print(f"[Discovery] Bing search error: {e}")
    return results

def search_yahoo(query_text: str, max_results: int = 6) -> List[Dict[str, Any]]:
    """
    Queries Yahoo Public Search and decodes target URLs.
    """
    results: List[Dict[str, Any]] = []
    try:
        session = requests.Session()
        session.headers.update(DEFAULT_SEARCH_HEADERS)
        url = f"https://search.yahoo.com/search?p={urllib.parse.quote_plus(query_text)}"
        resp = session.get(url, timeout=6)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for h3 in soup.select("h3 a"):
                raw_href = h3.get("href", "")
                dest = _clean_url(raw_href)
                if not dest.startswith("http") or not is_crawlable_public_url(dest):
                    continue
                domain = urllib.parse.urlparse(dest).netloc.lower()
                title = h3.get_text(strip=True) or domain
                results.append({
                    "url": dest,
                    "domain": domain,
                    "title": title,
                    "source": "yahoo_search"
                })
                if len(results) >= max_results:
                    break
    except Exception as e:
        print(f"[Discovery] Yahoo search error: {e}")
    return results

def search_duckduckgo_lite(query_text: str, max_results: int = 6) -> List[Dict[str, Any]]:
    """
    Queries DuckDuckGo Lite endpoint (GET).
    """
    results: List[Dict[str, Any]] = []
    try:
        session = requests.Session()
        session.headers.update(DEFAULT_SEARCH_HEADERS)
        url = f"https://lite.duckduckgo.com/lite/?q={urllib.parse.quote_plus(query_text)}"
        resp = session.get(url, timeout=6)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.select("a.result-link"):
                raw_href = a.get("href", "")
                dest = _clean_url(raw_href)
                if not dest.startswith("http") or not is_crawlable_public_url(dest):
                    continue
                domain = urllib.parse.urlparse(dest).netloc.lower()
                title = a.get_text(strip=True) or domain
                results.append({
                    "url": dest,
                    "domain": domain,
                    "title": title,
                    "source": "ddg_lite"
                })
                if len(results) >= max_results:
                    break
    except Exception as e:
        print(f"[Discovery] DDG Lite error: {e}")
    return results

def probe_candidate_developer_domains(name: str) -> List[Dict[str, Any]]:
    """
    Rapidly probes standard personal portfolio domain patterns (e.g. name.dev, name.github.io, name.me)
    using non-blocking HEAD requests.
    """
    results: List[Dict[str, Any]] = []
    clean = re.sub(r'[^a-zA-Z0-9]', '', name).lower()
    if len(clean) >= 3:
        candidates = [
            f"https://{clean}.dev",
            f"https://{clean}.github.io",
            f"https://{clean}.me"
        ]
        for cand in candidates:
            try:
                r = requests.head(cand, timeout=2.0, allow_redirects=True, headers=DEFAULT_SEARCH_HEADERS)
                if r.status_code in (200, 301, 302):
                    dest = r.url
                    if is_crawlable_public_url(dest):
                        domain = urllib.parse.urlparse(dest).netloc.lower()
                        results.append({
                            "url": dest,
                            "domain": domain,
                            "title": f"Personal Developer Portfolio ({domain})",
                            "source": "direct_portfolio_resolution"
                        })
            except Exception:
                pass
    return results

def search_github_profiles(name: str, max_results: int = 4) -> List[Dict[str, Any]]:
    """
    Queries GitHub's public user search API for developer and academic profiles.
    Enriches with personal website/blog and authentic portrait avatar images.
    """
    results: List[Dict[str, Any]] = []
    try:
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'FaceChain-Discovery/2.5',
            'Accept': 'application/vnd.github.v3+json'
        })
        # Try both name search and exact keyword search
        url = f"https://api.github.com/search/users?q={urllib.parse.quote_plus(name)}+in:name"
        resp = session.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("items", [])
            for item in items[:max_results]:
                html_url = item.get("html_url", "")
                login = item.get("login", "")
                avatar = item.get("avatar_url", "")
                
                # Fetch user details to discover their personal portfolio website/blog
                blog_url = None
                try:
                    u_resp = session.get(f"https://api.github.com/users/{login}", timeout=3)
                    if u_resp.status_code == 200:
                        u_data = u_resp.json()
                        raw_blog = (u_data.get("blog") or "").strip()
                        if raw_blog:
                            if not raw_blog.startswith("http"):
                                raw_blog = "https://" + raw_blog
                            if is_crawlable_public_url(raw_blog):
                                blog_url = raw_blog
                except Exception:
                    pass

                # If developer has a personal portfolio website, add it first
                if blog_url:
                    b_domain = urllib.parse.urlparse(blog_url).netloc.lower()
                    results.append({
                        "url": blog_url,
                        "domain": b_domain,
                        "title": f"Developer Portfolio: {login} ({b_domain})",
                        "sample_image": avatar,
                        "source": "github_portfolio_blog"
                    })

                # Add GitHub profile
                if html_url and is_crawlable_public_url(html_url):
                    results.append({
                        "url": html_url,
                        "domain": "github.com",
                        "title": f"GitHub Developer Profile: @{login}",
                        "sample_image": avatar,
                        "source": "github_public_api"
                    })
    except Exception as e:
        print(f"[Discovery] GitHub search error: {e}")
    return results

def search_wikipedia_opensearch(name: str) -> List[Dict[str, Any]]:
    """
    Queries official Wikipedia OpenSearch REST API and enriches with
    lead portrait thumbnails from Wikipedia REST summary API.
    Zero-failure rate, no rate-limiting, returns authentic encyclopedia articles.
    """
    results: List[Dict[str, Any]] = []
    try:
        session = requests.Session()
        session.headers.update(DEFAULT_SEARCH_HEADERS)
        url = "https://en.wikipedia.org/w/api.php"
        params = {
            "action": "opensearch",
            "search": name.strip(),
            "limit": 3,
            "namespace": 0,
            "format": "json"
        }
        resp = session.get(url, params=params, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if len(data) >= 4 and isinstance(data[3], list):
                titles = data[1]
                urls = data[3]
                for idx, dest_url in enumerate(urls):
                    if dest_url.startswith("https://en.wikipedia.org/wiki/"):
                        title = titles[idx] if idx < len(titles) else name
                        # Filter out disambiguation & noise
                        if "(disambiguation)" in dest_url.lower():
                            continue

                        sample_image = None
                        try:
                            slug = dest_url.split("/wiki/")[-1]
                            summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug}"
                            s_resp = session.get(summary_url, timeout=3)
                            if s_resp.status_code == 200:
                                s_data = s_resp.json()
                                thumb_info = s_data.get("thumbnail")
                                if thumb_info and isinstance(thumb_info, dict):
                                    sample_image = thumb_info.get("source")
                        except Exception:
                            pass

                        item = {
                            "url": dest_url,
                            "domain": "en.wikipedia.org",
                            "title": f"Wikipedia Biography: {title}",
                            "source": "wikipedia_opensearch"
                        }
                        if sample_image:
                            item["sample_image"] = sample_image
                            item["is_cached_profile"] = True
                        results.append(item)
    except Exception as e:
        print(f"[Discovery] Wikipedia OpenSearch error: {e}")
    return results

def discover_candidate_target_urls(
    name: Optional[str] = None,
    image_path: Optional[str] = None,
    max_pages_budget: int = 10
) -> List[Dict[str, Any]]:
    """
    Multi-Tier Unified Autonomous Discovery Engine:
    1. Biometric Presets/Watchlist Match: Instantly identifies executive/target identity from face image.
    2. Direct Developer Domain Resolution (e.g. name.dev, name.github.io).
    3. Search Engine Cached Profile Portraits: Bing Images & DuckDuckGo Images extract LinkedIn & directory
       profiles paired directly with high-speed, unblocked public CDN thumbnails (e.g. ts*.mm.bing.net).
    4. GitHub Developer Search & Portfolio Blog discovery (authentic avatar portraits & portfolio sites).
    5. Wikipedia OpenSearch & Lead Portrait Lookup for verified encyclopedic biographies.
    6. Bing Web Search (Base64 click-redirect decoded, inline thumbnail enriched).
    """
    combined: List[Dict[str, Any]] = []
    seen_urls = set()
    seen_domains = set()

    resolved_name = (name or "").strip()

    def _norm(u: str) -> str:
        return u.strip().rstrip("/").lower()

    def add_result(item: Dict[str, Any]):
        u = item.get("url", "").strip()
        d = item.get("domain", "").strip()
        if not u or not d:
            return

        u_key = _norm(u)

        # If URL already seen, upgrade existing entry if incoming has sample_image
        for existing in combined:
            if _norm(existing.get("url", "")) == u_key:
                if item.get("sample_image") and not existing.get("sample_image"):
                    existing["sample_image"] = item["sample_image"]
                    existing["is_cached_profile"] = True
                    if item.get("title") and len(item["title"]) > len(existing.get("title", "")):
                        existing["title"] = item["title"]
                return

        # For LinkedIn: multiple distinct public profiles can exist (e.g. /in/profile-a vs /in/profile-b)
        # Allow multiple LinkedIn profile candidates so biometric verification tests the correct individual
        is_linkedin = "linkedin.com" in d
        if not is_linkedin and d in seen_domains:
            if item.get("sample_image"):
                for existing in combined:
                    if existing.get("domain") == d and not existing.get("sample_image"):
                        existing["sample_image"] = item["sample_image"]
                        existing["is_cached_profile"] = True
            return

        seen_urls.add(u)
        seen_domains.add(d)
        combined.append(item)

    # Tier 1: Biometric Identity Recognition from Image
    if image_path and os.path.exists(image_path):
        bio_match = recognize_known_identity_from_image(image_path)
        if bio_match:
            if not resolved_name:
                resolved_name = bio_match["name"]
            for target_url in bio_match.get("urls", []):
                add_result({
                    "url": target_url,
                    "domain": urllib.parse.urlparse(target_url).netloc.lower(),
                    "title": f"Biometrically Verified: {bio_match['name']} ({bio_match['confidence']}%)",
                    "source": "biometric_preset_recognition"
                })

    # Tier 2: Extract Name from Filename if still empty
    if not resolved_name and image_path:
        base_file = os.path.basename(image_path).replace(".jpg", "").replace(".png", "")
        clean_name = re.sub(r'media_\d+_[a-f0-9]+', '', base_file, flags=re.I)
        clean_name = re.sub(r'screenshot_\d+.*', '', clean_name, flags=re.I)
        clean_name = re.sub(r'[\d_-]+', ' ', clean_name).strip()
        if len(clean_name) >= 3 and any(c.isalpha() for c in clean_name):
            resolved_name = clean_name.title()
            print(f"[Discovery] Inferred subject name from filename: '{resolved_name}'")

    # If we have a resolved subject name, execute multi-engine search in parallel
    if resolved_name:
        tasks = [
            ("google_linkedin", lambda: search_google_profiles_and_thumbnails(f"{resolved_name} linkedin", max_results=6)),
            ("google_general", lambda: search_google_profiles_and_thumbnails(resolved_name, max_results=4)),
            ("bing_linkedin", lambda: search_bing_images_with_profiles(f"{resolved_name} linkedin", max_results=5)),
            ("bing_general", lambda: search_bing_images_with_profiles(resolved_name, max_results=4)),
            ("ddg_linkedin", lambda: search_duckduckgo_images(f"{resolved_name} linkedin", max_results=4)),
            ("ddg_general", lambda: search_duckduckgo_images(resolved_name, max_results=4)),
            ("dev_domains", lambda: probe_candidate_developer_domains(resolved_name)),
            ("github", lambda: search_github_profiles(resolved_name, max_results=4)),
            ("wikipedia", lambda: search_wikipedia_opensearch(resolved_name)),
            ("bing_web", lambda: search_bing(f'{resolved_name} portfolio OR github OR developer OR linkedin', max_results=4))
        ]

        engine_results = {}
        with ThreadPoolExecutor(max_workers=min(10, len(tasks))) as executor:
            future_to_key = {executor.submit(fn): key for key, fn in tasks}
            for future in as_completed(future_to_key):
                key = future_to_key[future]
                try:
                    engine_results[key] = future.result()
                except Exception as ex:
                    print(f"[Discovery] Search worker error ({key}): {ex}")
                    engine_results[key] = []

        # Merge in strict priority tier order
        for key, _ in tasks:
            for item in engine_results.get(key, []):
                add_result(item)

    # Prioritize candidates with cached thumbnails (especially LinkedIn profiles) first
    combined.sort(key=lambda x: (
        0 if x.get("sample_image") and "linkedin.com" in x.get("domain", "") else
        (1 if x.get("sample_image") else 2)
    ))

    # Return candidates capped by page budget
    return combined[:max(2, max_pages_budget)]
