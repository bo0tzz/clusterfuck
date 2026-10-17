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

- The selector `device.attributes["gpu.intel.com"].type == "vf"` only matches VFs; they're the unit of per-pod assignment and the whole GPU (`type: gpu`) is never claimed directly. VFs created after the DRA plugin started don't get the `sriov` attribute, so don't select on that.
- `GPU_CEL` is additive — both selectors must match (CEL selectors are ANDed by DRA). Use it to require a specific GPU attribute (model, memory size, etc.) when there's more than one GPU type in the cluster — check whatever the Intel DRA driver actually exposes at that point in time.
- This component only ships the template. The DRA driver and the VFs live at the cluster level: each SR-IOV node has a Talos `UdevRulesConfig` setting `sriov_numvfs`.
- There's no resource sharing budget here — each pod consuming the RCT gets a dedicated VF. Capacity is bounded by each node's `sriov_numvfs`. Pods in one namespace can share a VF through a plain `ResourceClaim` instead (immich does).
