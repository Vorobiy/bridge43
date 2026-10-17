# Bridge43

Intake and AI case drafting for **PS43 Foundation**: every conversation with a school or partner in
Cameroon (WhatsApp, SMS, calls, field forms) becomes a structured, source-cited case draft that a PS43
staff member checks and approves.

This repo covers the first two steps of the system design:

```
1a WhatsApp ─┐
1b SMS ──────┤                         ┌──────────────────────────────┐
1c Calls ────┼─► convert to text ─────►│ 2. AI summary + human check  │──► approved case (JSON) ─► 3. central database
1d KoBo ─────┤   (Whisper, Claude      │   SNAP draft with citations  │
   Uploads ──┘    vision, FFmpeg)      └──────────────────────────────┘
```

## What it does

| Channel | How it arrives | What happens |
|---|---|---|
| WhatsApp text, voice notes, photos, videos, PDFs | Twilio webhook (official WhatsApp Business API) | Saved, consent notice sent on first contact, media converted to text |
| SMS (basic phones) | Same Twilio webhook | Same |
| Phone calls and WhatsApp calls | Twilio voice webhook | Spoken bilingual recording notice; call is forwarded to a staff phone and recorded, or the caller leaves a recorded message; recording is transcribed with timestamps |
| Field visits | KoBoToolbox REST service | Form answers and attached photos/audio become items |
| Exported WhatsApp chats | Staff upload (.zip with media, or .txt) | Parsed (iPhone + Android, English/French exports), each message and attachment becomes an item; re-uploads are de-duplicated |
| WhatsApp Desktop calls on a PS43 Mac | **Call recorder** (`recorder/`): starts recording when WhatsApp uses the mic, asks who it was when the call ends | Both sides recorded, uploaded to `POST /api/recorder/calls`, transcribed |
| Laptop calls / Wispr Notetaker / phone notes | Staff form: paste a transcript, add notes, attach recordings or photos | Same pipeline |

**Converters:** voice → Whisper (Groq, OpenAI, or local faster-whisper), photo → Claude vision,
video → FFmpeg splits sound (Whisper) and 4 frames (Claude vision), PDF → Claude.

**AI draft (SNAP = Situation, Needs, Assets, Plan):** Claude writes a structured draft per contact:
need vs. offer, organization and location, needs with urgency, offers, assets, readiness (power,
internet, storage, road access, trained staff, space), numbers, people as named, agreed next steps,
open questions, safety flags, and a confirmation message in the contact's own language. Every fact
cites the source items (`I12`), which link to the original message, photo or call on the review screen.

**One case per contact:** every contact gets a case number (`PS43-2026-0001`) as soon as they first
get in touch. Everything they send afterwards, on any channel (WhatsApp texts, voice notes, photos, videos,
SMS, calls through Twilio or the Mac call recorder, KoBo forms, staff notes), is filed in that same case.
People are matched by phone number, so the same number on WhatsApp and SMS is one person, and by name
when there is no number, which covers WhatsApp Desktop calls. Approved cases keep collecting; staff
**close** a case to start a fresh one, and can **move** an item or **merge** two cases when one school used two
numbers. Search the inbox by case number, name or phone.

**Human check:** staff edit, approve (creates case number `PS43-2026-0001`) or reject, and can send the
confirmation message back on WhatsApp/SMS. Approved cases are available at `GET /api/cases` for the next stage.

**Policy and privacy:** official Twilio/Meta APIs only (no unofficial WhatsApp bots); consent notice on first
contact and at the start of every call; `STOP`/`ARRET` deletes all of a contact's data; optional
explicit opt-in mode; Twilio signature checks on webhooks; staff login on every screen; the AI is told to
use only what the sources say and to treat their content as data, not instructions.

## Run it

Needs Python 3.11+ and FFmpeg (`brew install ffmpeg` / `apt install ffmpeg`).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add ANTHROPIC_API_KEY and TRANSCRIBE_API_KEY
python -m scripts.seed_demo   # optional: fictional demo cases, works with no keys
uvicorn app.main:app --reload
```

Open http://localhost:8000. `GET /health` lists anything missing in your configuration.

**Try it without Twilio:** in WhatsApp, open a chat → *Export chat* → *Include media*, then upload
the .zip on **Add intake**. Voice notes are transcribed, photos described, and a draft appears ~20 s later.

### Keys

| Key | Needed for | Where |
|---|---|---|
| `ANTHROPIC_API_KEY` | AI drafts, photo/video/PDF descriptions | console.anthropic.com |
| `TRANSCRIBE_API_KEY` | Voice notes, calls, video sound | console.groq.com (free tier) or platform.openai.com; or `TRANSCRIBE_PROVIDER=local` |
| `TWILIO_*` | Live WhatsApp/SMS/calls, sending confirmations | twilio.com (WhatsApp sandbox is free to test) |
| `KOBO_*` | Field forms | kf.kobotoolbox.org → project → Settings → REST Services |

### Connect live channels (Twilio)

1. Expose the server: `cloudflared tunnel --url http://localhost:8000` (or ngrok) and set `PUBLIC_BASE_URL` to that URL.
2. WhatsApp sender (or sandbox) and SMS number → *When a message comes in*: `POST {PUBLIC_BASE_URL}/webhooks/twilio/messaging`
3. Voice number / WhatsApp sender voice settings → *A call comes in*: `POST {PUBLIC_BASE_URL}/webhooks/twilio/voice`
4. KoBoToolbox REST service: `{PUBLIC_BASE_URL}/webhooks/kobo?secret={KOBO_WEBHOOK_SECRET}`

Note: WhatsApp only allows free-form replies within 24 h of the contact's last message; after that a
pre-approved template is needed (Twilio returns error 63016 and the review screen shows it).

## Code map

```
app/
  main.py               FastAPI app, startup checks
  config.py             all settings (env vars)
  db.py                 SQLite storage: contacts, threads, items, summaries (swap for Postgres/Supabase here)
  consent.py            notice, opt-in, STOP = delete
  channels/twilio.py    WhatsApp + SMS + calls + recordings, signature check, outbound messages
  channels/kobo.py      KoBoToolbox submissions
  channels/whatsapp_export.py   parser for WhatsApp "Export chat"
  channels/staff_intake.py      uploads and call notes
  processing/pipeline.py        item → text, background pool, debounced re-drafting
  processing/transcribe.py      Whisper (API or local)
  processing/vision.py          Claude vision for photos, video frames, PDFs
  processing/ffmpeg.py          audio/video helpers
  recorder_api.py       upload endpoint for the Mac call recorder
  summary.py            SNAP schema, prompt, citation checks
  web.py + templates/   inbox, intake forms, review screen, JSON API
recorder/               Mac call recorder (Swift) - see recorder/README.md
scripts/seed_demo.py    fictional demo data
tests/                  end-to-end tests with fake AI providers (pytest)
```

Run the tests: `pip install -r requirements-dev.txt && pytest`
