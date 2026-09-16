# Ansible deployment example

This directory is a small, roleless Ansible deployment that can be copied into
an existing infrastructure repository. It deploys the Factorio project to a
Linux host under the dedicated `factorio` user, using `/home/factorio` as that
user's home and the Compose install directory.

## Copy and adapt

Copy `ansible/` into your deployment repository and adjust these values in
`defaults/main.yaml`:

```yaml
factorio_version: "2.0.77"
factorio_port: 34197
```

The Dockerfile, Compose file, server defaults, entrypoint, and backup script are
bundled in `files/`. The deployment does not need a separate Factorio
checkout or a `factorio_source_dir` variable.

Copy the examples, then edit the host and variables:

```bash
cp ansible/inventory.example.yml ansible/inventory.yml
$EDITOR ansible/inventory.yml ansible/deploy_factorio.yml
ansible-playbook -i ansible/inventory.yml ansible/deploy_factorio.yml --ask-become-pass
```

The target needs SSH access, Python, Docker Engine, and the Docker Compose
plugin. The playbook does not install Docker or other host prerequisites, so
your infrastructure repo can keep its own installation policy.

## What it does

The playbook creates the `factorio:factorio` service account with
`/home/factorio` as its home (UID/GID 845 to match the container),
copies the bundled project there, keeps runtime data in
`/home/factorio/data`, renders `.env`, builds/starts Compose, and enables
`factorio.service` at boot.

The first run creates the game password in
`/home/factorio/data/config/server-settings.json`; retrieve it on the target with:

```bash
sudo jq -r .game_password /home/factorio/data/config/server-settings.json
```

## Files

| File | Use |
| --- | --- |
| `inventory.example.yml` | Inventory starting point |
| `deploy_factorio.yml` | Main deployment playbook |
| `defaults/main.yaml` | Deployment variables |
| `templates/factorio.env.j2` | Runtime `.env` generated on the target |
| `templates/factorio.service.j2` | systemd unit for Compose |
| `tasks/main.yml` | Deployment task list imported by the playbook |
| `handlers/main.yml` | Service restart handler list |
| `files/` | Bundled Docker/Compose application files |

RCON is deliberately not published; use `docker compose exec factorio rcon ...`
on the host.

To deploy a new image version, update `factorio_version`, run the backup script,
then rerun the playbook. The deployed service rebuilds with the requested pinned
version.
