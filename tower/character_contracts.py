"""Version 1 contracts for character sets; no grants, rendering or AI calls."""
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


CONTENT_DIR = Path(__file__).parent / "content"
SHAPE_IDS = ("square", "diamond", "triangle", "pentagon", "hexagon")
SET_SLOTS = ("head", "prop", "background", "ui_loader", "ui_decoration", "companion")
ANCHOR_IDS = ("head", "hand", "side", "ground")
ShapeId = Literal["square", "diamond", "triangle", "pentagon", "hexagon"]
SetSlot = Literal["head", "prop", "background", "ui_loader", "ui_decoration", "companion"]
SelectionSlot = Literal["head", "prop", "background", "ui_loader", "ui_decoration", "companion", "glasses", "mustache"]
AnchorId = Literal["head", "hand", "side", "ground"]
Slug = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", max_length=80)]
Unit = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
Angle = Annotated[float, Field(strict=True, ge=-180, le=180, allow_inf_nan=False)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
Label = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=160)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="before")
    @classmethod
    def strict_version_number(cls, value):
        if isinstance(value, dict) and "schema_version" in value and type(value["schema_version"]) is not int:
            raise ValueError("Schema versions must be integers, not booleans or coerced numbers.")
        return value


class Point(Contract):
    x: Unit
    y: Unit


class Anchor(Contract):
    point: Point
    span: Annotated[float, Field(strict=True, gt=0, le=1, allow_inf_nan=False)]
    rotation: Angle = 0.0


class ShapeGeometry(Contract):
    anchors: dict[AnchorId, Anchor]

    @model_validator(mode="after")
    def complete_anchors(self):
        if set(self.anchors) != set(ANCHOR_IDS):
            raise ValueError("Every shape needs head, hand, side and ground anchors.")
        return self


class AvatarGeometry(Contract):
    schema_version: Literal[1]
    shapes: dict[ShapeId, ShapeGeometry]

    @model_validator(mode="after")
    def complete_shapes(self):
        if set(self.shapes) != set(SHAPE_IDS):
            raise ValueError("Geometry must cover all five existing shapes.")
        return self


class Asset(Contract):
    # Relative paths only: URLs are resolved after publication by the asset layer.
    path: Annotated[str, StringConstraints(strict=True, pattern=r"^sets/[a-z0-9-]+/v[1-9][0-9]*/[a-z0-9-]+\.(?:png|webp|jpg|jpeg|svg)$")]
    width: Annotated[int, Field(strict=True, gt=0, le=8192)]
    height: Annotated[int, Field(strict=True, gt=0, le=8192)]


class Attachment(Contract):
    anchor: AnchorId
    pivot: Point
    width_ratio: Annotated[float, Field(strict=True, gt=0, le=3, allow_inf_nan=False)]
    rotation: Angle = 0.0
    layer: Literal["behind", "front"] = "front"


class LocalizedLabels(Contract):
    ru: Label
    kk: Label
    en: Label


class SetComponent(Contract):
    id: Slug
    slot: SetSlot
    labels: LocalizedLabels
    asset: Asset
    attachment: Attachment | None = None
    behavior_profile: Slug | None = None
    animation: Literal["none", "rotate"] = "none"
    placeholder: bool = False

    @model_validator(mode="after")
    def slot_contract(self):
        if self.placeholder and self.slot != "companion":
            raise ValueError("Only a companion can use an explicit temporary placeholder.")
        if self.asset.path.endswith(".svg") and self.slot not in {"ui_loader", "ui_decoration", "companion"}:
            raise ValueError("SVG is reserved for authored interface elements and companion placeholders.")
        if self.slot == "companion" and self.asset.path.endswith(".svg") and not self.placeholder:
            raise ValueError("An SVG companion must be explicitly marked as a placeholder.")
        if (self.slot in {"head", "prop"}) != (self.attachment is not None):
            raise ValueError("Only head and prop components require an attachment.")
        if self.slot == "head" and self.attachment.anchor != "head":
            raise ValueError("Headwear must use the head anchor.")
        if self.slot == "prop" and self.attachment.anchor == "head":
            raise ValueError("Props use hand, side or ground anchors.")
        if (self.slot == "companion") != (self.behavior_profile is not None):
            raise ValueError("Only a companion requires a behavior profile reference.")
        if self.animation != "none" and self.slot != "ui_loader":
            raise ValueError("Only the platform loader can request a rotation animation.")
        return self


