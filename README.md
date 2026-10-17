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

**School profile:** every school draft also rates the same 8 areas, so schools can be compared and scored:
safety and operation, learning space, water and toilets, teachers, materials and results, barriers for
children (fees, birth certificates, girls, disability), health and wellbeing (meals), and technology. Each is
`ok`, `partial`, `critical` or `unknown` with citations. `unknown` means "not discussed", so a school that
never mentioned water is not mistaken for one with water. The draft also records whether the school is open and
safe to reach, girls enrolled, teachers paid by parents, pupils without a birth certificate, and whether there is
an active parents' association.

**Location:** a WhatsApp location pin (Twilio `Latitude`/`Longitude`) is saved as a "Shared location" item and
becomes the case's map point, cited like any other fact. Map links and KoBo GPS fields work too.

**Trust level:** worked out from the evidence, not by the AI: *self-reported* (messages and calls only),
*photo-backed* (the contact sent a photo or video), *visit-confirmed* (a KoBo field visit form is in the case).

**Follow-up questions:** after each draft, Bridge43 checks 14 short profile questions (French and English) and
proposes a WhatsApp message with only the unanswered ones, at most 3 at a time, most important first (open and
safe, then name and place, biggest need, pupils, water, toilets...). Staff check and send it from the review
screen; the answers come back into the case and the next draft fills the profile. Partners, donors and
unrelated chats get no profile questions. The question bank is in `app/gaps.py`.

**One case per contact:** every contact gets a case number (`PS43-2026-0001`) as soon as they first
get in touch. Everything they send afterwards, on any channel (WhatsApp texts, voice notes, photos, videos,
SMS, calls through Twilio or the Mac call recorder, KoBo forms, staff notes), is filed in that same case.
People are matched by phone number, so the same number on WhatsApp and SMS is one person, and by name
when there is no number, which covers WhatsApp Desktop calls. Every new message, photo or voice note updates the case: the AI rewrites the draft from the current
record (keeping staff edits), says what changed, and sends the case back to "needs review". Staff
**close** a case to start a fresh one, and can **move** an item or **merge** two cases when one school used two
numbers. Search the inbox by case number, name or phone.

**Human check:** staff edit, approve (creates case number `PS43-2026-0001`) or reject, and can send the
confirmation message back on WhatsApp/SMS. Approved cases are available at `GET /api/cases` for the next stage
(priority matrix): each case comes with `trust`, `profile_complete` (0-100) and `missing_topics`, plus the
full record under `case` (profile, operating status, location, readiness, numbers, needs).

**Policy and privacy:** official Twilio/Meta APIs only (no unofficial WhatsApp bots); consent notice on first
contact and at the start of every call; `STOP`/`ARRET` deletes all of a contact's data; optional
explicit opt-in mode; Twilio signature checks on webhooks; staff login on every screen; the AI is told to
use only what the sources say and to treat their content as data, not instructions.

## Part two: from approved case to delivered help

After staff approve a case, Bridge43 carries it through to delivery and follow-up, on the staff screens
**Priorities**, **Tickets**, **Partners** and **Matching**.

