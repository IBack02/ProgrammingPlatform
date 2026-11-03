"""Loader motion is presentation metadata; published artwork stays immutable."""

LOADER_ANIMATIONS = {
    "nis-coder": "rotate",
    "nis-mathematician": "geometry",
    "nis-chemist": "flask",
    "nis-roboticist": "robot",
    "nis-artist": "palette",
    "nis-biologist": "leaf",
    "nis-physicist": "wave",
    "nis-debater": "speech",
    "nis-astronomer": "orbit",
    "nis-musician": "note",
    "nis-chessplayer": "fade",
    "nis-inventor": "gear",
}


def loader_animation(hero_id, slot, fallback="none"):
    return LOADER_ANIMATIONS.get(hero_id, fallback) if slot == "ui_loader" else fallback
