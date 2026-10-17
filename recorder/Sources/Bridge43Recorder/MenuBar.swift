// Bridge43 menu bar app: an icon at the top right of the screen that waits for WhatsApp calls,
// records them, files them into the Bridge43 database (Railway) and lists the latest cases.

import AppKit
import ServiceManagement

struct RecentCase: Decodable {
    let case_number: String
    let name: String
    let status: String
    let title: String?
    let item_count: Int
    let last_item_at: String
    let url: String
}

@MainActor
final class MenuBarApp: NSObject, NSApplicationDelegate, NSMenuDelegate {
    static func run() {
        let app = NSApplication.shared
        let delegate = MenuBarApp()
        app.delegate = delegate
        app.setActivationPolicy(.accessory)   // menu bar only, no Dock icon
        withExtendedLifetime(delegate) { app.run() }
    }

    private var statusItem: NSStatusItem!
    private let menu = NSMenu()
    private var state: WatchState = .watching
    private var paused = UserDefaults.standard.bool(forKey: "paused")
    private var lastOutcome: CallOutcome?
    private var lastOutcomeAt: Date?
    private var lastLine = ""
    private var recent: [RecentCase] = []
    private var recentError: String?
    private var timer: Timer?
    private var messageStatus: MessageSyncStatus = .off

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        menu.delegate = self
        statusItem.menu = menu

        isPaused = { [weak self] in self?.paused ?? false }
        currentSettings = { settings }
        stateListener = { [weak self] s in self?.state = s; self?.refreshIcon() }
        outcomeListener = { [weak self] o in
            self?.lastOutcome = o
            self?.lastOutcomeAt = Date()
            Task { await self?.loadRecent() }
        }
        logListener = { [weak self] line in self?.lastLine = line }
        messageStatusListener = { [weak self] s in Task { @MainActor in self?.messageStatus = s } }

