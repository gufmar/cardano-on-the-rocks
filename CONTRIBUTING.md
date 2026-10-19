# Contributing

Thank you for interest in **Cardano on the Rocks**.

## Source and attribution

- Canonical repository: https://github.com/gufmar/cardano-on-the-rocks
- Copyright holder: Markus (GitHub: [gufmar](https://github.com/gufmar))
- Licence: [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0)

Please preserve copyright and licence notices in source files you modify.
Include attribution to the original project and a link to the source repository
in documentation and any application About page.

## Licence expectations

This project uses AGPL-3.0. That means:

- You may use, study, modify, and redistribute the software under AGPL-3.0.
- If you convey a modified version, you must provide corresponding source under AGPL-3.0.
- If users interact with a modified version over a network, AGPL section 13 requires
  offering them corresponding source for that version.

AGPL does **not** require contributors to open pull requests against this repository.
Contributions back here are welcome and encouraged, but they are a community norm,
not a licence obligation. See [docs/licensing.md](docs/licensing.md).

## How to contribute

1. Open an issue describing the problem or proposal when practical.
2. Fork or branch from `main`.
3. Keep changes focused; match existing shell and Python style.
4. Prefer idempotent provisioning changes with inspection-only modes.
5. Do not commit secrets, device credentials, private keys, or local runtime state.
6. Document reboot requirements and recovery implications.
7. Open a pull request with a short summary and test notes.

## Development on RockPi hardware

Development currently happens on RockPi 2. Do not disrupt Ethernet, SSH, or existing
administrative users while testing preparation scripts.

Useful entry points:

```bash
./scripts/cot-r status
sudo ./scripts/cot-r prepare
sudo ./scripts/cot-r reset-project
```

See [docs/provisioning.md](docs/provisioning.md).

## Security and secrets

Deployment secrets (Wi-Fi passwords, RouterOS credentials, device identity keys)
belong outside this public repository, typically under `/etc/cot-r/` on each device
with restrictive permissions.
