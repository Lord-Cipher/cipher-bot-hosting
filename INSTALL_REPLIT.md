# Deploying on Replit

Cipher Vault key generation is automatic on Replit. Configure `BOT_TOKEN`, `OWNER_ID`, and the private vault repository/token as Secrets. Do not set `CIPHER_VAULT_KEY` for a new installation; the bot generates and stores it in its encrypted keyring.

The keyring and panel state must survive restarts. Keep the project workspace persistent for the running app, or configure a persistent storage solution and point the deployment at the directory containing the bot's `storage/` folder. Replit App Storage is private cloud object storage; it is not automatically mounted as a local filesystem, so using it requires a separate storage integration rather than only adding a Secret.

Use one running deployment for a polling bot. After the first deployment starts, configure the vault token and run the first sync. The automatically generated key is then reused for later syncs and restores on that persistent deployment. Set `CIPHER_VAULT_KEY` only when importing an existing encrypted vault whose original key is available.

Replit can provide Node.js through its native environment/Nix configuration.
When Docker is unavailable, owner-owned or administrator-approved Node.js bots
may run directly with that host runtime. This mode is not an isolation
boundary, so unapproved user code remains blocked unless Docker Sandbox or a
remote worker is available.

See the [Replit App Storage documentation](https://docs.replit.com/features/data-and-storage/object-storage) for persistent object storage options.
