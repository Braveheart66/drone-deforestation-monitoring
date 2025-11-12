# drone_utils.py
import rasterio
import numpy as np
from shapely.geometry import shape
import geopandas as gpd

def compute_drone_ndvi(nir_path, red_path):
    """Compute NDVI array (float32) from NIR and RED GeoTIFFs."""
    with rasterio.open(nir_path) as nir_src, rasterio.open(red_path) as red_src:
        nir = nir_src.read(1).astype("float32")
        red = red_src.read(1).astype("float32")

        # Align dimensions (resize red if slightly off)
        if nir.shape != red.shape:
            min_h, min_w = min(nir.shape[0], red.shape[0]), min(nir.shape[1], red.shape[1])
            nir, red = nir[:min_h, :min_w], red[:min_h, :min_w]

        ndvi = (nir - red) / (nir + red + 1e-6)
        ndvi = np.clip(ndvi, -1, 1)

        meta = nir_src.meta.copy()
        return ndvi, meta

def ndvi_stats_from_drone(ndvi_arr, ndvi_thresh=0.4):
    """Compute summary stats from drone NDVI array."""
    valid = np.isfinite(ndvi_arr)
    mean_ndvi = float(np.nanmean(ndvi_arr[valid])) if valid.any() else None
    tree_cover_pixels = (ndvi_arr > ndvi_thresh).sum()
    total_pixels = valid.sum()
    percent_tree_cover = (tree_cover_pixels / total_pixels * 100) if total_pixels > 0 else 0
    return {
        "mean_ndvi": mean_ndvi,
        "percent_tree_cover": percent_tree_cover,
        "total_pixels": int(total_pixels)
    }
