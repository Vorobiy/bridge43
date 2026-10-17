import io
import zipfile

from app.channels import whatsapp_export as wx

IOS = """‎[15/10/2026, 09:30:12] PS43 Eugene: Bonjour, comment va l'école ?
[15/10/2026, 09:31:05] Mr. Tabi: Bonjour. Le toit de deux salles fuit.
Quand il pleut les élèves vont chez eux.
[15/10/2026, 09:32:00] Mr. Tabi: ‎<attached: 00000012-AUDIO-2026-10-15-09-32-00.opus>
[15/10/2026, 09:33:00] Mr. Tabi: ‎<attached: 00000013-PHOTO-2026-10-15-09-33-00.jpg>
[15/10/2026, 09:34:00] Mr. Tabi: ‎image omitted
"""

ANDROID = """10/15/26, 9:30 AM - Messages and calls are end-to-end encrypted. Tap to learn more.
10/15/26, 9:30 AM - PS43: How many students?
10/15/26, 1:05 PM - Headmaster: 412 students, 6 classrooms
10/15/26, 1:06 PM - Headmaster: PTT-20261015-WA0003.opus (file attached)
10/15/26, 1:07 PM - Headmaster: <Media omitted>
"""


def test_ios_export():
    msgs = wx.parse_text(IOS)
    assert [m.author for m in msgs] == ["PS43 Eugene", "Mr. Tabi", "Mr. Tabi", "Mr. Tabi", "Mr. Tabi"]
    assert "élèves vont chez eux" in msgs[1].text  # multi-line message joined
    assert msgs[2].attachment == "00000012-AUDIO-2026-10-15-09-32-00.opus"
    assert msgs[3].attachment.endswith(".jpg")
    assert msgs[4].media_omitted
    assert msgs[0].sent_at.day == 15 and msgs[0].sent_at.month == 10


def test_android_export_month_first_with_ampm():
    msgs = wx.parse_text(ANDROID)
    assert len(msgs) == 4  # system line skipped
    assert msgs[1].sent_at.hour == 13 and msgs[1].sent_at.month == 10 and msgs[1].sent_at.day == 15
    assert msgs[2].attachment == "PTT-20261015-WA0003.opus"
    assert msgs[3].media_omitted


def test_zip_upload_collects_media():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("WhatsApp Chat - GBHS/_chat.txt", IOS)
        zf.writestr("WhatsApp Chat - GBHS/00000012-AUDIO-2026-10-15-09-32-00.opus", b"OggS fake audio")
    parsed = wx.parse_upload("chat.zip", buf.getvalue())
    assert "00000012-AUDIO-2026-10-15-09-32-00.opus" in parsed.files
    assert parsed.authors == ["PS43 Eugene", "Mr. Tabi"]
