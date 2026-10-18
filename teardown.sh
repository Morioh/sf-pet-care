#!/usr/bin/env bash
# =====================================================================
# SF Pet Care -- tear down what gcloud_command.sh deployed
#
# Removes, in reverse order of deployment:
#   5. The Cloud Scheduler job
#   4. The Streamlit dashboard service
#   2. The FastAPI service
#   1. The service account key and Census key secrets
#   -  The container images (Artifact Registry repo
#      'cloud-run-source-deploy') and uploaded source
#      (gs://run-sources-<PROJECT_ID>-<REGION>) that --source deploys
#      create
#   0. The IAM grants gcloud_command.sh added
#
# Kept unless you ask otherwise:
#   - The data buckets <GCP_BUCKET_NAME>-dev and -prod, which hold the
#     ETL output. Pass --delete-buckets to delete them and their data.
#   - Enabled APIs. They cost nothing while unused, and a redeploy
#     re-enables them anyway.
#
# Run it from anywhere:
#   bash teardown.sh                  # asks before deleting
#   bash teardown.sh --yes            # no prompt
#   bash teardown.sh --delete-buckets # also delete the data buckets
#
# Every step skips resources that are already gone, so it is safe to
# re-run after a partial teardown.
# =====================================================================
set -euo pipefail

ASSUME_YES=""
DELETE_BUCKETS=""
for _arg in "$@"; do
  case "$_arg" in
    -y|--yes)         ASSUME_YES=1 ;;
    --delete-buckets) DELETE_BUCKETS=1 ;;
    -h|--help)        awk 'NR > 1 && /^#/ { print; next } NR > 1 { exit }' "$0"; exit 0 ;;
    *) echo "ERROR: unknown option '$_arg' (see --help)." >&2; exit 1 ;;
  esac
done
unset _arg

# =====================================================================
# CONFIGURATION -- same names gcloud_command.sh deployed with.
# =====================================================================
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SKIP_KEY_CHECKS=1
source "$SCRIPT_DIR/config.sh"

# Created by 'gcloud run deploy --source', not by our script directly.
# Newer gcloud uploads source to run-sources-<project>-<region>; older
# releases used <project>_cloudbuild. Both are cleaned up if present.
SOURCE_REPO=cloud-run-source-deploy
BUILD_BUCKETS=("gs://run-sources-$PROJECT_ID-$REGION" "gs://${PROJECT_ID}_cloudbuild")

PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" \
  --format="value(projectNumber)")
COMPUTE_SA="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"
KEY_SA=""
if [ -f "$LOCAL_KEY_FILE" ]; then
  KEY_SA=$(sed -n 's/.*"client_email": *"\([^"]*\)".*/\1/p' "$LOCAL_KEY_FILE")
fi

DATA_BUCKETS=("gs://$BUCKET_BASE-dev" "gs://$BUCKET_BASE-prod")

echo "This will delete from project '$PROJECT_ID' (region $REGION):"
echo "  - Cloud Scheduler job:  $SCHEDULER_JOB"
echo "  - Cloud Run services:   $WEBAPP_SERVICE, $API_SERVICE"
echo "  - Secrets:              $GCP_KEY_SECRET, $CENSUS_KEY_SECRET"
echo "  - Container images:     Artifact Registry repo $SOURCE_REPO"
echo "  - Uploaded source:      ${BUILD_BUCKETS[*]} (if present)"
echo "  - IAM grants added by gcloud_command.sh"
if [ -n "$DELETE_BUCKETS" ]; then
  echo "  - DATA BUCKETS AND ALL THEIR CONTENTS: ${DATA_BUCKETS[*]}"
else
  echo "Keeping data buckets: ${DATA_BUCKETS[*]} (use --delete-buckets)"
fi
if [ -z "$ASSUME_YES" ]; then
  read -r -p "Continue? [y/N] " _reply
  case "$_reply" in
    y|Y|yes|YES) ;;
    *) echo "Aborted."; exit 1 ;;
  esac
fi

gcloud config set project "$PROJECT_ID"

# =====================================================================
# STEP 5 -- the scheduler job, first, so it can't call a half-deleted
# API.
# =====================================================================
if gcloud scheduler jobs describe "$SCHEDULER_JOB" \
     --location="$REGION" >/dev/null 2>&1; then
  gcloud scheduler jobs delete "$SCHEDULER_JOB" \
    --location="$REGION" --quiet
else
  echo "Scheduler job $SCHEDULER_JOB: already gone."
fi

# =====================================================================
# STEPS 4 and 2 -- the Cloud Run services.
# =====================================================================
for _service in "$WEBAPP_SERVICE" "$API_SERVICE"; do
  if gcloud run services describe "$_service" \
       --region "$REGION" >/dev/null 2>&1; then
    gcloud run services delete "$_service" --region "$REGION" --quiet
  else
    echo "Cloud Run service $_service: already gone."
  fi
done
unset _service

# =====================================================================
# STEP 1 -- the secrets. Deleting a secret also removes its
# secretAccessor grant.
# =====================================================================
for _secret in "$GCP_KEY_SECRET" "$CENSUS_KEY_SECRET"; do
  if gcloud secrets describe "$_secret" >/dev/null 2>&1; then
    gcloud secrets delete "$_secret" --quiet
  else
    echo "Secret $_secret: already gone."
  fi
done
unset _secret

# =====================================================================
# Build leftovers -- images and uploaded source from --source deploys.
# These keep costing storage after the services are gone.
# =====================================================================
if gcloud artifacts repositories describe "$SOURCE_REPO" \
     --location="$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories delete "$SOURCE_REPO" \
    --location="$REGION" --quiet
else
  echo "Artifact Registry repo $SOURCE_REPO: already gone."
fi

for _bucket in "${BUILD_BUCKETS[@]}"; do
  if gcloud storage buckets describe "$_bucket" >/dev/null 2>&1; then
    gcloud storage rm --recursive "$_bucket"
  else
    echo "Bucket $_bucket: already gone."
  fi
done
unset _bucket

# =====================================================================
# STEP 0 -- data buckets (only with --delete-buckets) and IAM grants.
# =====================================================================
for _bucket in "${DATA_BUCKETS[@]}"; do
  if ! gcloud storage buckets describe "$_bucket" >/dev/null 2>&1; then
    echo "Bucket $_bucket: already gone."
  elif [ -n "$DELETE_BUCKETS" ]; then
    gcloud storage rm --recursive "$_bucket"
  elif [ -n "$KEY_SA" ]; then
    gcloud storage buckets remove-iam-policy-binding "$_bucket" \
      --member="serviceAccount:$KEY_SA" \
      --role=roles/storage.objectAdmin >/dev/null 2>&1 \
      || echo "Bucket $_bucket: no objectAdmin grant to remove."
  else
    echo "Bucket $_bucket: key file not found, leaving its IAM grant."
  fi
done
unset _bucket

gcloud projects remove-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$COMPUTE_SA" \
  --role=roles/run.builder \
  --condition=None >/dev/null 2>&1 \
  || echo "Project: no run.builder grant to remove."

echo
echo "Teardown complete for project '$PROJECT_ID'."
if [ -z "$DELETE_BUCKETS" ]; then
  echo "Data buckets kept: ${DATA_BUCKETS[*]}"
fi
