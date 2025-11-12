"""
generate_drone_data.py
Creates synthetic RED and NIR drone bands + NDVI GeoTIFF for testing NDVI analysis.
"""

import os
import numpy as np
import rasterio
from rasterio.transform import from_origin
import matplotlib.pyplot as plt

# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------
OUTPUT_DIR = os.path.join("data", "drone_data")
os.makedirs(OUTPUT_DIR, exist_ok=True)

width, height = 512, 512
pixel_size = 0.5  # meters per pixel
transform = from_origin(80.0, 25.0, pixel_size, pixel_size)

# ---------------------------------------------------------------------
# Generate synthetic drone imagery
# ---------------------------------------------------------------------
print("🛰️ Generating synthetic drone RED & NIR bands...")

# Simulate Red (0.6–0.7 μm) and NIR (0.8–0.9 μm) reflectance
red_band = np.random.uniform(0.1, 0.8, (height, width))
nir_band = np.random.uniform(0.1, 0.9, (height, width))

# Introduce artificial “deforestation” — lower NIR reflectance
deforest_mask = np.zeros_like(nir_band)
deforest_mask[100:250, 300:450] = 1
nir_band = np.where(deforest_mask == 1, nir_band * 0.3, nir_band)

# Compute NDVI = (NIR - RED) / (NIR + RED)
ndvi = (nir_band - red_band) / (nir_band + red_band + 1e-6)
ndvi = np.clip(ndvi, -1, 1)

# ---------------------------------------------------------------------
# Save to GeoTIFFs
# ---------------------------------------------------------------------
profile = {
    "driver": "GTiff",
    "height": height,
    "width": width,
    "count": 1,
    "dtype": "float32",
    "crs": "EPSG:4326",
    "transform": transform
}

red_path = os.path.join(OUTPUT_DIR, "drone_red.tif")
nir_path = os.path.join(OUTPUT_DIR, "drone_nir.tif")
ndvi_path = os.path.join(OUTPUT_DIR, "drone_ndvi.tif")

with rasterio.open(red_path, "w", **profile) as dst:
    dst.write(red_band.astype(np.float32), 1)
with rasterio.open(nir_path, "w", **profile) as dst:
    dst.write(nir_band.astype(np.float32), 1)
with rasterio.open(ndvi_path, "w", **profile) as dst:
    dst.write(ndvi.astype(np.float32), 1)

# ---------------------------------------------------------------------
# Visualization
# ---------------------------------------------------------------------
plt.imshow(ndvi, cmap="RdYlGn")
plt.title("Synthetic Drone NDVI (green = healthy vegetation, red = loss)")
plt.colorbar(label="NDVI")
plt.savefig(os.path.join(OUTPUT_DIR, "drone_ndvi_preview.png"), dpi=150)
plt.close()

print("✅ Drone data generated successfully!")
print(f"📂 Files saved to: {os.path.abspath(OUTPUT_DIR)}")
