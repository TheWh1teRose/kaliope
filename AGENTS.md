# AGENTS.md

## Deploy

Every push to `main` (or a manual run of the **Deploy** workflow) ships to the GCP VM.
Workflow: `.github/workflows/deploy.yml`. VM-side script: `.github/scripts/deploy-vm.sh`.

1. Auth to GCP via Workload Identity Federation (no JSON key).
2. Build the Dockerfile on the runner, push `…/kalliope/kalliope:<commit-sha>` and `:latest` to Artifact Registry.
3. SSH to the VM through IAP (`gcloud compute ssh --tunnel-through-iap`, OS Login) and pipe `deploy-vm.sh` to `sudo bash -s`.
4. On the VM: pull the SHA image, read `app-secret-key` / `anthropic-api-key` from Secret Manager into env (never a file), `docker rm -f kalliope`, `docker run` it again with `-v /var/lib/kalliope:/data`, `DATA_DIR=/data`, `--restart unless-stopped`.
5. Poll `http://127.0.0.1:8000/api/health` for `"status":"ok"` (60s). On failure the job fails, logs are printed, and the previous image is restarted.

Existing port binding is preserved: `127.0.0.1:8000` if the running container uses it, else `8000:8000`. Override with variable `GCP_PORT_BIND`.
Runs are serialized (`concurrency: deploy`, no cancel). State in `/var/lib/kalliope` survives redeploys.

### Repository variables (Settings → Variables)

| Name | Value |
|---|---|
| `GCP_PROJECT_ID` | `qlug-kalliope` |
| `GCP_REGION` | `europe-west1` |
| `GCP_ZONE` | `europe-west1-b` |
| `GCP_VM_NAME` | `kalliope` |
| `GCP_AR_REPO` | optional, default `kalliope` |
| `GCP_IMAGE_NAME` | optional, default `kalliope` |
| `GCP_PORT_BIND` | optional, e.g. `127.0.0.1:8000:8000`; empty = keep existing |

Repository secrets: `GCP_WORKLOAD_IDENTITY_PROVIDER` (full provider resource name), `GCP_SERVICE_ACCOUNT` (deployer SA email).

### One-time setup (run by hand with gcloud as a project owner)

```sh
PROJECT_ID=qlug-kalliope
REGION=europe-west1
ZONE=europe-west1-b
REPO=TheWh1teRose/kaliope
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
VM_SA=${PROJECT_NUMBER}-compute@developer.gserviceaccount.com
DEPLOYER=gh-deployer
DEPLOYER_SA=${DEPLOYER}@${PROJECT_ID}.iam.gserviceaccount.com

gcloud services enable iamcredentials.googleapis.com iap.googleapis.com oslogin.googleapis.com \
  artifactregistry.googleapis.com compute.googleapis.com --project=$PROJECT_ID

# Deployer service account + roles
gcloud iam service-accounts create $DEPLOYER --project=$PROJECT_ID --display-name="GitHub deployer"
for ROLE in roles/artifactregistry.writer roles/compute.instanceAdmin.v1 \
            roles/compute.osAdminLogin roles/iap.tunnelResourceAccessor; do
  gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member=serviceAccount:$DEPLOYER_SA --role=$ROLE --condition=None
done
gcloud iam service-accounts add-iam-policy-binding $VM_SA --project=$PROJECT_ID \
  --member=serviceAccount:$DEPLOYER_SA --role=roles/iam.serviceAccountUser

# Allow IAP to reach SSH on the VM
gcloud compute firewall-rules create allow-iap-ssh-kalliope --project=$PROJECT_ID \
  --network=default --direction=INGRESS --action=ALLOW --rules=tcp:22 \
  --source-ranges=35.235.240.0/20 --target-tags=kalliope

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

Set `GCP_SERVICE_ACCOUNT` to `$DEPLOYER_SA`. The VM's service account must already be able to read both secrets (`roles/secretmanager.secretAccessor`) and pull from Artifact Registry (`roles/artifactregistry.reader`). The VM needs OS Login enabled (`enable-oslogin=TRUE` metadata, project or instance).

### Roll back

Actions → Deploy → Run workflow, set `image_tag` to an earlier commit SHA (skips the build, deploys that tag). Tags are listed with
`gcloud artifacts docker tags list europe-west1-docker.pkg.dev/qlug-kalliope/kalliope/kalliope`.

### Deploy manually

Same as CI: from a machine with gcloud access,
```sh
IMAGE=europe-west1-docker.pkg.dev/qlug-kalliope/kalliope/kalliope:<tag>
gcloud builds submit --region=europe-west1 --tag $IMAGE      # or docker build + push
gcloud compute ssh kalliope --zone=europe-west1-b --tunnel-through-iap \
  --command "sudo env IMAGE=$IMAGE PROJECT_ID=qlug-kalliope REGISTRY_HOST=europe-west1-docker.pkg.dev PORT_BIND= bash -s" \
  < .github/scripts/deploy-vm.sh
```
