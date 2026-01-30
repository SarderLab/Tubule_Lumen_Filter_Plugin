import os, json, argparse
from pathlib import Path
from collections import Counter
import copy
import numpy as np
import cv2
from tiffslide import TiffSlide
from tqdm import tqdm


# ----------------------------
# Parsers
# ----------------------------
def looks_like_geojson(obj) -> bool:
    # FeatureCollection
    if isinstance(obj, dict) and isinstance(obj.get("features"), list):
        return True
    # list of Feature
    if isinstance(obj, list) and obj and isinstance(obj[0], dict) and obj[0].get("type") == "Feature":
        return True
    return False


def load_dsa_nuclei_geometries(path: str):
    """
    Load nuclei from a DSA/Athena annotation JSON (elements).
    Returns: (polys, points, gtypes)
      - polys: list[np.ndarray Nx2] in level-0 coords
      - points: list[(x,y)] in level-0 coords
      - gtypes: Counter with keys like 'Polygon', 'Point'
    """
    elems = load_dsa_elements(path)
    polys = []
    points = []
    gtypes = Counter()

    for e in elems:
        etype = (e.get("type") or "").lower()

        # ---- points (some DSA exports use "point" or circles) ----
        if etype in ("point", "circle"):
            # prefer explicit center if present
            if isinstance(e.get("center"), (list, tuple)) and len(e["center"]) >= 2:
                x, y = float(e["center"][0]), float(e["center"][1])
                points.append((x, y))
                gtypes["Point"] += 1
                continue

            # fallback: first point in points[]
            pts = elem_points_to_xy(e)
            if pts.shape[0] >= 1:
                points.append((float(pts[0, 0]), float(pts[0, 1])))
                gtypes["Point"] += 1
            continue

        # ---- polygons / polylines (closed shapes) ----
        pts = elem_points_to_xy(e)
        if pts.shape[0] >= 3:
            polys.append(pts)
            gtypes["Polygon"] += 1

    return polys, points, gtypes

def rasterize_points_in_roi(points_xy, x0, y0, roi_h, roi_w, radius_px: int):
    """
    points_xy: list[(x,y)] in global coords
    Draw into ROI-local mask.
    """
    m = np.zeros((roi_h, roi_w), dtype=np.uint8)
    if not points_xy:
        return m
    r = int(radius_px)
    for (x, y) in points_xy:
        cx = int(round(x)) - x0
        cy = int(round(y)) - y0
        if 0 <= cx < roi_w and 0 <= cy < roi_h:
            cv2.circle(m, (cx, cy), r, 1, thickness=-1)
    return m


def load_nuclei_geometries_auto(path: str):
    """
    Auto-detect nuclei file format:
      - GeoJSON (FeatureCollection or list of Features)
      - DSA/Athena annotation JSON with elements
    """
    with open(path, "r") as f:
        obj = json.load(f)

    if looks_like_geojson(obj):
        # reuse your existing geojson path
        return load_geojson_geometries(path)

    # otherwise treat as DSA nuclei.json
    return load_dsa_nuclei_geometries(path)



def bbox_from_poly_xy(poly_xy: np.ndarray):
    xs = poly_xy[:, 0]
    ys = poly_xy[:, 1]
    return float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())

def expand_and_clip_bbox(x0, y0, x1, y1, pad, W, H):
    x0 = max(0, int(np.floor(x0)) - pad)
    y0 = max(0, int(np.floor(y0)) - pad)
    x1 = min(W - 1, int(np.ceil(x1)) + pad)
    y1 = min(H - 1, int(np.ceil(y1)) + pad)
    return x0, y0, x1, y1

def fill_poly_local(mask_u8: np.ndarray, poly_xy: np.ndarray, x0: int, y0: int, val: int = 1):
    """
    Fill a polygon into a local ROI mask. poly_xy is in global coords.
    """
    p = np.round(poly_xy).astype(np.int32).copy()
    p[:, 0] -= x0
    p[:, 1] -= y0
    # clip to ROI
    h, w = mask_u8.shape
    p[:, 0] = np.clip(p[:, 0], 0, w - 1)
    p[:, 1] = np.clip(p[:, 1], 0, h - 1)
    cv2.fillPoly(mask_u8, [p], int(val))

