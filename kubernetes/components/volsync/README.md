# volsync component

Per-app kopia backup of a PVC to the app's bucket. Ships a `ReplicationSource` (scheduled backup), `ReplicationDestination` (manual restore trigger), the app PVC itself (with `dataSourceRef` pointing at the RD so first apply auto-restores if a snapshot exists), and an ExternalSecret that assembles the kopia config from the bucket Secret + the shared `KOPIA_PASSWORD`.

## Variables

| var | required | default |
| --- | --- | --- |
| `APP` | yes | — |
| `BUCKET` | yes | — |
| `VOLSYNC_CAPACITY` | no | `5Gi` |
| `VOLSYNC_STORAGECLASS` | no | `ssd-block` |
| `VOLSYNC_ACCESSMODES` | no | `ReadWriteOnce` |
| `VOLSYNC_SCHEDULE` | no | `0 3 * * *` |
| `VOLSYNC_CACHE_CAPACITY` | no | `2Gi` |
| `VOLSYNC_COPY_METHOD` | no | `Snapshot` |
| `VOLSYNC_PUID` / `VOLSYNC_PGID` | no | `568` (LSIO default) |

## Dependencies

- bucket component for the app (same two-stage Flux KS pattern as postgres — RS/RD substitute from the bucket-derived Secret via the kopia ExternalSecret).
- `cluster-secrets` namespace's `kopia` Secret must contain `KOPIA_PASSWORD` (shared cluster-wide).
- `eso-kubernetes-local` component on the namespace for the local SecretStore.

## Restore semantics

First apply of the PVC has `dataSourceRef: ReplicationDestination/${APP}-dst`. The RD's `trigger.manual: restore-once` means it'll attempt exactly one restore. Outcomes:

- **No prior backup**: kopia repo is empty, restore yields an empty PVC. Consumer comes up fresh.
- **Prior backup exists**: PVC populated from the latest snapshot matching `sourceIdentity.sourceName: ${APP}`.
- **PVC already exists**: dataSourceRef is ignored (only used at PVC creation). No-op.

To force a fresh restore: delete the PVC + RD, then let Flux re-create.

## Notes

- `sourceIdentity.sourceName: ${APP}` on the RD must match the RS's identity — kopia tags snapshots by `<source>@<host>` and a mismatch silently restores nothing. The component wires both correctly; don't override one without the other.
- Compression is `zstd-fastest` on the RS — good ratio without CPU cost. Kopia chunking + dedup happens regardless.
- `retain: hourly: 24, daily: 7` — local retention only, not lifecycle on the bucket side.
- The RD uses `cleanupTempPVC: true` + `enableFileDeletion: true`. The temp PVC is just for the restore mover; the cache PVC for the RS stays around (warm cache between runs).
- Per-PVC backup, not per-app. An app with multiple PVCs needs the component included multiple times with distinct `APP` substitutions (or different naming) — there's no built-in multi-PVC handling.
- `VOLSYNC_COPY_METHOD` controls how the RS prepares the source for kopia. Default `Snapshot` takes a CSI VolumeSnapshot of the source PVC and provisions a temp PVC from it — on RBD this is CoW and near-instant, but on **CephFS the snapshot-to-PVC step is a full RADOS object copy**, which makes every scheduled run take as long as the original restore. For large RWX CephFS volumes (immich-scale photo libraries) use `Direct`: the mover mounts the live source PVC directly. Kopia handles concurrent writes at file granularity, so brief inconsistency on in-flight files is benign — they get caught next run.
