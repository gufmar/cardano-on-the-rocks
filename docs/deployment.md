# Deployment and updates

## Separation of clones

| Role | Typical path | Purpose |
|---|---|---|
| Development | `/home/mgu/git/cardano-on-the-rocks` (RockPi 2) | Active editing; not deleted by project reset |
| Deploy | `/opt/cot-r/src` | Device runtime source of truth for install/update |
| Runtime install | `/opt/cot-r/display`, `/opt/cot-r/mgmt` | Copied trees executed by systemd |

Shared setup code is identical across devices. Device identity and secrets stay in
`/etc/cot-r/` and are never committed.

## Users

| User | Role |
|---|---|
| `cot-r-display` | OLED service (`cot-r-display.service`) |
| `cot-r` | Management API (`cot-r-mgmt.service`); owns deploy home |

Administrative users (for example `mgu`) are untouched.

## First-time prepare

```bash
sudo ./scripts/cot-r prepare
```

This creates users, API token (`/etc/cot-r/api.token`), device id, runtime installs,
systemd units, and the deploy clone (cloned from the preparing tree / configured remote).

Remote URL is taken from `git remote get-url origin` when available:
`https://github.com/gufmar/cardano-on-the-rocks.git`

## Update

```bash
sudo ./scripts/cot-r update           # track current branch tip from origin
sudo ./scripts/cot-r update v0.2.0    # pin to tag/commit/branch
sudo ./scripts/cot-r update --force   # only if deploy clone has local edits you accept discarding
```

Behaviour:

1. Refuses to discard local modifications in `/opt/cot-r/src` unless `--force`.
2. Fetches `origin`, checks out the requested ref.
3. Installs `display/` and `mgmt/` into `/opt/cot-r/`.
4. Restarts project units.
5. Records commit metadata in `/var/lib/cot-r/deploy.json` including `previous_commit`.

## Rollback

```bash
sudo ./scripts/cot-r rollback
```

Checks out the recorded `previous_commit` and reinstalls. Only one previous generation
is retained in `deploy.json`.

## Version / commit reporting

- File `VERSION` in the tree (shown by `cot-r version`).
- `GET /api/v1/status` includes `deploy.commit`, `deploy.ref`, `deploy.deployed_at`.
- `GET /api/v1/about` includes licence and source URL for attribution.

## State schema

Management durable state: `/var/lib/cot-r/mgmt-state.json` (schema version field).
Incompatible or corrupt state falls back to `Recovery` / `Initial` without erasing
device identity or API token.

Future schema migrations must be explicit and version-gated; until then, unknown
fields are ignored and invalid documents are replaced carefully with Recovery notes.

## Secrets

| File | Contents |
|---|---|
| `/etc/cot-r/api.token` | Bearer token for management API |
| `/etc/cot-r/device.id` | Stable device UUID |
| `/etc/cot-r/device.env` | Non-secret device labels / reserved IP hints |
| future AP / RouterOS secrets | Separate files under `/etc/cot-r/` |

Never commit these files.
