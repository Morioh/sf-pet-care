#!/usr/bin/env bash
# =====================================================================
# SF Pet Care -- Cloud Run deployment script
#
# Deploys the project to your own Google Cloud project:
#   0. Enable the required APIs, grant build access, create buckets
#   1. Store the service account key in Secret Manager
#   2. Deploy the FastAPI service (api/)
#   3. Read back the FastAPI service's public URL
#   4. Deploy the Streamlit dashboard (streamlit/), pointed at that URL
#   5. Create a Cloud Scheduler job that POSTs to /etl every midnight
#
# Before running:
#   - A GCP project with billing enabled, and you logged in as an
#     Owner of it:  gcloud auth login
#   - A service account in that project and a JSON key for it, saved
#     outside the repo (see api/.env_template for the commands).
#   - api/.env copied from api/.env_template with your own
#     GCP_PROJECT_ID, GCP_BUCKET_NAME (globally unique),
#     GCP_SERVICE_ACCOUNT_KEY (absolute path), and either
#     CENSUS_API_KEY_FILE (path to a file holding the key) or
#     CENSUS_API_KEY. Either way the key is stored in Secret Manager.
#
# All other settings live in config.sh, which this script sources.
#
# Run it from anywhere:
#   bash gcloud_command.sh
# =====================================================================
set -euo pipefail

# =====================================================================
# CONFIGURATION
#
# PROJECT_ID, BUCKET_BASE, LOCAL_KEY_FILE, REGION, the ports and the
# service names are defined in config.sh. Do not redefine them here.
# =====================================================================
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/config.sh"

gcloud config set project "$PROJECT_ID"
gcloud config set run/region "$REGION"

PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" \
  --format="value(projectNumber)")
# Cloud Run services and Cloud Build source deploys run as this account.
COMPUTE_SA="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"

# Run 'gcloud run deploy', retrying on PERMISSION_DENIED. On a project's
# first deploy, the APIs and IAM grants from STEP 0 can take a few
# minutes to propagate, and the build fails until they do.
deploy_with_retry() {
  local attempt log
  log=$(mktemp)
  for attempt in 1 2 3 4; do
    if gcloud run deploy "$@" 2>&1 | tee "$log"; then
      rm -f "$log"
      return 0
    fi
    if [ "$attempt" -eq 4 ] || ! grep -q "PERMISSION_DENIED" "$log"; then
      rm -f "$log"
      return 1
    fi
    echo "Permissions may still be propagating; retrying in 90s" \
         "(attempt $((attempt + 1)) of 4)..." >&2
    sleep 90
  done
}
# The account the JSON key belongs to; the API uses it to reach GCS.
KEY_SA=$(sed -n 's/.*"client_email": *"\([^"]*\)".*/\1/p' "$LOCAL_KEY_FILE")
if [ -z "$KEY_SA" ]; then
  echo "ERROR: no client_email in '$LOCAL_KEY_FILE' -" \
       "is it a service account JSON key?" >&2
  exit 1
fi

# =====================================================================
# STEP 0 -- project setup.
# =====================================================================
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  cloudscheduler.googleapis.com \
  storage.googleapis.com

# Newer projects no longer give the compute account Editor, so
# 'gcloud run deploy --source' fails to build without this role.
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$COMPUTE_SA" \
  --role=roles/run.builder \
  --condition=None >/dev/null

# The API reads and writes <GCP_BUCKET_NAME>-dev and -prod. Create
# them if missing and let the key's service account use them.
for _suffix in dev prod; do
  _bucket="gs://$BUCKET_BASE-$_suffix"
  if ! gcloud storage buckets describe "$_bucket" >/dev/null 2>&1; then
    gcloud storage buckets create "$_bucket" \
      --location="$REGION" \
      --uniform-bucket-level-access
  fi
  gcloud storage buckets add-iam-policy-binding "$_bucket" \
    --member="serviceAccount:$KEY_SA" \
    --role=roles/storage.objectAdmin >/dev/null
done
unset _suffix _bucket

# =====================================================================
# STEP 1 -- put the service account key and Census key in Secret
# Manager.
#
# Cloud Run mounts the service account key as a file and exposes the
# Census key as the CENSUS_API_KEY env var at runtime.
# =====================================================================
# Create the secrets if they do not exist.
for _secret in "$GCP_KEY_SECRET" "$CENSUS_KEY_SECRET"; do
  if ! gcloud secrets describe "$_secret" >/dev/null 2>&1; then
    gcloud secrets create "$_secret" --replication-policy=automatic
  fi
done

# Add a new version of each secret. The Census key is stripped of
# whitespace so a trailing newline in the file doesn't end up in it.
gcloud secrets versions add "$GCP_KEY_SECRET" --data-file="$LOCAL_KEY_FILE"
if [ -n "$CENSUS_KEY_FILE" ]; then
  tr -d '[:space:]' < "$CENSUS_KEY_FILE"
else
  printf '%s' "$CENSUS_KEY_VALUE" | tr -d '[:space:]'
fi | gcloud secrets versions add "$CENSUS_KEY_SECRET" --data-file=-

# The API runs as the compute account, which needs secretAccessor on
# the secrets for --set-secrets to work.
for _secret in "$GCP_KEY_SECRET" "$CENSUS_KEY_SECRET"; do
  gcloud secrets add-iam-policy-binding "$_secret" \
    --member="serviceAccount:$COMPUTE_SA" \
    --role=roles/secretmanager.secretAccessor >/dev/null
