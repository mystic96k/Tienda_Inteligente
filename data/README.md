# data/

Local folder for the hackathon CSVs when you run **without Docker**. It is git-ignored: the dataset is synthetic,
for the Road2Tech hackathon only, and must not be redistributed.

* Docker / Cloud Run: nothing to do. The API downloads the CSVs automatically (`AUTO_DOWNLOAD=true`).
* Without Docker: `python ../scripts/fetch_dataset.py` (or just start the API; it downloads on first run).

Source: https://github.com/ams-innovation/dataset-hacktahton-road2tech
