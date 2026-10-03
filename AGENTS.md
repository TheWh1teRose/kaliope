# AGENTS.md

## Deploy

Every push to `main` (or a manual run of the **Deploy** workflow) ships to the existing Cloud Run service.
Workflow: `.github/workflows/deploy.yml`.

1. Auth to GCP via Workload Identity Federation (no JSON key).
2. Build the Dockerfile on the runner, push `…/kalliope/kalliope:<commit-sha>` and `:latest` to Artifact Registry.
3. `gcloud run deploy kalliope --image <sha-tag> --region europe-west1`. Only the image changes; env (`DATA_DIR=/data`), secrets (`app-secret-key`, `anthropic-api-key`), the GCS FUSE volume (bucket `qlug-kalliope-data` at `/data`), scaling and resources stay as configured on the service.
4. Look up the service URL and poll `<url>/api/health` for `"status":"ok"` (about 2 min). The job fails otherwise. This assumes the service allows unauthenticated requests.

The image defaults to `SQLITE_JOURNAL_MODE=DELETE` for the GCS FUSE mount at `/data` (local development stays on WAL). SQLite's WAL mode cannot write `kalliope.db-wal` there (`stale file handle`, logins return 500). On the next start the app (and `alembic upgrade head` in the entrypoint) checkpoints any leftover `kalliope.db-wal` into the database, then removes it.

Runs are serialized (`concurrency: deploy`, no cancel). The service runs with max 1 instance because the data is SQLite on a mounted bucket; do not raise it. Cloud Run keeps old revisions, so a failed health check leaves the bad revision serving: roll back (below).

### Repository variables (Settings → Variables)

| Name | Value |
|---|---|
| `GCP_PROJECT_ID` | `qlug-kalliope` |
| `GCP_REGION` | `europe-west1` |
| `GCP_CLOUD_RUN_SERVICE` | optional, default `kalliope` |
| `GCP_AR_REPO` | optional, default `kalliope` |
| `GCP_IMAGE_NAME` | optional, default `kalliope` |

Repository secrets: `GCP_WORKLOAD_IDENTITY_PROVIDER` (full provider resource name), `GCP_SERVICE_ACCOUNT` (deployer SA email).

### One-time setup (run by hand with gcloud as a project owner)

The service itself already exists; this only creates what GitHub needs to deploy it. Each `create` fails with "already exists" if done before, which is harmless; the IAM bindings are idempotent.

```sh
PROJECT_ID=qlug-kalliope
REPO=TheWh1teRose/kaliope
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
RUNTIME_SA=825911302957-compute@developer.gserviceaccount.com   # Cloud Run runtime service account
DEPLOYER=gh-deployer
DEPLOYER_SA=${DEPLOYER}@${PROJECT_ID}.iam.gserviceaccount.com

gcloud services enable iamcredentials.googleapis.com run.googleapis.com \
  artifactregistry.googleapis.com --project=$PROJECT_ID

# Deployer service account + roles
gcloud iam service-accounts create $DEPLOYER --project=$PROJECT_ID --display-name="GitHub deployer"
for ROLE in roles/artifactregistry.writer roles/run.admin; do
  gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member=serviceAccount:$DEPLOYER_SA --role=$ROLE --condition=None
done
# Needed to deploy a revision that runs as the runtime service account
gcloud iam service-accounts add-iam-policy-binding $RUNTIME_SA --project=$PROJECT_ID \
  --member=serviceAccount:$DEPLOYER_SA --role=roles/iam.serviceAccountUser

# Workload Identity pool + provider, restricted to this repo
gcloud iam workload-identity-pools create github --project=$PROJECT_ID --location=global \
  --display-name="GitHub Actions"
gcloud iam workload-identity-pools providers create-oidc github-oidc --project=$PROJECT_ID \
  --location=global --workload-identity-pool=github \
  --issuer-uri=https://token.actions.githubusercontent.com \
  --attribute-mapping=google.subject=assertion.sub,attribute.repository=assertion.repository \
  --attribute-condition="assertion.repository=='$REPO'"
gcloud iam service-accounts add-iam-policy-binding $DEPLOYER_SA --project=$PROJECT_ID \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/$REPO"

# Value for the GCP_WORKLOAD_IDENTITY_PROVIDER secret
gcloud iam workload-identity-pools providers describe github-oidc --project=$PROJECT_ID \
  --location=global --workload-identity-pool=github --format='value(name)'
```

Set `GCP_SERVICE_ACCOUNT` to `$DEPLOYER_SA`. The runtime service account must already read both secrets (`roles/secretmanager.secretAccessor`) and the bucket; the Cloud Run service agent pulls from Artifact Registry in the same project.

Audio generation needs one more, optional secret: `elevenlabs-api-key`, bound as `ELEVENLABS_API_KEY` (commands in the README under "Audio pipelines"). Without it the app runs normally and the audio panel explains the setup.

### Roll back

Actions → Deploy → Run workflow, set `image_tag` to an earlier commit SHA (skips the build, deploys that tag). Tags are listed with
`gcloud artifacts docker tags list europe-west1-docker.pkg.dev/qlug-kalliope/kalliope/kalliope`.

### Deploy manually

Same as CI, from a machine with gcloud access:
```sh
IMAGE=europe-west1-docker.pkg.dev/qlug-kalliope/kalliope/kalliope:<tag>
gcloud builds submit --region=europe-west1 --tag $IMAGE      # or docker build + push
gcloud run deploy kalliope --project=qlug-kalliope --region=europe-west1 --image=$IMAGE
```
