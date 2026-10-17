// WhatsApp Desktop messages -> Bridge43.
//
// WhatsApp for Mac keeps every chat in a local SQLite database (ChatStorage.sqlite). Every 20 s this
// reads the messages that arrived since the last look (texts, voice notes, photos, videos, documents)
// and sends each one-to-one chat's new messages to Bridge43:
//   * someone who already has a case -> the messages are added to it (the case draft updates itself)
//   * a new chat -> Bridge43 screens it and opens a case only if it is about schools or logistics;
//     personal chats are not stored anywhere.
// Group chats, status updates and channels are never read. Needs Full Disk Access for this app
// (macOS protects other apps' data).

import Foundation
import SQLite3

let whatsAppContainer = home.appendingPathComponent("Library/Group Containers/group.net.whatsapp.WhatsApp.shared",
                                                    isDirectory: true)
let chatDBURL = whatsAppContainer.appendingPathComponent("ChatStorage.sqlite")
let messageStateURL = appDir.appendingPathComponent("messages.json")
let fullDiskAccessSettings = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"

struct MessageState: Codable {
    var lastPK: Int64
}

/// What the message watcher is doing, for the menu bar.
enum MessageSyncStatus {
    case off
    case noAccess
    case error(String)
    case ok(lastFiled: String?)
}

var messageStatusListener: ((MessageSyncStatus) -> Void)?

private func report(_ s: MessageSyncStatus) {
    if let l = messageStatusListener { DispatchQueue.main.async { l(s) } }
}

struct WAMessage {
    let pk: Int64
    let chatPK: Int64
    let fromMe: Bool
    let type: Int
    let date: Date
    let text: String?
    let mediaPath: String?
    let jid: String
    let partner: String?

    /// text | image | video | audio | document, or nil for things we skip (stickers, system events, calls)
    var kind: String? {
        switch type {
        case 0, 7: return "text"
        case 1: return "image"
        case 2, 11: return "video"
        case 3: return "audio"
        case 8: return "document"
        default: return (text?.isEmpty == false) ? "text" : nil
        }
    }

    var isMedia: Bool { kind != nil && kind != "text" }

    /// The attachment on disk, if WhatsApp has downloaded it.
    var mediaFile: URL? {
        guard let p = mediaPath, !p.isEmpty else { return nil }
        let candidates = p.hasPrefix("/") ? [URL(fileURLWithPath: p)] : [
            whatsAppContainer.appendingPathComponent("Message").appendingPathComponent(p),
            whatsAppContainer.appendingPathComponent(p),
        ]
        return candidates.first { FileManager.default.fileExists(atPath: $0.path) }
    }

    /// Phone number from a one-to-one chat id like 237670000001@s.whatsapp.net
    var phone: String? {
        guard jid.hasSuffix("@s.whatsapp.net") else { return nil }
        let digits = jid.prefix { $0 != "@" }.filter(\.isNumber)
        return digits.count >= 7 ? "+" + digits : nil
    }

    var isOneToOne: Bool {
        !(jid.hasSuffix("@g.us") || jid.hasSuffix("@broadcast") || jid.hasSuffix("@newsletter")
          || jid.hasPrefix("status") || jid.isEmpty)
    }
}

final class ChatDB {
    private var db: OpaquePointer?

    init() throws {
        if sqlite3_open_v2(chatDBURL.path, &db, SQLITE_OPEN_READONLY, nil) != SQLITE_OK {
            let msg = db.map { String(cString: sqlite3_errmsg($0)) } ?? "cannot open"
            sqlite3_close(db)
            db = nil
            throw RecorderError("cannot open WhatsApp's messages (\(msg))")
        }
        sqlite3_busy_timeout(db, 3000)
        try checkLayout()
    }

    deinit { sqlite3_close(db) }

    private func columns(_ table: String) throws -> Set<String> {
        var out = Set<String>()
        try query("PRAGMA table_info(\(table))") { s in if let c = ChatDB.str(s, 1) { out.insert(c) } }
        return out
    }

