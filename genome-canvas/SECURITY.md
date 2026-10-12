# Security policy

Genome Canvas is intended for a trusted local network. Its password-free
workspace selector separates preferences and Favorites; it is not an access
control boundary. Any user who can reach the application may select another
workspace and read supported tracks exposed through that workspace's configured
roots.

Do not expose the backend directly to the public internet. Use a firewall, VPN,
or an authenticated reverse proxy and grant the service account read access only
to the required genomic data.

Never commit `genomecanvas.config.json`, `.genomecanvas/`, track data, TLS keys,
credentials, logs, or user Home contents. These paths are excluded by the
repository defaults.

To report a vulnerability, use GitHub's private security advisory feature for
the repository instead of opening a public issue with sensitive details.
