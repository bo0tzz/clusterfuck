# postgres component

CNPG Cluster + barman-cloud ObjectStore + scheduled/initial backups + the secret needed to talk to the old cluster at `192.168.4.105`.

## Variables

Required via `postBuild.substitute` (or substituteFrom):

| var | from | notes |
| --- | --- | --- |
| `APP` | per-app | used for Cluster/ObjectStore/Backup names and DB/owner |
| `BUCKET` | per-app | name of the bucket Secret produced by the bucket component |
| `BUCKET_NAME`, `BUCKET_ENDPOINT` | substituteFrom the bucket Secret | wired into ObjectStore |

Optional with defaults: `PG_IMAGE`, `PG_STORAGE`, `PG_STORAGECLASS`, `PG_CPU_REQ`, `PG_MEM_REQ`, `PG_MEM_LIMIT`, `PG_SCHEDULE`, `PG_RETENTION`.

## Dependencies

- bucket component must run first (this component `substituteFrom`s its Secret). Use the two-stage Flux KS pattern (`bo0kkeeper/ks.yaml` is the reference).
- `cnpg-barman-cloud` plugin: installed once cluster-wide. `dependsOn` is recommended because the ObjectStore is a `barmancloud.cnpg.io/v1` resource — without the CRD the whole KS apply fails until the plugin lands.
- `pg-migrator` entry in `cluster-secrets`: only required for apps using the migration bootstrap. Otherwise the ExternalSecret sits in `SecretSyncError` but nothing else cares — the import branch gets patched out.

## Bootstrap modes

The component ships with `bootstrap.initdb` (with `import`) **and** `bootstrap.recovery` both defined. CNPG's webhook only allows one, so every consumer **must** patch out the unused branches. Pick one of the three modes below and add the matching patch to the app's `kustomization.yaml` `patches:` block (not Flux KS `spec.patches` — that gets clobbered by `cluster-apps`).

### Fresh deploy (new database)

```yaml
patches:
  - target:
      group: postgresql.cnpg.io
      kind: Cluster
    patch: |-
      - op: remove
        path: /spec/bootstrap/recovery
      - op: remove
        path: /spec/bootstrap/initdb/import
```

### Migration from old cluster

Keep `initdb.import`, drop `recovery`, replace the `placeholder` DB name:

```yaml
patches:
  - target:
      group: postgresql.cnpg.io
      kind: Cluster
    patch: |-
      - op: remove
        path: /spec/bootstrap/recovery
      - op: replace
        path: /spec/bootstrap/initdb/import/databases
        value: [actual_db_name]
```

Once the import has completed and a barman backup has been confirmed, flip the patch to the recovery form (see below) — it's inert on running clusters but means a disaster automatically re-bootstraps from B2.

### Recovery from barman-cloud backups (recommended steady-state)

Drop the entire `initdb` branch (with import) — `bootstrap.recovery` stays and points at the `backups` externalCluster which uses the barman-cloud plugin:

```yaml
patches:
  - target:
      group: postgresql.cnpg.io
      kind: Cluster
    patch: |-
      - op: remove
        path: /spec/bootstrap/initdb
```

This is the recommended long-term state for every app: bootstrap is only consulted on initial cluster creation, so on a running cluster this patch is inert. If something ever eats the PVCs (storage failure, accidental delete, fresh-namespace rebuild), Flux re-creates the Cluster and recovery fires automatically from B2.

Notes:

- Flip to this only after the `${APP}-init` Backup completes — that's a valid basebackup with WAL archiving wired, sufficient for recovery. Flipping earlier means a disaster recreate has nothing to pull from.
- The `backups` externalCluster references `barmanObjectName: ${APP}` / `serverName: ${APP}` — renaming the app breaks this path, you'd need to override both.
- Point-in-time recovery: add `recoveryTarget` under `bootstrap.recovery` (see CNPG docs for `targetTime`, `targetLSN`, etc.). The component intentionally doesn't pre-bake one — it's a one-off rather than a steady-state.
- Recovery only fires when there's no existing data. If the Cluster CR is recreated but PVCs are still there, CNPG reattaches and ignores recovery.

## Other notes

- The component sets `synchronous: { method: any, number: 1, dataDurability: required }`. For single-instance apps, patch to `instances: 1` and **also** strip the `postgresql.synchronous` block — `dataDurability: required` with no replica will block writes forever. The CNPG webhook will reject `number >= instances`, so a forgotten strip surfaces fast.
- `data.compression: gzip` (not zstd — barman only accepts bzip2/gzip/snappy for data; WAL takes zstd).
- CNPG auto-creates a `<APP>-app` Secret with a `uri` key — that's what the consumer HelmRelease should pull its DB URL from.
