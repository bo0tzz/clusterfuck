# Migrating apps from the old cluster

Scope: how to move apps from the old cluster at `192.168.4.105` into this one. Postgres has its own story (see `components/postgres/README.md`); this doc covers PVC data migration and coordinating multi-app stacks.

## Decision tree

| App shape | Approach |
| --- | --- |
| New app (no old data) | Fresh deploy, no migration needed |
| Postgres-backed only | Use the postgres component's `initdb.import` migration patch (see postgres README) |
| Small PVC (< ~50GB, downtime tolerable) | **Pattern A**: restic restore from old cluster's existing B2 backups |
| Large PVC (downtime not tolerable) | **Pattern B**: rsync-TLS pre-sync from the running old cluster |
| Tightly-coupled stack (HA + z2m + broker, etc.) | Downtime window — pre-sync is wasted because the apps aren't independently useful |

Both PVC patterns share the same shape: a separate `migration/` directory, a dedicated Flux KS that lines up a temporary PVC, and a `dataSourceRef` patch on the volsync component's PVC to clone from the temp PVC at app-creation time.

## Common structure

```
kubernetes/apps/<ns>/<app>/
├── ks.yaml                 # bucket KS + migration KS + app KS (3 docs)
├── bucket/                 # standard bucket component scaffold
├── migration/
│   ├── kustomization.yaml
│   ├── replicationdestination.yaml  # restic OR rsync-tls mover; auto-creates the temp PVC
│   └── externalsecret.yaml # restic creds (pattern A only)
└── app/
    ├── kustomization.yaml  # includes volsync component + dataSourceRef patch
    └── helmrelease.yaml etc.
```

The RD auto-creates a PVC matching its name (`${APP}-migration`) when `destinationPVC` is omitted and `capacity` / `storageClassName` / `accessModes` are provided on the RD itself. The temp PVC is one-shot scaffolding, so there's no benefit to declaring it separately — let the RD make it.

The `app/kustomization.yaml` patch that makes the main PVC clone from the temp PVC:

```yaml
patches:
  - target: { kind: PersistentVolumeClaim, name: ${APP} }
    patch: |-
      - op: replace
        path: /spec/dataSourceRef
        value:
          kind: PersistentVolumeClaim
          name: ${APP}-migration
```

