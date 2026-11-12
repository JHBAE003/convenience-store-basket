"""서울대입구역 주변 공개 OSM 도로를 한 번 내려받는다. 앱 실행 중에는 호출하지 않는다."""
from datetime import datetime,timezone
from pathlib import Path
import gzip
import hashlib
import json
import requests
import argparse

ROOT=Path(__file__).resolve().parents[1]
BBOX=(37.465,126.926,37.495,126.975)
ENDPOINT='https://overpass-api.de/api/interpreter'
QUERY='[out:json][timeout:40];way["highway"]('+','.join(map(str,BBOX))+');(._;>;);out body;'


def save(data,endpoint=ENDPOINT,requests_log=None):
    target=ROOT/'data/maps';target.mkdir(parents=True,exist_ok=True)
    path=target/'seoul_walk.osm.json.gz'
    if path.exists():raise FileExistsError('기존 지도 파일을 덮어쓰지 않습니다.')
    if data.get('remark') or not isinstance(data.get('elements'),list):raise ValueError('지도 서버가 불완전한 응답을 보냈습니다.')
    nodes={e['id'] for e in data['elements'] if e['type']=='node'}
    ways=[e for e in data['elements'] if e['type']=='way' and 'highway' in e.get('tags',{})]
    if len(ways)<100 or any(any(n not in nodes for n in w['nodes']) for w in ways):raise ValueError('도로 또는 좌표가 누락됐습니다.')
    raw=json.dumps(data,ensure_ascii=False,separators=(',',':')).encode()
    path.write_bytes(gzip.compress(raw,compresslevel=9,mtime=0))
    metadata={'bbox':BBOX,'endpoint':endpoint,'query':QUERY if endpoint==ENDPOINT else None,'downloaded_at':datetime.now(timezone.utc).isoformat(),
              'source_updated_at':data.get('osm3s',{}).get('timestamp_osm_base'), 'requests':requests_log or [],
              'nodes':len(nodes),'ways':len(ways),'compressed_bytes':path.stat().st_size,
              'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
              'attribution':'© OpenStreetMap contributors','license':'ODbL 1.0',
              'license_url':'https://opendatacommons.org/licenses/odbl/1-0/',
              'source_url':'https://www.openstreetmap.org/copyright'}
    (target/'metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(metadata,ensure_ascii=False),flush=True)


def download():
    response=requests.post(ENDPOINT,data={'data':QUERY},headers={'User-Agent':'ConvenienceBasketStudent/3.1.1 (small offline walking-map extract)'},timeout=(8,45))
    response.raise_for_status()
    if len(response.content)>15_000_000:raise ValueError('지도 응답이 예상 크기를 넘었습니다.')
    save(response.json())


def download_osm_api():
    """이번 빌드에 사용한 대안. 50,000노드 제한 안의 4개 작은 영역만 요청한다."""
    if (ROOT/'data/maps/seoul_walk.osm.json.gz').exists():raise FileExistsError('기존 지도를 보존하려면 다른 폴더에서 실행하세요.')
    endpoint='https://api.openstreetmap.org/api/0.6/map.json'
    elements={};logs=[]
    for s,n in ((37.465,37.480),(37.480,37.495)):
        for w,e in ((126.926,126.9505),(126.9505,126.975)):
            bbox=f'{w},{s},{e},{n}'
            response=requests.get(endpoint,params={'bbox':bbox},headers={'User-Agent':'ConvenienceBasketStudent/3.1.1 small-offline-map'},timeout=(8,45))
            response.raise_for_status()
            if len(response.content)>15_000_000:raise ValueError('지도 응답 크기 제한을 넘었습니다.')
            data=response.json()
            if not isinstance(data.get('elements'),list):raise ValueError('지도 자료가 없습니다.')
            logs.append({'url':endpoint,'bbox':bbox,'elements':len(data['elements'])})
            for item in data['elements']:elements[item['type'],item['id']]=item
    ways=[e for e in elements.values() if e['type']=='way' and 'highway' in e.get('tags',{})]
    needed={n for way in ways for n in way['nodes']}
    nodes=[e for e in elements.values() if e['type']=='node' and e['id'] in needed]
    save({'version':0.6,'generator':'OSM API 0.6 bounded extract, road-only filtering','elements':nodes+ways},endpoint,logs)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',choices=('osm-api','overpass'),default='osm-api')
    args=parser.parse_args()
    download_osm_api() if args.source=='osm-api' else download()
