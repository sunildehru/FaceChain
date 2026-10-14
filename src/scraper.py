import os
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, urldefrag
from typing import List, Tuple, Set, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Pacing & Limits
DEFAULT_MAX_PAGES = 10
REQUEST_TIMEOUT = 8
IMAGE_TIMEOUT = 6
PARALLEL_WORKERS = 2        # Concurrency 2 prevents Wikimedia/CDN 429 rate limits
DISPATCH_STAGGER_DELAY = 0.20 # Polite 200ms spacing between page requests

# Clean standard browser headers accepted by Wikimedia and all CDNs
STANDARD_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

IMAGE_EXCLUDE_KEYWORDS = (
    "symbol", "disambig", "magnify-clip", "edit-pencil",
    "padlock", "lock-icon", "ui-icon", "arrow-", "button-"
)

def create_resilient_session() -> requests.Session:
    """
    Creates a requests session with connection pooling.
    CRITICAL: Does NOT include 429 in status_forcelist and disables respect_retry_after_header.
    This guarantees that if a 429 is encountered, urllib3 will NEVER stall the thread for 60+ seconds.
    """
    session = requests.Session()
    retries = Retry(
        total=1,
        backoff_factor=0.1,
        status_forcelist=[500, 502, 503, 504],
        respect_retry_after_header=False,
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(STANDARD_HEADERS)
    return session

def is_valid_article_link(url: str, domain: str) -> bool:
    """
    Filters out administrative, non-content, or off-domain links.
    For Wikipedia: guarantees scanning only genuine encyclopedia articles and eliminates all
    Special:, Wikipedia:, Help:, Portal:, Talk:, Category:, File:, Main_Page, and edit links.
    """
    parsed = urlparse(url)
    if parsed.netloc != domain:
        return False

    path = parsed.path
    query = parsed.query.lower()

    # Reject static binary assets mistakenly linked
    if any(path.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.pdf', '.mp4', '.zip', '.svg', '.json', '.xml']):
        return False

    # Wikipedia-specific optimization
    if "wikipedia.org" in domain:
        if not path.startswith("/wiki/"):
            return False
        
        article_slug = path[6:]
        # On Wikipedia, ALL administrative/meta pages contain a namespace colon (e.g. Special:, Wikipedia:, Help:, Talk:, File:, Category:)
        if ":" in article_slug:
            return False
        # Exclude Main_Page
        if article_slug.lower() == "main_page":
            return False
        # Exclude administrative actions
        if any(act in query for act in ['action=', 'oldid=', 'diff=', 'returnto=', 'title=special:', 'printable=']):
            return False
        return True

    # General domain optimization
    for excluded in ['/login', '/signin', '/signup', '/cart', '/checkout', '/privacy', '/terms', '/contact', '/about-us']:
        if path.rstrip('/').endswith(excluded):
            return False

    return True

def is_valid_photo_candidate(img_url: str, img_tag: BeautifulSoup) -> bool:
    """Filters out SVG graphics, tiny UI icons, and non-photographic assets."""
    lower_url = img_url.lower()
    if any(lower_url.endswith(ext) for ext in ['.svg', '.gif', '.ico']):
        return False
    if any(k in lower_url for k in IMAGE_EXCLUDE_KEYWORDS):
        return False

    # Check width/height attributes if declared as positive integers
    w = img_tag.get('width')
    h = img_tag.get('height')
    try:
        if w and str(w).isdigit() and int(w) < 60:
            return False
        if h and str(h).isdigit() and int(h) < 60:
            return False
    except Exception:
        pass

    return True

def _download_single_image(session: requests.Session, img_url: str, current_url: str, output_dir: str, file_idx: int) -> Optional[Tuple[str, str, str]]:
    """Downloads a single candidate image file and returns (filepath, img_url, page_url)."""
    try:
        resp = session.get(img_url, headers=STANDARD_HEADERS, timeout=IMAGE_TIMEOUT, stream=True)
        if resp.status_code == 200:
            content_type = resp.headers.get('content-type', '').lower()
            if 'image' not in content_type or 'svg' in content_type or 'gif' in content_type:
                return None

            ext = '.jpg'
            if 'png' in content_type: ext = '.png'
            elif 'webp' in content_type: ext = '.webp'

            filename = f"scraped_{int(time.time() * 1000)}_{file_idx}{ext}"
            filepath = os.path.join(output_dir, filename)

            with open(filepath, 'wb') as f:
                for chunk in resp.iter_content(2048):
                    f.write(chunk)

            # Ignore empty or tiny corrupt files (< 1.5 KB)
            if os.path.getsize(filepath) < 1500:
                try:
                    os.remove(filepath)
                except Exception:
                    pass
                return None

            return (filepath, img_url, current_url)
    except Exception:
        pass
    return None

def _scrape_page_worker(
    session: requests.Session,
    url: str,
    domain: str,
    output_dir: str,
    on_image_downloaded: Optional[Callable[[Tuple[str, str, str]], None]] = None
) -> Tuple[str, Optional[str], List[Tuple[str, str, str]], List[str]]:
    """
    Scrapes a single page:
    1. Extracts page title
    2. Downloads candidate photographs in parallel (invokes on_image_downloaded immediately for streaming consumer)
    3. Extracts high-relevance internal article links
    """
    url_cleaned, _ = urldefrag(url)
    try:
        resp = session.get(url_cleaned, headers=STANDARD_HEADERS, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 429:
            # 429 received: skip immediately without sleeping
            print(f"[Crawler] Rate-limited (429) on {url_cleaned}. Skipping without delay.")
            return url_cleaned, None, [], []
        resp.raise_for_status()
    except Exception as e:
        print(f"[Crawler] Skip {url_cleaned}: {e}")
        return url_cleaned, None, [], []

    soup = BeautifulSoup(resp.text, 'lxml')
    page_title = soup.title.string.strip() if soup.title and soup.title.string else url_cleaned

    # Focus on main content container if present, otherwise body/soup
    is_wiki = "wikipedia.org" in domain
    if is_wiki:
        content_root = soup.find('div', id='bodyContent') or soup.find('div', id='mw-content-text') or soup
    else:
        content_root = soup.find('main') or soup.find('article') or soup.find('div', id='content') or soup

    # 1. Collect candidate images
    candidate_img_urls = []
    seen_img_urls = set()
    for img in content_root.find_all('img'):
        src = img.get('src') or img.get('data-src')
        if not src:
            continue
        full_img_url = urljoin(url_cleaned, src)
        if full_img_url not in seen_img_urls and is_valid_photo_candidate(full_img_url, img):
            seen_img_urls.add(full_img_url)
            candidate_img_urls.append(full_img_url)

    # Parallel image downloading (max 3 concurrent image download threads to prevent CDN throttles)
    downloaded_images = []
    if candidate_img_urls:
        with ThreadPoolExecutor(max_workers=3) as img_pool:
            futures = [
                img_pool.submit(_download_single_image, session, img_url, url_cleaned, output_dir, idx)
                for idx, img_url in enumerate(candidate_img_urls[:20]) # Limit to top 20 photos per page
            ]
            for f in as_completed(futures):
                res = f.result()
                if res:
                    downloaded_images.append(res)
                    if on_image_downloaded:
                        on_image_downloaded(res)

    # 2. Extract valid article links
    extracted_links = []
    seen_links = set()
    for a in content_root.find_all('a'):
        href = a.get('href')
        if not href:
            continue
        next_url = urljoin(url_cleaned, href)
        next_url, _ = urldefrag(next_url)
        if next_url not in seen_links and is_valid_article_link(next_url, domain):
            seen_links.add(next_url)
            extracted_links.append(next_url)

    return url_cleaned, page_title, downloaded_images, extracted_links

def crawl_domain(
    start_url: str,
    output_dir: str = "temp/scraped",
    max_pages: int = DEFAULT_MAX_PAGES,
    on_image_downloaded: Optional[Callable[[Tuple[str, str, str]], None]] = None,
    on_page_completed: Optional[Callable[[str, int, int], None]] = None
) -> Tuple[List[Tuple[str, str, str]], str, int]:
    """
    Crawls domain starting from start_url up to max_pages using polite pacing.
    Invokes on_image_downloaded callback immediately as each image lands on disk (Producer mode).
    Returns:
      1. list of (local_file_path, image_url, page_url)
      2. main page title
      3. total unique pages crawled
    """
    os.makedirs(output_dir, exist_ok=True)
    domain = urlparse(start_url).netloc
    session = create_resilient_session()

    visited_urls: Set[str] = set()
    queue: List[str] = [start_url]
    downloaded_files: List[Tuple[str, str, str]] = []
    main_page_title: Optional[str] = None

    effective_max = max(1, min(max_pages, 50))
    print(f"[Crawler] Starting polite crawl on {domain} (Budget: {effective_max} pages, Paced Concurrency: {PARALLEL_WORKERS})")

    while queue and len(visited_urls) < effective_max:
        # Form a batch of up to PARALLEL_WORKERS URLs
        batch: List[str] = []
        while queue and len(batch) < PARALLEL_WORKERS and (len(visited_urls) + len(batch)) < effective_max:
            candidate = queue.pop(0)
            if candidate not in visited_urls and candidate not in batch:
                batch.append(candidate)

        if not batch:
            break

        print(f"[Crawler] Dispatching batch of {len(batch)} worker(s) ({len(visited_urls)+1}-{len(visited_urls)+len(batch)} of {effective_max})...")

        # Launch batch with polite spacing
        futures = []
        with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as page_pool:
            for idx, target_url in enumerate(batch):
                visited_urls.add(target_url)
                if idx > 0 and DISPATCH_STAGGER_DELAY > 0:
                    time.sleep(DISPATCH_STAGGER_DELAY)
                futures.append(page_pool.submit(_scrape_page_worker, session, target_url, domain, output_dir, on_image_downloaded))

            for f in as_completed(futures):
                try:
                    c_url, p_title, images, links = f.result()
                    if main_page_title is None and p_title:
                        main_page_title = p_title

                    downloaded_files.extend(images)

                    # Queue newly discovered article links
                    for lk in links:
                        if lk not in visited_urls and lk not in queue:
                            queue.append(lk)

                    print(f"[Crawler] Completed: {c_url[:65]} | +{len(images)} photos | Queued: {len(queue)}")
                    if on_page_completed:
                        on_page_completed(c_url, len(visited_urls), len(downloaded_files))
                except Exception as ex:
                    print(f"[Crawler] Worker error on batch: {ex}")

        # Short pause between waves to keep Wikimedia request rate smooth
        time.sleep(0.1)

    print(f"[Crawler] Finished sweep! Scanned {len(visited_urls)} pages. Extracted {len(downloaded_files)} candidate photos.")
    return downloaded_files, main_page_title or start_url, len(visited_urls)