    private func checkLayout() throws {
        let need: [String: [String]] = [
            "ZWAMESSAGE": ["Z_PK", "ZCHATSESSION", "ZISFROMME", "ZMESSAGETYPE", "ZMESSAGEDATE", "ZTEXT", "ZMEDIAITEM"],
            "ZWACHATSESSION": ["Z_PK", "ZCONTACTJID", "ZPARTNERNAME"],
            "ZWAMEDIAITEM": ["Z_PK", "ZMEDIALOCALPATH"],
        ]
        for (table, cols) in need {
            let have = try columns(table)
            if have.isEmpty { throw RecorderError("cannot read WhatsApp's messages (no \(table) table: no access, or a new WhatsApp version)") }
            let missing = cols.filter { !have.contains($0) }
            if !missing.isEmpty { throw RecorderError("WhatsApp's database changed (\(table) has no \(missing.joined(separator: ", ")))") }
        }
    }

    static func str(_ s: OpaquePointer, _ i: Int32) -> String? {
        guard let c = sqlite3_column_text(s, i) else { return nil }
        return String(cString: c)
    }

    func query(_ sql: String, _ binds: [Double] = [], _ row: (OpaquePointer) -> Void) throws {
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK, let st = stmt else {
            throw RecorderError("WhatsApp database: \(String(cString: sqlite3_errmsg(db)))")
        }
        defer { sqlite3_finalize(st) }
        for (i, b) in binds.enumerated() { sqlite3_bind_double(st, Int32(i + 1), b) }
        while true {
            let rc = sqlite3_step(st)
            if rc == SQLITE_ROW { row(st) } else if rc == SQLITE_DONE { break } else {
                throw RecorderError("WhatsApp database: \(String(cString: sqlite3_errmsg(db)))")
            }
        }
    }

    private static let select = """
        SELECT m.Z_PK, m.ZCHATSESSION, m.ZISFROMME, m.ZMESSAGETYPE, m.ZMESSAGEDATE, m.ZTEXT,
               mi.ZMEDIALOCALPATH, s.ZCONTACTJID, s.ZPARTNERNAME
        FROM ZWAMESSAGE m
        JOIN ZWACHATSESSION s ON s.Z_PK = m.ZCHATSESSION
        LEFT JOIN ZWAMEDIAITEM mi ON mi.Z_PK = m.ZMEDIAITEM
        """

    private func messages(_ whereSQL: String, _ binds: [Double]) throws -> [WAMessage] {
        var out: [WAMessage] = []
        try query(ChatDB.select + " " + whereSQL, binds) { s in
            out.append(WAMessage(
                pk: sqlite3_column_int64(s, 0), chatPK: sqlite3_column_int64(s, 1),
                fromMe: sqlite3_column_int64(s, 2) != 0, type: Int(sqlite3_column_int64(s, 3)),
                date: Date(timeIntervalSinceReferenceDate: sqlite3_column_double(s, 4)),
                text: ChatDB.str(s, 5), mediaPath: ChatDB.str(s, 6),
                jid: ChatDB.str(s, 7) ?? "", partner: ChatDB.str(s, 8)))
        }
        return out
    }

    func newMessages(after pk: Int64, limit: Int = 300) throws -> [WAMessage] {
        try messages("WHERE m.Z_PK > ? ORDER BY m.Z_PK LIMIT \(limit)", [Double(pk)])
    }

    /// Earlier messages in the chat, so Bridge43 can tell what a new conversation is about.
    func context(chat: Int64, before pk: Int64, limit: Int = 15) throws -> [WAMessage] {
        let since = Date().addingTimeInterval(-14 * 86400).timeIntervalSinceReferenceDate
        return try messages("WHERE m.ZCHATSESSION = ? AND m.Z_PK <= ? AND m.ZMESSAGEDATE > ? ORDER BY m.Z_PK DESC LIMIT \(limit)",
                            [Double(chat), Double(pk), since]).reversed()
    }

