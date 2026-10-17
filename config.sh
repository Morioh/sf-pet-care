# =====================================================================
# HW3 shared configuration -- FILL IN THE TODOs
#
# This file is SOURCED by both gcloud_command.sh and test_hw3.sh 
# to set environment variables:
#
#   source "$(dirname "$0")/config.sh"
#
# so you fill these values in ONCE to run the .sh files
# =====================================================================

# TODO 1: your GCP_PROJECT_ID.
PROJECT_ID="sf-pet-care"

# TODO 2: the region to deploy into, e.g. "us-west1". 
# Cloud Run and Cloud Scheduler must be in the same region.
REGION="us-west1"

# TODO 3: absolute path to your .env file. 
# Cloud Run reads this at
# deploy time to set GCP_PROJECT_ID, VERTEX_AI_PROJECT_ID,
# SEARCH_ENGINE_ID, GCP_BUCKET_NAME and GCP_SERVICE_ACCOUNT_KEY 
# inside the containers.
ENV_FILE="./.env"

# TODO 4: absolute path to your service account JSON key on your laptop
# (the file GCP_SERVICE_ACCOUNT_KEY points at for local development).
#LOCAL_KEY_FILE="/Users/jonahrobinson/.ssh/dsasi692-sec002-c208e4b03d03.json"
LOCAL_KEY_FILE="$HOME/.ssh/sf-pet-care.json"

# TODO 5: the ports your two Dockerfiles EXPOSE. 
# These must match your Dockerfiles exactly.
API_PORT="8000"
WEBAPP_PORT="8501"

# ---------------------------------------------------------------------
# Provided -- no need to change anything below this line.
# ---------------------------------------------------------------------

# Secret Manager holds the key, and we need to mount it.
GCP_KEY_SECRET=pet-gcp-key
SECRET_MOUNT_PATH=/secrets/gcp_key.json

# Cloud Run service names.
API_SERVICE=pet-api-server
WEBAPP_SERVICE=pet-webapp

# Cloud Scheduler job: name, schedule, and the request body.
SCHEDULER_JOB="refresh-pet-data"
SCHEDULE="0 0 * * *"

# The API endpoint, and the GCS prefix.
ETL_PATH=/etl


# =====================================================================
# Validation 
# =====================================================================
# for _hw3_var in PROJECT_ID REGION ENV_FILE LOCAL_KEY_FILE API_PORT \
#                 WEBAPP_PORT; do
#   if [ -z "${!_hw3_var}" ]; then
#     echo "ERROR: $_hw3_var is empty - fill in TODOs in config.sh" \
#          "before running this script." >&2
#     exit 1
#   fi
# done
unset _hw3_var

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: ENV_FILE '$ENV_FILE' does not exist." >&2
  exit 1
fi
if [ ! -f "$LOCAL_KEY_FILE" ]; then
  echo "ERROR: LOCAL_KEY_FILE '$LOCAL_KEY_FILE' does not exist." >&2
  exit 1
fi
