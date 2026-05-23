# gpu component

A per-app `ResourceClaimTemplate` named `${APP}-gpu` against the `gpu.intel.com` DeviceClass. Sharing happens via SR-IOV virtual functions on the host — the template's CEL selector skips parent PFs and grabs a VF.

## Variables

| var | required | notes |
| --- | --- | --- |
| `APP` | yes | names the RCT (`${APP}-gpu`) |
| `GPU_CEL` | no | extra CEL selector expression, defaults to `true` (no extra filter). Add this when an app needs a specific VF attribute. |

## Consuming the RCT

In the consumer pod spec:

```yaml
spec:
  resourceClaims:
    - name: gpu
      resourceClaimTemplateName: ${APP}-gpu
  containers:
    - name: app
      resources:
        claims:
          - name: gpu
```

## Notes

- The selector `device.attributes["gpu.intel.com"].sriov == false` is intentionally inverted from what it reads like: `sriov: false` on a *device* means "this is a VF, not the parent SR-IOV-enabled PF." VFs are the unit of per-pod assignment; the PF is the parent and not claimed directly.
- `GPU_CEL` is additive — both selectors must match (CEL selectors are ANDed by DRA). Use it to require a specific GPU attribute (model, memory size, etc.) when there's more than one GPU type in the cluster — check whatever the Intel DRA driver actually exposes at that point in time.
- This component only ships the template. The Intel GPU operator + DRA driver + SR-IOV setup live at the cluster level (Talos extension + kernel args for `xe.max_vfs`).
- There's no resource sharing budget here — each pod consuming the RCT gets a dedicated VF. Capacity is bounded by `xe.max_vfs` on the host.
