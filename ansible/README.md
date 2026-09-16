# Ansible deployment example

This directory is a small, roleless Ansible deployment that can be copied into
an existing infrastructure repository. It deploys the Factorio project to a
Linux host under the dedicated `factorio` user, using `/home/factorio` as that
user's home and the Compose install directory.

## Copy and adapt

Copy `ansible/` into your deployment repository and adjust these values in
`group_vars/factorio.yml`:

```yaml
factorio_source_dir: "{{ playbook_dir }}/../.."
factorio_version: "2.0.77"
factorio_port: 34197
```

`factorio_source_dir` is resolved on the Ansible controller. Point it at the
Factorio repository checkout (or the matching checkout in your deployment repo).
The example assumes this `ansible/` directory remains inside the Factorio repo.

Copy the examples, then edit the host and variables:

```bash
cp ansible/files/inventory.example.yml ansible/files/inventory.yml
cp ansible/group_vars/factorio.yml.example ansible/group_vars/factorio.yml
$EDITOR ansible/files/inventory.yml ansible/group_vars/factorio.yml
ansible-galaxy collection install -r ansible/files/requirements.yml
ansible-playbook -i ansible/files/inventory.yml ansible/files/deploy_factorio.yml --ask-become-pass
```

The target needs SSH access, Python, Docker Engine, and the Docker Compose
plugin. The playbook checks those prerequisites; it does not install Docker so
your infrastructure repo can keep its own Docker installation policy.

## What it does

The playbook verifies x86-64 and Compose, creates the `factorio:factorio` service
account with `/home/factorio` as its home (UID/GID 845 to match the container),
stages and installs the project there, keeps runtime data in
`/home/factorio/data`, renders `.env`, optionally
opens the game UDP port through UFW, builds/starts Compose, and enables
`factorio.service` at boot.

The first run creates the game password in
`/home/factorio/data/config/server-settings.json`; retrieve it on the target with:

```bash
sudo jq -r .game_password /home/factorio/data/config/server-settings.json
```

## Files

| File | Use |
| --- | --- |
| `files/deploy_factorio.yml` | Main deployment playbook |
| `files/inventory.example.yml` | Inventory starting point |
| `group_vars/factorio.yml.example` | Deployment variables |
| `templates/factorio.env.j2` | Runtime `.env` generated on the target |
| `templates/factorio.service.j2` | systemd unit for Compose |
| `tasks/main.yml` | Deployment task list imported by the playbook |
| `handlers/main.yml` | Service restart handler list |
| `files/requirements.yml` | Collections used by the playbook |

Set `factorio_manage_firewall: true` only when this host uses UFW and this
playbook should manage the rule. Cloud security groups remain outside this
example. RCON is deliberately not published; use `docker compose exec factorio
rcon ...` on the host.

To deploy a new image version, update `factorio_version`, run the backup script,
then rerun the playbook. The deployed service rebuilds with the requested pinned
version.
