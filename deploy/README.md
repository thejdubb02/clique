# Deploying CLIque

```bash
cp deploy/clique.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now CLIque
```

Then publish it on the tailnet:

```bash
tailscale serve --bg --set-path /clique http://127.0.0.1:3200
```

`tailscale serve` strips the `/clique` prefix before the request reaches CLIque,
so the server always thinks it is at the root. The browser knows better — see
the `<base href>` note at the top of `clique/web/index.html`.

## Why it binds to loopback

The only way in is through `tailscale serve`, which means the tailnet, which
means an identity Tailscale has already checked. Binding to the tailnet IP
directly would also work, but loopback makes it impossible to reach any other
way by accident.

## The password

Generated on first install into `/root/.clique/password` (0600) and mirrored
into Vaultwarden. Rotating it is: write a new value to that file and
`systemctl --user restart clique`. Existing logins survive a restart — the
signing secret is separate, in `/root/.clique/secret`.
