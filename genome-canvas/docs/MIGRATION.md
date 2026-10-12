# Server deployment and migration

This guide moves the application, configuration, workspace catalog, Favorites
and shared sessions. Genomic tracks may stay on shared storage if the new server
mounts the same paths.

## 1. Prepare the new server

Install Python 3.8+ and clone the repository:

```bash
git clone https://github.com/QuertermousLab/genome-canvas.git /opt/genome-canvas
cd /opt/genome-canvas
cp genomecanvas.config.example.json genomecanvas.config.json
```

Edit the configuration and verify every `dataRoots` path exists and is readable
by the service account. Configure `homeRoot`, `dbSnpHg38` and the optional PLINK
LD reference for the new filesystem layout.

## 2. Copy runtime state

Stop the old service before copying SQLite state:

```bash
sudo systemctl stop genome-canvas
rsync -a OLD_SERVER:/path/to/genome-canvas/.genomecanvas/ /opt/genome-canvas/.genomecanvas/
```

The directory contains:

- `workspaces.sqlite3`: manual workspaces and hidden Home workspaces
- `profiles/`: workspace-isolated Favorites
- `sessions/`: share-link state

Do not publish this directory. If saved track URLs encode old data-root IDs or
paths, update the corresponding profile JSON files or reload and resave those
tracks after migration.

## 3. Install as a system service

Create a dedicated account and install the example files:

```bash
sudo useradd --system --home /opt/genome-canvas --shell /usr/sbin/nologin genome-canvas
sudo cp deploy/genome-canvas.env.example /etc/genome-canvas.env
sudo cp deploy/genome-canvas.service.example /etc/systemd/system/genome-canvas.service
sudo chown -R genome-canvas:genome-canvas /opt/genome-canvas/.genomecanvas
sudo systemctl daemon-reload
sudo systemctl enable --now genome-canvas
curl http://127.0.0.1:8000/api/health
```

Adjust `User`, `Group`, `WorkingDirectory`, `ExecStart` and the environment file
when the repository is installed somewhere other than `/opt/genome-canvas`.

## 4. Add a reverse proxy

Copy `deploy/nginx.conf.example` into the Nginx site configuration, replace the
example hostname, validate the configuration, and reload Nginx. Add your normal
LAN authentication policy if workspace selection must not be publicly visible.

For a subpath deployment, preserve the prefix when proxying and enter that full
URL in the macOS app, for example:

```text
http://genome-canvas.example.internal/genome-canvas/
```

## 5. Validate

1. Select or create a workspace.
2. Open each configured data root and load an indexed file.
3. Restore a Favorite and verify track URLs and indexes.
4. Search a gene and an rsID.
5. Test a Hi-C track, a GWAS track, PNG export and a share link.
6. Build the macOS client and set its server address to the new URL.

Keep a backup of `genomecanvas.config.json` and `.genomecanvas/`. Track files
should follow the backup policy of the underlying sequencing storage.
