# ee_utils.py
import ee
import datetime

# Initialize Earth Engine. Caller should have already authenticated.
def init_ee():
    try:
        ee.Initialize()
    except Exception:
        ee.Authenticate()
        ee.Initialize()

def sentinel_collection(aoi, start_date, end_date):
    col = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
           .filterBounds(aoi)
           .filterDate(start_date, end_date)
           .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 40)))

    def select_common(img):
        available = img.bandNames()
        common_bands = ee.List([
            'B1','B2','B3','B4','B5','B6','B7','B8',
            'B8A','B9','B11','B12','AOT','WVP','SCL'
        ])

        def keep_if_present(band):
            band = ee.String(band)
            return ee.Algorithms.If(available.contains(band), band, None)

        filtered = common_bands.map(keep_if_present).removeAll([None])
        return img.select(filtered)

    col = col.map(select_common)
    return col



def mask_s2_clouds(image):
    """
    Cloud mask for Sentinel-2 Surface Reflectance.
    Handles both QA60 (old) and SCL/MSK_CLDPRB (new) versions.
    """
    band_names = image.bandNames()

    # If the QA60 band exists (old method)
    def mask_qa60(img):
        qa = img.select('QA60')
        cloudBitMask = 1 << 10
        cirrusBitMask = 1 << 11
        mask = qa.bitwiseAnd(cloudBitMask).eq(0).And(qa.bitwiseAnd(cirrusBitMask).eq(0))
        return img.updateMask(mask).copyProperties(img, img.propertyNames())

    # If using newer mask bands (SCL / MSK_CLDPRB)
    def mask_modern(img):
        scl = img.select('SCL')
        mask = scl.neq(3).And(scl.neq(8))  # remove clouds/shadows
        return img.updateMask(mask).copyProperties(img, img.propertyNames())

    # Auto-choose
    return ee.Algorithms.If(band_names.contains('QA60'), mask_qa60(image), mask_modern(image))

import ee

def mask_s2_clouds_modern(image):
    """Modern Sentinel-2 SR cloud mask."""
    cloud_prob = image.select('MSK_CLDPRB')
    snow_prob = image.select('MSK_SNWPRB')
    mask = cloud_prob.lt(40).And(snow_prob.lt(20))
    return image.updateMask(mask).divide(10000)

def get_median_ndvi(aoi, start_date, end_date):
    """Compute median NDVI using Sentinel-2 SR or fallback to Landsat-8."""
    # Try Sentinel-2 SR first
    try:
        s2 = (
            ee.ImageCollection("COPERNICUS/S2_SR")
            .filterBounds(aoi)
            .filterDate(start_date, end_date)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 60))
            .map(mask_s2_clouds_modern)
        )
        ndvi = s2.median().normalizedDifference(['B8', 'B4']).rename('NDVI')
        info = ndvi.reduceRegion(reducer=ee.Reducer.mean(), geometry=aoi, scale=30, maxPixels=1e9).getInfo()
        if info and "NDVI" in info:
            return ndvi.clip(aoi)
        else:
            raise Exception("Sentinel NDVI empty, falling back to Landsat-8.")
    except Exception as e:
        print("⚠️ Sentinel NDVI failed, using Landsat 8:", e)
        l8 = (
            ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
            .filterBounds(aoi)
            .filterDate(start_date, end_date)
            .map(lambda img: img.updateMask(img.select('QA_PIXEL').bitwiseAnd(1 << 3).eq(0)))  # Cloud mask
        )
        ndvi = l8.median().normalizedDifference(['SR_B5', 'SR_B4']).rename('NDVI')
        return ndvi.clip(aoi)

def area_of_ndvi_threshold(ndvi_image, threshold, aoi):
    """
    Compute area (hectares) within AOI where NDVI >= threshold.
    Uses ee.Image.pixelArea() (in m^2).
    Returns an ee.Number (hectares).
    """
    mask = ndvi_image.gte(threshold)
    pixelArea = ee.Image.pixelArea().updateMask(mask)
    # Sum area within AOI
    area_m2 = pixelArea.reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=aoi,
        scale=10,      # Sentinel-2 native ~10m
        maxPixels=1e13
    ).get('area')
    # convert m2 to hectares (1 ha = 10000 m2)
    area_ha = ee.Number(area_m2).divide(10000)
    return area_ha

def compute_change_stats(ndvi_past, ndvi_present, aoi, threshold):
    """
    Compute hectares in past & present, absolute loss, percent change, and rate per year.
    Returns ee.Dictionary-like structure (but here we'll return Python dicts by evaluating).
    """
    area_past = area_of_ndvi_threshold(ndvi_past, threshold, aoi)
    area_present = area_of_ndvi_threshold(ndvi_present, threshold, aoi)
    # convert to ee.Number operations
    loss_ha = ee.Number(area_past).subtract(area_present)
    percent_change = ee.Algorithms.If(ee.Number(area_past).gt(0),
                                      loss_ha.divide(area_past).multiply(100),
                                      ee.Number(0))
    return {
        'area_past_ha': area_past,
        'area_present_ha': area_present,
        'loss_ha': loss_ha,
        'percent_loss': percent_change
    }
