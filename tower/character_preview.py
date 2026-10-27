"""Offline engineering preview, using schematic items rather than character art."""
from html import escape

from tower.avatar_geometry import place_attachment
from tower.character_contracts import Asset, Attachment, SHAPE_IDS


SHAPE_POINTS = {
    "square": "0,0 1,0 1,1 0,1",
    "diamond": "0.5,0.008 0.992,0.5 0.5,0.992 0.008,0.5",
    "triangle": "0.5,0 1,1 0,1",
    "pentagon": "0.5,0 1,0.38 0.82,1 0.18,1 0,0.38",
    "hexagon": "0.25,0 0.75,0 1,0.5 0.75,1 0.25,1 0,0.5",
}


def geometry_preview(geometry):
    head = Asset(path="sets/nis-coder/v1/head.webp", width=512, height=512)
    prop = Asset(path="sets/nis-coder/v1/prop.webp", width=512, height=512)
    head_attachment = Attachment(anchor="head", pivot={"x": 0.5, "y": 0.85}, width_ratio=1.12)
    cells = []
    size = 110
    for row, prop_anchor in enumerate(("hand", "side", "ground")):
        attachment = Attachment(anchor=prop_anchor, pivot={"x": 0.5, "y": 0.95 if prop_anchor == "ground" else 0.5},
                                width_ratio=0.8, rotation=-12.0 if prop_anchor == "hand" else 0.0)
        for column, shape in enumerate(SHAPE_IDS):
            hat = place_attachment(geometry=geometry, shape=shape, asset=head, attachment=head_attachment, size=size)
            item = place_attachment(geometry=geometry, shape=shape, asset=prop, attachment=attachment, size=size)
            anchors = geometry.shapes[shape].anchors
            markers = "".join(f'<circle cx="{anchors[key].point.x * size:g}" cy="{anchors[key].point.y * size:g}" r="3" fill="#ff6d7b" stroke="#fff"/>'
                              for key in ("head", prop_anchor))
            cells.append(f'''<g transform="translate({column * 188 + 18} {row * 233 + 109})" data-shape="{shape}" data-anchor="{prop_anchor}">
  <rect width="176" height="216" rx="14" fill="#1c2a3c"/>
  <text x="12" y="24" fill="#f5d797" font-size="15">{escape(shape)} · {escape(prop_anchor)}</text>
  <g transform="translate(29 76)">
    <polygon points="{SHAPE_POINTS[shape]}" transform="scale({size})" fill="#537ee8"/>
    <g transform="{hat.svg_transform()}" data-item="head">
      <path d="M48 435 L80 185 Q256 45 432 185 L464 435 Z" fill="#f4c76c" stroke="#3d2d20" stroke-width="12"/>
      <path d="M0 435 H512" stroke="#fff0c6" stroke-width="30"/>
    </g>
    <g transform="{item.svg_transform()}" data-item="prop">
      <rect x="60" y="26" width="392" height="460" rx="20" fill="#67c9bd" stroke="#193e43" stroke-width="18"/>
      <path d="M256 40 V470 M100 100 H210 M300 100 H412" stroke="#e2fff7" stroke-width="16"/>
    </g>{markers}
  </g>
</g>''')
    return '''<svg xmlns="http://www.w3.org/2000/svg" width="976" height="838" viewBox="0 0 976 838" role="img" aria-labelledby="title description">
<title id="title">Универсальные крепления аватарки</title>
<desc id="description">Одна шапка и один предмет на пяти фигурах. Три варианта крепления предмета. Красные точки показывают совпадение опоры предмета и фигуры.</desc>
<rect width="976" height="838" fill="#101a29"/>
<g font-family="Arial, sans-serif">
<text x="20" y="34" fill="#f5d797" font-size="24">Контракт креплений · версия 1</text>
<text x="20" y="61" fill="#c3d3e7" font-size="14">Красная точка — совмещение опоры предмета с креплением фигуры.</text>
<text x="20" y="84" fill="#c3d3e7" font-size="14">Схематические предметы; окончательная графика и калибровка — этап 2.</text>
''' + "\n".join(cells) + "\n</g></svg>\n"
