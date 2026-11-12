"""v3.2.0 대비 로컬 지도 출력의 동등성과 반복 생성 비용만 측정한다."""
import hashlib
import importlib.util
import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from planner.test_data import TestDataset
from planner import walk_map as current


def timed(function,repeats=1):
    samples=[];value=None
    for _ in range(repeats):
        start=perf_counter();value=function();samples.append((perf_counter()-start)*1000)
    return {'median_ms':round(median(samples),6),'min_ms':round(min(samples),6),
            'max_ms':round(max(samples),6),'samples':repeats},value


def main():
    predecessor=ROOT.parent/'기말 과제 v3.2.0'
    spec=importlib.util.spec_from_file_location('v320_walk_baseline',predecessor/'planner/walk_map.py')
    old=importlib.util.module_from_spec(spec);sys.modules[spec.name]=old;spec.loader.exec_module(old)
    stores=TestDataset().stores()
    points=tuple(sorted(((current.ORIGIN,(37.48121,126.9527)),)+
                        tuple((s['id'],(s['lat'],s['lng'])) for s in stores)))
    report={'scope':'로컬 OSM SVG/GPX 문자열 생성만 측정. 앱 전체, 네트워크, 재고 조회 속도 측정 아님.',
            'conditions':{'python':sys.version.split()[0],'stores':len(stores),'origin':[37.48121,126.9527],
                          'warm_samples':50,'cold_definition':'지도망·거리행렬 준비 후 해당 경로의 첫 출력 생성',
                          'warm_definition':'동일 지도망/방문 순서/왕복 조건을 반복 출력',
                          'cache_limit_per_network':current.RENDER_CACHE_SIZE},'versions':{},'equivalence':[]}
    nets={}
    route=tuple(s['id'] for s in stores[:2])
    for label,module in (('3.2.0',old),('3.2.1',current)):
        module.load_map.cache_clear();module.get_network.cache_clear()
        prep,net=timed(lambda:module.get_network(points));matrix,_=timed(net.matrix)
        nets[label]=net
        metrics={'network_first_build':prep,'matrix_first_build':matrix}
        for kind,render in (('svg',module.walking_svg),('gpx',module.route_gpx)):
            first,value=timed(lambda:render(net,route,False))
            warm,last=timed(lambda:render(net,route,False),50)
            metrics[kind]={'first_render':first,'warm_render':warm,'utf8_bytes':len(value.encode('utf-8')),
                           'sha256':hashlib.sha256(last.encode('utf-8')).hexdigest()}
        report['versions'][label]=metrics
    for length in (1,2,3):
        selected=tuple(s['id'] for s in stores[:length])
        for home in (False,True):
            for kind in ('svg','gpx'):
                fn='walking_svg' if kind=='svg' else 'route_gpx'
                before=getattr(old,fn)(nets['3.2.0'],selected,home)
                after=getattr(current,fn)(nets['3.2.1'],selected,home)
                equal=before==after
                report['equivalence'].append({'format':kind,'route':selected,'return_home':home,
                                               'identical':equal,'sha256':hashlib.sha256(after.encode('utf-8')).hexdigest()})
                if not equal:raise AssertionError('지도 출력이 이전 버전과 다릅니다.')
    report['warm_median_speedup']={kind:round(report['versions']['3.2.0'][kind]['warm_render']['median_ms']/
                                             max(report['versions']['3.2.1'][kind]['warm_render']['median_ms'],1e-9),2)
                                   for kind in ('svg','gpx')}
    target=ROOT/'docs/verification/v3.2.1-performance.json'
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'report':str(target),'equivalent_outputs':len(report['equivalence']),
                      'versions':report['versions'],'warm_median_speedup':report['warm_median_speedup']},ensure_ascii=False))


if __name__=='__main__':main()
