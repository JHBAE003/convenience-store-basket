"""Local SVG route map and calculation-based prose; no remote map tiles."""
from html import escape

def comparison_text(plan, other, distance_label='직선 이동'):
    delta=plan.total-other.total;distance=plan.distance_km-other.distance_km;visits=plan.count-other.count
    return (f'비교안 대비 {abs(delta):,}원 '+('저렴' if delta<0 else '비쌈' if delta>0 else '차이 없음')+
            f' · {distance_label} {abs(distance)*1000:,.0f}m '+('감소' if distance<0 else '증가' if distance>0 else '차이 없음')+
            f' · 방문 {abs(visits)}곳 '+('감소' if visits<0 else '증가' if visits>0 else '차이 없음'))

def route_svg(plan, stores, origin, return_home=False):
    byid={s.id:s for s in stores}
    points=[('출발지',origin)]+[(f'{i}. {byid[k].name}',(byid[k].lat,byid[k].lng)) for i,k in enumerate(plan.route,1)]
    xs=[p[1][1] for p in points];ys=[p[1][0] for p in points]
    span=max(max(xs)-min(xs),max(ys)-min(ys),.002)
    def project(p):return 70+(p[1]-min(xs))/span*450,330-(p[0]-min(ys))/span*230
    coords=[project(p[1]) for p in points]
    route=coords+[coords[0]] if return_home else coords
    path=' '.join(f'{x:.2f},{y:.2f}' for x,y in route)
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 760 420" role="img" aria-label="출발지와 방문 순서의 직선 연결 지도"><rect width="760" height="420" rx="16" fill="#f3f6f4"/><text x="24" y="32" font-size="16" fill="#34564a">직선 연결 지도 · 실제 도로와 다른 개략 배치</text><polyline points="{path}" fill="none" stroke="#17664e" stroke-width="3" stroke-dasharray="7 5"/>']
    for i,((label,_),(x,y)) in enumerate(zip(points,coords)):
        parts.append(f'<circle cx="{x}" cy="{y}" r="15" fill="#17664e"/><text x="{x}" y="{y+5}" text-anchor="middle" fill="white" font-size="14">{i}</text><text x="{x+22}" y="{y+4}" font-size="14" fill="#203e33">{escape(label)}</text>')
    return ''.join(parts)+'</svg>'