def mask_to_polylines_with_offset(mask_bool, min_area_px: int, x0: int, y0: int):
    """
    Same as your mask_to_polylines, but adds (x0,y0) offset back to global coords.
    Assumes mask pixels are already in level-0 coords.
    """
    m = (mask_bool.astype(np.uint8) * 255)
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)

    polylines = []
    for c in contours:
        if c.shape[0] < 3:
            continue
        if cv2.contourArea(c) < min_area_px:
            continue
        pts_local = c[:, 0, :].astype(np.float32)
        pts_global = pts_local + np.array([x0, y0], dtype=np.float32)
        pts = [[float(x), float(y), 0.0] for x, y in pts_global]
        polylines.append(pts)
    return polylines

def filter_small_components_u8(mask_u8_01: np.ndarray, min_area_px: int) -> np.ndarray:
    """
    mask_u8_01 is uint8 {0,1}. Returns bool keep mask.
    """
    if min_area_px <= 1:
        return mask_u8_01.astype(bool)
    m = (mask_u8_01 * 255).astype(np.uint8)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    keep = np.zeros_like(mask_u8_01, dtype=bool)
    for lab in range(1, num):
        area = stats[lab, cv2.CC_STAT_AREA]
        if area >= min_area_px:
            keep |= (labels == lab)
    return keep

def load_dsa_elements(path: str):
    with open(path, "r") as f:
        data = json.load(f)

    if isinstance(data, list) and data:
        data = data[0]

    if isinstance(data, dict) and "elements" in data and isinstance(data["elements"], list):
        return data["elements"]

    if (
        isinstance(data, dict)
        and "annotation" in data
        and isinstance(data["annotation"], dict)
        and "elements" in data["annotation"]
        and isinstance(data["annotation"]["elements"], list)
    ):
        return data["annotation"]["elements"]

    raise ValueError(f"Could not find elements[] in: {path}")


def get_elem_label(elem: dict) -> str:
    """
    Your tubules_subcompartments.json uses: label = {'value': 'Eosinophilic'}.
    This function supports both string and dict labels.
    """
    for k in ("label", "group", "name", "category", "class"):
        v = elem.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            for kk in ("value", "name", "label", "title", "text"):
                vv = v.get(kk)
                if isinstance(vv, str) and vv.strip():
                    return vv.strip()
    return ""


def elem_points_to_xy(elem: dict):
    pts = elem.get("points", [])
    out = []
    for p in pts:
        if isinstance(p, list) and len(p) > 0 and isinstance(p[0], list):
            x, y = p[0][0], p[0][1]
        else:
            x, y = p[0], p[1]
        out.append([float(x), float(y)])
    return np.array(out, dtype=np.float32)


# ----------------------------
# GeoJSON loader (supports list-of-features + FeatureCollection)
# ----------------------------
def iter_geojson_features(gj):
    if isinstance(gj, dict) and isinstance(gj.get("features"), list):
        for f in gj["features"]:
            yield f
    elif isinstance(gj, list):
        for f in gj:
            yield f
    else:
        return


def load_geojson_geometries(path: str):
    with open(path, "r") as f:
        gj = json.load(f)

    polys = []
    points = []
    gtypes = Counter()

    for feat in iter_geojson_features(gj):
        geom = (feat or {}).get("geometry") or {}
        gtype = geom.get("type")
        gtypes[gtype] += 1
        coords = geom.get("coordinates")

        if gtype == "Polygon" and coords and coords[0]:
            polys.append(np.array(coords[0], dtype=np.float32))

        elif gtype == "MultiPolygon":
            for poly in coords or []:
                if poly and poly[0]:
                    polys.append(np.array(poly[0], dtype=np.float32))

        elif gtype == "Point" and isinstance(coords, (list, tuple)) and len(coords) >= 2:
            points.append((float(coords[0]), float(coords[1])))

    return polys, points, gtypes


