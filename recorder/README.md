# Bridge43 call recorder (Mac)

**Menu bar app (recommended):** `./build_app.sh` builds *Bridge43 Recorder.app*, installs it in
`~/Applications` and starts it. A phone icon appears at the top right of the screen: it turns into a red
dot with a timer while a WhatsApp call is recorded, then files the call into Bridge43 (your Railway server).
Its menu shows the last filed call, the latest cases (click to open), *Open Bridge43 inbox*, *Pause*,
*Start at login* (switched on automatically), *Server address…* and *Check setup…*. It reads the server address
and token from `~/.bridge43/recorder.json`. Messages, photos and voice notes don't need the Mac at all: they
reach Railway through Twilio. The terminal version below still works (`--cli`, `--check`, `--debug`).


Automatic note-taking for WhatsApp calls on a PS43 laptop, like Otter for Google Meet, but nothing
joins the call. When WhatsApp Desktop starts using the microphone, the recorder records both sides
on this Mac. When the call ends, it checks your rules, asks you who it was, and files the recording in
Bridge43, where it is transcribed and turned into a case draft.

```
WhatsApp call starts ─► recorder notices (WhatsApp uses the mic) ─► records other side + your mic
call ends ─► rules (min length, skip / only lists) ─► "Send to Bridge43? Who was it?" ─► uploaded
         ─► Bridge43: transcript ─► AI case draft ─► staff review
```

**Needs:** macOS 14.4 or later, Xcode Command Line Tools,
`brew install ffmpeg`, and a running Bridge43 server.

## Set up (once)

1. **Server side.** In Bridge43's `.env`, add a long random secret and restart the server:
   ```
   RECORDER_TOKEN=paste-a-long-random-string-here
   ```
   (Make one with `openssl rand -hex 24`.)

2. **Build the recorder.** In Terminal:
   ```
   xcode-select --install          # only if you have never installed the command line tools
   cd bridge43/recorder
   swift build -c release
   ```

3. **First run creates the settings file:**
   ```
   .build/release/bridge43-recorder
   ```
   Open `~/.bridge43/recorder.json` and set `"token"` to the same RECORDER_TOKEN. If Bridge43 runs on
   another computer, set `"serverURL"` to its address (e.g. your tunnel URL).

4. **Check everything:**
   ```
   .build/release/bridge43-recorder --check
   ```
   macOS will ask for **Screen & System Audio Recording** and **Microphone** permission for Terminal.
   Allow both (System Settings → Privacy & Security), quit and reopen Terminal, and run `--check`
   again until every line is ✅.

## Use it

```
.build/release/bridge43-recorder
```
Leave that Terminal window open. Then just take WhatsApp calls on WhatsApp Desktop:
- When a call starts you get a notification: **tell the caller the call is recorded.**
- When it ends, a dialog asks **"Send this call to Bridge43? Who was it?"**, pre-filled with the name
  from WhatsApp's call window if it could read one. Fix the name, add their number if you have it
  (e.g. `Lycée de Bambui +237 670 000 001`), and click **Send to Bridge43**, or **Discard**.
- A notification confirms it was filed. The case appears in Bridge43 within a minute.

The recording is deleted from the Mac once Bridge43 has it. If an upload fails, it stays in
`~/.bridge43/calls/` and the Terminal says so.

## Rules ("certain conditions")

All in `~/.bridge43/recorder.json`:

| Setting | Default | What it does |
|---|---|---|
| `onlyContacts` | `[]` | If not empty, keep only calls whose WhatsApp window name contains one of these, e.g. `["Lycée", "GBHS", "+237"]` |
| `skipContacts` | `[]` | Always discard calls with these, e.g. `["Mom", "Alex"]` |
| `confirmBeforeSending` | `true` | Ask before every upload. Set `false` to file calls with no dialog |
| `minCallSeconds` | `20` | Ignore anything shorter |
| `minOtherSideSpeechSeconds` | `3` | Discard if the other side never spoke (e.g. you were recording a voice note) |
| `ffmpegPath` | `/opt/homebrew/bin/ffmpeg` | Change on Intel Macs to `/usr/local/bin/ffmpeg` |

## Troubleshooting

- **Nothing happens when a call starts:** run with `--debug` and start a call. It prints which apps are
  using the microphone. If WhatsApp's id doesn't contain "whatsapp", tell us what it shows.
- **"No recording permission":** System Settings → Privacy & Security → Screen & System Audio
  Recording → enable your Terminal app, then quit Terminal completely and reopen it.
- **Only the other person is in the recording:** turn on Terminal under System Settings → Privacy & Security → Microphone, then quit and reopen Terminal (`--check` shows the status).
- **The dialog shows no name:** WhatsApp didn't expose the caller in its window title; type it in.

## Privacy

Tell every caller the call is recorded (the notification reminds you). Only calls you approve are
sent. Audio is deleted from the Mac after upload, and Bridge43 removes everything for a contact who
sends STOP / ARRET.
