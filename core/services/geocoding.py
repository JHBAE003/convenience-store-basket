"""Small, on-demand reverse geocoder for the location picker."""
from __future__ import annotations

import requests

_HEADERS = {"User-Agent": "ConvenienceStoreCompare/1.0 (desktop location picker)"}


def reverse_geocode_korean(latitude: float, longitude: float) -> str:
    """Return a Korean address from OpenStreetMap Nominatim, or raise on failure."""
    response = requests.get(
        "https://nominatim.openstreetmap.org/reverse",
        params={
            "lat": latitude,
            "lon": longitude,
            "format": "jsonv2",
            "zoom": 18,
            "addressdetails": 1,
            "accept-language": "ko",
        },
        headers=_HEADERS,
        timeout=8,
    )
    response.raise_for_status()
    payload = response.json()
    address = payload.get("address", {})

    # Prefer Korean administrative names and a recognizable road/building number.
    parts = []
    for key in ("province", "city", "county", "borough", "city_district", "suburb", "town", "village", "road", "house_number"):
        value = address.get(key)
        if value and value not in parts:
            parts.append(value)
    if parts:
        return " ".join(parts)
    return payload.get("display_name", "").split(",")[0].strip()


def emart24_region_korean(latitude: float, longitude: float,
                          geocoder=reverse_geocode_korean) -> tuple[str, str]:
    """Resolve coordinates to the AREA1/AREA2 values used by store lookup."""
    words = geocoder(latitude, longitude).replace(",", " ").split()
    aliases = {
        "서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시",
        "인천": "인천광역시", "광주": "광주광역시", "대전": "대전광역시",
        "울산": "울산광역시", "세종": "세종특별자치시", "경기": "경기도",
        "강원": "강원특별자치도", "충북": "충청북도", "충남": "충청남도",
        "전북": "전북특별자치도", "전남": "전라남도", "경북": "경상북도",
        "경남": "경상남도", "제주": "제주특별자치도",
    }
    area1 = ""
    area2 = ""
    for index, word in enumerate(words):
        candidate = aliases.get(word, word)
        if candidate.endswith(("특별시", "광역시", "특별자치시", "특별자치도", "도")):
            area1 = candidate
            area2 = next((part for part in words[index + 1:]
                          if part != word and aliases.get(part, part) != candidate
                          and part.endswith(("시", "구", "군"))), "")
            break
    if not area1 or (area1 != "세종특별자치시" and not area2):
        raise ValueError("선택 위치의 시·도와 시·군·구를 확인하지 못했습니다.")
    return area1, area2


def search_korean_address(query: str) -> tuple[float, float, str]:
    """Resolve a typed place/address to latitude, longitude, and display name."""
    response = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": query, "format": "jsonv2", "limit": 1, "accept-language": "ko", "countrycodes": "kr"},
        headers=_HEADERS,
        timeout=8,
    )
    response.raise_for_status()
    results = response.json()
    if not results:
        raise LookupError("검색 결과가 없습니다. 주소나 장소명을 다시 입력하세요.")
    result = results[0]
    return float(result["lat"]), float(result["lon"]), result.get("display_name", query)


def locate_by_ip() -> tuple[float, float, str]:
    """Return approximate coordinates and Korean region label for the public IP."""
    response = requests.get("https://ipapi.co/json/", headers=_HEADERS, timeout=8)
    response.raise_for_status()
    payload = response.json()
    if payload.get("error") or payload.get("latitude") is None or payload.get("longitude") is None:
        raise RuntimeError(payload.get("reason") or "IP 위치를 확인할 수 없습니다.")
    region = " ".join(filter(None, (payload.get("country_name"), payload.get("region"), payload.get("city"))))
    return float(payload["latitude"]), float(payload["longitude"]), region