PVC-to-PVC cloning is a CSI feature — Ceph RBD and CephFS both support it. RBD is CoW (near-instant even at hundreds of GB). CephFS clones are full RADOS object copies — proportional to data size. Measured on this cluster at ~280 MB/s sustained throughput for a multi-hundred-GB volume, so a 400G immich library should land in the 20–45 min range (the upper end accounts for metadata cost from immich's many-small-files shape).

**This patch is a one-way door.** `dataSourceRef` is creation-only/immutable. After cleanup deletes the temp PVC, the field references a non-existent PVC — harmless (k8s never dereferences it post-creation) but you can't remove the patch either, or Flux will try to revert to the kopia RD and get rejected with an immutable-field error on every reconcile. Leave it in with a comment:

```yaml
# immutable field, remove on restore
```

## Pattern A: restic restore from B2

Fully chainable — `dependsOn + wait: true` plus `healthCheckExprs` does the cutover automatically.

The `healthCheckExprs` block on the migration KS is **required**, not optional. Flux's default `wait: true` uses kstatus, which for a `ReplicationDestination` only checks the object exists — it does *not* wait for the restic restore to complete. Without the custom health check, the migration KS reports Ready as soon as the RD is applied, the app KS unblocks immediately, the main PVC clones from a still-empty source, and the app comes up with no data.

`ks.yaml` shape:

```yaml
---
# bucket KS as usual
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: <ns>-<app>-migration
spec:
  path: ./kubernetes/apps/<ns>/<app>/migration
  prune: true
  wait: true
  sourceRef: { kind: GitRepository, name: flux-system, namespace: flux-system }
  healthCheckExprs:
    - apiVersion: volsync.backube/v1alpha1
      kind: ReplicationDestination
      current: has(status.lastManualSync) && status.lastManualSync == 'restore-once'
      inProgress: has(status.conditions) && status.conditions.exists(c, c.type == 'Synchronizing' && c.status == 'True')
  postBuild:
    substitute:
      APP: <app>
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: <ns>-<app>
spec:
  path: ./kubernetes/apps/<ns>/<app>/app
  prune: true
  dependsOn:
    - name: <ns>-<app>-bucket
    - name: <ns>-<app>-migration
  # ... usual app substitutions
```

The `current` expression keys on `status.lastManualSync` — volsync only sets this field once the manual-trigger sync (`restore-once`) actually completes successfully. While restic is still running the `Synchronizing` condition is True; the `inProgress` expression keeps the KS in a clean reporting state during that window.

The shared restic credentials live in `cluster-secrets` as the `old-restic` Secret (keys: `RESTIC_REPOSITORY_BASE`, `RESTIC_PASSWORD`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`). Per-app migration pulls them via ExternalSecret and constructs the per-app restic env, appending `/${APP}` to the base path (matches the old cluster's per-app repo layout).

`migration/externalsecret.yaml`:

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: ${APP}-restic-migration
spec:
  refreshInterval: 1m
  secretStoreRef:
    kind: ClusterSecretStore
    name: cluster-secrets
  target:
    name: ${APP}-restic-migration
    creationPolicy: Owner
    template:
      type: Opaque
      data:
        RESTIC_REPOSITORY: '{{ .RESTIC_REPOSITORY_BASE }}/${APP}'
        RESTIC_PASSWORD: "{{ .RESTIC_PASSWORD }}"
        AWS_ACCESS_KEY_ID: "{{ .AWS_ACCESS_KEY_ID }}"
        AWS_SECRET_ACCESS_KEY: "{{ .AWS_SECRET_ACCESS_KEY }}"
  dataFrom:
    - extract:
        key: old-restic
```

`migration/replicationdestination.yaml`:

```yaml
apiVersion: volsync.backube/v1alpha1
kind: ReplicationDestination
metadata:
  name: ${APP}-migration
spec:
  trigger:
    manual: restore-once
  restic:
    repository: ${APP}-restic-migration
    copyMethod: Direct
    cleanupCachePVC: true
    enableFileDeletion: true
    capacity: <size>
    storageClassName: ssd-block
    accessModes: ["ReadWriteOnce"]
```

Restore completes → `lastManualSync` is set on the RD → `current` expression evaluates true → migration KS goes Ready → app KS unblocks → main PVC clones from temp PVC → app comes up populated. End-to-end automated *with* the health check; without it the chain races and the app gets an empty PVC (see above).

### Source ownership mismatch (lchown failures)

The volsync restic mover container drops all Linux capabilities by default. If the source-side restic snapshot recorded ownership the mover can't reproduce (e.g. files owned by `root` because the old-cluster app ran as uid 0), the restore will print

```
ignoring error for /some/path: lchown ...: operation not permitted
…
Fatal: There were 1 errors
```

and the RD stays `Synchronizing` forever — `lastManualSync` never gets set, the migration KS never goes Ready.

The clean escape hatch is an opt-in namespace annotation that volsync ships specifically for this:

```
kubectl annotate ns <ns> volsync.backube/privileged-movers=true
```

When that's present, the controller adds `CHOWN`, `FOWNER`, `DAC_OVERRIDE` to the mover container's capabilities and runs it as uid 0 — lchown succeeds, restore completes.

Treat this as **a one-shot manual step, not part of the committed pattern**: it's only needed to escape the *first* restore. The volsync component's PVC mounts the app pod with `fsGroup: 568` + `fsGroupChangePolicy: OnRootMismatch`, so on first mount kubelet recursively chgrps the volume contents to gid 568. From that point on the data ownership matches the LSIO default the ongoing-backup mover uses, and any future kopia-from-bucket restore via the volsync component works without elevated privileges. Removing the annotation after migration is fine but not required.

Apply via `kubectl`, don't bake into `namespace.yaml`. Keeps the privilege footprint visible and short-lived.

## Pattern B: rsync-TLS pre-sync

Used when pattern A's downtime is too long. rsync-TLS isn't literally continuous — the RD is a long-lived listener (Service + key Secret), and the RS on the source cluster runs the actual rsync as discrete push jobs on a schedule. Each push is incremental (rsync delta), so pre-sync at a tight cadence keeps the temp PVC close-to-current.

`migration/replicationdestination.yaml`:

```yaml
apiVersion: volsync.backube/v1alpha1
kind: ReplicationDestination
metadata:
  name: ${APP}-migration
spec:
  rsyncTLS:
    copyMethod: Direct
    serviceType: LoadBalancer
    capacity: <size>
    storageClassName: ssd-block
    accessModes: ["ReadWriteOnce"]
```

The RD generates a TLS key Secret (named `${APP}-migration-key` by default) on first apply. Grab its contents and hand-apply on the old cluster via `kubectl`.

On the **old cluster** (out-of-band — one-shot kubectl apply):

```yaml
apiVersion: volsync.backube/v1alpha1
kind: ReplicationSource
metadata:
  name: <app>-migration
spec:
  sourcePVC: <app>-on-old-cluster
  trigger:
    schedule: "*/15 * * * *"  # how often you want a delta push
  rsyncTLS:
    keySecret: <app>-migration-key
    address: <new-cluster-LB-ip>
    copyMethod: Snapshot
```

`dependsOn + wait: true` doesn't work for this — the listener never reports a clean "done" status. Two options for gating the app KS:

1. Ship the app KS with `spec.suspend: true`. At cutover: stop old app, manual-trigger final RS, then `suspend: false` in a commit.
2. Don't commit the app KS until cutover. Pre-sync runs against just `migration/`; commit the app KS when ready.

Option 2 is simpler.

### Cutover steps

1. Stop the app on the old cluster (scale to 0).
2. Manually trigger the final RS run: `kubectl patch -n <ns> rs <app>-migration --type merge -p '{"spec":{"trigger":{"manual":"final-sync"}}}'`. Wait for completion.
3. Commit the app KS (or flip `suspend: false`).
4. Main PVC clones from temp PVC. App comes up.

## Cleanup

Once the app is healthy on the new cluster:

1. Delete `migration/` directory.
2. Remove the migration KS doc from `ks.yaml`.
3. Remove the migration `dependsOn` from the app KS.
4. (Pattern B only) Tear down the old-cluster RS and the key Secret.

Leave the `dataSourceRef` patch in `app/kustomization.yaml` in place (see immutability note above). One-line comment is enough:

```yaml
# dataSourceRef is immutable post-creation; patch retained because reverting it errors.
```

## Tightly-coupled stacks

When a set of apps is only useful together — HA + zigbee2mqtt + MQTT broker is the canonical example — pre-sync optimization is wasted. While any one is down, the stack is effectively down (z2m's USB dongle has to physically move, automations don't fire, etc.). Just schedule a window:

1. Stop all coupled apps on the old cluster.
2. Move physical dependencies (zigbee dongle, etc.).
3. Run pattern A (restic restore) for each PVC in parallel — they're independent restic operations even though the apps are coupled.
4. Bring everything up on the new cluster.

For most coupled stacks the data is small enough that pattern A's window is short. Don't reach for pattern B unless an individual PVC in the stack is large.

## Gotchas summary

- `dependsOn + wait: true` is **not enough** to gate on one-shot movers — the KS goes Ready as soon as the RD is applied, not when restic finishes. The Pattern A KS needs the `healthCheckExprs` block above.
- `dependsOn + wait: true` doesn't work at all for long-running listeners (rsync-TLS) — they never report a clean "done" status. Use manual gating (suspend or commit-on-cutover).
- `dataSourceRef` is immutable — patches that set it stay in the manifest forever.
- PVC-to-PVC cloning requires the same StorageClass on source and destination.
- The old cluster's RS is out-of-band — neither cluster's GitOps fully owns the migration period.
- Source data owned by something the mover can't chown to (e.g. `root`) requires the namespace `volsync.backube/privileged-movers=true` annotation for the first restore. See above; one-shot manual application is preferred.
