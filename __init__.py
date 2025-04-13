import sys
import argparse
import math
from fontTools.ttLib import TTFont
from fontTools.pens.ttGlyphPen import TTGlyphPointPen
from fontTools.pens.recordingPen import RecordingPointPen

def parse_args():
    parser = argparse.ArgumentParser(description="Embolden all simple glyphs in a font.")
    parser.add_argument("input_font", help="Path to input TTF/OTF font")
    parser.add_argument("output_font", help="Path to output emboldened font")
    parser.add_argument("strength", type=float, help="Emboldening strength (applies to both X and Y)")
    return parser.parse_args()

def normalize_len(dx, dy):
    length = math.hypot(dx, dy)
    if length == 0:
        return (0, 0, 0)
    return (dx / length, dy / length, length)

def control_area(contour):
    area = 0
    n = len(contour)
    for i in range(n):
        x1, y1 = contour[i][0]
        x2, y2 = contour[(i + 1) % n][0]
        area += (x1 * y2 - x2 * y1)
    return area / 2.0

def embolden_contours(contours, strength):
    x_strength = y_strength = strength / 2.0

    for contour in contours:
        if len(contour) < 2:
            continue
        orientation_negative = control_area(contour) < 0
        n = len(contour)
        i = n - 1
        j = 0
        k = -1
        in_vec = (0, 0)
        l_in = 0
        anchor = (0, 0)
        l_anchor = 0

        while True:
            if j != k:
                dx = contour[j][0][0] - contour[i][0][0]
                dy = contour[j][0][1] - contour[i][0][1]
                norm = normalize_len(dx, dy)
                out_vec = (norm[0], norm[1])
                l_out = norm[2]
                if l_out == 0:
                    j = (j + 1) % n
                    continue
            else:
                out_vec = anchor
                l_out = l_anchor

            if l_in != 0:
                if k < 0:
                    k = i
                    anchor = in_vec
                    l_anchor = l_in

                d = in_vec[0] * out_vec[0] + in_vec[1] * out_vec[1]
                if d > -15.0 / 16.0:
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
                        shift_x *= x_strength / d
                    else:
                        shift_x *= l / q if q != 0 else 0

                    if y_strength * q <= l * d:
                        shift_y *= y_strength / d
                    else:
                        shift_y *= l / q if q != 0 else 0
                else:
                    shift_x = shift_y = 0

                while i != j:
                    x, y = contour[i][0]
                    contour[i][0] = (x + shift_x, y + shift_y)
                    i = (i + 1) % n
            else:
                i = j

            in_vec = out_vec
            l_in = l_out
            j = (j + 1) % n
            if j == i or i == k:
                break

def extract_contours(recording_value):
    contours = []
    current = []
    for op in recording_value:
        if op[0] == "beginPath":
            current = []
        elif op[0] == "addPoint":
            pt, segmentType, smooth, name, *rest = op[1]
            identifier = rest[0] if rest else None
            current.append([(pt[0], pt[1]), segmentType, smooth, name, identifier])
        elif op[0] == "endPath":
            if current:
                contours.append(current)
            current = []
    return contours

def rebuild_pen_value(contours):
    value = []
    for contour in contours:
        value.append(("beginPath", (None,), {}))
        for pt in contour:
            value.append(("addPoint", ((pt[0][0], pt[0][1]), pt[1], pt[2], pt[3], pt[4]), {}))
        value.append(("endPath", (), {}))
    return value

def main():
    args = parse_args()
    font = TTFont(args.input_font)
    glyph_set = font.getGlyphSet()
    glyf_table = font["glyf"]

    for glyph_name in font.getGlyphOrder():
        glyph = glyf_table[glyph_name]
        if glyph.isComposite():
            continue

        recpen = RecordingPointPen()
        glyph_set[glyph_name].drawPoints(recpen)

        contours = extract_contours(recpen.value)
        embolden_contours(contours, args.strength)
        recpen.value = rebuild_pen_value(contours)

        ttpen = TTGlyphPointPen(glyph_set)
        recpen.replay(ttpen)
        glyf_table[glyph_name] = ttpen.glyph()

    font.save(args.output_font)
    print(f"Saved emboldened font to {args.output_font}")

if __name__ == "__main__":
    main()

