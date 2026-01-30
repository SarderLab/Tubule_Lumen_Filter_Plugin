import os, sys, json, tempfile, datetime, warnings, logging
from pathlib import Path

import girder_client
from ctk_cli import CLIArgumentParser

warnings.filterwarnings("ignore", category=UserWarning)
print("RUNNING:", __file__)
print("ARGV:", sys.argv)

WSI_EXTS = (".svs", ".tif", ".tiff", ".ndpi", ".scn", ".svslide")


# ───────────────────────────────
# Logging
# ───────────────────────────────
def set_logger():
    log_path = os.path.join("/tmp", "refine_subcompartments_orchestrator.log")
    logging.basicConfig(
        filename=log_path,
        filemode="w",
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logging.getLogger().addHandler(console)
    logging.info(f"Log file: {log_path}")


# ───────────────────────────────
# Workdir helper (same pattern as your other orchestrator)
# ───────────────────────────────
def _pick_workdir():
    try:
        gw = "/mnt/girder_worker"
        if os.path.isdir(gw):
            entries = [e for e in os.listdir(gw) if not e.startswith(".")]
            if entries:
                return os.path.join(gw, entries[0])
    except Exception:
        pass

    for env in ("TMPDIR", "TEMP", "TMP"):
        p = os.getenv(env)
        if p and os.path.isdir(p):
            return p

    return tempfile.gettempdir()


def _resolve_file_and_item(gc: girder_client.GirderClient, input_id: str):
    """
    Return (file_doc, item_doc). Accepts either a file _id or an item _id.
    If item id: choose the most suitable WSI file under the item.
    """
    # try file id
    try:
        fdoc = gc.get(f"/file/{input_id}")
        idoc = gc.get(f"/item/{fdoc['itemId']}")
        return fdoc, idoc
    except Exception:
        pass

    # else treat as item id
    idoc = gc.get(f"/item/{input_id}")
    files = list(gc.listFile(idoc["_id"]))
    if not files:
        raise RuntimeError(f"Item {input_id} has no files.")

    def score(fd):
        name = fd.get("name", "").lower()
        ext = os.path.splitext(name)[1]
        is_wsi = ext in WSI_EXTS
        size = int(fd.get("size", 0))
        return (1 if is_wsi else 0, size)

    fdoc = sorted(files, key=score, reverse=True)[0]
    return fdoc, idoc


# ───────────────────────────────
# Annotation helpers
# ───────────────────────────────
def _get_latest_annot_by_name(gc, item_id: str, annot_name: str):
    annots = list(gc.get(f"/annotation/item/{item_id}", parameters={"sort": "updated"}))
    for a in reversed(annots):  # newest last
        name = a.get("annotation", {}).get("name", "").strip()
        if name == annot_name:
            return a
    return None


def _write_annotation_to_json(annot_doc: dict, out_path: str):
    """
    Writes to the "DSA export style" your parsers already support:
      [ { annotation: { name, elements, ... } } ]  OR [ { ...annotation... } ]
    Your load_dsa_elements() supports list-wrapped.
    """
    Path(os.path.dirname(out_path)).mkdir(parents=True, exist_ok=True)

    # Your loaders accept list with the annotation dict inside:
    #   if isinstance(data, list) and data: data = data[0]
    #   then look for data["elements"] or data["annotation"]["elements"]
    # So simplest is to write [annot_doc["annotation"]]
    with open(out_path, "w") as f:
        json.dump([annot_doc["annotation"]], f)


# ───────────────────────────────
# Main Orchestrator
# ───────────────────────────────
def main(args):
    set_logger()

    apiURL = str(args.girderApiUrl)
    token = str(args.girderToken)
    svs_input = str(args.svsFile)

    # Annotation names on the item
    tubules_sub_name = str(getattr(args, "tubulesSubcompartmentsName", "tubules_subcompartments"))
    nuclei_name = str(getattr(args, "nucleiAnnotationName", "nuclei"))

    # Params for your refine code
    level = int(getattr(args, "level", 0))
    fill_alpha = float(getattr(args, "fillAlpha", 0.35))
    line_width = int(getattr(args, "lineWidth", 2))
    pad_px = int(getattr(args, "padPx", 64))
    min_lumen_area_px = int(getattr(args, "minLumenAreaPx", 50))
    min_area_px = int(getattr(args, "minAreaPx", 10))
    nuclei_point_radius_px = int(getattr(args, "nucleiPointRadiusPx", 3))

    # Optional debug folder id (Girder folder)
    debug_masks_dir_id = None
    if getattr(args, "debugMasksDir", None):
        debug_masks_dir_id = str(args.debugMasksDir).split("/")[-2]

    output_basename = f"tubules_subcompartments_refined_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    logging.info(f"Using girderApiUrl: {apiURL}")
    logging.info(f"Using girderToken: {token[:4]}...{token[-4:]}")
    logging.info(f"Input svsFile (id): {svs_input}")
    logging.info(f"Looking for annotation: {tubules_sub_name}")
    logging.info(f"Looking for nuclei annotation: {nuclei_name}")

    # 1) Connect to Girder
    gc = girder_client.GirderClient(apiUrl=apiURL)
    gc.setToken(token)
    logging.info("Connected to Girder.")

    # 2) Resolve file + item
    fdoc, idoc = _resolve_file_and_item(gc, svs_input)
    file_id = fdoc["_id"]
    item_id = idoc["_id"]
    file_name = fdoc["name"]
    logging.info(f"Using item: {item_id} ({idoc.get('name','')})")
    logging.info(f"Using file: {file_id} ({file_name})")

    # 3) Workdir + download SVS
    workdir = _pick_workdir()
    svs_path = os.path.join(workdir, file_name)
    gc.downloadFile(file_id, svs_path)
    logging.info(f"Downloaded WSI to: {svs_path}")

    # 4) Fetch required annots
    tub_sub = _get_latest_annot_by_name(gc, item_id, tubules_sub_name)
    if tub_sub is None:
        raise RuntimeError(f"No '{tubules_sub_name}' annotation found on this item.")
    logging.info(f"Using tubules_subcompartments annot id: {tub_sub.get('_id')} updated={tub_sub.get('updated')}")

    nuc_annot = _get_latest_annot_by_name(gc, item_id, nuclei_name)
    if nuc_annot is None:
        raise RuntimeError(f"No '{nuclei_name}' annotation found on this item.")
    logging.info(f"Using nuclei annot id: {nuc_annot.get('_id')} updated={nuc_annot.get('updated')}")

    # 5) Write inputs to disk
    inputs_dir = os.path.join(workdir, "inputs")
    Path(inputs_dir).mkdir(parents=True, exist_ok=True)

    tubules_sub_json = os.path.join(inputs_dir, "tubules_subcompartments.json")
    nuclei_json = os.path.join(inputs_dir, "nuclei.json")

    _write_annotation_to_json(tub_sub, tubules_sub_json)
    _write_annotation_to_json(nuc_annot, nuclei_json)

    logging.info(f"Wrote tubules_subcompartments JSON: {tubules_sub_json}")
    logging.info(f"Wrote nuclei JSON: {nuclei_json}")

    # 6) Output paths
    outputs_dir = os.path.join(workdir, "outputs")
    Path(outputs_dir).mkdir(parents=True, exist_ok=True)

    debug_dir = None
    if debug_masks_dir_id:
        debug_dir = os.path.join(workdir, "debug_masks")
        Path(debug_dir).mkdir(parents=True, exist_ok=True)

    output_json_path = os.path.join(outputs_dir, output_basename)

    # 7) Run your refine pipeline
    logging.info("Running refine_subcompartments pipeline...")

    # IMPORTANT: import your function from wherever you placed it
    # Example:
    # from SC_seg.Code.refine_subcompartments import integrated_refine_subcompartments_single
    from Refine_tubular_SCSeg.Code.refine_subcompartments import integrated_refine_subcompartments_single

    integrated_refine_subcompartments_single(
        wsi=svs_path,
        tubules_subcompartments_json=tubules_sub_json,
        nuclei_json=nuclei_json,
        out_json=output_json_path,
        level=level,
        fill_alpha=fill_alpha,
        line_width=line_width,
        pad_px=pad_px,
        min_lumen_area_px=min_lumen_area_px,
        min_area_px=min_area_px,
        nuclei_point_radius_px=nuclei_point_radius_px,
        debug_dir=debug_dir,
    )

    logging.info(f"Pipeline complete. Output JSON: {output_json_path}")

    # 8) Post annotation back to DSA and upload file
    with open(output_json_path, "r") as f:
        payload = json.load(f)

    # Ensure DSA expects {"name":..., "elements":[...]} (your pipeline already writes this)
    _ = gc.post(
        path="annotation",
        parameters={"itemId": item_id},
        data=json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )
    logging.info(f"Posted refined annotation to item {item_id}.")

    uploaded = gc.uploadFileToItem(
        item_id, output_json_path, filename=output_basename, mimeType="application/json"
    )
    logging.info(f"Uploaded output JSON as item file: {uploaded.get('name')} ({uploaded.get('_id')})")

    # 9) Optional: upload debug masks to a folder
    if debug_masks_dir_id and debug_dir:
        count = 0
        for fn in os.listdir(debug_dir):
            fp = os.path.join(debug_dir, fn)
            if os.path.isfile(fp):
                gc.uploadFileToFolder(debug_masks_dir_id, fp, filename=fn)
                count += 1
        logging.info(f"Uploaded {count} debug files to folder {debug_masks_dir_id}")

    logging.info(
        json.dumps(
            {
                "status": "ok",
                "girder_item_id": item_id,
                "wsi_local_path": svs_path,
                "inputs": {
                    "tubules_subcompartments_json": tubules_sub_json,
                    "nuclei_json": nuclei_json,
                },
                "output_json_path": output_json_path,
                "girder_uploaded_file_id": uploaded.get("_id"),
                "girder_uploaded_file_name": uploaded.get("name"),
            }
        )
    )


if __name__ == "__main__":
    # CTK-CLI parser provides your girderApiUrl/girderToken/svsFile fields.
    # You must also define the extra args in your XML, but this works once provided.
    main(CLIArgumentParser().parse_args())
