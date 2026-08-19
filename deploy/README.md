# Deploying CLIque

Bind it to loopback. Put a tunnel you already trust in front of it. Anyone who
reaches the panel has a terminal as the user that started the process.

```bash
export CLIQUE_HOME="$HOME/.clique"
python3 -m clique password
python3 -m clique                 # 127.0.0.1:3200
```

## systemd user unit

```bash
cp deploy/clique.service ~/.config/systemd/user/
# edit the unit: WorkingDirectory, CLIQUE_HOME, the python you actually run
systemctl --user daemon-reload
systemctl --user enable --now clique
```

The unit in this folder is a starting point, not a drop-in for every box.
Paths and the user will be yours.

## Tailscale Serve

```bash
tailscale serve --bg --set-path /clique http://127.0.0.1:3200
```

`tailscale serve` strips the `/clique` prefix before the request reaches
CLIque, so the server always thinks it is at the root. The browser knows
better — see the `<base href>` note at the top of `clique/web/index.html`.

Caddy or nginx in front of `127.0.0.1:3200` is the same idea. Terminate TLS
there. Do not bind CLIque itself to `0.0.0.0`.

## The password

`python3 -m clique password` writes an scrypt hash to `$CLIQUE_HOME/password`
(mode 0600). The plaintext never touches that file. Rotating it is: run the
same command again and restart the process. Existing logins survive a restart
— the signing secret is separate, in `$CLIQUE_HOME/secret`.
