"""Read installed app dependencies and query public OSV/PyPI metadata.

No packages are installed or changed. Only package names and versions leave the
computer; project files and credentials are never sent to these services.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from importlib import metadata
import json
from pathlib import Path
import sys
from urllib.parse import quote

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
import requests

ROOT = Path(__file__).resolve().parents[1]


def installed_closure(requirements: Path) -> tuple[list[dict], list[str]]:
    roots = [Requirement(line.strip()) for line in requirements.read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    environment = default_environment()
    environment["extra"] = ""
    pending = [req.name for req in roots]
    seen, packages, errors = set(), [], []
    root_names = {canonicalize_name(req.name) for req in roots}
    while pending:
        name = pending.pop()
        key = canonicalize_name(name)
        if key in seen:
            continue
        seen.add(key)
        try:
            distribution = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            errors.append(f"설치된 배포판을 찾을 수 없음: {key}")
            continue
        packages.append({"name": canonicalize_name(distribution.metadata["Name"]),
                         "version": distribution.version, "direct": key in root_names})
        for raw in distribution.requires or []:
            req = Requirement(raw)
            if req.marker is None or req.marker.evaluate(environment):
                pending.append(req.name)
    installed = {p["name"]: p["version"] for p in packages}
    for req in roots:
        version = installed.get(canonicalize_name(req.name))
        if version and req.specifier and version not in req.specifier:
            errors.append(f"요구 버전과 설치 버전 불일치: {req.name}")
    return sorted(packages, key=lambda p: p["name"]), errors


def pypi_metadata(package: dict) -> dict:
    url = "https://pypi.org/pypi/{}/{}/json".format(quote(package["name"], safe=""),
                                                   quote(package["version"], safe=""))
    try:
        response = requests.get(url, timeout=(5, 20), allow_redirects=False)
        response.raise_for_status()
        payload = response.json()
        info = payload.get("info") or {}
        if str(info.get("version")) != package["version"]:
            raise ValueError("정확 버전 응답 불일치")
        urls = payload.get("urls") or []
        return {**package, "status": "verified", "metadata_url": url,
                "release_upload_times": sorted({row.get("upload_time_iso_8601", "") for row in urls}),
                "all_release_files_yanked": bool(urls) and all(row.get("yanked") is True for row in urls),
                "vulnerability_ids": sorted({str(v["id"]) for v in payload.get("vulnerabilities", []) if v.get("id")})}
    except (requests.RequestException, ValueError, TypeError) as exc:
        return {**package, "status": "unverified", "error_type": type(exc).__name__}


def audit(requirements: Path) -> dict:
    packages, errors = installed_closure(requirements)
    queries = [{"package": {"ecosystem": "PyPI", "name": p["name"]}, "version": p["version"]}
               for p in packages]
    osv, ids = [], set()
    try:
        response = requests.post("https://api.osv.dev/v1/querybatch", json={"queries": queries},
                                 timeout=(5, 30), allow_redirects=False)
        response.raise_for_status()
        results = response.json().get("results")
        if not isinstance(results, list) or len(results) != len(packages):
            raise ValueError("OSV 응답 수가 요청 수와 다름")
        for package, result in zip(packages, results):
            vulnerabilities = sorted({str(v["id"]) for v in result.get("vulns", []) if v.get("id")})
            ids.update(vulnerabilities)
            osv.append({**package, "vulnerability_ids": vulnerabilities})
    except (requests.RequestException, ValueError, TypeError) as exc:
        errors.append(f"OSV 배치 조회 미완료: {type(exc).__name__}")
    with ThreadPoolExecutor(max_workers=6) as pool:
        pypi = list(pool.map(pypi_metadata, packages))
    errors.extend(f"PyPI 정확 버전 미확인: {p['name']}" for p in pypi if p["status"] != "verified")
    for package in pypi:
        ids.update(package.get("vulnerability_ids", []))
    return {"checked_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "python_version": sys.version.split()[0], "requirements": str(requirements.name),
            "scope": "requirements의 기본 의존성과 현재 Python/Windows marker에 해당하는 설치된 간접 의존성; extras 제외",
            "package_count": len(packages), "direct_count": sum(p["direct"] for p in packages),
            "osv_query_count": 1 if queries else 0, "pypi_metadata_query_count": len(packages),
            "osv": osv, "pypi": pypi, "vulnerability_ids": sorted(ids), "errors": errors,
            "status": "incomplete" if errors else ("findings" if ids else "no_known_advisories_returned")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT/"docs/security/dependency-audit.json")
    parser.add_argument("--inventory-only", action="store_true")
    args = parser.parse_args()
    if args.inventory_only:
        packages, errors = installed_closure(ROOT/"requirements.txt")
        print(json.dumps({"packages": packages, "errors": errors}, ensure_ascii=False, indent=2))
        return 2 if errors else 0
    result = audit(ROOT/"requirements.txt")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("status", "package_count", "direct_count", "vulnerability_ids", "errors")}, ensure_ascii=False))
    return 2 if result["errors"] else (1 if result["vulnerability_ids"] else 0)


if __name__ == "__main__":
    raise SystemExit(main())
