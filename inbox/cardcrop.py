"""
Find a QSL card in a phone photo and flatten it.

Phone shots have a tabletop around the card and are rarely square-on, so
this looks for the card's outline, then perspective-warps the four corners
into a flat rectangle. Scanner images skip this entirely (they are already
cropped and straight).

    find_and_flatten(pil_img) -> (pil_img, info)

info["method"] is "quad" (four clean corners found), "rect" (outline found
but not a clean quad, straightened with a rotated bounding box), or "none"
(nothing card-shaped found, original returned untouched). Anything but
"quad" is surfaced in the review PR so a bad crop gets a human look.
"""

import cv2
import numpy as np
from PIL import Image

WORK_EDGE = 1000          # detection runs on a downscaled copy for speed
MIN_AREA_FRAC = 0.15      # card must fill at least this much of the photo
MAX_AREA_FRAC = 0.98      # anything bigger is the photo frame itself
MIN_ASPECT, MAX_ASPECT = 1.0, 2.4    # most QSLs are ~1.55 (3.5x5.5in), some square


def _order_corners(pts):
    """Top-left, top-right, bottom-right, bottom-left."""
    pts = np.asarray(pts, dtype="float32").reshape(4, 2)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)],
                     pts[np.argmax(s)], pts[np.argmax(d)]], dtype="float32")


def _warp(rgb, corners):
    tl, tr, br, bl = corners
    w = int(round(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl))))
    h = int(round(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl))))
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype="float32")
    m = cv2.getPerspectiveTransform(corners, dst)
    return cv2.warpPerspective(rgb, m, (w, h), flags=cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_REPLICATE)


def _aspect_ok(w, h):
    if min(w, h) <= 0:
        return False
    a = max(w, h) / min(w, h)
    return MIN_ASPECT <= a <= MAX_ASPECT


def _background_distance_mask(rgb):
    """Pixels that differ from the photo's border color. The border of a
    phone shot is almost always tabletop, so this survives wood grain and
    patterned cloth that confuse plain edge detection."""
    lab = cv2.cvtColor(cv2.GaussianBlur(rgb, (9, 9), 0), cv2.COLOR_RGB2LAB).astype("float32")
    h, w = lab.shape[:2]
    b = max(4, int(min(h, w) * 0.03))
    border = np.concatenate([lab[:b].reshape(-1, 3), lab[-b:].reshape(-1, 3),
                             lab[:, :b].reshape(-1, 3), lab[:, -b:].reshape(-1, 3)])
    bg = np.median(border, axis=0)
    spread = np.percentile(np.linalg.norm(border - bg, axis=1), 90)
    dist = np.linalg.norm(lab - bg, axis=2)
    thresh = max(spread * 1.5, 18.0)
    mask = (dist > thresh).astype("uint8") * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=4)


def _fill_holes(mask):
    """A card's printed artwork leaves holes in the mask; only the outline matters."""
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(mask)
    cv2.drawContours(filled, contours, -1, 255, thickness=cv2.FILLED)
    return filled


def _candidate_masks(rgb, gray):
    """Several ways of separating card from background; different photos
    (white card on dark table, dark card on light desk, busy wood grain)
    favor different ones."""
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))

    yield _fill_holes(_background_distance_mask(rgb))

    edges = cv2.Canny(blur, 40, 140)
    yield cv2.dilate(edges, kernel, iterations=2)

    _, otsu = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    for m in (otsu, 255 - otsu):
        yield _fill_holes(cv2.morphologyEx(m, cv2.MORPH_CLOSE, kernel, iterations=3))


def find_and_flatten(img):
    rgb = np.array(img.convert("RGB"))
    H, W = rgb.shape[:2]
    scale = WORK_EDGE / max(H, W) if max(H, W) > WORK_EDGE else 1.0
    small = cv2.resize(rgb, (int(W * scale), int(H * scale)), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    frame_area = small.shape[0] * small.shape[1]

    best_quad, best_rect = None, None   # (area, points)
    for mask in _candidate_masks(small, gray):
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
            area = cv2.contourArea(c)
            if not (MIN_AREA_FRAC * frame_area <= area <= MAX_AREA_FRAC * frame_area):
                continue
            peri = cv2.arcLength(c, True)
            quad = None
            # rounded corners and shadows need a looser fit to collapse to 4 points
            for eps in (0.02, 0.03, 0.045):
                approx = cv2.approxPolyDP(c, eps * peri, True)
                if len(approx) == 4 and cv2.isContourConvex(approx):
                    corners = _order_corners(approx)
                    tl, tr, br, bl = corners
                    if _aspect_ok(np.linalg.norm(tr - tl), np.linalg.norm(bl - tl)) \
                            and cv2.contourArea(approx) >= 0.93 * area:
                        quad = corners
                    break
            if quad is not None:
                if best_quad is None or area > best_quad[0]:
                    best_quad = (area, quad)
                continue
            (cx, cy), (rw, rh), ang = cv2.minAreaRect(c)
            # a rotated box much larger than the contour means it isn't a card
            if _aspect_ok(rw, rh) and area >= 0.85 * rw * rh:
                if best_rect is None or area > best_rect[0]:
                    best_rect = (area, _order_corners(cv2.boxPoints(((cx, cy), (rw, rh), ang))))

    # A clean quad wins unless a straightened outline is clearly bigger
    # (the quad then is likely an inner frame printed on the card).
    if best_quad is not None and (best_rect is None or best_quad[0] >= 0.9 * best_rect[0]):
        method, (area, corners) = "quad", best_quad
    elif best_rect is not None:
        method, (area, corners) = "rect", best_rect
    else:
        return img, {"method": "none", "area_frac": None}

    corners = corners / scale
    out = _warp(rgb, corners)
    return Image.fromarray(out), {"method": method,
                                  "area_frac": round(area / frame_area, 3),
                                  "corners": [[round(float(x)), round(float(y))] for x, y in corners]}


def rotate_clockwise(img, degrees):
    """Rotate a PIL image clockwise by 0/90/180/270."""
    degrees = int(degrees or 0) % 360
    if degrees == 0:
        return img
    return img.rotate(-degrees, expand=True)
