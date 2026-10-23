# Рамки карточек: готовые ассеты и промпты

Создано 23 октября 2026 года встроенным инструментом image_gen, режим stylized-concept. Четыре отдельные рамки: общий силуэт, геометрия и прозрачный центр. Портреты персонажей не редактировались и с рамкой не объединялись.

| Редкость | Цвет | Звёзды | Мастер PNG | WebP для приложения |
|---|---|---|---|---|
| common | Серебро | 2 | frames/common.png | tower/static/tower/frames/common.webp |
| rare | Сапфировый синий | 3 | frames/rare.png | tower/static/tower/frames/rare.webp |
| epic | Аметистовый фиолетовый | 4 | frames/epic.png | tower/static/tower/frames/epic.webp |
| legendary | Золото | 5 | frames/legendary.png | tower/static/tower/frames/legendary.webp |

Мастера находятся относительно этого документа в frames/, 1024×1536 RGBA. Web-экспорт 512×768 WebP с сохранением альфа-канала, примерно 65–78 КБ. Размер всей карточки остаётся 2:3. Экспорт только уменьшает и кодирует рамку; её рисунок не перерисовывается.

Звёзды не впечатываются в PNG: компонент накладывает точное число SVG-звёзд на нижнюю металлическую ленту. Имя и специализация тоже остаются отдельным HTML-слоем. Число звёзд обозначает редкость, а не силу или прокачку персонажа.

## Базовая генерация

Полный базовый промпт (встроенный инструмент, transparent_background=true):

```text
Use case: stylized-concept. Production game asset: exactly ONE portrait collectible card FRAME OVERLAY, isolated on a truly transparent background. Canvas width:height exactly 2:3, ideally 1024x1536. A flat front-facing orthographic rectangle, straight sides, very slightly rounded corners, no perspective. Original elegant fantasy school collectible frame inspired by fine foil trading cards: a coherent double metallic border, refined symmetrical filigree at all four corners, fine geometric curved engravings, restrained beveled polished metal highlights. IMPORTANT: the hollow central portrait opening MUST BE REAL ALPHA TRANSPARENCY, not black, white, parchment, a checkerboard picture or any colored panel. The portrait hole covers x=8%..92%, y=6%..86% of the canvas. The outside beyond the frame is also alpha transparent. Outer border aligned within x=2%..98%, y=1%..99%. Border thickness at sides about 4% of canvas width. Bottom border contains an elegant narrow metal ribbon at y=88%..96% reserved for programmatically overlaid star icons, keep its center plain and unlettered. Top and sides stay slender so a student's face and school tie remain visible. No background, no portrait, no character, no card art, NO STARS, no lettering, no numbers, no rarity word, no logo, no watermark, no extra floating objects. Crisp clean manufactured foil details, refined and readable at small scale, warm key light from upper left. Deliver the transparent frame only, not a preview composition, not a sheet of frames. 
```

Для common к нему добавлено:

```text
Rarity variant: COMMON. Palette: satin silver and cool platinum, restrained pale gray-blue recesses. Simple tasteful corner ornaments, minimal magical accents, a little engraved craftsmanship. Silver bottom ribbon. Do not add colored gemstones.
```

## Варианты той же рамки

В качестве цели редактирования использовался утверждённый common.png. Каждый вариант генерировался отдельным вызовом с transparent_background=true.

```text
Edit this supplied transparent frame asset. Change ONLY its metal palette to [PALETTE]. Preserve EXACTLY the canvas 1024x1536, 2:3 portrait aspect, frame placement, border widths, all four ornaments, curves, geometry and empty bottom ribbon. The central hollow region and outside of the frame remain true alpha transparency, do not fill them. This is the [RARITY] rarity frame in the SAME coherent collection. No stars, no text, no numbers, no gemstones, no portrait, no background, no additional objects. Output just one transparent overlay frame.
```

- rare / PALETTE: sapphire blue and cyan-silver metal, deep rich blue recessed inlays, a restrained cyan edge highlight.
- epic / PALETTE: amethyst violet metal with pale lilac-silver highlights and deep purple engraved recesses.
- legendary / PALETTE: rich warm champagne gold with amber highlights and deep antique-gold engraved recesses.

## Использование

- Манифест: tower/content/cards.json; соответствия редкости, цвета рамки и числа звёзд едины для каталога.
- Общий компонент: tower/templates/tower/_collection_card.html.
- Портреты: TOWER_ASSET_BASE_URL + /chars/nis-*.png.
- Рамки: Django static/WhiteNoise; загружать их в Cloudflare для работы этого варианта не требуется.
- Авторизованный предпросмотр: /teacher/tower/preview/.
- Переносимый локальный пример: artifacts/tower/frame-preview/index.html; изображения персонажей требуют интернета.

Проверки: все четыре мастера и web-копии имеют альфа-канал и одинаковую геометрию 2:3, середина прозрачна. Количество звёзд проверяется в тестах компонента. Все 12 публичных портретов в chars/ проверены HTTP-запросами, каждый вернул 200 и PNG 1024×1536.

