#!/usr/bin/env python3
"""Draw `demo/testcard.jpg` from explicit, reviewable text constants.

The card is the pixel payload every camera snapshot in the demo carries, so it
is generated here instead of kept as an opaque binary: a grep cannot inspect a
JPEG, but a reviewer can read this file and see every string the demo video
would broadcast. Nothing on the card names a customer, vendor, or project.

The drawing has no randomness, no clock, no system font lookup and no
environment-dependent path, so repeated runs write byte-identical output.
Verify that with:

    python3 demo/make_testcard.py
    shasum -a 256 demo/testcard.jpg   # run again, compare

Anyone editing a string, a colour, or the geometry below should regenerate the
asset and update the byte count quoted in
`skills/ros2-camera-and-images/SKILL.md`.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# --- the whole vocabulary of the card -------------------------------------
# Framework, resolution and rate only. Resolution and rate are the ones the
# bundled camera node actually publishes, so the picture cannot contradict the
# stream it is sent on.
HEADER = "CAMERA TEST CARD"
CENTER = "NOUS"
FOOTER = "HERMES AGENT | ROS 2 | 400x300 | 2 Hz"

# --- frame geometry, shared with demo/nodes.py ----------------------------
WIDTH, HEIGHT = 400, 300  # a raw rgb8 frame at this size is 360,000 B
QUALITY = 85  # the q85 the camera skill's bandwidth table quotes

# --- palette --------------------------------------------------------------
BACKGROUND = (5, 10, 16)
GRID = (12, 30, 40)
INK = (0, 229, 255)
ACCENT = (255, 107, 107)

OUTPUT = Path(__file__).with_name("testcard.jpg")

CENTER_XY = (WIDTH // 2, HEIGHT // 2)
OUTER_R = 110
INNER_R = 70
FRAME_INSET = 3  # accent border, drawn outside the wordmark so nothing crosses the letters
CROSS_REACH = 160  # crosshair arm length, each way from the centre
TEXT_SCALE = 2  # built-in bitmap font, nearest-neighbour upscaled
MARK_SCALE = 9  # the CENTER wordmark, large enough to be read in a 400px frame


def _grid(draw: ImageDraw.ImageDraw) -> None:
    for x in range(0, WIDTH, 20):
        draw.line(((x, 0), (x, HEIGHT - 1)), fill=GRID, width=1)
    for y in range(0, HEIGHT, 20):
        draw.line(((0, y), (WIDTH - 1, y)), fill=GRID, width=1)


def _text(image: Image.Image, xy: tuple[int, int], text: str, fill=INK, scale=TEXT_SCALE) -> None:
    """Paste text drawn with Pillow's built-in bitmap font, scaled up crisply.

    A system TTF would make the output depend on whatever fonts the host has;
    the built-in bitmap face is part of Pillow, so the asset regenerates the
    same bytes on any machine. The mask keeps the grid visible behind the
    glyph gaps.
    """
    base = ImageFont.load_default()
    left, top, right, bottom = (int(v) for v in base.getbbox(text))
    patch = Image.new("RGB", (max(1, right - left), max(1, bottom - top)), BACKGROUND)
    ImageDraw.Draw(patch).text((-left, -top), text, font=base, fill=fill)
    patch = patch.resize(
        (patch.width * scale, patch.height * scale), Image.Resampling.NEAREST
    )
    # Only the glyph pixels land on the card, so the grid shows through the gaps.
    mask = patch.convert("L").point([0 if value <= 40 else 255 for value in range(256)])
    image.paste(patch, xy, mask)


def _text_width(text: str, scale: int = TEXT_SCALE) -> int:
    """Pixel width of `text` once scaled, for centring captions."""
    left, _, right, _ = (int(v) for v in ImageFont.load_default().getbbox(text))
    return (right - left) * scale


def build() -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    _grid(draw)

    cx, cy = CENTER_XY

    # Crosshair: horizontal and vertical arms through the exact centre.
    draw.line(((cx - CROSS_REACH, cy), (cx + CROSS_REACH, cy)), fill=INK, width=1)
    draw.line(((cx, cy - CROSS_REACH), (cx, cy + CROSS_REACH)), fill=INK, width=1)

    # Two concentric circles, the classic optical-alignment rings.
    for radius in (OUTER_R, INNER_R):
        box = (cx - radius, cy - radius, cx + radius, cy + radius)
        draw.ellipse(box, outline=INK, width=1)

    # Accent frame: the image border, so the card reads as a captured frame.
    draw.rectangle(
        (FRAME_INSET, FRAME_INSET, WIDTH - 1 - FRAME_INSET, HEIGHT - 1 - FRAME_INSET),
        outline=ACCENT,
        width=2,
    )

    # Corner framing ticks, no text.
    for ox, oy, sx, sy in (
        (8, 8, 1, 1),
        (WIDTH - 9, 8, -1, 1),
        (8, HEIGHT - 9, 1, -1),
        (WIDTH - 9, HEIGHT - 9, -1, -1),
    ):
        draw.line(((ox, oy), (ox + 16 * sx, oy)), fill=INK, width=2)
        draw.line(((ox, oy), (ox, oy + 10 * sy)), fill=INK, width=2)

    # Captions: neutral header, centre label, and the footer that names
    # framework, resolution and rate.
    _text(image, (14, 12), HEADER)
    # A plate behind the wordmark: the reticle is a HUD, and a HUD drawn through
    # a logo reads as noise. The band is part of the card, not an overlay.
    mark_w = _text_width(CENTER, MARK_SCALE)
    _, mark_top, _, mark_bottom = (
        int(v) * MARK_SCALE for v in ImageFont.load_default().getbbox(CENTER)
    )
    mark_h = mark_bottom - mark_top
    pad = 10
    draw.rectangle(
        (
            cx - mark_w // 2 - pad,
            cy - mark_h // 2 - pad,
            cx + mark_w // 2 + pad,
            cy + mark_h // 2 + pad,
        ),
        fill=BACKGROUND,
    )
    _text(image, (cx - mark_w // 2, cy - mark_h // 2), CENTER, scale=MARK_SCALE)
    _text(image, (cx - _text_width(FOOTER) // 2, HEIGHT - 26), FOOTER)

    return image


def main() -> None:
    image = build()
    image.save(OUTPUT, "JPEG", quality=QUALITY, optimize=False, progressive=False)
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size} B, {WIDTH}x{HEIGHT}, q{QUALITY})")


if __name__ == "__main__":
    main()
