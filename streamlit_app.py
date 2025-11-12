# streamlit_app.py — FINAL PRESENTATION BUILD (fixed & robust)
import streamlit as st
import os, json, datetime as dt, tempfile, traceback, requests, threading, time, base64
import numpy as np
import matplotlib.pyplot as plt
from PIL import Image
import rasterio
from rasterio.enums import Resampling
from streamlit_folium import st_folium
import folium
from jinja2 import Template
from branca.element import Element
from twilio.rest import Client
from dotenv import load_dotenv

# -------------------- Directories --------------------
PERSISTENT_DIR = os.path.join(os.getcwd(), "persistent_maps")
ASSETS_DIR = os.path.join(os.getcwd(), "assets")
os.makedirs(PERSISTENT_DIR, exist_ok=True)
os.makedirs(ASSETS_DIR, exist_ok=True)

# -------------------- Custom imports (project modules) --------------------
from ndvi_analysis import run_analysis
from drone_utils import compute_drone_ndvi, ndvi_stats_from_drone

# -------------------- State --------------------
for key, default in {
    "analysis_done": False,
    "result": None,
    "drone_stats": None,
    "alert_triggered": False,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# -------------------- Helpers --------------------
def ndvi_array_to_png(ndvi_array, out_png, cmap_name="RdYlGn"):
    """Convert NDVI array to PNG using a matplotlib colormap. Handles all-NaN arrays safely."""
    arr = np.array(ndvi_array, dtype=float)
    arr = np.where(np.isfinite(arr), arr, np.nan)
    arr = np.clip(arr, -0.5, 1.0)
    valid = ~np.isnan(arr)
    if valid.any():
        vmin, vmax = float(np.nanmin(arr[valid])), float(np.nanmax(arr[valid]))
    else:
        # fallback range when no valid pixels
        vmin, vmax = -0.5, 1.0
    norm = (arr - vmin) / (vmax - vmin + 1e-9)
    cmap = plt.get_cmap(cmap_name)
    rgba = cmap(norm, bytes=True)  # returns uint8 RGBA
    rgb = rgba[..., :3]
    Image.fromarray(rgb).save(out_png)
    return out_png

def ensure_sharp_tif(in_tif, factor=1.5):
    """Resample GeoTIFF to be a bit larger for nicer overlays (non-destructive on failure)."""
    if not in_tif or not os.path.exists(in_tif):
        return in_tif
    out_tif = in_tif.replace(".tif", "_sharp.tif")
    try:
        with rasterio.open(in_tif) as src:
            data = src.read(
                out_shape=(src.count, int(src.height * factor), int(src.width * factor)),
                resampling=Resampling.bilinear,
            )
            meta = src.meta.copy()
            meta.update({"height": data.shape[1], "width": data.shape[2]})
            with rasterio.open(out_tif, "w", **meta) as dst:
                dst.write(data)
        return out_tif
    except Exception:
        return in_tif

def export_ee_to_tif_safe(ee_image, region_geojson, out_path, scale=10, timeout=120):
    """Export Earth Engine image to GeoTIFF and validate result is TIFF stream."""
    try:
        params = {"scale": scale, "region": region_geojson, "format": "GEO_TIFF"}
        url = ee_image.getDownloadURL(params)
        r = requests.get(url, stream=True, timeout=timeout)
        r.raise_for_status()
        content_type = r.headers.get("Content-Type", "")
        if "tiff" not in content_type and "octet-stream" not in content_type:
            raise Exception(f"EE export returned non-TIFF content-type: {content_type}")
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(8192):
                if chunk:
                    f.write(chunk)
        return out_path
    except Exception as e:
        print("EE export failed:", e)
        return None

def geojson_to_region(geo):
    """Return a geometry object suitable for Earth Engine export.
       Accepts FeatureCollection, Feature, or Polygon/MultiPolygon objects."""
    if geo is None:
        raise ValueError("AOI empty")
    if isinstance(geo, dict):
        t = geo.get("type")
        if t == "FeatureCollection":
            feats = geo.get("features", [])
            if not feats:
                raise ValueError("FeatureCollection empty")
            return feats[0].get("geometry")
        if t == "Feature":
            return geo.get("geometry")
        if t in ("Polygon", "MultiPolygon"):
            return geo
    raise ValueError("Unsupported AOI type")

# -------------------- Twilio + Siren --------------------
@st.cache_resource
def load_siren_audio():
    siren_path = os.path.join(ASSETS_DIR, "warning_siren.mp3")
    try:
        with open(siren_path, "rb") as f:
            return f.read()
    except Exception:
        return None

def send_whatsapp_twilio_async(phone, body):
    """Send WhatsApp message via Twilio in background (non-blocking)."""
    def worker(p, b):
        try:
            load_dotenv()
            sid, auth = os.getenv("TWILIO_SID"), os.getenv("TWILIO_AUTH")
            if not sid or not auth:
                print("❌ Missing Twilio credentials.")
                return
            client = Client(sid, auth)
            from_ = "whatsapp:+14155238886"
            phone_clean = p.strip()
            if not phone_clean.startswith("+"):
                phone_clean = "+91" + phone_clean
            # include from_ explicitly
            client.messages.create(from_=from_, to=f"whatsapp:{phone_clean}", body=b)
            print("✅ WhatsApp alert sent to", phone_clean)
        except Exception as ex:
            print("❌ Twilio send failed:", ex)
    threading.Thread(target=worker, args=(phone, body), daemon=True).start()

# -------------------- Streamlit UI --------------------
st.set_page_config(layout="wide", page_title="🌲 Drone-AI Deforestation Monitor", page_icon="🌍")
st.title("🌲 Drone-AI Deforestation Monitor")
st.caption("Real-time NDVI Analysis • Drone NDVI • WhatsApp Alerts")
st.divider()

# Sidebar
with st.sidebar:
    st.header("📍 Area of Interest (AOI)")
    aoi_choice = st.radio("AOI Source:", ["Upload GeoJSON", "Use Demo Lucknow AOI"])
    if aoi_choice == "Upload GeoJSON":
        geojson_file = st.file_uploader("Upload AOI (GeoJSON)", type=["geojson", "json"])
        aoi_geojson = json.load(geojson_file) if geojson_file else None
    else:
        aoi_geojson = {
            "type": "Polygon",
            "coordinates": [[[81.0115, 26.7902], [81.0552, 26.7902],
                             [81.0552, 26.8151], [81.0115, 26.8151], [81.0115, 26.7902]]]
        }

    st.header("🗓️ Time Range")
    past_start = st.date_input("Past Start", dt.date(2018,1,1))
    past_end = st.date_input("Past End", dt.date(2018,12,31))
    pres_start = st.date_input("Present Start", dt.date(2024,1,1))
    pres_end = st.date_input("Present End", dt.date(2024,12,31))
    ndvi_thresh = st.slider("NDVI Threshold (tree)", 0.0, 1.0, 0.4, 0.01)

    st.header("🚁 Drone Data (optional)")
    include_drone = st.checkbox("Include Drone NDVI Analysis")
    red_band = nir_band = None
    if include_drone:
        red_band = st.file_uploader("RED band (GeoTIFF)", type=["tif","tiff"], key="drone_red")
        nir_band = st.file_uploader("NIR band (GeoTIFF)", type=["tif","tiff"], key="drone_nir")

    st.header("📲 Alerts")
    phone = st.text_input("Recipient phone (e.g. +919876543210)")
    msg_enabled = st.checkbox("Enable WhatsApp alerts (Twilio)")

# -------------------- Main Analysis --------------------
if st.button("🚀 Run NDVI Analysis"):
    try:
        with st.spinner("Running Earth Engine NDVI analysis..."):
            result = run_analysis(aoi_geojson, str(past_start), str(past_end),
                                  str(pres_start), str(pres_end), ndvi_thresh)

        if not result or not result.get("stats"):
            st.error("Analysis failed — check AOI/dates/EE auth.")
            st.stop()

        stats = result["stats"]
        past_area = float(stats.get("area_past_ha", 0))
        present_area = float(stats.get("area_present_ha", 0))
        delta = present_area - past_area
        pct_change = ((delta / past_area) * 100) if past_area else 0.0

        c1,c2,c3 = st.columns(3)
        c1.metric("Past Cover (ha)", f"{past_area:.2f}")
        c2.metric("Present Cover (ha)", f"{present_area:.2f}")
        c3.metric("Change (%)", f"{pct_change:.2f}%")

        if delta < 0:
            st.warning(f"🚨 Deforestation detected: ↓ {abs(pct_change):.2f}%")
            siren = load_siren_audio()
            if siren:
                # streamlit audio accepts bytes directly
                st.audio(siren, format="audio/mp3")
            if msg_enabled and phone:
                send_whatsapp_twilio_async(phone, f"🚨 ALERT: {abs(pct_change):.2f}% deforestation detected.")
        else:
            st.success(f"🌱 Gain: +{abs(pct_change):.2f}%")

        # ---------------- Drone NDVI ----------------
        if include_drone and red_band and nir_band:
            st.markdown("### 🚁 Drone NDVI (Uploaded)")
            tmp = tempfile.mkdtemp()
            red_path, nir_path = os.path.join(tmp, "red.tif"), os.path.join(tmp, "nir.tif")
            with open(red_path, "wb") as f: f.write(red_band.read())
            with open(nir_path, "wb") as f: f.write(nir_band.read())
            ndvi_arr, meta = compute_drone_ndvi(nir_path, red_path)
            drone_stats = ndvi_stats_from_drone(ndvi_arr, ndvi_thresh)
            st.json(drone_stats)

            fig, ax = plt.subplots(figsize=(6,4))
            im = ax.imshow(ndvi_arr, cmap="RdYlGn", vmin=-0.2, vmax=1.0)
            plt.colorbar(im, ax=ax)
            ax.set_title("Drone NDVI Map")
            st.pyplot(fig)

        # ---------------- FINAL DYNAMIC NDVI SLIDER (custom HTML overlay) ----------------
        st.markdown("## 🗺️ NDVI Comparison Map (Past ↔ Present)")

        # Unique timestamp per analysis ensures fresh images
        run_id = int(time.time())

        ndvi_past_png = os.path.join(PERSISTENT_DIR, f"past_{run_id}.png")
        ndvi_present_png = os.path.join(PERSISTENT_DIR, f"present_{run_id}.png")
        past_tif_path = os.path.join(PERSISTENT_DIR, f"past_{run_id}.tif")
        pres_tif_path = os.path.join(PERSISTENT_DIR, f"present_{run_id}.tif")

        # Safe AOI handling: if AOI is too small, auto-pad bounding box
        try:
            region_geojson = geojson_to_region(aoi_geojson)
            if region_geojson.get("type") == "Polygon":
                coords = np.array(region_geojson["coordinates"][0])
                lon_min, lat_min = coords[:, 0].min(), coords[:, 1].min()
                lon_max, lat_max = coords[:, 0].max(), coords[:, 1].max()
                # pad very small AOIs
                if (lon_max - lon_min) < 0.005 or (lat_max - lat_min) < 0.005:
                    pad = 0.01
                    lon_min -= pad; lat_min -= pad; lon_max += pad; lat_max += pad
                    region_geojson = {
                        "type": "Polygon",
                        "coordinates": [[
                            [lon_min, lat_min],
                            [lon_max, lat_min],
                            [lon_max, lat_max],
                            [lon_min, lat_max],
                            [lon_min, lat_min],
                        ]]
                    }
        except Exception:
            # fallback region (small buffer near Lucknow) — ensures export won't be empty
            region_geojson = {
                "type": "Polygon",
                "coordinates": [[[80.94, 26.84], [80.96, 26.84], [80.96, 26.86], [80.94, 26.86], [80.94, 26.84]]]
            }

        ndvi_past_img = result.get("map_layers", {}).get("past")
        ndvi_present_img = result.get("map_layers", {}).get("present")

        if not ndvi_past_img or not ndvi_present_img:
            st.error("No NDVI layers available from Earth Engine result. Map cannot be generated.")
        else:
            st.info("📡 Exporting NDVI layers from Earth Engine (may take a short while)...")
            p_out = export_ee_to_tif_safe(ndvi_past_img, region_geojson, past_tif_path, scale=10)
            q_out = export_ee_to_tif_safe(ndvi_present_img, region_geojson, pres_tif_path, scale=10)

            if not p_out or not q_out:
                st.error("❌ Earth Engine export failed — cannot render comparison map.")
            else:
                # ensure nicer resolution and create PNG overlays
                p_sharp = ensure_sharp_tif(p_out, factor=1.6)
                q_sharp = ensure_sharp_tif(q_out, factor=1.6)
                try:
                    with rasterio.open(p_sharp) as pr:
                        arr_p = pr.read(1)
                        ndvi_array_to_png(arr_p, ndvi_past_png)
                        bounds = [[pr.bounds.bottom, pr.bounds.left], [pr.bounds.top, pr.bounds.right]]
                    with rasterio.open(q_sharp) as qr:
                        arr_q = qr.read(1)
                        ndvi_array_to_png(arr_q, ndvi_present_png)
                except Exception as e:
                    st.error(f"Could not read exported GeoTIFFs: {e}")
                    st.stop()

                # encode to base64 for embedding in HTML
                with open(ndvi_past_png, "rb") as f:
                    past_b64 = base64.b64encode(f.read()).decode("utf-8")
                with open(ndvi_present_png, "rb") as f:
                    pres_b64 = base64.b64encode(f.read()).decode("utf-8")

                # custom HTML slider (pure JS + CSS) — stable inside Streamlit component
                html_code = f"""
                <style>
                .compare-container {{
                    position: relative;
                    width: 100%;
                    max-width: 1100px;
                    height: 600px;
                    overflow: hidden;
                    border-radius: 8px;
                    background: #eee;
                }}
                .compare-img {{
                    position: absolute;
                    top: 0; left: 0;
                    width: 100%;
                    height: 100%;
                    object-fit: cover;
                    user-select: none;
                }}
                .compare-overlay {{
                    position: absolute;
                    top: 0; left: 0;
                    width: 50%;
                    height: 100%;
                    overflow: hidden;
                    transition: width 0.12s linear;
                }}
                .slider {{
                    position: absolute;
                    left: 0; top: 0;
                    width: 100%;
                    height: 100%;
                    background: transparent;
                    -webkit-appearance: none;
                }}
                .slider::-webkit-slider-thumb {{
                    -webkit-appearance: none;
                    width: 18px;
                    height: 48px;
                    background: rgba(255,255,255,0.95);
                    border: 2px solid rgba(0,0,0,0.6);
                    border-radius: 6px;
                    margin-top: 276px; /* center vertically for 600px height */
                    box-shadow: 0 4px 12px rgba(0,0,0,0.4);
                }}
                .label-left, .label-right {{
                    position: absolute;
                    top: 8px;
                    background: rgba(0,0,0,0.6);
                    color: #fff;
                    padding: 6px 8px;
                    border-radius: 4px;
                    font-size: 13px;
                }}
                .label-left {{ left: 8px; }}
                .label-right {{ right: 8px; }}
                </style>

                <div class="compare-container">
                    <img src="data:image/png;base64,{pres_b64}" class="compare-img" id="present_img">
                    <div class="compare-overlay" id="overlay">
                        <img src="data:image/png;base64,{past_b64}" class="compare-img" id="past_img">
                    </div>
                    <input id="slider" class="slider" type="range" min="0" max="100" value="50">
                    <div class="label-left">← Past NDVI</div>
                    <div class="label-right">Present NDVI →</div>
                </div>

                <script>
                const slider = document.getElementById('slider');
                const overlay = document.getElementById('overlay');
                slider.addEventListener('input', function(e) {{
                    overlay.style.width = this.value + '%';
                }});
                // small animation on load to show interaction
                let v = 50;
                const anim = setInterval(() => {{
                    v = v + 2;
                    if (v > 92) {{ clearInterval(anim); return; }}
                    slider.value = v;
                    overlay.style.width = v + '%';
                }}, 20);
                </script>
                """

                st.components.v1.html(html_code, height=640, scrolling=False)
                st.success("✅ NDVI slider map updated and rendered for this run!")

        # ---------------- Histogram ----------------
        st.markdown("### 📈 NDVI Distribution")
        try:
            fig = plt.figure(figsize=(8,3.5))
            plt.hist(arr_p.ravel()[~np.isnan(arr_p.ravel())], bins=40, alpha=0.5, label="Past")
            plt.hist(arr_q.ravel()[~np.isnan(arr_q.ravel())], bins=40, alpha=0.5, label="Present")
            plt.legend(); plt.xlabel("NDVI"); plt.ylabel("Pixel Count")
            st.pyplot(fig)
        except Exception:
            st.info("Histogram unavailable (no valid NDVI arrays).")

    except Exception as e_main:
        st.error(f"❌ Error: {e_main}")
        st.text(traceback.format_exc())

# Footer
st.markdown("---")
st.markdown("<div style='text-align:center; color:#00FFCC;'>⚙️ <b>Powered by Google Earth Engine & Streamlit</b> — Built with ❤️ by Team Neo Deforestation</div>", unsafe_allow_html=True)
