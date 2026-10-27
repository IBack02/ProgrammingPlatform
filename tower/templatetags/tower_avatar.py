from django import template

from tower.character_contracts import load_avatar_geometry

register = template.Library()


@register.simple_tag
def avatar_geometry():
    return load_avatar_geometry().model_dump(mode="json")