    /// Where to start the first time: messages from the last 24 hours.
    func startPK() throws -> Int64 {
        var pk: Int64 = 0
        let cutoff = Date().addingTimeInterval(-24 * 3600).timeIntervalSinceReferenceDate
        try query("SELECT COALESCE(MAX(Z_PK), 0) FROM ZWAMESSAGE WHERE ZMESSAGEDATE < ?", [cutoff]) { s in
            pk = sqlite3_column_int64(s, 0)
        }
        return pk
    }
}

func loadMessageState() -> MessageState? {
    guard let data = try? Data(contentsOf: messageStateURL) else { return nil }
    return try? JSONDecoder().decode(MessageState.self, from: data)
}

func saveMessageState(_ s: MessageState) {
    try? JSONEncoder().encode(s).write(to: messageStateURL)
}

/// Can this app read WhatsApp's messages? (For --check and the menu.)
func messageAccessProblem() -> String? {
    do { _ = try ChatDB(); return nil } catch { return "\(error)" }
}

private let maxAttachmentBytes = 100 * 1024 * 1024

private func mimeType(_ ext: String) -> String {
    ["opus": "audio/ogg", "ogg": "audio/ogg", "m4a": "audio/mp4", "mp3": "audio/mpeg", "aac": "audio/aac",
     "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "heic": "image/heic",
     "mp4": "video/mp4", "mov": "video/quicktime", "pdf": "application/pdf"][ext.lowercased()]
        ?? "application/octet-stream"
}

/// Send one chat's messages; returns the case number if Bridge43 filed them, nil if it skipped the chat.
func uploadChat(_ msgs: [WAMessage], context: [WAMessage], settings: Settings) async throws -> String? {
    guard let first = msgs.first,
          let url = URL(string: settings.serverURL.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + "/api/recorder/messages") else {
        return nil
    }
    let iso = ISO8601DateFormatter()
    var attachments: [(name: String, data: Data)] = []
    func entry(_ m: WAMessage, withFile: Bool) -> [String: Any] {
        var e: [String: Any] = ["id": m.pk, "at": iso.string(from: m.date), "from_me": m.fromMe,
                                "kind": m.kind ?? "text", "text": m.text ?? ""]
        if withFile, m.isMedia, let f = m.mediaFile,
           let size = (try? f.resourceValues(forKeys: [.fileSizeKey]))?.fileSize, size <= maxAttachmentBytes,
           let data = try? Data(contentsOf: f) {
            let name = "\(m.pk).\(f.pathExtension.isEmpty ? "bin" : f.pathExtension)"
            attachments.append((name, data))
            e["file"] = name
        }
        return e
    }
    let chat: [String: Any] = [
        "chat_id": first.jid, "phone": first.phone ?? "", "name": first.partner ?? "",
        "messages": msgs.map { entry($0, withFile: true) },
        "context": context.compactMap { $0.kind == "text" ? entry($0, withFile: false) : nil },
    ]
    let boundary = "bridge43-\(UUID().uuidString)"
    var body = Data()
    let json = try JSONSerialization.data(withJSONObject: chat)
    body.append("--\(boundary)\r\nContent-Disposition: form-data; name=\"chat\"\r\n\r\n".data(using: .utf8)!)
    body.append(json)
    body.append("\r\n".data(using: .utf8)!)
    for a in attachments {
        body.append(("--\(boundary)\r\nContent-Disposition: form-data; name=\"files\"; filename=\"\(a.name)\"\r\n" +
                     "Content-Type: \(mimeType((a.name as NSString).pathExtension))\r\n\r\n").data(using: .utf8)!)
        body.append(a.data)
        body.append("\r\n".data(using: .utf8)!)
    }
    body.append("--\(boundary)--\r\n".data(using: .utf8)!)

    var req = URLRequest(url: url)
    req.httpMethod = "POST"
    req.setValue("Bearer \(settings.token)", forHTTPHeaderField: "Authorization")
    req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
    req.timeoutInterval = 300
    let (data, resp) = try await URLSession.shared.upload(for: req, from: body)
    let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
    guard code == 200 else {
        throw RecorderError("server answered \(code): \(String(data: data, encoding: .utf8)?.prefix(200) ?? "")")
    }
    let reply = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
    if reply["filed"] as? Bool == true { return reply["case_number"] as? String ?? "filed" }
    if debug { log("Chat with \(first.partner ?? first.jid) skipped: \(reply["reason"] as? String ?? "")") }
    return nil
}

