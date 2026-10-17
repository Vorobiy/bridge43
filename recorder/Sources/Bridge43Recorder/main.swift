// Bridge43 call recorder for macOS (14.4+; recording your own voice needs macOS 15+).
//
// Watches for WhatsApp Desktop using the microphone (= a call), records both sides
// (WhatsApp's audio = the other person, your microphone = you) with ScreenCaptureKit,
// applies your contact rules, asks you to confirm, mixes the two tracks with ffmpeg and
// files the call in Bridge43 under the caller. Nothing joins the call.
//
// Usage:  bridge43-recorder            run and watch for calls
//         bridge43-recorder --check    test settings, permissions and the server, then exit
//         bridge43-recorder --debug    also print every app using the microphone

import AVFoundation
import CoreAudio
import Foundation
import ScreenCaptureKit

// MARK: - Settings (~/.bridge43/recorder.json)

struct Settings: Codable {
    var serverURL = "http://localhost:8000"
    var token = ""
    /// If not empty, only calls whose WhatsApp window title contains one of these are kept.
    var onlyContacts: [String] = []
    /// Calls whose window title contains one of these are always discarded (e.g. family).
    var skipContacts: [String] = []
    /// Show a dialog after each call to confirm/fix the contact before sending.
    var confirmBeforeSending = true
    /// Ignore anything shorter (voice notes also use the microphone).
    var minCallSeconds = 20.0
    /// The other side must speak for at least this long, or it was not a call.
    var minOtherSideSpeechSeconds = 3.0
    var ffmpegPath = "/opt/homebrew/bin/ffmpeg"
}

let home = FileManager.default.homeDirectoryForCurrentUser
let appDir = home.appendingPathComponent(".bridge43", isDirectory: true)
let settingsURL = appDir.appendingPathComponent("recorder.json")
let callsDir = appDir.appendingPathComponent("calls", isDirectory: true)
let args = Set(CommandLine.arguments.dropFirst())
let debug = args.contains("--debug")

func log(_ message: String) {
    let f = DateFormatter()
    f.dateFormat = "HH:mm:ss"
    print("[\(f.string(from: Date()))] \(message)")
    fflush(stdout)
}

func loadSettings() -> Settings {
    try? FileManager.default.createDirectory(at: callsDir, withIntermediateDirectories: true)
    if let data = try? Data(contentsOf: settingsURL) {
        do { return try JSONDecoder().decode(Settings.self, from: data) } catch {
            log("Could not read \(settingsURL.path): \(error). Fix the file and run again.")
            exit(1)
        }
    }
    let enc = JSONEncoder()
    enc.outputFormatting = [.prettyPrinted, .sortedKeys]
    try? enc.encode(Settings()).write(to: settingsURL)
    log("Created \(settingsURL.path). Put your RECORDER_TOKEN in \"token\", then run again.")
    exit(0)
}

// MARK: - Which apps are using the microphone (Core Audio process objects, macOS 14.4+)

enum MicWatcher {
    static func property<T>(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector, _ value: inout T) -> Bool {
        var addr = AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
                                              mElement: kAudioObjectPropertyElementMain)
        var size = UInt32(MemoryLayout<T>.size)
        return AudioObjectGetPropertyData(object, &addr, 0, nil, &size, &value) == noErr
    }

    static func processObjects() -> [AudioObjectID] {
        var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyProcessObjectList,
                                              mScope: kAudioObjectPropertyScopeGlobal,
                                              mElement: kAudioObjectPropertyElementMain)
        var size: UInt32 = 0
        let system = AudioObjectID(kAudioObjectSystemObject)
        guard AudioObjectGetPropertyDataSize(system, &addr, 0, nil, &size) == noErr, size > 0 else { return [] }
        var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
        guard AudioObjectGetPropertyData(system, &addr, 0, nil, &size, &ids) == noErr else { return [] }
        return ids
    }

    static func bundleID(_ process: AudioObjectID) -> String? {
        var value: Unmanaged<CFString>?
        guard property(process, kAudioProcessPropertyBundleID, &value), let v = value else { return nil }
        return v.takeRetainedValue() as String
    }

    static func isUsingInput(_ process: AudioObjectID) -> Bool {
        var running: UInt32 = 0
        return property(process, kAudioProcessPropertyIsRunningInput, &running) && running != 0
    }

    /// Bundle ids of every process currently recording from an input device.
    static func appsUsingMic() -> [String] {
        processObjects().filter(isUsingInput).compactMap(bundleID)
    }

    static func whatsAppUsingMic() -> Bool {
        appsUsingMic().contains { $0.lowercased().contains("whatsapp") }
    }
}

