# Factorio server operations

This guide assumes the Ansible deployment described in `ansible/README.md`.
Run the commands on the Factorio host, using an account with `sudo` access.

## Important paths

| Purpose | Path |
| --- | --- |
| Compose installation | `/home/factorio` |
| Persistent runtime data | `/home/factorio/data` |
| World save files | `/home/factorio/data/saves` |
| Server configuration | `/home/factorio/data/config` |
| Local backup archives | `/home/factorio/backups` |
| systemd service | `factorio.service` |

The game uses UDP port `34197` by default. A server restart does not change the
world. A world reset or a newly generated world does.

## Check the server

```bash
sudo systemctl status factorio --no-pager
cd /home/factorio
sudo docker compose ps
sudo docker compose logs --tail=100 factorio
```

Follow the live server log with:

```bash
cd /home/factorio
sudo docker compose logs -f factorio
```

## Restart the server, keeping the current world

Use this after changing server configuration or when the server is behaving
strangely. It leaves all saves untouched.

```bash
sudo systemctl restart factorio
sudo systemctl status factorio --no-pager
```

To stop and start it separately:

```bash
sudo systemctl stop factorio
sudo systemctl start factorio
```

## Create a backup

Stop the server first so the archive contains a consistent copy of the runtime
data. This backs up saves, configuration, and any other persistent Factorio
data.

```bash
sudo install -d -o factorio -g factorio -m 0750 /home/factorio/backups
sudo systemctl stop factorio
sudo tar -C /home/factorio \
  -czf "/home/factorio/backups/factorio-data-$(date +%Y%m%d-%H%M%S).tar.gz" \
  data
sudo systemctl start factorio
sudo systemctl status factorio --no-pager
```

List available backups:

```bash
sudo ls -lh /home/factorio/backups
```

These archives are on the same disk as the server. They are useful for trying
different worlds, but do not protect against loss of the host itself.

## Restore a specific backup

Replace `BACKUP_FILE` below with the archive you want, for example
`factorio-data-20260916-203000.tar.gz`.

First inspect the archive:

```bash
sudo tar -tzf "/home/factorio/backups/BACKUP_FILE" | head -30
```

Then restore it. The current data directory is moved aside instead of deleted,
so the restore can be undone if necessary.

```bash
sudo systemctl stop factorio
sudo mv /home/factorio/data \
  "/home/factorio/data.before-restore-$(date +%Y%m%d-%H%M%S)"
sudo tar -C /home/factorio \
  -xzf "/home/factorio/backups/BACKUP_FILE"
sudo chown -R factorio:factorio /home/factorio/data
sudo systemctl start factorio
sudo systemctl status factorio --no-pager
```

At this point the server is running the world and configuration from that
backup. Connect with the same Factorio version used to create the save.

## Reset the world

This removes the active save from the server's save directory while preserving
the old directory as a rollback copy. It does not remove the server password
or other configuration.

```bash
sudo systemctl stop factorio
sudo mv /home/factorio/data/saves \
  "/home/factorio/data/saves.before-reset-$(date +%Y%m%d-%H%M%S)"
sudo install -d -o factorio -g factorio -m 0750 /home/factorio/data/saves
sudo systemctl start factorio
sudo systemctl status factorio --no-pager
```

Whether an empty saves directory automatically creates a new world depends on
the Compose command and image configuration. If it does not, inspect the
configured create/start command:

```bash
cd /home/factorio
grep -nE 'SAVE|CREATE|START|factorio' compose.yaml .env 2>/dev/null
```

Use the same world-creation command defined there, then check the logs for the
new save name.

## Change the world

There are three common cases:

### Return to an earlier world

Restore the corresponding `factorio-data-*.tar.gz` archive using the restore
procedure above.

### Start a new world with different map generation

Map-generation settings only affect a newly created world; changing them does
not alter an existing save.

1. Stop the server.
2. Edit `/home/factorio/data/config/map-gen-settings.json`.
3. Move the current `/home/factorio/data/saves` directory aside as shown in
   the reset procedure.
4. Create/start the new world using the command configured in `compose.yaml`.
5. Start the service and verify the new world in the logs.

Keep `server-settings.json` unless you intentionally want to change server
name, description, visibility, password, or related settings.

### Switch to a separate save file

Copy the save ZIP into the saves directory, then select that save using the
save-name option already defined by the deployment's `compose.yaml` or `.env`:

```bash
sudo cp /path/to/world.zip /home/factorio/data/saves/
sudo chown factorio:factorio /home/factorio/data/saves/world.zip
cd /home/factorio
grep -nE 'SAVE|START|factorio' compose.yaml .env 2>/dev/null
sudo systemctl restart factorio
```

Do not change map-generation settings expecting them to affect this existing
save; they are only read when a new map is generated.

## Quick recovery checklist

When an AI experiment damages the world:

1. Stop the server if it is still running.
2. Reset the saves directory, or restore a chosen backup.
3. Start the service.
4. Check `systemctl status` and the Compose logs.
5. Reconnect using the same server address and Factorio version.