/// One look at WhatsApp: send what arrived since last time. Returns a line for the menu, if something was filed.
func syncMessagesOnce(settings: Settings) async throws -> String? {
    let db = try ChatDB()
    var state = try loadMessageState() ?? MessageState(lastPK: db.startPK())
    let rows = try db.newMessages(after: state.lastPK)
    guard let last = rows.last else { saveMessageState(state); return nil }

    // keep chat order: chats with the oldest new message go first
    var order: [Int64] = []
    var byChat: [Int64: [WAMessage]] = [:]
    for m in rows {
        if byChat[m.chatPK] == nil { order.append(m.chatPK) }
        byChat[m.chatPK, default: []].append(m)
    }

    var retryFrom: Int64?   // lowest message that must be looked at again (upload failed / media not downloaded yet)
    var filedLine: String?
    for chatPK in order {
        let all = byChat[chatPK] ?? []
        guard let first = all.first, first.isOneToOne else { continue }
        let name = first.partner ?? first.phone ?? first.jid
        if matches(name, settings.skipContacts) { continue }
        if !settings.onlyContacts.isEmpty && !matches(name, settings.onlyContacts) && !matches(first.phone, settings.onlyContacts) { continue }

        var msgs: [WAMessage] = []
        for m in all where m.kind != nil {
            // a voice note or photo that WhatsApp is still downloading: wait for it (up to 3 minutes)
            if m.isMedia, m.mediaFile == nil, Date().timeIntervalSince(m.date) < 180 {
                retryFrom = min(retryFrom ?? m.pk, m.pk)
                break
            }
            msgs.append(m)
        }
        guard !msgs.isEmpty else { continue }
        do {
            let context = try db.context(chat: chatPK, before: state.lastPK)
            if let caseNumber = try await uploadChat(msgs, context: context, settings: settings) {
                log("💬 \(msgs.count) WhatsApp message\(msgs.count == 1 ? "" : "s") from \(name) → \(caseNumber)")
                filedLine = "\(caseNumber) · \(name)"
            }
        } catch {
            log("Could not send WhatsApp messages from \(name): \(error)")
            retryFrom = min(retryFrom ?? msgs[0].pk, msgs[0].pk)
        }
    }
    state.lastPK = retryFrom.map { $0 - 1 } ?? last.pk
    saveMessageState(state)
    return filedLine
}

/// Runs for as long as the app is open.
func watchMessages() async {
    var lastFiled: String?
    var lastProblem: String?
    while true {
        let s = currentSettings()
        if s.syncMessages == false || s.token.isEmpty {
            report(.off)
        } else {
            do {
                if let line = try await syncMessagesOnce(settings: s) {
                    let t = DateFormatter.localizedString(from: Date(), dateStyle: .none, timeStyle: .short)
                    lastFiled = "\(line) (\(t))"
                }
                if lastProblem != nil { log("WhatsApp messages: working again") }
                lastProblem = nil
                report(.ok(lastFiled: lastFiled))
            } catch {
                let text = "\(error)"
                if text != lastProblem { log("WhatsApp messages: \(text)") }
                lastProblem = text
                report(text.contains("cannot open") || text.contains("no access") ? .noAccess : .error(text))
            }
        }
        try? await Task.sleep(nanoseconds: 20_000_000_000)
    }
}
