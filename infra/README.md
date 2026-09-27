# UNFOUND API -- CDK stack

One stack (`UnfoundApiStack`): ECR repository, a Lambda container-image function
(arm64, 2048 MB, 30s timeout, no VPC), its Function URL, a 14-day-retention log
group, and least-privilege IAM for the app's own SSM parameters. ap-northeast-1,
matching the Supabase project's region.

## 1. Create the SSM parameters (you do this, not me)

Three plain `String` parameters (baked into the Lambda's environment as the actual
value at deploy time) and up to two `SecureString` secrets (only their *path* is
baked in; `backend.app._resolve_secret` fetches and decrypts the real value itself
via boto3 at Lambda startup -- AWS rejects `ssm-secure` dynamic references inside a
Lambda function's environment variables entirely, caught by `cdk synth` before any
deploy was attempted, so CloudFormation-time resolution was never an option for
these two). Run these yourself -- the actual values never need to be pasted into
chat or committed anywhere.

```bash
aws ssm put-parameter --region ap-northeast-1 --type String --overwrite \
  --name /unfound/prod/SUPABASE_URL --value "https://qilerhwlccptkpdghfen.supabase.co"

aws ssm put-parameter --region ap-northeast-1 --type String --overwrite \
  --name /unfound/prod/SUPABASE_KEY --value "<the Supabase publishable/anon key>"

aws ssm put-parameter --region ap-northeast-1 --type String --overwrite \
  --name /unfound/prod/ALLOWED_ORIGINS --value "http://localhost:5173"
  # update this to the real Vercel origin once you have it (Step 4), then redeploy
```

Only if you want personalization live in this deploy (needs the service-role key):

```bash
aws ssm put-parameter --region ap-northeast-1 --type SecureString --overwrite \
  --name /unfound/prod/SUPABASE_SERVICE_ROLE_KEY --value "<the service-role key>"
```

Only if you want the admin endpoints enabled (generate a random key yourself, e.g.
`openssl rand -hex 32`, and use that -- don't reuse anything already shown in chat):

```bash
aws ssm put-parameter --region ap-northeast-1 --type SecureString --overwrite \
  --name /unfound/prod/ADMIN_API_KEY --value "<a freshly generated random key>"
```

## 2. First deploy only: bootstrap the ECR repo before the image exists

A container-image Lambda can't be created before its image exists in the repo, and
the repo doesn't exist before this stack's first deploy. So the very first time:

```bash
cd infra
source .venv/bin/activate
pip install -r requirements.txt
npx aws-cdk@2 deploy -c deployFunction=false   # creates ONLY the ECR repo
./build_and_push.sh                             # builds + pushes the image
npx aws-cdk@2 deploy                            # now adds the Function/URL/IAM
```

Every deploy after that is just `./build_and_push.sh <tag>` then
`cdk deploy -c imageTag=<tag>` (or omit `-c imageTag` to reuse `latest`).

## Context flags

- `-c deployFunction=false` -- first deploy only (see above).
- `-c imageTag=<tag>` -- which pushed image tag the function should use (default `latest`).
- `-c enablePersonalization=true` -- adds `SUPABASE_SERVICE_ROLE_KEY` to the function's
  environment. Only pass this once that SSM parameter actually exists.
- `-c enableAdmin=true` -- same, for `ADMIN_API_KEY`.

## Outputs

`cdk deploy` prints `RepositoryUri` always, and `FunctionUrl` / `LogGroupName` once
the function exists.
