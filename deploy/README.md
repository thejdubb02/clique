# Deploying muxpanel

```bash
cp deploy/muxpanel.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now muxpanel
```

Then publish it on the tailnet:

```bash
tailscale serve --bg --set-path /mux http://127.0.0.1:3200
```

`tailscale serve` strips the `/mux` prefix before the request reaches muxpanel,
so the server always thinks it is at the root. The browser knows better — see
the `<base href>` note at the top of `muxpanel/web/index.html`.

## Why it binds to loopback

The only way in is through `tailscale serve`, which means the tailnet, which
means an identity Tailscale has already checked. Binding to the tailnet IP
directly would also work, but loopback makes it impossible to reach any other
way by accident.

## The password

Generated on first install into `/root/.muxpanel/password` (0600) and mirrored
into Vaultwarden. Rotating it is: write a new value to that file and
`systemctl --user restart muxpanel`. Existing logins survive a restart — the
signing secret is separate, in `/root/.muxpanel/secret`.
