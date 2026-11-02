# In Docker

The `Dockerfile` at the top of the checkout builds an image you run instead
of installing. It carries all three ways to use mailctl: the command line,
the API, and the terminal UI. Nothing is published, so build it yourself:

```bash
make image                          # or: docker build -t mailctl:dev .
docker run --rm --security-opt no-new-privileges \
  --user "$(id -u):$(id -g)" mailctl:dev --version
```

A run that talks to the server needs two things from the host: your config
directory, and your password in a file. This is the one pattern to use:

```bash
docker run --rm \
  --security-opt no-new-privileges \
  --user "$(id -u):$(id -g)" \
  -v "$HOME/.config/mailctl:/config/mailctl" \
  -v "$HOME/.config/mailctl/password:/run/secrets/mailctl-password:ro" \
  -e MAILCTL_PASSWORD_FILE=/run/secrets/mailctl-password \
  mailctl:dev server test
```

- **The config directory is a mount, and the image refuses to run without
  it.** It holds `config.toml`, the baselines, and the backup mailctl writes
  before every change to a filter set. A backup written inside the
  container is gone when the container is, so any command other than
  `--help`, `--version`, and `help` stops with exit 2 unless
  `/config/mailctl` is a mount; a help word after `--` does not count. That
  holds for `api` and `tui` too (below), where only `--help` or `-h` given
  alone, and `--version` alone for `api`, run without it. A backup anywhere
  outside that mount is refused too, whatever set it: `--backup-dir`,
  `MAILCTL_BACKUP_DIR` in an env file or the environment, `backup_dir` in
  `config.toml`, or `filterset backup --output`. That refusal is mailctl's
  own, through `MAILCTL_BACKUP_ROOT`, which the image sets to
  `/config/mailctl` and a `docker run -e` cannot change. The path is
  resolved first, so `..` or a symlink inside the mount cannot lead out of
  it.
- **`--user "$(id -u):$(id -g)"` is what makes the password file readable.**
  The file's mode survives the mount, so a `0600` file is still `0600`
  inside and passes mailctl's mode check. What breaks without `--user` is
  ownership: the file belongs to your uid, the image's default user has a
  different one, and it cannot read the file at all. The image also refuses
  to run as root, which would let a backup land anywhere.
- **`--security-opt no-new-privileges` stops anything in the container
  gaining privileges**, such as through a setuid program like `su`; mailctl
  never needs to, so it costs nothing on top of running as your own user.
- **Use the password file, not a command or the prompt.** `--password-cmd`
  runs its command inside the container, where your password manager does
  not exist. The interactive prompt has no terminal under a plain
  `docker run`; `docker run -it` gives it one, which is fine by hand and
  useless from cron. `MAILCTL_PASSWORD_FILE` outranks a `password_file` or
  `password_cmd` in `config.toml`, so one `config.toml` serves both a
  checkout and the image.
- **On WSL or Docker Desktop, keep the password file on the Linux
  filesystem.** A file on a Windows drive (`/mnt/c/...`, or a Windows path
  shared into Docker Desktop) shows mode `0777` whatever you set, and
  mailctl refuses it. That refusal is deliberate and has no exception: put
  the file under your Linux home and `chmod 600` it there.

**The first word picks what runs**: `tui` runs the terminal UI, `api` runs
the API, and anything else goes to the command line, as above. Neither word
is a mailctl command. Each takes the same mounts and settings, and every
refusal above holds for each.

```bash
docker run --rm -it \
  --security-opt no-new-privileges \
  --user "$(id -u):$(id -g)" \
  -v "$HOME/.config/mailctl:/config/mailctl" \
  -v "$HOME/.config/mailctl/password:/run/secrets/mailctl-password:ro" \
  -e MAILCTL_PASSWORD_FILE=/run/secrets/mailctl-password \
  mailctl:dev tui
```

- **The terminal UI needs both `-i` and `-t`.** Without `-t` the image
  refuses it, exit 2. With `-t` alone it opens but takes no keys, so it
  cannot even be quit; the image cannot tell, so give both. It starts the
  API inside the same container and works through it, so it reaches the
  server exactly as the command line does, and its `--api` option is
  refused. Without a password file it asks for the password in a masked
  field. `q` quits.
- **The API is for a program outside the container.** Run the same command
  with `-i` in place of `-it` and `api` in place of `tui`; the API reads
  one JSON-RPC message per line on stdin and answers on stdout until
  `shutdown` or the end of its input.

A checkout stays the way to develop and test; the image is another way to
run it.