class CharacterSet(Contract):
    hero_id: Slug
    revision: PositiveInt
    status: Literal["draft", "published"]
    components: dict[SetSlot, SetComponent]

    @property
    def version_id(self):
        return f"{self.hero_id}-v{self.revision}"

    @model_validator(mode="after")
    def full_set(self):
        if set(self.components) != set(SET_SLOTS):
            raise ValueError("A complete character set has exactly six independent components.")
        for slot, component in self.components.items():
            if component.slot != slot or component.id != f"{self.hero_id}-{slot.replace('_', '-')}":
                raise ValueError("Component IDs and slots must belong to their character set.")
            if not component.asset.path.startswith(f"sets/{self.hero_id}/v{self.revision}/"):
                raise ValueError("Assets must use their own character and revision directory.")
        if self.components["companion"].behavior_profile != self.hero_id:
            raise ValueError("Each companion needs its own character's behavior profile.")
        return self


class CharacterSetCatalog(Contract):
    schema_version: Literal[1]
    sets: list[CharacterSet]

    @model_validator(mode="after")
    def unique_sets(self):
        heroes = [entry.hero_id for entry in self.sets]
        if len(heroes) != len(set(heroes)):
            raise ValueError("The current catalog may contain only one revision per hero.")
        return self

    def check_references(self, hero_ids, legacy_cosmetic_ids=()):
        if {entry.hero_id for entry in self.sets} != set(hero_ids):
            raise ValueError("The character-set catalog must cover the card catalog exactly.")
        ids = {component.id for entry in self.sets for component in entry.components.values()}
        if ids.intersection(legacy_cosmetic_ids):
            raise ValueError("Character components must not replace legacy cosmetics.")

    def published_sets(self):
        return tuple(entry for entry in self.sets if entry.status == "published")


class AppearanceSelection(Contract):
    """Selection references inventory; it never proves ownership or grants items."""
    schema_version: Literal[1] = 1
    shape: ShapeId = "square"
    color: Annotated[str, StringConstraints(strict=True, pattern=r"^#[0-9a-f]{6}$")] = "#3b82f6"
    legacy_theme: Slug = "midnight"
    slots: dict[SelectionSlot, Slug] = Field(default_factory=dict)

    @classmethod
    def from_legacy(cls, *, shape, color, accessories, theme):
        # Legacy palette/theme and new background image are separate settings.
        return cls(shape=shape, color=color, legacy_theme=theme,
                   slots={slot: item for slot, item in accessories.items() if item})

    def check_ownership(self, owned_slots):
        """owned_slots maps component ID -> its server-verified inventory slot."""
        selected = {**self.slots}
        if self.legacy_theme != "midnight":
            selected["theme"] = self.legacy_theme
        for slot, item in selected.items():
            if owned_slots.get(item) != slot:
                raise ValueError(f"Item {item!r} is not owned in slot {slot!r}.")


class BehaviorProfile(Contract):
    """Character delivery style and localized reference utterances."""
    hero_id: Slug
    revision: PositiveInt
    temperament: Label
    tone: Label
    vocabulary: Annotated[list[Label], Field(min_length=1, max_length=12)]
    humor: Label
    support_style: Label
    examples: dict[Literal["ru", "kk", "en"], Annotated[list[Label], Field(min_length=2, max_length=4)]]

    @model_validator(mode="after")
    def all_languages(self):
        if set(self.examples) != {"ru", "kk", "en"}:
            raise ValueError("Behavior examples are required in all platform languages.")
        return self


class BehaviorCatalog(Contract):
    schema_version: Literal[1]
    profiles: list[BehaviorProfile]

    @model_validator(mode="after")
    def unique_characters(self):
        heroes = [profile.hero_id for profile in self.profiles]
        if len(heroes) != len(set(heroes)):
            raise ValueError("Each character must have one current behavior profile.")
        return self

    def check_references(self, catalog):
        if {profile.hero_id for profile in self.profiles} != {entry.hero_id for entry in catalog.sets}:
            raise ValueError("Behavior profiles must cover the full character catalog.")


def load_behavior_profiles(path=None):
    return BehaviorCatalog.model_validate_json((path or CONTENT_DIR / "behavior_profiles.json").read_text(encoding="utf-8"))


def load_character_sets(path=None):
    return CharacterSetCatalog.model_validate_json((path or CONTENT_DIR / "character_sets.json").read_text(encoding="utf-8"))


def load_avatar_geometry(path=None):
    return AvatarGeometry.model_validate_json((path or CONTENT_DIR / "avatar_geometry.json").read_text(encoding="utf-8"))


def contract_schemas():
    return {model.__name__: model.model_json_schema() for model in
            (CharacterSetCatalog, AvatarGeometry, AppearanceSelection, BehaviorProfile, BehaviorCatalog)}
