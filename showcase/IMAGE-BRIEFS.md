# Mirally campaign imagery

The campaign images were generated on 2026-10-15 through the imagegen skill's bundled API/CLI fallback, explicitly authorized by the user, using the current Codex API provider and `gpt-image-2`. Preserve the existing Companion M logo; all copy and interface elements are rendered in HTML. All four displayed campaign scenes use AI-generated imagery; temporary editorial photography was removed from the deliverable.

## 01 — A quieter kind of intelligence

Use case: ads-marketing
Asset type: website hero, landscape 3:2
Primary request: Premium editorial lifestyle photograph for Mirally, an Everyday Ally. A warm, lived-in contemporary home workspace beside a large window, a young adult seen naturally from behind or in profile working on a slim laptop, soft coastal daylight, pale blue sky visible outside, linen curtain, warm oak, ceramic coffee cup and a single leafy plant. Human, calm and aspirational, with authentic surface texture and subtle film grain.
Composition: wide environmental framing, subject and laptop on the right half, uncluttered negative space on the left; no need to show screen details. Depth and natural asymmetry, not a stock office handshake.
Palette: pale sky blue, warm ivory, muted teal and warm wood.
Constraints: no text, no logos, no watermark, no robots, no neon circuitry, no artificial UI. Use the resulting image as a reference for the next scene to preserve the same person and home.

## 02 — Curiosity after hours

Use case: ads-marketing
Asset type: evening entertainment section, landscape 3:2
Primary request: The same person's home at blue hour, an inviting sofa facing a television, warm table lamp, a blanket casually folded, a person relaxing while watching a fictional coastal drama. Window reveals an indigo evening sky. An intimate, candid, cinematic moment; technology belongs quietly to the room.
Composition: environmental wide shot with screen or projection on the left and person on the right, enough negative space for a separately rendered conversation card.
Palette: deep blue, desaturated sea green, warm amber and ivory.
Constraints: no readable screen text, no identifiable real actors or shows, no logos or watermark. No generated UI; preserve visual continuity with the daylight image.

## 03 — A life, connected

Use case: photorealistic-natural
Asset type: closing vision, ultrawide landscape
Primary request: A poetic panoramic coastline at dawn, soft luminous horizon, distant hills, small human figure walking along a curving path. Optimism, openness and a sense of possibility. High-end travel editorial, restrained realistic color, subtle atmospheric depth.
Composition: large open sky and water, no literal technology, natural texture, clean central space for HTML typography.
Constraints: no lettering, no brand, no watermark, no futuristic city or robots.

## Production notes

- Preferred: built-in image generation. If unavailable, the imagegen skill's API/CLI fallback requires explicit user choice and an OPENAI_API_KEY.
- Export final approved images as WebP/AVIF in `public/images/`, and record actual generation tool, prompts and attribution here.
- Keep text, brand marks and functional interfaces separate from image pixels.

## Actual generated assets

- `public/images/hero-ai.webp`: 1536 × 1024, high quality generation, warm daylight home workspace. Original: `.artifacts/hero-ai.png`. Exact prompt: `image-prompts/hero-prompt.txt`.
- `public/images/evening-ai.webp`: 1536 × 1024, high quality image edit using the hero image as the visual reference, preserving the same person and home atmosphere. Original: `.artifacts/evening-ai.png`. Exact prompt: `image-prompts/evening-prompt.txt`.
- `public/images/vision-ai.webp`: high quality coastal dawn generation; requested 2048 × 1152 and the provider returned 1672 × 941. Original and exact prompt: `.artifacts/vision-ai.png`, `image-prompts/vision-prompt.txt`.
- `public/images/story-ai.webp`: 1536 × 1024, high quality fictional coastal-drama still for the interactive character-identification concept. Fictional photographer Xu Zhou appears clearly on the left in a khaki jacket with a film camera; the sea on the right accommodates HTML conversation overlays. Original and exact prompt: `.artifacts/story-ai.png`, `image-prompts/story-prompt.txt`. No real performer or existing drama is represented.
- Prompts contain no product credentials. Private API configuration is neither copied into the showcase nor changed. No real actors or shows are depicted in the evening image.
- Each delivered image is visually inspected before integration; WebP copies use quality 90. The original generated PNG files are retained for future crops and exports.
