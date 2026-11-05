# Deploying on Render

Cipher Vault key generation is automatic on Render. Configure `BOT_TOKEN`, `OWNER_ID`, and the private vault repository/token in the service Variables tab. Do not set `CIPHER_VAULT_KEY` for a new installation; the bot generates and stores it in its encrypted keyring.

Render services use an ephemeral filesystem by default. Attach a paid Render persistent disk and mount it at the application storage path. For this Dockerfile, use `/app/storage`. If deploying with Render's native Python runtime, mount the disk at the absolute `storage` path under the service source directory instead. Only files under the disk mount survive deploys and restarts.

Keep one service instance when using a disk-backed bot. After attaching the disk, deploy the service and configure the vault token. The first successful vault sync creates the first automatically managed snapshot. Set `CIPHER_VAULT_KEY` only when importing an existing encrypted vault whose original key is available.

Render's native runtimes include Node.js and npm. With Sandbox enabled but no
Docker daemon or remote worker available, Cipher Bot Hosting can run
owner-owned or administrator-approved Node.js bots directly in the Render
service. This is host-native execution rather than a security sandbox; users'
unapproved code remains blocked unless Docker Sandbox or a remote worker is
configured.

Unapproved bots are never allowed to run natively beside the control plane.
Multi-user hosting requires Docker Sandbox or a configured remote worker.

See the [Render persistent disk documentation](https://render.com/docs/disks) for disk setup and limitations.