# ----------------------------
# Rasterization + contours
# ----------------------------
def rasterize_polys(polys_xy, H, W, scale: float):
    mask = np.zeros((H, W), dtype=np.uint8)
    used = 0
    for poly in polys_xy:
        if poly is None or len(poly) < 3:
            continue
        p = np.round(poly * scale).astype(np.int32)
        p[:, 0] = np.clip(p[:, 0], 0, W - 1)
        p[:, 1] = np.clip(p[:, 1], 0, H - 1)
        cv2.fillPoly(mask, [p], 1)
        used += 1
    return mask, used


def rasterize_points_as_disks(points_xy, H, W, scale: float, radius_lvl_px: int):
    mask = np.zeros((H, W), dtype=np.uint8)
    kept = 0
    for (x, y) in points_xy:
        cx = int(round(x * scale))
        cy = int(round(y * scale))
        if 0 <= cx < W and 0 <= cy < H:
            cv2.circle(mask, (cx, cy), int(radius_lvl_px), 1, thickness=-1)
            kept += 1
    return mask.astype(bool), kept


def mask_to_polylines(mask_bool, inv_scale: float, min_area_px: int):
    m = (mask_bool.astype(np.uint8) * 255)
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)

    polylines = []
    for c in contours:
        if c.shape[0] < 3:
            continue
        if cv2.contourArea(c) < min_area_px:
            continue
        pts_level = c[:, 0, :].astype(np.float32)
        pts_lvl0 = pts_level * inv_scale
        pts = [[float(x), float(y), 0.0] for x, y in pts_lvl0]
        polylines.append(pts)
    return polylines


def save_bool_png(mask_bool, out_path):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), (mask_bool.astype(np.uint8) * 255))


def rgba(r, g, b, a):
    return f"rgba({r}, {g}, {b}, {a})"


def make_elem(label, pts, line_rgb, fill_alpha=0.35, line_width=2):
    r, g, b = line_rgb
    return {
        "closed": True,
        "type": "polyline",
        "points": pts,
        "lineWidth": int(line_width),
        "lineColor": f"rgb({r},{g},{b})",
        "fillColor": rgba(r, g, b, float(fill_alpha)),
        "label": {"value": label},  # keep same style as your tubule json
    }
def filter_small_components(mask_bool: np.ndarray, min_area_px: int) -> np.ndarray:
    """Keep only connected components with area >= min_area_px (8-connectivity)."""
    m = (mask_bool.astype(np.uint8) * 255)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    # stats: [label, x, y, w, h, area] for each component, label 0 is background
    keep = np.zeros_like(mask_bool, dtype=bool)

    for lab in range(1, num):
        area = stats[lab, cv2.CC_STAT_AREA]
        if area >= min_area_px:
            keep |= (labels == lab)

    return keep

