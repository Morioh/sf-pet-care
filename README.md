# sf-pet-care
An analysis of how friendly the neighborhoods in San Francisco are to pet owners.

## Team Members

| Name | GitHub ID | Role | Features |
| --- | --- | --- | --- |
| Ahmad Naggayev | ahmadavar  | Engineer | ETL integration |
| Zhidan Niu (Jessie) | zniu4 | Engineer | Community Survey Code, documentation, README |
| Maurice Onyonyi | morioh | Engineer | Business Locations |
| Jonah Robinson | jrobinson123 | Engineer | SF Neighborhood Analysis, GCP |
| Tom Sasser | tjsasser | Manager, Scrum Master, QA, Integration | Sprint, meetings, QA, integration, documentation, PR |
---

| Technology | URL |
| --- | --- |
| Git Repo | https://github.com/Morioh/sf-pet-care |
| GCP Project | https://console.cloud.google.com/welcome/new?authuser=1&project=sf-pet-care |

---

## Problem Statement

The purpose of this project is to see if different San Francisco neighborhoods have access to pet care services.  These services include veterinary clinics, grooming shops, and pet stores.  We compare service availability across the official neighborhoods in San Francisco.  Also included are  the smaller, unofficial neighborhoods within each major zone.  We use multiple data sets, including Geo Location, business and census data.  This information is useful for pet owners who want to know which neighborhoods have better access to various pet oriented services.

---

## Data Sources and Integration Goal

### Sources

Source:  Registered Business Locations — San Francisco  
URL:  https://data.sfgov.org/resource/g8m3-pdis.json
Method:  API
Contains:  Registered businesses in San Francisco including: business name, address, business information, dates, and geographic location
Update Frequency:  Daily
Access Requirements:  Public; no login or API key required

Source:  American Community Survey 2024 5 Year Estimates
URL:  https://api.census.gov/data/2024/acs/acs5
Method:  API
Contains:  Population and demographic information for small geographic areas
Update Frequency:  Daily
Access Requirements:  Requires API key

Source:  San Francisco Analysis — Neighborhoods
URL:  https://data.sf.gov/api/v3/views/j2bu-swwd/query.geojson?accessType=DOWNLOAD
Method:  File
Contains:  Names and geographic boundaries of San Francisco neighborhoods
Update Frequency:  Daily
Access Requirements:  Public, no key or login

---

### Integration Goal

This project is concerned with pet related services, including veterinary clinics, pet grooming and pet stores. The data collected by registered_businesses.py contains details and locations of 366 businesses in San Francisco.  The data collected by acs_data.py, which is Census data, provides popuation and demographic information.  Finally, the neighborhood files give access to neighborhood boundaries.  Combining this data will allow us to answer a variety of questions including locations, radius from any particular neighborhood.  The Census data will allow us to group and compare types of pet care services relative to the population in different neighborhoods.

---

You can review the FastAPI docstrings at: http://127.0.0.1:8000/docs

---

## Setup Instructions (Locally)

The project runs as two processes: a **FastAPI service** (`api/`) that collects the three sources, stores them in a GCS bucket, and serves them; and a **Streamlit dashboard** (`streamlit/`) that reads from the API. Even when running locally, the API reads and writes a real GCS bucket.

### Local prerequisites

