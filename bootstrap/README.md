# Bootstrap

This directory contains the helmfile used to bootstrap the cluster (Cilium + Flux).

## Manual step: SOPS age key secret

Before Flux can fully reconcile, the `sops-age` Secret must exist in `flux-system`.
The kustomize-controller is configured to use it for SOPS decryption across all
Kustomizations automatically.

```sh
kubectl create secret generic sops-age \
  --from-file=age.agekey=~/.config/sops/age/keys.txt \
  -n flux-system
```
