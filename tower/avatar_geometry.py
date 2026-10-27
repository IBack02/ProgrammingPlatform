"""Align an asset's pivot with a shape anchor in a square avatar viewport."""
import math
from dataclasses import dataclass

from tower.character_contracts import Asset, Attachment, AvatarGeometry


@dataclass(frozen=True)
class Placement:
    # Affine matrix maps ORIGINAL image pixels into avatar viewport pixels.
    a: float
    b: float
    c: float
    d: float
    tx: float
    ty: float
    layer: str

    def map_point(self, x, y):
        return self.a * x + self.c * y + self.tx, self.b * x + self.d * y + self.ty

    def svg_transform(self):
        return "matrix(" + " ".join(f"{value:.9g}" for value in (self.a, self.b, self.c, self.d, self.tx, self.ty)) + ")"


def place_attachment(*, geometry: AvatarGeometry, shape: str, asset: Asset, attachment: Attachment, size: float):
    if isinstance(size, bool) or not isinstance(size, (int, float)) or not math.isfinite(size) or size <= 0:
        raise ValueError("Avatar viewport size must be a positive finite number.")
    if shape not in geometry.shapes:
        raise ValueError("Unknown avatar shape.")
    anchor = geometry.shapes[shape].anchors[attachment.anchor]
    scale = size * anchor.span * attachment.width_ratio / asset.width
    radians = math.radians(anchor.rotation + attachment.rotation)
    a = d = scale * math.cos(radians)
    b = scale * math.sin(radians)
    c = -b
    pivot_x, pivot_y = attachment.pivot.x * asset.width, attachment.pivot.y * asset.height
    return Placement(a=a, b=b, c=c, d=d,
                     tx=size * anchor.point.x - a * pivot_x - c * pivot_y,
                     ty=size * anchor.point.y - b * pivot_x - d * pivot_y,
                     layer=attachment.layer)