done
unset _secret

# Cloud Run rejects a variable set both from the env file and from a
# secret, so deploy with a copy of api/.env minus the Census entries.
# The name must end in .env for gcloud to read it as a dotenv file.
DEPLOY_ENV_DIR=$(mktemp -d)
trap 'rm -rf "$DEPLOY_ENV_DIR"' EXIT
DEPLOY_ENV_FILE="$DEPLOY_ENV_DIR/api.env"
grep -vE '^[[:space:]]*CENSUS_API_KEY(_FILE)?[[:space:]]*=' "$ENV_FILE" \
  > "$DEPLOY_ENV_FILE" || true

# =====================================================================
# STEP 2 -- deploy the FastAPI service.
#
# '--source api' has Cloud Build build api/Dockerfile, push the image,
# and deploy it.
# =====================================================================
deploy_with_retry "$API_SERVICE" \
  --source "$SCRIPT_DIR/api" \
  --region "$REGION" \
  --port="$API_PORT" \
  --timeout="$API_TIMEOUT" \
  --allow-unauthenticated \
  --env-vars-file="$DEPLOY_ENV_FILE" \
  --set-secrets="$SECRET_MOUNT_PATH=$GCP_KEY_SECRET:latest,CENSUS_API_KEY=$CENSUS_KEY_SECRET:latest"

# --env-vars-file loaded GCP_SERVICE_ACCOUNT_KEY as the path on your
# laptop, which does not exist inside the container. Point it at the
# mounted secret instead.
gcloud run services update "$API_SERVICE" \
  --region "$REGION" \
  --update-env-vars=GCP_SERVICE_ACCOUNT_KEY="$SECRET_MOUNT_PATH"

# =====================================================================
# STEP 3 -- read back the API server's public URL.
#
# A Cloud Run service answers on two hostnames: the
# SERVICE-PROJECTNUMBER.REGION.run.app form that 'gcloud run deploy'
# prints as "Service URL", and a SERVICE-HASH-uc.a.run.app alias.
# Both reach the same service, but status.url returns the alias --
# 'urls[0]' is the one the deploy output and the Cloud Console show.
# =====================================================================
API_SERVER_URL=$(gcloud run services describe "$API_SERVICE" \
  --region "$REGION" --format="value(urls[0])")
# Older gcloud releases don't expose 'urls'; fall back to the alias.
if [ -z "$API_SERVER_URL" ]; then
  API_SERVER_URL=$(gcloud run services describe "$API_SERVICE" \
    --region "$REGION" --format="value(status.url)")
fi
# Without this check an empty URL silently propagates into the
# dashboard's API_URL and the scheduler's --uri.
if [ -z "$API_SERVER_URL" ]; then
  echo "ERROR: could not resolve the URL for Cloud Run service" \
       "'$API_SERVICE' in $REGION." >&2
  exit 1
fi
echo "$API_SERVICE deployed at $API_SERVER_URL"

# =====================================================================
# STEP 4 -- deploy the Streamlit dashboard, pointed at the API server.
#
# The dashboard only talks to the API, so it gets API_URL (read by
# streamlit/streamlit_app.py) and no GCP credentials. Cloud Run only
# assigns the API's URL once the service exists, which is why STEP 3
# reads it back instead of hardcoding it.
# =====================================================================
deploy_with_retry "$WEBAPP_SERVICE" \
  --source "$SCRIPT_DIR/streamlit" \
  --region "$REGION" \
  --port="$WEBAPP_PORT" \
  --allow-unauthenticated \
  --set-env-vars=API_URL="$API_SERVER_URL" \
  --clear-secrets

WEBAPP_URL=$(gcloud run services describe "$WEBAPP_SERVICE" \
  --region "$REGION" --format="value(urls[0])")
echo "$WEBAPP_SERVICE deployed at $WEBAPP_URL"

# =====================================================================
# STEP 5 -- schedule the nightly ETL refresh.
#
# Cloud Scheduler sends an HTTP POST to the API's /etl endpoint
# (api/main.py) on the schedule set in config.sh.
# =====================================================================
# Create or update based on whether the job already exists.
if gcloud scheduler jobs describe "$SCHEDULER_JOB" \
     --location="$REGION" >/dev/null 2>&1; then
  SCHEDULER_ACTION=update
else
  SCHEDULER_ACTION=create
fi

gcloud scheduler jobs "$SCHEDULER_ACTION" http "$SCHEDULER_JOB" \
  --location="$REGION" \
  --schedule="$SCHEDULE" \
  --uri="$API_SERVER_URL$ETL_PATH" \
  --http-method=POST \
  --time-zone="America/Los_Angeles" \
  --attempt-deadline="${API_TIMEOUT}s"

echo
echo "Done."
echo "  API:       $API_SERVER_URL"
echo "  Dashboard: $WEBAPP_URL"
echo "  Scheduler: $SCHEDULER_JOB ($SCHEDULE) -> POST $ETL_PATH"
echo
echo "The dashboard is empty until the first ETL run. To run it now:"
echo "  gcloud scheduler jobs run $SCHEDULER_JOB --location=$REGION"
