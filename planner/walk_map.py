"""다운로드된 OSM 도보망의 최단 경로. 런타임 외부 요청 없음."""
from dataclasses import dataclass
from functools import lru_cache
import gzip
import hashlib
import heapq
from html import escape
import json
from math import cos,radians,isfinite
from pathlib import Path
from planner.engine import haversine_km

MAP_DIR=Path(__file__).resolve().parents[1]/'data/maps'
ORIGIN='@origin'
ALLOWED={'footway','pedestrian','path','steps','living_street','residential','service',
         'unclassified','tertiary','tertiary_link','secondary','secondary_link','primary','primary_link','track','road'}
BLOCKED={'no','private','permit','customers','delivery','agricultural','forestry','use_sidepath'}


def walkable(tags):
    road=tags.get('highway','')
    foot=tags.get('foot',tags.get('access',''))
    return (tags.get('area')!='yes' and foot not in BLOCKED and
            (road in ALLOWED or road=='cycleway' and foot in ('yes','designated','permissive'))
            and tags.get('construction') is None and tags.get('proposed') is None)


@dataclass(frozen=True)
class Snap:
    point: tuple
    road_point: tuple
    edge: tuple
    fraction: float
    distance_km: float


@dataclass(frozen=True)
class Leg:
    points: tuple
    distance_km: float
    approaches: tuple


def _dijkstra(adj,source):
    distances={source:0.0};previous={};queue=[(0.0,source)]
    while queue:
        distance,node=heapq.heappop(queue)
        if distance!=distances[node]:continue
        for other,length in adj.get(node,{}).items():
            candidate=distance+length
            if candidate<distances.get(other,float('inf')):
                distances[other]=candidate;previous[other]=node;heapq.heappush(queue,(candidate,other))
    return distances,previous


class WalkMap:
    def __init__(self,data,metadata):
        self.metadata=metadata;self.bbox=tuple(metadata['bbox'])
        self.nodes={e['id']:(e['lat'],e['lon']) for e in data['elements'] if e['type']=='node'}
        node_tags={e['id']:e.get('tags',{}) for e in data['elements'] if e['type']=='node'}
        self.ways=[e for e in data['elements'] if e['type']=='way' and 'highway' in e.get('tags',{})]
        self.edges={}
        self.names={}
        for way in self.ways:
            tags=way['tags']
            if not walkable(tags):continue
            foot_oneway=tags.get('oneway:foot')
            forward=foot_oneway!='-1' and tags.get('foot:forward')!='no'
            backward=foot_oneway not in ('yes','1','true') and tags.get('foot:backward')!='no'
            for a,b in zip(way['nodes'],way['nodes'][1:]):
                if a==b or a not in self.nodes or b not in self.nodes:continue
                if any(node_tags[n].get('foot',node_tags[n].get('access','')) in BLOCKED or
                       node_tags[n].get('barrier') in ('wall','fence') for n in (a,b)):continue
                if forward:self.edges[a,b]=haversine_km(*self.nodes[a],*self.nodes[b])
                if backward:self.edges[b,a]=haversine_km(*self.nodes[a],*self.nodes[b])
                if tags.get('name'):self.names[a,b]=tags['name']
        self.snap_edges=tuple(sorted({tuple(sorted(e)) for e in self.edges}))
        if not self.snap_edges:raise ValueError('지도에 연결 가능한 보행로가 없습니다.')

    def contains(self,point):
        s,w,n,e=self.bbox
        return len(point)==2 and all(isfinite(x) for x in point) and s<=point[0]<=n and w<=point[1]<=e

    def snap(self,point,max_km=.12):
        if not self.contains(point):raise ValueError('출발지 또는 매장이 다운로드한 지도 범위 밖에 있습니다.')
        scale=cos(radians(point[0]));best=None
        for a,b in self.snap_edges:
            pa,pb=self.nodes[a],self.nodes[b]
            ax,ay=(pa[1]-point[1])*scale,pa[0]-point[0]
            bx,by=(pb[1]-point[1])*scale,pb[0]-point[0]
            dx,dy=bx-ax,by-ay
            t=max(0,min(1,-(ax*dx+ay*dy)/(dx*dx+dy*dy))) if dx or dy else 0
            distance=(ax+t*dx)**2+(ay+t*dy)**2
            if best is None or distance<best[0]:best=(distance,a,b,t)
        _,a,b,t=best;pa,pb=self.nodes[a],self.nodes[b]
        road=(pa[0]+t*(pb[0]-pa[0]),pa[1]+t*(pb[1]-pa[1]))
        distance=haversine_km(*point,*road)
        if distance>max_km:raise ValueError(f'가까운 보행로까지 {distance*1000:.0f}m로, 연결 허용 범위 120m를 넘습니다.')
        return Snap(tuple(point),road,(a,b),t,distance)

    def network(self,points):return WalkNetwork(self,points)


