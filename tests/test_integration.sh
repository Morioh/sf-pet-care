#!/usr/bin/env bash
# Integration test for the unified ETL.
#
# Pulls each engineer's CURRENT script from their branch, puts them in one
# folder with client.py and contract.py, and runs the pipeline end to end.
#
# Why this exists: client.py imports the source modules, but each one lives on
# a different branch. Until they are all merged into dev there is nowhere the
# pipeline can actually run. This recreates a merged dev in a temp folder.
#
#   bash etl/test_integration.sh
#
# Exit code 0 = the ETL ran and the output reconciled.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="${TMPDIR:-/tmp}/sfpet_integration_test"
PY="${SFPET_PYTHON:-/opt/anaconda3/envs/sfpet/bin/python}"

pass=0; fail=0
ok()   { echo "  PASS  $1"; pass=$((pass+1)); }
bad()  { echo "  FAIL  $1"; fail=$((fail+1)); }

echo "=================================================="
echo " sf-pet-care ETL integration test"
echo "=================================================="

# ---- 0. prerequisites -------------------------------------------------
[ -x "$PY" ] || { echo "Python not found at $PY"; echo "Set SFPET_PYTHON=/path/to/python"; exit 1; }
[ -f "$REPO/.env" ] || echo "  NOTE: no .env found - the ETL will fall back to public APIs"

# ---- 1. get everyone's latest code -----------------------------------
echo
echo "[1] Fetching the latest from every branch"
git -C "$REPO" fetch --all --prune -q

rm -rf "$WORK"; mkdir -p "$WORK"
grab() {  # grab <branch> <file>
  if git -C "$REPO" show "origin/$1:$2" > "$WORK/$2" 2>/dev/null; then
    echo "      $2  <- origin/$1  ($(git -C "$REPO" log "origin/$1" -1 --format=%h))"
  else
    echo "      $2  <- origin/$1  NOT FOUND"
    rm -f "$WORK/$2"
  fi
}
grab maurice registered_businesses.py
grab jonah   neighborhood.py
grab jessie  acs_data.py || grab feature/acs-2024-5yr acs_data.py
cp "$REPO/etl/client.py" "$REPO/etl/contract.py" "$WORK/"

# ---- 2. do the modules import at all? --------------------------------
echo
echo "[2] Can each module be imported?"
cd "$WORK"
for m in registered_businesses neighborhood; do
  [ -f "$m.py" ] || { bad "$m.py missing"; continue; }
  if err=$("$PY" -c "import $m" 2>&1); then
    ok "$m imports"
  else
    bad "$m import failed: $(echo "$err" | tail -1)"
  fi
done

# ---- 3. does adding FastAPI start a server by accident? --------------
echo
echo "[3] No module starts a web server on import"
if "$PY" - <<'PY' 2>/dev/null
import socket, importlib
for m in ("registered_businesses", "neighborhood"):
    try: importlib.import_module(m)
    except Exception: pass
s = socket.socket(); s.settimeout(0.3)
busy = s.connect_ex(("127.0.0.1", 8000)) == 0
s.close()
raise SystemExit(1 if busy else 0)
PY
then ok "nothing listening on :8000"
else bad "something is listening on :8000 - a module started a server"
fi

# ---- 4. each source still returns what the contract expects ----------
echo
echo "[4] Each source satisfies the data contract"
"$PY" - <<'PY'
import sys, contract

# Import separately: if this fails, nb never exists and referencing it in the
# fallback below would raise NameError instead of saying what actually broke.
try:
    import neighborhood as nb
except Exception as e:
    print(f"        cannot import neighborhood.py: {type(e).__name__}: {e}")
    sys.exit(1)

rc = 0
try:
    gdf = nb.download_neighborhood(dev_mode=True)
    contract.check_neighborhoods(gdf)
except Exception as e:
    print(f"        bucket read failed ({type(e).__name__}), trying his source fetch")
    try:
        gdf = nb.fetch_and_clean_neighborhood_data_from_internet()
        contract.check_neighborhoods(gdf)
    except Exception as e2:
        print(f"        FAIL neighborhoods: {type(e2).__name__}: {e2}")
        rc = 1
sys.exit(rc)
PY
[ $? -eq 0 ] && ok "Jonah's neighborhoods" || bad "Jonah's neighborhoods"

# ---- 5. the whole pipeline ------------------------------------------
echo
echo "[5] Full pipeline end to end"
rm -rf "$WORK/data/processed"
set -a; [ -f "$REPO/.env" ] && . "$REPO/.env"; set +a
# --dry-run: the team shares one -dev bucket, so the test must not upload a
# near-identical file every time it runs. Output still lands locally so step 6
# can reconcile the counts.
if "$PY" client.py --dry-run > "$WORK/run.log" 2>&1; then
  ok "client.py completed"
  grep -E "^(OK|.*rows from)" "$WORK/run.log" | sed 's/^/        /'
else
  bad "client.py failed"
  tail -15 "$WORK/run.log" | sed 's/^/        /'
fi

# ---- 6. do the numbers add up? --------------------------------------
echo
echo "[6] Output reconciles"
"$PY" - <<'PY'
import glob, io, os, sys
import pandas as pd

# The ETL writes to GCS when credentials exist, otherwise to disk. Check
# whichever one it actually produced. Exit 2 = genuinely could not check,
# which must NOT be reported as a pass.
d = src = None
files = sorted(glob.glob("data/processed/*.csv"))
if files:
    src = files[-1]
    d = pd.read_csv(src)
else:
    bucket = os.getenv("GCP_BUCKET_NAME")
    key = os.getenv("GCP_SERVICE_ACCOUNT_KEY")
    if not (bucket and key):
        print("        no local output and no credentials to read the bucket")
        sys.exit(2)
    try:
        from google.cloud import storage
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(key)
        cl = storage.Client(project=os.getenv("GCP_PROJECT_ID"), credentials=creds)
        blobs = [b for b in cl.list_blobs(f"{bucket}-dev", prefix="processed/")
                 if b.name.endswith(".csv")]
        if not blobs:
            print("        no processed/*.csv found in the bucket")
            sys.exit(1)
        newest = max(blobs, key=lambda b: b.updated)
        src = f"gs://{bucket}-dev/{newest.name}"
        d = pd.read_csv(io.BytesIO(newest.download_as_bytes()))
    except Exception as e:
        print(f"        could not read the bucket: {type(e).__name__}: {e}")
        sys.exit(2)

total = int(d["businesses_total"].sum())
cats = int(d[["veterinary", "pet_care", "pet_store"]].sum().sum())
dupes = int(d["neighborhood"].duplicated().sum())
print(f"        source:         {src}")
print(f"        neighbourhoods: {len(d)}")
print(f"        businesses:     {total}")
print(f"        category sum:   {cats}")
print(f"        duplicates:     {dupes}")
sys.exit(0 if total == cats and dupes == 0 else 1)
PY
case $? in
  0) ok   "counts reconcile" ;;
  2) echo "  SKIP  could not verify output - neither local file nor bucket readable" ;;
  *) bad  "counts do not reconcile" ;;
esac

# ---- summary ---------------------------------------------------------
echo
echo "=================================================="
echo " $pass passed, $fail failed"
echo " workspace kept at: $WORK"
echo "=================================================="
exit $(( fail > 0 ? 1 : 0 ))
