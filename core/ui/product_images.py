"""Bounded image loading with a consistent, uncropped product-photo canvas."""
from io import BytesIO
import ipaddress
from pathlib import Path
import socket
import time
from urllib.parse import urlsplit

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError
import requests
import streamlit as st
import urllib3

from core.models import StoreProduct
from core.services.product_list import local_photo

MAX_BYTES = 3 * 1024 * 1024
MAX_DOWNLOAD_SECONDS = 8
# Exact hosts observed in the checked-in catalog/provider responses, plus the
# official sites used by the current collectors. No arbitrary CDN subdomains.
IMAGE_HOSTS = frozenset({
    'image.woodongs.com',
    'tqklhszfkvzk6518638.edge.naverncp.com',
    'cu.bgfretail.com',
    'www.7-eleven.co.kr',
    'new.7-elevenapp.co.kr',
    'emart24.co.kr',
    'www.emart24.co.kr',
})


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
        if (not isinstance(url, str) or len(url) > 2048
                or any(ord(char) < 33 or ord(char) == 127 or char == '\\' for char in url)):
            return None
        parts = urlsplit(url)
        host = (parts.hostname or '').lower()
        if (parts.scheme != 'https' or host not in IMAGE_HOSTS or parts.port not in (None, 443)
                or parts.username is not None or parts.password is not None or parts.fragment):
            return None
        answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
        if not addresses:
            return None
        for address in addresses:
            parsed = ipaddress.ip_address(address)
            if not parsed.is_global or parsed.is_multicast or parsed.is_reserved or parsed.is_unspecified:
                return None
    except ValueError:
        return None
    except OSError:
        return None

    # Connect to the validated numeric address, while checking the original
    # hostname's certificate and sending that hostname in TLS SNI and HTTP Host.
    # A direct pool does not load environment proxies, .netrc or session cookies.
    pool = urllib3.HTTPSConnectionPool(
        addresses[0], port=443, server_hostname=host, assert_hostname=host,
        cert_reqs='CERT_REQUIRED', ca_certs=requests.certs.where(),
        timeout=urllib3.Timeout(connect=2, read=3), retries=False, maxsize=1,
    )
    response = None
    try:
        target = (parts.path or '/') + ('?' + parts.query if parts.query else '')
        response = pool.urlopen(
            'GET', target, headers={'Host': host, 'Accept': 'image/*', 'Accept-Encoding': 'identity'},
            redirect=False, retries=False, preload_content=False, decode_content=False,
            assert_same_host=False,
        )
        if response.status != 200 or not response.headers.get('Content-Type', '').lower().startswith('image/'):
            return None
        if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
            return None
        length = response.headers.get('Content-Length')
        if length is not None and not 0 <= int(length) <= MAX_BYTES:
            return None
        data = bytearray()
        deadline = time.monotonic() + MAX_DOWNLOAD_SECONDS
        while time.monotonic() < deadline:
            # read1 returns after at most one underlying read. A slow stream
            # cannot keep one large read alive indefinitely by sending bytes.
            chunk = response.read1(min(65536, MAX_BYTES - len(data) + 1), decode_content=False)
            if not chunk:
                return photo_canvas(bytes(data))
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                return None
        return None
    except (urllib3.exceptions.HTTPError, OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return None
    finally:
        if response is not None:
            response.close()
        pool.close()


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
