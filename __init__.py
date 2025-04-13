#!/usr/bin/env python3

import sys
import math
import argparse
from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import RecordingPointPen
from fontTools.pens.ttGlyphPen import TTGlyphPointPen

def parse_args():
    parser = argparse.ArgumentParser(
        description="Embolden all simple glyphs using the EXACT port of hb_outline_t::embolden."
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
    """
    Equivalent of control_area() in your C++ code: returns area/2 across ALL points.
    points: list of dicts [ {'x':..., 'y':...}, ... ]
    """
    area = 0.0
    num_points = len(points)
    if num_points < 2:
        return 0.0
    for i in range(num_points):
        x1 = points[i]['x']
        y1 = points[i]['y']
        x2 = points[(i + 1) % num_points]['x']
        y2 = points[(i + 1) % num_points]['y']
        area += (x1 * y2 - x2 * y1)
    return area / 2.0

def normalize_len(vec):
    """
    Replicates out.normalize_len() from C++.
    vec is dict: {'x':..., 'y':...}
    Returns the original length. Modifies vec in place to make it unit-length (if non-zero).
    """
    length = math.hypot(vec['x'], vec['y'])
    if length != 0.0:
        vec['x'] /= length
        vec['y'] /= length
    return length

def hb_outline_embolden(points, contours, x_strength, y_strength, x_shift, y_shift):
    """
    Direct Python port of:

      void hb_outline_t::embolden (float x_strength, float y_strength,
                                   float x_shift, float y_shift)

    from your C++ snippet. Variable names remain identical:
    - 'in', 'out', 'anchor', 'shift' become Python dicts { 'x':..., 'y':... }
    - l_in, l_out, l_anchor, etc. remain floats
    - loops replicate i, j, k iteration
    - orientation checks a single time for the entire glyph

    points:   list of dicts [ {'x': float, 'y': float}, ... ] for ALL contours
    contours: list of endpoint indices (like 'contours[c]' in C++),
              where contours[i] = lastPointIndexInContour+1
    modifies 'points' in place
    """

    if (x_strength == 0 and y_strength == 0) or not points:
        return

    x_strength /= 2.0
    y_strength /= 2.0

    orientation_negative = (control_area(points) < 0)

    first = 0
    for c in range(len(contours)):
        last = contours[c] - 1
        if last < first:
            first = contours[c]
            continue

        in_ =  {'x': 0.0, 'y': 0.0 }
        out_ = {'x': 0.0, 'y': 0.0 }
        anchor = {'x': 0.0, 'y': 0.0 }
        shift_ = {'x': 0.0, 'y': 0.0 }

        l_in = 0.0
        l_out = 0.0
        l_anchor = 0.0

        i = last
        j = first
        k = -1

        while True:
            if j != k:
                out_['x'] = points[j]['x'] - points[i]['x']
                out_['y'] = points[j]['y'] - points[i]['y']
                l_out = normalize_len(out_)
                if l_out == 0.0:
                    # skip
                    if j < last:
                        j += 1
                    else:
                        j = first
                    if j == i or i == k:
                        break
                    continue
            else:
                out_ = {'x': anchor['x'], 'y': anchor['y']}
                l_out = l_anchor

            if l_in != 0.0:
                if k < 0:
                    k = i
                    anchor = {'x': in_['x'], 'y': in_['y']}
                    l_anchor = l_in

                d = in_['x'] * out_['x'] + in_['y'] * out_['y']

                if d > -15.0/16.0:
                    d += 1.0

                    shift_['x'] = in_['y'] + out_['y']
                    shift_['y'] = in_['x'] + out_['x']

                    if orientation_negative:
                        shift_['x'] = -shift_['x']
                    else:
                        shift_['y'] = -shift_['y']

                    q = out_['x'] * in_['y'] - out_['y'] * in_['x']
                    if orientation_negative:
                        q = -q

                    l = min(l_in, l_out)

                    # non-strict inequalities avoid divide-by-zero when q == l == 0
                    if x_strength * q <= l * d:
                        shift_['x'] = shift_['x'] * x_strength / d
                    else:
                        shift_['x'] = shift_['x'] * l / q

                    if y_strength * q <= l * d:
                        shift_['y'] = shift_['y'] * y_strength / d
                    else:
                        shift_['y'] = shift_['y'] * l / q
                else:
                    shift_['x'] = 0.0
                    shift_['y'] = 0.0

                # move i forward
                while i != j:
                    points[i]['x'] += x_shift + shift_['x']
                    points[i]['y'] += y_shift + shift_['y']
                    if i < last:
                        i += 1
                    else:
                        i = first
            else:
                i = j

            in_ = {'x': out_['x'], 'y': out_['y']}
            l_in = l_out

            if j < last:
                j += 1
            else:
                j = first
            if j == i or i == k:
                break

        first = last + 1

def flatten_glyph_to_points(glyphOps):
    """
    Flatten a glyph's recording-pen ops (all contours) into:
      - points: list of {'x':..., 'y':...}
      - contours: list of end indices
    """
    points = []
    contours = []
    start = 0

    for item in glyphOps:
        if len(item) == 3:
            operator, args, kwargs = item
        else:
            operator, args = item
            kwargs = {}

        if operator == "beginPath":
            pass
        elif operator == "addPoint":
            ((x, y), segType, smooth, name, *rest) = args
            points.append({'x': x, 'y': y})
        elif operator == "endPath":
            if len(points) > start:
                contours.append(len(points))
            start = len(points)
        # ignore addComponent
    return points, contours

def rebuild_glyph_ops(points, contours, originalOps):
    """
    Rebuild a new .value for the RecordingPointPen using updated points,
    while preserving segmentType, smooth, name, etc. in the same order.
    """
    outVal = []
    idx = 0  # point index across entire glyph
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

            newX = points[idx]['x']
            newY = points[idx]['y']
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

        # 1) Record existing outline
        recpen = RecordingPointPen()
        glyphSet[gName].drawPoints(recpen)
        ops = recpen.value

        # 2) Flatten
        points, contours = flatten_glyph_to_points(ops)
        if not points:
            continue

        # 3) Embolden EXACT C++ logic
        hb_outline_embolden(points, contours,
                            x_strength=args.x_strength,
                            y_strength=args.y_strength,
                            x_shift=args.x_shift,
                            y_shift=args.y_shift)

        # 4) Rebuild pen ops
        newOps = rebuild_glyph_ops(points, contours, ops)
        recpen.value = newOps

        # 5) Replay into TTGlyph
        ttpen = TTGlyphPointPen(glyphSet)
        recpen.replay(ttpen)
        glyf[gName] = ttpen.glyph()

    font.save(args.output_font)
    print(f"Saved emboldened font to {args.output_font}")

if __name__ == "__main__":
    main()
