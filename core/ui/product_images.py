"""Bounded image loading with a consistent, uncropped product-photo canvas."""
from io import BytesIO
import ipaddress
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError
import requests
import streamlit as st

from core.models import StoreProduct
from core.services.product_list import local_photo

MAX_BYTES = 3 * 1024 * 1024


def placeholder() -> bytes:
    canvas = Image.new('RGB', (640, 420), '#F3F5F1')
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((260, 150, 380, 280), radius=16, outline='#A9B5A8', width=4)
    draw.arc((288, 121, 352, 185), 180, 360, fill='#A9B5A8', width=4)
    out = BytesIO()
    canvas.save(out, format='PNG')
    return out.getvalue()


def photo_canvas(data: bytes) -> bytes:
    with Image.open(BytesIO(data)) as source:
        if source.width * source.height > 20_000_000:
            raise ValueError('상품 이미지가 너무 큽니다.')
        image = ImageOps.exif_transpose(source).convert('RGBA')
        image.thumbnail((520, 340))
        canvas = Image.new('RGB', (640, 420), 'white')
        canvas.paste(image, ((640-image.width)//2, (420-image.height)//2), image)
        out = BytesIO()
        canvas.save(out, format='PNG')
        return out.getvalue()


@st.cache_data(ttl=3600, max_entries=96, show_spinner=False)
def remote_photo(url: str) -> bytes | None:
    try:
        parts = urlsplit(url)
        host = (parts.hostname or '').lower()
    except ValueError:
        return None
    if parts.scheme not in ('https', 'http') or not host or parts.username or parts.password:
        return None
    if host == 'localhost' or host.endswith(('.local', '.internal')):
        return None
    try:
        if not ipaddress.ip_address(host).is_global:
            return None
    except ValueError:
        pass
    try:
        # Redirects are not followed: a failed image must not block product browsing.
        with requests.get(url, stream=True, timeout=(2, 3), allow_redirects=False) as response:
            response.raise_for_status()
            if response.status_code != 200 or not response.headers.get('Content-Type', '').startswith('image/'):
                return None
            data = bytearray()
            for chunk in response.iter_content(65536):
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    return None
        return photo_canvas(bytes(data))
    except (requests.RequestException, OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return None


@st.cache_data(ttl=3600, max_entries=32, show_spinner=False)
def bundled_photo(path: str, modified: float) -> bytes | None:
    try:
        return photo_canvas(Path(path).read_bytes())
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return None


def product_photo(row: StoreProduct) -> tuple[bytes, bool]:
    path = local_photo(row)
    photo = bundled_photo(str(path), path.stat().st_mtime) if path else None
    if photo is None and row.image_url and row.data_source != 'TEST':
        photo = remote_photo(row.image_url)
    return (photo, True) if photo else (placeholder(), False)
