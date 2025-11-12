# ndvi_analysis.py
import ee
import folium
import streamlit as st
from ee_utils import init_ee, get_median_ndvi, compute_change_stats

def _parse_aoi(aoi_geojson):
    """Return a clean GeoJSON geometry dict suitable for ee.Geometry and folium.GeoJson."""
    if not aoi_geojson:
        raise ValueError("Empty AOI")
    if isinstance(aoi_geojson, dict):
        t = aoi_geojson.get("type")
        if t == "FeatureCollection":
            features = aoi_geojson.get("features", [])
            if not features:
                raise ValueError("FeatureCollection contains no features")
            geom = features[0].get("geometry") or features[0]
        elif t == "Feature":
            geom = aoi_geojson.get("geometry")
        elif t in ("Polygon", "MultiPolygon"):
            geom = aoi_geojson
        else:
            # try to find geometry key or fallback
            geom = aoi_geojson.get("geometry", aoi_geojson)
        if not geom:
            raise ValueError("Could not extract geometry from AOI")
        return geom
    else:
        # assume it's already an ee.Geometry or similar - let caller handle
        return aoi_geojson

def _safe_get_float(obj):
    """Try to convert an ee.Number or other to float, fallback to None."""
    try:
        # If obj is ee.ComputedObject or ee.Number etc.
        if hasattr(obj, "getInfo"):
            return float(obj.getInfo())
        else:
            return float(obj)
    except Exception:
        try:
            # try ee.Number conversion
            return float(ee.Number(obj).getInfo())
        except Exception:
            return None

def run_analysis(aoi_geojson, past_start, past_end, present_start, present_end, ndvi_thresh=0.4):
    """
    Returns dict with keys:
      - map: folium.Map (prebuilt)
      - centroid: [lon, lat]
      - map_layers: {'past': ee.Image, 'present': ee.Image}
      - stats: {'area_past_ha', 'area_present_ha', 'loss_ha', 'percent_loss'} (floats or None)
    """
    print("DEBUG: Starting run_analysis()")
    try:
        init_ee()
        print("✅ EE initialized")
    except Exception as e:
        st.error(f"Earth Engine init failed: {e}")
        print("DEBUG ERROR:", e)
        return {"map": None, "centroid": None, "map_layers": {}, "stats": {}}

    # Parse AOI robustly
    try:
        parsed_geom = _parse_aoi(aoi_geojson)
        aoi = ee.Geometry(parsed_geom) if not isinstance(parsed_geom, ee.Geometry) else parsed_geom
    except Exception as e:
        st.error(f"Invalid AOI: {e}")
        print("DEBUG ERROR:", e)
        return {"map": None, "centroid": None, "map_layers": {}, "stats": {}}

    # NDVI composites
    try:
        print("DEBUG: Getting NDVI composites...")
        ndvi_past = get_median_ndvi(aoi, past_start, past_end)
        ndvi_present = get_median_ndvi(aoi, present_start, present_end)
        print("✅ NDVI computed")
    except Exception as e:
        st.error(f"NDVI composite error: {e}")
        print("DEBUG ERROR:", e)
        return {"map": None, "centroid": None, "map_layers": {}, "stats": {}}

    # Compute stats (EE operations)
    try:
        stats_ee = compute_change_stats(ndvi_past, ndvi_present, aoi, ndvi_thresh)
    except Exception as e:
        st.error(f"Change stats error: {e}")
        print("DEBUG ERROR:", e)
        return {"map": None, "centroid": None, "map_layers": {}, "stats": {}}

    # Try to materialize numeric stats safely
    try:
        area_past = _safe_get_float(stats_ee.get('area_past_ha') if isinstance(stats_ee, dict) else stats_ee['area_past_ha'])
        area_present = _safe_get_float(stats_ee.get('area_present_ha') if isinstance(stats_ee, dict) else stats_ee['area_present_ha'])
        loss_ha = _safe_get_float(stats_ee.get('loss_ha') if isinstance(stats_ee, dict) else stats_ee['loss_ha'])
        percent_loss = _safe_get_float(stats_ee.get('percent_loss') if isinstance(stats_ee, dict) else stats_ee['percent_loss'])
    except Exception as e:
        print("DEBUG WARNING: could not convert stats to floats:", e)
        area_past = area_present = loss_ha = percent_loss = None

    # Prepare map tiles
    try:
        ndvi_vis = {'min': -0.2, 'max': 1.0, 'palette': ['blue','white','green']}
        past_mapid = ee.Image(ndvi_past).getMapId(ndvi_vis)
        present_mapid = ee.Image(ndvi_present).getMapId(ndvi_vis)
        centroid = aoi.centroid().coordinates().getInfo()  # [lon, lat]
        m = folium.Map(location=[centroid[1], centroid[0]], zoom_start=12)

        folium.TileLayer(tiles=past_mapid['tile_fetcher'].url_format, name=f"NDVI Past ({past_start}→{past_end})", attr="EE", overlay=True, control=True).add_to(m)
        folium.TileLayer(tiles=present_mapid['tile_fetcher'].url_format, name=f"NDVI Present ({present_start}→{present_end})", attr="EE", overlay=True, control=True).add_to(m)

        # safe AOI render (only if parsed_geom is dict-like)
        try:
            if isinstance(parsed_geom, dict):
                folium.GeoJson(parsed_geom, name="AOI", style_function=lambda x: {'color':'red','fill':False,'weight':2}).add_to(m)
        except Exception as e:
            print("DEBUG: AOI overlay skipped:", e)

        folium.LayerControl(collapsed=False).add_to(m)
    except Exception as e:
        st.error(f"Map build error: {e}")
        print("DEBUG ERROR:", e)
        return {"map": None, "centroid": None, "map_layers": {}, "stats": {}}

    # Final results
    stats = {
        'area_past_ha': area_past,
        'area_present_ha': area_present,
        'loss_ha': loss_ha,
        'percent_loss': percent_loss
    }
    return {
        'map': m,
        'centroid': centroid,
        'map_layers': {'past': ndvi_past, 'present': ndvi_present},
        'stats': stats
    }