def refine_subcompartments(
    *,
    wsi: str,
    tubules_subcompartments_json: str,
    nuclei_json: str,
    out_json: str,
    selected_tubule_ids=None,
    level: int = 0,
    fill_alpha: float = 0.35,
    line_width: int = 2,
    pad_px: int = 64,
    min_lumen_area_px: int = 50,
    min_area_px: int = 10,
    nuclei_point_radius_px: int = 3,
    debug_dir: str = None,
):
    if level != 0:
        raise ValueError("ROI-per-tubule mode assumes level-0 coords. Keep level=0.")

    slide = TiffSlide(wsi)
    try:
        W, H = slide.level_dimensions[0]
        print(f"[INFO] Level=0 H={H} W={W}")

        # ---- tubules ----
        elems = load_dsa_elements(tubules_subcompartments_json)
        space_polys, nonspace_polys = [], []
        # label_counts = Counter()
        for e in elems:
            elem_id = str(e.get("id", "")).strip()   # DSA elements often have "id"
            if selected_tubule_ids is not None:
                if not elem_id or elem_id not in selected_tubule_ids:
                    continue

            lab = get_elem_label(e)
            pts = elem_points_to_xy(e)
            if pts.shape[0] < 3:
                continue

            ll = lab.lower()
            if "luminal" in ll:
                space_polys.append(pts)
            elif "eosinophilic" in ll:
                nonspace_polys.append(pts)

            # lab = get_elem_label(e)
            # label_counts[lab] += 1
            # pts = elem_points_to_xy(e)
            # if pts.shape[0] < 3:
            #     continue
            # ll = lab.lower()
            # if "luminal" in ll:
            #     space_polys.append(pts)
            # elif "eosinophilic" in ll:
            #     nonspace_polys.append(pts)

        # print("[INFO] Top tubule labels:", label_counts.most_common(10))
        print(f"[INFO] space polys (Luminal): {len(space_polys)}")
        print(f"[INFO] nonspace polys (Eosinophilic): {len(nonspace_polys)}")

        # ---- nuclei ----
        nuc_polys, nuc_points, gtypes = load_nuclei_geometries_auto(nuclei_json)
        print("[INFO] Nuclei geometry types:", dict(gtypes))
        print(f"[INFO] Nuclei polygons: {len(nuc_polys)}  points: {len(nuc_points)}")

        nuc_poly_bboxes = np.zeros((len(nuc_polys), 4), dtype=np.float32)
        for i, p in enumerate(nuc_polys):
            nuc_poly_bboxes[i] = bbox_from_poly_xy(p)

        nuc_points_xy = (
            np.array(nuc_points, dtype=np.float32)
            if len(nuc_points)
            else np.zeros((0, 2), dtype=np.float32)
        )

        def select_nuclei_for_bbox(x0, y0, x1, y1):
            poly_idxs = []
            if nuc_poly_bboxes.shape[0] > 0:
                nb = nuc_poly_bboxes
                hit = (nb[:, 0] <= x1) & (nb[:, 2] >= x0) & (nb[:, 1] <= y1) & (nb[:, 3] >= y0)
                poly_idxs = np.where(hit)[0].tolist()

            point_idxs = []
            if nuc_points_xy.shape[0] > 0:
                hitp = (
                    (nuc_points_xy[:, 0] >= x0) & (nuc_points_xy[:, 0] <= x1) &
                    (nuc_points_xy[:, 1] >= y0) & (nuc_points_xy[:, 1] <= y1)
                )
                point_idxs = np.where(hitp)[0].tolist()

            return poly_idxs, point_idxs

        def rasterize_nuclei_roi(x0, y0, x1, y1, roi_h, roi_w):
            poly_idxs, point_idxs = select_nuclei_for_bbox(x0, y0, x1, y1)
            nuclei_roi = np.zeros((roi_h, roi_w), dtype=np.uint8)

            for j in poly_idxs:
                fill_poly_local(nuclei_roi, nuc_polys[j], x0, y0, val=1)

            if len(point_idxs) > 0:
                pts = [tuple(nuc_points_xy[j].tolist()) for j in point_idxs]
                nuclei_roi |= rasterize_points_in_roi(
                    pts, x0, y0, roi_h, roi_w, radius_px=nuclei_point_radius_px
                )
            return nuclei_roi

        elems_out = []

        # ✅ ONE unified processor for any tubule polygon
        def process_one_tubule(poly, kind: str):
            bx0, by0, bx1, by1 = bbox_from_poly_xy(poly)
            x0, y0, x1, y1 = expand_and_clip_bbox(bx0, by0, bx1, by1, pad_px, W, H)
            roi_h, roi_w = (y1 - y0 + 1), (x1 - x0 + 1)

            roi = np.zeros((roi_h, roi_w), dtype=np.uint8)
            fill_poly_local(roi, poly, x0, y0, val=1)

            nuclei_roi = rasterize_nuclei_roi(x0, y0, x1, y1, roi_h, roi_w)

            if kind == "Eosinophilic":
                nuclei_in = (nuclei_roi == 1) & (roi == 1)
                minus = (roi == 1) & (nuclei_roi == 0)

                for pts in mask_to_polylines_with_offset(nuclei_in, min_area_px, x0, y0):
                    elems_out.append(make_elem("Nuclei", pts, (255, 0, 0), fill_alpha, line_width))
                for pts in mask_to_polylines_with_offset(minus, min_area_px, x0, y0):
                    elems_out.append(make_elem("Eosinophilic", pts, (0, 255, 0), fill_alpha, line_width))

            elif kind == "Luminal":
                lumen_minus_u8 = (roi == 1).astype(np.uint8)
                lumen_minus_u8[nuclei_roi == 1] = 0
                lumen_clean = filter_small_components_u8(lumen_minus_u8, min_lumen_area_px)

                for pts in mask_to_polylines_with_offset(lumen_clean, min_area_px, x0, y0):
                    elems_out.append(make_elem("Luminal Space", pts, (0, 0, 255), fill_alpha, line_width))

            else:
                raise ValueError(f"Unknown kind: {kind}")

        # run both lists, but same core logic
        for poly in tqdm(nonspace_polys, desc="Processing Eosinophilic tubules"):
            process_one_tubule(poly, "Eosinophilic")

        for poly in tqdm(space_polys, desc="Processing Luminal tubules"):
            process_one_tubule(poly, "Luminal")

        payload = {"name": "tubular_subcompartments", "elements": elems_out}
        Path(os.path.dirname(out_json)).mkdir(parents=True, exist_ok=True)
        with open(out_json, "w") as f:
            json.dump(payload, f, separators=(",", ":"))

        print(f"[INFO] Output elements: {len(elems_out)}")
        print(f"[INFO] Wrote: {out_json}")

    finally:
        slide.close()