// MARK: - Recording one call

final class CallRecording: NSObject, SCStreamOutput, SCStreamDelegate, @unchecked Sendable {
    let folder: URL
    let startedAt = Date()
    private let queue = DispatchQueue(label: "bridge43.audio")
    private var stream: SCStream?
    private var otherFile: AVAudioFile?   // WhatsApp's audio output = the other person
    private var meFile: AVAudioFile?      // microphone = you
    private(set) var otherStart: Double?
    private(set) var meStart: Double?
    private(set) var otherSpeechSeconds = 0.0
    private(set) var windowTitles: [String: Int] = [:]
    private(set) var micCaptured = false
    private(set) var lastOtherAudioAt = Date()

    var otherURL: URL { folder.appendingPathComponent("other.caf") }
    var meURL: URL { folder.appendingPathComponent("me.caf") }

    override init() {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd_HH-mm-ss"
        folder = callsDir.appendingPathComponent("call_" + f.string(from: Date()), isDirectory: true)
        super.init()
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    }

    func start() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
        guard let display = content.displays.first else { throw RecorderError("no display found") }
        let whatsApp = content.applications.filter { $0.bundleIdentifier.lowercased().contains("whatsapp") }
        guard !whatsApp.isEmpty else { throw RecorderError("WhatsApp is not running") }
        noteTitles(content)

        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.excludesCurrentProcessAudio = true
        config.sampleRate = 48_000
        config.channelCount = 1
        // We only want sound; keep the (unused) video stream tiny and slow.
        config.width = 2
        config.height = 2
        config.minimumFrameInterval = CMTime(value: 10, timescale: 1)
        if #available(macOS 15.0, *) {
            config.captureMicrophone = true
            micCaptured = true
        }
        let filter = SCContentFilter(display: display, including: whatsApp, exceptingWindows: [])
        let s = SCStream(filter: filter, configuration: config, delegate: self)
        try s.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
        try s.addStreamOutput(self, type: .screen, sampleHandlerQueue: queue)   // frames are ignored
        if #available(macOS 15.0, *) {
            try s.addStreamOutput(self, type: .microphone, sampleHandlerQueue: queue)
        }
        try await s.startCapture()
        stream = s
    }

    /// Window titles help name the caller (WhatsApp shows the contact in the call window).
    func refreshTitles() async {
        if let content = try? await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false) {
            noteTitles(content)
        }
    }

    private func noteTitles(_ content: SCShareableContent) {
        for w in content.windows where (w.owningApplication?.bundleIdentifier.lowercased().contains("whatsapp") ?? false) {
            let t = (w.title ?? "").trimmingCharacters(in: .whitespaces)
            if t.isEmpty || t.lowercased().hasPrefix("whatsapp") { continue }
            windowTitles[t, default: 0] += 1
        }
    }

    var likelyContact: String? { windowTitles.max { $0.value < $1.value }?.key }

    func stop() async {
        if let s = stream { try? await s.stopCapture() }
        stream = nil
        queue.sync {
            otherFile = nil   // closing = releasing the AVAudioFile
            meFile = nil
        }
    }

    // SCStreamOutput
    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard sampleBuffer.isValid, sampleBuffer.numSamples > 0 else { return }
        if type == .audio {
            write(sampleBuffer, other: true)
        } else if #available(macOS 15.0, *), type == .microphone {
            write(sampleBuffer, other: false)
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        log("Recording stopped by macOS: \(error.localizedDescription)")
    }

    private func write(_ sb: CMSampleBuffer, other: Bool) {
        guard let desc = sb.formatDescription,
              let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(desc),
              let format = AVAudioFormat(streamDescription: asbd) else { return }
        let frames = AVAudioFrameCount(sb.numSamples)
        guard let pcm = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames) else { return }
        pcm.frameLength = frames
        guard CMSampleBufferCopyPCMDataIntoAudioBufferList(sb, at: 0, frameCount: Int32(frames),
                                                           into: pcm.mutableAudioBufferList) == noErr else { return }
        let pts = CMTimeGetSeconds(sb.presentationTimeStamp)
        do {
            if other {
                if otherFile == nil {
                    otherFile = try AVAudioFile(forWriting: otherURL, settings: format.settings,
                                                commonFormat: format.commonFormat, interleaved: format.isInterleaved)
                    otherStart = pts
                }
                try otherFile?.write(from: pcm)
                otherSpeechSeconds += speechSeconds(pcm)
                lastOtherAudioAt = Date()
            } else {
                if meFile == nil {
                    meFile = try AVAudioFile(forWriting: meURL, settings: format.settings,
                                             commonFormat: format.commonFormat, interleaved: format.isInterleaved)
                    meStart = pts
                }
                try meFile?.write(from: pcm)
            }
        } catch {
            log("Could not write audio: \(error.localizedDescription)")
        }
    }

    /// Rough voice activity: how much of this buffer is louder than near-silence.
    private func speechSeconds(_ pcm: AVAudioPCMBuffer) -> Double {
        guard let data = pcm.floatChannelData, pcm.frameLength > 0 else { return 0 }
        let n = Int(pcm.frameLength)
        var sum: Float = 0
        for i in 0..<n { sum += data[0][i] * data[0][i] }
        let rms = (sum / Float(n)).squareRoot()
        return rms > 0.01 ? Double(n) / pcm.format.sampleRate : 0
    }
}

