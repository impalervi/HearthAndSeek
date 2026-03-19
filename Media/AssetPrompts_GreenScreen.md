# Asset Prompts — Green Screen Background

These prompts instruct Gemini to render assets on a solid bright green (#00FF00) background instead of a "transparent" checkerboard. This makes extraction trivial via color-keying.

**Key changes from original prompts:**
- Replaced "transparent background" with "solid bright green (#00FF00) background"
- Added explicit instruction to NOT use checkerboard/checkered patterns
- Added note that the green will be removed in post-processing
- Increased button dimensions to 256x64 (better detail for WoW textures)

---

## Asset 1: Title Bar Background (horizontal tileable strip with border art)

Create a seamless horizontally-tileable dark texture strip for a World of Warcraft addon UI title bar. The texture should be a dark metallic/stone surface with subtle weathering and rivets or runic engravings along the edges. Color palette: dark charcoal (#1a1a1a to #2a2a2a) with faint bronze/gold highlights (#3a3020) on the edges. The texture should tile seamlessly left-to-right. Dimensions: 512 pixels wide by 64 pixels tall. PNG format, fully opaque, NO transparency. Style: hand-painted Blizzard Entertainment World of Warcraft UI art style, dark fantasy.

---

## Asset 1b: Filter Bar Background (horizontal tileable strip, plain)

Create a seamless horizontally-tileable dark texture strip for a World of Warcraft addon UI filter bar. This should have the SAME rich, hand-painted dark metallic/stone surface material as Asset 1 — with visible brushwork, subtle scratches, weathering marks, surface imperfections, and slight tonal variation across the metal plating. The surface should feel like aged, worn dark iron or stone with real depth and texture. **The only difference from Asset 1: do NOT include the rivets, runic engravings, or decorative border elements along the edges.** Keep ALL the surface material detail — just remove the edge ornamentation. Color palette: dark charcoal (#1a1a1a to #2a2a2a) with faint bronze/gold tonal hints (#3a3020) in the surface weathering. The texture should tile seamlessly left-to-right AND top-to-bottom. Dimensions: 512 pixels wide by 64 pixels tall. PNG format, fully opaque, NO transparency. Style: hand-painted Blizzard Entertainment World of Warcraft UI art style, dark fantasy — richly textured dark metal plate without edge decoration.

---

## Asset 2: Dropdown Panel Background (9-slice border source)

Create a dropdown panel frame for a World of Warcraft addon UI. This will be used as a 9-slice border (corners + edges + center tile). **IMPORTANT: Keep the borders and corner pieces thin and minimal — no thicker than 8-10 pixels at 256px scale.** The panel should have: small, understated bronze/gold corner accents (NOT large ornate corner pieces), very thin gold edge borders (1-2px), and a dark semi-transparent interior (#0d0d0d at 90% opacity). The corners should be subtle — small metallic rivets or simple angular brackets, not elaborate scrollwork. Overall style: clean dark panel with minimal gold trim, similar to WoW's tooltip panels. Dimensions: 256 pixels by 256 pixels. PNG format. **Place the panel on a solid bright green (#00FF00) background.** All areas outside the border frame should be this solid green — do NOT use a checkerboard or checkered pattern. The green background will be removed in post-processing. Style: hand-painted Blizzard Entertainment World of Warcraft UI art, dark fantasy.

---

## Asset 3: Progress Bar Fill (thin, uniform/tileable)

Create a horizontal fill texture for a progress bar in a World of Warcraft addon UI. **IMPORTANT: The color and pattern must be uniform across the entire WIDTH (left-to-right) — do NOT use a left-to-right gradient or fade. The texture must tile seamlessly horizontally.** However, the bar SHOULD have a vertical gradient from TOP to BOTTOM: bright, luminous gold (#f0d060 to #e8c040) at the top edge, transitioning smoothly to a deeper, richer dark gold/amber (#9a7010 to #7a5808) at the bottom edge. This top-to-bottom fade gives the bar a polished, 3D metallic look. The surface should have a visible gold mineral/ore texture throughout — think of natural gold veins in rock, or polished gold nugget surface with subtle crystalline grain and mineral variation. NOT smooth or flat — the surface should have organic, mineral-like detail (tiny flecks, grain, subtle roughness) that makes it feel like real gold ore or a gold mineral deposit. Dimensions: 256 pixels wide by 32 pixels tall. PNG format. **Place the bar on a solid bright green (#00FF00) background.** All areas outside the bar shape should be this solid green — do NOT use a checkerboard or checkered pattern. The green background will be removed in post-processing. Style: hand-painted World of Warcraft UI art, gold mineral/ore texture with vertical brightness gradient.

---

## Asset 4: Filter Button (Normal State)

Create a button texture for a World of Warcraft addon UI filter button in its normal/idle state. Dark metal plate with subtle beveled edges and a faint bronze border. Should look like a clickable tab or plate. Dimensions: 256 pixels wide by 64 pixels tall. PNG format. **Place the button on a solid bright green (#00FF00) background.** All areas outside the button shape should be this solid green — do NOT use a checkerboard or checkered pattern. The green background will be removed in post-processing. Style: hand-painted Blizzard Entertainment World of Warcraft UI, dark fantasy metallic.

---

## Asset 5: Filter Button (Hover/Active State)

Create a button texture for a World of Warcraft addon UI filter button in its highlighted/active state. **IMPORTANT: This must be the exact same rectangular plate shape and silhouette as the normal state button (Asset 4) — same corners, same beveled edges, same overall outline. Do NOT add cut-out corners, diamond shapes, or any structural changes to the button shape.** The only differences from the normal state should be: (1) the existing thin border gains a warm golden glow/highlight color instead of dark bronze, (2) the interior surface is slightly lighter (#2a2a2a to #3a3a3a instead of #1a1a1a), and (3) a subtle warm light bloom along the inner edges of the border. The button should look "selected" through lighting and color changes only, not through shape changes. Dimensions: 256 pixels wide by 64 pixels tall. PNG format. **Place the button on a solid bright green (#00FF00) background.** All areas outside the button shape should be this solid green — do NOT use a checkerboard or checkered pattern. The green background will be removed in post-processing. Style: hand-painted Blizzard Entertainment World of Warcraft UI, dark fantasy metallic with golden highlight.

---

# Frame Textures — Separate Pieces for 9-Slice

These frame borders are split into separate corner, edge, and background pieces so they resize properly at any frame dimension. Each piece is generated separately.

**Strategy:** WoW's 9-slice system needs:
- 4 corner pieces (fixed size, never stretched)
- 4 edge pieces (tiled along the frame edges)
- 1 background fill (tiled to fill the interior)

We generate ONE corner and ONE horizontal edge per frame style, then rotate/flip them in the processing pipeline.

**Style guide:** Very subtle, matching the filter bar background (Asset 1). Dark charcoal metal, barely-visible borders, minimal corner accents. Nothing thick, ornate, or heavy.

---

## Asset 6a: Main Frame — Corner Piece

Create a single corner piece (top-left) for a World of Warcraft addon frame border. This is a small, subtle corner accent — NOT an elaborate ornamental bracket. Think: a tiny dark metal rivet or angular bracket with the faintest bronze highlight (#3a3020), sitting on a dark charcoal (#1a1a1a) surface. The corner decoration should be concentrated in a small area near the actual corner (upper-left quadrant of the image). Keep it minimal — a small metallic accent mark, maybe 8-12 pixels of actual detail. The rest of the image should be the dark charcoal background color, fading seamlessly into the edge and interior. Dimensions: 64 pixels by 64 pixels. PNG format. **Place on a solid bright green (#00FF00) background.** All areas that should be transparent (outside the corner piece) should be this solid green — do NOT use a checkerboard pattern. The green will be removed in post-processing. Style: extremely subtle, hand-painted World of Warcraft UI, dark fantasy. Match the look of the filter bar background texture (dark metal with faint gold details).

---

## Asset 6b: Main Frame — Horizontal Edge (tileable)

Create a seamless horizontally-tileable edge strip for a World of Warcraft addon frame border. This is the TOP edge of the frame. It should be a very thin, subtle border — a dark metal strip with a barely-visible bronze/gold trim line (1-2px) along the outer edge. The rest should be the dark charcoal (#1a1a1a) interior color. **IMPORTANT: The pattern must tile seamlessly left-to-right and must be uniform across the entire length.** Keep it extremely understated — like the filter bar background but even thinner. This edge should look like a subtle separation line, not a decorative border. Dimensions: 256 pixels wide by 16 pixels tall. The actual visible border detail should be in the top 4-6 pixels only; the bottom portion should be the dark interior fill. PNG format. **Place on a solid bright green (#00FF00) background.** Any transparent areas should be this solid green — do NOT use a checkerboard pattern. The green will be removed in post-processing. Style: extremely subtle, hand-painted World of Warcraft UI, dark fantasy metallic.

---

## Asset 6c: Main Frame — Background Fill (tileable)

Create a seamless tileable dark background texture for a World of Warcraft addon frame interior. This fills the center of the frame. Very similar to Asset 1 (filter bar background) but optimized for tiling in BOTH directions — must tile seamlessly both horizontally and vertically. Dark charcoal metallic/stone surface (#0d0d0d to #1a1a1a) with very subtle noise/weathering. NO border details, NO gold accents — this is pure interior fill. Dimensions: 128 pixels by 128 pixels. PNG format, fully opaque, NO transparency. Style: hand-painted World of Warcraft UI, dark fantasy, matching the filter bar background texture.

---

## Asset 7a: Detail Panel — Corner Piece

Create a single corner piece (top-left) for a secondary panel border in a World of Warcraft addon UI. Same approach as Asset 6a but slightly lighter/softer — dark metal (#222222) with a very faint bronze inlay accent. Even more understated than the main frame corner. The decoration should be a tiny accent mark in the upper-left area, 6-10 pixels of detail. Dimensions: 32 pixels by 32 pixels. PNG format. **Place on a solid bright green (#00FF00) background.** Transparent areas should be solid green, not checkered. Style: extremely subtle, matching the main frame theme but lighter.

---

## Asset 7b: Detail Panel — Horizontal Edge (tileable)

Create a seamless horizontally-tileable edge strip for a secondary panel border. Even thinner and more subtle than Asset 6b. A barely-visible dark bronze trim line (1px) along the outer edge, with the rest being dark interior fill (#111111). **Must tile seamlessly left-to-right with uniform pattern.** Dimensions: 128 pixels wide by 8 pixels tall. Visible border detail in top 2-3 pixels only. PNG format. **Place on a solid bright green (#00FF00) background.** Transparent areas should be solid green. Style: extremely subtle, hand-painted World of Warcraft UI.

---

## Asset 8a: Settings Panel — Corner Piece

Create a single corner piece (top-left) for a settings/popup panel border. Same minimal style as Asset 7a — tiny metallic rivet or dot accent with faint bronze tone. Dimensions: 32 pixels by 32 pixels. PNG format. **Place on a solid bright green (#00FF00) background.** Style: extremely subtle dark fantasy metallic.

---

## Asset 8b: Settings Panel — Horizontal Edge (tileable)

Create a seamless horizontally-tileable edge strip for a settings panel border. Identical approach to Asset 7b — very thin (1px) dark bronze trim line. **Must tile seamlessly left-to-right.** Dimensions: 128 pixels wide by 8 pixels tall. PNG format. **Place on a solid bright green (#00FF00) background.** Style: extremely subtle, matching the other frame edges.

---

# Small UI Elements

These will be resized down in post-processing. Generate them at full canvas size with clean, sharp edges.

---

## Asset 9a: Reset Filters Button — Normal State

Create a single button texture for a World of Warcraft addon UI "Reset Filters" button in its **normal/idle state**.

A dark rectangular plate with slightly rounded corners that fills most of the canvas. Dark red-tinted surface (#33201f to #3a2420) with a thin muted dark red border (#5a2a2a to #6a3030). The overall feel should be a dark, muted crimson — clearly red-tinted but subtle and understated. Clean, flat color with a darker red border. No ornate detail, no text, no icons — just the plate shape.

Dimensions: 256 pixels wide by 64 pixels tall. The button plate should fill roughly 220x50 of the canvas, centered. PNG format. **CRITICAL BACKGROUND REQUIREMENT: The ENTIRE background must be a single flat solid color: bright green, hex #00FF00, RGB(0,255,0). Every single pixel that is not part of the button must be this exact green. DO NOT use transparency. DO NOT use a checkerboard pattern. DO NOT use a checkered pattern. DO NOT use any pattern whatsoever. DO NOT use gray and white squares. The background is ONE solid uniform color: green #00FF00. This is for chroma-key removal in post-processing — any pattern or variation in the background will break the pipeline.** Style: clean, flat, minimal — World of Warcraft UI, dark fantasy metallic.

---

## Asset 9b: Reset Filters Button — Hover State

Create a single button texture for a World of Warcraft addon UI button in its **hover/highlighted state**.

A dark rectangular plate with slightly rounded corners that fills most of the canvas. Bright red-tinted surface (#4d2626 to #553030) with a warm vivid red border (#8a4040 to #aa5555). The plate should have a faint warm red glow along the inner edge of the border. Clean, flat color — no ornate detail, no text, no icons, no shadows, no glow outside the button, no particles, no extra elements. ONLY the single rectangular plate shape on the green background. Nothing else.

Dimensions: 256 pixels wide by 64 pixels tall. The button plate should fill roughly 220x50 of the canvas, centered. PNG format. **CRITICAL BACKGROUND REQUIREMENT: The ENTIRE background must be a single flat solid color: bright green, hex #00FF00, RGB(0,255,0). Every single pixel that is not part of the button must be this exact green. DO NOT use transparency. DO NOT use a checkerboard pattern. DO NOT use a checkered pattern. DO NOT use any pattern whatsoever. DO NOT use gray and white squares. The background is ONE solid uniform color: green #00FF00. This is for chroma-key removal in post-processing — any pattern or variation in the background will break the pipeline.** Style: clean, flat, minimal — World of Warcraft UI, dark fantasy metallic.

---

## Asset 10a: Filter Pill / Active Filter Tag — Normal State

Create a single pill-shaped tag texture for a World of Warcraft addon UI "active filter" indicator in its **normal/idle state**.

A pill-shaped plate (rounded rectangle with generously rounded ends) that fills most of the canvas. Dark charcoal-blue surface (#14141a to #1a1a22) with a thin bronze/gold border (#4a4030 to #5a5040). Clean, flat, minimal — no texture, no gradients, no ornamentation, no text, no icons. Just the pill shape.

Dimensions: 256 pixels wide by 64 pixels tall. The pill should fill roughly 220x50 of the canvas, centered. PNG format. **CRITICAL BACKGROUND REQUIREMENT: The ENTIRE background must be a single flat solid color: bright green, hex #00FF00, RGB(0,255,0). Every single pixel that is not part of the pill must be this exact green. DO NOT use transparency. DO NOT use a checkerboard pattern. DO NOT use a checkered pattern. DO NOT use any pattern whatsoever. DO NOT use gray and white squares. The background is ONE solid uniform color: green #00FF00. This is for chroma-key removal in post-processing — any pattern or variation in the background will break the pipeline.** Style: clean, flat, minimal — World of Warcraft UI, dark fantasy metallic.

---

## Asset 10b: Filter Pill / Active Filter Tag — Hover State

Create a single pill-shaped tag texture for a World of Warcraft addon UI "active filter" indicator in its **hover/close state**. This must be the **exact same pill shape, size, and proportions** as Asset 10a (the normal state) — same rounded ends, same border thickness, same silhouette.

The only differences from the normal state: Slightly brighter interior (#222228 to #28282e). The border brightens to gold (#7a6840 to #8a7850). A very subtle warm inner glow. Just a color shift, no structural changes. No text, no icons.

Dimensions: 256 pixels wide by 64 pixels tall. The pill should fill roughly 220x50 of the canvas, centered. PNG format. **CRITICAL BACKGROUND REQUIREMENT: The ENTIRE background must be a single flat solid color: bright green, hex #00FF00, RGB(0,255,0). Every single pixel that is not part of the pill must be this exact green. DO NOT use transparency. DO NOT use a checkerboard pattern. DO NOT use a checkered pattern. DO NOT use any pattern whatsoever. DO NOT use gray and white squares. The background is ONE solid uniform color: green #00FF00. This is for chroma-key removal in post-processing — any pattern or variation in the background will break the pipeline.** Style: clean, flat, minimal — World of Warcraft UI, dark fantasy metallic.

---

## Asset 11a: Settings Cogwheel Button — Normal State

Create a settings button for a World of Warcraft addon UI in its **normal/idle state**. The button consists of a cogwheel (gear) icon on a dark red square background plate.

**Background plate:** A square plate with slightly rounded corners. Dark red surface (#3a1818 to #4a2020) with a thin gray metallic border (#606060 to #707070) around the outside edge. The plate fills roughly 220x220 of the canvas, centered.

**Cogwheel icon:** Centered on the red plate, a classic **8-tooth** cogwheel/gear shape. The gear is a single flat toothed ring of polished bronze/gold metal — warm gold surface (#b8973a to #d4a840) with lighter gold highlights (#e8c850) on the tooth tips and outer rim. The center hole shows the red background plate beneath. The gear teeth are evenly spaced, broad, and rounded — a clean, readable silhouette. The gear fills roughly 160x160 pixels within the plate. The metal surface has a slightly weathered, hand-forged look with subtle surface variation. **Do NOT add inner rings, inner dents, recessed channels, spokes, hub details, or any structural embellishment around the center hole. The gear is just a flat toothed ring with a clean round hole — nothing else.**

No text, no extra elements, no shadows outside the button, no particles, no glow effects. ONLY the single button (plate + gear) on the green background.

Dimensions: 256 pixels wide by 256 pixels tall. PNG format. **CRITICAL BACKGROUND REQUIREMENT: The ENTIRE background must be a single flat solid color: bright green, hex #00FF00, RGB(0,255,0). Every single pixel that is not part of the button must be this exact green. DO NOT use transparency. DO NOT use a checkerboard pattern. DO NOT use a checkered pattern. DO NOT use any pattern whatsoever. DO NOT use gray and white squares. The background is ONE solid uniform color: green #00FF00. This is for chroma-key removal in post-processing — any pattern or variation in the background will break the pipeline.** Style: hand-painted Blizzard Entertainment World of Warcraft UI, dark fantasy metallic.

---

## Asset 11b: Settings Cogwheel Button — Hover State

Create a settings button for a World of Warcraft addon UI in its **hover/highlighted state**. The button consists of a cogwheel (gear) icon on a red square background plate.

**Background plate:** A square plate with slightly rounded corners — **identical shape, size, proportions, and corner rounding as the normal state (Asset 11a)**. Brighter red surface (#5a2020 to #6a2828) with a thin gray metallic border (#808080 to #909090) around the outside edge, slightly brighter than the normal state border. The plate fills roughly 220x220 of the canvas, centered.

**Cogwheel icon:** Centered on the red plate, a classic **8-tooth** cogwheel/gear shape. The gear is a single flat toothed ring of polished gold metal — bright warm gold surface (#d4a840 to #e8c050) with bright gold highlights (#f0d860 to #ffe070) on the tooth tips and outer rim. The gear sits ON TOP of the solid red plate — the red plate is fully solid and continuous behind the gear, with the red surface visible through the gear's center opening. **There is NO hole or cutout through the button — the red plate is opaque and unbroken.** The gear teeth are evenly spaced, broad, and rounded. The gear fills roughly 160x160 pixels within the plate. A subtle golden luminous glow along the gear edges. The metal surface has the same slightly weathered, hand-forged look. **CRITICAL: Do NOT add inner rings, inner dents, recessed channels, spokes, hub details, or any structural embellishment around the center. The gear is just a flat toothed ring sitting on a solid red plate. Do NOT punch a hole through the red plate. The ONLY differences from the normal state are: brighter red plate, brighter gold color, subtle glow. No structural changes whatsoever.**

No text, no extra elements, no shadows outside the button, no particles. ONLY the single button (plate + gear) on the green background.

Dimensions: 256 pixels wide by 256 pixels tall. PNG format. **CRITICAL BACKGROUND REQUIREMENT: The ENTIRE background must be a single flat solid color: bright green, hex #00FF00, RGB(0,255,0). Every single pixel that is not part of the button must be this exact green. DO NOT use transparency. DO NOT use a checkerboard pattern. DO NOT use a checkered pattern. DO NOT use any pattern whatsoever. DO NOT use gray and white squares. The background is ONE solid uniform color: green #00FF00. This is for chroma-key removal in post-processing — any pattern or variation in the background will break the pipeline.** Style: hand-painted Blizzard Entertainment World of Warcraft UI, dark fantasy metallic.

---

# Processing Notes

The pipeline will:
1. Process each corner piece → rotate 90°/180°/270° to create all 4 corners
2. Process each horizontal edge → rotate 90° to create vertical edges
3. All panels share Asset 6c as background fill
4. Each piece saved as separate TGA for Lua-side 9-slice assembly
5. Lua code anchors corners at fixed size, tiles edges along frame perimeter