- Python 3.11 or newer (the containers use 3.11) and git
- The [gcloud CLI](https://cloud.google.com/sdk/docs/install), logged in with `gcloud auth login`
- Access to a GCP project (the team project is `sf-pet-care`)
- A service account JSON key for that project, saved **outside** the repo
- A free Census API key from <https://api.census.gov/data/key_signup.html> (the key works only after the user clicks the activation link in the email)
- Optional: a Socrata app token from <https://data.sf.gov/profile/edit/developer_settings> to raise rate limits on the business data

### 1. Clone the repo and create a virtual environment

```bash
git clone https://github.com/Morioh/sf-pet-care.git
cd sf-pet-care
python3 -m venv .venv
source .venv/bin/activate
pip install -r api/requirements.txt -r streamlit/requirements.txt
```

### 2. Configure `api/.env`

```bash
cp api/.env_template api/.env
```

Then fill in `api/.env`:

| Variable | What to put there |
| --- | --- |
| `GCP_PROJECT_ID` | The GCP project ID, e.g. `sf-pet-care` |
| `GCP_BUCKET_NAME` | Bucket **base** name, e.g. `sf-pet-care-bucket`. The code appends `-dev` or `-prod` itself, so leave the suffix off. Bucket names are globally unique, so a user deploying to their own project must choose a new name. |
| `GCP_SERVICE_ACCOUNT_KEY` | Absolute path to the service account JSON key |
| `CENSUS_API_KEY` | The Census API key. Required to run the ETL locally. |
| `CENSUS_API_KEY_FILE` | Optional: path to a file holding only the Census key. Used only by the cloud deploy (see below). |
| `SODA_APP_TOKEN` | Optional Socrata token |

`api/.env` is git-ignored. It and the key files must never be committed.

For a user without a service account key yet, `api/.env_template` contains the `gcloud iam service-accounts keys create` command for creating one.

### 3. Make sure the buckets exist

The API uses `<GCP_BUCKET_NAME>-dev` by default and `<GCP_BUCKET_NAME>-prod` when called with `dev_mode=false`. On the team project they already exist. On a new project, the user can either run the cloud deploy (it creates them) or create them manually:

```bash
gcloud storage buckets create gs://<GCP_BUCKET_NAME>-dev --location=us-west1 --uniform-bucket-level-access
gcloud storage buckets create gs://<GCP_BUCKET_NAME>-prod --location=us-west1 --uniform-bucket-level-access
```

The service account behind the key also needs access to both buckets, either through a project role such as Editor or with `roles/storage.objectAdmin` on each bucket.

### 4. Run the API

```bash
cd api
fastapi dev main.py
```

The API starts at <http://127.0.0.1:8000> and reloads automatically when the code changes. Interactive docs are at <http://127.0.0.1:8000/docs>.

### 5. Load the data (ETL)

The read endpoints serve whatever the last ETL run saved in the bucket. To refresh it, with the API running:

```bash
curl -X POST http://127.0.0.1:8000/etl
```

Or run it once without starting the server, from `api/`:

```bash
python main.py
```

The ETL downloads all three sources, uploads them to the bucket, combines businesses with neighborhoods, and saves the result to `processed/pet_care_by_neighborhood.csv`. It returns a summary of row counts and where the result was saved.

> **Note:** The local API and the deployed API share the same `-dev` bucket, so a local ETL run also changes what the deployed dashboard shows.

### 6. Run the dashboard

In a second terminal:

```bash
source .venv/bin/activate
cd streamlit
streamlit run streamlit_app.py
```

The dashboard opens at <http://localhost:8501> and reads from `http://127.0.0.1:8000` by default. To point it at a different API, such as the deployed one, set `API_URL`:

```bash
API_URL=https://<api-service-url> streamlit run streamlit_app.py
```

### Using the API

| Method | Endpoint | Returns |
| --- | --- | --- |
| GET | `/neighborhoods` | SF neighborhood boundaries as GeoJSON (41 polygons) |
| GET | `/businesses` | Active pet-care businesses: veterinary, pet care and pet stores |
| GET | `/census` | Population and median household income per SF census tract (ACS 5-year) |
| GET | `/processed` | Pet-care business counts per neighborhood, by type, from the last ETL run |
| POST | `/etl` | Refreshes all sources, recombines them, and saves the result |

Every endpoint accepts `?dev_mode=false` to use the `-prod` bucket instead of `-dev`. Note that `-prod` stays empty until `POST /etl?dev_mode=false` has been run.

```bash
curl http://127.0.0.1:8000/processed
```

---

## Setup Instructions (Remotely)

The project deploys to Google Cloud as two public **Cloud Run** services, plus a **Cloud Scheduler** job that refreshes the data every night at midnight Pacific time. `gcloud_command.sh` does the whole deployment, `teardown.sh` removes it, and both read their settings from `config.sh` and `api/.env`.

### Remote prerequisites

- Everything in [Local prerequisites](#local-prerequisites) above, including a filled-in `api/.env`
- A GCP project with **billing enabled**, on which the user has the **Owner** role
- `bash` to run the scripts (macOS and Linux have it). Run them with `bash`, not `zsh` or `sh`.
- Docker is **not** needed. Cloud Build builds the containers in the cloud.

### 1. Store the Census key safely (recommended)

For the cloud deploy, the Census key is stored in **Secret Manager** rather than as a plain environment variable. Put the key alone in a file outside the repo and point `api/.env` at it:

```text
CENSUS_API_KEY_FILE=/absolute/path/to/census-api-key.txt
```

If `CENSUS_API_KEY_FILE` isn't set, the deploy uses the `CENSUS_API_KEY` value from `api/.env` instead. Either way, it ends up in Secret Manager.

### 2. Review `config.sh` (optional)

`config.sh` reads the project ID, bucket name and key paths from `api/.env`, so most people don't need to edit it. It also sets:

| Setting | Default |
| --- | --- |
| `REGION` | `us-west1`. It can be overridden per run: `REGION=us-central1 bash gcloud_command.sh` |
| `API_SERVICE` / `WEBAPP_SERVICE` | `pet-api-server` / `pet-webapp` |
| `SCHEDULER_JOB` / `SCHEDULE` | `refresh-pet-data` / `0 0 * * *` (midnight PT) |
| `API_TIMEOUT` | `900` seconds, so a full ETL run isn't cut off |

### 3. Deploy

From the repo root:

```bash
bash gcloud_command.sh
```

The script:

1. Enables the required APIs (Cloud Run, Cloud Build, Artifact Registry, Secret Manager, Cloud Scheduler, Cloud Storage), grants Cloud Build the access it needs, and creates the `-dev`/`-prod` buckets if they're missing.
2. Uploads the service account key and the Census key to Secret Manager.
3. Builds and deploys the API (`pet-api-server`), with the key mounted as a file and the Census key as an environment variable.
4. Builds and deploys the dashboard (`pet-webapp`), pointed at the API's URL.
5. Creates or updates the nightly Cloud Scheduler job that calls `POST /etl`.

It prints the API and dashboard URLs when it's done. The first deploy takes several minutes. On a brand-new project, the first build may report `PERMISSION_DENIED` while new permissions propagate; the script waits and retries automatically.

The script is safe to re-run: existing buckets, secrets and the scheduler job are reused or updated.

### 4. Load the data

The dashboard shows data from the last ETL run. To run one now instead of waiting for midnight:

```bash
gcloud scheduler jobs run refresh-pet-data --location=us-west1
```

### 5. Use the deployed service

- **Dashboard:** open the `pet-webapp` URL printed by the deploy.
- **API:** call the `pet-api-server` URL with the endpoints listed under [Using the API](#using-the-api), e.g. `curl https://<api-url>/processed`. Docs are at `https://<api-url>/docs`.

To look up the URLs later:

```bash
gcloud run services list --region us-west1
```

### Updating the deployment after code changes

Cloud Run runs the image built at deploy time, so code changes only go live after a redeploy. The deploy builds from the user's **local** files, not from GitHub.

- **Everything:** `bash gcloud_command.sh`
- **Dashboard only** (faster; keeps its existing settings):

  ```bash
  gcloud run deploy pet-webapp --source streamlit --region us-west1
  ```

- **API only:**

  ```bash
  gcloud run deploy pet-api-server --source api --region us-west1
  ```

Any new Python package must also be added to that service's `requirements.txt`, or the deployed container will fail to start.

### Tearing it down

```bash
bash teardown.sh
```

This removes the scheduler job, both Cloud Run services, both secrets, the built container images, and the uploaded source. It lists everything first and asks for confirmation; pass `--yes` to skip the prompt.

The data buckets are **kept** by default. To delete them and all their data as well:

```bash
bash teardown.sh --delete-buckets
```

The enabled Google Cloud services stay on; they cost nothing while unused. It's safe to re-run teardown after a partial teardown.

### Troubleshooting

| Symptom | Fix |
| --- | --- |
| `ERROR: ENV_FILE ... does not exist` | Create `api/.env` from `api/.env_template` |
| `ERROR: set CENSUS_API_KEY_FILE ... or CENSUS_API_KEY` | Add the Census key (see step 1) |
| `bad substitution` when running a script | Run it with `bash`, not `zsh` or `sh` |
| `PERMISSION_DENIED` on the first deploy, even after retries | Wait a few minutes and re-run `bash gcloud_command.sh` |
| Dashboard loads but shows no data | Run the ETL: `gcloud scheduler jobs run refresh-pet-data --location=us-west1` |
| `CENSUS_API_KEY is not set` when running locally | Local runs need `CENSUS_API_KEY` itself in `api/.env`, not just `CENSUS_API_KEY_FILE` |


---

## Repository Structure

```text
sf-pet-care/
├── README.md                  # overview, local + remote setup
├── LICENSE
├── .gitignore
├── requirements.txt           # local dev: just includes the two below
├── config.sh                  # shared deploy settings
├── gcloud_command.sh          # deploy to Cloud Run
├── teardown.sh                # remove the deployment
│
├── api/                       # Cloud Run service 1: FastAPI + ETL
│   ├── main.py                # endpoints + ETL (combine, save)
│   ├── collectors/
│   │   ├── __init__.py
│   │   ├── acs_data.py              # Census ACS
│   │   ├── neighborhood.py          # SF neighborhoods
│   │   └── registered_businesses.py # SF businesses
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── .gcloudignore
│   └── .env_template
│
├── streamlit/                 # Cloud Run service 2: dashboard
│   ├── streamlit_app.py
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── .gcloudignore
│   └── .env_template
│
└── docs/
    ├── etl-design.md
    ├── sf-pet-care.drawio     # system diagram (editable source)
    ├── sf-pet-care.jpg        # system diagram (rendered)
    ├── report.pdf             # export of report.pages
    └── work-contract.pdf
```
