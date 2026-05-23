# bucket component

A COSI `BucketClaim` + `BucketAccess` + an `ExternalSecret` that reshapes the driver-produced `BucketInfo` JSON into a flat set of env-var-style keys that postgres/volsync/consumers actually want.

## Variables

| var | required | notes |
| --- | --- | --- |
| `BUCKET` | yes | name of the BucketClaim/BucketAccess and the output Secret |
| `BUCKETCLASS` | no | defaults to `backblaze` |
| `BUCKETACCESSCLASS` | no | defaults to `backblaze` |

## Output Secret

The component produces a Secret named `${BUCKET}` with these keys:

| key | source |
| --- | --- |
| `AWS_ACCESS_KEY_ID` | BucketInfo `.spec.secretS3.accessKeyID` |
| `AWS_SECRET_ACCESS_KEY` | BucketInfo `.spec.secretS3.accessSecretKey` |
| `BUCKET_NAME` | BucketInfo `.spec.bucketName` |
| `BUCKET_ENDPOINT` | BucketInfo `.spec.secretS3.endpoint` (with scheme) |
| `BUCKET_HOST` | as above, scheme stripped |
| `BUCKET_REGION` | BucketInfo `.spec.secretS3.region` |
| `RESTIC_REPOSITORY` | `s3:<endpoint>/<bucket>/restic` |

The intermediate `${BUCKET}-raw` Secret (BucketInfo JSON, written by the COSI driver) is left alone.

## Consuming

Bucket lives in a different Flux KS than the consuming workload — `substituteFrom: Secret name: ${BUCKET}` doesn't work in the same KS because the Secret doesn't exist at build time. Use the two-stage pattern in `paperwork/bo0kkeeper/ks.yaml` as the reference: a `<app>-bucket` KS with `wait: true` for the bucket alone, then the main KS with `dependsOn`.

## Notes

- Requires the `eso-kubernetes-local` component on the namespace's parent kustomization (the ExternalSecret here pulls via the `kubernetes-local` SecretStore which that component sets up).
- The `BucketClass` and `BucketAccessClass` are cluster-scoped and live in the COSI driver app, not here.
- For BackBlaze + the homegrown `b2-cosi-driver`: the driver's `DriverGrantBucketAccess` isn't idempotent on retry, so don't kick the BucketAccess into churn. Once it settles, leave it.
