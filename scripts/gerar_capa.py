"""Gera capa (perimetro a direita) e print de escopo (centralizado) a partir
de KML/KMZ/GeoJSON/shapefile .zip em entrada/. Mesmo enquadramento do
Gerador de Capa Meta: quadro 1280x720 exportado em 2x (2560x1440)."""
import io, json, math, os, re, sys, zipfile, unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
import requests
from PIL import Image, ImageDraw

W, H, T, MAXZ, RESERVA, ESCALA = 1280, 720, 256, 18, 0.54, 2
COR, ESPESSURA = (255, 242, 0), 2.5
TILE = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
MARGEM = {"capa": 90, "escopo": 40}


def lx(lon):
    return (lon + 180) / 360 * T


def ly(lat):
    s = max(-0.9999, min(0.9999, math.sin(math.radians(lat))))
    return (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * T


def parse_kml(texto):
    root = ET.fromstring(texto.encode("utf-8") if isinstance(texto, str) else texto)
    rings = []
    for el in root.iter():
        if el.tag.split("}")[-1] == "coordinates" and el.text:
            pts = []
            for t in el.text.split():
                p = t.split(",")
                try:
                    pts.append((float(p[0]), float(p[1])))
                except (ValueError, IndexError):
                    pass
            if len(pts) >= 3:
                rings.append(pts)
    return rings


def parse_geojson(gj):
    rings = []

    def push(cs):
        pts = [(c[0], c[1]) for c in cs if len(c) >= 2]
        if len(pts) >= 3:
            rings.append(pts)

    def geom(g):
        if not g:
            return
        t = g.get("type")
        if t == "Polygon":
            [push(r) for r in g["coordinates"]]
        elif t == "MultiPolygon":
            [push(r) for p in g["coordinates"] for r in p]
        elif t == "LineString":
            push(g["coordinates"])
        elif t == "MultiLineString":
            [push(r) for r in g["coordinates"]]
        elif t == "GeometryCollection":
            [geom(x) for x in g["geometries"]]

    def walk(o):
        if isinstance(o, list):
            return [walk(x) for x in o]
        if not isinstance(o, dict):
            return
        if o.get("type") == "FeatureCollection":
            return [walk(f) for f in o["features"]]
        if o.get("type") == "Feature":
            return geom(o.get("geometry"))
        geom(o)

    walk(gj)
    return rings


def parse_shp_zip(dados):
    import shapefile
    z = zipfile.ZipFile(io.BytesIO(dados))
    rings = []
    for nome in z.namelist():
        if nome.lower().endswith(".shp"):
            base = nome[:-4]
            achar = lambda ext: next((n for n in z.namelist() if n.lower() == (base + ext).lower()), None)
            r = shapefile.Reader(shp=io.BytesIO(z.read(nome)),
                                 dbf=io.BytesIO(z.read(achar(".dbf"))) if achar(".dbf") else None)
            for s in r.shapes():
                partes = list(s.parts) + [len(s.points)]
                for a, b in zip(partes[:-1], partes[1:]):
                    pts = [(p[0], p[1]) for p in s.points[a:b]]
                    if len(pts) >= 3:
                        rings.append(pts)
    return rings


def ler(caminho):
    n = caminho.lower()
    dados = open(caminho, "rb").read()
    if n.endswith(".kmz"):
        z = zipfile.ZipFile(io.BytesIO(dados))
        kml = next(x for x in z.namelist() if x.lower().endswith(".kml"))
        return parse_kml(z.read(kml))
    if n.endswith((".geojson", ".json")):
        return parse_geojson(json.loads(dados))
    if n.endswith(".zip"):
        return parse_shp_zip(dados)
    return parse_kml(dados)


def enquadrar(modo, rings):
    p = MARGEM[modo]
    xs = [lx(a) for r in rings for a, _ in r]
    ys = [ly(b) for r in rings for _, b in r]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    esq = (round(W * RESERVA) if modo == "capa" else 0) + p
    dir_, top, base = W - p, p, H - p
    lw, lh = max(40, dir_ - esq), max(40, base - top)
    bw, bh = max(maxx - minx, 1e-9), max(maxy - miny, 1e-9)
    zoom = max(3, min(MAXZ, math.log2(min(lw / bw, lh / bh))))
    k = 2 ** zoom
    fx, fy = (esq + dir_) / 2, (top + base) / 2
    return zoom, (minx + maxx) / 2 - (fx - W / 2) / k, (miny + maxy) / 2 - (fy - H / 2) / k


SESSAO = requests.Session()
SESSAO.headers["User-Agent"] = "gerador-capa-meta"


def baixar(z, y, x):
    for _ in range(3):
        try:
            r = SESSAO.get(TILE.format(z=z, y=y, x=x), timeout=20)
            if r.ok:
                return Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception:
            pass
    return None


def renderizar(rings, vista):
    zf, cx, cy = vista
    s = ESCALA
    z = max(0, min(MAXZ, round(zf)))
    f = 2 ** (zf - z)
    k = s * f
    w, h = W / f, H / f
    x0, y0 = cx * 2 ** z - w / 2, cy * 2 ** z - h / 2
    img = Image.new("RGB", (W * s, H * s), (16, 35, 26))
    n = 2 ** z
    tiles = [(tx, ty) for tx in range(math.floor(x0 / T), math.floor((x0 + w - 1e-6) / T) + 1)
             for ty in range(math.floor(y0 / T), math.floor((y0 + h - 1e-6) / T) + 1) if 0 <= ty < n]
    with ThreadPoolExecutor(8) as ex:
        imgs = list(ex.map(lambda t: baixar(z, t[1], t[0] % n), tiles))
    falhas = 0
    for (tx, ty), im in zip(tiles, imgs):
        if im is None:
            falhas += 1
            continue
        d = math.ceil(T * k) + 1
        img.paste(im.resize((d, d), Image.LANCZOS), (math.floor((tx * T - x0) * k), math.floor((ty * T - y0) * k)))
    dr = ImageDraw.Draw(img)
    esc = 2 ** z
    lw = max(1, round(ESPESSURA * s))
    for r in rings:
        pts = [((lx(a) * esc - x0) * k, (ly(b) * esc - y0) * k) for a, b in r]
        dr.line(pts + [pts[0]], fill=COR, width=lw, joint="curve")
    return img, falhas, len(tiles)


def slug(t):
    t = unicodedata.normalize("NFD", t).encode("ascii", "ignore").decode()
    return re.sub(r"[^\w\- ]+", "", t).strip() or "propriedade"


def main():
    feitos = 0
    for nome in sorted(os.listdir("entrada")):
        caminho = os.path.join("entrada", nome)
        if not os.path.isfile(caminho) or nome.startswith("."):
            continue
        base = slug(os.path.splitext(nome)[0])
        destino = os.path.join("saida", base)
        if os.path.exists(os.path.join(destino, "capa.png")) and not os.environ.get("FORCAR"):
            continue  # ja gerado; para refazer, apague saida/<nome>/
        try:
            rings = ler(caminho)
            if not rings:
                raise ValueError("nenhum polígono encontrado")
        except Exception as e:
            os.makedirs(destino, exist_ok=True)
            open(os.path.join(destino, "erro.txt"), "w").write(f"{nome}: {e}\n")
            print("ERRO", nome, e)
            continue
        os.makedirs(destino, exist_ok=True)
        info = {"arquivo": nome, "poligonos": len(rings)}
        for modo in ("capa", "escopo"):
            img, falhas, total = renderizar(rings, enquadrar(modo, rings))
            img.save(os.path.join(destino, f"{modo}.png"), optimize=True)
            img.convert("RGB").resize((1280, 720), Image.LANCZOS).save(
                os.path.join(destino, f"{modo}.jpg"), quality=86, optimize=True)
            info[modo] = {"tiles": total, "falhas": falhas}
        open(os.path.join(destino, "info.json"), "w").write(json.dumps(info, ensure_ascii=False, indent=1))
        ep = os.path.join(destino, "erro.txt")
        if os.path.exists(ep):
            os.remove(ep)
        feitos += 1
        print("OK", nome, info)
    print("Gerados:", feitos)


if __name__ == "__main__":
    main()
