#!/usr/bin/env python
"""List tubule elements from the latest 'tubules' annotation.

This helper connects to a Girder server, finds the latest annotation by name on the
item associated with the provided file/item id, and prints a JSON array of elements
with the following fields:
  - index: sequential index in the annotation (0-based)
  - element_id: the element's id if provided (e.g. 'id' or '_id')
  - bbox: [min_x, min_y, max_x, max_y]
  - centroid: [cx, cy]
  - label: optional label if present

This CLI is intended to power a simple UI or to produce a machine-readable list
so a user can pick element ids to pass to the segmentation CLI using --tubules_ids.
"""

import json
import os
from pathlib import Path
import sys
import girder_client
from ctk_cli import CLIArgumentParser

WSI_EXTS = (".svs", ".tif", ".tiff", ".ndpi", ".scn", ".svslide")


def _resolve_file_and_item(gc, input_id: str):
    # Try file id first
    try:
        fdoc = gc.get(f"/file/{input_id}")
        idoc = gc.get(f"/item/{fdoc['itemId']}")
        return fdoc, idoc
    except Exception:
        pass

    # Try as item id
    idoc = gc.get(f"/item/{input_id}")
    files = list(gc.listFile(idoc["_id"]))
    if not files:
        raise RuntimeError(f"Item {input_id} has no files.")

    # choose largest or WSI-like file
    def score(fd):
        name = fd.get("name", "").lower()
        ext = os.path.splitext(name)[1]
        is_wsi = ext in WSI_EXTS
        size = int(fd.get("size", 0))
        return (1 if is_wsi else 0, size)

    fdoc = sorted(files, key=score, reverse=True)[0]
    return fdoc, idoc


def _get_latest_tubules_annot(gc, item_id: str):
    annots = list(gc.get(f"/annotation/item/{item_id}", parameters={"sort": "updated"}))
    for a in reversed(annots):
        name = a.get("annotation", {}).get("name", "").strip()
        if name == "tubules":
            return a
    return None


def _element_id(elem):
    for k in ("_id", "id", "elementId", "objectId"):
        if k in elem:
            return str(elem[k])
    return None


def _points_to_bbox_centroid(points):
    # points may be [[x,y,z], ...] or [x,y] pairs
    xs = []
    ys = []
    for p in points:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            xs.append(float(p[0]))
            ys.append(float(p[1]))
    if not xs:
        return None, None
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    cx = (min_x + max_x) / 2.0
    cy = (min_y + max_y) / 2.0
    return [min_x, min_y, max_x, max_y], [cx, cy]


def main(args):

    gc = girder_client.GirderClient(apiUrl=args.girderApiUrl)
    gc.setToken(args.girderToken)

    fdoc, idoc = _resolve_file_and_item(gc, args.input_file)
    item_id = idoc["_id"]

    tubules = _get_latest_tubules_annot(gc, item_id)
    if tubules is None:
        raise SystemExit("No 'tubules' annotation found on this item.")

    data = tubules.get("annotation", {})
    elements = data.get("elements", [])

    listing = []
    for i, elem in enumerate(elements):
        pts = elem.get("points", [])
        bbox, centroid = _points_to_bbox_centroid(pts)
        listing.append(
            {
                "index": i,
                "element_id": _element_id(elem),
                "bbox": bbox,
                "centroid": centroid,
                "label": (
                    elem.get("label") if isinstance(elem.get("label"), str) else None
                ),
            }
        )

    if args.out:
        Path(os.path.dirname(args.out) or ".").mkdir(parents=True, exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(listing, f, indent=2)
        print(f"Wrote {len(listing)} elements to {args.out}")
    else:
        print(json.dumps(listing, indent=2))

    if args.publish_annotation:
        # Build a shallow copy of the top-level annotation but replace element labels with ids
        data = tubules.get("annotation", {})
        ann_copy = {
            "name": (f'{"id_" + data.get("name")}'),
            "elements": [],
        }
        for elem in data.get("elements", []):
            # copy minimal fields and set label to element id
            elem_copy = {
                "type": elem.get("type", "polyline"),
                "closed": elem.get("closed", True),
                "points": elem.get("points", []),
                "lineWidth": elem.get("lineWidth", 2),
                "lineColor": elem.get("lineColor", "rgb(255,255,0)"),
                "fillColor": elem.get("fillColor", "rgba(255,255,0,0.4)"),
            }
            eid = _element_id(elem) or ""
            # Girder/DSA annotation schema expects the 'label' to be an object
            # with a 'value' field (see how segmentation output uses
            # {"label": {"value": name}}). Provide the same shape here so
            # the server will accept the published annotation.
            elem_copy["label"] = {"value": str(eid)}
            ann_copy["elements"].append(elem_copy)

        # Post the new annotation to the item so the DSA viewer can render it
        # Diagnostic: inspect payload before sending
        try:
            print("Prepared annotation payload summary:")
            print(" name:", ann_copy.get("name"))
            elems = ann_copy.get("elements", [])
            print(" elements:", len(elems))
            # show first element keys and types
            if elems:
                sample = elems[0]
                print(" sample element keys:", list(sample.keys()))
                print(
                    " sample element types:",
                    {k: type(v).__name__ for k, v in sample.items()},
                )

            post_resp = gc.post(
                "annotation",
                parameters={"itemId": item_id},
                data=json.dumps(ann_copy),
                headers={"Content-Type": "application/json"},
            )
            print(
                "Published annotation with element-id labels to item",
                item_id,
                "annotation id:",
                post_resp.get("_id"),
            )
        except Exception as exc:
            print("Failed to publish annotation:", exc)
            try:
                dump = json.dumps(ann_copy)
                print("Payload (truncated 2000 chars):", dump[:2000])
            except Exception:
                print("Could not serialize payload for printing.")


if __name__ == "__main__":
    main(CLIArgumentParser().parse_args())
