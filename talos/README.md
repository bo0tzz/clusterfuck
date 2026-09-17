# Talos

Machine config is committed as plain Talos documents and merged per node with
`talosctl machineconfig patch`, later layers winning:

| Layer                    | Applies to                                                          |
| ------------------------ | ------------------------------------------------------------------- |
| `cluster.yaml`           | every node                                                          |
| `controlplane.yaml`      | control-plane nodes                                                 |
| `talsecret.sops.yaml`    | control-plane nodes: the `talosctl gen secrets` bundle, reshaped into a patch by `secrets.yq` |
| `nodes/<node>.yaml`      | one node: install disk and image, hostname, link alias, address     |
| `schematics/<node>.yaml` | the Image Factory customization behind that node's installer image  |

`machine.ca` and `cluster.ca` merge as a cert-plus-key unit: a layer that supplies only `key`
blanks `crt`, so both always live in the same file.
