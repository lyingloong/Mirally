"""Rounded ttk buttons with distinct selection and keyboard-focus states."""

from __future__ import annotations

import math
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

from openassist.presentation import blend, choose_ui_font


class ButtonStyles:
    def __init__(self, root, scale):
        self.root = root
        self.body_font = choose_ui_font(root)
        self.scale = scale
        self.images = []
        self._image_cache = {}
        self.created = set()
        self.style = ttk.Style(root)
        self.style.theme_use("clam")

    def _image(self, fill, border, background, *, focus=None):
        cache_key = (fill, border, background, focus)
        if cache_key in self._image_cache:
            return self._image_cache[cache_key]
        # Render the same outline through the straight edges and corners. The
        # old one-pixel straight edges became hairlines on high-DPI displays.
        # The reserved outer gutter keeps focus from changing button geometry.
        supersample = 4
        factor = self.scale * supersample
        size = math.ceil(28 * self.scale)
        image = Image.new("RGB", (size * supersample, size * supersample), background)
        draw = ImageDraw.Draw(image)

        def rounded(inset, radius, color, *, outline=None, width=0):
            start = round(inset * factor)
            end = size * supersample - start - 1
            draw.rounded_rectangle((start, start, end, end), radius=round(radius * factor),
                                   fill=color, outline=outline, width=round(width * factor))

        if focus:
            rounded(0.5, 11, focus)
            rounded(2.25, 9.25, background)
        rounded(3.5, 8, fill, outline=border, width=1.25)
        image = image.resize((size, size), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(image, master=self.root)
        self.images.append(photo)
        result = (photo, math.ceil(12 * self.scale))
        self._image_cache[cache_key] = result
        return result

    def apply(self, key, theme):
        primary_foreground = theme.background if key == "graphite" else "#FFFFFF"
        # A nine-slice image also paints the corner and focus-ring gutters.
        # Match their color to the button's parent instead of leaving a square
        # background-colored patch on white cards (or dark raised surfaces).
        for context, background in (("", theme.background), ("surface.", theme.surface)):
            for role in ("primary", "outline", "quiet"):
                self._configure_role(f"{key}.{context}{role}.TButton", role, theme,
                                     background, primary_foreground)

    def _configure_role(self, name, role, t, background, primary_foreground):
        foreground = primary_foreground if role == "primary" else t.text
        fill = t.accent if role == "primary" else (t.surface if role == "outline" else background)
        border = t.border if role == "outline" else fill
        if name not in self.created:
            normal, inset = self._image(fill, border, background)
            focus = blend(t.accent, background, 0.18)
            disabled_fill = blend(t.accent, background, 0.86) if role == "primary" else fill
            disabled_border = blend(border, background, 0.4) if role == "outline" else disabled_fill
            disabled, _ = self._image(disabled_fill, disabled_border, background)
            states = [("disabled", disabled)]
            # Specific combinations precede their less specific states, so
            # hover/press can never erase a selected tab or its focus ring.
            for selected in (True, False):
                selected_fill = (blend(background, t.accent, 0.12)
                                 if selected and role != "primary" else fill)
                selected_border = (blend(background, t.accent, 0.26)
                                   if selected and role != "primary" else border)
                for interaction, intensity in (("pressed", 0.16), ("active", 0.07), ("", 0)):
                    state_fill = blend(selected_fill, t.text if role == "primary" else t.accent, intensity)
                    state_border = state_fill if role == "primary" or (role == "quiet" and not selected) else selected_border
                    for focused in (True, False):
                        state = tuple(value for value in ("selected" if selected else "", interaction,
                                                          "focus" if focused else "") if value)
                        if not state:
                            continue
                        image, _ = self._image(state_fill, state_border, background, focus=focus if focused else None)
                        states.append((*state, image))
            element = name + ".border"
            self.style.element_create(element, "image", normal, *states, border=inset, sticky="nsew")
            self.style.layout(name, [(element, {"sticky": "nsew", "children": [("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})]})])
            self.created.add(name)
        self.style.configure(name, font=(self.body_font, 10), foreground=foreground, background=background,
                             padding=(round(8 * self.scale), 0), anchor="center")
        self.style.map(name, foreground=[("disabled", blend(t.muted, background, 0.2)),
                                         ("selected", foreground if role == "primary" else t.accent),
                                         ("active", foreground if role == "primary" else t.accent)])
