# Mirally campaign imagery

The campaign images were generated on 2026-10-15 through the imagegen skill's bundled API/CLI fallback, explicitly authorized by the user, using the current Codex API provider and `gpt-image-2`. Preserve the existing Companion M logo; product copy and interactive interface elements are rendered in HTML. The work desktop background and capability images include fictional, non-interactive screen content. All nine campaign images use AI-generated imagery; temporary editorial photography was removed from the deliverable. Imagery illustrates use settings, not actual customer endorsements or unimplemented product interfaces.

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

- `public/images/capability-chat-ai.webp`: 1536 × 1024, a person using a laptop beside a sunlit window, with a document and reference material. 177,538 bytes. Exact prompt: `image-prompts/capability-chat-prompt.txt`.
- `public/images/capability-task-ai.webp`: 1536 × 1024, completed documents and charts on a laptop and an organized desk. 132,620 bytes. Exact prompt: `image-prompts/capability-task-prompt.txt`.
- `public/images/capability-memory-ai.webp`: 1536 × 1024, document and photo thumbnails arranged by time on a laptop. 176,294 bytes. Exact prompt: `image-prompts/capability-memory-prompt.txt`.
- These three capability images were generated together with the bundled CLI's `generate-batch`, concurrency 2, `gpt-image-2`, high quality, using the authorized current API. All three were visually checked and exported as WebP quality 90. Originals: `.artifacts/capability-{chat,task,memory}-ai.png`. Functional status strips remain HTML.
- `public/images/business-studio-ai.webp`: 1536 × 1024, an independent worker and two colleagues in a shared creative studio, 187,480 bytes. Exact prompt: `image-prompts/business-studio-prompt.txt`. Bundled CLI `generate`, `gpt-image-2`, high quality; original `.artifacts/business-studio-ai.png`, visually checked, WebP quality 90. Fictional people and setting, no customer or partnership claim.
- `public/images/market-reference-thumb.webp` and `market-poster-thumb.webp`: local crops of `work-desktop-ai.webp`, 200 × 161 and 160 × 200. Used in the memory timeline so the thumbnails match the market venue and poster discussed in the example. No additional API generation.
- `public/images/work-desktop-ai.webp`: 1672 × 823, fictional work desktop with a coffee-market moodboard, riverside reference photos, calendar and notes. Generated with `gpt-image-2`, high quality, requested 2048 × 1152; provider returned 1672 × 941. Cropped 27px from the top and 91px from the bottom to omit the generated OS menu and app dock. Original: `.artifacts/work-desktop-ai.png`. Exact final prompt: `image-prompts/work-desktop-prompt.txt`. Browser/reference content is illustrative; Mirally chat, meeting notes, extracted task list and downloads remain live HTML. WebP is approximately 257 KiB, quality 90.
- `public/images/hero-ai.webp`: 1536 × 1024, high quality generation, warm daylight home workspace. Original: `.artifacts/hero-ai.png`. Exact prompt: `image-prompts/hero-prompt.txt`.
- `public/images/evening-ai.webp`: 1536 × 1024, high quality image edit using the hero image as the visual reference, preserving the same person and home atmosphere. Original: `.artifacts/evening-ai.png`. Exact prompt: `image-prompts/evening-prompt.txt`.
- `public/images/vision-ai.webp`: high quality coastal dawn generation; requested 2048 × 1152 and the provider returned 1672 × 941. Original and exact prompt: `.artifacts/vision-ai.png`, `image-prompts/vision-prompt.txt`.
- `public/images/story-ai.webp`: 1536 × 1024, high quality fictional coastal-drama still for the interactive character-identification concept. Fictional photographer Xu Zhou appears clearly on the left in a khaki jacket with a film camera; the sea on the right accommodates HTML conversation overlays. Original and exact prompt: `.artifacts/story-ai.png`, `image-prompts/story-prompt.txt`. No real performer or existing drama is represented.
- Prompts contain no product credentials. Private API configuration is neither copied into the showcase nor changed. No real actors or shows are depicted in the evening image.
- Each delivered image is visually inspected before integration; WebP copies use quality 90. The original generated PNG files are retained for future crops and exports.