def integrated_refine_subcompartments_single(
    wsi: str,
    tubules_subcompartments_json: str,
    nuclei_json: str,
    out_json: str,
    selected_tubule_ids=None, 
    *,
    level: int = 0,
    fill_alpha: float = 0.35,
    line_width: int = 2,
    pad_px: int = 64,
    min_lumen_area_px: int = 50,
    min_area_px: int = 10,
    nuclei_point_radius_px: int = 3,
    debug_dir: str = None,
):
    """
    Integrated wrapper (like integrated_pipeline_single):
      - One public entry point
      - Calls the core function that does all work
    """
    refine_subcompartments(
        wsi=wsi,
        tubules_subcompartments_json=tubules_subcompartments_json,
        nuclei_json=nuclei_json,
        out_json=out_json,
        selected_tubule_ids=selected_tubule_ids,
        level=level,
        fill_alpha=fill_alpha,
        line_width=line_width,
        pad_px=pad_px,
        min_lumen_area_px=min_lumen_area_px,
        min_area_px=min_area_px,
        nuclei_point_radius_px=nuclei_point_radius_px,
        debug_dir=debug_dir,
    )

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wsi", required=True)
    ap.add_argument("--tubules_subcompartments_json", required=True)
    ap.add_argument("--nuclei_json", required=True)
    ap.add_argument("--out_json", required=True)

    ap.add_argument("--level", type=int, default=0)
    ap.add_argument("--fill_alpha", type=float, default=0.35)
    ap.add_argument("--line_width", type=int, default=2)
    ap.add_argument("--pad_px", type=int, default=64)
    ap.add_argument("--min_lumen_area_px", type=int, default=50)
    ap.add_argument("--min_area_px", type=int, default=10)
    ap.add_argument("--nuclei_point_radius_px", type=int, default=3)
    ap.add_argument("--debug_dir", default=None)

    args = ap.parse_args()
    integrated_refine_subcompartments_single(**vars(args))


if __name__ == "__main__":
    main()