class WalkNetwork:
    def __init__(self,base,points):
        self.base=base;self.points=dict(points);self.snaps={};self.issues={};self._matrix=None
        self.coords=dict(base.nodes);self.adj={};self.node_ids={}
        for key,point in sorted(self.points.items()):
            try:self.snaps[key]=base.snap(point)
            except ValueError as exc:self.issues[key]=str(exc)
        groups={}
        for i,(key,snap) in enumerate(sorted(self.snaps.items()),1):
            node=-i;self.node_ids[key]=node;self.coords[node]=snap.road_point
            groups.setdefault(snap.edge,[]).append((snap.fraction,node))
        for (a,b),length in base.edges.items():
            edge=tuple(sorted((a,b)))
            inserts=groups.get(edge,())
            if not inserts:self._add(a,b,length);continue
            nodes=[(0,a),(1,b)]+[(t if a==edge[0] else 1-t,node) for t,node in inserts]
            nodes.sort()
            for (t1,u),(t2,v) in zip(nodes,nodes[1:]):
                self._add(u,v,length*(t2-t1))
                # 같은 도로 위치에 여러 지점이 겹쳐도 우회하지 않는다.
                # 보행 일방통행은 도로 진행에 적용되며 동일 위치 전환은 양방향이다.
                if abs(t2-t1)<1e-12:self._add(v,u,0.0)

    def _add(self,a,b,length):
        self.adj.setdefault(a,{})[b]=min(length,self.adj.get(a,{}).get(b,float('inf')))

    @lru_cache(maxsize=8)
    def tree(self,key):
        # 25개 시작점의 전체 탐색 트리를 세션마다 무제한 보관하지 않는다.
        return _dijkstra(self.adj,self.node_ids[key])

    def distance(self,a,b):
        if a not in self.snaps or b not in self.snaps:return float('inf')
        if self.points[a]==self.points[b]:return 0.0
        distance=self.tree(a)[0].get(self.node_ids[b],float('inf'))
        return distance+self.snaps[a].distance_km+self.snaps[b].distance_km

    def matrix(self):
        if self._matrix is None:
            self._matrix=tuple((a,b,self.distance(a,b)) for a in sorted(self.points) for b in sorted(self.points) if a!=b)
        return self._matrix

    def leg(self,a,b):
        distance=self.distance(a,b)
        if not isfinite(distance):raise ValueError('두 지점 사이에 연결된 보행 경로가 없습니다.')
        if self.points[a]==self.points[b]:return Leg((self.points[a],),0.0,())
        node=self.node_ids[b];start=self.node_ids[a];nodes=[node];previous=self.tree(a)[1]
        while node!=start:node=previous[node];nodes.append(node)
        nodes.reverse()
        approaches=((self.points[a],self.snaps[a].road_point),(self.snaps[b].road_point,self.points[b]))
        return Leg(tuple(self.coords[n] for n in nodes),distance,approaches)

    def legs(self,route,return_home=False):
        order=(ORIGIN,)+tuple(route)+((ORIGIN,) if return_home else ())
        return tuple(self.leg(a,b) for a,b in zip(order,order[1:]))


@lru_cache(maxsize=1)
def load_map():
    meta=json.loads((MAP_DIR/'metadata.json').read_text(encoding='utf-8'))
    raw=(MAP_DIR/'seoul_walk.osm.json.gz').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=meta['sha256']:raise ValueError('지도 파일 검증에 실패했습니다.')
    return WalkMap(json.loads(gzip.decompress(raw)),meta)


@lru_cache(maxsize=4)
def get_network(points):return load_map().network(points)


