# =====================================================================
# SF Pet Care -- shared deployment configuration
#
# Sourced by gcloud_command.sh to set the values it deploys with:
#
#   source "$(dirname "$0")/config.sh"
#
# Project ID, bucket name and key path are read from api/.env, so a
# new deployer only fills in api/.env (copied from api/.env_template).
# Everything below that has a working default.
# =====================================================================

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Path to the API's .env file (copy api/.env_template to api/.env).
# Cloud Run loads it at deploy time to set GCP_PROJECT_ID,
# GCP_BUCKET_NAME, GCP_SERVICE_ACCOUNT_KEY, SODA_APP_TOKEN and
# CENSUS_API_KEY inside the API container.
ENV_FILE="$REPO_DIR/api/.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: ENV_FILE '$ENV_FILE' does not exist." \
       "Copy api/.env_template to api/.env and fill it in." >&2
  exit 1
fi

# Read KEY=value from the .env file (last match wins, quotes stripped).
# Prints nothing for a missing key; '|| true' keeps 'set -e' and
# 'pipefail' in gcloud_command.sh from exiting when grep finds no match.
_env_get() {
  { grep -E "^[[:space:]]*$1[[:space:]]*=" "$ENV_FILE" || true; } | tail -n 1 \
    | cut -d= -f2- | sed -E 's/^[[:space:]]*//; s/[[:space:]]*$//; s/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/'
}

# GCP project that hosts the Cloud Run services, buckets and scheduler.
PROJECT_ID="$(_env_get GCP_PROJECT_ID)"

# Bucket BASE name; the API appends "-dev" or "-prod" itself.
BUCKET_BASE="$(_env_get GCP_BUCKET_NAME)"

# Service account JSON key on your laptop (GCP_SERVICE_ACCOUNT_KEY).
# Uploaded to Secret Manager by gcloud_command.sh.
LOCAL_KEY_FILE="$(_env_get GCP_SERVICE_ACCOUNT_KEY)"

# Census API key, stored in Secret Manager rather than as a plain env
# var. Point CENSUS_API_KEY_FILE in api/.env at a file holding just the
# key; if it is unset, the CENSUS_API_KEY value in api/.env is used.
CENSUS_KEY_FILE="$(_env_get CENSUS_API_KEY_FILE)"
CENSUS_KEY_VALUE="$(_env_get CENSUS_API_KEY)"

# Region to deploy into. Cloud Run and Cloud Scheduler must match.
# Override per run with:  REGION=us-central1 bash gcloud_command.sh
REGION="${REGION:-us-west1}"

# Ports the containers listen on. These must match the EXPOSE/CMD
# ports in api/Dockerfile and streamlit/Dockerfile.
API_PORT="8080"
WEBAPP_PORT="8080"

# Seconds Cloud Run lets one API request run. POST /etl pulls all
# three sources, so this matches the scheduler's attempt deadline.
API_TIMEOUT="900"

# ---------------------------------------------------------------------
# Deployment resource names
# ---------------------------------------------------------------------

# Secret Manager secret holding the service account key, and where
# Cloud Run mounts it inside the API container.
GCP_KEY_SECRET=pet-gcp-key
SECRET_MOUNT_PATH=/secrets/gcp_key.json

# Secret Manager secret holding the Census key; Cloud Run exposes it
# to the API as the CENSUS_API_KEY env var.
CENSUS_KEY_SECRET=census-api-key

# Cloud Run service names.
API_SERVICE=pet-api-server
WEBAPP_SERVICE=pet-webapp

# Cloud Scheduler job that refreshes the data nightly (midnight PT).
SCHEDULER_JOB="refresh-pet-data"
SCHEDULE="0 0 * * *"

# API endpoint the scheduler POSTs to (POST /etl in api/main.py).
ETL_PATH=/etl


# =====================================================================
# Validation
# =====================================================================
for _cfg_var in PROJECT_ID BUCKET_BASE LOCAL_KEY_FILE REGION API_PORT \
                WEBAPP_PORT; do
  if [ -z "${!_cfg_var}" ]; then
    echo "ERROR: $_cfg_var is empty - check api/.env and config.sh" \
         "before running this script." >&2
    exit 1
  fi
done
unset _cfg_var

# The key files are only needed to deploy; teardown.sh sets
# SKIP_KEY_CHECKS=1 so it still works if they have been moved.
if [ -z "${SKIP_KEY_CHECKS:-}" ]; then
  if [ ! -f "$LOCAL_KEY_FILE" ]; then
    echo "ERROR: GCP_SERVICE_ACCOUNT_KEY '$LOCAL_KEY_FILE' in api/.env" \
         "does not exist. Use an absolute path to your key file." >&2
    exit 1
  fi

  if [ -n "$CENSUS_KEY_FILE" ]; then
    if [ ! -s "$CENSUS_KEY_FILE" ]; then
      echo "ERROR: CENSUS_API_KEY_FILE '$CENSUS_KEY_FILE' in api/.env" \
           "does not exist or is empty." >&2
      exit 1
    fi
  elif [ -z "$CENSUS_KEY_VALUE" ]; then
    echo "ERROR: set CENSUS_API_KEY_FILE (path to a file holding the key)" \
         "or CENSUS_API_KEY in api/.env." >&2
    exit 1
  fi
fi