struct RecorderError: Error, CustomStringConvertible {
    let description: String
    init(_ d: String) { description = d }
}

// MARK: - After the call: rules, confirm, mix, upload

func matches(_ title: String?, _ list: [String]) -> Bool {
    guard let t = title?.lowercased() else { return false }
    return list.contains { !$0.isEmpty && t.contains($0.lowercased()) }
}

func runProcess(_ path: String, _ arguments: [String]) -> (Int32, String) {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: path)
    p.arguments = arguments
    let out = Pipe()
    p.standardOutput = out
    p.standardError = out
    do { try p.run() } catch { return (-1, error.localizedDescription) }
    let data = out.fileHandleForReading.readDataToEndOfFile()
    p.waitUntilExit()
    return (p.terminationStatus, String(data: data, encoding: .utf8) ?? "")
}

func appleScriptString(_ s: String) -> String {
    "\"" + s.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") + "\""
}

func notify(_ title: String, _ message: String) {
    _ = runProcess("/usr/bin/osascript", ["-e",
        "display notification \(appleScriptString(message)) with title \(appleScriptString(title))"])
}

/// Returns the contact to file under, or nil if the user chose Discard (or didn't answer in 10 min).
func confirm(minutes: Double, suggested: String) -> String? {
    let prompt = String(format: "Send this %.1f-minute WhatsApp call to Bridge43?\n\nWho was it? (school or name, and number if you have it)", minutes)
    let script = """
    set r to display dialog \(appleScriptString(prompt)) default answer \(appleScriptString(suggested)) ¬
        buttons {"Discard", "Send to Bridge43"} default button "Send to Bridge43" ¬
        with title "Bridge43 call recorder" giving up after 600
    if gave up of r then return "GAVEUP|"
    return (button returned of r) & "|" & (text returned of r)
    """
    let (status, output) = runProcess("/usr/bin/osascript", ["-e", script])
    guard status == 0 else { return nil }
    let parts = output.trimmingCharacters(in: .whitespacesAndNewlines).split(separator: "|", maxSplits: 1).map(String.init)
    guard parts.first == "Send to Bridge43" else { return nil }
    let who = parts.count > 1 ? parts[1].trimmingCharacters(in: .whitespaces) : ""
    return who.isEmpty ? "Unknown caller" : who
}

func mix(_ rec: CallRecording, ffmpeg: String) -> URL? {
    let fm = FileManager.default
    let out = rec.folder.appendingPathComponent("call.m4a")
    var inputs: [(URL, Double)] = []
    let starts = [rec.otherStart, rec.meStart].compactMap { $0 }
    let t0 = starts.min() ?? 0
    if fm.fileExists(atPath: rec.otherURL.path), let s = rec.otherStart { inputs.append((rec.otherURL, s - t0)) }
    if fm.fileExists(atPath: rec.meURL.path), let s = rec.meStart { inputs.append((rec.meURL, s - t0)) }
    guard !inputs.isEmpty else { return nil }
    var a: [String] = ["-y", "-loglevel", "error"]
    for (url, _) in inputs { a += ["-i", url.path] }
    var chains: [String] = []
    for (i, (_, offset)) in inputs.enumerated() {
        let ms = max(0, Int(offset * 1000))
        chains.append("[\(i)]aformat=channel_layouts=mono,aresample=48000,adelay=\(ms):all=1[a\(i)]")
    }
    let labels = inputs.indices.map { "[a\($0)]" }.joined()
    let filter = chains.joined(separator: ";") + ";" + labels +
        "amix=inputs=\(inputs.count):duration=longest:normalize=0,aresample=16000[out]"
    a += ["-filter_complex", filter, "-map", "[out]", "-ac", "1", "-c:a", "aac", "-b:a", "48k", out.path]
    let (status, output) = runProcess(ffmpeg, a)
    if status != 0 { log("ffmpeg failed: \(output)"); return nil }
    return out
}

