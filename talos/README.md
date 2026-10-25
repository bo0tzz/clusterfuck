# Talos

Machine config is committed as plain Talos documents and merged per node with
`talosctl machineconfig patch`, later layers winning. Control planes and workers
get different layers:

| Layer                    | Applies to                                                          |
| ------------------------ | ------------------------------------------------------------------- |
| `cluster.yaml`           | every node                                                          |
| `controlplane.yaml`      | control-plane nodes                                                 |
| `talsecret.sops.yaml`    | the `talosctl gen secrets` bundle, reshaped into a patch: `secrets.yq` for control planes, `secrets-worker.yq` (no CA keys, etcd, service-account or aggregator secrets) for workers |
| `nodes/<node>.yaml`      | one node: `machine.type`, install disk and image, hostname, link alias, address |
| `schematics/<node>.yaml` | the Image Factory customization behind that node's installer image  |

`machine.type` in `nodes/<node>.yaml` selects the layers: `controlplane` pulls in
`controlplane.yaml` and the full secret bundle, anything else gets the worker subset.

`machine.ca` and `cluster.ca` merge as a cert-plus-key unit: a layer that supplies only `key`
blanks `crt`, so both always live in the same file.
