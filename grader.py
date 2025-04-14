#!/usr/bin/env python3

import sys
import math
import argparse
from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import RecordingPointPen
from fontTools.pens.ttGlyphPen import TTGlyphPointPen


def parse_args():
    parser = argparse.ArgumentParser(
        description="Embolden all simple glyphs using an optimized port of hb_outline_t::embolden."
    )
    parser.add_argument("input_font", help="Path to input TTF/OTF font")
    parser.add_argument("output_font", help="Path to output emboldened font")
    parser.add_argument("--x-strength", type=float, default=0.0,
                        help="Horizontal emboldening strength (font units)")
    parser.add_argument("--y-strength", type=float, default=0.0,
                        help="Vertical emboldening strength (font units)")
    parser.add_argument("--x-shift", type=float, default=0.0,
                        help="Horizontal shift (font units) to apply each time points are moved")
    parser.add_argument("--y-shift", type=float, default=0.0,
                        help="Vertical shift (font units) to apply each time points are moved")

    return parser.parse_args()


def control_area(points):
    """Returns area/2 using tuples"""
    area = 0.0
    num_points = len(points)
    if num_points < 2:
        return 0.0
    for i in range(num_points):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % num_points]
        area += (x1 * y2 - x2 * y1)
    return area / 2.0


def normalize_len(x, y):
    """Returns (length, normalized_x, normalized_y)"""
    length = math.hypot(x, y)
    if length != 0.0:
        return length, x/length, y/length
    return 0.0, 0.0, 0.0


def hb_outline_embolden(points, contours, x_strength, y_strength, x_shift, y_shift):
    """
    Optimized embolden function using tuples instead of dictionaries.
    Modifies 'points' list in place.
    """
    if (x_strength == 0 and y_strength == 0) or not points:
        return

    x_strength = x_strength / 2.0
    y_strength = y_strength / 2.0

    orientation_negative = (control_area(points) < 0)

    first = 0
    for last in (c - 1 for c in contours):
        if last < first:
            first = last + 1
            continue

        in_vec = (0.0, 0.0)
        out_vec = (0.0, 0.0)
        anchor = (0.0, 0.0)
        shift = (0.0, 0.0)

        l_in = 0.0
        l_out = 0.0
        l_anchor = 0.0

        i = last
        j = first
        k = -1

        while True:
            if j != k:
                out_x = points[j][0] - points[i][0]
                out_y = points[j][1] - points[i][1]
                l_out, out_x, out_y = normalize_len(out_x, out_y)
                out_vec = (out_x, out_y)

                if l_out == 0.0:
                    j = j + 1 if j < last else first
                    if j == i or i == k:
                        break
                    continue
            else:
                out_vec = anchor
                l_out = l_anchor

            if l_in != 0.0:
                if k < 0:
                    k = i
                    anchor = in_vec
                    l_anchor = l_in

                d = in_vec[0] * out_vec[0] + in_vec[1] * out_vec[1]

                if d > -15.0/16.0:
                    d += 1.0

                    shift_x = in_vec[1] + out_vec[1]
                    shift_y = in_vec[0] + out_vec[0]

                    if orientation_negative:
                        shift_x = -shift_x
                    else:
                        shift_y = -shift_y

                    q = out_vec[0] * in_vec[1] - out_vec[1] * in_vec[0]
                    if orientation_negative:
                        q = -q

                    l = min(l_in, l_out)

                    if x_strength * q <= l * d:
                        shift_x = shift_x * x_strength / d
                    else:
                        shift_x = shift_x * l / q

                    if y_strength * q <= l * d:
                        shift_y = shift_y * y_strength / d
                    else:
                        shift_y = shift_y * l / q

                    shift = (shift_x, shift_y)
                else:
                    shift = (0.0, 0.0)

                while i != j:
                    points[i] = (
                        points[i][0] + x_shift + shift[0],
                        points[i][1] + y_shift + shift[1]
                    )
                    i = i + 1 if i < last else first
            else:
                i = j

            in_vec = out_vec
            l_in = l_out

            j = j + 1 if j < last else first
            if j == i or i == k:
                break

        first = last + 1


def flatten_glyph_to_points(glyphOps):
    """Convert glyph operations to points and contours using tuples"""
    points = []
    contours = []
    start = 0

    for item in glyphOps:
        if len(item) == 3:
            operator, args, kwargs = item
        else:
            operator, args = item
            kwargs = {}

        if operator == "addPoint":
            ((x, y), segType, smooth, name, *rest) = args
            points.append((x, y))
        elif operator == "endPath":
            if len(points) > start:
                contours.append(len(points))
            start = len(points)

    return points, contours


def rebuild_glyph_ops(points, contours, originalOps):
    """Rebuild glyph operations with modified points"""
    outVal = []
    idx = 0

    for item in originalOps:
        if len(item) == 3:
            operator, args, kwargs = item
        else:
            operator, args = item
            kwargs = {}

        if operator == "beginPath":
            outVal.append(("beginPath", (None,), {}))
        elif operator == "addPoint":
            ((xOld, yOld), segType, smooth, name, *rest) = args
            ident = rest[0] if rest else None
            newX, newY = points[idx]
            idx += 1
            newArgs = ((newX, newY), segType, smooth, name, ident)
            outVal.append((operator, newArgs, {}))
        elif operator == "endPath":
            outVal.append((operator, (), {}))
        else:
            outVal.append((operator, args, kwargs))

    return outVal


def main():
    args = parse_args()

    font = TTFont(args.input_font)
    glyf = font["glyf"]
    glyphSet = font.getGlyphSet()

    for gName in font.getGlyphOrder():
        glyph = glyf[gName]
        if glyph.isComposite():
            continue

        # Record existing outline
        recpen = RecordingPointPen()
        glyphSet[gName].drawPoints(recpen)
        ops = recpen.value

        # Flatten to points and contours
        points, contours = flatten_glyph_to_points(ops)
        if not points:
            continue

        # Embolden
        hb_outline_embolden(
            points, contours,
            x_strength=args.x_strength,
            y_strength=args.y_strength,
            x_shift=args.x_shift,
            y_shift=args.y_shift
        )

        # Rebuild glyph operations
        newOps = rebuild_glyph_ops(points, contours, ops)
        recpen.value = newOps

        # Replay into TTGlyph
        ttpen = TTGlyphPointPen(glyphSet)
        recpen.replay(ttpen)
        glyf[gName] = ttpen.glyph()

    font.save(args.output_font)
    print(f"Saved emboldened font to {args.output_font}")


if __name__ == "__main__":
    main()
