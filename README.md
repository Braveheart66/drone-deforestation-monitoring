# 🌲 Drone-Based AI System for Monitoring Deforestation Patterns

### 🛰️ Overview
This project presents a **Drone-Based AI System for Monitoring Deforestation Patterns** — an integrated platform combining **satellite NDVI analytics** from **Google Earth Engine (GEE)** with **high-resolution NDVI from drone imagery**.  
The system visualizes vegetation changes over time and alerts users about deforestation via **WhatsApp notifications (Twilio API)**.

---

### 🚀 Features
- 🌍 Real-time **NDVI analysis** from Earth Engine  
- 🚁 Drone-based NDVI computation using **RED** and **NIR GeoTIFF bands**  
- 🗺️ **Interactive NDVI comparison slider** (Past ↔ Present)  
- 📈 NDVI statistics and histograms  
- ⚠️ Automated **WhatsApp alerts** for vegetation loss  
- 🧠 Persistent outputs and robust visualization fallback (Streamlit dashboard)  

---

### 🧩 Project Workflow
1. **Select AOI** – Upload a GeoJSON or use the demo AOI (Lucknow/Gomti belt)  
2. **Fetch Satellite Data** – Retrieve and compute NDVI from Earth Engine  
3. **Drone NDVI Analysis** – Upload RED & NIR bands to compute NDVI heatmap  
4. **Change Detection** – Compare historical vs. present NDVI  
5. **Visualization** – Interactive slider map, heatmaps, and area stats  
6. **Notification** – WhatsApp alerts via Twilio when deforestation detected  

---

### 🧠 Tech Stack
| Layer | Tools |
|-------|-------|
| **Frontend / Dashboard** | Streamlit, Folium, HTML/CSS/JS |
| **Backend / Processing** | Python, Rasterio, Matplotlib, NumPy |
| **Satellite Data** | Google Earth Engine (Sentinel-2, Landsat) |
| **Drone Data** | RED & NIR GeoTIFF inputs |
| **Alerts** | Twilio WhatsApp API |
| **Visualization** | NDVI slider, heatmaps, metrics cards |

