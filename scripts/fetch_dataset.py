"""Download the 14 hackathon CSVs into ./data (works on Windows, macOS and Linux). Usage: python scripts/fetch_dataset.py"""
import os
import sys
import urllib.request

BASE = "https://raw.githubusercontent.com/ams-innovation/dataset-hacktahton-road2tech/master"
FILES = ["sites", "stores", "store_closures", "suppliers", "products", "product_barcodes", "calendar", "promotions",
         "prices", "weather_daily", "sales_daily", "inventory_daily", "purchase_orders", "store_daily_kpis"]
out = os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(out, exist_ok=True)
for f in FILES:
    dst = os.path.join(out, f + ".csv")
    if os.path.exists(dst):
        print("ok      ", f)
        continue
    print("download", f, flush=True)
    urllib.request.urlretrieve(f"{BASE}/{f}.csv", dst)
print("done ->", os.path.abspath(out))
sys.exit(0)