        refreshIcon()
        // First launch: start automatically at every login, so opening the Mac is enough
        if !UserDefaults.standard.bool(forKey: "loginConfigured") {
            UserDefaults.standard.set(true, forKey: "loginConfigured")
            try? SMAppService.mainApp.register()
        }
        if settings.token.isEmpty || settings.serverURL.contains("localhost") {
            askForServer(first: true)
        }
        Task { await watch() }
        Task.detached { await watchMessages() }
        Task { await self.loadRecent() }
        // keep the recording timer and the case list fresh
        timer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.refreshIcon()
                await self?.loadRecent()
            }
        }
    }

    // MARK: icon

    private func refreshIcon() {
        guard let button = statusItem?.button else { return }
        let symbol: String
        var tint: NSColor? = nil
        var text = ""
        if paused, case .watching = state {
            symbol = "pause.circle"
        } else {
            switch state {
            case .watching: symbol = "phone.circle"
            case .paused: symbol = "pause.circle"
            case .recording(let since, _):
                symbol = "record.circle.fill"; tint = .systemRed
                let secs = Int(Date().timeIntervalSince(since))
                text = String(format: " %d:%02d", secs / 60, secs % 60)
            case .finishing: symbol = "arrow.up.circle"
            }
        }
        let image = NSImage(systemSymbolName: symbol, accessibilityDescription: "Bridge43")
        image?.isTemplate = tint == nil
        button.image = image
        button.contentTintColor = tint
        button.title = text
        button.toolTip = "Bridge43 call recorder"
    }

    // MARK: menu (rebuilt every time it opens)

    func menuWillOpen(_ menu: NSMenu) { rebuildMenu() }

    private func rebuildMenu() {
        menu.removeAllItems()
        menu.addItem(disabled(statusLine()))
        if let o = lastOutcome, let at = lastOutcomeAt {
            let time = DateFormatter.localizedString(from: at, dateStyle: .none, timeStyle: .short)
            switch o {
            case .filed(let who, let caseNumber, let url):
                let item = NSMenuItem(title: "Last call: \(caseNumber ?? "filed") · \(who) (\(time))",
                                      action: #selector(openURL(_:)), keyEquivalent: "")
                item.representedObject = url; item.target = self
                menu.addItem(item)
            case .discarded(let why): menu.addItem(disabled("Last call skipped (\(time)): \(why)"))
            case .failed: menu.addItem(disabled("Last call not sent (\(time)): saved on this Mac"))
            }
        }
        switch messageStatus {
        case .off:
            menu.addItem(disabled("WhatsApp messages: not sent"))
        case .noAccess:
            menu.addItem(action("WhatsApp messages: allow Full Disk Access…", #selector(openFullDiskAccess)))
        case .error(let e):
            menu.addItem(disabled("WhatsApp messages: \(e.prefix(60))"))
        case .ok(let last):
            if let last {
                menu.addItem(disabled("Last messages: \(last)"))
            } else {
                menu.addItem(disabled("Watching WhatsApp messages"))
            }
        }
        let pending = pendingRecordings()
        if pending > 0 {
            menu.addItem(action("Send \(pending) saved recording\(pending == 1 ? "" : "s") to Bridge43", #selector(retry)))
        }

        menu.addItem(.separator())
        menu.addItem(disabled("Latest cases"))
        if let err = recentError {
            menu.addItem(disabled("  \(err)"))
        } else if recent.isEmpty {
            menu.addItem(disabled("  No cases yet"))
        } else {
            for c in recent.prefix(8) {
                let label = "\(c.case_number) · \(c.name) · \(statusLabel(c.status))"
                let item = NSMenuItem(title: label, action: #selector(openURL(_:)), keyEquivalent: "")
                item.representedObject = c.url
                item.target = self
                item.toolTip = c.title ?? ""
                menu.addItem(item)
            }
        }
        menu.addItem(action("Open Bridge43 inbox", #selector(openInbox), key: "o"))

        menu.addItem(.separator())
        let pause = action(paused ? "Resume recording calls" : "Pause recording calls", #selector(togglePause))
        menu.addItem(pause)
        let sync = action("Send WhatsApp messages to Bridge43", #selector(toggleMessages))
        sync.state = settings.syncMessages == false ? .off : .on
        menu.addItem(sync)
        let login = action("Start at login", #selector(toggleLogin))
        login.state = SMAppService.mainApp.status == .enabled ? .on : .off
        menu.addItem(login)
        menu.addItem(action("Server address…", #selector(changeServer)))
        menu.addItem(action("Check setup…", #selector(runCheck)))
        menu.addItem(.separator())
        menu.addItem(action("Quit Bridge43 Recorder", #selector(quit), key: "q"))
    }

    private func statusLine() -> String {
        if paused, case .watching = state { return "Paused: calls are not recorded" }
        switch state {
        case .watching: return "Waiting for WhatsApp calls"
        case .paused: return "Paused: calls are not recorded"
        case .recording(let since, let who):
            let secs = Int(Date().timeIntervalSince(since))
            return String(format: "Recording call%@ · %d:%02d", who.map { " with \($0)" } ?? "", secs / 60, secs % 60)
        case .finishing: return "Sending the call to Bridge43…"
        }
    }

    private func statusLabel(_ s: String) -> String {
        ["open": "collecting", "draft_ready": "needs review", "approved": "approved",
         "closed": "closed", "rejected": "rejected", "not_relevant": "not relevant"][s] ?? s
    }

    private func disabled(_ title: String) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        item.isEnabled = false
        return item
    }

    private func action(_ title: String, _ selector: Selector, key: String = "") -> NSMenuItem {
        let item = NSMenuItem(title: title, action: selector, keyEquivalent: key)
        item.target = self
        return item
    }

    // MARK: data from the server

    private func loadRecent() async {
        let base = settings.serverURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard !settings.token.isEmpty, let url = URL(string: base + "/api/recorder/recent?limit=8") else { return }
        var req = URLRequest(url: url)
        req.setValue("Bearer \(settings.token)", forHTTPHeaderField: "Authorization")
        req.timeoutInterval = 15
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            guard code == 200 else {
                recentError = code == 401 ? "Wrong token (Server address… to fix)" : "Server answered \(code)"
                return
            }
            recent = try JSONDecoder().decode([RecentCase].self, from: data)
            recentError = nil
        } catch {
            recentError = "Cannot reach \(URL(string: base)?.host ?? base)"
        }
    }

    // MARK: actions

    @objc private func openURL(_ sender: NSMenuItem) {
        if let s = sender.representedObject as? String, let url = URL(string: s) { NSWorkspace.shared.open(url) }
    }

    @objc private func openInbox() {
        if let url = URL(string: settings.serverURL) { NSWorkspace.shared.open(url) }
    }

    @objc private func togglePause() {
        paused.toggle()
        UserDefaults.standard.set(paused, forKey: "paused")
        if case .recording = state {} else { state = paused ? .paused : .watching }
        log(paused ? "Paused" : "Resumed")
        refreshIcon()
    }

    @objc private func toggleMessages() {
        settings.syncMessages = settings.syncMessages == false
        saveSettings(settings)
        if settings.syncMessages == false { messageStatus = .off }
        log(settings.syncMessages == false ? "WhatsApp messages: off" : "WhatsApp messages: on")
    }

    @objc private func openFullDiskAccess() {
        if let url = URL(string: fullDiskAccessSettings) { NSWorkspace.shared.open(url) }
        alert("Let Bridge43 read WhatsApp messages",
              "In the window that opened, turn on Bridge43 Recorder under Full Disk Access " +
              "(click + and pick it from your Applications folder if it isn't listed), then quit and reopen Bridge43 Recorder.\n\n" +
              "Only one-to-one chats are read. Chats that aren't about schools or logistics are not stored.")
    }

    @objc private func toggleLogin() {
        do {
            if SMAppService.mainApp.status == .enabled {
                try SMAppService.mainApp.unregister()
            } else {
                try SMAppService.mainApp.register()
            }
        } catch {
            alert("Could not change \"Start at login\"",
                  "Move Bridge43 Recorder into your Applications folder and try again.\n\n\(error.localizedDescription)")
        }
    }

    @objc private func retry() {
        Task {
            let n = await retryPending(settings: settings)
            notify("Bridge43", n > 0 ? "Sent \(n) saved recording\(n == 1 ? "" : "s")." : "Could not send the saved recordings yet.")
            await loadRecent()
        }
    }

    @objc private func changeServer() { askForServer(first: false) }

    private func askForServer(first: Bool) {
        NSApp.activate(ignoringOtherApps: true)
        let a = NSAlert()
        a.messageText = first ? "Connect Bridge43 Recorder" : "Bridge43 server"
        a.informativeText = "Server address (e.g. https://bridge43-production.up.railway.app) and the recorder token " +
            "(RECORDER_TOKEN in the server's settings)."
        let url = NSTextField(frame: NSRect(x: 0, y: 30, width: 340, height: 24))
        url.placeholderString = "https://…"
        url.stringValue = settings.serverURL.contains("localhost") ? "" : settings.serverURL
        let token = NSSecureTextField(frame: NSRect(x: 0, y: 0, width: 340, height: 24))
        token.placeholderString = "Recorder token"
        token.stringValue = settings.token
        let box = NSView(frame: NSRect(x: 0, y: 0, width: 340, height: 56))
        box.addSubview(url); box.addSubview(token)
        a.accessoryView = box
        a.addButton(withTitle: "Save")
        a.addButton(withTitle: "Cancel")
        guard a.runModal() == .alertFirstButtonReturn else { return }
        let u = url.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        if !u.isEmpty { settings.serverURL = u.hasPrefix("http") ? u : "https://" + u }
        let t = token.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        if !t.isEmpty { settings.token = t }
        saveSettings(settings)
        Task { await loadRecent() }
    }

    @objc private func runCheck() {
        Task {
            var lines: [String] = []
            let previous = logListener
            logListener = { lines.append($0) }
            await check(settings)
            try? await Task.sleep(nanoseconds: 400_000_000)   // let the last log lines arrive
            logListener = previous
            alert("Bridge43 setup check", lines.joined(separator: "\n"))
        }
    }

    @objc private func quit() { NSApp.terminate(nil) }

    private func alert(_ title: String, _ text: String) {
        NSApp.activate(ignoringOtherApps: true)
        let a = NSAlert()
        a.messageText = title
        a.informativeText = text
        a.runModal()
    }
}
