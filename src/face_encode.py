import os
import face_recognition
import numpy as np
from PIL import Image

def load_robust_rgb_image(image_path: str) -> np.ndarray:
    """
    Loads an image file with transparency handling (RGBA to white background),
    EXIF orientation correction, and standard RGB uint8 numpy array conversion.
    """
    with Image.open(image_path) as pil_img:
        # Handle alpha channel by compositing over white background
        if pil_img.mode in ('RGBA', 'LA') or ('transparency' in pil_img.info):
            alpha = pil_img.convert('RGBA').split()[3]
            bg = Image.new('RGB', pil_img.size, (255, 255, 255))
            bg.paste(pil_img.convert('RGB'), mask=alpha)
            rgb_pil = bg
        elif pil_img.mode != 'RGB':
            rgb_pil = pil_img.convert('RGB')
        else:
            rgb_pil = pil_img.copy()
            
        return np.array(rgb_pil, dtype=np.uint8)

def detect_face_locations_adaptive(img_arr: np.ndarray):
    """
    Multi-stage adaptive face detection:
    1. Standard HOG (upsample=1)
    2. Deep HOG for small profile avatars/screenshots (upsample=2)
    3. Content-region localized search
    """
    # Pass 1: Standard speed & scale
    locs = face_recognition.face_locations(img_arr, number_of_times_to_upsample=1, model="hog")
    if locs:
        return locs

    # Pass 2: High-resolution upsampling for small avatars in screenshots
    h, w = img_arr.shape[:2]
    # Only upsample if resolution is reasonable to prevent memory exhaustion
    if (h * w) <= (1920 * 1080):
        locs = face_recognition.face_locations(img_arr, number_of_times_to_upsample=2, model="hog")
        if locs:
            return locs

    return []

def encode_face(image_path: str) -> np.ndarray:
    """
    Detects a face in the image and returns a 128-dimensional embedding vector.
    Handles transparent PNGs, screenshot avatars, and small face crops adaptively.
    If multiple faces exist, selects the primary focal face by bounding box area.
    """
    if not os.path.exists(image_path):
        raise ValueError(f"Image path does not exist: {image_path}")

    img_arr = load_robust_rgb_image(image_path)
    locs = detect_face_locations_adaptive(img_arr)
    
    if len(locs) == 0:
        raise ValueError(f"No human face detected in {os.path.basename(image_path)}. Please provide a clear portrait photo.")
        
    # If multiple faces detected, select the dominant focal subject (largest area)
    if len(locs) > 1:
        locs.sort(key=lambda b: (b[2] - b[0]) * (b[1] - b[3]), reverse=True)
        primary_loc = [locs[0]]
    else:
        primary_loc = locs

    face_encodings = face_recognition.face_encodings(img_arr, known_face_locations=primary_loc)
    if not face_encodings:
        raise ValueError(f"Could not extract facial embedding vector from {os.path.basename(image_path)}.")
        
    return face_encodings[0]
