# streamlit_app.py — Streamlit-Cloud-friendly (no rasterio)
import streamlit as st
import os, json, datetime as dt, tempfile, traceback, requests, threading, time, base64
import numpy as np
import matplotlib.pyplot as plt
from PIL import Image
from streamlit_lottie import st_lottie
import folium
from streamlit_folium import st_folium
from twilio.rest import Client
from dotenv import load_dotenv

# -------------------- Config / persistent dirs --------------------
PERSISTENT_DIR = os.path.join(os.getcwd(), "persistent_maps")
ASSETS_DIR = os.path.join(os.getcwd(), "assets")
os.makedirs(PERSISTENT_DIR, exist_ok=True)
os.makedirs(ASSETS_DIR, exist_ok=True)

# -------------------- Custom imports (your modules) --------------------
# IMPORTANT: These must exist in repo. compute_drone_ndvi may use rasterio locally.
from ndvi_analysis import run_analysis
try:
    from drone_utils import compute_drone_ndvi, ndvi_stats_from_drone
    DRONE_AVAILABLE = True
except Exception as e:
    print("⚠️ Drone NDVI disabled (rasterio not available):", e)
    DRONE_AVAILABLE = False


# -------------------- Session state --------------------
for key, default in {
    "analysis_done": False,
    "result": None,
    "drone_stats": None,
    "alert_triggered": False,
    "last_map_html": None
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# -------------------- Helpers --------------------
def safe_load_json_utf8(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def ndvi_array_to_png_bytes(ndvi_array, cmap_name="RdYlGn"):
    """Return PNG bytes from a numpy NDVI array (no file IO)."""
    arr = np.array(ndvi_array, dtype=float)
    arr = np.where(np.isfinite(arr), arr, np.nan)
    arr = np.clip(arr, -0.5, 1.0)
    valid = ~np.isnan(arr)
    if valid.any():
        vmin, vmax = np.nanmin(arr[valid]), np.nanmax(arr[valid])
    else:
        vmin, vmax = -0.5, 1.0
    norm = (arr - vmin) / (vmax - vmin + 1e-9)
    cmap = plt.get_cmap(cmap_name)
    rgba = cmap(norm, bytes=True)
    rgb = rgba[..., :3]
    im = Image.fromarray(rgb)
    buf = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    im.save(buf.name, format="PNG")
    with open(buf.name, "rb") as fh:
        data = fh.read()
    try:
        os.unlink(buf.name)
    except Exception:
        pass
    return data

def export_ee_image_to_png_via_thumb(ee_image, region_geojson, out_path, dimensions=1024, viz_params=None, timeout=120):
    """
    Use Earth Engine getThumbURL to request a PNG thumbnail of ee_image.
    Saves PNG bytes to out_path. Returns out_path or None on failure.
    - ee_image: ee.Image (object from your ndvi_analysis result)
    - region_geojson: geometry (GeoJSON) or bounding polygon list
    - viz_params: dict with visualization parameters (min/max/palette/bands)
    """
    try:
        # Build default viz params if not provided
        viz = dict(min=-0.5, max=1.0, dimensions=dimensions, region=region_geojson, format="png")
        if isinstance(viz_params, dict):
            viz.update(viz_params)
        url = ee_image.getThumbURL(viz)
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        with open(out_path, "wb") as f:
            f.write(r.content)
        return out_path
    except Exception as e:
        print("export_ee_image_to_png_via_thumb failed:", e)
        return None

def geojson_to_region(geo):
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

# -------------------- Twilio & Siren helpers --------------------
@st.cache_resource
def load_siren_audio():
    siren_path = os.path.join(ASSETS_DIR, "warning_siren.mp3")
    try:
        with open(siren_path, "rb") as f:
            return f.read()
    except Exception:
        return None

def send_whatsapp_twilio_async(phone, body):
    def worker(p, b):
        try:
            load_dotenv()
            sid = os.getenv("TWILIO_SID")
            auth = os.getenv("TWILIO_AUTH")
            if not sid or not auth:
                print("❌ Twilio credentials missing; skipping.")
                return
            client = Client(sid, auth)
            from_ = "whatsapp:+14155238886"
            phone_clean = p.strip().replace(" ", "")
            if not phone_clean.startswith("+"):
                phone_clean = "+91" + phone_clean
            client.messages.create(from_=from_, to=f"whatsapp:{phone_clean}", body=b)
            print("✅ WhatsApp sent to", phone_clean)
        except Exception as e:
            print("❌ Twilio error:", e)
    threading.Thread(target=worker, args=(phone, body), daemon=True).start()

# -------------------- UI header --------------------
st.set_page_config(layout="wide", page_title="🌲 Drone-AI Deforestation Monitor", page_icon="🌍")
st.title("🌲 Drone-AI Deforestation Monitor")
st.caption("Real-time NDVI Analysis • Earth Engine • Drone NDVI • WhatsApp Alerts")
st.divider()

# Lottie animation (optional)
lottie_path = os.path.join(ASSETS_DIR, "drone_anim.json")
drone_anim = safe_load_json_utf8(lottie_path)
if drone_anim:
    try:
        st_lottie(drone_anim, height=140, key="drone_anim")
    except Exception:
        st.info("Drone animation could not play.")

# -------------------- Sidebar inputs --------------------
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
    if include_drone and DRONE_AVAILABLE and red_band and nir_band:
        red_band = st.file_uploader("RED band (GeoTIFF)", type=["tif","tiff"], key="drone_red")
        nir_band = st.file_uploader("NIR band (GeoTIFF)", type=["tif","tiff"], key="drone_nir")
    # existing drone processing code...
    elif include_drone and not DRONE_AVAILABLE:
        st.warning("Drone NDVI feature is unavailable on Streamlit Cloud (rasterio missing). Run locally to use this feature.")


    st.header("📲 Alerts")
    phone = st.text_input("Recipient phone (e.g. +919876543210)")
    msg_enabled = st.checkbox("Enable WhatsApp alerts (Twilio)")

# -------------------- Run NDVI Analysis --------------------
if st.button("🚀 Run NDVI Analysis"):
    try:
        with st.spinner("Running Earth Engine NDVI analysis..."):
            result = run_analysis(aoi_geojson, str(past_start), str(past_end),
                                  str(pres_start), str(pres_end), ndvi_thresh)

        if not result or not result.get("stats"):
            st.error("Analysis failed — check AOI/dates/ and EE auth.")
            st.stop()

        st.session_state.result = result
        stats = result["stats"]
        past_area = float(stats.get("area_past_ha") or 0.0)
        present_area = float(stats.get("area_present_ha") or 0.0)
        delta_ha = present_area - past_area
        pct_change = ((delta_ha / past_area) * 100.0) if past_area else (100.0 if delta_ha>0 else 0.0)

        # Summary metrics
        st.markdown("### 🌿 NDVI / Tree Cover Summary")
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Past Cover (ha)", f"{past_area:.2f}")
        c2.metric("Present Cover (ha)", f"{present_area:.2f}")
        if delta_ha < 0:
            c3.metric("Change (ha)", f"{abs(delta_ha):.2f}", "- (Loss)")
            c4.metric("Change (%)", f"{abs(pct_change):.2f}%", "Loss", delta_color="inverse")
            st.warning(f"🚨 Deforestation detected: ↓ {abs(pct_change):.2f}% ({abs(delta_ha):.2f} ha loss)")
            siren_bytes = load_siren_audio()
            if siren_bytes:
                try:
                    st.audio(siren_bytes, format="audio/mp3")
                except Exception:
                    st.info("Browser may block autoplay.")
            if msg_enabled and phone:
                send_whatsapp_twilio_async(phone, f"🚨 ALERT: {abs(pct_change):.2f}% deforestation detected!")
        else:
            c3.metric("Change (ha)", f"{abs(delta_ha):.2f}", "+ (Gain)")
            c4.metric("Change (%)", f"{abs(pct_change):.2f}%", "Gain")
            st.success(f"🌱 Net gain: {abs(pct_change):.2f}% ({abs(delta_ha):.2f} ha)")

        # ---------------- Drone NDVI (uploaded) ----------------
        if include_drone and red_band and nir_band:
            st.markdown("### 🚁 Drone NDVI (uploaded)")
            # Save uploaded files to persistent dir (so they don't vanish)
            ts = int(time.time())
            red_path = os.path.join(PERSISTENT_DIR, f"drone_red_{ts}.tif")
            nir_path = os.path.join(PERSISTENT_DIR, f"drone_nir_{ts}.tif")
            with open(red_path, "wb") as f: f.write(red_band.read())
            with open(nir_path, "wb") as f: f.write(nir_band.read())

            # compute_drone_ndvi may rely on rasterio — it will work locally if you have rasterio installed.
            try:
                ndvi_arr, meta = compute_drone_ndvi(nir_path, red_path)
                drone_stats = ndvi_stats_from_drone(ndvi_arr, ndvi_thresh)
                st.session_state.drone_stats = drone_stats
                st.json(drone_stats)
                st.success(f"Drone Tree Cover: {drone_stats['percent_tree_cover']:.2f}%")

                # Show drone NDVI heatmap
                fig, ax = plt.subplots(figsize=(6,4))
                im = ax.imshow(ndvi_arr, cmap="RdYlGn", vmin=-0.2, vmax=1.0)
                plt.colorbar(im, ax=ax, label="NDVI")
                ax.set_title("Drone NDVI")
                st.pyplot(fig)
            except Exception as e:
                st.warning("Drone NDVI processing failed (rasterio may be required in your environment): " + str(e))

        # ---------------- NDVI Comparison Map (Past <-> Present) using thumbnail PNGs ----------------
        st.markdown("## 🗺️ NDVI Comparison Map (Past ↔ Present)")

        # Build filenames
        run_id = int(time.time())
        past_png = os.path.join(PERSISTENT_DIR, f"past_{run_id}.png")
        pres_png = os.path.join(PERSISTENT_DIR, f"present_{run_id}.png")

        # Get EE image objects from result (your run_analysis must return ee.Image objects under map_layers)
        ndvi_past_img = result.get("map_layers", {}).get("past")
        ndvi_present_img = result.get("map_layers", {}).get("present")

        if not ndvi_past_img or not ndvi_present_img:
            st.warning("No NDVI image layers returned from Earth Engine (map_layers missing).")
        else:
            # Determine region for thumbnail
            try:
                region_geojson = geojson_to_region(aoi_geojson)
            except Exception:
                region_geojson = None
            if region_geojson is None:
                # fallback: try to get centroid (EE call) — may fail if no EE auth
                try:
                    centroid = ndvi_present_img.geometry().centroid().getInfo()
                    lon, lat = centroid["coordinates"]
                    region_geojson = {"type":"Polygon","coordinates":[[[lon-0.01, lat-0.01],[lon+0.01, lat-0.01],[lon+0.01, lat+0.01],[lon-0.01, lat+0.01],[lon-0.01, lat-0.01]]]}
                except Exception:
                    region_geojson = None

            if region_geojson is None:
                st.warning("Could not determine export region. Skipping NDVI comparison map.")
            else:
                st.info("📡 Exporting NDVI thumbnails from Earth Engine...")
                # use getThumbURL to fetch PNG thumbnails (no rasterio needed)
                p_ok = export_ee_image_to_png_via_thumb(ndvi_past_img, region_geojson, past_png, dimensions=1024)
                q_ok = export_ee_image_to_png_via_thumb(ndvi_present_img, region_geojson, pres_png, dimensions=1024)

                if not p_ok or not q_ok:
                    st.warning("EE thumbnail export failed — check Earth Engine authentication and AOI.")
                else:
                    # Encode to base64 for slider embed
                    with open(past_png, "rb") as f:
                        past_b64 = base64.b64encode(f.read()).decode("utf-8")
                    with open(pres_png, "rb") as f:
                        pres_b64 = base64.b64encode(f.read()).decode("utf-8")

                    # Build simple HTML slider widget (fast, portable)
                    html_code = f"""
                    <style>
                    .compare-container {{
                        position: relative;
                        width: 100%;
                        max-width: 1000px;
                        height: 600px;
                        overflow: hidden;
                        border-radius: 8px;
                    }}
                    .compare-img {{
                        position: absolute; top:0; left:0; width:100%; height:100%; object-fit:cover;
                    }}
                    .compare-overlay {{
                        position:absolute; top:0; left:0; width:50%; height:100%; overflow:hidden;
                        transition: width 0.12s linear;
                    }}
                    .slider {{
                        position:absolute; width:100%; height:100%; top:0; left:0; background:none; outline:none; z-index:30; cursor:ew-resize;
                    }}
                    .slider::-webkit-slider-thumb {{
                        -webkit-appearance: none; appearance:none; width:14px; height:40px; background:rgba(255,255,255,0.9); border:2px solid #333; border-radius:4px;
                    }}
                    .label-left, .label-right {{
                        position:absolute; top:12px; background: rgba(0,0,0,0.6); color:#fff; padding:6px 10px; border-radius:4px; font-size:13px;
                    }}
                    .label-left {{ left:12px; }}
                    .label-right {{ right:12px; }}
                    </style>

                    <div class="compare-container">
                        <img src="data:image/png;base64,{pres_b64}" class="compare-img" id="present-img">
                        <div class="compare-overlay" id="overlay">
                            <img src="data:image/png;base64,{past_b64}" class="compare-img" id="past-img">
                        </div>
                        <input type="range" min="0" max="100" value="50" class="slider" id="sliderRange">
                        <div class="label-left">← Past NDVI</div>
                        <div class="label-right">Present NDVI →</div>
                    </div>

                    <script>
                    const slider = document.getElementById("sliderRange");
                    const overlay = document.getElementById("overlay");
                    slider.oninput = function() {{
                        overlay.style.width = this.value + "%";
                    }};
                    // small auto-animate for presentation
                    let v=50; let t=setInterval(()=>{{ v+=2; if(v>70){{ clearInterval(t); return; }} slider.value=v; overlay.style.width=v+"%"; }},30);
                    </script>
                    """
                    st.components.v1.html(html_code, height=640)
                    st.success("✅ NDVI slider rendered.")

        # ---------------- Histogram (small) ----------------
        st.markdown("### 📈 NDVI Distribution (Quick view)")
        try:
            # If we have arrays (drone or EE) try to show basic histogram; else skip
            # We saved arrays earlier? If not, histogram may be skipped.
            if 'arr_p' in locals() and 'arr_q' in locals():
                fig = plt.figure(figsize=(8,3))
                plt.hist(arr_p.ravel()[~np.isnan(arr_p.ravel())], bins=40, alpha=0.5, label="Past")
                plt.hist(arr_q.ravel()[~np.isnan(arr_q.ravel())], bins=40, alpha=0.5, label="Present")
                plt.legend(); plt.xlabel("NDVI"); plt.ylabel("Pixel Count")
                st.pyplot(fig)
            else:
                st.info("Histogram skipped (no arrays available in this run).")
        except Exception as e:
            st.info("Histogram unavailable: " + str(e))

    except Exception as e_main:
        st.error("❌ Error during analysis: " + str(e_main))
        st.text(traceback.format_exc())

# -------------------- Persist & embed last map if available --------------------
if st.session_state.get("last_map_html"):
    st.markdown("### 🗺️ Previously Generated NDVI Map")
    try:
        with open(st.session_state.last_map_html, "r", encoding="utf-8") as f:
            st.components.v1.html(f.read(), height=600, scrolling=True)
    except Exception:
        st.markdown(f"[Open last map externally]({st.session_state.last_map_html})")

# -------------------- Footer --------------------
st.markdown("---")
st.markdown("<div style='text-align:center; color:#00FFCC;'>⚙️ <b>Powered by Google Earth Engine & Streamlit</b> — Built with ❤️ by Team Neo Deforestation</div>", unsafe_allow_html=True)