**Priority matrix** (`/dashboard`): every approved school gets a **need** score (missing items in the 8 profile
areas, weighted, plus how many pupils) and a **readiness** score (power, signal, secure storage, someone to
maintain it, truck access, parents' association). Schools land in *Act now*, *Prepare first*, *Quick wins* or
*Monitor*; closed or unsafe schools go on *Safety hold*. Needs already delivered drop out of the score, so a school
moves down the matrix once helped. Filter by region. JSON: `GET /api/matrix`.

**Tickets** (`/tickets`): approving a school creates one ticket per need, with deployment steps for that kind of
help (survey, 2 quotes, build or deliver, train, school confirms with photos). Status moves New → Partner found →
In progress → Delivered → Confirmed by school. Every message to a partner or school, note and status change is
logged on the ticket (communication tracking). Messages go out on WhatsApp/SMS through Twilio, or staff tick
"I sent it from my phone" to just log it.

**Partners and donors** (`/partners`): PS43 has no fixed partner list, so the database builds itself. A donor or
partner who calls or writes is screened and summarised like a school (case type *offer*): who they are, what and
how much they can give, where, when, conditions, whether the items are abroad, and what they say about past work
with PS43. Approving the case adds or updates the partner with its items. Roles: donor, does the work (local
contractor or NGO), supplier, shipping. Staff can also add a partner they already know. Partners get their own
follow-up questions (what and how many, regions, timing, registration, contact).

**Trust** (0-100: New, Verified, Trusted) is earned from what PS43 has seen, not what partners claim: identity
checked by staff (+30), registered organisation (+5), photos or documents (+10), each case the **school confirmed**
(+15, up to 45), deliveries waiting for confirmation (+5), commitments not kept (-15).

**Matching** (`/matches`, and on each ticket): for every open need and every partner item, what they have in
common: same type of help (or related help, or money), the partner serves the school's region (a partner that says
"Northwest only" is never suggested for the Far North), quantity still needed vs. still available, and trust. When
one partner is not enough, up to 3 are combined (e.g. 25 laptops from Toronto + 15 from Yaoundé for a 40-laptop
lab), each asked only for what is still missing. Items abroad add shipping steps (ship, customs, transport) and
suggest a shipping partner. The by-type table shows where demand has no supply: where to look for new partners.

**Quote check**: each quote (total FCFA, quantity, unit) is compared with other quotes for the same kind of item on
any ticket. More than 25% above the usual unit price is flagged "ask why before accepting"; far below is flagged
"check quality".

**Follow-ups**: when a ticket is delivered, the school gets a short WhatsApp check at 30, 90 and 180 days (French or
English). Due follow-ups appear on Priorities; staff check and send. Answers land in the school's case.

**Public sign-up** (`/join`, no login, French and English): *Request help* for schools and educators, and *Offer
help* for donors, contractors, suppliers and shippers. Each form becomes an intake item in the inbox, exactly like a
WhatsApp message (AI draft, staff review), with a consent checkbox and a hidden anti-bot field. Share the link in
PS43's outreach.

**PS43 branding**: all screens and the public sign-up follow the PS43 Brand Book (green #007A5E, yellow #FDBB2F,
navy #002D62, red #CE1126, Arial text, PS43 logo in `app/static/brand/`). Titles use Superline when its licensed
file is placed in `app/static/fonts/` (see the README there), otherwise the bundled free Bebas Neue.

**Demo data**: `python -m scripts.seed_demo` loads fictional schools and partners for the whole flow (a computer lab
covered by two laptop donors and a shipper, an inflated roofing quote, a trusted borehole contractor with two
confirmed cases, a follow-up due, a school on safety hold). `--intake-only` loads only the two intake examples.

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

### Run it permanently on a Mac

`scripts/mac_services.sh install <your-ngrok-domain>` installs the server, a fixed-address ngrok tunnel
(free static domain from dashboard.ngrok.com → Domains) and the call recorder as background services:
they start at login, restart if they crash, and keep the Mac awake. `status`, `logs`, `restart` and
`uninstall` do what they say. Logs go to `~/Library/Logs/Bridge43/`.

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
  summary.py            SNAP schema + school profile, prompt, citation checks
  gaps.py               follow-up questions (14 for schools, 7 for donors) and which are still unanswered
  scoring.py            need and readiness scores, priority matrix boxes
  matching.py           regions, quantities, partner trust, need-to-partner matching, quote price check
  workflow.py           approval hook, tickets and steps, partners database, messages, follow-ups
  ops_web.py            Priorities, Tickets, Partners, Matching screens and their JSON APIs
  public_web.py         public /join sign-up forms (French/English)
  evidence.py           trust level and map location, worked out from the items
  web.py + templates/   inbox, intake forms, review screen, JSON API
recorder/               Mac call recorder (Swift) - see recorder/README.md
scripts/seed_demo.py    fictional demo data
tests/                  end-to-end tests with fake AI providers (pytest)
```

Run the tests: `pip install -r requirements-dev.txt && pytest`
