# 24/7 loop stream on an Oracle Always Free server

Streams one video file to YouTube Live on an endless loop, from a server that
costs nothing and stays up when your phone is locked, asleep, or out of battery.
Once it is running the phone is only for checking on it.

Everything here can be done from an iPhone: Safari for the two web consoles, and
a free SSH app for the handful of commands.

## What is in this directory

```
bootstrap.sh            One-shot installer. Run it once on a fresh server.
bin/stream.sh           The supervisor. Keeps ffmpeg pushing to the ingest.
bin/prepare-video.sh    Builds a loop file the stream can send without re-encoding.
bin/streamctl           Day-to-day control: start, stop, status, logs, key.
bin/common.sh           Shared config loader.
systemd/loopstream.service  Starts the stream at boot, restarts it if it dies.
stream.env.example      Every setting, commented.
```

## Before you start

Two things about YouTube that are easier to learn now than at step 6:

- Live streaming has to be enabled on the channel, and **the first time you
  enable it there is a 24-hour wait** before you can go live. Do this today even
  if you set up the server tomorrow.
- Streaming with a stream key has no subscriber minimum. The 50-subscriber
  minimum applies only to going live from the YouTube mobile app, which is not
  what this does.

## Step 1 — Create the server

At [cloud.oracle.com](https://cloud.oracle.com), sign up for an Always Free
account. A card is required for identity verification and is not charged for
Always Free resources; expect a small temporary authorisation hold.

**Your home region is permanent and Always Free resources only exist in it.**
Pick one geographically close to you.

Then: **Compute → Instances → Create instance**.

| Field | Choose |
| --- | --- |
| Image | Canonical Ubuntu 24.04 (22.04 is fine) |
| Shape | Change shape → **Ampere** → `VM.Standard.A1.Flex` |
| OCPUs / memory | **2 OCPUs, 12 GB** — see below |
| Networking | Assign a public IPv4 address |
| SSH keys | **Save private key.** Download it before leaving the page. |

Confirm the shape panel shows **Always Free eligible** before you create.

**Why 2 OCPUs and not 4.** The Always Free allowance for A1 is 1,500 OCPU hours
and 9,000 GB hours per month. A month is about 730 hours, so running nonstop
that is 2 OCPUs and 12 GB. A 4-OCPU instance left on all month would use roughly
2,920 OCPU hours and bill you for the excess. Two cores are ample here — the
stream is a remux, not an encode. Check the figures your own account shows, as
the allowance has changed before.

**"Out of host capacity"** on creation is common and not something you did
wrong. Free A1 capacity is oversubscribed. Try a different availability domain,
or try again later — often early morning in the region's timezone.

Copy the instance's **public IP address** when it finishes provisioning.

## Step 2 — Connect from the phone

Install **Termius** (free). Then:

1. Save the downloaded `.key` file into the Files app if it is not there already.
2. Termius → **Keychain** → **+** → import the key file.
3. **Hosts** → **+** → address = the public IP, username = `ubuntu`, key = the
   one you just imported.
4. Connect, and accept the host fingerprint.

You should land at a `ubuntu@...:~$` prompt.

## Step 3 — Install

Get this directory onto the server, then run the installer.

If the repository is public:

```bash
git clone https://github.com/jayCryptoJay/fibstein-lab.git
bash fibstein-lab/stream/bootstrap.sh
```

If `git clone` asks for credentials, use Termius's **SFTP** tab instead: upload
the `stream` folder to `/home/ubuntu/`, then run `bash ~/stream/bootstrap.sh`.

The installer adds ffmpeg and tmux, copies the scripts to `~/loopstream`,
creates `~/loopstream/stream.env`, installs the `loopstream` service so it comes
back after a reboot, and puts `streamctl` on your PATH. It is safe to re-run; it
will not overwrite your stream key.

**No inbound ports are needed.** The server connects out to YouTube; nothing
connects in. Leave the Oracle security list alone apart from SSH.

## Step 4 — Get the video onto the server

The reliable way from a phone is Termius's **SFTP** tab — upload straight from
the Files app to `/home/ubuntu/`.

`wget` also works, but only with a link that returns the file itself:

```bash
wget -O ~/source.mp4 'https://...'
```

A Google Drive "share" link returns an HTML page, not a video, and a file
downloaded that way will fail at step 5. A Dropbox link works if you change
`?dl=0` to `?dl=1` at the end. After downloading, check the size looks right:

```bash
ls -lh ~/source.mp4
```

## Step 5 — Build the loop file

```bash
streamctl video ~/source.mp4
```

This re-encodes once, offline, into `~/loopstream/media/loop.mp4`: H.264 and AAC
at 1080p30, with a keyframe every two seconds, and silent audio added if your
source has none (a live ingest needs an audio track). It runs faster than real
time but is not instant.

Doing this once is what lets the stream itself run at almost no CPU afterwards —
it only copies packets. It also means a source file YouTube would have rejected
fails here, on the ground, instead of at 3am.

## Step 6 — Get the stream key

In Safari, open [studio.youtube.com](https://studio.youtube.com), tap **aA** in
the address bar and choose **Request Desktop Website**. Then **Create → Go Live
→ Stream**.

Set the stream's title and visibility, then find **Stream key** and copy it.

In the stream settings, turn **Auto-start on** and **Auto-stop off**.
Auto-stop ends the broadcast after about a minute without data — which is
exactly what happens during a reconnect, and you would come back to a stream
that had ended hours ago.

Back in Termius:

```bash
streamctl key
```

Paste the key and press Enter. It is not echoed and does not enter your shell
history. The key is stored in `~/loopstream/stream.env`, mode 600.

## Step 7 — Start it

```bash
streamctl start
streamctl status
```

Then check YouTube Studio. Within a minute or so the stream should show as live
with its health indicator green. That page, not the server, is the real answer
to "is it working".

From here the server handles reboots on its own.

## Day to day

```bash
streamctl status        # running? what is it streaming? key set?
streamctl logs          # last 50 lines
streamctl logs -f       # follow live, Ctrl-C to stop watching
streamctl restart       # after changing the video or any setting
streamctl stop
streamctl check         # validate config and media without streaming
```

`streamctl status` shows only the last four characters of the stream key.

## Configuration

Settings live in `~/loopstream/stream.env`. Edit with `nano
~/loopstream/stream.env`, then `streamctl restart`. Every option is commented in
`stream.env.example`. The ones that matter:

| Setting | Default | Notes |
| --- | --- | --- |
| `MODE` | `copy` | `copy` sends the prepared file untouched. `encode` re-encodes live. |
| `VIDEO_BITRATE_K` | `4500` | 1080p30 in YouTube's recommended range. |
| `INGEST_URL` | RTMPS on 443 | Switch to `rtmp://a.rtmp.youtube.com/live2` if RTMPS is blocked. |
| `MAX_BACKOFF_SECONDS` | `300` | Longest pause between reconnect attempts. |

An environment variable overrides the file, so you can try a setting without
committing to it: `MODE=encode streamctl check`.

## Bandwidth, and what actually stays free

Always Free includes 10 TB of outbound data per month. A 4,500 kbps stream
running every second of a 30-day month sends about **1.46 TB** — comfortably
inside it. You could run three of these and still be under.

Free, indefinitely: the instance, the bandwidth, the boot volume, the IP.
Not free: exceeding the OCPU-hour allowance (see step 1), or the egress above
10 TB if you raise the bitrate a long way.

## Idle reclamation — the one real risk

Oracle documents that it may reclaim an Always Free compute instance if, over a
**7-day period**, *all* of these hold:

- CPU utilisation at the 95th percentile is under 20%
- Network utilisation is under 20%
- Memory utilisation is under 20% (A1 shapes only)

A copy-mode stream trips all three. It uses almost no CPU by design, 4.5 Mbps is
a fraction of a percent of the instance's network capacity, and ffmpeg's memory
footprint is small. **Oracle's documentation does not promise a warning email
before reclamation**, so do not plan on being told.

Your options, honestly:

- **Accept it.** Many people run streams like this for months untouched. If the
  instance does get reclaimed you rebuild it — this directory is the rebuild.
- **Use encode mode**, which keeps CPU genuinely busy. On 2 OCPUs, 1080p30 is a
  tight fit; 720p is comfortable. Set `MODE=encode`, and for 720p also
  `WIDTH=1280`, `HEIGHT=720`, `VIDEO_BITRATE_K=3000`. Watch `streamctl logs`
  for dropped frames afterwards.

There is no setting that makes this risk go away, and anything that fakes load
to defeat the check is both against the spirit of the free tier and one more
thing to maintain.

## Troubleshooting

**Nothing appears on YouTube.** `streamctl logs`. A bad key shows as an
immediate failure and a doubling retry delay; fix it with `streamctl key`.

**The unit will not start after a config mistake.** Repeated instant failures
put the service in a failed state on purpose, so it stops hammering the ingest.
Fix the config and `streamctl restart` — that clears it.

**YouTube says the stream is unstable.** Usually keyframes too far apart, which
`streamctl check` warns about, or a bitrate the link cannot sustain. Re-run
`streamctl video` on the original source, or lower `VIDEO_BITRATE_K`.

**Video stutters in encode mode.** The instance cannot encode that fast. Drop to
720p as above, or go back to `MODE=copy`.

**"No space left on device".** The default boot volume is generous but your
source file plus the loop file plus logs add up. `df -h`, then delete the
original source once the loop file is built.

**Two streams at once.** Running `stream.sh` by hand while the service is also
running pushes two streams to one key and YouTube rejects both. `streamctl stop`
first.

## Running it without systemd

The service is the better route — it survives reboots and restarts on failure.
If you would rather watch it run in a terminal:

```bash
streamctl stop
tmux new -s stream
~/loopstream/bin/stream.sh
```

Detach with `Ctrl-B` then `D`; it keeps running after you close Termius.
Reattach with `tmux attach -t stream`.

## Security notes

- `stream.env` holds your stream key. It is mode 600 and must never be
  committed. If it leaks, rotate the key in YouTube Studio — anyone holding it
  can broadcast to your channel.
- The scripts filter the key out of everything ffmpeg logs, because ffmpeg
  prints the full ingest URL in several of its error messages and the journal is
  readable by other accounts on the machine.
- The key is still visible in `ps` to anyone with a shell on the server, since
  ffmpeg takes the URL as an argument. On a single-user instance that is you.
- Keep the SSH private key in a password manager. Oracle will not re-issue it,
  and losing it means losing access to the instance.

## One non-technical note

YouTube's policies on repetitive and reused content apply to 24/7 loops. A
stream that is a single clip repeating forever, with nothing added, is the kind
of thing that gets limited or removed. Worth knowing before you invest time in
the setup rather than after.
