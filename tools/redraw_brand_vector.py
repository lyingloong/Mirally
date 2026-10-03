"""Fit an editable SVG silhouette and gradient grid to the selected raster mark.

Design-time only: requires Pillow, NumPy and SciPy. No bitmap is embedded in SVG.
The low-frequency color fit is an approximation, not a watermark detection tool.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi
from scipy.interpolate import CubicSpline, RBFInterpolator, splprep, splev


def ribbon_boundary():
    """Measured crease landmarks, with smooth tangents through the embrace."""
    # The previous hand-placed Beziers missed the right crease by up to 20px.
    # Continue each split outside the mark rather than adding a second seam.
    upper = np.array([(600, 679), (630, 704), (650, 721), (670, 735),
                      (690, 745), (710, 752), (730, 755), (750, 754),
                      (770, 748), (790, 737), (810, 722), (830, 704),
                      (850, 687), (870, 673), (890, 662), (910, 653),
                      (930, 648), (970, 643), (1100, 635), (1254, 635)], float)
    lower = np.array([(0, 650), (250, 644), (310, 648), (330, 650),
                      (350, 655), (370, 663), (390, 676), (410, 690),
                      (430, 708), (450, 727), (470, 746), (490, 767)], float)
    commands = ['M0,0 L375,0 L600,679']
    boundary = [(0, 0), (375, 0), (600, 679)]

    def append_curve(points):
        t = np.r_[0, np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
        curve = CubicSpline(t, points, bc_type='natural')
        derivative = curve(t, 1)
        for i, dt in enumerate(np.diff(t)):
            controls = (points[i]+derivative[i]*dt/3,
                        points[i+1]-derivative[i+1]*dt/3, points[i+1])
            commands.append('C'+' '.join(f'{x:.4f},{y:.4f}' for x, y in controls))
        boundary.extend(map(tuple, curve(np.linspace(0, t[-1], int(t[-1])*2))))

    append_curve(upper)
    commands.append('L1254,1254 L490,1254 L490,767')
    boundary.extend([(1254, 1254), (490, 1254), (490, 767)])
    append_curve(lower[::-1])
    commands.append('Z')
    return ' '.join(commands), boundary


def outline(mask):
    """Trace clockwise pixel edges, then fit a smooth periodic cubic curve."""
    padded = np.pad(mask, 1)
    edges = {}
    for exposed, start, end in (
        (mask & ~padded[:-2, 1:-1], (0, 0), (1, 0)),
        (mask & ~padded[1:-1, 2:], (1, 0), (1, 1)),
        (mask & ~padded[2:, 1:-1], (1, 1), (0, 1)),
        (mask & ~padded[1:-1, :-2], (0, 1), (0, 0)),
    ):
        for y, x in zip(*np.where(exposed)):
            edges[(x+start[0], y+start[1])] = (x+end[0], y+end[1])
    start = min(edges)
    points, current = [start], edges[start]
    while current != start:
        points.append(current)
        current = edges[current]
    points.append(start)
    points = np.asarray(points, dtype=float)
    distance = np.r_[0, np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
    samples = np.linspace(0, distance[-1], round(distance[-1]/3)+1)
    xy = np.column_stack([np.interp(samples, distance, points[:, axis]) for axis in (0, 1)])
    # A tolerance fit removes the pixel staircase without rounding every corner
    # by a fixed radius. Interpolating dense points reproduced tiny scallops.
    weights = np.ones(len(samples))
    arm_turn = ((np.abs(xy[:, 0]-487) < 18) | (np.abs(xy[:, 0]-767) < 18))
    weights[arm_turn & (xy[:, 1] > 748) & (xy[:, 1] < 790)] = 5
    curve, _ = splprep(xy.T, u=samples/distance[-1], w=weights,
                      s=len(samples)*.7, per=True)
    knots = np.unique(np.clip(curve[0], 0, 1))
    xy = np.asarray(splev(knots, curve)).T
    derivative = np.asarray(splev(knots, curve, der=1)).T
    commands = [f"M{xy[0, 0]:.3f},{xy[0, 1]:.3f}"]
    for i, dt in enumerate(np.diff(knots)):
        a, b = xy[i]+derivative[i]*dt/3, xy[i+1]-derivative[i+1]*dt/3
        commands.append("C" + " ".join(f"{p[0]:.3f},{p[1]:.3f}" for p in (a, b, xy[i+1])))
    return " ".join(commands) + " Z"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--grid", type=int, default=96)
    args = parser.parse_args()
    rgba = np.asarray(Image.open(args.source).convert("RGBA"))
    h, w = rgba.shape[:2]
    if (w, h) != (1254, 1254) or not 8 <= args.grid <= 96:
        parser.error("This fit uses the selected 1254px reference and a grid between 8 and 96")
    labels, _ = ndi.label(rgba[:, :, 3] > 128)
    sizes = np.bincount(labels.ravel())
    components = np.argsort(sizes[1:])[-3:]+1
    body_id = max(components, key=lambda i: sizes[i])
    source_mask = np.isin(labels, components)
    body = ndi.binary_fill_holes(labels == body_id)
    signed = ndi.distance_transform_edt(body)-ndi.distance_transform_edt(~body)
    symmetric = ndi.gaussian_filter((signed+signed[:, ::-1])/2, 1.2) > 0
    body_path = outline(symmetric)
    heads = []
    for component in components:
        if component != body_id:
            y, x = np.where(labels == component)
            heads.append(((x.min()+x.max()+1)/2, (y.min()+y.max()+1)/2,
                          (x.max()-x.min()+1)/2, (y.max()-y.min()+1)/2))
    cy, rx, ry = np.mean(np.asarray(heads)[:, 1:], axis=0)
    offset = abs(heads[0][0]-heads[1][0])/2
    # Use the *same mirrored path*, not two independently fitted outlines.
    # The averaged distance field establishes the shape; this clipping halves
    # the fitted curve and mirrors it to make exact reflection deterministic.
    geometry = (f'<g clip-path="url(#left-half)"><path d="{body_path}"/></g>'
                f'<g transform="translate({w} 0) scale(-1 1)" clip-path="url(#left-half)">'
                f'<path d="{body_path}"/></g>')
    # Flatten nested clips for the final mask by using a luminance mask.
    ellipses = "".join(f'<ellipse cx="{cx:.3f}" cy="{cy:.3f}" rx="{rx:.3f}" ry="{ry:.3f}"/>'
                       for cx in (w/2-offset, w/2+offset))
    # The crossing ribbon has a real color discontinuity. Fit each surface
    # separately instead of blurring its edge across adjacent mesh cells.
    front_path, boundary = ribbon_boundary()
    front_image = Image.new('1', (w, h))
    ImageDraw.Draw(front_image).polygon(boundary, fill=1)
    front = np.asarray(front_image)
    grid = np.linspace(0, w, args.grid+1)
    definitions = [f'<clipPath id="left-half"><rect width="{w/2+1}" height="{h}"/></clipPath>',
        f'<mask id="mark" maskUnits="userSpaceOnUse" x="0" y="0" width="{w}" height="{h}">'
        f'<g fill="white">{geometry}{ellipses}</g></mask>',
        f'<clipPath id="front-surface"><path d="{front_path}"/></clipPath>',
        '<linearGradient id="vertical-mix" x2="0" y2="1"><stop stop-color="white" stop-opacity="0"/>'
        '<stop offset="1" stop-color="white"/></linearGradient>',
        '<mask id="mix" maskContentUnits="objectBoundingBox"><rect width="1" height="1" shape-rendering="crispEdges" fill="url(#vertical-mix)"/></mask>']
    patches = []

    def color(col, row):
        value = colors[row, col]
        return 'rgb('+','.join(f'{v/255*100:.5f}%' for v in value)+')'

    for layer, selection in (('back', source_mask & ~front), ('front', source_mask & front)):
      # Exclude antialiased edges from the color samples. A continuous local
      # thin-plate surface extrapolates across the clip boundary; nearest-pixel
      # extension produced ridges wherever the closest source pixel changed.
      interior = ndi.distance_transform_edt(selection) > 5
      ys, xs = np.mgrid[0:h:8, 0:w:8]
      keep = interior[ys, xs]
      samples = np.column_stack((xs[keep], ys[keep]))
      smoothed = ndi.gaussian_filter(rgba[:, :, :3].astype(float), (1.5, 1.5, 0))
      fit = RBFInterpolator(samples, smoothed[ys[keep], xs[keep]],
                            neighbors=64, smoothing=20, kernel='thin_plate_spline')
      gx, gy = np.meshgrid(grid, grid)
      colors = np.clip(fit(np.column_stack((gx.ravel(), gy.ravel()))), 0, 255)
      colors = colors.reshape(len(grid), len(grid), 3)
      patches.append('<g'+(' clip-path="url(#front-surface)"' if layer == 'front' else '')+'>')
      for row, (y1, y2) in enumerate(zip(grid, grid[1:])):
        for col, (x1, x2) in enumerate(zip(grid, grid[1:])):
            # Include a small guard around the fitted geometry for clean edges.
            if not source_mask[max(0, int(y1)-8):min(h, int(y2)+8), max(0, int(x1)-8):min(w, int(x2)+8)].any():
                continue
            if layer == 'front' and not front[max(0,int(y1)-8):min(h,int(y2)+8),max(0,int(x1)-8):min(w,int(x2)+8)].any():
                continue
            ident = f'{layer}{row}-{col}'
            for suffix, sample_row in (('t', row), ('b', row+1)):
                definitions.append(f'<linearGradient id="{ident}{suffix}"><stop stop-color="{color(col,sample_row)}"/>'
                                   f'<stop offset="1" stop-color="{color(col+1,sample_row)}"/></linearGradient>')
            rect = f'x="{x1:.4f}" y="{y1:.4f}" width="{x2-x1:.4f}" height="{y2-y1:.4f}" shape-rendering="crispEdges"'
            patches.append(f'<rect {rect} fill="url(#{ident}t)"/><rect {rect} fill="url(#{ident}b)" mask="url(#mix)"/>')
      patches.append('</g>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">\n'
           '<title>Mirally companion M — reference-fitted vector</title>\n'
           '<desc>Symmetric outline, matched heads and a fitted low-frequency gradient grid. No embedded bitmap.</desc>\n'
           '<defs>\n'+'\n'.join(definitions)+'\n</defs>\n<g mask="url(#mark)">\n'+'\n'.join(patches)+'\n</g>\n</svg>\n')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(svg, encoding="utf-8")
    print(f"Saved {args.out}; {len(patches)} gradient cells; mirrored body and equal heads")


if __name__ == "__main__":
    main()