func upload(_ file: URL, contact: String, startedAt: Date, duration: Double, settings: Settings) async throws -> String {
    guard let url = URL(string: settings.serverURL.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + "/api/recorder/calls") else {
        throw RecorderError("bad serverURL in settings")
    }
    let boundary = "bridge43-\(UUID().uuidString)"
    var body = Data()
    func field(_ name: String, _ value: String) {
        body.append("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"\r\n\r\n\(value)\r\n".data(using: .utf8)!)
    }
    let iso = ISO8601DateFormatter()
    field("contact", contact)
    field("started_at", iso.string(from: startedAt))
    field("duration_seconds", String(format: "%.0f", duration))
    field("source", "whatsapp_call")
    let isCaf = file.pathExtension.lowercased() == "caf"
    let fname = isCaf ? "call.caf" : "call.m4a"
    let ctype = isCaf ? "audio/x-caf" : "audio/mp4"
    body.append("--\(boundary)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(fname)\"\r\nContent-Type: \(ctype)\r\n\r\n".data(using: .utf8)!)
    body.append(try Data(contentsOf: file))
    body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)

    var req = URLRequest(url: url)
    req.httpMethod = "POST"
    req.setValue("Bearer \(settings.token)", forHTTPHeaderField: "Authorization")
    req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
    req.timeoutInterval = 300
    let (data, resp) = try await URLSession.shared.upload(for: req, from: body)
    let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
    let text = String(data: data, encoding: .utf8) ?? ""
    guard code == 200 else { throw RecorderError("server answered \(code): \(text.prefix(300))") }
    if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any], let link = json["url"] as? String {
        return link
    }
    return settings.serverURL
}

func finish(_ rec: CallRecording, settings: Settings) async {
    let duration = Date().timeIntervalSince(rec.startedAt)
    let contact = rec.likelyContact
    func discard(_ why: String) {
        log("Discarded: \(why)")
        try? FileManager.default.removeItem(at: rec.folder)
    }
    log("Checking rules…")
    if duration < settings.minCallSeconds {
        return discard(String(format: "only %.0f s (probably a voice note)", duration))
    }
    if rec.otherSpeechSeconds < settings.minOtherSideSpeechSeconds {
        return discard(String(format: "the other side was silent (%.1f s of speech): not a call", rec.otherSpeechSeconds))
    }
    if matches(contact, settings.skipContacts) {
        return discard("\(contact ?? "") is on your skip list")
    }
    if !settings.onlyContacts.isEmpty, !matches(contact, settings.onlyContacts) {
        if contact != nil || !settings.confirmBeforeSending {
            return discard("\(contact ?? "unknown caller") is not on your onlyContacts list")
        }
        // Caller unknown: let the user decide in the dialog below.
    }

    var who = contact ?? "Unknown caller"
    if settings.confirmBeforeSending {
        guard let answer = confirm(minutes: duration / 60, suggested: contact ?? "") else {
            return discard("you chose Discard")
        }
        who = answer
    }
    let fm = FileManager.default
    let toSend: URL
    if let mixed = mix(rec, ffmpeg: settings.ffmpegPath) {
        toSend = mixed
    } else if fm.fileExists(atPath: rec.otherURL.path) {
        log("Mixing failed; sending the caller's track as recorded (Bridge43 converts it).")
        toSend = rec.otherURL
    } else {
        log("No audio was captured. Nothing to send.")
        try? fm.removeItem(at: rec.folder)
        return
    }
    if !fm.fileExists(atPath: rec.meURL.path) {
        log("⚠️  Only the caller's voice was recorded. Turn on Terminal in System Settings → Privacy & Security → Microphone, then restart the recorder.")
    }
    log("Uploading to \(settings.serverURL)…")
    do {
        let link = try await upload(toSend, contact: who, startedAt: rec.startedAt, duration: duration, settings: settings)
        log("Filed in Bridge43 under \"\(who)\": \(link)")
        notify("Call filed in Bridge43", "\(who) · \(Int(duration / 60)) min. The case draft follows shortly.")
        try? FileManager.default.removeItem(at: rec.folder)   // don't keep audio on the laptop once it's filed
    } catch {
        log("Upload failed: \(error). The recording is kept in \(rec.folder.path)")
        notify("Bridge43: upload failed", "The recording is saved on this Mac. Check that Bridge43 is running.")
    }
}

