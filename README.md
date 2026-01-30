# Tubule Subcompartment Segmentation from WSI Annotations

This script refines **kidney tubule subcompartment annotations** on whole slide images (WSIs) by combining:

* Tubule compartment polygons (Luminal, Eosinophilic) from **DSA JSON**
* Nuclei annotations from **GeoJSON**
* Slide geometry from the **WSI (TIFF/WSI)**

It performs following:

* assign nuclei inside eosinophilic regions → **Nuclei**
* subtract nuclei from eosinophilic → **Eosinophilic (cleaned)**
* subtract nuclei from lumen → **Luminal Space (cleaned)**
* remove small noisy components
* output clean **polygon annotations** back to DSA-style JSON

The result is a json file suitable for Digital Slide Archive (DSA), HistomicsUI, or downstream morphometry/ML pipelines.

---

## Features

* **Per-tubule ROI processing** (memory efficient; avoids full-slide masks)
* Supports:

  * Polygons (nuclei or regions)
  * Points (nuclei centers → optional disks)
* Removes:

  * nuclei from lumen/eosinophilic masks
  * small connected components (noise)
* Outputs **DSA-compatible polyline elements**

---

## Inputs

### 1. WSI

Whole slide image readable by `tiffslide`

Examples:

```
.svs
.tiff
.tif
```

---

### 2. Tubule annotations (DSA JSON)

Expected structure:

```json
{
  "elements": [
    {
      "type": "polyline",
      "points": [[x, y, 0], ...],
      "label": {"value": "Luminal"}
    },
    {
      "label": {"value": "Eosinophilic"}
    }
  ]
}
```

Required labels:

* `Luminal`
* `Eosinophilic`

---

### 3. Nuclei annotations (GeoJSON)

Supports:

* Polygon
* MultiPolygon
* Point

Examples:

```json
{
  "type": "FeatureCollection",
  "features": [...]
}
```

or list-of-features format.

---

## Outputs

### DSA-style JSON

```json
{
  "name": "tubular_subcompartments",
  "elements": [...]
}
```

Generated labels:

| Label         | Meaning                    |
| ------------- | -------------------------- |
| Nuclei        | nuclei inside eosinophilic |
| Eosinophilic  | eosinophilic minus nuclei  |
| Luminal Space | lumen minus nuclei         |

---

## Installation

### Requirements

* Python ≥ 3.9
* numpy
* opencv-python
* tiffslide
* tqdm

### Install

```bash
pip install numpy opencv-python tiffslide tqdm
```

---

## Usage

Basic:

```bash
python script.py \
  --wsi slide.svs \
  --tubules_subcompartments_json tubules.json \
  --nuclei_geojson nuclei.geojson \
  --out_json refined.json
```

---

## Arguments

| Argument                         | Description                               | Default  |
| -------------------------------- | ----------------------------------------- | -------- |
| `--wsi`                          | Input WSI path                            | required |
| `--tubules_subcompartments_json` | DSA tubule polygons                       | required |
| `--nuclei_geojson`               | nuclei annotations                        | required |
| `--out_json`                     | output JSON path                          | required |
| `--level`                        | slide level (currently only 0 supported)  | 0        |
| `--pad_px`                       | padding around each tubule ROI            | 64       |
| `--min_lumen_area_px`            | remove lumen components smaller than this | 50       |
| `--min_area_px`                  | drop tiny polygon contours                | 10       |
| `--fill_alpha`                   | fill transparency                         | 0.35     |
| `--line_width`                   | polygon border width                      | 2        |
| `--debug_dir`                    | optional debug mask outputs               | None     |

---

## Processing Pipeline

For each tubule polygon:

1. Compute bounding box
2. Expand with padding
3. Rasterize:

   * tubule region
   * intersecting nuclei
4. Boolean operations:

   * nuclei ∩ eosinophilic → Nuclei
   * eosinophilic − nuclei → cleaned eosinophilic
   * lumen − nuclei → cleaned lumen
5. Remove small connected components
6. Convert masks → polygons
7. Write DSA elements

---

## Why ROI-based processing?

Instead of rasterizing the entire slide:

* much lower memory usage
* faster for sparse annotations
* avoids huge full-resolution masks
* scales to gigapixel WSIs

---

## Example Workflow

```bash
python refine_subcompartments.py \
  --wsi kidney.svs \
  --tubules_subcompartments_json tubules.json \
  --nuclei_geojson nuclei.geojson \
  --out_json kidney_refined.json \
  --pad_px 80 \
  --min_lumen_area_px 100
```

Then load `refined.json` in:

* Digital Slide Archive (DSA)
* HistomicsUI
* or downstream ML pipeline
