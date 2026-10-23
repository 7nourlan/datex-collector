# datex-collector

Collects live road weather and traffic counter data (40 km around Tartu) from the Estonian Transport Administration DATEX II service every ~10 minutes with GitHub Actions. The API has no history, so this builds it.

Output: `data/sites.csv`, `data/weather/YYYY-MM-DD.csv`, `data/traffic/YYYY-MM-DD.csv`

Setup: add the API key as a repository secret named `DATEX_API_KEY` (Settings → Secrets and variables → Actions).

Data: Transpordiamet / Tark Tee DATEX II.