def walking_svg(network,route,return_home=False):
    legs=network.legs(route,return_home)
    points=[p for leg in legs for p in leg.points]+[network.points[k] for k in (ORIGIN,)+tuple(route)]
    center=sum(p[0] for p in points)/len(points);scale=cos(radians(center))
    xs=[p[1]*scale for p in points];ys=[p[0] for p in points]
    # 좌표를 같은 축척으로 투영하고 주변 거리와 여백을 함께 보여준다.
    midx=(min(xs)+max(xs))/2;midy=(min(ys)+max(ys))/2
    spanx=max(max(xs)-min(xs),.003)*1.35;spany=max(max(ys)-min(ys),.002)*1.4
    factor=min(880/spanx,490/spany)
    def xy(p):return 500+(p[1]*scale-midx)*factor,300-(p[0]-midy)*factor
    def line(coords):return ' '.join(f'{x:.1f},{y:.1f}' for x,y in map(xy,coords))
    svg=['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 620" role="img" aria-label="다운로드한 실제 도로 지도와 방문 도보 경로"><rect width="1000" height="620" rx="20" fill="#f0f3ed"/><defs><clipPath id="map"><rect x="12" y="50" width="976" height="506" rx="12"/></clipPath></defs><g clip-path="url(#map)">']
    labels=[];seen=set()
    for way in network.base.ways:
        coords=[network.base.nodes[n] for n in way['nodes'] if n in network.base.nodes]
        if len(coords)<2:continue
        projected=[xy(p) for p in coords]
        if not any(-50<x<1050 and 0<y<600 for x,y in projected):continue
        road=way['tags']['highway'];major=road in ('primary','secondary','tertiary','residential')
        width=8 if major else 3
        svg.append(f'<polyline points="{line(coords)}" fill="none" stroke="'+('#d5ded2' if major else '#dfe5db')+f'" stroke-width="{width+2}" stroke-linecap="round" stroke-linejoin="round"/>')
        svg.append(f'<polyline points="{line(coords)}" fill="none" stroke="#fffef9" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"/>')
        name=way['tags'].get('name','');x,y=projected[len(projected)//2]
        cell=(round(x/135),round(y/60))
        if name and major and 70<x<900 and 90<y<490 and name not in seen and all(abs(x-a)>110 or abs(y-b)>35 for a,b,_ in labels):
            labels.append((x,y,name));seen.add(name)
    for x,y,name in labels:svg.append(f'<text x="{x:.1f}" y="{y:.1f}" font-family="sans-serif" font-size="12" fill="#6b7869" text-anchor="middle">{escape(name)}</text>')
    for leg in legs:
        svg.append(f'<polyline points="{line(leg.points)}" fill="none" stroke="white" stroke-width="10" stroke-linejoin="round" stroke-linecap="round"/><polyline points="{line(leg.points)}" fill="none" stroke="#17664e" stroke-width="5" stroke-linejoin="round" stroke-linecap="round"/>')
        for pair in leg.approaches:svg.append(f'<polyline points="{line(pair)}" fill="none" stroke="#b47726" stroke-width="3" stroke-dasharray="5 4"/>')
    svg.append('</g>')
    for i,key in enumerate((ORIGIN,)+tuple(route)):
        x,y=xy(network.points[key]);label='출발' if i==0 else str(i)
        svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="18" fill="'+('#263b31' if i==0 else '#17664e')+f'" stroke="white" stroke-width="3"/><text x="{x:.1f}" y="{y+5:.1f}" text-anchor="middle" font-family="sans-serif" font-size="{12 if i==0 else 16}" font-weight="700" fill="white">{label}</text>')
    svg.append('<g font-family="sans-serif"><text x="24" y="32" font-size="17" fill="#263b31" font-weight="700">실제 도로망 · 도보 방문 경로</text><text x="24" y="580" font-size="13" fill="#455b48">초록 실선: 도로 경로 / 황토 점선: 위치에서 보행로까지 연결 추정</text><a href="https://www.openstreetmap.org/copyright"><text x="24" y="604" font-size="12" fill="#455b48">© OpenStreetMap contributors · ODbL 1.0</text></a></g></svg>')
    return ''.join(svg)


def route_gpx(network,route,return_home=False):
    # 도로 구간만 내보낸다. 미확인 진입 연결은 별도 트랙 세그먼트로 남기지 않는다.
    segments=['<trkseg>'+''.join(f'<trkpt lat="{lat:.7f}" lon="{lng:.7f}"/>' for lat,lng in leg.points)+'</trkseg>' for leg in network.legs(route,return_home)]
    return '<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" creator="ConvenienceBasket3.1.1" xmlns="http://www.topografix.com/GPX/1/1"><metadata><desc>OSM 도로 구간; 점포 진입 연결 미포함. © OpenStreetMap contributors, ODbL 1.0</desc><link href="https://www.openstreetmap.org/copyright"/></metadata><trk><name>편의점 도보 방문 경로</name>'+''.join(segments)+'</trk></gpx>'