// MARK: - Check mode

func check(_ settings: Settings) async {
    log("Settings: \(settingsURL.path)")
    log("Server: \(settings.serverURL)")
    if #unavailable(macOS 15.0) {
        log("⚠️  macOS 14: only the other person's voice can be recorded. Update to macOS 15+ to record your side too.")
    }
    let (s, _) = runProcess(settings.ffmpegPath, ["-version"])
    log(s == 0 ? "✅ ffmpeg found" : "❌ ffmpeg not found at \(settings.ffmpegPath) (brew install ffmpeg, or fix ffmpegPath)")
    do {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
        let wa = content.applications.filter { $0.bundleIdentifier.lowercased().contains("whatsapp") }
        log("✅ Screen & audio recording permission OK")
        log(wa.isEmpty ? "⚠️  WhatsApp is not running right now" : "✅ WhatsApp found: \(wa.map(\.bundleIdentifier).joined(separator: ", "))")
    } catch {
        log("❌ No recording permission: System Settings → Privacy & Security → Screen & System Audio Recording → turn on your Terminal app, then quit and reopen Terminal.")
    }
    log("Apps using the microphone right now: \(MicWatcher.appsUsingMic())")
    if let url = URL(string: settings.serverURL.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + "/api/recorder/ping") {
        var req = URLRequest(url: url)
        req.setValue("Bearer \(settings.token)", forHTTPHeaderField: "Authorization")
        do {
            let (_, resp) = try await URLSession.shared.data(for: req)
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            log(code == 200 ? "✅ Bridge43 server accepted the token" :
                code == 401 ? "❌ Bridge43 rejected the token: it must equal RECORDER_TOKEN in the server's .env" :
                code == 403 ? "❌ Recorder uploads are off on the server: set RECORDER_TOKEN in .env and restart it" :
                "❌ Bridge43 answered \(code)")
        } catch {
            log("❌ Cannot reach Bridge43 at \(settings.serverURL): is the server running?")
        }
    }
}

// MARK: - Main loop

@MainActor
func watch(_ settings: Settings) async {
    log("Watching for WhatsApp calls. Keep this window open. Press Ctrl+C to stop.")
    if !settings.onlyContacts.isEmpty { log("Only keeping calls with: \(settings.onlyContacts.joined(separator: ", "))") }
    var current: CallRecording?
    var activeTicks = 0, idleTicks = 0, tick = 0
    var lastDebug: [String] = []
    while true {
        try? await Task.sleep(nanoseconds: 1_000_000_000)
        tick += 1
        if debug {
            let apps = MicWatcher.appsUsingMic()
            if apps != lastDebug { log("Microphone in use by: \(apps)"); lastDebug = apps }
        }
        let inCall = MicWatcher.whatsAppUsingMic()
        activeTicks = inCall ? activeTicks + 1 : 0
        idleTicks = inCall ? 0 : idleTicks + 1

        if current == nil, activeTicks >= 2 {
            let rec = CallRecording()
            do {
                try await rec.start()
                current = rec
                log("📞 WhatsApp call started: recording")
                notify("Bridge43 is recording this call", "Tell the caller: this call is recorded to help their school.")
            } catch {
                log("Could not start recording: \(error)")
                try? FileManager.default.removeItem(at: rec.folder)
                activeTicks = -30   // wait ~30 s before trying again
            }
        } else if let rec = current {
            if tick % 5 == 0 { await rec.refreshTitles() }
            let audioStopped = Date().timeIntervalSince(rec.lastOtherAudioAt) > 6
            if idleTicks >= 3 || (audioStopped && rec.otherStart != nil) {
                if audioStopped && idleTicks < 3 { log("WhatsApp call audio stopped: treating the call as ended") }
                current = nil
                await rec.stop()
                log(String(format: "Call ended after %.0f s (caller spoke ~%.0f s, your mic %@)",
                           Date().timeIntervalSince(rec.startedAt), rec.otherSpeechSeconds,
                           FileManager.default.fileExists(atPath: rec.meURL.path) ? "recorded" : "NOT recorded"))
                await finish(rec, settings: settings)
            }
        }
    }
}

let settings = loadSettings()
if settings.token.isEmpty {
    log("Add your token to \(settingsURL.path) (\"token\": the RECORDER_TOKEN from Bridge43's .env).")
    exit(1)
}
Task { @MainActor in
    if args.contains("--check") {
        await check(settings)
        exit(0)
    }
    await watch(settings)
}
RunLoop.main.run()
